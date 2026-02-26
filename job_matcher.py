import argparse
import asyncio
from datetime import datetime, timezone
import html
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from dotenv import load_dotenv

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from get_jobs import get_jobs

from get_employee_details import get_employee_hierarchy  # For enriching job data with employee hierarchy


load_dotenv()


MODEL_FAST = os.getenv("MODEL_FAST")
MODEL_STRONG = os.getenv("MODEL_STRONG")

APP_NAME = "job-matcher"
USER_ID = os.getenv("USER_ID", "default_user")

# ---------------- Pricing + mode ----------------
# Execution mode in THIS script remains real-time (standard).
# This flag controls COST CALCULATION only.
# Values: "standard" | "batch"
PRICING_MODE = os.getenv("PRICING_MODE", "standard").strip().lower()

# Prompt length threshold for Gemini 2.5 Pro tiered pricing
PRO_TIER_THRESHOLD_TOKENS = 200_000

# USD per 1M tokens (text) from pricing docs you pasted.
# Batch is ~50% of standard cost (Gemini Batch API discount). :contentReference[oaicite:3]{index=3}
PRICING_USD_PER_1M = {
    "gemini-2.5-pro": {
        "standard": {
            "le_200k": {"input": 1.25, "output": 10.00},
            "gt_200k": {"input": 2.50, "output": 15.00},
        },
        "batch": {
            "le_200k": {"input": 0.625, "output": 5.00},
            "gt_200k": {"input": 1.25, "output": 7.50},
        },
    },
    "gemini-2.5-flash": {
        "standard": {
            "flat": {"input": 0.30, "output": 2.50},
        },
        "batch": {
            "flat": {"input": 0.15, "output": 1.25},
        },
    },
}

RESULTS_DIR = os.getenv("JOB_MATCH_RESULTS_FOLDER_NAME", "results").strip()
FINAL_RESULTS_PATH = os.path.join(RESULTS_DIR, "job_match_results.json")
PER_JOB_DIR = os.path.join(RESULTS_DIR, "per_job")


def ensure_results_dirs() -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    os.makedirs(PER_JOB_DIR, exist_ok=True)


def atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)  # atomic on POSIX


