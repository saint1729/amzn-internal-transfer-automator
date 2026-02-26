#!/usr/bin/env python
"""request_informational_filler.py

Generates LLM responses for internal transfer informational requests.

This script:
1. Auto-discovers unprocessed jobs from per_job/ folder (job_matcher YES-decision output)
   OR reads job IDs from command-line arguments or a JSON file
2. Skips jobs that already have responses generated (unless --force is used)
3. Fetches job details for jobs without responses
4. Uses Google ADK agent to generate 3 responses:
   - Why interested in the role (~240 words)
   - Relevant skills and experience (~240 words)
   - Forte context summary (130-150 words)
5. Saves each job result immediately to per_job_request_informational/{job_id}.json
6. Runs all jobs in parallel for efficiency

Note: This script ONLY generates responses. It does NOT submit them.
Use internal_transfer_request_informational_automator_pipeline.py to submit.

Per-job file structure: output/per_job_request_informational/{job_id}.json
{
  "job_id": "3185242",
  "status": "inference_complete|inference_error",
  "reason": "...",
  "timestamp": "...",
  "job_title": {...},
  "department": {...},
  "responses": {...} or null
}

Usage:
    # Auto-discover and process all unprocessed jobs from per-job folder (default)
    python request_informational_filler.py
    
    # Generate for specific job IDs
    python request_informational_filler.py 3185242 3185630
    
    # Generate responses for jobs in file
    python request_informational_filler.py --file output/sent_emails_state.json
    
    # Force regenerate (ignores existing responses)
    python request_informational_filler.py --force
"""
import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from get_job_details import get_job_details

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ADK Configuration
APP_NAME = "internal-transfer-automator"
USER_ID = "saint1729"

# Constants
FORTE_DETAILS = {
    "cycleYear": 2025,
    "employeePortion": {
        "bestContribute": "At my best, I leverage my ability to think big and simplify intricate problems to design robust, scalable ML data pipelines. My high standards drive me to build efficient workflows that improve data preparation, tokenizer training and annotation processes. I dive deep into challenges, ensuring that my solutions optimize performance, enhance automation, and empower teams to work smarter.",
        "growthIdeas": "To strengthen Ownership, I want to proactively identify gaps in ML workflows, take end-to-end responsibility for improvements, and drive long-term impact beyond my immediate projects. To enhance Learn and Be Curious, I want to deepen my expertise in model distillation, explore emerging ML trends, and seek cross-team collaborations to expand my knowledge and innovate effectively.",
        "growthLeadershipPrinciples": [
            "OWNERSHIP",
            "LEARN_AND_BE_CURIOUS"
        ],
        "leadershipPrinciples": [
            "INVENT_AND_SIMPLIFY",
            "INSIST_ON_THE_HIGHEST_STANDARDS",
            "THINK_BIG"
        ],
        "mostExcited": "I am most excited about driving innovation in ML data processing, tokenization, and model distillation. I thrive on simplifying complex workflows, creating scalable solutions, and pushing the boundaries of efficiency. The challenge of building high-impact systems that enhance model performance and streamline data annotation keeps me motivated to invent, iterate, and deliver lasting improvements."
    },
    "lpSummary": "SOLID_STRENGTH",
    "managerPortion": {
        "growthIdeas": "1. I would like Sai to be able to deliver their work earning trust from stakeholders. Sai can keep their stakeholders in loop around status, blockers and proposing solutions with tradeoffs to unblock themselves. This would demonstrate Sai's ability to deliver in ambiguous situations.\n2. Sai demonstrates strong technical acumen. I encourage Sai to step up as a leader, driving team development, upholding the highest engineering standards, and championing process improvements to raise the bar.",
        "growthLeadershipPrinciples": [
            "ARE_RIGHT_A_LOT",
            "THINK_BIG",
            "HIRE_AND_DEVELOP_THE_BEST"
        ],
        "leadershipPrinciples": [
            "INVENT_AND_SIMPLIFY",
            "DIVE_DEEP",
            "LEARN_AND_BE_CURIOUS"
        ],
        "superPowers": "Sai has showcased delivering high quality code and uses the opportunity to improve the existing code base as well as propose right architecture patterns during their projects. An example is establishing the API naming convention to use for DAWM. Similarly, Sai identified and redesigned the DAWM job tracker Dynamodb table enabling maintainability and reduce errors during updates. "
    },
    "sharedPerformanceRating": "MEETS_HIGH_BAR"
}

