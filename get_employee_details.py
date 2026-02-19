#!/usr/bin/env python
"""get_employee_details.py

Fetch employee details from the dependency-provider API using fresh Cognito credentials.

Usage:
  python get_employee_details.py <username> [--target-level N]
  
  # Or import as a library:
  from get_employee_details import get_employee_details, get_employee_hierarchy
  
  # Get raw details:
  details = get_employee_details("mrkcath")
  
  # Get hierarchy up to target level:
  hierarchy = get_employee_hierarchy("mrkcath", target_level=8)
  # Returns: [(alias, firstname, lastname, job_level), ...] until L8 is reached
"""
import datetime
import hashlib
import hmac
import json
import logging
import os
import sys
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, quote, urlparse

import requests
from dotenv import load_dotenv

# Configure logger for this module
logger = logging.getLogger(__name__)


def sign_request(method, url, headers, access_key, secret_key, session_token, region="us-west-2", service="execute-api"):
    """Sign a request with AWS SigV4."""
    parsed = urlparse(url)
    host = parsed.netloc
    headers["host"] = host

    # Create timestamp and payload hash
    t = datetime.datetime.now(datetime.timezone.utc)
    amz_date = t.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = t.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(b"").hexdigest()
    
    headers["x-amz-date"] = amz_date
    headers["x-amz-content-sha256"] = payload_hash
    if session_token:
        headers["x-amz-security-token"] = session_token

    # Canonical URI and query
    canonical_uri = parsed.path or "/"
    qs_items = parse_qsl(parsed.query, keep_blank_values=True)
    qs_items.sort()
    canonical_querystring = "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in qs_items)

    # Canonical headers (lowercase, sorted)
    header_dict = {k.lower(): v for k, v in headers.items()}
    canonical_headers = "".join(f"{k}:{header_dict[k]}\n" for k in sorted(header_dict.keys()))
    signed_headers = ";".join(sorted(header_dict.keys()))

    canonical_request = (
        f"{method}\n{canonical_uri}\n{canonical_querystring}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )

    algorithm = "AWS4-HMAC-SHA256"
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = (
        f"{algorithm}\n{amz_date}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
    )

    def sign(key, msg):
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k_date = sign(f"AWS4{secret_key}".encode("utf-8"), date_stamp)
    k_region = sign(k_date, region)
    k_service = sign(k_region, service)
    k_signing = sign(k_service, "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization_header = (
        f"{algorithm} Credential={access_key}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"
    )
    headers["Authorization"] = authorization_header
    return headers


def get_employee_details(username: str) -> Optional[Dict]:
    """
    Get employee details for the given username/login.
    
    Args:
        username: The employee's username (e.g., "ajassy")
        
    Returns:
        Dict containing employee details, or None if request failed
    """
    load_dotenv()

    url = f"https://api.prod.dependency-provider.talent.amazon.dev/v1/employee/details/login/{username}"

    # Try to fetch fresh credentials from Cognito
    access_key = None
    secret_key = None
    session_token = None
    
    try:
        from cognito_auth import get_fresh_credentials
        
        fresh_creds = get_fresh_credentials()
        if fresh_creds:
            access_key = fresh_creds.get("AccessKeyId")
            secret_key = fresh_creds.get("SecretKey")
            session_token = fresh_creds.get("SessionToken")
            logger.debug(f"Got fresh AWS credentials (expires: {fresh_creds.get('Expiration', 'unknown')})")
    except Exception as e:
        logger.error(f"Failed to fetch Cognito credentials: {e}")
        return None

    if not (access_key and secret_key):
        logger.error("No AWS credentials available. Set COGNITO_REFRESH_TOKEN in .env")
        return None

    # Build headers for the request
    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "origin": "https://internal-transfer.talent.amazon.dev",
        "referer": "https://internal-transfer.talent.amazon.dev/",
        "x-amz-user-agent": "aws-sdk-js/2.1544.0 promise",
    }

    # Sign the request
    headers = sign_request(
        method="GET",
        url=url,
        headers=headers,
        access_key=access_key,
        secret_key=secret_key,
        session_token=session_token,
        region="us-west-2",
        service="execute-api",
    )

    # Make the request
    logger.info(f"Fetching employee details for: {username}")
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        logger.debug(f"Response status: {resp.status_code}")
        
        if resp.status_code == 200:
            data = resp.json()
            logger.info(f"Successfully retrieved employee details for {username}")
            return data
        else:
            logger.error(f"Error: {resp.status_code}")
            logger.error(f"Response: {resp.text}")
            return None
            
    except Exception as e:
        logger.error(f"Request failed: {e}")
        return None


