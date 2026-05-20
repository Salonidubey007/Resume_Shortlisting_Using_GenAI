# utils/parallel.py

from concurrent.futures import ThreadPoolExecutor, as_completed
from utils.logger import log_info, log_warn
import time
import threading

class WorkerPool:
    """
    Wrapper around ThreadPoolExecutor to safely run parallel tasks.
    Logs progress, supports throttling.
    """
    def __init__(self, max_workers=6):
        self.max_workers = max_workers
        self._lock = threading.Lock()

    def map(self, fn, items, *, desc="Processing", log_every=5):
        results = []
        total = len(items)
        completed = 0

        if total == 0:
            return []

        log_info(f"[WorkerPool] Starting {desc}: {total} items with {self.max_workers} workers")

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            future_map = { pool.submit(fn, item): item for item in items }

            for i, future in enumerate(as_completed(future_map)):
                item = future_map[future]
                try:
                    res = future.result()
                    results.append(res)
                except Exception as e:
                    log_warn(f"[WorkerPool] Error processing item {item}: {e}")

                completed += 1
                if completed % log_every == 0 or completed == total:
                    remaining = total - completed
                    log_info(f"[WorkerPool] {completed}/{total} done — remaining={remaining}")

        log_info(f"[WorkerPool] Completed {desc}.")
        return results