RECENT_WORK_SUMMARY = """
Over the past two weeks, I've been focused on building automation tools for internal transfers and exploring agentic AI design patterns.

Key accomplishments:
1. Built an end-to-end automator for Amazon internal transfers that fetches job details, generates personalized responses using LLMs, and submits informational requests automatically. This tool saves several hours of overall manual work.

2. Implemented and benchmarked multiple agentic design patterns. Compared different prompting strategies and evaluated their effectiveness on reasoning tasks. Created a book notes website to share insights and best practices for agentic application development: https://saint1729.me/books/agentic-design-patterns/

3. Contributed to the lm-evaluation-harness by adding support for multi-turn conversations, enabling better evaluation of conversational AI systems.

I'm particularly excited about how AI agents can automate tedious workflows while maintaining quality through LLM-generated personalization. The internal transfer tool is a perfect example - instead of manually crafting responses for each job, the LLM generates thoughtful, contextually relevant answers based on candidate background information and the job requirements.
"""


def load_config() -> Dict[str, str]:
    """Load and validate required environment variables."""
    load_dotenv()
    
    required_vars = [
        'GOOGLE_API_KEY',
        'MODEL_STRONG',
        'CANDIDATE_SUMMARY'
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


def get_job_matcher_per_job_dir() -> Path:
    """Get the job_matcher per-job directory containing YES/NO/ERROR decision files."""
    results_folder = os.getenv("JOB_MATCH_RESULTS_FOLDER_NAME", "output")
    return Path(results_folder) / "per_job"


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


def read_job_ids_from_json(file_path: str) -> List[str]:
    """Read job IDs from JSON file with 'sent_jobs' array."""
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
            
        if "sent_jobs" in data:
            job_ids = [str(job_id).strip() for job_id in data["sent_jobs"]]
            logger.info(f"Loaded {len(job_ids)} job IDs from {file_path}")
            return job_ids
        else:
            logger.error(f"No 'sent_jobs' key found in {file_path}")
            return []
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {file_path}: {e}")
        return []
    except Exception as e:
        logger.error(f"Failed to read {file_path}: {e}")
        return []


def get_jobs_without_responses() -> List[str]:
    """Return YES-decision job IDs that have not been successfully inferred yet.

    Sources job IDs from the job_matcher per_job folder (output/per_job/) - only
    jobs with decision='YES' are considered. Cross-references with the filler's
    per_job_request_informational/ folder to exclude jobs already successfully processed.

    A job needs (re)processing if:
    - It has no per_job_request_informational file (brand new match from job_matcher)
    - It has a per_job_request_informational file but responses is null (inference_error retry)

    Returns:
        List of job IDs that need inference
    """
    matcher_per_job_dir = get_job_matcher_per_job_dir()
    filler_per_job_dir = get_per_job_dir()

    if not matcher_per_job_dir.exists():
        logger.warning(f"job_matcher per-job directory not found: {matcher_per_job_dir}")
        logger.warning("Run job_matcher.py first to generate job matches.")
        return []

    # Collect YES-decision job IDs from job_matcher output
    yes_job_ids = []
    for job_file in sorted(matcher_per_job_dir.glob("*.json")):
        try:
            with open(job_file, 'r') as f:
                data = json.load(f)
            if data.get("decision") == "YES":
                yes_job_ids.append(job_file.stem)
        except Exception as e:
            logger.warning(f"Failed to read {job_file}: {e}")

    if not yes_job_ids:
        logger.info("No YES-decision jobs found in job_matcher output.")
        return []

    logger.info(f"Found {len(yes_job_ids)} YES-decision job(s) from job_matcher.")

    # Filter out jobs already successfully processed
    unprocessed = []
    for job_id in yes_job_ids:
        filler_file = filler_per_job_dir / f"{job_id}.json"
        if not filler_file.exists():
            # Brand new job - never processed by filler
            unprocessed.append(job_id)
            logger.debug(f"Job {job_id}: brand new (no filler file) - will process")
        else:
            try:
                with open(filler_file, 'r') as f:
                    data = json.load(f)
                if not data.get("responses"):
                    unprocessed.append(job_id)
                    logger.debug(f"Job {job_id}: retry (status: {data.get('status', 'unknown')}) - will process")
            except Exception as e:
                logger.warning(f"Failed to read filler file for {job_id}: {e}")

    return unprocessed


def setup_agent(api_key: str, model_name: str) -> Tuple[LlmAgent, Runner, InMemorySessionService]:
    """Configure and return Google ADK LlmAgent with runner."""
    # Set API key as environment variable (ADK reads from environment)
    os.environ['GOOGLE_API_KEY'] = api_key
    
    # Create session service
    session_service = InMemorySessionService()
    
    # Create Google ADK LlmAgent
    agent = LlmAgent(
        name="career_advisor_agent",
        model=model_name,
        instruction=(
            "You are an expert career advisor helping candidates write compelling, "
            "authentic responses for internal job transfer requests at Amazon. "
            "Your responses should be professional, specific, and tailored to the job. "
            "Target approximately 140 words per response unless explicitly stated otherwise. "
            "Use concrete examples and avoid generic statements. "
            "Match the tone to Amazon's leadership principles and culture."
        )
    )
    
    # Create runner
    runner = Runner(
        app_name=APP_NAME,
        agent=agent,
        session_service=session_service
    )
    
    logger.info(f"Initialized Google ADK LlmAgent with model: {model_name}")
    return agent, runner, session_service


async def run_agent_async(
    runner: Runner,
    session_service: InMemorySessionService,
    prompt: str,
    session_id: str
) -> str:
    """Run agent asynchronously and return the generated text."""
    # Ensure session exists
    try:
        await session_service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=session_id
        )
    except Exception:
        pass  # Session might already exist
    
    # Create content from prompt
    new_message = types.Content(
        role="user",
        parts=[types.Part(text=prompt)]
    )
    
    # Run agent and collect response
    last_text = ""
    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=session_id,
        new_message=new_message
    ):
        content = getattr(event, "content", None)
        parts = getattr(content, "parts", None) if content else None
        if parts:
            chunk = "".join([(getattr(p, "text", "") or "") for p in parts]).strip()
            if chunk:
                last_text = chunk
    
    return last_text


