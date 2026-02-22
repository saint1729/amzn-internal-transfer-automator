#!/usr/bin/env python
"""internal_transfer_request_informational_automator_pipeline.py

Pipeline to submit informational requests to hiring managers using pre-generated responses.

This script:
1. Scans per_job_request_informational/ folder for jobs with generated responses
2. Loads request_informational_results.json to track submission status
3. Skips already submitted jobs (unless --force is used)
4. Submits informational requests via POST API for jobs with responses
5. Updates per-job files with submission status
6. Updates request_informational_results.json with processed/not_processed state

Note: This script ONLY submits requests. It does NOT generate responses.
Use request_informational_filler.py to generate responses first.

Workflow:
1. First run request_informational_filler.py to generate responses (expensive LLM calls)
2. Then run this script to submit (cheap API calls)
3. Can retry failed submissions multiple times without regenerating responses

Consolidated file: output/request_informational_results.json
{
  "processed": {
    "job_id": {
      "status": "success",
      "reason": "Successfully submitted informational request",
      "timestamp": "...",
      "job_title": "...",
      "department": "...",
      "responses_generated": true
    }
  },
  "not_processed": {
    "job_id": {
      "status": "error",
      "reason": "Failed to submit informational request",
      "timestamp": "...",
      "responses_generated": true
    }
  }
}

Usage:
    # Submit all jobs with generated responses
    python internal_transfer_request_informational_automator_pipeline.py
    
    # Submit specific jobs only (if they have responses)
    python internal_transfer_request_informational_automator_pipeline.py 3185242 3185630
    
    # Force resubmit already submitted jobs
    python internal_transfer_request_informational_automator_pipeline.py --force
"""
import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv
import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants
ACCOMPLISHMENTS = [
    {
        "title": "Amazon Internal Transfer Automator - Agentic Application",
        "link": "https://github.com/saint1729/amzn-internal-transfer-automator"
    },
    {
        "title": "Agentic Design Patterns - Applied Learning",
        "link": "https://saint1729.me/books/agentic-design-patterns/"
    },
    {
        "title": "Open Source Contribution - lm-evaluation-harness",
        "link": "https://github.com/EleutherAI/lm-evaluation-harness/pull/3547"
    }
]


def load_config() -> Dict[str, str]:
    """Load and validate required environment variables."""
    load_dotenv()
    
    required_vars = [
        'REQUESTER_PEOPLE_SOFT_ID',
        'REQUEST_INFORMATIONAL_AUTHORIZATION'
    ]
    
    config = {}
    missing_vars = []
    
    for var in required_vars:
        value = os.getenv(var)
        if not value:
            missing_vars.append(var)
        else:
            config[var] = value
    
    if missing_vars:
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        sys.exit(1)
    
    logger.info("Configuration loaded successfully")
    return config


def get_request_informational_jobs_file() -> Path:
    """Get the path to the request informational jobs tracking file."""
    results_folder = os.getenv("JOB_MATCH_RESULTS_FOLDER_NAME", "output")
    output_dir = Path(results_folder)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "request_informational_results.json"


def get_per_job_dir() -> Path:
    """Get the directory for per-job intermediate results."""
    results_folder = os.getenv("JOB_MATCH_RESULTS_FOLDER_NAME", "output")
    per_job_dir = Path(results_folder) / "per_job_request_informational"
    per_job_dir.mkdir(parents=True, exist_ok=True)
    return per_job_dir


def save_job_result(job_id: str, result: Dict) -> None:
    """Save individual job result to per-job file immediately after processing."""
    per_job_dir = get_per_job_dir()
    job_file = per_job_dir / f"{job_id}.json"
    
    try:
        with open(job_file, 'w') as f:
            json.dump(result, f, indent=2)
        logger.debug(f"Saved job {job_id} result to {job_file}")
    except Exception as e:
        logger.error(f"Failed to save job {job_id} result: {e}")


