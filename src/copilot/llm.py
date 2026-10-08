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
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.copilot.config import ROOT, llm_mode, model_name

CACHE_DIR = Path(os.getenv("LLM_CACHE_DIR", str(ROOT / "evals" / "llm_cache")))
_last_call_at = 0.0
_rate_lock = threading.Lock()


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
        self.provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
        if self.mode == "live" and self.provider == "openai":
            self.base_url = os.getenv("LLM_BASE_URL", "http://localhost:1234/v1").rstrip("/")
        elif self.mode == "live":
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
        key_material = json.dumps(_strip({"model": self.model, "provider": getattr(self, "provider", "gemini"), "salt": os.getenv("LLM_CACHE_SALT", ""),
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
        if self.provider == "openai":
            response, latency = self._openai_call(system, contents, tools, response_schema)
        else:
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
            with _rate_lock:
                wait = min_interval - (time.monotonic() - _last_call_at)
                if wait > 0:
                    time.sleep(wait)
                _last_call_at = time.monotonic()
            start = time.perf_counter()
            try:
                response = self._client.models.generate_content(model=self.model, contents=contents, config=config)
                return response, (time.perf_counter() - start) * 1000
            except Exception as exc:  # google.genai.errors.APIError and network errors
                last_exc = exc
                text = str(exc)
                code = getattr(exc, "code", None)
                retryable = code in (429, 500, 502, 503, 504) or "RESOURCE_EXHAUSTED" in text or "UNAVAILABLE" in text or "timed out" in text.lower() or "timeout" in type(exc).__name__.lower()
                self.stats.log.append({"label": "retry", "error": text[:120]})
                if "PerDay" in text or "per day" in text.lower():
                    retryable = False  # daily quota: retrying only wastes time
                if not retryable or attempt == 5:
                    break
                m = re.search(r"retry(?:Delay| in)[\"': ]+([0-9.]+)s", text)
                time.sleep(min(float(m.group(1)) + 1 if m else 3 * (attempt + 1), 65))
        raise LLMUnavailable(f"{type(last_exc).__name__}: {str(last_exc)[:300]}")


    # ------------------------------------------------------------ OpenAI-compatible (LM Studio, vLLM, Ollama)
    def _openai_call(self, system: str, contents: list, tools: list | None, schema: dict | None):
        """Translate the Gemini-typed conversation to the OpenAI chat format and back,
        so agents.py stays provider-agnostic."""
        import requests
        from google.genai import types
        # Thinking models (Qwen3.x) spend most of their time reasoning and LM Studio may
        # return the answer outside `content`. LLM_NO_THINK=1 (default for this provider)
        # adds Qwen's documented /no_think switch.
        no_think = os.getenv("LLM_NO_THINK", "1") == "1"
        messages: list[dict[str, Any]] = [{"role": "system", "content": system + ("\n/no_think" if no_think else "")}]
        pending: list[tuple[str, str]] = []
        n = 0
        for content in contents:
            role = "assistant" if content.role == "model" else "user"
            texts, calls = [], []
            for part in content.parts or []:
                if part.function_call is not None:
                    n += 1
                    cid = f"call_{n}"
                    pending.append((part.function_call.name, cid))
                    calls.append({"id": cid, "type": "function", "function": {
                        "name": part.function_call.name, "arguments": json.dumps(dict(part.function_call.args or {}))}})
                elif part.function_response is not None:
                    name = part.function_response.name
                    idx = next((i for i, (nm, _) in enumerate(pending) if nm == name), 0 if pending else None)
                    cid = pending.pop(idx)[1] if idx is not None else f"call_orphan_{name}"
                    messages.append({"role": "tool", "tool_call_id": cid,
                                     "content": json.dumps(part.function_response.response, default=str)})
                elif part.text:
                    texts.append(part.text)
            if calls:
                messages.append({"role": "assistant", "content": "\n".join(texts) or None, "tool_calls": calls})
            elif texts:
                messages.append({"role": role, "content": "\n".join(texts)})
        if no_think and messages[-1]["role"] == "user" and isinstance(messages[-1].get("content"), str):
            messages[-1]["content"] += "\n/no_think"
        body: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0}
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": d.name, "description": d.description,
                                                              "parameters": d.parameters_json_schema}}
                             for t in tools for d in (t.function_declarations or [])]
        if schema:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "output", "schema": schema}}
        timeout = float(os.getenv("LLM_TIMEOUT_S", "300"))
        last = ""
        for attempt in range(3):
            start = time.perf_counter()
            try:
                r = requests.post(f"{self.base_url}/chat/completions", json=body, timeout=timeout)
                if r.status_code >= 500 or r.status_code == 429:
                    last = f"HTTP {r.status_code}: {r.text[:200]}"
                    time.sleep(2 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    raise LLMUnavailable(f"HTTP {r.status_code}: {r.text[:300]}")
                payload = r.json()
                latency = (time.perf_counter() - start) * 1000
                break
            except requests.RequestException as exc:
                last = f"{type(exc).__name__}: {exc}"
                time.sleep(2 * (attempt + 1))
        else:
            raise LLMUnavailable(f"OpenAI-compatible server failed: {last}")
        msg = payload["choices"][0]["message"]
        debug = Path(os.getenv("LLM_DEBUG_DIR", str(ROOT / "runs")))
        debug.mkdir(exist_ok=True)
        (debug / "last_openai_message.json").write_text(json.dumps(msg, indent=1)[:20000], encoding="utf-8")
        parts = []
        for tc in msg.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_unparseable_arguments": tc["function"].get("arguments")}
            parts.append(types.Part(function_call=types.FunctionCall(name=tc["function"]["name"], args=args)))
        if not parts:
            text = re.sub(r"<think>.*?(</think>|$)", "", msg.get("content") or "", flags=re.S).strip()
            if not text:
                # Some servers put the whole answer in the reasoning field.
                reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
                if isinstance(reasoning, dict):
                    reasoning = json.dumps(reasoning)
                text = _last_json_object(str(reasoning)) or ""
            parts.append(types.Part.from_text(text=text))
        usage = payload.get("usage") or {}
        response = types.GenerateContentResponse(
            candidates=[types.Candidate(content=types.Content(role="model", parts=parts or [types.Part.from_text(text="")]))],
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=usage.get("prompt_tokens"), candidates_token_count=usage.get("completion_tokens")))
        return response, latency


def response_text(response) -> str:
    try:
        return response.text or ""
    except Exception:
        return ""


def _last_json_object(text: str) -> str | None:
    """Return the last balanced {...} block in text, if any."""
    end = text.rfind("}")
    while end != -1:
        depth = 0
        for i in range(end, -1, -1):
            if text[i] == "}":
                depth += 1
            elif text[i] == "{":
                depth -= 1
                if depth == 0:
                    candidate = text[i:end + 1]
                    try:
                        json.loads(candidate)
                        return candidate
                    except json.JSONDecodeError:
                        break
        end = text.rfind("}", 0, end)
    return None


def parse_json(text: str) -> dict:
    text = re.sub(r"<think>.*?(</think>|$)", "", text.strip(), flags=re.S).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        block = _last_json_object(text)
        if block is None:
            raise
        return json.loads(block)
