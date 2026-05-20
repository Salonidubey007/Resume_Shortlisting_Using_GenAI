# engines/scoring_engine.py

import json
import time
from pathlib import Path
from typing import List, Dict, Any

import pandas as pd

from utils.llm_client import LLMClient
from utils.logger import log_info, log_warn, log_error
from utils.parallel import WorkerPool

# --------------------------
# Paths (consistent with project)
# --------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STORAGE_DIR = PROJECT_ROOT / "storage"
DATA_RESUMES_DIR = PROJECT_ROOT / "data" / "resumes"

JD_LATEST = STORAGE_DIR / "jd_latest.json"
JD_JSONS_DIR = STORAGE_DIR / "jd_jsons"
RESUMES_MASTER = STORAGE_DIR / "resumes_master.json"

FINAL_RESULTS_XLSX = STORAGE_DIR / "final_results.xlsx"

PROMPTS_DIR = PROJECT_ROOT / "prompts"
MS_PROMPT_FILE = PROMPTS_DIR / "MS_Prompt.md"

RAW_DEBUG = STORAGE_DIR / "raw" / "scoring"

# Scoring uses the 70B model (high accuracy)
MODEL_70B = "@cf/meta/llama-3.1-70b-instruct"


def load_ms_prompt() -> str:
    return MS_PROMPT_FILE.read_text(encoding="utf-8")


def _score_single_resume(resume_json: Dict[str, Any], jd_json: Dict[str, Any], *, debug_dir: Path) -> Dict[str, Any]:
    """
    One-shot scoring for a single resume using LLM 70B.
    """
    if not isinstance(resume_json, dict):
        return {}

    # Build the one-shot scoring prompt
    prompt_template = load_ms_prompt()

    user_prompt = (
        prompt_template
            .replace("{{JD_JSON}}", json.dumps(jd_json, ensure_ascii=False, indent=2))
            .replace("{{RESUME_JSON}}", json.dumps(resume_json, ensure_ascii=False, indent=2))
    )

    messages = [
        {"role": "system", "content": "You are a strict JSON scorer. Return only JSON."},
        {"role": "user", "content": user_prompt}
    ]

    client = LLMClient(model=MODEL_70B, max_tokens=2048)
    result = client.run(messages, debug_dir=debug_dir)

    if not isinstance(result, dict):
        log_warn("[SCORING] Score model returned non-dict")
        return {}

    # Compute total explicitly if needed
    fields = [
        "role_score",
        "grade_score",
        "location_score",
        "domain_score",
        "experience_score",
        "project_score",
        "mandatory_skill_score",
        "nice_to_have_skill_score",
        "soft_skill_score",
        "responsibilities_score"
    ]

    total = 0
    for f in fields:
        try:
            total += int(result.get(f, 0))
        except:
            pass

    result["total"] = max(0, min(100, total))
    return result


def _load_latest_jd() -> Dict[str, Any]:
    """
    jd_latest.json contains: { "use": "<JD base name or JD json name>" }
    Normalize pointer to the actual JSON file under storage/jd_jsons/.
    Supports pointers like:
      - "SomeJD.docx"  -> use stem "SomeJD.json"
      - "SomeJD"       -> try "SomeJD.json"
      - "SomeJD.json"  -> direct
    """
    if not JD_LATEST.exists():
        raise FileNotFoundError("jd_latest.json not found. Parse a JD first.")

    pointer = json.loads(JD_LATEST.read_text(encoding="utf-8"))
    use = (pointer.get("use") or "").strip()
    if not use:
        raise FileNotFoundError("jd_latest.json missing 'use' field")

    # candidate 1: use as-is
    cand1 = JD_JSONS_DIR / use
    if cand1.exists():
        return json.loads(cand1.read_text(encoding="utf-8"))

    # candidate 2: stem + .json
    p = Path(use)
    cand2 = JD_JSONS_DIR / f"{p.stem}.json"
    if cand2.exists():
        return json.loads(cand2.read_text(encoding="utf-8"))

    # candidate 3: append .json
    if not use.lower().endswith(".json"):
        cand3 = JD_JSONS_DIR / (use + ".json")
        if cand3.exists():
            return json.loads(cand3.read_text(encoding="utf-8"))

    # helpful error
    tried = [cand1.name, f"{p.stem}.json"]
    if not use.lower().endswith(".json"):
        tried.append(use + ".json")
    raise FileNotFoundError(
        f"JD JSON not found for pointer '{use}'. Checked {tried} under {JD_JSONS_DIR}"
    )