def load_json_file(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        logging.warning("Could not read %s; ignoring.", path)
        return None


def load_all_per_job_results() -> Dict[str, Dict[str, Any]]:
    """
    Reads RESULTS_DIR/per_job/*.json and returns {job_id: job_result_dict}.
    Ignores unreadable files.
    """
    by_id: Dict[str, Dict[str, Any]] = {}
    if not os.path.isdir(PER_JOB_DIR):
        return by_id

    for name in os.listdir(PER_JOB_DIR):
        if not name.endswith(".json"):
            continue
        path = os.path.join(PER_JOB_DIR, name)
        d = load_json_file(path)
        if not d:
            continue
        jid = str(d.get("job_id") or "").strip()
        if jid:
            by_id[jid] = d
    return by_id


def extract_results_list(existing: Dict[str, Any]) -> list[Dict[str, Any]]:
    out: list[Dict[str, Any]] = []
    for section in ("yes_ranked", "no_audit"):
        out.extend(existing.get(section, []) or [])
    return out


def aggregate_from_disk(
    pricing_mode: str,
    pricing_usd_per_1m: Dict[str, Any],
    pro_prompt_tier_threshold_tokens: int,
) -> Dict[str, Any]:
    """
    Builds an aggregated results dict from:
      1) FINAL_RESULTS_PATH (if present)
      2) PER_JOB_DIR/*.json (treated as source of truth / most recent)
    Writes the aggregated dict back to FINAL_RESULTS_PATH atomically.
    """
    ensure_results_dirs()

    existing_final = load_json_file(FINAL_RESULTS_PATH) or {}
    existing_items = extract_results_list(existing_final)

    per_job_by_id = load_all_per_job_results()

    # Merge: final file first, then per-job overrides (newer/better)
    by_id: Dict[str, Dict[str, Any]] = {}
    for r in existing_items:
        jid = str(r.get("job_id") or "").strip()
        if jid:
            by_id[jid] = r

    for jid, r in per_job_by_id.items():
        by_id[jid] = r

    merged_all = list(by_id.values())

    def conf_rank(c: str) -> int:
        return {"HIGH": 2, "MEDIUM": 1, "LOW": 0}.get(c or "", 0)

    yes_ranked = [r for r in merged_all if r.get("decision") == "YES"]
    no_audit = [r for r in merged_all if r.get("decision") != "YES"]
    yes_ranked.sort(key=lambda r: (r.get("score") or 0, conf_rank(r.get("confidence"))), reverse=True)

    total_cost_known = 0.0
    unknown_cost_count = 0
    token_summary_by_model: Dict[str, Dict[str, int]] = {}
    cost_usd_by_model_known: Dict[str, float] = {}

    for r in merged_all:
        c = r.get("cost_usd")
        if c is None:
            unknown_cost_count += 1
        else:
            total_cost_known += float(c)
        
        ubm = r.get("usage_by_model")
        if isinstance(ubm, dict):
            for m, t in ubm.items():
                token_summary_by_model.setdefault(m, {"prompt_tokens": 0, "candidate_tokens": 0, "total_tokens": 0})
                token_summary_by_model[m]["prompt_tokens"] += int(t.get("prompt_tokens") or 0)
                token_summary_by_model[m]["candidate_tokens"] += int(t.get("candidate_tokens") or 0)
                token_summary_by_model[m]["total_tokens"] += int(t.get("total_tokens") or 0)

        cbm = r.get("cost_by_model")
        if isinstance(cbm, dict):
            for m, c in cbm.items():
                cost_usd_by_model_known[m] = cost_usd_by_model_known.get(m, 0.0) + float(c or 0.0)

    aggregated = {
        "yes_ranked": yes_ranked,
        "no_audit": no_audit,
        "counts": {"total": len(merged_all), "yes": len(yes_ranked), "no": len(no_audit)},
        "cost_summary": {
            "pricing_mode": pricing_mode,
            "total_cost_usd_known": total_cost_known,
            "token_summary_by_model": token_summary_by_model,
            "cost_usd_by_model_known": cost_usd_by_model_known,
            "jobs_with_unknown_cost": unknown_cost_count,
            "pricing_usd_per_1m": pricing_usd_per_1m,
            "pro_prompt_tier_threshold_tokens": pro_prompt_tier_threshold_tokens,
        },
    }

    atomic_write_json(FINAL_RESULTS_PATH, aggregated)
    return aggregated


def processed_job_ids_from_aggregate(agg: Dict[str, Any]) -> set[str]:
    s: set[str] = set()
    for r in extract_results_list(agg):
        if r.get("decision") == "ERROR":
            continue  # allow retry
        jid = str(r.get("job_id") or "").strip()
        if jid:
            s.add(jid)
    return s


def per_job_path(job_id: str) -> str:
    return os.path.join(PER_JOB_DIR, f"{job_id}.json")


def persist_per_job_result(result: Dict[str, Any]) -> None:
    jid = str(result.get("job_id") or "").strip()
    if not jid:
        return
    timestamped = {"timestamp": datetime.now(timezone.utc).isoformat(), **result}
    atomic_write_json(per_job_path(jid), timestamped)


def persist_per_job_error(job: Dict[str, Any], stage: str, err: Exception) -> None:
    jid = str(job.get("job_id") or "").strip()
    if not jid:
        return

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "job_id": jid,
        "title": job.get("title"),
        "decision": "ERROR",
        "confidence": None,
        "score": None,
        "error": {
            "stage": stage,
            "type": type(err).__name__,
            "message": str(err),
            "repr": repr(err),
        },
    }
    atomic_write_json(per_job_path(jid), payload)


def pricing_key_for_pro(prompt_tokens: int) -> str:
    return "le_200k" if prompt_tokens <= PRO_TIER_THRESHOLD_TOKENS else "gt_200k"


