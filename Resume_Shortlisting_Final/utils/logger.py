# utils/logger.py

import datetime
import sys
import io

RESET = "\033[0m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"

# Ensure stdout handles unicode on Windows (cp1252 terminals)
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

def _timestamp():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _safe_print(*args):
    try:
        print(*args)
        sys.stdout.flush()
    except UnicodeEncodeError:
        safe = ' '.join(str(a).encode('ascii', 'replace').decode() for a in args)
        print(safe)
        sys.stdout.flush()

def log_info(*msg):
    _safe_print(f"{GREEN}[INFO { _timestamp() }]{RESET}", *msg)

def log_warn(*msg):
    _safe_print(f"{YELLOW}[WARN { _timestamp() }]{RESET}", *msg)

def log_error(*msg):
    _safe_print(f"{RED}[ERROR { _timestamp() }]{RESET}", *msg)

def log(*msg):
    _safe_print(f"{CYAN}[LOG { _timestamp() }]{RESET}", *msg)