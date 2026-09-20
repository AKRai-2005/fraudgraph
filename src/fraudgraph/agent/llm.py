"""LLM layer: narrative rewriting and investigation planning.

What the LLM is allowed to do
-----------------------------
* propose *additional* graph queries from the catalogue (its proposals are
  validated against the catalogue and executed by the deterministic store);
* rewrite the case summary and the SAR narrative for readability.

What it is not allowed to do
----------------------------
* set the verdict, the probability, the pattern, the exposure, the actions, the
  approval routes or the SAR decision -- all of those are computed before it is
  called, and its rewrites are rejected if they introduce an identifier, amount
  or date that is not already in the retrieved evidence
  (see ``fraudgraph.agent.narrative._validate``);
* skip the mandatory baseline retrieval. Its planning runs *after* that, and
  can only add.

With no API key configured the system runs end to end on deterministic
templates; the answer files record ``tokens: 0`` honestly in that case.
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

import httpx

from ..config import LLM
from ..graph.queries import CATALOGUE

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

SYSTEM = (
    "You are the writing and planning assistant inside a bank's fraud investigation agent. "
    "The investigation's findings, probability, pattern and recommended actions have already "
    "been determined by deterministic graph analysis and a policy engine. You never change "
    "them. You never introduce a transaction id, card id, case id, amount or date that is not "
    "present in the material you are given. You write in plain, precise, unexcited English."
)


class _Provider:
    """Base provider with the free-tier pacing every hosted model needs.

    Gemini's free tier is rate limited per minute and per day. Without pacing a
    20-case run burns through the per-minute allowance in the first few cases
    and every later call returns 429, which the narrator silently absorbs as a
    template fallback -- the run still completes, but half the narratives are
    templates and nothing says so. So calls are spaced, 429s are retried with
    backoff, and ``rate_limited`` records how many gave up, which the benchmark
    reports.
    """

    #: minimum seconds between calls; free tiers are typically 10-15 RPM
    min_interval_s = float(LLM.min_interval_s)
    max_retries = 3

    def __init__(self) -> None:
        self.tokens_used = 0
        self.calls = 0
        self.rate_limited = 0
        self.errors: list[str] = []
        self._lock = threading.Lock()
        self._last_call = 0.0

    def _pace(self) -> None:
        with self._lock:
            wait = self.min_interval_s - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    def complete(self, prompt: str, max_tokens: int | None = None) -> str:
        raise NotImplementedError

    def _redact(self, text: str) -> str:
        """Never let the API key reach a log, a traceback or the dashboard."""
        key = getattr(self, "api_key", "")
        return str(text).replace(key, "<REDACTED>") if key else str(text)


class GeminiProvider(_Provider):
    enabled = True

    def __init__(self, model: str, api_key: str) -> None:
        super().__init__()
        self.model, self.api_key = model, api_key

    def complete(self, prompt: str, max_tokens: int | None = None) -> str:
        for attempt in range(self.max_retries):
            try:
                return self._call(prompt, max_tokens)
            except _RateLimited as exc:
                if attempt == self.max_retries - 1:
                    self.rate_limited += 1
                    raise RuntimeError(str(exc)) from None
                time.sleep(exc.retry_after or (4 * (attempt + 1)))
        return ""

    def _call(self, prompt: str, max_tokens: int | None = None) -> str:
        self._pace()
        url = GEMINI_URL.format(model=self.model)
        body = {
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": LLM.temperature,
                "maxOutputTokens": int(max_tokens or LLM.max_output_tokens),
            },
        }
        # The key goes in a header, not the query string: a 4xx from httpx
        # embeds the request URL in the exception message, which would put the
        # key into every traceback and log line.
        r = httpx.post(
            url, json=body, timeout=LLM.timeout_s,
            headers={"content-type": "application/json", "x-goog-api-key": self.api_key},
        )
        if r.status_code == 429:
            raise _RateLimited(
                f"Gemini rate limit / quota exhausted for {self.model}",
                _retry_after(r),
            )
        if r.status_code >= 400:
            raise RuntimeError(
                f"Gemini {r.status_code} for model {self.model}: "
                f"{self._redact(r.text)[:300]}"
            )
        data = r.json()
        self.calls += 1
        usage = data.get("usageMetadata") or {}
        self.tokens_used += int(usage.get("totalTokenCount") or 0)
        cands = data.get("candidates") or []
        if not cands:
            return ""
        parts = (cands[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts).strip()


class AnthropicProvider(_Provider):
    enabled = True

    def __init__(self, model: str, api_key: str) -> None:
        super().__init__()
        self.model, self.api_key = model, api_key

    def complete(self, prompt: str, max_tokens: int | None = None) -> str:
        r = httpx.post(
            ANTHROPIC_URL,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": int(max_tokens or LLM.max_output_tokens),
                "temperature": LLM.temperature,
                "system": SYSTEM,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=LLM.timeout_s,
        )
        if r.status_code >= 400:
            raise RuntimeError(
                f"Anthropic {r.status_code} for model {self.model}: "
                f"{self._redact(r.text)[:300]}"
            )
        data = r.json()
        self.calls += 1
        u = data.get("usage") or {}
        self.tokens_used += int(u.get("input_tokens", 0)) + int(u.get("output_tokens", 0))
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()


class _RateLimited(Exception):
    def __init__(self, msg: str, retry_after: float | None = None):
        super().__init__(msg)
        self.retry_after = retry_after


def _retry_after(r) -> float | None:
    """Honour Retry-After, and the RetryInfo Google returns in the body."""
    hdr = r.headers.get("retry-after")
    if hdr:
        try:
            return float(hdr)
        except ValueError:
            pass
    try:
        for d in (r.json().get("error") or {}).get("details") or []:
            delay = d.get("retryDelay")
            if isinstance(delay, str) and delay.endswith("s"):
                return float(delay[:-1])
    except Exception:  # noqa: BLE001
        pass
    return None


class NullProvider(_Provider):
    """No credentials: everything falls back to the deterministic templates."""

    enabled = False

    def complete(self, prompt: str, max_tokens: int | None = None) -> str:
        return ""


def build_provider() -> _Provider:
    if not LLM.configured:
        return NullProvider()
    if LLM.provider == "gemini":
        return GeminiProvider(LLM.model, LLM.api_key)
    if LLM.provider == "anthropic":
        return AnthropicProvider(LLM.model, LLM.api_key)
    return NullProvider()


# --------------------------------------------------------------------------


class Narrator:
    """Rewrites text and plans extra retrieval. Failures degrade to templates."""

    def __init__(self, provider: _Provider | None = None):
        self.provider = provider or build_provider()

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.provider, "enabled", False))

    @property
    def tokens_used(self) -> int:
        return self.provider.tokens_used

    @property
    def stats(self) -> dict:
        p = self.provider
        return {
            "enabled": self.enabled,
            "calls_succeeded": p.calls,
            "calls_rate_limited": p.rate_limited,
            "tokens": p.tokens_used,
            "errors": p.errors[-3:],
        }

    # ------------------------------------------------------------- writing
    def _evidence_block(self, answer, feats) -> str:
        lines = [
            f"Flagged transaction: {feats.txn_id} on card {feats.card_id} "
            f"(customer {feats.customer_id}), ${feats.amount:,.2f}, {feats.channel}, {feats.ts}.",
            f"Verdict (already decided): {answer.case.verdict.value}, "
            f"probability {answer.case.fraud_probability:.2f}, pattern {answer.case.pattern.value}, "
            f"exposure ${answer.case.exposure_usd:,.2f}.",
            "Evidence retrieved:",
        ]
        lines += [f"  - {e.claim}" for e in answer.case.evidence]
        lines.append("Recommended actions: " + ", ".join(
            f"{a.action.value} ({a.route.value})" for a in answer.next_best_actions.final
        ))
        return "\n".join(lines)

    def rewrite_summary(self, base: str, answer, feats, risk) -> str:
        if not self.enabled:
            return ""
        prompt = (
            f"{self._evidence_block(answer, feats)}\n\n"
            "A draft summary for a fraud analyst follows. Rewrite it in two to five sentences so "
            "it reads naturally, keeping every factual claim, number and identifier exactly as "
            "given and adding none. Do not add a preamble. Return only the rewritten summary.\n\n"
            f"DRAFT:\n{base}"
        )
        try:
            return self.provider.complete(prompt, max_tokens=520)
        except Exception as exc:  # noqa: BLE001
            self.provider.errors.append(
                self.provider._redact(f"summary: {type(exc).__name__}: {exc}")[:300]
            )
            return ""

    def rewrite_sar(self, base: str, answer, feats, risk) -> str:
        if not self.enabled:
            return ""
        prompt = (
            f"{self._evidence_block(answer, feats)}\n\n"
            "A draft suspicious activity report narrative follows. Rewrite it as six to twelve "
            "sentences of continuous prose for a regulator, covering who, what, when, where, how "
            "and why it is suspicious. It must stand alone. Keep every identifier, amount and "
            "date exactly as given and introduce none. Keep the statement that any customer "
            "response was simulated. Return only the narrative.\n\n"
            f"DRAFT:\n{base}"
        )
        try:
            return self.provider.complete(prompt, max_tokens=1100)
        except Exception as exc:  # noqa: BLE001
            self.provider.errors.append(
                self.provider._redact(f"sar: {type(exc).__name__}: {exc}")[:300]
            )
            return ""

    # ------------------------------------------------------------ planning
    def plan_extra_queries(
        self, context_summary: str, already_run: list[str], max_new: int = 3
    ) -> list[dict]:
        """Ask the model which further catalogue queries are worth running.

        Returns validated proposals only: unknown query names, unknown
        parameters and already-executed calls are dropped.
        """
        if not self.enabled:
            return []
        catalogue = "\n".join(
            f"  {q.name}({', '.join(q.params)}"
            + (f"[, {', '.join(q.optional_params)}]" if q.optional_params else "")
            + f") -- {q.purpose}"
            for q in CATALOGUE.values()
            if q.name not in ("write_case", "read_cases")
        )
        prompt = (
            "An investigation has gathered the baseline evidence below. Decide whether any "
            "further graph queries would change the assessment, and if so which.\n\n"
            f"CURRENT STATE:\n{context_summary}\n\n"
            f"ALREADY RUN: {', '.join(already_run) or 'none'}\n\n"
            f"AVAILABLE QUERIES:\n{catalogue}\n\n"
            f"Reply with JSON only: a list of at most {max_new} objects, each "
            '{"name": "<query>", "params": {...}, "why": "<one sentence>"}. '
            "Return [] if the baseline is sufficient. Do not repeat a query already run. "
            "Prefer queries that could change the verdict rather than confirm it."
        )
        try:
            raw = self.provider.complete(prompt, max_tokens=700)
        except Exception as exc:  # noqa: BLE001
            self.provider.errors.append(
                self.provider._redact(f"plan: {type(exc).__name__}: {exc}")[:300]
            )
            return []
        return self._validate_plan(raw, already_run, max_new)

    @staticmethod
    def _validate_plan(raw: str, already_run: list[str], max_new: int) -> list[dict]:
        if not raw:
            return []
        m = re.search(r"\[.*\]", raw, re.S)
        if not m:
            return []
        try:
            proposals = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
        out: list[dict] = []
        seen = set(already_run)
        for p in proposals if isinstance(proposals, list) else []:
            if not isinstance(p, dict):
                continue
            name = str(p.get("name", ""))
            spec = CATALOGUE.get(name)
            if spec is None or name in ("write_case", "read_cases"):
                continue
            params = p.get("params")
            if not isinstance(params, dict):
                continue
            allowed = set(spec.params) | set(spec.optional_params)
            params = {k: v for k, v in params.items() if k in allowed}
            if not set(spec.params).issubset(params):
                continue
            key = f"{name}:{sorted(params.items())}"
            if key in seen:
                continue
            seen.add(key)
            out.append({"name": name, "params": params, "why": str(p.get("why", ""))[:300]})
            if len(out) >= max_new:
                break
        return out


def build_narrator() -> Narrator:
    return Narrator()
