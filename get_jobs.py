"""get_jobs.py

Fetch job listings from the internal transfer jobs search API using fresh Cognito credentials.

Usage:
  python get_jobs.py
"""
import datetime
import hashlib
import hmac
import json
import os
import sys
from typing import Dict
from urllib.parse import parse_qsl, quote, urlparse

import requests
from dotenv import load_dotenv


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


def get_jobs():
    load_dotenv()

    default_url = (
        "https://api.prod.internal-transfer.talent.amazon.dev/v1/jobs/search"
        "?country=USA&jobCategory=Software%20Development&jobLevel=5&query=&sort=recent"
    )
    url = os.getenv("JOBS_API_URL", default_url)
    page_limit = int(os.getenv("JOBS_PAGE_LIMIT", "20"))

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
    except Exception as e:
        print(f"Failed to fetch Cognito credentials: {e}")
        return 1

    if not (access_key and secret_key):
        print("No AWS credentials available. Set COGNITO_REFRESH_TOKEN in .env")
        return 1

    sess = requests.Session()

    all_jobs = []
    start = 0
    total_found = None

    while True:
        # Build paged URL (append start & limit)
        if "?" in url:
            page_url = f"{url}&start={start}&limit={page_limit}"
        else:
            page_url = f"{url}?start={start}&limit={page_limit}"

        # Build headers for each request and sign (fresh x-amz-date)
        headers = {
            "Accept": "*/*",
            "Origin": "https://internal-transfer.talent.amazon.dev",
            "Referer": "https://internal-transfer.talent.amazon.dev/",
            "User-Agent": "python-requests/unknown",
        }

        print(f"GET {page_url}")
        print("Signing request with fresh Cognito credentials...")
        signed_headers = sign_request("GET", page_url, headers, access_key, secret_key, session_token)

        resp = sess.get(page_url, headers=signed_headers, timeout=30)
        print(f"status: {resp.status_code}")

        try:
            data = resp.json()
        except Exception:
            print("Non-JSON response:", resp.text[:500])
            return None

        if resp.status_code >= 400:
            print(json.dumps(data, indent=2))
            return None

        search = data.get("jobSearchResults", {})
        page_jobs = search.get("searchResults", []) or []

        # Append jobs
        all_jobs.extend(page_jobs)

        # Determine total found
        if total_found is None:
            total_found = search.get("found")
            if total_found is None:
                # If API doesn't return 'found', stop when a page returns fewer than limit
                total_found = None

        # Progress print
        print(f"Fetched {len(page_jobs)} jobs (start={start}) — total so far: {len(all_jobs)}")

        # Stop conditions
        if not page_jobs:
            break
        if total_found is not None and len(all_jobs) >= int(total_found):
            break

        # Advance start
        start += page_limit

    print(f"\nTotal jobs collected: {len(all_jobs)}")
    return all_jobs


if __name__ == "__main__":
    result = get_jobs()
    if result is None:
        sys.exit(1)
    # Print brief summary
    print(f"{json.dumps(result[0], indent=4) if result else 'No jobs found'}")
    print(json.dumps({"collected": len(result)}, indent=4))
    sys.exit(0)
