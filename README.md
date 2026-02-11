# amzn-internal-transfer-automator

Python automation for Amazon internal tools:
- **send_email.py**: Send emails via OWA (Outlook Web Access) using browser session
- **get_jobs.py**: Fetch job listings from internal transfer API with auto-refreshing AWS credentials
- **cognito_auth.py**: Fetch fresh AWS credentials from Cognito (used by `get_jobs.py`)
- **job_matcher.py**: Apply heuristics to job listings to find candidate-suitable roles and produce match summaries
- **extract_tokens.js**: Small DevTools script to extract Cognito `refreshToken` (and optionally `idToken`) from Chrome localStorage

## Setup

1. Install dependencies:
```bash
bash install_requirements.sh
```

2. Copy `.env.example` to `.env` and fill in your values:
```bash
cp .env.example .env
```

3. Get your browser cookies and JWT token:
   - **For OWA**: Open DevTools → Network → Find request to magnolia.amazon.com → Copy Cookie header
   - **For Jobs API**: 
     1. Open https://internal-transfer.talent.amazon.dev in Chrome
     2. Open DevTools (F12) → Console tab
     3. Copy and paste the content of [extract_tokens.js](extract_tokens.js)
     4. Copy only the `COGNITO_REFRESH_TOKEN` line to your `.env` file (the ID token is optional and will be auto-refreshed)

## Usage

### Send Email via OWA
```bash
source .venv/bin/activate
python send_email.py
```

### Fetch Job Listings
```bash
source .venv/bin/activate
python get_jobs.py
```

**Auto-refresh:** The script automatically refreshes expired ID tokens using the refresh token, so you won't need to manually update tokens frequently. The refresh token lasts much longer (typically months).

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

## Environment Variables

See [.env.example](.env.example) for all available options. This repository automates sending emails to HMs for matched internal job postings at Amazon.
