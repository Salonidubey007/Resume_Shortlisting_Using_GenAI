# utils/json_tools.py

import json
import re
from utils.logger import log_warn


def sanitize(text: str) -> str:
    if not text:
        return ""

    t = str(text).strip()

    t = t.replace("```json", "").replace("```", "")
    t = t.replace("\u200b", "").replace("\ufeff", "")

    # remove control chars
    t = re.sub(r"[\x00-\x1f]", "", t)
    return t


def extract_json(text: str):
    """
    Extract a JSON object out of dirty LLM output.
    Stronger and safer.
    """
    cleaned = sanitize(text)

    # Try direct parse
    try:
        return json.loads(cleaned)
    except:
        pass

    # Try brace-walk
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1:
        snippet = cleaned[start:end+1]
        try:
            return json.loads(snippet)
        except:
            log_warn("[JSON] Brace-walk failed")

    # Last fallback — empty dict
    log_warn("[JSON] Failed. Returning {}")
    return {}