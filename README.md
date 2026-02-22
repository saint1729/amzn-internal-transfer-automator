# amzn-internal-transfer-automator

Python automation for Amazon internal tools:

### Core Pipelines
- **request_informational_filler.py**: Generate LLM responses for informational requests (inference only, expensive)
- **internal_transfer_request_informational_automator_pipeline.py**: Submit pre-generated responses via API (submission only, cheap)
- **internal_transfer_email_automator_pipeline.py**: Automated email pipeline for hiring managers
- **job_matcher.py**: Apply heuristics and LLM scoring to find candidate-suitable roles

### API Utilities
- **get_jobs.py**: Fetch job listings from internal transfer API with auto-refreshing AWS credentials
- **get_job_details.py**: Fetch detailed job information using AtoZ httpOnly cookies
- **get_employee_details.py**: Fetch employee details by username with hierarchy traversal
- **send_email.py**: Send emails via OWA (Outlook Web Access) using browser session

### Authentication & Tools
- **cognito_auth.py**: Automatic token refresh for AWS Cognito authentication
- **refresh_cognito_token.py**: CLI tool to test Cognito token refresh
- **extract_tokens.js**: DevTools script to extract Cognito refresh tokens from browser localStorage
- **extract_atoz_cookie.js**: Visual guide for extracting AtoZ httpOnly cookies from Network tab

## Design

![Internal Transfers Automator - Design Diagram](Internal%20Transfers%20Automator%20-%20Design%20Diagram.jpg)

## Setup

1. Install dependencies:
```bash
bash install_requirements.sh
```

2. Copy `.env.example` to `.env` and fill in your values:
```bash
cp .env.example .env
```

3. **Get your Cognito refresh token (ONE-TIME SETUP):**
   
   The refresh token enables automatic token renewal for AWS-based APIs (job search, employee details), eliminating the need to manually extract tokens every hour.
   
   **Quick Method:**
   1. Open https://prod.aboutme.talent.amazon.dev in Chrome (logged in)
   2. Open DevTools (F12) → Application tab → Local Storage
   3. Find key containing `refreshToken` (e.g., `CognitoIdentityServiceProvider.1h1ms88guc4kn86rmdc6er1ecl.<user>.refreshToken`)
   4. Copy the value and add to `.env`:
      ```
      COGNITO_REFRESH_TOKEN=eyJjdHkiOiJKV1QiLCJlbmMiOiJBMjU2R0NNIi...
      ```
   
   **Alternative Method (JavaScript):**
   1. Open https://prod.aboutme.talent.amazon.dev
   2. Open DevTools (F12) → Console tab
   3. Run:
      ```javascript
      Object.keys(localStorage).filter(k => k.includes('refreshToken')).forEach(k => {
        console.log('Key:', k);
        console.log('Value:', localStorage.getItem(k));
      });
      ```
   4. Copy the token value to `.env`
   
   📖 **Detailed Guide**: See [REFRESH_TOKEN_GUIDE.md](REFRESH_TOKEN_GUIDE.md) for complete instructions

4. **Get manual authorization tokens (required for some APIs):**
   
   Some APIs require manual token extraction due to authentication limitations:
   
   **For Self Intro API (REQUEST_INFORMATIONAL_AUTHORIZATION):**
   1. Open https://prod.aboutme.talent.amazon.dev in Chrome (logged in)
   2. Open DevTools (F12) → Network tab
   3. Perform any action that triggers an API call
   4. Find a request with `authorization:` header
   5. Copy the bearer token value and add to `.env`:
      ```
      REQUEST_INFORMATIONAL_AUTHORIZATION=eyJraWQiOiJKR1lPYlJiMz...
      ```
   
   **For AtoZ Job Details (JOB_DETAILS_COOKIE):**
   1. Open https://atoz.amazon.work in browser (logged in)
   2. Open DevTools (F12) → Network tab
   3. Refresh the page
   4. Click any request to atoz.amazon.work
   5. Find `cookie:` in Request Headers
   6. Copy ENTIRE cookie value and add to `.env`:
      ```
      JOB_DETAILS_COOKIE='paste entire cookie string here'
      ```
   
   ⚠️ **Note:** These tokens expire (~1 hour for AtoZ, variable for Self Intro) and must be re-extracted when expired.

5. **(Optional) Verify automatic token refresh works:**
   ```bash
   python refresh_cognito_token.py
   ```
   
   You should see:
   ```
   ✅ Token refresh successful!
   Token expires in: 59 minutes
   ```

## Usage

### Send Email via OWA

**CLI Usage (reads from .env):**
```bash
source .venv/bin/activate
python send_email.py
```