def get_employee_hierarchy(username: str, target_level: int = 8, max_iterations: int = 20) -> List[Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]]:
    """
    Get employee hierarchy (employee -> manager -> skip-manager -> ...) until target job level is reached.
    
    Args:
        username: The employee's username/login
        target_level: Target job level to reach (default: 8)
                     Fetches up the chain until someone at or above this level is found
        max_iterations: Safety limit to prevent infinite loops (default: 20)
    
    Returns:
        List of tuples: [(alias, firstname, lastname, job_level), ...]
        Continues fetching until target_level is reached or no more managers exist.
        If a manager cannot be found, stops and returns what was collected.
    
    Examples:
        # Get all users up to nearest L8
        hierarchy = get_employee_hierarchy("saintamz", target_level=8)
        
        # Get all users up to nearest L7
        hierarchy = get_employee_hierarchy("saintamz", target_level=7)
    """
    hierarchy = []
    current_username = username
    iterations = 0
    
    while current_username and iterations < max_iterations:
        iterations += 1
        
        details = get_employee_details(current_username)
        
        if not details:
            # Failed to fetch details, stop here
            break
        
        # Extract current employee info
        alias = details.get('employeeIds', {}).get('login')
        firstname = details.get('firstName')
        lastname = details.get('lastName')
        job_level = details.get('jobLevel')
        
        # Convert job_level to int for comparison
        try:
            job_level_int = int(job_level) if job_level else 0
        except (ValueError, TypeError):
            job_level_int = 0
        
        hierarchy.append((alias, firstname, lastname, job_level))
        
        # Check if we've reached the target level
        if job_level_int >= target_level:
            logger.info(f"Reached target level L{target_level} (found L{job_level_int})")
            break
        
        # Get next level (manager)
        manager_ids = details.get('managerEmployeeIds', {})
        current_username = manager_ids.get('login') if manager_ids else None
        
        if not current_username:
            logger.info(f"No more managers found (stopped at L{job_level_int})")
            break
    
    return hierarchy


def main():
    import argparse
    
    # Configure logging for CLI usage
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Add verbosity control
    parser = argparse.ArgumentParser(
        description='Fetch employee details and management hierarchy',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Get hierarchy up to nearest L8 (default):
  python get_employee_details.py saintamz
  
  # Get hierarchy up to nearest L7:
  python get_employee_details.py saintamz --target-level 7
  
  # Get hierarchy up to nearest L10:
  python get_employee_details.py saintamz --target-level 10
  
  # Get raw JSON for single employee:
  python get_employee_details.py saintamz --json
        """
    )
    parser.add_argument('username', help='Employee username/login')
    parser.add_argument(
        '--target-level', 
        type=int, 
        default=8,
        help='Target job level to reach (default: 8). Fetches up the chain until this level is found.'
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help='Output raw JSON instead of hierarchy tuples'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging (DEBUG level)'
    )
    parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='Suppress INFO logs (only show WARNING and ERROR)'
    )
    
    args = parser.parse_args()
    
    # Configure logging level based on arguments
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    elif args.quiet:
        logging.getLogger().setLevel(logging.WARNING)
    
    if args.json:
        # Original behavior: output raw JSON
        details = get_employee_details(args.username)
        if details:
            logger.info("\n" + "=" * 80)
            logger.info("EMPLOYEE DETAILS")
            logger.info("=" * 80)
            print(json.dumps(details, indent=2))
            return 0
        else:
            logger.error("Failed to retrieve employee details")
            return 1
    else:
        # New behavior: output hierarchy tuples
        logger.info(f"Fetching hierarchy for {args.username} up to target level L{args.target_level}...")
        hierarchy = get_employee_hierarchy(args.username, target_level=args.target_level)
        
        logger.info("\n" + "=" * 80)
        logger.info("EMPLOYEE HIERARCHY")
        logger.info("=" * 80)
        
        level_names = ["Employee", "Manager", "Skip Manager", "Skip+1 Manager", "Skip+2 Manager"]
        
        for i, (alias, firstname, lastname, job_level) in enumerate(hierarchy):
            level_name = level_names[i] if i < len(level_names) else f"Level {i}"
            
            if alias is None:
                logger.info(f"\n{level_name}: (Not available)")
            else:
                logger.info(f"\n{level_name}:")
                logger.info(f"  Alias:      {alias}")
                logger.info(f"  Name:       {firstname} {lastname}")
                logger.info(f"  Job Level:  L{job_level}")
        
        print(f"{hierarchy}")
        
        return 0


if __name__ == "__main__":
    sys.exit(main())
