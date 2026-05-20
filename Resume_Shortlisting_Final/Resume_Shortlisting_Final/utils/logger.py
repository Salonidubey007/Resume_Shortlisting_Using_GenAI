# utils/logger.py

import datetime
import sys

RESET = "\033[0m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"

def _timestamp():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log_info(*msg):
    print(f"{GREEN}[INFO { _timestamp() }]{RESET}", *msg)
    sys.stdout.flush()

def log_warn(*msg):
    print(f"{YELLOW}[WARN { _timestamp() }]{RESET}", *msg)
    sys.stdout.flush()

def log_error(*msg):
    print(f"{RED}[ERROR { _timestamp() }]{RESET}", *msg)
    sys.stdout.flush()

def log(*msg):
    print(f"{CYAN}[LOG { _timestamp() }]{RESET}", *msg)
    sys.stdout.flush()