async def generate_interest_reason(
    runner: Runner,
    session_service: InMemorySessionService,
    job_details: Dict,
    candidate_summary: str,
    session_id: str
) -> str:
    """Generate response for 'Why are you interested in this role?'
    
    Target: ~240 words
    """
    job_title = job_details.get('job', {}).get('role', {}).get('title', 'SDE - II')
    job_desc = job_details.get('job', {}).get('descriptionInternal', '')
    department = job_details.get('job', {}).get('department', {}).get('name', 'Amazon')
    
    prompt = f"""Write a compelling response to "Why are you interested in this role?"

Job Title: {job_title}
Department: {department}
Job Description: {job_desc}

Candidate Background:
{candidate_summary}

Write a personalized, enthusiastic response in 1 paragraph (~240 words) explaining why the candidate is interested in this specific role. Focus on:
1. Alignment between the role and the candidate's interests/goals
2. Specific aspects of the job description that resonate
3. How this role fits into the candidate's career trajectory

USE FIRST PERSON AND BE AUTHENTIC, SPECIFIC, FORMAL, AND ENTHUSIASTIC. Avoid generic statements. Reference specific details from the job description. Make sure to use keyboard/ASCII characters ONLY.`

Response:"""
    
    try:
        result = await run_agent_async(runner, session_service, prompt, session_id)
        logger.info(f"Generated interest reason ({len(result.split())} words)")
        return result
    except Exception as e:
        logger.error(f"Failed to generate interest reason: {e}")
        raise