def _load_resumes_master() -> List[Dict[str, Any]]:
    """
    Load all resume JSON objects from resumes_master.json (JSONL).
    """
    if not RESUMES_MASTER.exists():
        raise FileNotFoundError("resumes_master.json not found. Parse resumes first.")

    items = []
    with open(RESUMES_MASTER, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
                if isinstance(obj, dict):
                    items.append(obj)
            except:
                continue

    return items


def score_all_resumes(
    *,
    top_n: int = 50,
    verbose: bool = True,
    max_workers: int = 6
) -> Dict[str, Any]:
    """
    Main public scoring function.
    Loads JD + resume master.
    Performs one-shot scoring.
    Writes final_results.xlsx.
    """
    # Load JD
    jd_json = _load_latest_jd()
    jd_filename = jd_json.get("Meta", {}).get("jd_source_filename", "-")

    # Load all resumes
    resumes = _load_resumes_master()
    total_resumes = len(resumes)

    if verbose:
        log_info(f"[SCORING] Starting scoring for {total_resumes} resumes")
        log_info(f"[SCORING] JD used: {jd_filename}")

    pool = WorkerPool(max_workers=max_workers)

    start_time = time.time()
    results = []

    # Wrapper for WorkerPool to track index
    def _worker_wrapper(resume_json):
        index = resume_json.get("Source_file", "unknown")

        out = _score_single_resume(
            resume_json,
            jd_json,
            debug_dir=RAW_DEBUG / f"score_{index}"
        )

        # enrich scoring JSON
        out["resume_source"] = resume_json.get("Source_file", "")
        out["resume_path"] = str(DATA_RESUMES_DIR / resume_json.get("Source_file", ""))
        out["resume_emp_id"] = resume_json.get("Candidate_Emp_ID", "")
        out["resume_name"] = resume_json.get("Candidate_name", "")
        out["jd_file"] = jd_filename
        out["total_experience_years"] = resume_json.get("Total_experience_years", "")

        return out

    # Run scoring in parallel
    scored = []
    counter = 0

    for result in pool.map(_worker_wrapper, resumes, desc="scoring resumes", log_every=1):
        counter += 1

        if verbose:
            elapsed = time.time() - start_time
            rate = counter / elapsed if elapsed else 0
            eta = (total_resumes - counter) / rate if rate else 0

            mm, ss = divmod(int(eta), 60)
            name = result.get("resume_name") or result.get("resume_emp_id", "")
            log_info(f"[SCORING] {counter}/{total_resumes} | score={result.get('total',0)} "
                     f"| rate={rate:.2f}/s | ETA={mm:02d}:{ss:02d} | {name}")

        scored.append(result)

    # Sort by total score
    scored_sorted = sorted(scored, key=lambda x: x.get("total", 0), reverse=True)

    # Prepare summary
    summary_rows = []
    for i, row in enumerate(scored_sorted[:top_n]):
        summary_rows.append({
            "rank": i + 1,
            "employee_ID": row.get("resume_emp_id", ""),
            "employee_name": row.get("resume_name", ""),
            "total_experience_years": row.get("total_experience_years", ""),
            "total_score": row.get("total", 0),
            "file_name": row.get("resume_source", ""),
            "file_path": row.get("resume_path", ""),
            "jd_file": row.get("jd_file", ""),
            "role_score": row.get("role_score", 0),
            "grade_score": row.get("grade_score", 0),
            "location_score": row.get("location_score", 0),
            "domain_score": row.get("domain_score", 0),
            "experience_score": row.get("experience_score", 0),
            "project_score": row.get("project_score", 0),
            "mandatory_skill_score": row.get("mandatory_skill_score", 0),
            "nice_to_have_skill_score": row.get("nice_to_have_skill_score", 0),
            "soft_skill_score": row.get("soft_skill_score", 0),
            "responsibilities_score": row.get("responsibilities_score", 0)
        })

    # Write Excel
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        df_summary = pd.DataFrame(summary_rows)
        df_resumes = pd.DataFrame(scored_sorted)
        df_jd = pd.DataFrame([jd_json])

        with pd.ExcelWriter(FINAL_RESULTS_XLSX, engine="openpyxl") as writer:
            df_summary.to_excel(writer, index=False, sheet_name="Summary")
            df_resumes.to_excel(writer, index=False, sheet_name="Resumes_JSON")
            df_jd.to_excel(writer, index=False, sheet_name="JD")

        log_info(f"[SCORING] Excel saved → {FINAL_RESULTS_XLSX}")

    except Exception as e:
        log_error(f"[SCORING] Failed to save Excel: {e}")

    return {
        "jd_file": jd_filename,
        "total_resumes": total_resumes,
        "top_n": top_n,
        "results": summary_rows
    }