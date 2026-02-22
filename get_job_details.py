"""get_job_details.py

Get detailed job information from AtoZ Internal Transfer Portal API.

IMPORTANT: AtoZ uses Amazon Federate/Passport tokens (NOT Cognito tokens).
These tokens are httpOnly cookies that expire every ~1 hour.
They CANNOT be auto-refreshed and must be manually extracted from Network tab.

Setup (Network Tab Method):
    1. Open https://atoz.amazon.work in browser (logged in)
    2. Open DevTools (F12) → Network tab
    3. Refresh page or navigate to any job page
    4. Click on any request to atoz.amazon.work (status 200)
    5. In "Request Headers" section, find "cookie:" header
    6. Copy the ENTIRE value after "cookie: "
    7. Add to .env:
       JOB_DETAILS_COOKIE='paste entire cookie string here'
    
    See extract_atoz_cookie.js for detailed visual guide.
    Cookie expires in ~1 hour - re-extract when needed.

Usage:
   python get_job_details.py <job_id>
    
Example:
    python get_job_details.py 3185242
"""

import json
import logging
import os
import sys
from dotenv import load_dotenv
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def get_job_details(job_id):
    """Get detailed job information from AtoZ Internal Transfer Portal.
    
    Args:
        job_id (str): The iCIMS job ID
        
    Returns:
        dict: Job details response or None if failed
    """
    load_dotenv()
    
    # Get cookie string from environment
    cookie_string = os.getenv("JOB_DETAILS_COOKIE")
    
    # Validate we have authentication
    if not cookie_string:
        logger.error("❌ JOB_DETAILS_COOKIE not found in .env")
        logger.error("")
        logger.error("AtoZ uses httpOnly cookies (NOT accessible via JavaScript).")
        logger.error("These cookies expire every ~1 hour and must be extracted manually.")
        logger.error("")
        logger.error("Quick setup (Network Tab Method):")
        logger.error("  1. Open https://atoz.amazon.work (logged in)")
        logger.error("  2. DevTools (F12) → Network tab")
        logger.error("  3. Refresh page")
        logger.error("  4. Click any request to atoz.amazon.work")
        logger.error("  5. Find 'cookie:' in Request Headers")
        logger.error("  6. Copy ENTIRE cookie value")
        logger.error("  7. Add to .env:")
        logger.error("     JOB_DETAILS_COOKIE='paste here'")
        logger.error("")
        logger.error("📄 See extract_atoz_cookie.js for visual guide")
        return None
    
    api_base_url = os.getenv("JOB_DETAILS_API_URL")
    
    if not api_base_url:
        api_base_url = "https://atoz.amazon.work/apis/InternalTransferPortal/v1/job/details/icims"
    
    # Build the full URL
    url = f"{api_base_url}/{job_id}"
    
    # Set up headers matching the browser request
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "cookie": cookie_string,
        "referer": f"https://atoz.amazon.work/jobs/role/{job_id}",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }
    
    try:
        logger.info(f"Fetching job details for job ID: {job_id}")
        logger.debug(f"URL: {url}")
        
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code == 200:
            # Check if we got HTML (login page) instead of JSON
            content_type = response.headers.get('Content-Type', '')
            if 'text/html' in content_type or response.text.strip().startswith('<!doctype html>'):
                logger.error("❌ Got HTML login page instead of JSON - Authentication failed!")
                logger.error("")
                logger.error("Your AtoZ cookies have expired (~1 hour lifetime).")
                logger.error("")
                logger.error("Solution: Extract fresh cookie from Network tab:")
                logger.error("  1. Open https://atoz.amazon.work (logged in)")
                logger.error("  2. DevTools → Network tab → Refresh page")
                logger.error("  3. Click any request → Request Headers → cookie:")
                logger.error("  4. Copy entire value and update .env")
                logger.error("")
                logger.error("⚠️  Note: httpOnly cookies cannot be auto-refreshed!")
                return None
            try:
                data = response.json()
                logger.info(f"✅ Successfully retrieved job details for {job_id}")
                return data
            except json.JSONDecodeError as e:
                logger.error(f"❌ Failed to parse JSON response: {e}")
                logger.error(f"Response preview: {response.text[:200]}...")
                return None
                
        elif response.status_code == 401:
            logger.error("❌ Authentication failed (401 Unauthorized)")
            logger.error("Your AtoZ cookie has expired (~1 hour lifetime).")
            logger.error("")
            logger.error("Extract fresh cookie from Network tab - see extract_atoz_cookie.js")
            return None
        elif response.status_code == 403:
            logger.error("❌ Access forbidden (403)")
            return None
        elif response.status_code == 404:
            logger.error(f"❌ Job {job_id} not found (404)")
            return None
        else:
            logger.error(f"❌ Request failed with status {response.status_code}")
            logger.error(f"Response preview: {response.text[:200]}...")
            return None
            
    except requests.exceptions.Timeout:
        logger.error("❌ Request timed out")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Request error: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        return None


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python get_job_details.py <job_id>")
        print("Example: python get_job_details.py 3155554")
        sys.exit(1)
    
    job_id = sys.argv[1]
    
    result = get_job_details(job_id)

    if result:
        print("\n" + "="*80)
        print(f"JOB DETAILS FOR ID: {job_id}")
        print("="*80)
        print(json.dumps(result, indent=2))
        print("="*80)
    else:
        print(f"\n❌ Failed to retrieve job details for {job_id}")
        sys.exit(1)
