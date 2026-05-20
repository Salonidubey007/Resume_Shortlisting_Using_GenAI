# utils/jd_duplicate.py

import hashlib
import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STORAGE_DIR = PROJECT_ROOT / "storage"
JD_MASTER = STORAGE_DIR / "jds_master.json"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation/whitespace for fuzzy comparison."""
    t = text.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _similarity(a: str, b: str) -> float:
    """
    Token-based Jaccard similarity between two texts.
    Returns 0.0 to 1.0.
    """
    set_a = set(_normalize(a).split())
    set_b = set(_normalize(b).split())
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def _load_master_entries() -> list:
    if not JD_MASTER.exists():
        return []
    entries = []
    with open(JD_MASTER, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                entries.append(json.loads(ln))
            except Exception:
                continue
    return entries


def check_duplicate(new_text: str, new_filename: str) -> dict:
    """
    Compare incoming JD text against all existing JDs in master.

    Returns one of:
    {
        "status": "exact",
        "req_id": "...",
        "role_name": "...",
        "existing_filename": "..."
    }
    {
        "status": "similar",
        "similarity": 0.91,
        "req_id": "...",
        "role_name": "...",
        "existing_filename": "..."
    }
    {
        "status": "new"
    }
    """
    new_hash = _content_hash(new_text)
    entries = _load_master_entries()

    best_sim = 0.0
    best_entry = None

    for entry in entries:
        existing_filename = (entry.get("Meta") or {}).get("jd_source_filename", "")
        existing_hash = (entry.get("Meta") or {}).get("content_hash", "")
        req_id = entry.get("req_id", "")
        role_name = entry.get("Role_name", "")

        # Same filename: check if content changed
        if existing_filename == new_filename:
            if existing_hash and existing_hash == new_hash:
                # Exact same file re-uploaded
                return {
                    "status": "exact",
                    "req_id": req_id,
                    "role_name": role_name,
                    "existing_filename": existing_filename
                }
            else:
                # Same filename, different content = updated version
                return {
                    "status": "similar",
                    "similarity": 1.0,
                    "req_id": req_id,
                    "role_name": role_name,
                    "existing_filename": existing_filename
                }

        # Different filename: exact hash match
        if existing_hash and existing_hash == new_hash:
            return {
                "status": "exact",
                "req_id": req_id,
                "role_name": role_name,
                "existing_filename": existing_filename
            }

        # Fuzzy similarity check
        existing_text = entry.get("_raw_text", "")
        if existing_text:
            sim = _similarity(new_text, existing_text)
            if sim > best_sim:
                best_sim = sim
                best_entry = {
                    "req_id": req_id,
                    "role_name": role_name,
                    "existing_filename": existing_filename,
                    "similarity": round(sim, 3)
                }

    # 65–80%: grey zone — treat as new (not similar enough to warn)
    # <65%: definitely new
    # >=80%: similar/updated
    if best_entry and best_sim >= 0.80:
        return {
            "status": "similar",
            **best_entry
        }

    return {"status": "new"}
