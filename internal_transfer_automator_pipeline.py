#!/usr/bin/env python
"""internal_transfer_automator_pipeline.py

Pipeline to send personalized emails to hiring managers based on job match results.

This script:
1. Scans per_job/ folder for individual job match results
2. Filters for YES decisions that haven't been emailed yet (using state file)
3. Groups jobs by hiring manager
4. For each hiring manager, creates personalized email with:
   - Manager's first name
   - List of matched job summaries
   - TO: Hiring manager + env TO_ADDRS
   - CC: Manager's chain (manager, skip-manager, etc.) + env CC_ADDRS
   - BCC: env BCC_ADDRS
5. Updates state file with sent job IDs

Usage:
  python internal_transfer_automator_pipeline.py [--dry-run] [--force-resend-all]
  
  --dry-run: Preview emails without sending them
  --force-resend-all: Ignore state file and resend all jobs

Environment Variables:
  JOB_MATCH_RESULTS_FOLDER_NAME - Folder containing per_job/ subfolder (default: output)
  SUBJECT - Email subject
  BODY - Email body HTML template (use {{manager_first_name}} and {{match_reasons}})
  TO_ADDRS - Additional TO addresses (comma-separated, optional)
  CC_ADDRS - Additional CC addresses (comma-separated, optional)
  BCC_ADDRS - BCC addresses (comma-separated, optional)
  COOKIE_STRING - OWA session cookies
  
State File:
  sent_emails_state.json - Tracks which job_ids have already been emailed
"""
import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set

from dotenv import load_dotenv

from send_email import send_email

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_state_file(state_path: Path) -> Set[str]:
    """Load the set of job IDs that have already been emailed.
    
    Args:
        state_path: Path to state file
        
    Returns:
        Set of job_id strings that have been sent
    """
    if not state_path.exists():
        logger.info(f"No state file found at {state_path}, starting fresh")
        return set()
    
    try:
        with open(state_path, 'r') as f:
            state = json.load(f)
        sent_jobs = set(state.get("sent_jobs", []))
        last_run = state.get("last_run")
        logger.info(f"Loaded state: {len(sent_jobs)} jobs already sent (last run: {last_run})")
        return sent_jobs
    except Exception as e:
        logger.error(f"Error loading state file: {e}")
        return set()


def save_state_file(state_path: Path, sent_jobs: Set[str]):
    """Save the set of sent job IDs to state file.
    
    Args:
        state_path: Path to state file
        sent_jobs: Set of job_id strings that have been sent
    """
    state = {
        "sent_jobs": sorted(list(sent_jobs)),
        "last_run": datetime.now().isoformat(),
        "total_sent": len(sent_jobs)
    }
    
    try:
        with open(state_path, 'w') as f:
            json.dump(state, f, indent=2)
        logger.info(f"State file updated: {len(sent_jobs)} total jobs tracked")
    except Exception as e:
        logger.error(f"Error saving state file: {e}")


def load_jobs_from_per_job_folder(results_folder: str, sent_jobs: Set[str]) -> List[Dict]:
    """Load individual job files from per_job/ folder and filter for unsent YES jobs.
    
    Args:
        results_folder: Base folder containing per_job/ subfolder
        sent_jobs: Set of job_ids that have already been emailed
        
    Returns:
        List of YES-decision jobs that haven't been sent yet
    """
    per_job_folder = Path(results_folder) / "per_job"
    
    if not per_job_folder.exists():
        logger.error(f"per_job folder not found: {per_job_folder}")
        raise FileNotFoundError(f"per_job folder not found: {per_job_folder}")
    
    logger.info(f"Scanning for job files in: {per_job_folder}")
    
    yes_jobs = []
    total_files = 0
    yes_count = 0
    already_sent = 0
    
    for job_file in per_job_folder.glob("*.json"):
        total_files += 1
        try:
            with open(job_file, 'r') as f:
                job = json.load(f)
            
            job_id = job.get("job_id")
            decision = job.get("decision")
            
            if decision == "YES":
                yes_count += 1
                if job_id in sent_jobs:
                    logger.debug(f"Job {job_id} already sent, skipping")
                    already_sent += 1
                else:
                    yes_jobs.append(job)
                    logger.debug(f"Job {job_id}: YES (new)")
            else:
                logger.debug(f"Job {job_id}: {decision} (skipping)")
                
        except Exception as e:
            logger.warning(f"Error loading {job_file.name}: {e}")
    
    logger.info(f"Scanned {total_files} job files: {yes_count} YES decisions, {already_sent} already sent, {len(yes_jobs)} new to send")
    
    return yes_jobs


