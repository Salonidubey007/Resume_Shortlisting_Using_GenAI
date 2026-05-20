# utils/text_cleaner.py

import re

def clean_text(text: str) -> str:
    if not text:
        return ""

    t = text
    t = t.replace("\x00", "")
    t = t.replace("\u200b", "")
    t = t.replace("\u00a0", " ")

    # Remove control chars
    t = re.sub(r"[\x00-\x1f\x7f]", "", t)

    # Normalize whitespace
    t = re.sub(r"\s+", " ", t)
    return t.strip()