**Programmatic Usage (function-based):**
```python
from send_email import send_email

# Simple usage
result = send_email(
    to_addrs=["user@amazon.com"],
    subject="Test Email",
    body_html="<p>Hello!</p>"
)

# With CC and BCC
result = send_email(
    to_addrs=["recipient@amazon.com"],
    cc_addrs=["cc1@amazon.com", "cc2@amazon.com"],
    bcc_addrs=["bcc@amazon.com"],
    subject="Project Update",
    body_html="<p>See attached update...</p>"
)

if result["success"]:
    print("Email sent successfully!")
else:
    print(f"Failed: {result.get('error')}")
```

**Note:** The function reads `COOKIE_STRING` and action IDs from `.env` automatically. You can override by passing `cookie_string`, `create_action_id`, etc. as parameters.

### Fetch Job Listings
```bash
source .venv/bin/activate
python get_jobs.py
```

**Auto-refresh:** The script automatically refreshes expired ID tokens using the refresh token, so you won't need to manually update tokens frequently. The refresh token lasts much longer (typically months).

### Automated Informational Request Pipeline (Two-File Architecture) 🚀

**Two-stage pipeline** that separates expensive LLM inference from cheap API submission, using Google ADK with Gemini for intelligent response generation.

**Architecture:**
1. **request_informational_filler.py** - Inference stage (expensive, run once)
2. **internal_transfer_request_informational_automator_pipeline.py** - Submission stage (cheap, can retry many times)

**Why Split?**
- 💰 **Cost-efficient**: Generate LLM responses once, submit many times without re-generating
- 🔄 **Retryable**: Failed submissions don't waste LLM costs
- ⚡ **Parallel processing**: Both stages use rate limiting (10 concurrent) for speed
- 🧪 **Testable**: Dry run mode to preview submissions without API calls

#### Stage 1: Generate Responses (Inference)

**Features:**
- 🤖 Uses Google ADK (Agentic Development Kit) with Gemini 2.5 Pro
- 📝 Generates three personalized sections (~140 words each):
  - Why you're interested in the role
  - Your relevant qualifications and experience
  - Forte context summary
- 💾 Saves responses to `output/per_job_request_informational/{job_id}.json`
- ⏭️ Skips jobs with existing responses (unless `--force`)
- ⚡ Rate-limited to 10 concurrent LLM requests

**Prerequisites:**
- `GOOGLE_API_KEY` for Gemini API
- `CANDIDATE_SUMMARY` in `.env` (your background)
- `MODEL_STRONG` in `.env` (e.g., `gemini-2.0-flash-001`)

**Usage:**
```bash
source .venv/bin/activate

# Generate responses for jobs from a file
python request_informational_filler.py --file output/sent_emails_state.json

# Generate for specific job IDs
python request_informational_filler.py 3185641 3184578 3200123

# Force regenerate even if responses exist
python request_informational_filler.py --file jobs.json --force
```

**Output:**
```
2026-02-21 15:30:00 - INFO - Found 5 job(s) to process
2026-02-21 15:30:01 - INFO - Skipping 2 job(s) with existing responses
2026-02-21 15:30:02 - INFO - Generating responses for 3 job(s)
2026-02-21 15:30:10 - INFO - ✓ Generated interest reason (142 words)
2026-02-21 15:30:15 - INFO - ✓ Generated qualifications (138 words)
2026-02-21 15:30:20 - INFO - ✓ Generated Forte context (140 words)
2026-02-21 15:30:25 - INFO - ✓ Inference completed: 3, Failed: 0
```

#### Stage 2: Submit Requests (Submission)

**Features:**
- 📤 Auto-discovers jobs with generated responses
- 🎯 Only submits jobs not already processed
- 🔍 Dry run mode to preview without submitting
- ⚡ Rate-limited to 10 concurrent API submissions
- 📊 Tracks state in `output/request_informational_results.json`

**Prerequisites:**
- `REQUEST_INFORMATIONAL_AUTHORIZATION` in `.env` (bearer token from browser)
- `REQUESTER_PEOPLE_SOFT_ID` in `.env` (your employee ID)
- Responses must be generated first (Stage 1)

**Usage:**
```bash
source .venv/bin/activate

# Submit all jobs with generated responses
python internal_transfer_request_informational_automator_pipeline.py

# Submit specific jobs (if they have responses)
python internal_transfer_request_informational_automator_pipeline.py 3185641 3184578

# Dry run - preview without submitting
python internal_transfer_request_informational_automator_pipeline.py --dry-run

# Force resubmit already submitted jobs
python internal_transfer_request_informational_automator_pipeline.py --force

# Dry run specific job
python internal_transfer_request_informational_automator_pipeline.py 3185641 --dry-run
```

