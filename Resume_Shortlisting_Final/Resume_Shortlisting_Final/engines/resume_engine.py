# engines/resume_engine.py

import json
from pathlib import Path
from typing import List, Dict, Any
import re
from typing import Any
import pandas as pd
import hashlib
from utils.history_manager import load_history, save_history, append_history

from utils.llm_client import LLMClient
from utils.file_reader import read_any
from utils.json_tools import extract_json
from utils.text_cleaner import clean_text
from utils.schema_validator import validate_resume_schema
from utils.logger import log_info, log_warn, log_error
from utils.parallel import WorkerPool


# Paths (matching your project structure)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_RESUMES_DIR = PROJECT_ROOT / "data" / "resumes"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
STORAGE_DIR = PROJECT_ROOT / "storage"
RESUME_JSONS_DIR = STORAGE_DIR / "resume_jsons"
ARCHIVE_JSONS_DIR = RESUME_JSONS_DIR / "archived"
RESUMES_MASTER_JSON = STORAGE_DIR / "resumes_master.json"
RAW_DEBUG_DIR = STORAGE_DIR / "raw" / "resumes"

RS_PROMPT_FILE = PROMPTS_DIR / "RS_Prompt.md"

# Cloudflare model for parsing
MODEL_8B = "@cf/meta/llama-3.1-8b-instruct"


# -----------------------------
# Chunking + Compact + Merge
# -----------------------------

def _clip_str(s: str, max_len: int) -> str:
    if not s:
        return ""
    s = str(s)
    return s[:max_len].strip()

SECTION_PAT = re.compile(
    r"(?im)^\s*(experience|work experience|employment history|projects|assignments|engagements|responsibilit(?:y|ies))\s*[:\-]?\s*$"
)

def _section_aware_compact(text: str, *, max_chars=18000) -> str:
    """
    1) Prefer important sections (Experience/Projects/Responsibilities).
    2) Use remaining budget for head + tail.
    3) Final clamp to max_chars.
    """
    if not text:
        return ""
    lines = text.splitlines()
    blocks, curr = [], []
    for ln in lines:
        if SECTION_PAT.match(ln):
            if curr:
                blocks.append("\n".join(curr).strip())
                curr = []
            curr.append(ln)
        else:
            curr.append(ln)
    if curr:
        blocks.append("\n".join(curr).strip())

    picked, used = [], 0
    for b in blocks:
        if used + len(b) <= int(max_chars * 0.7):
            picked.append(b)
            used += len(b)
        else:
            break

    body = "\n\n".join(picked).strip()
    need = max_chars - len(body)
    if need > 0:
        head = text[: int(need * 0.6)]
        tail = text[-int(need * 0.4):] if len(text) > int(need * 0.6) else ""
        body = (body + "\n\n" + head + "\n\n" + tail).strip()

    return _clip_str(body, max_chars)

def _chunk_by_chars(text: str, *, chunk_chars=9000, overlap=600):
    """
    Break long text into chunks of ~chunk_chars with overlap.
    Good for 8k context models (safe headroom).
    """
    text = (text or "").strip()
    n = len(text)
    if n <= chunk_chars:
        return [text]
    chunks = []
    start = 0
    while start < n:
        end = min(n, start + chunk_chars)
        chunks.append(text[start:end])
        if end == n:
            break
        start = max(end - overlap, 0)
    return chunks