async def generate_qualifications(
    runner: Runner,
    session_service: InMemorySessionService,
    job_details: Dict,
    candidate_summary: str,
    forte_details: Dict,
    session_id: str
) -> str:
    """Generate response for 'What are your relevant skills and experience?'
    
    Target: ~240 words
    """
    job_title = job_details.get('job', {}).get('role', {}).get('title', 'SDE - II')
    qualifications = job_details.get('job', {}).get('jobQualifications', [])
    
    # Extract qualifications by type (REQUIRED vs PREFERRED)
    basic_quals_data = [q.get('qualificationData', '') for q in qualifications if q.get('qualificationType') == 'REQUIRED']
    preferred_quals_data = [q.get('qualificationData', '') for q in qualifications if q.get('qualificationType') == 'PREFERRED']
    
    basic_quals_str = '\n'.join(basic_quals_data) if basic_quals_data else 'Not specified'
    preferred_quals_str = '\n'.join(preferred_quals_data) if preferred_quals_data else 'Not specified'
    
    prompt = f"""Write a compelling response to "What are your relevant skills and experience?"

Job Title: {job_title}

Basic Qualifications:
{basic_quals_str}

Preferred Qualifications:
{preferred_quals_str}

Candidate Background:
{candidate_summary}

Candidate's Manager Assessment (from Forte):
{forte_details.get('managerPortion', {}).get('superPowers', '')}

Leadership Strengths from Forte by manager:
{', '.join(forte_details.get('managerPortion', {}).get('leadershipPrinciples', []))}

Write a confident, specific response in 1 paragraph (~240 words) highlighting the candidate's relevant skills and experience. Focus on:
1. How their background matches the required qualifications
2. Specific technical skills and accomplishments
3. Unique strengths that set them apart

USE FIRST PERSON AND BE AUTHENTIC, SPECIFIC, FORMAL, AND ENTHUSIASTIC. Be specific with examples. Match the tone to the seniority level of the role. Avoid generic claims without evidence. Make sure to use keyboard/ASCII characters ONLY.

Response:"""
    
    try:
        result = await run_agent_async(runner, session_service, prompt, session_id)
        logger.info(f"Generated qualifications ({len(result.split())} words)")
        return result
    except Exception as e:
        logger.error(f"Failed to generate qualifications: {e}")
        raise