def cost_usd(model: str, usage: Optional[Dict[str, int]], pricing_mode: str) -> Optional[float]:
    """
    Computes USD cost using usage token counts.
    usage expects: {"prompt_tokens": int, "candidate_tokens": int}
    pricing_mode: "standard" or "batch" (cost estimation only; batch execution not implemented here).
    """
    if not usage:
        return None

    prompt_tokens = usage.get("prompt_tokens")
    candidate_tokens = usage.get("candidate_tokens")
    if prompt_tokens is None or candidate_tokens is None:
        return None

    model_prices = PRICING_USD_PER_1M.get(model)
    if not model_prices:
        return None

    mode_prices = model_prices.get(pricing_mode)
    if not mode_prices:
        return None

    if model == "gemini-2.5-pro":
        tier = pricing_key_for_pro(prompt_tokens)
        rates = mode_prices.get(tier)
        if not rates:
            return None
        inp_rate = rates["input"]
        out_rate = rates["output"]
    else:
        rates = mode_prices.get("flat")
        if not rates:
            return None
        inp_rate = rates["input"]
        out_rate = rates["output"]

    inp_cost = (prompt_tokens / 1_000_000) * inp_rate
    out_cost = (candidate_tokens / 1_000_000) * out_rate
    return inp_cost + out_cost


def cost_for_model(
    model: str,
    prompt_tokens: int,
    candidate_tokens: int,
    pricing_mode: str,
    pro_prompt_tier_threshold_tokens: int,
    pricing_usd_per_1m: Dict[str, Any],
) -> float:
    model_key = str(model)
    if model_key not in pricing_usd_per_1m:
        return 0.0

    rates = pricing_usd_per_1m[model_key][pricing_mode]

    # Pro has <=200k vs >200k tiers; your prompts are practically always <=200k.
    if model_key == "gemini-2.5-pro":
        tier = "le_200k"  # if you later want, decide tier by prompt length
        in_rate = float(rates[tier]["input"])
        out_rate = float(rates[tier]["output"])
    else:
        in_rate = float(rates["flat"]["input"])
        out_rate = float(rates["flat"]["output"])

    return (prompt_tokens / 1_000_000.0) * in_rate + (candidate_tokens / 1_000_000.0) * out_rate


def sum_usage(usage: Optional[Dict[str, int]]) -> Dict[str, int]:
    if not usage:
        return {"prompt_tokens": 0, "candidate_tokens": 0, "total_tokens": 0}
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("candidate_tokens") or 0)
    return {"prompt_tokens": pt, "candidate_tokens": ct, "total_tokens": pt + ct}


def sum_usage_by_step(usage_by_step: Dict[str, Optional[Dict[str, int]]]) -> Dict[str, int]:
    pt = 0
    ct = 0
    for u in (usage_by_step or {}).values():
        if not u:
            continue
        pt += int(u.get("prompt_tokens") or 0)
        ct += int(u.get("candidate_tokens") or 0)
    return {"prompt_tokens": pt, "candidate_tokens": ct, "total_tokens": pt + ct}


def usage_by_model_from_steps(usage_by_step: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}
    for _, u in (usage_by_step or {}).items():
        if not isinstance(u, dict):
            continue
        model = str(u.get("model") or "unknown")
        pt = int(u.get("prompt_tokens") or 0)
        ct = int(u.get("candidate_tokens") or 0)
        if model not in out:
            out[model] = {"prompt_tokens": 0, "candidate_tokens": 0, "total_tokens": 0}
        out[model]["prompt_tokens"] += pt
        out[model]["candidate_tokens"] += ct
        out[model]["total_tokens"] += pt + ct
    return out


def extract_usage_from_event(event: Any) -> Optional[Dict[str, int]]:
    """
    Best-effort extraction of token usage from ADK event objects.
    Field names vary by SDK version; try common shapes.
    Returns {"prompt_tokens": ..., "candidate_tokens": ...} if found.
    """
    resp = getattr(event, "response", None)

    for obj in [resp, event]:
        if not obj:
            continue

        usage = getattr(obj, "usage_metadata", None) or getattr(obj, "usageMetadata", None)
        if not usage:
            continue

        pt = getattr(usage, "prompt_token_count", None) or getattr(usage, "promptTokenCount", None)
        ct = getattr(usage, "candidates_token_count", None) or getattr(usage, "candidatesTokenCount", None)

        # Sometimes named differently
        if pt is None:
            pt = getattr(usage, "input_tokens", None) or getattr(usage, "inputTokenCount", None)
        if ct is None:
            ct = getattr(usage, "output_tokens", None) or getattr(usage, "outputTokenCount", None)

        if pt is not None and ct is not None:
            try:
                return {"prompt_tokens": int(pt), "candidate_tokens": int(ct)}
            except Exception:
                return None

    return None


