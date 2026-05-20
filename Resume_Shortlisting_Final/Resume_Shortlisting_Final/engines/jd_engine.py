# engines/jd_engine.py

import json
from pathlib import Path
from typing import Dict, Any

from utils.llm_client import LLMClient
from utils.file_reader import read_any
from utils.text_cleaner import clean_text
from utils.json_tools import extract_json
from utils.schema_validator import validate_jd_schema
from utils.logger import log_info, log_warn, log_error


# Paths (aligned with your project structure)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_JDS_DIR = PROJECT_ROOT / "data" / "jds"
STORAGE_DIR = PROJECT_ROOT / "storage"
JD_JSONS_DIR = STORAGE_DIR / "jd_jsons"
RAW_DEBUG_DIR = STORAGE_DIR / "raw" / "jds"
PROMPTS_DIR = PROJECT_ROOT / "prompts"

JD_LATEST = STORAGE_DIR / "jd_latest.json"
JD_MASTER = STORAGE_DIR / "jds_master.json"

JD_PROMPT_FILE = PROMPTS_DIR / "JD_Prompt.md"

# Cloudflare model for JD parsing (8B = cheap + fast)
MODEL_8B = "@cf/meta/llama-3.1-8b-instruct"


def load_prompt() -> str:
    return JD_PROMPT_FILE.read_text(encoding="utf-8")


def _prepare_messages(jd_filename: str, jd_text: str):
    prompt_text = load_prompt()
    body = prompt_text.replace("{{JD_FILE_NAME}}", jd_filename)\
                      .replace("{{JD_TEXT}}", jd_text)

    return [
        {"role": "system", "content": "You are a strict JSON extractor. Return ONLY JSON."},
        {"role": "user", "content": body}
    ]


def _jd_output_path(stem: str) -> Path:
    return JD_JSONS_DIR / f"{stem}.json"


def _append_to_jd_master(obj: Dict[str, Any]):
    """
    Append a JD JSON object to jds_master.json (JSONL format)
    """
    JD_MASTER.parent.mkdir(parents=True, exist_ok=True)

    with open(JD_MASTER, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _load_jd_master() -> Dict[str, Dict[str, Any]]:
    """
    Returns mapping:
    {
      "filename": {full jd json...},
      ...
    }
    """
    if not JD_MASTER.exists():
        return {}

    items = {}
    with open(JD_MASTER, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
                fname = obj.get("Meta", {}).get("jd_source_filename")
                if fname:
                    items[fname] = obj
            except:
                continue
    return items


def _update_latest_pointer(jd_filename: str):
    """
    Save file pointer:
    jd_latest.json = { "use": "<filename>.json" }
    """
    JD_LATEST.parent.mkdir(parents=True, exist_ok=True)
    JD_LATEST.write_text(
        json.dumps({"use": jd_filename}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    log_info(f"[JD_ENGINE] Updated jd_latest.json → {jd_filename}")


def _parse_single_jd(jd_path: Path, *, force_reparse: bool = False) -> Dict[str, Any]:
    """
    Parse a single JD file using Cloudflare Llama 3.1 8B.
    Implements caching and master logic.
    """
    stem = jd_path.stem
    jd_filename = jd_path.name
    out_json_path = _jd_output_path(stem)

    # Load existing master for caching
    jd_master_map = _load_jd_master()

    # If cached in master and not forcing reparse → reuse it
    if jd_filename in jd_master_map and not force_reparse:
        log_info(f"[JD_ENGINE] Using cached JD from jds_master.json → {jd_filename}")
        cached_obj = jd_master_map[jd_filename]
        _update_latest_pointer(jd_filename)
        return cached_obj

    # Read JD text
    try:
        raw_text = read_any(jd_path)
    except Exception as e:
        log_error(f"[JD_ENGINE] Failed to read JD file {jd_filename}: {e}")
        return {}

    cleaned = clean_text(raw_text)
    messages = _prepare_messages(jd_filename, cleaned)

    # LLM call
    client = LLMClient(model=MODEL_8B, max_tokens=2048)
    jd_json = client.run(messages, debug_dir=RAW_DEBUG_DIR / stem)

    if not isinstance(jd_json, dict):
        log_warn(f"[JD_ENGINE] LLM returned non-dict for {jd_filename}")
        jd_json = {}

    # Validate schema
    jd_json = validate_jd_schema(jd_json)

    # Set Meta.jd_source_filename correctly
    if "Meta" not in jd_json or not isinstance(jd_json["Meta"], dict):
        jd_json["Meta"] = {}
    jd_json["Meta"]["jd_source_filename"] = jd_filename

    # Write individual JD JSON file
    out_json_path.parent.mkdir(parents=True, exist_ok=True)
    out_json_path.write_text(
        json.dumps(jd_json, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # Append to JD master (overwrite old copies if any)
    _append_to_jd_master(jd_json)

    # Update pointer
    _update_latest_pointer(jd_filename)

    log_info(f"[JD_ENGINE] Parsed JD: {jd_filename}")
    log_info(f"[JD_ENGINE] Saved: {out_json_path}")

    return jd_json


def parse_jd(
    jd_path: Path,
    *,
    force_reparse: bool = False,
    verbose: bool = True
) -> Dict[str, Any]:
    """
    Public API: Parse a single JD file.
    - Respects caching
    - Updates JD master + latest pointer
    """
    jd_path = Path(jd_path)

    if not jd_path.exists():
        raise FileNotFoundError(f"JD not found: {jd_path}")

    if verbose:
        log_info(f"[JD_ENGINE] Starting JD parsing → {jd_path.name}")

    result = _parse_single_jd(jd_path, force_reparse=force_reparse)

    if verbose:
        log_info(f"[JD_ENGINE] JD parsing completed for → {jd_path.name}")

    return result