def load_previous_job_result(job_id: str) -> Optional[Dict]:
    """Load previous job result from per-job file if it exists."""
    per_job_dir = get_per_job_dir()
    job_file = per_job_dir / f"{job_id}.json"
    
    if not job_file.exists():
        return None
    
    try:
        with open(job_file, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load previous result for job {job_id}: {e}")
        return None


def accumulate_all_job_results() -> Dict:
    """Accumulate all per-job results from individual files."""
    per_job_dir = get_per_job_dir()
    
    accumulated_data = {
        "processed": {},
        "not_processed": {},
        "last_run": None,
        "total_processed": 0,
        "total_not_processed": 0
    }
    
    if not per_job_dir.exists():
        return accumulated_data
    
    # Read all job files
    job_files = list(per_job_dir.glob("*.json"))
    logger.info(f"Found {len(job_files)} job result files in {per_job_dir}")
    
    for job_file in job_files:
        try:
            with open(job_file, 'r') as f:
                result = json.load(f)
            
            job_id = result.get("job_id", job_file.stem)
            status = result.get("status", "unknown")
            
            # Only actual successful submissions go to "processed"
            if status == "success":
                accumulated_data["processed"][job_id] = {
                    "job_id": job_id,
                    "status": status,
                    "reason": result.get("reason", ""),
                    "timestamp": result.get("timestamp", ""),
                    "job_title": result.get("job_title"),
                    "department": result.get("department"),
                    "responses_generated": result.get("responses") is not None
                }
            else:
                # Includes: inference_complete, inference_error, submission_error
                accumulated_data["not_processed"][job_id] = {
                    "job_id": job_id,
                    "status": status,
                    "reason": result.get("reason", "Unknown error"),
                    "timestamp": result.get("timestamp", ""),
                    "job_title": result.get("job_title"),
                    "department": result.get("department"),
                    "responses_generated": result.get("responses") is not None
                }
        except Exception as e:
            logger.warning(f"Failed to load {job_file}: {e}")
    
    return accumulated_data


def load_request_informational_jobs() -> Dict:
    """Load previously processed jobs from consolidated tracking file.
    
    Returns dict with 'processed' and 'not_processed' entries.
    """
    file_path = get_request_informational_jobs_file()
    
    if not file_path.exists():
        logger.info("No existing request_informational_results.json found")
        return {
            "processed": {},
            "not_processed": {},
            "last_run": None,
            "total_processed": 0,
            "total_not_processed": 0
        }
    
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
        logger.info(f"Loaded {len(data.get('processed', {}))} processed, {len(data.get('not_processed', {}))} not processed from {file_path}")
        return data
    except Exception as e:
        logger.error(f"Failed to load request informational jobs file: {e}")
        return {
            "processed": {},
            "not_processed": {},
            "last_run": None,
            "total_processed": 0,
            "total_not_processed": 0
        }


def save_request_informational_jobs(data: Dict) -> None:
    """Save processed jobs data to tracking file."""
    file_path = get_request_informational_jobs_file()
    
    # Update counters and timestamp
    data["total_processed"] = len(data.get("processed", {}))
    data["total_not_processed"] = len(data.get("not_processed", {}))
    data["last_run"] = datetime.now().isoformat()
    
    try:
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info(f"✓ Saved results to {file_path}")
    except Exception as e:
        logger.error(f"Failed to save request informational jobs file: {e}")


def clean_responses(responses: Dict) -> Dict:
    """Clean up LLM-generated responses by removing any meta-text prefixes.
    
    Args:
        responses: Dict with keys: interest_reason, qualifications, forte_context
        
    Returns:
        Cleaned responses dict
    """
    cleaned = responses.copy()
    
    # Clean forte_context - remove meta-text prefix
    if "forte_context" in cleaned:
        forte_context = cleaned["forte_context"].strip()
        meta_prefixes = [
            "Here is a summary of the candidate's Forte context for the hiring manager:\n\n",
            "Here is a summary of the candidate's Forte and recent work:\n\n",
            "Here is a summary:\n\n",
            "Summary:\n\n",
            "Context:\n\n",
            "Here is a summary of the candidate's Forte context for the hiring manager:",
            "Here is a summary of the candidate's Forte and recent work:",
            "Here is a summary:",
            "Summary:",
            "Context:"
        ]
        for prefix in meta_prefixes:
            if forte_context.startswith(prefix):
                forte_context = forte_context[len(prefix):].strip()
                logger.debug(f"Removed prefix '{prefix.strip()}' from forte_context")
                break
        cleaned["forte_context"] = forte_context
    
    return cleaned


def submit_informational_request(config: Dict, job_id: str, responses: Dict, dry_run: bool = False) -> bool:
    """Submit informational request via POST API.
    
    Uses Authorization header for authentication.
    
    Args:
        config: Configuration dict with authorization header value
        job_id: Job ID (e.g., "3185641")
        responses: Dict with keys: interest_reason, qualifications, forte_context
        dry_run: If True, only show what would be submitted without making the request
    
    Returns:
        True if successful, False otherwise
    """
    url = "https://data.prod.movement.talent.amazon.dev/v1/selfIntroduction"
    
    headers = {
        "accept": "application/json, text/plain, */*",
        "authorization": config['REQUEST_INFORMATIONAL_AUTHORIZATION'],
        "content-type": "application/json",
        "origin": "https://prod.aboutme.talent.amazon.dev",
        "referer": "https://prod.aboutme.talent.amazon.dev/"
    }
    
    params = {
        "requesterPeopleSoftId": config['REQUESTER_PEOPLE_SOFT_ID']
    }
    
    # Clean up responses (remove any meta-text prefixes)
    cleaned_responses = clean_responses(responses)
    
    payload = {
        "accomplishments": ACCOMPLISHMENTS,
        "candidatePeopleSoftId": config['REQUESTER_PEOPLE_SOFT_ID'],
        "jobId": {
            "icims": job_id
        },
        "qualifications": cleaned_responses['qualifications'],
        "selfIntroduction": cleaned_responses['interest_reason'],
        "forteContext": cleaned_responses['forte_context'],
        "shareForte": True
    }
    
    try:
        if dry_run:
            logger.info(f"[DRY RUN] Would submit informational request for job {job_id}")
            logger.info(f"[DRY RUN] URL: {url}")
            logger.info(f"[DRY RUN] Params: {json.dumps(params, indent=2)}")
            logger.info(f"[DRY RUN] Headers: {json.dumps({k: v if k != 'authorization' else '***REDACTED***' for k, v in headers.items()}, indent=2)}")
            logger.info(f"[DRY RUN] Payload:")
            logger.info(json.dumps(payload, indent=2))
            logger.info(f"[DRY RUN] ✓ Would successfully submit (simulated)")
            return True
        
        logger.info(f"Submitting informational request for job {job_id}...")
        response = requests.post(url, headers=headers, params=params, json=payload)
        
        if response.status_code == 200 or response.status_code == 201:
            logger.info(f"✓ Successfully submitted request for job {job_id}")
            return True
        else:
            logger.error(f"✗ Failed to submit request for job {job_id}: {response.status_code}")
            logger.error(f"Response: {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"✗ Exception while submitting request for job {job_id}: {e}")
        return False


async def process_job_submission(job_id: str, config: Dict, dry_run: bool = False) -> Dict:
    """Submit informational request for a single job using pre-generated responses.
    
    Args:
        job_id: Job ID to process
        config: Configuration dict
        dry_run: If True, only show what would be submitted
    
    Returns:
        Dict with keys: job_id, status, reason, timestamp, job_title, department, responses
    """
    # Load previous result to get responses and metadata
    previous_result = load_previous_job_result(job_id)
    
    if not previous_result:
        logger.error(f"No per-job file found for {job_id}")
        return {
            "job_id": job_id,
            "status": "submission_error",
            "reason": "No per-job file found",
            "timestamp": datetime.now().isoformat(),
            "job_title": None,
            "department": None,
            "responses": None
        }
    
    if not previous_result.get("responses"):
        logger.error(f"No responses found for job {job_id}")
        return {
            "job_id": job_id,
            "status": "submission_error",
            "reason": "No responses available - run request_informational_filler.py first",
            "timestamp": datetime.now().isoformat(),
            "job_title": previous_result.get("job_title"),
            "department": previous_result.get("department"),
            "responses": None
        }
    
    logger.info(f"{'='*60}")
    logger.info(f"{'[DRY RUN] ' if dry_run else ''}Submitting job ID: {job_id}")
    logger.info(f"{'='*60}")
    
    # Submit with pre-generated responses
    success = submit_informational_request(config, job_id, previous_result["responses"], dry_run=dry_run)
    
    result = {
        "job_id": job_id,
        "timestamp": datetime.now().isoformat(),
        "job_title": previous_result.get("job_title"),
        "department": previous_result.get("department"),
        "responses": previous_result.get("responses")
    }
    
    if success:
        result["status"] = "success"
        result["reason"] = "Successfully submitted informational request"
    else:
        result["status"] = "submission_error"
        result["reason"] = "Failed to submit informational request (check API/auth)"
    
    # Save updated result (skip in dry run mode to avoid marking jobs as processed)
    if not dry_run:
        save_job_result(job_id, result)
    else:
        logger.info(f"[DRY RUN] Skipping save to file - no state changes made")
    
    return result


def get_all_jobs_with_responses() -> List[str]:
    """Scan per-job folder and return list of job IDs that have responses."""
    per_job_dir = get_per_job_dir()
    
    if not per_job_dir.exists():
        return []
    
    job_ids_with_responses = []
    
    for job_file in per_job_dir.glob("*.json"):
        try:
            with open(job_file, 'r') as f:
                result = json.load(f)
            
            if result.get("responses"):
                job_ids_with_responses.append(result.get("job_id", job_file.stem))
        except Exception as e:
            logger.warning(f"Failed to read {job_file}: {e}")
    
    return job_ids_with_responses


def main():
    """Main entry point for the submission pipeline."""
    parser = argparse.ArgumentParser(
        description='Submit informational requests using pre-generated responses'
    )
    parser.add_argument(
        'job_ids',
        nargs='*',
        help='Job IDs to submit (must have responses already generated)'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Force resubmit already submitted jobs'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be submitted without actually sending requests'
    )
    
    args = parser.parse_args()
    
    # Load configuration
    config = load_config()
    
    # Load previously processed jobs
    request_informational_data = load_request_informational_jobs()
    already_submitted = set(request_informational_data.get("processed", {}).keys())
    
    # Determine which jobs to process
    if args.job_ids:
        # User specified job IDs
        job_ids = args.job_ids
        logger.info(f"User specified {len(job_ids)} job(s): {', '.join(job_ids)}")
    else:
        # Auto-discover all jobs with responses
        job_ids = get_all_jobs_with_responses()
        logger.info(f"Found {len(job_ids)} job(s) with responses in per-job folder")
    
    if not job_ids:
        logger.info("No jobs to submit. Run request_informational_filler.py first to generate responses.")
        sys.exit(0)
    
    # Determine which jobs to skip
    jobs_to_skip = set()
    
    if not args.force:
        for job_id in job_ids:
            if job_id in already_submitted:
                jobs_to_skip.add(job_id)
                logger.debug(f"Job {job_id} already submitted - will skip")
    
    if jobs_to_skip:
        logger.info(f"Skipping {len(jobs_to_skip)} already submitted job(s): {', '.join(jobs_to_skip)}")
    
    jobs_to_process = [job_id for job_id in job_ids if job_id not in jobs_to_skip]
    
    if not jobs_to_process:
        logger.info("All jobs have already been submitted. Use --force to resubmit.")
        sys.exit(0)
    
    if args.dry_run:
        logger.info(f"[DRY RUN MODE] Would submit {len(jobs_to_process)} job(s): {', '.join(jobs_to_process)}")
    else:
        logger.info(f"Submitting {len(jobs_to_process)} job(s): {', '.join(jobs_to_process)}")
    
    # Process all jobs in parallel with rate limiting
    async def process_all_jobs():
        # Semaphore to limit concurrent requests (avoid throttling)
        sem = asyncio.Semaphore(10)
        
        async def worker(job_id: str):
            async with sem:
                try:
                    return await process_job_submission(job_id, config, dry_run=args.dry_run)
                except Exception as e:
                    logger.exception(f"Unexpected exception for job {job_id}: {e}")
                    return e
        
        # return_exceptions=True ensures one job's failure doesn't stop others
        return await asyncio.gather(*(worker(job_id) for job_id in jobs_to_process), return_exceptions=True)
    
    # Run async processing
    results = asyncio.run(process_all_jobs())
    
    # Handle unexpected exceptions
    for idx, result in enumerate(results):
        if isinstance(result, Exception):
            job_id = jobs_to_process[idx] if idx < len(jobs_to_process) else f"unknown_{idx}"
            logger.error(f"Unexpected exception for job {job_id}: {result}")
            if not args.dry_run:
                # Save the exception result to per-job file
                error_result = {
                    "job_id": job_id,
                    "status": "submission_error",
                    "reason": f"Unexpected exception: {str(result)}",
                    "timestamp": datetime.now().isoformat(),
                    "job_title": None,
                    "department": None,
                    "responses": None
                }
                save_job_result(job_id, error_result)
    
    # Re-accumulate all results from per-job files to create final consolidated file
    # Skip this in dry run mode since we didn't modify any files
    if not args.dry_run:
        logger.info("\nAccumulating all job results to create final consolidated file...")
        request_informational_data = accumulate_all_job_results()
        save_request_informational_jobs(request_informational_data)
    else:
        logger.info("\n[DRY RUN] Skipping file updates - no state changes made")
        # Load existing data just for statistics display
        request_informational_data = load_request_informational_jobs()
    
    # Count results (filter out exceptions first)
    valid_results = [r for r in results if not isinstance(r, Exception)]
    successful = sum(1 for r in valid_results if r.get("status") == "success")
    failed = len(valid_results) - successful
    exception_count = len(results) - len(valid_results)
    
    # Print summary
    logger.info(f"{'='*60}")
    logger.info(f"{'[DRY RUN] ' if args.dry_run else ''}SUMMARY")
    logger.info(f"{'='*60}")
    if args.dry_run:
        logger.info("[DRY RUN MODE] No requests were actually sent, no files were modified")
    logger.info(f"Total jobs in this run: {len(results)}")
    logger.info(f"✓ Successfully submitted: {successful}")
    logger.info(f"✗ Failed: {failed}")
    if exception_count > 0:
        logger.info(f"⚠️  Unexpected exceptions: {exception_count}")
    
    if successful > 0:
        success_ids = [r["job_id"] for r in valid_results if r.get("status") == "success"]
        logger.info(f"\nSuccessful jobs: {', '.join(success_ids)}")
    
    if failed > 0:
        failed_ids = [r["job_id"] for r in valid_results if r.get("status") != "success"]
        logger.info(f"\nFailed jobs: {', '.join(failed_ids)}")
        for result in valid_results:
            if result.get("status") != "success":
                logger.info(f"  - {result['job_id']}: {result['reason']}")
    
    logger.info(f"\n📊 Overall statistics:")
    logger.info(f"   Total submitted (all time): {request_informational_data['total_processed']}")
    logger.info(f"   Total not submitted (all time): {request_informational_data['total_not_processed']}")
    
    # Exit with appropriate code
    sys.exit(0 if (failed == 0 and exception_count == 0) else 1)


if __name__ == '__main__':
    main()
