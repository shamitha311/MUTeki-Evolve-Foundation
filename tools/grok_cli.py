"""tools/grok_cli.py — MUTeki-Evolve Grok CLI Bridge

Implements the CLI interface that Muteki's GrokDriver expects:
  - `grok --version` → prints version string and exits 0
  - `grok -p <prompt>` → queries xAI API with XAI_API_KEY and streams JSON lines:
      {"role": "assistant", "content": "..."}
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


def _get_api_key() -> str:
    """Read xAI API key from env or .env file."""
    key = (
        os.environ.get("XAI_API_KEY")
        or os.environ.get("GROK_API_KEY")
        or os.environ.get("API_KEY")
        or ""
    ).strip()
    if not key and os.path.exists(".env"):
        try:
            with open(".env", "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("XAI_API_KEY="):
                        key = line.split("=", 1)[1].strip().strip("'\"")
                        break
        except Exception:
            pass
    return key


def main() -> int:
    args = sys.argv[1:]

    # Probe check: `grok --version`
    if "--version" in args or "-v" in args:
        print("grok 1.0.0 (MUTeki-Evolve xAI Bridge)")
        return 0

    # Extract prompt: `-p "prompt"` or bare arguments
    prompt = ""
    for i, arg in enumerate(args):
        if arg == "-p" and i + 1 < len(args):
            prompt = args[i + 1]
            break
    if not prompt and args:
        prompt = args[-1]

    if not prompt:
        print("grok 1.0.0 — MUTeki-Evolve Grok CLI Bridge")
        return 0

    api_key = _get_api_key()
    if not api_key:
        err_msg = (
            "[grok-bridge] ERROR: XAI_API_KEY is not set.\n"
            "Please set XAI_API_KEY in your .env file or environment."
        )
        print(err_msg, file=sys.stderr)
        # Emit error event as JSON line so Muteki captures it
        print(json.dumps({"role": "assistant", "content": err_msg}), flush=True)
        return 1

    # xAI API configuration
    model = os.environ.get("MUTEKI_WORKER_MODEL") or "grok-2-latest"
    base_url = (os.environ.get("XAI_BASE_URL") or "https://api.x.ai/v1").rstrip("/")
    url = f"{base_url}/chat/completions"

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are Grok, an autonomous cybersecurity investigation agent. "
                    "Analyze the given target and strategy instructions, identify attack surface, "
                    "reason step-by-step, and report findings."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "MUTeki-Evolve/1.0",
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]

            # Emit assistant response line in Muteki's expected JSON format
            output_evt = {"role": "assistant", "content": content}
            print(json.dumps(output_evt, ensure_ascii=False), flush=True)

            # Emit session hint line so Muteki records session state
            session_evt = {
                "role": "meta",
                "type": "session.resume_hint",
                "session_id": "grok-session-001",
            }
            print(json.dumps(session_evt, ensure_ascii=False), flush=True)
            return 0

    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        err_msg = f"[grok-bridge] xAI API HTTP {exc.code}: {err_body[:300]}"
        print(err_msg, file=sys.stderr)
        print(json.dumps({"role": "assistant", "content": err_msg}), flush=True)
        return 1
    except Exception as exc:
        err_msg = f"[grok-bridge] Request failed: {type(exc).__name__}: {exc}"
        print(err_msg, file=sys.stderr)
        print(json.dumps({"role": "assistant", "content": err_msg}), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
