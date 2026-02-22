"""cognito_auth.py

Fetch fresh AWS credentials and refresh Cognito tokens automatically.

This module supports two authentication flows:

1. **Self Intro API Token Refresh** (for About Me Talent API)
   - Uses AtoZ Workforce User Pool (us-west-2_BWi3Em5ZC)
   - Refreshes SELF_INTRO_AUTH_TOKEN using COGNITO_REFRESH_TOKEN
   - Required for self-intro API calls

2. **AWS Credentials via Cognito Identity** (for AtoZ jobs API)
   - Uses Cognito Identity Pool for temporary AWS credentials
   - Automatically refreshes ID token if expired

Environment variables:
- COGNITO_REFRESH_TOKEN: The refresh token from localStorage (for auto-refresh)
- SELF_INTRO_AUTH_TOKEN: Current ID token for Self Intro API
- COGNITO_ID_TOKEN: The ID token from localStorage (for AWS credentials)
- COGNITO_CLIENT_ID: 6hr71icfdda6n67uvvm3nvlu4d (for AWS credentials)
- COGNITO_IDENTITY_POOL_ID: us-west-2:74ab0fc1-ddcb-43b1-a90d-32fec0b92043
- COGNITO_USER_POOL_PROVIDER: cognito-idp.us-west-2.amazonaws.com/us-west-2_fTk7zNMno

Usage:
    # For AWS credentials (AtoZ jobs API)
    from cognito_auth import get_fresh_credentials
    creds = get_fresh_credentials()
    
    # For Self Intro API token
    from cognito_auth import get_self_intro_token
    token = get_self_intro_token()
"""
import base64
import json
import logging
import os
import time
from typing import Dict, Optional

import requests

# Configure logger for this module
logger = logging.getLogger(__name__)


def decode_jwt_payload(token: str) -> Optional[Dict]:
    """Decode JWT payload to check expiration."""
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None
        # Add padding if needed
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += '=' * padding
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception as e:
        logger.error(f"Failed to decode JWT: {e}")
        return None


def is_token_expired(token: str, buffer_seconds: int = 300) -> bool:
    """Check if JWT token is expired or will expire within buffer_seconds."""
    payload = decode_jwt_payload(token)
    if not payload or 'exp' not in payload:
        return True
    
    expiration = payload['exp']
    current_time = int(time.time())
    return current_time >= (expiration - buffer_seconds)