async def generate_forte_context(
    runner: Runner,
    session_service: InMemorySessionService,
    job_details: Dict,
    candidate_summary: str,
    forte_details: Dict,
    recent_work: str,
    session_id: str
) -> str:
    """Generate Forte context summary.
    
    Target: ~140 words
    """
    job_title = job_details.get('job', {}).get('role', {}).get('title', 'SDE - II')
    
    employee_portion = forte_details.get('employeePortion', {})
    best_contribute = employee_portion.get('bestContribute', '')
    most_excited = employee_portion.get('mostExcited', '')
    leadership_principles = employee_portion.get('leadershipPrinciples', [])
    lp_summary = forte_details.get('lpSummary', '')

    manager_portion = forte_details.get('managerPortion', {})
    manager_superpowers = manager_portion.get('superPowers', '')
    manager_leadership_principles = manager_portion.get('leadershipPrinciples', [])
    
    prompt = f"""Write a concise summary of the candidate's Forte (performance review) and recent work for sharing with a hiring manager.

Job Title: {job_title}

Candidate's Self-Assessment (from Forte):
How I best contribute: {best_contribute}

What I'm most excited about: {most_excited}

Leadership principles I am strong in: {', '.join(leadership_principles)}

Manager's view of candidate's superpowers: {manager_superpowers}
Leadership Strengths from Forte by manager: {', '.join(manager_leadership_principles)}

Leadership Principles Summary by manager: {lp_summary}

Recent Work (past 2 weeks):
{recent_work}

Performance Rating: {forte_details.get('sharedPerformanceRating', 'MEETS_HIGH_BAR')}

Write the Forte context summary in 1 paragraph. You MUST write strictly under 145 words. Do NOT exceed 144 words under any circumstances. Cover:
1. Key strengths and superpowers
2. Performance and impact
3. Leadership principles demonstrated
4. Recent accomplishments that show momentum

Be factual and professional. This will be shared directly with the hiring manager.

IMPORTANT: USE FIRST PERSON AND BE AUTHENTIC, SPECIFIC, FORMAL, AND ENTHUSIASTIC. Write ONLY the summary paragraph itself. Do NOT include any meta-text like "Here is a summary" or "Summary:" - just write the actual content that will be shared with the hiring manager. Make sure to use keyboard/ASCII characters ONLY.

Context:"""
    
    try:
        result = await run_agent_async(runner, session_service, prompt, session_id)
        
        # Clean up any meta-text prefix (e.g., "Here is a summary...", "Summary:", etc.)
        result = result.strip()
        meta_prefixes = [
            "Here is a summary of the candidate's Forte context for the hiring manager:",
            "Here is a summary of the candidate's Forte and recent work:",
            "Here is a summary:",
            "Summary:",
            "Context:"
        ]
        for prefix in meta_prefixes:
            if result.startswith(prefix):
                result = result[len(prefix):].strip()
                break
        
        original_word_count = len(result.split())
        logger.info(f"Generated Forte context ({original_word_count} words)")
        return result
    except Exception as e:
        logger.error(f"Failed to generate Forte context: {e}")
        raise


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


async def process_job_inference(
    job_id: str,
    runner: Runner,
    session_service: InMemorySessionService,
    config: Dict,
    force: bool = False
) -> Dict:
    """Generate LLM responses for a single job.
    
    Args:
        job_id: Job ID to process
        runner: Google ADK runner
        session_service: Session service for agent
        config: Configuration dict
        force: If True, regenerate responses even if they exist
    
    Returns:
        Dict with keys: job_id, status, reason, timestamp, job_title, department, responses
    """
    result = {
        "job_id": job_id,
        "timestamp": datetime.now().isoformat(),
        "status": "inference_error",
        "reason": "",
        "job_title": None,
        "department": None,
        "responses": None
    }
    
    logger.info(f"{'='*60}")
    logger.info(f"Processing inference for job ID: {job_id}")
    logger.info(f"{'='*60}")
    
    # Check if we already have responses
    previous_result = load_previous_job_result(job_id)
    has_previous_responses = previous_result and previous_result.get("responses")
    
    if has_previous_responses and not force:
        logger.info(f"✓ Found existing responses - skipping inference")
        return previous_result
    elif has_previous_responses and force:
        logger.info(f"⚠️  Found existing responses but --force flag set - regenerating...")
    
    # Use job_id as session_id for uniqueness
    session_id = f"job_{job_id}"
    
    # Step 1: Fetch job details
    logger.info("Step 1: Fetching job details...")
    job_details = get_job_details(job_id)
    
    if not job_details:
        logger.error(f"✗ Failed to fetch job details for {job_id}")
        result["reason"] = "Failed to fetch job details"
        save_job_result(job_id, result)
        return result
    
    job_title = job_details.get('job', {}).get('role', {})
    department = job_details.get('job', {}).get('department', {})
    result["job_title"] = str(job_title)
    result["department"] = str(department)
    logger.info(f"✓ Retrieved: {job_title} - {department}")
    
    # Step 2: Generate responses using Google ADK agent
    logger.info("\nStep 2: Generating responses with Google ADK agent...")
    
    try:
        logger.info("  Generating: Why are you interested in this role?")
        interest_reason = await generate_interest_reason(
            runner,
            session_service,
            job_details,
            config['CANDIDATE_SUMMARY'],
            f"{session_id}_interest"
        )
        
        logger.info("  Generating: What are your relevant skills and experience?")
        qualifications = await generate_qualifications(
            runner,
            session_service,
            job_details,
            config['CANDIDATE_SUMMARY'],
            FORTE_DETAILS,
            f"{session_id}_qualifications"
        )
        
        logger.info("  Generating: Forte context summary")
        forte_context = await generate_forte_context(
            runner,
            session_service,
            job_details,
            config['CANDIDATE_SUMMARY'],
            FORTE_DETAILS,
            RECENT_WORK_SUMMARY,
            f"{session_id}_forte"
        )
        
        logger.info("✓ All responses generated successfully")
        
        responses = {
            'interest_reason': interest_reason,
            'qualifications': qualifications,
            'forte_context': forte_context
        }
        
        # Clean responses (remove meta-text)
        responses = clean_responses(responses)
        result["responses"] = responses
        
        # Log generated responses for review
        logger.info("\n--- Generated Responses ---")
        logger.info(f"\n1. Why interested ({len(interest_reason.split())} words):")
        logger.info(interest_reason)
        logger.info(f"\n2. Qualifications ({len(qualifications.split())} words):")
        logger.info(qualifications)
        logger.info(f"\n3. Forte Context ({len(forte_context.split())} words):")
        logger.info(forte_context)
        logger.info("--- End Responses ---\n")
        
        result["status"] = "inference_complete"
        result["reason"] = "Successfully generated all responses"
        
    except Exception as e:
        logger.error(f"✗ Failed to generate responses: {e}")
        result["reason"] = f"Failed to generate responses: {str(e)}"
        result["status"] = "inference_error"
    
    # Save result immediately after processing
    save_job_result(job_id, result)
    
    return result