**Example Output (Dry Run):**
```
2026-02-21 15:45:00 - INFO - [DRY RUN MODE] Would submit 3 job(s)
2026-02-21 15:45:01 - INFO - [DRY RUN] Submitting job ID: 3185641
2026-02-21 15:45:02 - INFO - [DRY RUN] Would submit informational request for job 3185641
2026-02-21 15:45:03 - INFO - [DRY RUN] URL: https://data.prod.movement.talent.amazon.dev/v1/selfIntroduction
2026-02-21 15:45:04 - INFO - [DRY RUN] Payload:
{
  "accomplishments": [...],
  "candidatePeopleSoftId": "112836877",
  "jobId": {"icims": "3185641"},
  "qualifications": "I have 5 years of experience...",
  "selfIntroduction": "I'm excited about this role...",
  "forteContext": "My strengths include...",
  "shareForte": true
}
2026-02-21 15:45:05 - INFO - [DRY RUN] ✓ Would successfully submit (simulated)
2026-02-21 15:45:06 - INFO - [DRY RUN MODE] No requests were actually sent, no files were modified
```

**Complete Workflow:**
```bash
# Step 1: Generate responses (expensive - once)
python request_informational_filler.py --file output/sent_emails_state.json

# Step 2: Preview submissions (dry run)
python internal_transfer_request_informational_automator_pipeline.py --dry-run

# Step 3: Submit for real
python internal_transfer_request_informational_automator_pipeline.py

# If some fail, retry just those (responses already generated)
python internal_transfer_request_informational_automator_pipeline.py
```

**Token Management:**
⚠️ **Manual token required**: The Self Intro API requires `REQUEST_INFORMATIONAL_AUTHORIZATION` token extracted from browser. This token expires periodically and must be re-extracted (see Setup section above).

**File Structure:**
```
output/
├── per_job_request_informational/
│   ├── 3185641.json  # Individual job with responses
│   ├── 3184578.json
│   └── 3200123.json
└── request_informational_results.json  # Consolidated tracking (submissions only)
```


### Fetch Job Details

**Uses AtoZ httpOnly cookies** (must be manually extracted from browser Network tab).

⚠️ **Important**: AtoZ uses httpOnly cookies that:
- Cannot be accessed via JavaScript
- Expire every ~1 hour
- Must be manually re-extracted from Network tab when expired

**Setup:**
```bash
# Add to .env (must re-extract every ~1 hour)
JOB_DETAILS_COOKIE='paste entire cookie string from Network tab here'
```

**CLI Usage:**
```bash
source .venv/bin/activate

# Get details for a specific job ID
python get_job_details.py 3185641
```

**Programmatic Usage:**
```python
from get_job_details import get_job_details

# Get details for a specific job
job_details = get_job_details("3185641")

if job_details:
    print(f"Title: {job_details.get('job', {}).get('role', {}).get('title')}")
    print(f"Description: {job_details.get('job', {}).get('descriptionInternal')}")
    print(f"Department: {job_details.get('job', {}).get('department', {}).get('name')}")
    
    # Full JSON available
    import json
    print(json.dumps(job_details, indent=2))
```

**Batch Usage:**
```python
from get_job_details import get_job_details

job_ids = ["3185641", "3184578", "3200123"]
for job_id in job_ids:
    details = get_job_details(job_id)
    if details:
        title = details.get('job', {}).get('role', {}).get('title', 'Unknown')
        print(f"Job {job_id}: {title}")
```

**What you get:**
- Job title, description, department
- Basic and preferred qualifications
- Hiring manager information
- Job location and level
- Application status

**Environment Variables:**
- `JOB_DETAILS_COOKIE`: (Required) Full cookie string from browser Network tab (expires ~1 hour)
- `JOB_DETAILS_API_URL`: (Optional) Override base API URL (default: AtoZ job details endpoint)

### Fetch Employee Details

**CLI Usage (Hierarchy - Default):**
```bash
source .venv/bin/activate

# Get hierarchy up to nearest L8 (default)
python get_employee_details.py <username>

# Example:
python get_employee_details.py saintamz

# Get hierarchy up to nearest L7:
python get_employee_details.py saintamz --target-level 7

# Get hierarchy up to nearest L10:
python get_employee_details.py saintamz --target-level 10

# Get raw JSON output (original behavior):
python get_employee_details.py saintamz --json

# Verbose logging (DEBUG level):
python get_employee_details.py saintamz --verbose

# Quiet mode (WARNING+ only):
python get_employee_details.py saintamz --quiet
```

