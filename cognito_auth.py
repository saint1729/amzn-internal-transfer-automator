"""cognito_auth.py

Fetch fresh AWS credentials from Cognito with automatic token refresh.

This replicates the browser's credential fetching flow:
1. Check if idToken is expired, refresh it if needed using refreshToken
2. GetId: Exchange JWT for Cognito Identity ID
3. GetCredentialsForIdentity: Get temporary AWS credentials

Environment variables:
- COGNITO_ID_TOKEN: The ID token from localStorage (initial value)
- COGNITO_REFRESH_TOKEN: The refresh token from localStorage (for auto-refresh)
- COGNITO_CLIENT_ID: 6hr71icfdda6n67uvvm3nvlu4d
- COGNITO_IDENTITY_POOL_ID: us-west-2:74ab0fc1-ddcb-43b1-a90d-32fec0b92043
- COGNITO_USER_POOL_PROVIDER: cognito-idp.us-west-2.amazonaws.com/us-west-2_fTk7zNMno

Usage:
    from cognito_auth import get_fresh_credentials
    creds = get_fresh_credentials()
    # Returns: {"AccessKeyId": "...", "SecretKey": "...", "SessionToken": "...", "Expiration": "..."}
"""
import base64
import json
import os
import time
from typing import Dict, Optional

import requests


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
        print(f"Failed to decode JWT: {e}")
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
    """Use refresh token to get a new ID token from Cognito User Pool."""
    url = "https://cognito-idp.us-west-2.amazonaws.com/"
    headers = {
        "Content-Type": "application/x-amz-json-1.1",
        "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
    }
    payload = {
        "ClientId": client_id,
        "AuthFlow": "REFRESH_TOKEN_AUTH",
        "AuthParameters": {
            "REFRESH_TOKEN": refresh_token,
        }
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code != 200:
        print(f"Token refresh failed: {resp.status_code} {resp.text}")
        return None

    data = resp.json()
    auth_result = data.get("AuthenticationResult", {})
    new_id_token = auth_result.get("IdToken")
    
    if new_id_token:
        print("Successfully refreshed ID token")
    return new_id_token


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
        print(f"GetId failed: {resp.status_code} {resp.text}")
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
        print(f"GetCredentialsForIdentity failed: {resp.status_code} {resp.text}")
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
            print("No ID token found, using refresh token to get new one...")
        else:
            print("ID token expired, refreshing...")
            
        if not refresh_token:
            print("COGNITO_REFRESH_TOKEN not set in .env")
            return None
        
        new_id_token = refresh_id_token(refresh_token, client_id)
        if not new_id_token:
            print("Failed to refresh token")
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

    print(f"Got Cognito Identity ID: {identity_id}")

    # Step 2: Get credentials
    creds = get_credentials_for_identity(identity_id, id_token, user_pool_provider)
    if not creds:
        return None

    print(f"Got fresh AWS credentials (expires: {creds.get('Expiration')})")
    return creds


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    credentials = get_fresh_credentials()
    if credentials:
        print(json.dumps(credentials, indent=2))
    else:
        print("Failed to fetch credentials")