def restructure_by_hiring_manager(yes_ranked_jobs: List[Dict]) -> Dict[str, List[Dict]]:
    """Group jobs by hiring manager username.
    
    Args:
        yes_ranked_jobs: List of YES-ranked job matches
        
    Returns:
        Dict mapping hiring_manager_username -> list of jobs
    """
    restructured = defaultdict(list)
    
    for job in yes_ranked_jobs:
        hiring_managers = job.get("hiring_manager_usernames", [])
        
        if not hiring_managers:
            logger.warning(f"Job {job.get('job_id')} has no hiring managers, skipping")
            continue
        
        # Use the first hiring manager
        hiring_manager = hiring_managers[0]
        restructured[hiring_manager].append(job)
    
    logger.info(f"Grouped {len(yes_ranked_jobs)} jobs across {len(restructured)} hiring managers")
    
    return dict(restructured)


def build_summary_list(jobs: List[Dict]) -> str:
    """Build HTML list of job summaries.
    
    Args:
        jobs: List of job match results for a hiring manager
        
    Returns:
        HTML string with <ul> list of job summaries
    """
    items = []
    for job in jobs:
        job_id = job.get("job_id", "Unknown")
        summary = job.get("summary_50w", "No summary available")
        title = job.get("title", "Unknown Title")
        
        items.append(
            f'  <li><strong>Job ID {job_id}</strong> ({title}) — {summary}</li>'
        )
    
    summary_html = "<ol>\n" + "\n".join(items) + "\n</ol>"
    return summary_html


def build_email_addresses(employee_hierarchy: List[List[str]]) -> Dict[str, List[str]]:
    """Extract email addresses from employee hierarchy.
    
    Args:
        employee_hierarchy: List of [alias, firstname, lastname, level]
        
    Returns:
        Dict with 'to' (hiring manager) and 'cc' (management chain)
    """
    if not employee_hierarchy:
        return {"to": [], "cc": []}
    
    # First person is the hiring manager (TO)
    hiring_manager_alias = employee_hierarchy[0][0]
    to_addrs = [f"{hiring_manager_alias}@amazon.com"]
    
    # Rest are the management chain (CC)
    cc_addrs = []
    for person in employee_hierarchy[1:]:
        if person[0]:  # Check alias exists
            cc_addrs.append(f"{person[0]}@amazon.com")
    
    return {"to": [], "cc": []}
    # return {"to": to_addrs, "cc": cc_addrs}


def prepare_email_content(
    hiring_manager: str,
    jobs: List[Dict],
    body_template: str,
    env_to_addrs: List[str],
    env_cc_addrs: List[str],
    env_bcc_addrs: List[str]
) -> Dict:
    """Prepare email content for a hiring manager.
    
    Args:
        hiring_manager: Hiring manager username
        jobs: List of matched jobs for this hiring manager
        body_template: Email body HTML template
        env_to_addrs: Additional TO addresses from env
        env_cc_addrs: Additional CC addresses from env
        env_bcc_addrs: BCC addresses from env
        
    Returns:
        Dict with to_addrs, cc_addrs, bcc_addrs, body_html
    """
    if not jobs:
        logger.warning(f"No jobs for hiring manager {hiring_manager}")
        return None
    
    # Get manager's first name from first job's employee_hierarchy
    first_job = jobs[0]
    employee_hierarchy = first_job.get("employee_hierarchy", [])
    
    if not employee_hierarchy or not employee_hierarchy[0]:
        logger.error(f"No employee hierarchy for hiring manager {hiring_manager}")
        return None
    
    manager_first_name = employee_hierarchy[0][1]  # First name is index 1
    
    # Build summary list
    summary_list = build_summary_list(jobs)
    
    # Replace template variables
    body_html = body_template.replace("{{manager_first_name}}", manager_first_name)
    body_html = body_html.replace("{{match_reasons}}", summary_list)
    
    # Build email addresses from hierarchy
    hierarchy_emails = build_email_addresses(employee_hierarchy)
    
    # Combine with env addresses
    to_addrs = hierarchy_emails["to"] + (env_to_addrs or [])
    cc_addrs = hierarchy_emails["cc"] + (env_cc_addrs or [])
    bcc_addrs = env_bcc_addrs or []
    
    return {
        "to_addrs": to_addrs,
        "cc_addrs": cc_addrs,
        "bcc_addrs": bcc_addrs,
        "body_html": body_html,
        "manager_first_name": manager_first_name,
        "job_count": len(jobs)
    }