def _merge_resume_objs(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge two partial resume JSONs.
    - Scalars: prefer non-empty (a first), else take from b.
    - Lists: union de-duped with caps.
    - Tech_skillset merged per list with caps.
    """
    if not isinstance(a, dict): a = {}
    if not isinstance(b, dict): b = {}
    out = dict(a)

    def pick_scalar(k):
        va = a.get(k)
        vb = b.get(k)
        if va not in (None, "", "insufficient data"):
            out[k] = va
        elif vb not in (None, "", "insufficient data"):
            out[k] = vb
        else:
            out[k] = va if va is not None else vb

    def union_list(k, limit=60):
        la = a.get(k) or []
        lb = b.get(k) or []
        merged, seen = [], set()
        for x in list(la) + list(lb):
            xs = (x or "").strip()
            if not xs: 
                continue
            key = xs.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(xs)
            if len(merged) >= limit:
                break
        out[k] = merged

    # Scalars
    for k in [
        "Candidate_Emp_ID","Candidate_name","Current_Role","Candidate_grade",
        "Profile_location","Total_experience_years","Count_of_projects",
        "Current_project_age","Previously_worked_for_the_customer","Source_file"
    ]:
        pick_scalar(k)

    # Lists
    for k in ["Domains_known","Functional_skillset","Overall_responsibilities"]:
        union_list(k, limit=60)

    # Tech_skillset (lists)
    ta = a.get("Tech_skillset") or {}
    tb = b.get("Tech_skillset") or {}
    prim = (ta.get("primary_tech_skills") or []) + (tb.get("primary_tech_skills") or [])
    sec = (ta.get("secondary_tech_skills") or []) + (tb.get("secondary_tech_skills") or [])

    def _uniq(xs, cap=100):
        outl, seen = [], set()
        for x in xs:
            if not x: 
                continue
            k = x.lower()
            if k in seen: 
                continue
            seen.add(k); outl.append(x)
            if len(outl) >= cap: 
                break
        return outl

    out["Tech_skillset"] = {
        "primary_tech_skills": _uniq(prim, 100),
        "secondary_tech_skills": _uniq(sec, 100),
    }
    return out

def _content_hash(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def _call_llm_resume(path_name: str, text: str, debug_dir: Path) -> Dict[str, Any]:
    """
    Single LLM call for one chunk. 
    Lower max_tokens because resume JSON is small.
    """
    messages = _prepare_messages(path_name, text)
    client = LLMClient(model=MODEL_8B, max_tokens=384)  # tighter budget
    return client.run(messages, debug_dir=debug_dir)


def load_prompt() -> str:
    return RS_PROMPT_FILE.read_text(encoding="utf-8")


def _prepare_messages(file_name: str, resume_text: str) -> List[Dict[str, str]]:
    prompt_text = load_prompt()

    user_msg = prompt_text.replace("{{FILE_NAME}}", file_name)\
                          .replace("{{RESUME_TEXT}}", resume_text)

    return [
        {"role": "system", "content": "You are a strict JSON extractor. Return only JSON."},
        {"role": "user", "content": user_msg},
    ]


def _resume_output_path(stem: str) -> Path:
    return RESUME_JSONS_DIR / f"{stem}.json"


def _append_to_master(json_obj: Dict[str, Any]):
    """
    Append a single resume JSON object to resumes_master.json (JSONL format).
    """
    RESUMES_MASTER_JSON.parent.mkdir(parents=True, exist_ok=True)

    with open(RESUMES_MASTER_JSON, "a", encoding="utf-8") as f:
        f.write(json.dumps(json_obj, ensure_ascii=False) + "\n")


def _rebuild_master_from_individual():
    """
    Recreate resumes_master.json from storage/resume_jsons/*.json
    Latest version always wins per employee.
    """
    RESUMES_MASTER_JSON.parent.mkdir(parents=True, exist_ok=True)
    RESUMES_MASTER_JSON.write_text("", encoding="utf-8")

    records = {}

    for jp in sorted(RESUME_JSONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime):
        try:
            obj = json.loads(jp.read_text(encoding="utf-8"))
        except:
            continue

        emp_id = (obj.get("Candidate_Emp_ID") or "").strip().lower()
        name = (obj.get("Candidate_name") or "").strip().lower()
        if not emp_id or not name:
            continue

        key = f"{emp_id}_{name}"

        # ✅ Always replace → latest version wins
        records[key] = obj

    with open(RESUMES_MASTER_JSON, "w", encoding="utf-8") as f:
        for obj in records.values():
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    log_info(f"[RESUME_ENGINE] Master rebuilt from individual JSONs. Unique resumes: {len(records)}")

def _load_existing_master() -> List[Dict[str, Any]]:
    if not RESUMES_MASTER_JSON.exists():
        return []
    out = []
    with open(RESUMES_MASTER_JSON, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except:
                continue
    return out


def _parse_single_resume(path: Path, *, force_reparse=False) -> Dict[str, Any]:
    stem = path.stem
    out_json_path = _resume_output_path(stem)

    # -------------------------------
    # 1. Read file + compute content hash
    # -------------------------------
    try:
        raw = read_any(path)
    except Exception as e:
        log_error(f"[RESUME_ENGINE] Read failed for {path.name}: {e}")
        return {}

    cleaned = clean_text(raw)
    this_hash = _content_hash(cleaned)

    # -------------------------------
    # 2. Cache check: skip LLM only if hash matches
    # -------------------------------
    if out_json_path.exists() and not force_reparse:
        try:
            cached = json.loads(out_json_path.read_text(encoding="utf-8"))
            if cached.get("_content_hash") == this_hash:
                log_info(f"[RESUME_ENGINE] Using cached JSON: {path.name}")
                return cached
        except:
            log_warn("[RESUME_ENGINE] Cached JSON invalid, reparsing.")

    # -------------------------------
    # 3. LLM parse (original logic preserved)
    # -------------------------------
    compact = _section_aware_compact(cleaned, max_chars=18000)
    parsed = _call_llm_resume(path.name, compact, RAW_DEBUG_DIR / stem)

    if not isinstance(parsed, dict) or not parsed:
        chunks = _chunk_by_chars(cleaned, chunk_chars=9000, overlap=600)
        merged = {}
        for i, ch in enumerate(chunks, start=1):
            part = _call_llm_resume(
                f"{path.name} [chunk {i}/{len(chunks)}]",
                ch,
                RAW_DEBUG_DIR / f"{stem}_chunk{i}"
            )
            if isinstance(part, dict) and part:
                merged = _merge_resume_objs(merged, part)
        parsed = merged

    parsed = validate_resume_schema(parsed)
    parsed["Source_file"] = path.name
    parsed["_content_hash"] = this_hash

    # Identify employee
    emp_id = (parsed.get("Candidate_Emp_ID") or "").strip()
    emp_name = (parsed.get("Candidate_name") or "").strip().replace(" ", "_")

    archive_path = STORAGE_DIR / "resume_archive" / f"{emp_id}_{emp_name}.json"

    # -------------------------------
    # 4. Archival logic
    # -------------------------------
    history = load_history(archive_path)

    old_current = history.get("current_version", {})
    if old_current and old_current.get("_content_hash") != this_hash:
        append_history(history, old_current)

    history["current_version"] = parsed
    save_history(archive_path, history)

    # -------------------------------
    # 5. Save individual JSON (original behavior unchanged)
    # -------------------------------
    out_json_path.parent.mkdir(parents=True, exist_ok=True)
    out_json_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    log_info(f"[RESUME_ENGINE] Parsed: {path.name}")
    return parsed


def detect_updated_resumes(resumes_dir: Path = DATA_RESUMES_DIR) -> Dict[str, Any]:
    """
    Detect which uploaded resume files are updates to existing employees.
    Matches by Candidate_Emp_ID extracted from filename (prefix before first '_').
    Returns: { "new": [...filenames], "updated": [...filenames] }
    """
    resumes_dir = Path(resumes_dir)
    if not resumes_dir.exists():
        return {"new": [], "updated": []}

    # Build map of emp_id -> existing json stem from resume_jsons/
    existing_ids: Dict[str, str] = {}
    for jp in RESUME_JSONS_DIR.glob("*.json"):
        if jp.parent != RESUME_JSONS_DIR:  # skip archived subfolder
            continue
        try:
            obj = json.loads(jp.read_text(encoding="utf-8"))
            emp_id = (obj.get("Candidate_Emp_ID") or "").strip()
            if emp_id:
                existing_ids[emp_id.lower()] = jp.stem
        except:
            continue

    new_files, updated_files = [], []
    exts = {".pdf", ".docx", ".doc", ".txt", ".pptx"}
    for p in sorted(resumes_dir.iterdir()):
        if not p.is_file() or p.suffix.lower() not in exts:
            continue
        # Try to extract emp_id from filename stem (e.g. "30005909_Name_Resume")
        parts = p.stem.split("_")
        emp_id = parts[0].strip().lower() if parts else ""
        if emp_id and emp_id in existing_ids:
            updated_files.append(p.name)
        else:
            new_files.append(p.name)

    return {"new": new_files, "updated": updated_files}


def parse_resumes(
    resumes_dir: Path = DATA_RESUMES_DIR,
    *,
    force_reparse: bool = False,
    parse_mode: str = "all",
    max_workers: int = 6,
    verbose: bool = True
) -> List[Dict[str, Any]]:
    """
    Public API: Parse ALL resumes under data/resumes.
    Fills:
      - storage/resume_jsons/
      - storage/resumes_master.json (JSONL)
    """
    resumes_dir = Path(resumes_dir)
    if not resumes_dir.exists():
        raise FileNotFoundError(f"Resume directory not found: {resumes_dir}")

    all_files = sorted(
        [p for p in resumes_dir.iterdir() if p.is_file() and p.suffix.lower() in {".pdf", ".docx", ".doc", ".txt", ".pptx"}]
    )

    if parse_mode in ("new", "updated"):
        detected = detect_updated_resumes(resumes_dir)
        target_names = set(detected["updated"] if parse_mode == "updated" else detected["new"])
        files = [p for p in all_files if p.name in target_names]
        if verbose:
            log_info(f"[RESUME_ENGINE] parse_mode='{parse_mode}' → {len(files)} files selected.")
    else:
        files = all_files

    if verbose:
        log_info(f"[RESUME_ENGINE] Found {len(files)} resume files.")

    pool = WorkerPool(max_workers=max_workers)
    results = pool.map(
        lambda p: _parse_single_resume(p, force_reparse=force_reparse),
        files,
        desc="resume parsing",
        log_every=5
    )

    # Filter empty results (skip failed/empty dicts)
    parsed_resumes = [
        r for r in results
        if isinstance(r, dict) and r and (r.get("Source_file") or "").strip()
    ]

    if force_reparse:
        # FORCE path: Individual JSONs refresh ho chuke honge — ab MASTER ko clean re-build karo
        _rebuild_master_from_individual()
        # Debug: confirm file line count after rebuild
        total_after = len(_load_existing_master())
        log_info(f"[RESUME_ENGINE] Final total cached in master (after rebuild): {total_after}")
        # IMPORTANT: yahin RETURN kar do — taa ki append path run na ho
        return parsed_resumes

    # NORMAL path: sirf NEW unique entries append karo
    master_existing = _load_existing_master()
    master_filenames = {
        (r.get("Source_file") or "").strip().lower()
        for r in master_existing
        if isinstance(r, dict)
    }

    new_count = 0
    for obj in parsed_resumes:
        src = (obj.get("Source_file") or "").strip().lower()
        if not src:
            continue
        if src not in master_filenames:
            _append_to_master(obj)
            new_count += 1

    if verbose:
        log_info(f"[RESUME_ENGINE] Appended new unique resumes to master: {new_count}")
    
    # CRITICAL FIX: Always rebuild master from individual JSONs to ensure sync
    # This ensures that if any resumes were parsed but not added to master, they get included
    log_info("[RESUME_ENGINE] Rebuilding master from individual JSONs to ensure sync...")
    _rebuild_master_from_individual()
    
    final_total = len(_load_existing_master())
    if verbose:
        log_info(f"[RESUME_ENGINE] Final total cached in master: {final_total}")

    # end of parse_resumes(), after appending new JSONs to master
    try:
        _write_master_excel(RESUMES_MASTER_JSON, STORAGE_DIR / "resumes_master.xlsx")
        if verbose:
            log_info("[RESUME_ENGINE] Updated resumes_master.xlsx")
    except Exception as e:
        if verbose:
            log_warn(f"[RESUME_ENGINE] Failed to update resumes_master.xlsx: {e}")

    return parsed_resumes


def _write_master_excel(master_jsonl: Path, out_xlsx: Path):
    rows = []
    if master_jsonl.exists():
        with open(master_jsonl, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    obj = json.loads(ln)
                    if isinstance(obj, dict):
                        rows.append(obj)
                except Exception:
                    continue
    if not rows:
        return
    df = pd.DataFrame(rows)

    # Optional: columns order
    cols_order = [
        "Candidate_Emp_ID","Candidate_name","Current_Role","Candidate_grade",
        "Profile_location","Total_experience_years","Count_of_projects",
        "Current_project_age","Previously_worked_for_the_customer",
        "Source_file"
    ]
    front = [c for c in cols_order if c in df.columns]
    rest  = [c for c in df.columns if c not in front]
    df = df[front + rest]
    out_xlsx.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(out_xlsx, index=False, engine="openpyxl")