def parse_json_loose(s: str) -> Dict[str, Any]:
    s = (s or "").strip()
    if not s:
        raise RuntimeError("Empty LLM output when JSON was expected.")

    # Strip ```json fences if present
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n", "", s)
        s = re.sub(r"\n```$", "", s)
        s = s.strip()

    # Best-effort: if there is extra text, slice to outermost JSON object
    if not s.startswith("{"):
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            s = s[start : end + 1]

    return json.loads(s)


def strip_html(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def sanitize_job(job: Dict[str, Any]) -> Dict[str, Any]:
    f = job.get("fields", {})
    out = {
        "job_id": (f.get("icimsJobId") or [""])[0],
        "title": (f.get("title") or f.get("titleExternal") or [""])[0],
        "hiring_manager_usernames": [hm.split("(")[-1].rstrip(")") for hm in f.get("hiringManager") or []],
        "hiring_manager_names": ["(".join(hm.split("(")[:-1]).strip() for hm in f.get("hiringManager") or []],
        "recruiter_usernames": [r.split("(")[-1].rstrip(")") for r in f.get("recruiters") or []],
        "recruiter_names": ["(".join(r.split("(")[:-1]).strip() for r in f.get("recruiters") or []],
        "basic_qualifications": strip_html("\n".join(f.get("basicQualifications") or [""])),
        "preferred_qualifications": strip_html("\n".join(f.get("preferredQualifications") or [""])),
        "description": strip_html("\n".join(f.get("description") or [""])),
        "raw_fields": f,
    }
    return out


def load_processed_job_ids(path: str = "job_match_results.json") -> set[str]:
    """
    Reads existing results file and returns a set of already-processed job_ids.
    Supports both yes_ranked and no_audit lists.
    """
    if not os.path.exists(path):
        return set()

    try:
        with open(path, "r") as f:
            data = json.load(f)
    except Exception:
        logging.warning("Could not read %s; treating as empty.", path)
        return set()

    processed = set()
    for section in ("yes_ranked", "no_audit"):
        for item in data.get(section, []) or []:
            jid = str(item.get("job_id") or "").strip()
            if jid:
                processed.add(jid)
    return processed


def merge_outputs(
    existing: Dict[str, Any],
    new_results: list[Dict[str, Any]],
    pricing_mode: str,
    pricing_usd_per_1m: Dict[str, Any],
    pro_prompt_tier_threshold_tokens: int,
) -> Dict[str, Any]:
    """
    Merges new per-job results into existing output structure, de-duping by job_id.
    Recomputes cost_summary across ALL stored jobs (lifetime totals).
    """
    existing_yes = existing.get("yes_ranked", []) or []
    existing_no = existing.get("no_audit", []) or []

    by_id: Dict[str, Dict[str, Any]] = {}
    for r in existing_yes + existing_no:
        jid = str(r.get("job_id") or "").strip()
        if jid:
            by_id[jid] = r

    for r in new_results:
        jid = str(r.get("job_id") or "").strip()
        if jid:
            by_id[jid] = r

    merged_all = list(by_id.values())

    def conf_rank(c: str) -> int:
        return {"HIGH": 2, "MEDIUM": 1, "LOW": 0}.get(c or "", 0)

    yes_ranked = [r for r in merged_all if r.get("decision") == "YES"]
    no_audit = [r for r in merged_all if r.get("decision") != "YES"]
    yes_ranked.sort(key=lambda r: (r.get("score") or 0, conf_rank(r.get("confidence"))), reverse=True)

    # ---- cost_summary computed here (lifetime) ----
    total_cost_known = 0.0
    unknown_cost_count = 0
    for r in merged_all:
        c = r.get("cost_usd")
        if c is None:
            unknown_cost_count += 1
        else:
            total_cost_known += float(c)

    cost_summary = {
        "pricing_mode": pricing_mode,
        "total_cost_usd_known": total_cost_known,
        "jobs_with_unknown_cost": unknown_cost_count,
        "pricing_usd_per_1m": pricing_usd_per_1m,
        "pro_prompt_tier_threshold_tokens": pro_prompt_tier_threshold_tokens,
    }

    return {
        "yes_ranked": yes_ranked,
        "no_audit": no_audit,
        "counts": {"total": len(merged_all), "yes": len(yes_ranked), "no": len(no_audit)},
        "cost_summary": cost_summary,
    }


def extract_json_blob(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""

    # Strip markdown fences
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n", "", s)
        s = re.sub(r"\n```$", "", s).strip()

    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return ""
    return s[start : end + 1]


def parse_json_simple(s: str) -> Dict[str, Any]:
    blob = extract_json_blob(s)
    if not blob:
        raise RuntimeError("No JSON object found in LLM output.")
    return json.loads(blob)


# ---------- ADK Agents (LLM) ----------

candidate_parser = LlmAgent(
    name="CandidateParser",
    model=MODEL_FAST,
    instruction=(
        "You will be given a candidate work summary.\n"
        "Extract a CandidateProfileJSON with:\n"
        "- roles_targeted\n- seniority\n- years_experience\n- skills_core (top)\n"
        "- skills_secondary\n- domains\n- constraints (if any)\n"
        "- aliases for skills/titles\n- evidence_snippets (10-20 short quotes)\n\n"
        "Return ONLY valid JSON. Start with { and end with }."
    ),
)

job_card_extractor = LlmAgent(
    name="JobCardExtractor",
    model=MODEL_FAST,
    instruction=(
        "You will be given one sanitized job object (JSON).\n"
        "Create JobCardJSON with:\n"
        "- job_id, title, hiring_manager_usernames, recruiter_usernames,\n"
        "- must_have_requirements (bullets)\n- nice_to_have (bullets)\n"
        "- responsibilities (bullets)\n- keywords\n- red_flags\n\n"
        "Return ONLY valid JSON. Start with { and end with }."
    ),
)

judge1 = LlmAgent(
    name="Judge1",
    model=MODEL_FAST,
    instruction=(
        "You will be given:\n"
        "1) candidate_work_summary text\n"
        "2) job_card_json\n\n"
        "Decide YES or NO for whether the job suits the candidate.\n"
        "Important policy to avoid false negatives:\n"
        "- Output NO only if you are HIGH confidence it is not a match.\n"
        "- If uncertain or missing info, output YES with LOW or MEDIUM confidence.\n\n"
        "Return ONLY valid JSON with keys:\n"
        "decision (YES|NO), confidence (HIGH|MEDIUM|LOW), score (0-100),\n"
        "evidence_candidate (2-4 short quotes), evidence_job (1-3 quotes), gaps (list).\n"
        "Start output with { and end with }."
    ),
)

judge2 = LlmAgent(
    name="Judge2",
    model=MODEL_STRONG,
    instruction=(
        "You are an independent second judge.\n"
        "You will be given:\n"
        "1) candidate_work_summary text\n"
        "2) job_card_json\n\n"
        "Try to find reasons the candidate COULD fit.\n"
        "Important policy to avoid false negatives:\n"
        "- Output NO only if you are HIGH confidence it is not a match.\n"
        "- If uncertain or missing info, output YES with LOW or MEDIUM confidence.\n\n"
        "Return ONLY valid JSON with EXACT keys:\n"
        "decision (YES|NO), confidence (HIGH|MEDIUM|LOW), score (0-100),\n"
        "evidence_candidate (2-4 short quotes), evidence_job (1-3 quotes), gaps (list).\n"
        "Start output with { and end with }. No markdown."
    ),
)

arbiter = LlmAgent(
    name="Arbiter",
    model=MODEL_STRONG,
    instruction=(
        "You are the arbitrator. You receive candidate text, job card, judge1 output, judge2 output.\n"
        "Your job is to CONFIRM YES matches only.\n\n"
        "Decision policy:\n"
        "- Output YES only if you are HIGH confidence the candidate is a good match.\n"
        "- Otherwise output NO. If there is uncertainty, missing evidence, or borderline fit, choose NO.\n\n"
        "Return ONLY valid JSON with keys:\n"
        "decision (YES|NO), confidence (HIGH|MEDIUM|LOW), score (0-100), rationale (1-3 sentences), gaps (list).\n\n"
        "Evidence rules:\n"
        "- rationale must reference 1-2 concrete items from candidate and 1-2 from the job.\n"
        "- do not hallucinate; use only provided inputs.\n"
        "Start output with { and end with }."
    ),
)

summary_writer = LlmAgent(
    name="SummaryWriter",
    model=MODEL_FAST,
    instruction=(
        "Write a grounded ~50-word email-ready summary in FIRST PERSON (as the candidate), "
        "so I can copy-paste it into an email.\n\n"
        "Inputs: candidate summary text + sanitized job JSON + final decision JSON.\n\n"
        "Rules:\n"
        "- 45-55 words\n"
        "- Use first person (I / my)\n"
        "- Professional tone, 1 short paragraph\n"
        "- Mention 2-3 of my strengths that match 1-2 job needs\n"
        "- If decision is NO: write a ~50-word first-person note explaining the key mismatch briefly\n"
        "- No bullet points, no headings, no extra sections\n"
        "- Do not hallucinate; use only provided inputs\n"
        "- Output plain text only"
    ),
)


# ---------- Runner helper ----------

@dataclass
class AdkRuntime:
    session_service: InMemorySessionService
    runners: Dict[str, Runner]


def _content_from_text(text: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=text)])