def send_emails_to_hiring_managers(
    restructured_jobs: Dict[str, List[Dict]],
    subject: str,
    body_template: str,
    env_to_addrs: List[str],
    env_cc_addrs: List[str],
    env_bcc_addrs: List[str],
    dry_run: bool = False
) -> Dict:
    """Send personalized emails to each hiring manager.
    
    Args:
        restructured_jobs: Dict mapping hiring_manager -> list of jobs
        subject: Email subject
        body_template: Email body HTML template
        env_to_addrs: Additional TO addresses from env
        env_cc_addrs: Additional CC addresses from env
        env_bcc_addrs: BCC addresses from env
        dry_run: If True, print emails without sending
        
    Returns:
        Dict with send statistics and list of successfully sent job_ids
    """
    stats = {
        "total_managers": len(restructured_jobs),
        "emails_sent": 0,
        "emails_failed": 0,
        "errors": [],
        "sent_job_ids": []  # Track which jobs were successfully sent
    }
    
    for hiring_manager, jobs in restructured_jobs.items():
        logger.info(f"\n{'='*80}")
        logger.info(f"Processing hiring manager: {hiring_manager} ({len(jobs)} matched jobs)")
        
        # Prepare email content
        email_data = prepare_email_content(
            hiring_manager,
            jobs,
            body_template,
            env_to_addrs,
            env_cc_addrs,
            env_bcc_addrs
        )
        
        if not email_data:
            logger.error(f"Failed to prepare email for {hiring_manager}")
            stats["emails_failed"] += 1
            stats["errors"].append(f"Failed to prepare email for {hiring_manager}")
            continue
        
        logger.info(f"Manager: {email_data['manager_first_name']}")
        logger.info(f"Jobs: {email_data['job_count']}")
        logger.info(f"TO: {email_data['to_addrs']}")
        logger.info(f"CC: {email_data['cc_addrs']}")
        logger.info(f"BCC: {email_data['bcc_addrs']}")
        
        if dry_run:
            logger.info("DRY RUN - Email preview:")
            logger.info(f"Subject: {subject}")
            logger.info(f"Body:\n{email_data['body_html']}...")
            stats["emails_sent"] += 1
            # Track job IDs even in dry-run to show what would be marked as sent
            for job in jobs:
                job_id = job.get("job_id")
                if job_id:
                    stats["sent_job_ids"].append(job_id)
            continue
        
        # Send email
        try:
            result = send_email(
                to_addrs=email_data["to_addrs"],
                subject=subject,
                body_html=email_data["body_html"],
                cc_addrs=email_data["cc_addrs"],
                bcc_addrs=email_data["bcc_addrs"]
            )
            
            if result.get("success"):
                logger.info(f"✓ Email sent successfully to {hiring_manager}")
                stats["emails_sent"] += 1
                # Track job IDs that were successfully sent
                for job in jobs:
                    job_id = job.get("job_id")
                    if job_id:
                        stats["sent_job_ids"].append(job_id)
            else:
                error_msg = result.get("error", "Unknown error")
                logger.error(f"✗ Failed to send email to {hiring_manager}: {error_msg}")
                stats["emails_failed"] += 1
                stats["errors"].append(f"{hiring_manager}: {error_msg}")
                
        except Exception as e:
            logger.error(f"✗ Exception sending email to {hiring_manager}: {e}")
            stats["emails_failed"] += 1
            stats["errors"].append(f"{hiring_manager}: {str(e)}")
    
    return stats


