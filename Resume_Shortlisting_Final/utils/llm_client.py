# utils/llm_client.py

import os
import requests
from pathlib import Path
from utils.logger import log_error, log_info
from utils.json_tools import extract_json

# Load .env from project root if not already set
def _load_env():
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists() and not os.getenv("CLOUDFLARE_ACCOUNT_ID"):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

_load_env()


class LLMClient:
    """
    Cloudflare Workers AI unified client.
    """
    def __init__(self, model: str, max_tokens: int = 2048):
        self.account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
        self.token = os.getenv("CLOUDFLARE_API_TOKEN", "")
        self.model = model
        self.max_tokens = max_tokens

        if not self.account or not self.token:
            log_error("Cloudflare credentials missing in .env")

        self.url = f"https://api.cloudflare.com/client/v4/accounts/{self.account}/ai/run/{self.model}"
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

    def run(self, messages, *, debug_dir: Path = None):
        payload = {
            "messages": messages,
            "max_tokens": self.max_tokens
        }

        # Save request
        if debug_dir:
            debug_dir.mkdir(parents=True, exist_ok=True)
            (debug_dir / "last_request.json").write_text(str(payload), encoding="utf-8")

        for attempt in range(3):
            try:
                resp = requests.post(self.url, headers=self.headers, json=payload, timeout=120)
                data = resp.json()

                if not data.get("success", False):
                    raise RuntimeError(f"Cloudflare error: {data}")

                # Save response
                if debug_dir:
                    (debug_dir / "last_response.json").write_text(str(data), encoding="utf-8")

                result = data.get("result", {})
                text = result.get("response") or result.get("output") or result.get("text") or ""

                return extract_json(text)

            except Exception as e:
                log_error(f"[LLM ERROR] attempt {attempt+1}: {e}")

        return {}