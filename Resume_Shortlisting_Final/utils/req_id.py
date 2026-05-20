# utils/req_id.py

import json
import random
import string
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_FILE = PROJECT_ROOT / "storage" / "req_id_registry.json"

_CHARS = string.ascii_uppercase + string.digits  # A-Z 0-9


def _load_registry() -> dict:
    """Returns { jd_source_filename: req_id }"""
    if not REGISTRY_FILE.exists():
        return {}
    try:
        return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_registry(registry: dict):
    REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_FILE.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")


def _generate(existing_ids: set) -> str:
    """Generate a unique 7-char alphanumeric ID like HYA667W."""
    while True:
        req_id = "".join(random.choices(_CHARS, k=7))
        if req_id not in existing_ids:
            return req_id


def get_or_create_req_id(jd_source_filename: str) -> str:
    """
    Returns existing req_id for this JD filename, or generates + persists a new one.
    """
    registry = _load_registry()
    if jd_source_filename in registry:
        return registry[jd_source_filename]

    existing_ids = set(registry.values())
    req_id = _generate(existing_ids)
    registry[jd_source_filename] = req_id
    _save_registry(registry)
    return req_id


def get_req_id(jd_source_filename: str) -> str | None:
    """Returns req_id if already assigned, else None."""
    return _load_registry().get(jd_source_filename)


def all_req_ids() -> dict:
    """Returns full registry { filename: req_id }."""
    return _load_registry()
