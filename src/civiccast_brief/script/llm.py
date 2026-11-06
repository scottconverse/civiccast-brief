"""Streamed chat with the writer model through local Ollama (standard library only).

Streaming keeps bytes moving so long answers do not hit the proxy timeout that a single
non-streamed request hits. The same settings as the bake-off helper ``ds_stream32.py``:
DeepSeek v4.1 flash, thinking at low effort, a 32000-token output cap.
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass

DEFAULT_URL = "http://127.0.0.1:11434/api/chat"
DEFAULT_MODEL = "deepseek-v4.1-flash:1017-cloud"
DEFAULT_EFFORT = "low"
DEFAULT_OUTPUT_CAP = 32_000
DEFAULT_CONTEXT = 131_072
READ_TIMEOUT_S = 300
TOTAL_TIMEOUT_S = 1800


class ModelError(RuntimeError):
    """The model call failed or returned nothing usable."""


class ModelCutOff(ModelError):
    """The answer hit the output cap and is incomplete."""


@dataclass(frozen=True)
class Receipt:
    prompt_tokens: int
    output_tokens: int
    seconds: float
    done_reason: str

    def line(self) -> str:
        return (
            f"[receipt] in={self.prompt_tokens} out={self.output_tokens} "
            f"effort={DEFAULT_EFFORT} secs={self.seconds:.1f} done_reason={self.done_reason}"
        )


def read_stream(
    lines: Iterable[bytes], *, started: float, total_timeout: float = TOTAL_TIMEOUT_S
) -> tuple[str, Receipt]:
    """Join the streamed NDJSON chunks of one chat call into the answer text."""
    parts: list[str] = []
    final: dict = {}
    for raw in lines:
        if time.monotonic() - started > total_timeout:
            raise ModelError(f"model call ran over {total_timeout:.0f}s")
        line = raw.strip()
        if not line:
            continue
        message = json.loads(line)
        if "error" in message:
            raise ModelError(str(message["error"]))
        parts.append(message.get("message", {}).get("content", ""))
        if message.get("done"):
            final = message
    text = "".join(parts)
    receipt = Receipt(
        prompt_tokens=int(final.get("prompt_eval_count", 0)),
        output_tokens=int(final.get("eval_count", 0)),
        seconds=time.monotonic() - started,
        done_reason=str(final.get("done_reason", "none")),
    )
    if not text.strip():
        raise ModelError("model answer was empty")
    if receipt.done_reason == "length":
        raise ModelCutOff("model answer hit the output cap")
    return text, receipt


def chat(
    prompt: str,
    *,
    url: str = DEFAULT_URL,
    model: str = DEFAULT_MODEL,
    output_cap: int = DEFAULT_OUTPUT_CAP,
    context: int = DEFAULT_CONTEXT,
) -> tuple[str, Receipt]:
    """Send one user message to the model and return (answer text, receipt)."""
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "think": DEFAULT_EFFORT,
            "options": {"num_predict": output_cap, "num_ctx": context},
        }
    ).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=READ_TIMEOUT_S) as response:
            return read_stream(response, started=started)
    except (OSError, ValueError) as exc:
        raise ModelError(f"model call failed: {exc}") from exc