def main():
    parser = argparse.ArgumentParser(
        description='Send personalized emails to hiring managers based on job matches',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview emails without sending them'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging (DEBUG level)'
    )
    parser.add_argument(
        '--force-resend-all',
        action='store_true',
        help='Ignore state file and resend emails for all YES jobs'
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Load environment variables
    load_dotenv()

    # Get configuration from env
    results_folder = os.getenv("JOB_MATCH_RESULTS_FOLDER_NAME", "output")
    STATE_FILE = "sent_emails_state.json"

    subject = os.getenv("SUBJECT")
    body_template = os.getenv("BODY")
    
    if not subject or not body_template:
        logger.error("SUBJECT and BODY must be set in .env")
        return 1
    
    # Parse additional email addresses from env
    env_to_addrs = []
    to_addrs_raw = os.getenv("TO_ADDRS")
    if to_addrs_raw:
        env_to_addrs = [s.strip() for s in to_addrs_raw.split(",") if s.strip()]
    
    env_cc_addrs = []
    cc_addrs_raw = os.getenv("CC_ADDRS")
    if cc_addrs_raw:
        env_cc_addrs = [s.strip() for s in cc_addrs_raw.split(",") if s.strip()]
    
    env_bcc_addrs = []
    bcc_addrs_raw = os.getenv("BCC_ADDRS")
    if bcc_addrs_raw:
        env_bcc_addrs = [s.strip() for s in bcc_addrs_raw.split(",") if s.strip()]
    
    logger.info("="*80)
    logger.info("INTERNAL TRANSFER AUTOMATOR PIPELINE")
    logger.info("="*80)
    logger.info(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    logger.info(f"Force resend all: {args.force_resend_all}")
    logger.info(f"Results folder: {results_folder}")
    logger.info(f"Additional TO: {env_to_addrs}")
    logger.info(f"Additional CC: {env_cc_addrs}")
    logger.info(f"BCC: {env_bcc_addrs}")
    
    # Load state file (or empty set if force resend)
    state_path = Path(results_folder) / STATE_FILE
    if args.force_resend_all:
        logger.warning("Force resend enabled - ignoring state file")
        sent_jobs = set()
    else:
        logger.info(f"Loading state file from: {state_path}")
        sent_jobs = load_state_file(state_path)
    
    # Load job files from per_job/ folder
    try:
        yes_ranked = load_jobs_from_per_job_folder(results_folder, sent_jobs)
    except FileNotFoundError as e:
        logger.error(str(e))
        return 1
    
    if not yes_ranked:
        logger.warning("No new YES-ranked jobs to send")
        return 0
    
    logger.info(f"Found {len(yes_ranked)} new YES-ranked jobs to send")
    
    # Restructure by hiring manager
    restructured_jobs = restructure_by_hiring_manager(yes_ranked)
    
    if not restructured_jobs:
        logger.warning("No jobs to process after restructuring")
        return 0
    
    # Send emails
    stats = send_emails_to_hiring_managers(
        restructured_jobs,
        subject,
        body_template,
        env_to_addrs,
        env_cc_addrs,
        env_bcc_addrs,
        dry_run=args.dry_run
    )
    
    # Update state file with successfully sent job IDs (only in live mode)
    if not args.dry_run and stats['sent_job_ids']:
        # Merge with existing sent jobs
        all_sent_jobs = sent_jobs | set(stats['sent_job_ids'])
        save_state_file(state_path, all_sent_jobs)
        logger.info(f"Updated state file with {len(stats['sent_job_ids'])} newly sent job(s)")
    
    # Print summary
    logger.info("\n" + "="*80)
    logger.info("SUMMARY")
    logger.info("="*80)
    logger.info(f"Total hiring managers: {stats['total_managers']}")
    logger.info(f"Emails sent: {stats['emails_sent']}")
    logger.info(f"Emails failed: {stats['emails_failed']}")
    logger.info(f"Jobs marked as sent: {len(stats['sent_job_ids'])}")
    
    if stats['errors']:
        logger.info("\nErrors:")
        for error in stats['errors']:
            logger.error(f"  - {error}")
    
    if args.dry_run:
        logger.info("\n*** DRY RUN MODE - No emails were actually sent ***")
        logger.info("*** State file NOT updated ***")
    
    return 0 if stats['emails_failed'] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