async def ensure_session(session_service: InMemorySessionService, session_id: str) -> None:
    try:
        await session_service.create_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)
    except Exception:
        pass


async def run_agent_text(
    runtime: AdkRuntime, agent_name: str, user_text: str, session_id: str
) -> Tuple[str, Optional[Dict[str, int]]]:
    await ensure_session(runtime.session_service, session_id)

    runner = runtime.runners[agent_name]
    last_text = ""
    
    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=session_id,
        new_message=_content_from_text(user_text),
    ):
        # 1) Try event.content.parts[*].text
        content = getattr(event, "content", None)
        parts = getattr(content, "parts", None) if content else None
        if parts:
            chunk = "".join([(getattr(p, "text", "") or "") for p in parts]).strip()
            if chunk:
                last_text = chunk

        # 2) Try event.response.content.parts[*].text
        resp = getattr(event, "response", None)
        if resp:
            r_content = getattr(resp, "content", None)
            r_parts = getattr(r_content, "parts", None) if r_content else None
            if r_parts:
                chunk = "".join([(getattr(p, "text", "") or "") for p in r_parts]).strip()
                if chunk:
                    last_text = chunk

        usage = extract_usage_from_event(event)
        
        if hasattr(event, "is_final_response") and event.is_final_response():
            break

    call_meta = {
        "model": getattr(runner.agent, "model", "unknown"),
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "candidate_tokens": int(usage.get("candidate_tokens") or 0),
    }
    call_meta["total_tokens"] = call_meta["prompt_tokens"] + call_meta["candidate_tokens"]

    return last_text.strip(), call_meta