def refresh_id_token(refresh_token: str, client_id: str) -> Optional[str]:
    """Use refresh token to get a new ID token from Cognito User Pool.
    
    Args:
        refresh_token: The refresh token from localStorage
        client_id: Cognito client ID (pool-specific)
    
    Returns:
        New ID token string, or None on failure
    """
    url = "https://cognito-idp.us-west-2.amazonaws.com/"
    headers = {
        "Content-Type": "application/x-amz-json-1.1",
        "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
        "X-Amz-User-Agent": "aws-amplify/5.0.4 js",
    }
    payload = {
        "ClientId": client_id,
        "AuthFlow": "REFRESH_TOKEN_AUTH",
        "AuthParameters": {
            "REFRESH_TOKEN": refresh_token,
            "DEVICE_KEY": None,
        }
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code != 200:
        logger.error(f"Token refresh failed: {resp.status_code} {resp.text}")
        return None

    data = resp.json()
    auth_result = data.get("AuthenticationResult", {})
    new_id_token = auth_result.get("IdToken")
    
    if new_id_token:
        logger.info("Successfully refreshed ID token")
    return new_id_token


def get_self_intro_token() -> Optional[str]:
    """Get a valid Self Intro API token, refreshing if needed.
    
    This function checks if SELF_INTRO_AUTH_TOKEN is expired, and if so,
    uses COGNITO_REFRESH_TOKEN to get a fresh token.
    
    Environment variables required:
    - COGNITO_REFRESH_TOKEN: Refresh token from AtoZ Workforce pool
    - SELF_INTRO_AUTH_TOKEN: Current ID token (optional, will be refreshed if expired)
    
    Returns:
        Valid ID token string, or None on failure
    """
    # AtoZ Workforce User Pool credentials
    ATOZ_CLIENT_ID = "1h1ms88guc4kn86rmdc6er1ecl"
    
    current_token = os.getenv("SELF_INTRO_AUTH_TOKEN")
    refresh_token = os.getenv("SELF_INTRO_COGNITO_REFRESH_TOKEN")
    
    # Check if current token is valid
    if current_token and not is_token_expired(current_token):
        logger.debug("SELF_INTRO_AUTH_TOKEN is still valid")
        return current_token
    
    # Need to refresh
    if not refresh_token:
        logger.error("SELF_INTRO_COGNITO_REFRESH_TOKEN not set in .env - cannot refresh token")
        logger.error("Please extract refresh token from browser localStorage and add to .env")
        return None
    
    if current_token:
        logger.info("SELF_INTRO_AUTH_TOKEN expired, refreshing...")
    else:
        logger.info("No SELF_INTRO_AUTH_TOKEN found, getting fresh token...")
    
    new_token = refresh_id_token(refresh_token, ATOZ_CLIENT_ID)
    if new_token:
        logger.info("✅ Successfully refreshed Self Intro token")
        # Optionally update .env file
        # os.environ["SELF_INTRO_AUTH_TOKEN"] = new_token
        return new_token
    
    logger.error("❌ Failed to refresh Self Intro token")
    return None


def get_atoz_tokens() -> Optional[Dict[str, str]]:
    """Get valid AtoZ job details API tokens by refreshing from COGNITO_REFRESH_TOKEN.
    
    Returns both ID token and OAuth/Access token for AtoZ API authentication.
    
    Environment variables required:
    - COGNITO_REFRESH_TOKEN: Refresh token from AtoZ Workforce pool
    
    Returns:
        Dict with 'id_token' and 'oauth_token', or None on failure
    """
    # AtoZ Workforce User Pool credentials
    ATOZ_CLIENT_ID = "1h1ms88guc4kn86rmdc6er1ecl"
    
    refresh_token = os.getenv("COGNITO_REFRESH_TOKEN")
    
    # Refresh token is required
    if not refresh_token:
        logger.error("COGNITO_REFRESH_TOKEN not set in .env - cannot get tokens")
        logger.error("Please extract refresh token from browser localStorage and add to .env")
        logger.error("See REFRESH_TOKEN_GUIDE.md for instructions")
        return None
    
    logger.debug("Getting fresh ATOZ tokens from refresh token...")
    
    # Call refresh endpoint
    url = "https://cognito-idp.us-west-2.amazonaws.com/"
    headers = {
        "Content-Type": "application/x-amz-json-1.1",
        "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
        "X-Amz-User-Agent": "aws-amplify/5.0.4 js",
    }
    payload = {
        "ClientId": ATOZ_CLIENT_ID,
        "AuthFlow": "REFRESH_TOKEN_AUTH",
        "AuthParameters": {
            "REFRESH_TOKEN": refresh_token,
            "DEVICE_KEY": None,
        }
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code != 200:
            logger.error(f"Token refresh failed: {resp.status_code} {resp.text}")
            return None

        data = resp.json()
        auth_result = data.get("AuthenticationResult", {})
        
        new_id_token = auth_result.get("IdToken")
        new_access_token = auth_result.get("AccessToken")
        
        if new_id_token and new_access_token:
            logger.info("✅ Successfully refreshed ATOZ tokens")
            return {
                'id_token': new_id_token,
                'oauth_token': new_access_token
            }
        else:
            logger.error("❌ No tokens in refresh response")
            return None
            
    except Exception as e:
        logger.error(f"❌ Failed to refresh ATOZ tokens: {e}")
        return None


def refresh_id_token_for_aws(refresh_token: str, client_id: str) -> Optional[str]:
    """Refresh ID token specifically for AWS credentials flow (legacy)."""
    return refresh_id_token(refresh_token, client_id)


def get_cognito_identity_id(jwt_token: str, identity_pool_id: str, user_pool_provider: str) -> Optional[str]:
    """Call Cognito GetId to get an Identity ID."""
    url = "https://cognito-identity.us-west-2.amazonaws.com/"
    headers = {
        "Content-Type": "application/x-amz-json-1.1",
        "X-Amz-Target": "AWSCognitoIdentityService.GetId",
    }
    payload = {"IdentityPoolId": identity_pool_id, "Logins": {user_pool_provider: jwt_token}}

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code != 200:
        logger.error(f"GetId failed: {resp.status_code} {resp.text}")
        return None

    data = resp.json()
    return data.get("IdentityId")


def get_credentials_for_identity(identity_id: str, jwt_token: str, user_pool_provider: str) -> Optional[Dict]:
    """Call Cognito GetCredentialsForIdentity to get temporary AWS credentials."""
    url = "https://cognito-identity.us-west-2.amazonaws.com/"
    headers = {
        "Content-Type": "application/x-amz-json-1.1",
        "X-Amz-Target": "AWSCognitoIdentityService.GetCredentialsForIdentity",
    }
    payload = {"IdentityId": identity_id, "Logins": {user_pool_provider: jwt_token}}

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code != 200:
        logger.error(f"GetCredentialsForIdentity failed: {resp.status_code} {resp.text}")
        return None

    data = resp.json()
    credentials = data.get("Credentials", {})
    return {
        "AccessKeyId": credentials.get("AccessKeyId"),
        "SecretKey": credentials.get("SecretKey"),
        "SessionToken": credentials.get("SessionToken"),
        "Expiration": credentials.get("Expiration"),
    }


def get_fresh_credentials() -> Optional[Dict]:
    """Fetch fresh AWS credentials using Cognito tokens from environment.
    
    Automatically refreshes ID token if expired or missing using refresh token.
    Returns dict with AccessKeyId, SecretKey, SessionToken, Expiration.
    Returns None if any step fails.
    """
    id_token = os.getenv("COGNITO_ID_TOKEN")
    refresh_token = os.getenv("COGNITO_REFRESH_TOKEN")
    client_id = os.getenv("COGNITO_CLIENT_ID", "6hr71icfdda6n67uvvm3nvlu4d")
    
    # If no ID token or it's expired, refresh it
    if not id_token or is_token_expired(id_token):
        if not id_token:
            logger.info("No ID token found, using refresh token to get new one...")
        else:
            logger.info("ID token expired, refreshing...")
            
        if not refresh_token:
            logger.error("COGNITO_REFRESH_TOKEN not set in .env")
            return None
        
        new_id_token = refresh_id_token_for_aws(refresh_token, client_id)
        if not new_id_token:
            logger.error("Failed to refresh token")
            return None
        
        id_token = new_id_token

    identity_pool_id = os.getenv("COGNITO_IDENTITY_POOL_ID", "us-west-2:74ab0fc1-ddcb-43b1-a90d-32fec0b92043")
    user_pool_provider = os.getenv(
        "COGNITO_USER_POOL_PROVIDER", "cognito-idp.us-west-2.amazonaws.com/us-west-2_fTk7zNMno"
    )

    # Step 1: Get Identity ID
    identity_id = get_cognito_identity_id(id_token, identity_pool_id, user_pool_provider)
    if not identity_id:
        return None

    logger.debug(f"Got Cognito Identity ID: {identity_id}")

    # Step 2: Get credentials
    creds = get_credentials_for_identity(identity_id, id_token, user_pool_provider)
    if not creds:
        return None

    logger.debug(f"Got fresh AWS credentials (expires: {creds.get('Expiration')})")
    return creds


if __name__ == "__main__":
    from dotenv import load_dotenv

    # Configure logging for CLI usage
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    load_dotenv()
    
    # Test both functions
    print("=" * 80)
    print("Testing Cognito authentication...")
    print("=" * 80)
    
    # Test Self Intro token
    print("\n1. Testing Self Intro API token refresh:")
    print("-" * 80)
    token = get_self_intro_token()
    if token:
        print(f"✅ Got Self Intro token (length: {len(token)} chars)")
        print(f"Token preview: {token[:50]}...")
        
        # Show token details
        payload = decode_jwt_payload(token)
        if payload:
            print(f"\nToken details:")
            print(f"  User: {payload.get('preferred_username', 'N/A')}")
            print(f"  Email: {payload.get('email', 'N/A')}")
            print(f"  Expires: {payload.get('exp', 'N/A')}")
    else:
        print("❌ Failed to get Self Intro token")
        print("\nMake sure you have COGNITO_REFRESH_TOKEN in .env")

    # Test AWS credentials
    print("\n" + "=" * 80)
    print("2. Testing AWS credentials fetch:")
    print("-" * 80)
    credentials = get_fresh_credentials()
    if credentials:
        print("✅ Got AWS credentials:")
        print(json.dumps(credentials, indent=2))
    else:
        logger.error("Failed to fetch AWS credentials")
        print("\nThis is expected if you don't have COGNITO_ID_TOKEN configured")
