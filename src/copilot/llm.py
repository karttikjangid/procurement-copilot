"""Thin Gemini adapter with three things the evaluation needs:
- record/replay: every response is stored by a hash of the full request, so the
  eval can be re-run without an API key and gives identical numbers;
- rate-limit handling for the free tier (retry with backoff on 429/5xx);
- call counting and latency per call."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.copilot.config import ROOT, llm_mode, model_name

CACHE_DIR = Path(os.getenv("LLM_CACHE_DIR", str(ROOT / "evals" / "llm_cache")))
_last_call_at = 0.0


class LLMUnavailable(RuntimeError):
    pass


def _strip(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in sorted(obj.items()) if k not in {"thought_signature", "id"} and v is not None}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, list):
        return [_dump(v) for v in value]
    return value


@dataclass
class LLMStats:
    calls: int = 0
    latency_ms: float = 0.0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    log: list[dict[str, Any]] = field(default_factory=list)


class GeminiClient:
    def __init__(self, stats: LLMStats | None = None):
        self.stats = stats or LLMStats()
        self.mode = llm_mode()
        self.model = model_name()
        self._client = None
        if self.mode == "off":
            raise LLMUnavailable("LLM_MODE=off")
        if self.mode == "live":
            key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
            if not key:
                raise LLMUnavailable("GOOGLE_API_KEY is not set")
            from google import genai
            from google.genai import types as _t
            timeout_ms = int(float(os.getenv("LLM_TIMEOUT_S", "90")) * 1000)
            self._client = genai.Client(api_key=key, http_options=_t.HttpOptions(timeout=timeout_ms))

    def generate(self, *, label: str, system: str, contents: list, tools: list | None = None,
                 response_schema: dict | None = None):
        from google.genai import types
        config_kwargs: dict[str, Any] = {"system_instruction": system, "temperature": 0.0}
        if tools:
            config_kwargs["tools"] = tools
            config_kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
        if response_schema:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_json_schema"] = response_schema
        key_material = json.dumps(_strip({"model": self.model, "salt": os.getenv("LLM_CACHE_SALT", ""),
                                          "system": system, "contents": _dump(contents),
                                          "tools": _dump(tools), "schema": response_schema}), sort_keys=True)
        key = hashlib.sha256(key_material.encode()).hexdigest()[:24]
        path = CACHE_DIR / f"{key}.json"
        self.stats.calls += 1
        # LLM_CACHE: "on" = read + write recordings (UI default), "write" = always call the API
        # and overwrite the recording (live eval runs), "off" = neither. Replay mode always reads.
        cache_policy = os.getenv("LLM_CACHE", "on").strip().lower()
        if path.is_file() and (self.mode == "replay" or cache_policy == "on"):
            entry = json.loads(path.read_text(encoding="utf-8"))
            self.stats.cache_hits += 1
            self.stats.latency_ms += entry.get("latency_ms", 0.0)
            self.stats.log.append({"label": label, "cached": True, "latency_ms": entry.get("latency_ms", 0.0)})
            return types.GenerateContentResponse.model_validate(entry["response"])
        if self.mode == "replay":
            raise LLMUnavailable(f"replay mode: no recording for {label} ({key})")
        response, latency = self._call_with_retry(types.GenerateContentConfig(**config_kwargs), contents)
        self.stats.latency_ms += latency
        usage = getattr(response, "usage_metadata", None)
        if usage:
            self.stats.input_tokens += usage.prompt_token_count or 0
            self.stats.output_tokens += usage.candidates_token_count or 0
        self.stats.log.append({"label": label, "cached": False, "latency_ms": round(latency, 1)})
        if cache_policy == "off":
            return response
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"label": label, "model": self.model, "latency_ms": round(latency, 1),
                                    "response": _dump(response)}, indent=1), encoding="utf-8")
        return response

    def _call_with_retry(self, config, contents):
        global _last_call_at
        min_interval = float(os.getenv("LLM_MIN_INTERVAL_S", "0"))
        last_exc: Exception | None = None
        for attempt in range(6):
            wait = min_interval - (time.monotonic() - _last_call_at)
            if wait > 0:
                time.sleep(wait)
            start = time.perf_counter()
            try:
                _last_call_at = time.monotonic()
                response = self._client.models.generate_content(model=self.model, contents=contents, config=config)
                return response, (time.perf_counter() - start) * 1000
            except Exception as exc:  # google.genai.errors.APIError and network errors
                last_exc = exc
                text = str(exc)
                code = getattr(exc, "code", None)
                retryable = code in (429, 500, 502, 503, 504) or "RESOURCE_EXHAUSTED" in text or "UNAVAILABLE" in text or "timed out" in text.lower() or "timeout" in type(exc).__name__.lower()
                self.stats.log.append({"label": "retry", "error": text[:120]})
                if not retryable or attempt == 5:
                    break
                m = re.search(r"retry(?:Delay| in)[\"': ]+([0-9.]+)s", text)
                time.sleep(min(float(m.group(1)) + 1 if m else 3 * (attempt + 1), 65))
        raise LLMUnavailable(f"{type(last_exc).__name__}: {str(last_exc)[:300]}")


def response_text(response) -> str:
    try:
        return response.text or ""
    except Exception:
        return ""


def parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()
    return json.loads(text)
