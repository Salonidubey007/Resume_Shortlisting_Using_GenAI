import json
from pathlib import Path

def load_history(path: Path):
    if not path.exists():
        return {"current_version": {}, "history": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except:
        return {"current_version": {}, "history": []}

def save_history(path: Path, history):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

def append_history(history, old_version):
    if old_version:
        history.setdefault("history", [])
        history["history"].append(old_version)