def main():
    """Main entry point for the inference pipeline."""
    parser = argparse.ArgumentParser(
        description='Generate LLM responses for informational requests (inference only)'
    )
    parser.add_argument(
        'job_ids',
        nargs='*',
        help='Job IDs to process (e.g., 3185242 3185246). If omitted, auto-discovers unprocessed jobs from per-job folder.'
    )
    parser.add_argument(
        '--file',
        type=str,
        help='JSON file containing job IDs (with "sent_jobs" array)'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Force regenerate responses even if they already exist'
    )
    
    args = parser.parse_args()
    
    # Collect job IDs: explicit args/file take priority, otherwise auto-discover
    job_ids = []
    
    if args.job_ids:
        job_ids.extend(args.job_ids)
    
    if args.file:
        job_ids_from_file = read_job_ids_from_json(args.file)
        if job_ids_from_file:
            job_ids.extend(job_ids_from_file)
        else:
            logger.error(f"Failed to read job IDs from JSON file: {args.file}")
            sys.exit(1)
    
    if not job_ids:
        # Auto-discover: read YES-decision jobs from job_matcher, skip already successful ones
        matcher_dir = get_job_matcher_per_job_dir()
        logger.info(f"No job IDs specified - scanning {matcher_dir} for unprocessed YES-decision jobs...")
        job_ids = get_jobs_without_responses()
        if not job_ids:
            logger.info("No unprocessed jobs found. All YES-decision jobs already have responses.")
            sys.exit(0)
        logger.info(f"Auto-discovered {len(job_ids)} unprocessed job(s): {', '.join(job_ids)}")
    else:
        logger.info(f"Found {len(job_ids)} job(s) to process: {', '.join(job_ids)}")
        # Filter out non-YES jobs even when explicitly provided
        matcher_per_job_dir = get_job_matcher_per_job_dir()
        non_yes = []
        yes_only = []
        for job_id in job_ids:
            matcher_file = matcher_per_job_dir / f"{job_id}.json"
            if matcher_file.exists():
                try:
                    with open(matcher_file) as f:
                        decision = json.load(f).get("decision")
                    if decision != "YES":
                        non_yes.append((job_id, decision))
                        continue
                except Exception:
                    pass  # Can't read file - allow through, will fail at job details step
            yes_only.append(job_id)
        if non_yes:
            logger.warning(
                f"Skipping {len(non_yes)} non-YES job(s): "
                + ", ".join(f"{jid}({dec})" for jid, dec in non_yes)
            )
        job_ids = yes_only

    # Determine which jobs to skip (already have responses) - only applies when explicitly provided
    jobs_to_skip = set()
    
    if not args.force:
        for job_id in job_ids:
            prev_result = load_previous_job_result(job_id)
            if prev_result and prev_result.get("responses"):
                jobs_to_skip.add(job_id)
                logger.debug(f"Job {job_id} already has responses - will skip")
    
    if jobs_to_skip:
        logger.info(f"Skipping {len(jobs_to_skip)} job(s) with existing responses: {', '.join(jobs_to_skip)}")
    
    jobs_to_process = [job_id for job_id in job_ids if job_id not in jobs_to_skip]
    
    if not jobs_to_process:
        logger.info("All jobs already have responses. Use --force to regenerate.")
        sys.exit(0)
    
    logger.info(f"Generating responses for {len(jobs_to_process)} job(s): {', '.join(jobs_to_process)}")
    
    # Load configuration
    config = load_config()
    
    # Setup Google ADK agent
    agent, runner, session_service = setup_agent(config['GOOGLE_API_KEY'], config['MODEL_STRONG'])
    
    # Process all jobs in parallel with rate limiting
    async def process_all_jobs():
        # Semaphore to limit concurrent requests (avoid throttling)
        sem = asyncio.Semaphore(5) # Adjust concurrency level as needed
        
        async def worker(job_id: str):
            async with sem:
                try:
                    return await process_job_inference(job_id, runner, session_service, config, force=args.force)
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
            # Save the exception result to per-job file
            error_result = {
                "job_id": job_id,
                "status": "inference_error",
                "reason": f"Unexpected exception: {str(result)}",
                "timestamp": datetime.now().isoformat(),
                "job_title": None,
                "department": None,
                "responses": None
            }
            save_job_result(job_id, error_result)
    
    # Count results (filter out exceptions first)
    valid_results = [r for r in results if not isinstance(r, Exception)]
    successful = sum(1 for r in valid_results if r.get("status") == "inference_complete")
    failed = len(valid_results) - successful
    exception_count = len(results) - len(valid_results)
    
    # Print summary
    logger.info(f"{'='*60}")
    logger.info("SUMMARY")
    logger.info(f"{'='*60}")
    logger.info(f"Total jobs in this run: {len(results)}")
    logger.info(f"✓ Inference completed: {successful}")
    logger.info(f"✗ Failed: {failed}")
    if exception_count > 0:
        logger.info(f"⚠️  Unexpected exceptions: {exception_count}")
    
    if successful > 0:
        success_ids = [r["job_id"] for r in valid_results if r.get("status") == "inference_complete"]
        logger.info(f"\nSuccessful jobs: {', '.join(success_ids)}")
    
    if failed > 0:
        failed_ids = [r["job_id"] for r in valid_results if r.get("status") != "inference_complete"]
        logger.info(f"\nFailed jobs: {', '.join(failed_ids)}")
        for result in valid_results:
            if result.get("status") != "inference_complete":
                logger.info(f"  - {result['job_id']}: {result['reason']}")
    
    logger.info(f"\n📁 Results saved to: {get_per_job_dir()}")
    logger.info("Next step: Use internal_transfer_request_informational_automator_pipeline.py to submit")
    
    # Exit with appropriate code
    sys.exit(0 if (failed == 0 and exception_count == 0) else 1)


if __name__ == '__main__':
    main()
