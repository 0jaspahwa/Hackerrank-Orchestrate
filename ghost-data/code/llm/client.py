"""Minimal Groq / Gemini client with a disk cache, a rate limiter and usage accounting.

Keys come only from the environment (or an uncommitted .env at the repo root); they are never
printed, logged, cached or written anywhere. The cache key is a hash of (provider, model,
prompt, image bytes, params), so reruns are free and deterministic.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[2]
CACHE = Path(os.environ["LLM_CACHE_DIR"]) if os.environ.get("LLM_CACHE_DIR") else REPO / ".cache" / "llm"
# committed snapshot of the exact responses behind the final output.csv; used when the live cache lacks an
# entry, unless LLM_NO_SNAPSHOT=1 (main.py --no-snapshot)
SNAPSHOT = Path(__file__).resolve().parent / "cache_snapshot"
USED_CACHE: list[str] = []      # cache file names read or written by this process
GROQ = "https://api.groq.com/openai/v1"
GEMINI = "https://generativelanguage.googleapis.com/v1beta"


def load_env() -> None:
    p = REPO / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def key(provider: str) -> str:
    load_env()
    k = os.environ.get({"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY"}[provider], "")
    if not k:
        raise RuntimeError(f"{provider} API key not set in the environment")
    return k


@dataclass
class Limit:
    rpm: float                 # requests per minute we allow ourselves (below the provider's)
    tpm: float = 0             # tokens per minute (0 = unlimited)
    _times: list = field(default_factory=list)
    _tokens: list = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def wait(self, est_tokens: int = 0) -> None:
        if self.tpm:
            est_tokens = min(est_tokens, int(self.tpm * 0.9))   # one request must always fit
        with self._lock:
            while True:
                now = time.time()
                self._times = [t for t in self._times if now - t < 60]
                self._tokens = [(t, n) for t, n in self._tokens if now - t < 60]
                used = sum(n for _, n in self._tokens)
                if len(self._times) < self.rpm and (not self.tpm or used + est_tokens <= self.tpm):
                    self._times.append(now)
                    self._tokens.append((now, est_tokens))
                    return
                time.sleep(0.5)


# set safely below the limits observed in code/llm/smoke_test.py (docs/llm_limits.md)
LIMITS: dict[str, Limit] = {}


def limit_for(provider: str, model: str) -> Limit:
    from .models import RATE
    k = f"{provider}:{model}"
    if k not in LIMITS:
        rpm, tpm = RATE.get(k, (10, 0))
        LIMITS[k] = Limit(rpm, tpm)
    return LIMITS[k]


@dataclass
class Result:
    text: str
    input_tokens: int
    output_tokens: int
    latency_s: float
    cached: bool
    provider: str
    model: str
    headers: dict = field(default_factory=dict)

    def json(self):
        t = self.text.strip()
        if t.startswith("```"):
            t = t.strip("`")
            t = t[t.find("\n") + 1:] if "\n" in t else t
        return json.loads(t[t.find("{"): t.rfind("}") + 1] if "{" in t else t)


STRICT = os.environ.get("LLM_STRICT") == "1"     # set by main.py --strict: no fallback, abort on first failure


class StrictAbort(RuntimeError):
    pass


USAGE: list[dict] = []          # every call (cached or not) of this process, for usage_report.md
EVENTS: list[dict] = []         # every HTTP attempt: status, retries, waits (never headers with secrets)


def _event(provider, model, purpose, attempt, outcome, wait=0.0, latency=0.0):
    e = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "provider": provider, "model": model,
         "purpose": purpose, "attempt": attempt, "outcome": outcome, "retry_wait_s": round(wait, 1),
         "latency_s": round(latency, 2)}
    EVENTS.append(e)
    p = REPO / "runs" / "llm_events.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(e) + "\n")


def _cache_path(provider, model, system, prompt, image_bytes, params) -> Path:
    h = hashlib.sha256()
    for part in (provider, model, system, prompt, json.dumps(params, sort_keys=True)):
        h.update(part.encode("utf-8"))
        h.update(b"\0")
    if image_bytes:
        h.update(hashlib.sha256(image_bytes).digest())
    return CACHE / f"{provider}_{model.replace('/', '_')}_{h.hexdigest()[:24]}.json"


def complete(provider: str, model: str, prompt: str, *, system: str = "", image: Path | None = None,
             json_mode: bool = True, max_tokens: int = 1024, temperature: float = 0.0,
             use_cache: bool = True, timeout: int = 90, retries: int = 7, purpose: str = "",
             extra: dict | None = None) -> Result:
    image_bytes = image.read_bytes() if image else b""
    params = {"json": json_mode, "max_tokens": max_tokens, "temperature": temperature}
    if extra:
        params["extra"] = extra                     # provider-specific request options (part of the cache key)
    cp = _cache_path(provider, model, system, prompt, image_bytes, params)
    if use_cache and not cp.exists() and os.environ.get("LLM_NO_SNAPSHOT") != "1" and (SNAPSHOT / cp.name).exists():
        cp = SNAPSHOT / cp.name
    if use_cache and cp.exists():
        USED_CACHE.append(cp.name)
        d = json.loads(cp.read_text(encoding="utf-8"))
        r = Result(d["text"], d["input_tokens"], d["output_tokens"], d["latency_s"], True, provider, model)
        USAGE.append({"provider": provider, "model": model, "purpose": purpose, "cached": True,
                      "input_tokens": r.input_tokens, "output_tokens": r.output_tokens})
        return r
    if os.environ.get("LLM_OFFLINE") == "1":       # tests / no-network runs: cache or snapshot only
        raise RuntimeError(f"offline: no cached response for {provider}:{model}")
    est = len(prompt) // 3 + (1900 if image_bytes else 0) + min(max_tokens, 2000)   # typical, not worst case
    last_err = None
    for attempt in range(retries):
        limit_for(provider, model).wait(est)
        t0 = time.time()
        try:
            if provider == "groq":
                r = _groq(model, system, prompt, image_bytes, image, params, timeout)
            else:
                r = _gemini(model, system, prompt, image_bytes, image, params, timeout)
        except _Retry as e:
            last_err = e
            w = min(60, max(e.wait, 2 ** attempt * 3) + 1)      # honour retry-after, back off to ~60 s
            _event(provider, model, purpose, attempt + 1, str(e), w, time.time() - t0)
            time.sleep(w)
            continue
        except requests.RequestException as e:          # timeouts / connection errors: retry
            last_err = e
            w = 2 ** attempt * 3
            _event(provider, model, purpose, attempt + 1, type(e).__name__, w, time.time() - t0)
            time.sleep(w)
            continue
        except Exception as e:
            _event(provider, model, purpose, attempt + 1, f"error: {str(e)[:90]}", 0, time.time() - t0)
            raise
        r.latency_s = round(time.time() - t0, 3)
        _event(provider, model, purpose, attempt + 1, "ok", 0, r.latency_s)
        CACHE.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps({"text": r.text, "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
                                  "latency_s": r.latency_s, "provider": provider, "model": model},
                                 ensure_ascii=False), encoding="utf-8")
        USAGE.append({"provider": provider, "model": model, "purpose": purpose, "cached": False,
                      "input_tokens": r.input_tokens, "output_tokens": r.output_tokens})
        return r
    raise RuntimeError(f"{provider}:{model} failed after {retries} attempts: {last_err}")


class QuotaExhausted(RuntimeError):
    pass


class _Retry(Exception):
    def __init__(self, msg, wait=0.0):
        super().__init__(msg)
        self.wait = wait


def _mime(image: Path | None) -> str:
    return "image/png" if image and image.suffix.lower() == ".png" else "image/jpeg"


def _groq(model, system, prompt, image_bytes, image, params, timeout) -> Result:
    content = prompt
    if image_bytes:
        b64 = base64.b64encode(image_bytes).decode()
        content = [{"type": "text", "text": prompt},
                   {"type": "image_url", "image_url": {"url": f"data:{_mime(image)};base64,{b64}"}}]
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
    body = {"model": model, "messages": msgs, "temperature": params["temperature"], "max_tokens": params["max_tokens"]}
    body.update(params.get("extra") or {})
    if params["json"]:
        body["response_format"] = {"type": "json_object"}
    resp = requests.post(f"{GROQ}/chat/completions", json=body, timeout=timeout,
                         headers={"Authorization": f"Bearer {key('groq')}"})
    hdr = {k: v for k, v in resp.headers.items() if k.lower().startswith(("x-ratelimit", "retry-after"))}
    if resp.status_code in (429, 500, 502, 503):
        try:                                         # keep the limit name for diagnosis (no secrets in it)
            why = str(resp.json().get("error", {}).get("message", ""))[:160]
        except ValueError:
            why = ""
        raise _Retry(f"HTTP {resp.status_code} {why}".strip(), float(resp.headers.get("retry-after", 0) or 0))
    if resp.status_code != 200:
        raise RuntimeError(f"groq HTTP {resp.status_code}: {resp.text[:300]}")
    d = resp.json()
    if d["choices"][0].get("finish_reason") == "length":
        raise RuntimeError("groq answer truncated at max_tokens (not cached)")
    u = d.get("usage", {})
    return Result(d["choices"][0]["message"]["content"] or "", u.get("prompt_tokens", 0),
                  u.get("completion_tokens", 0), 0.0, False, "groq", model, hdr)


def _gemini(model, system, prompt, image_bytes, image, params, timeout) -> Result:
    parts = [{"text": prompt}]
    if image_bytes:
        parts.append({"inline_data": {"mime_type": _mime(image), "data": base64.b64encode(image_bytes).decode()}})
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": params["temperature"], "maxOutputTokens": params["max_tokens"]}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if params["json"]:
        body["generationConfig"]["responseMimeType"] = "application/json"
    resp = requests.post(f"{GEMINI}/models/{model}:generateContent", json=body, timeout=timeout,
                         headers={"x-goog-api-key": key("gemini")})
    hdr = {k: v for k, v in resp.headers.items() if "ratelimit" in k.lower() or k.lower() == "retry-after"}
    if resp.status_code in (429, 500, 502, 503):
        wait, quota = 0.0, ""
        try:
            for det in resp.json().get("error", {}).get("details", []):
                if str(det.get("retryDelay", "")).endswith("s"):
                    wait = float(det["retryDelay"][:-1])
                for v in det.get("violations", []):
                    quota = v.get("quotaId", quota)
        except ValueError:
            pass
        if "PerDay" in quota or wait > 300:         # daily quota: retrying today is pointless
            raise QuotaExhausted(f"HTTP 429 {quota or 'quota'} (retryDelay {wait:.0f}s)")
        raise _Retry(f"HTTP {resp.status_code}", wait)
    if resp.status_code != 200:
        raise RuntimeError(f"gemini HTTP {resp.status_code}: {resp.text[:300]}")
    d = resp.json()
    cand = (d.get("candidates") or [{}])[0]
    if cand.get("finishReason") == "MAX_TOKENS":
        raise RuntimeError("gemini answer truncated at max_tokens (not cached)")
    text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
    u = d.get("usageMetadata", {})
    out = u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
    return Result(text, u.get("promptTokenCount", 0), out, 0.0, False, "gemini", model, hdr)


def list_models(provider: str) -> list[str]:
    if provider == "groq":
        r = requests.get(f"{GROQ}/models", headers={"Authorization": f"Bearer {key('groq')}"}, timeout=30)
        r.raise_for_status()
        return sorted(m["id"] for m in r.json()["data"])
    out, token = [], ""
    while True:
        r = requests.get(f"{GEMINI}/models", params={"pageSize": 200, **({"pageToken": token} if token else {})},
                         headers={"x-goog-api-key": key("gemini")}, timeout=30)
        r.raise_for_status()
        d = r.json()
        out += [m["name"].split("/", 1)[1] for m in d.get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])]
        token = d.get("nextPageToken")
        if not token:
            return sorted(out)
