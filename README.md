# amzn-internal-transfer-automator

Python automation for Amazon internal tools:
- **send_email.py**: Send emails via OWA (Outlook Web Access) using browser session
- **get_jobs.py**: Fetch job listings from internal transfer API with auto-refreshing AWS credentials
- **get_employee_details.py**: Fetch employee details by username/login using auto-refreshing AWS credentials
- **cognito_auth.py**: Fetch fresh AWS credentials from Cognito (used by `get_jobs.py` and `get_employee_details.py`)
- **job_matcher.py**: Apply heuristics to job listings to find candidate-suitable roles and produce match summaries
- **extract_tokens.js**: Small DevTools script to extract Cognito `refreshToken` (and optionally `idToken`) from Chrome localStorage

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

3. Get your browser cookies and JWT token:
   - **For OWA**: Open DevTools → Network → Find request to magnolia.amazon.com → Copy Cookie header
   - **For Jobs API**: 
     1. Open https://internal-transfer.talent.amazon.dev in Chrome
     2. Open DevTools (F12) → Console tab
     3. Copy and paste the content of [extract_tokens.js](extract_tokens.js)
     4. Copy only the `COGNITO_REFRESH_TOKEN` line to your `.env` file (the ID token is optional and will be auto-refreshed)

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

## Environment Variables

See [.env.example](.env.example) for all available options. This repository automates sending emails to HMs for matched internal job postings at Amazon.