def should_second_opinion(j1: Dict[str, Any]) -> bool:
    return (j1.get("decision") == "NO") or (j1.get("confidence") != "HIGH")


async def parse_json_or_retry(runtime, agent_name: str, raw: str, session_id: str) -> Dict[str, Any]:
    try:
        return parse_json_simple(raw)
    except Exception as e:
        logging.warning("JSON parse failed for %s (%s). Retrying once.", agent_name, e)

        fix_prompt = (
            "Return ONLY valid JSON for the previous answer. "
            "No markdown. No extra text. Ensure all strings are properly escaped."
        )
        fixed_text, _ = await run_agent_text(
            runtime, agent_name, fix_prompt, session_id=f"{session_id}-fix"
        )
        return parse_json_simple(fixed_text)


async def process_one_job(runtime: AdkRuntime, candidate_text: str, sanitized_job: Dict[str, Any]) -> Dict[str, Any]:
    base = f"jobmatch-{sanitized_job['job_id']}"
    sid_jobcard = f"{base}-jobcard"
    sid_j1 = f"{base}-j1"
    sid_j2 = f"{base}-j2"
    sid_arb = f"{base}-arb"
    sid_sum = f"{base}-sum"

    # Per-job cost tracking
    cost_total = 0.0
    usage_by_step: Dict[str, Optional[Dict[str, int]]] = {}
    cost_by_step: Dict[str, Optional[float]] = {}

    # 1) Extract job card
    job_card_raw, usage = await run_agent_text(
        runtime, "JobCardExtractor", json.dumps(sanitized_job), session_id=sid_jobcard
    )
    usage_by_step["job_card_extractor"] = usage
    if not job_card_raw.strip():
        raise RuntimeError("JobCardExtractor returned empty text; event extraction still not working.")
    job_card = await parse_json_or_retry(runtime, "JobCardExtractor", job_card_raw, sid_jobcard)

    # 2) Judge1
    j1_raw, usage = await run_agent_text(
        runtime,
        "Judge1",
        json.dumps({"candidate_work_summary": candidate_text, "job_card_json": job_card}),
        session_id=sid_j1,
    )
    usage_by_step["judge1"] = usage
    j1 = await parse_json_or_retry(runtime, "Judge1", j1_raw, sid_j1)
    final_decision = j1

    judge2_out: Optional[Dict[str, Any]] = None
    arb_out: Optional[Dict[str, Any]] = None

    # 3) Judge2 + Arbitration as needed
    if should_second_opinion(j1):
        j2_raw, usage = await run_agent_text(
            runtime,
            "Judge2",
            json.dumps({"candidate_work_summary": candidate_text, "job_card_json": job_card}),
            session_id=sid_j2,
        )
        usage_by_step["judge2"] = usage
        judge2_out = await parse_json_or_retry(runtime, "Judge2", j2_raw, sid_j2)

        if judge2_out.get("decision") != j1.get("decision"):
            if j1.get("decision") == "YES" or judge2_out.get("decision") == "YES":
                arb_raw, usage = await run_agent_text(
                    runtime,
                    "Arbiter",
                    json.dumps(
                        {
                            "candidate_work_summary": candidate_text,
                            "job_card_json": job_card,
                            "judge1": j1,
                            "judge2": judge2_out,
                        }
                    ),
                    session_id=sid_arb,
                )
                usage_by_step["arbiter"] = usage
                arb_out = await parse_json_or_retry(runtime, "Arbiter", arb_raw, sid_arb)
                final_decision = arb_out
            else:
                usage_by_step["arbiter"] = None
                cost_by_step["arbiter"] = None
        else:
            usage_by_step["arbiter"] = None
            cost_by_step["arbiter"] = None
    else:
        usage_by_step["judge2"] = None
        usage_by_step["arbiter"] = None
        cost_by_step["judge2"] = None
        cost_by_step["arbiter"] = None

    # 4) 50-word summary
    summary, usage = await run_agent_text(
        runtime,
        "SummaryWriter",
        json.dumps(
            {
                "candidate_work_summary": candidate_text,
                "job_json": sanitized_job,
                "final_decision": final_decision,
            }
        ),
        session_id=sid_sum,
    )
    usage_by_step["summary_writer"] = usage
    
    usage_by_model = usage_by_model_from_steps(usage_by_step)

    cost_by_model = {}
    for m, t in usage_by_model.items():
        cost_by_model[m] = cost_for_model(
            model=m,
            prompt_tokens=int(t.get("prompt_tokens") or 0),
            candidate_tokens=int(t.get("candidate_tokens") or 0),
            pricing_mode=PRICING_MODE,
            pro_prompt_tier_threshold_tokens=PRO_TIER_THRESHOLD_TOKENS,
            pricing_usd_per_1m=PRICING_USD_PER_1M,
        )

    cost_total = sum(cost_by_model.values())

    logging.info(
        "job_id=%s decision=%s score=%s pricing_mode=%s cost_usd=%s usage_by_step=%s usage_by_model=%s cost_by_model=%s",
        sanitized_job["job_id"],
        final_decision.get("decision"),
        final_decision.get("score"),
        PRICING_MODE,
        f"{cost_total:.6f}",
        json.dumps(usage_by_step),
        json.dumps(usage_by_model),
        json.dumps(cost_by_model),
    )

    hiring_manager_usernames = sanitized_job["hiring_manager_usernames"]

    return {
        "job_id": sanitized_job["job_id"],
        "title": sanitized_job["title"],
        "hiring_manager_usernames": hiring_manager_usernames,
        "recruiter_usernames": sanitized_job["recruiter_usernames"],
        "employee_hierarchy": get_employee_hierarchy(hiring_manager_usernames[0], target_level=8) if hiring_manager_usernames else None,
        "decision": final_decision.get("decision"),
        "confidence": final_decision.get("confidence"),
        "score": final_decision.get("score"),
        "summary_50w": summary,
        "judge1": j1,
        "judge2": judge2_out,
        "arbiter": arb_out,
        "job_card": job_card,
        "pricing_mode": PRICING_MODE,
        "cost_usd": cost_total,
        "usage_by_step": usage_by_step,
        "cost_by_step": cost_by_step,
        "usage_by_step": usage_by_step,
        "usage_by_model": usage_by_model,
        "cost_by_model": cost_by_model,
    }


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    # full_run from argument parser
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--full-run",
        action="store_false",
        help="Process all jobs instead of just 1 (for testing)."
    )
    args = parser.parse_args()
    full_run = args.full_run

    ensure_results_dirs()

    # Startup aggregation: recover progress from last run (per-job files) and refresh final output
    aggregated = aggregate_from_disk(
        pricing_mode=PRICING_MODE,
        pricing_usd_per_1m=PRICING_USD_PER_1M,
        pro_prompt_tier_threshold_tokens=PRO_TIER_THRESHOLD_TOKENS,
    )

    processed_ids = processed_job_ids_from_aggregate(aggregated)
    logging.info("Recovered %d processed jobs from %s.", len(processed_ids), RESULTS_DIR)

    if PRICING_MODE not in {"standard", "batch"}:
        raise ValueError("PRICING_MODE must be 'standard' or 'batch'")

    if PRICING_MODE == "batch":
        logging.warning(
            "PRICING_MODE=batch affects COST ESTIMATION only. "
            "Actual Batch API execution is not implemented in this ADK Runner flow."
        )

    candidate_text = os.getenv("CANDIDATE_SUMMARY", "").strip()
    if not candidate_text:
        raise ValueError("CANDIDATE_SUMMARY is missing in your .env")

    jobs = get_jobs()

    sanitized_jobs = [sanitize_job(j) for j in jobs]

    before = len(sanitized_jobs)
    sanitized_jobs = [j for j in sanitized_jobs if str(j.get("job_id") or "").strip() not in processed_ids]
    after = len(sanitized_jobs)

    logging.info("Skipping %d already processed jobs. Remaining to process: %d (from %d).",
                before - after, after, before)

    if not sanitized_jobs:
        logging.info("No new jobs to process. Exiting.")
        return
    
    if full_run:
        # keep 1 while debugging
        sanitized_jobs = sanitized_jobs[:1]

    session_service = InMemorySessionService()

    runners = {
        "CandidateParser": Runner(app_name=APP_NAME, agent=candidate_parser, session_service=session_service),
        "JobCardExtractor": Runner(app_name=APP_NAME, agent=job_card_extractor, session_service=session_service),
        "Judge1": Runner(app_name=APP_NAME, agent=judge1, session_service=session_service),
        "Judge2": Runner(app_name=APP_NAME, agent=judge2, session_service=session_service),
        "Arbiter": Runner(app_name=APP_NAME, agent=arbiter, session_service=session_service),
        "SummaryWriter": Runner(app_name=APP_NAME, agent=summary_writer, session_service=session_service),
    }
    runtime = AdkRuntime(session_service=session_service, runners=runners)

    sem = asyncio.Semaphore(10)

    async def worker(job: Dict[str, Any]) -> Dict[str, Any]:
        async with sem:
            try:
                result = await process_one_job(runtime, candidate_text, job)
                persist_per_job_result(result)  # checkpoint success
                return result
            except Exception as e:
                # checkpoint failure, and continue
                logging.exception("Job failed job_id=%s title=%s", job.get("job_id"), job.get("title"))
                persist_per_job_error(job, stage="process_one_job", err=e)
                return {
                    "job_id": job.get("job_id"),
                    "title": job.get("title"),
                    "decision": "ERROR",
                    "error": {"type": type(e).__name__, "message": str(e)},
                }

    results = await asyncio.gather(*(worker(j) for j in sanitized_jobs), return_exceptions=False)

    # After processing (even partial), re-aggregate from disk (final file + all per-job)
    final_agg = aggregate_from_disk(
        pricing_mode=PRICING_MODE,
        pricing_usd_per_1m=PRICING_USD_PER_1M,
        pro_prompt_tier_threshold_tokens=PRO_TIER_THRESHOLD_TOKENS,
    )

    logging.info("Final aggregated results written to: %s", FINAL_RESULTS_PATH)
    logging.info("Counts: %s", final_agg["counts"])


if __name__ == "__main__":
    asyncio.run(main())