**Programmatic Usage (Hierarchy):**
```python
import logging
from get_employee_details import get_employee_hierarchy

# Optional: configure logging level for library use
logging.basicConfig(level=logging.WARNING)  # or INFO, DEBUG

# Get hierarchy up to nearest L8 (default)
hierarchy = get_employee_hierarchy("saintamz", target_level=8)

# Returns list of tuples:
# [
#   ('saintamz', 'Sai Nikhil', 'Thirandas', '5'),      # Employee (L5)
#   ('xiongzho', 'Xiong', 'Zhou', '6'),                 # Manager (L6)
#   ('ivasilei', 'Vassilis', 'Ioannidis', '6'),        # Skip Manager (L6)
#   ('rhuzefa', 'Huzefa', 'Rangwala', '7'),            # Skip+1 (L7)
#   ('gkarypis', 'George', 'Karypis', '8')             # Skip+2 (L8) - TARGET REACHED
# ]

# Each tuple: (alias, firstname, lastname, job_level)
# Fetches until target_level is reached or no more managers exist

# Use the data:
if len(hierarchy) > 0:
    employee = hierarchy[0]
    print(f"Employee: {employee[1]} {employee[2]} ({employee[0]}) - L{employee[3]}")

# Find the highest level person (last in list)
if hierarchy:
    highest = hierarchy[-1]
    print(f"Highest level: {highest[1]} {highest[2]} - L{highest[3]}")
```

**Programmatic Usage (Raw JSON):**
```python
from get_employee_details import get_employee_details

details = get_employee_details("mrkcath")
if details:
    print(f"Name: {details['firstName']} {details['lastName']}")
    print(f"Title: {details['businessTitle']}")
    print(f"Level: {details['jobLevel']}")
    print(f"Email: {details['primaryEmail']}")
    print(f"Manager: {details['managerEmployeeIds']['login']}")
```

Returns employee information including name, title, job level, manager, cost center, location, tenure, and more.

**Features:**
- **Target level search**: Automatically fetches up the management chain until target level is reached
- **Flexible**: Specify any target level (L7, L8, L10, etc.)
- **Auto-stop**: Stops when target is reached or no more managers exist
- **Auto-refresh credentials**: Uses same Cognito flow as `get_jobs.py`

### Job Matching (usage)

- **Script:** `job_matcher.py` — applies configurable heuristics to a list of job objects to surface candidate-suitable roles.
- **Inputs:** a jobs JSON file (from `get_jobs.py`) or omit `--jobs-file` to let the matcher call `get_jobs.py` directly.
- **Matching rules:** title keywords, location, level, skills, and job family; scores jobs and returns top matches per candidate profile.
- **Outputs:** JSON or CSV summary of matches with scores and links; a shortlist per candidate is produced if multiple profiles provided.
- **How to run:**

```bash
source .venv/bin/activate
python job_matcher.py --jobs-file jobs.json --output matches.json
```

Adjust weighting, keywords, and filters inside `job_matcher.py` to tune matching behavior.

### Email Automation Pipeline

**Script:** `internal_transfer_email_automator_pipeline.py` — automated pipeline for sending introduction emails to hiring managers for matched jobs.

**Features:**
- 📧 Sends personalized emails to hiring managers
- 🎯 Tracks sent emails to avoid duplicates
- 🔄 Retryable with state management
- 🧪 Dry run mode to preview emails

**Usage:**
```bash
source .venv/bin/activate

# Dry run - preview emails without sending
python internal_transfer_email_automator_pipeline.py --dry-run

# Send emails for real
python internal_transfer_email_automator_pipeline.py

# Force resend to all (ignores sent state)
python internal_transfer_email_automator_pipeline.py --force-resend-all
```

**Prerequisites:**
- `COOKIE_STRING` in `.env` (OWA cookie from browser)
- Email templates and configuration in script

## Authentication Summary

### ✅ Automatic Token Refresh (One-Time Setup)
These APIs use `COGNITO_REFRESH_TOKEN` with automatic refresh (~30 day lifetime):
- **get_jobs.py** - Job search API
- **get_employee_details.py** - Employee details API

### ⚠️ Manual Token Extraction (Periodic Re-extraction Required)
These APIs require manual token/cookie extraction from browser:

| API | Variable | Lifetime | Extraction Method |
|-----|----------|----------|-------------------|
| Self Intro API | `REQUEST_INFORMATIONAL_AUTHORIZATION` | Variable | DevTools → Network → Copy bearer token |
| AtoZ Job Details | `JOB_DETAILS_COOKIE` | ~1 hour | DevTools → Network → Copy entire cookie string |
| OWA Email | `COOKIE_STRING` | Variable | DevTools → Network → Copy cookie from magnolia.amazon.com |

**Why manual?**
- **AtoZ**: Uses httpOnly cookies (cannot be accessed via JavaScript, cannot auto-refresh)
- **Self Intro**: Cognito auto-refresh was causing 400 errors, switched to manual bearer token

## Environment Variables

See [.env.example](.env.example) for all available options. This repository automates sending emails to HMs for matched internal job postings at Amazon.
