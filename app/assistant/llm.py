"""Optional LLM layer (Groq) — used ONLY for what rules can't handle.

Design rule: the engine and database are the source of truth. The LLM never
invents order data, dates, or statuses. It is used for exactly two things:

  1. answer_freeform() — questions the rule-based router returns as "unknown".
     The model gets a compact, factual snapshot of the current floor (built by
     the caller) and must answer FROM THAT ONLY.

  2. parse_freeform()  — constraint phrasings the rule parser couldn't match.
     The model maps the sentence onto the same structured constraint the rule
     parser produces.

If GROQ_API_KEY is not set, both functions return None and the caller falls
back to the rule-based result — so the app runs fully offline without a key.
"""
from __future__ import annotations

import json
import os
from typing import Optional

# Groq is OpenAI-API-compatible. We call it over plain HTTPS so the only new
# dependency is `requests` (already common); no SDK required.
import urllib.request
import urllib.error

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Fast, capable, inexpensive. Override with GROQ_MODEL if you like.
DEFAULT_MODEL = "llama-3.3-70b-versatile"


def available() -> bool:
    return bool(os.environ.get("GROQ_API_KEY"))


def _call(messages: list[dict], *, temperature: float = 0.2,
          max_tokens: int = 500, json_mode: bool = False) -> Optional[str]:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return None
    body = {
        "model": os.environ.get("GROQ_MODEL", DEFAULT_MODEL),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        GROQ_URL, data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError):
        return None                      # any failure -> caller falls back


# --------------------------------------------------------------------------
# 1. Free-form Q&A, grounded in a factual snapshot the caller supplies
# --------------------------------------------------------------------------
_ANSWER_SYSTEM = (
    "You are the scheduling assistant for a transmitter factory. Answer using "
    "ONLY the JSON floor snapshot provided. Lead with the direct answer in the "
    "FIRST sentence — the specific order, status, date, or number the user asked "
    "for. Then at most one sentence of context. Never invent orders, dates, "
    "quantities, or statuses not in the snapshot; if it isn't there, say so in "
    "one line. Hard limit: two sentences. Be specific and concrete, not general. "
    "Never mention JSON, snapshots, or that you are an AI."
)


def answer_freeform(question: str, snapshot: dict) -> Optional[str]:
    """Answer a question the rules couldn't route. `snapshot` is a compact,
    factual dict the caller builds from the engine + DB."""
    if not available():
        return None
    messages = [
        {"role": "system", "content": _ANSWER_SYSTEM},
        {"role": "user", "content":
            "Floor snapshot:\n" + json.dumps(snapshot, default=str)
            + f"\n\nQuestion: {question}"},
    ]
    return _call(messages, temperature=0.15, max_tokens=160)


# --------------------------------------------------------------------------
# 2. Free-form constraint parsing -> the SAME structured shape as the rules
# --------------------------------------------------------------------------
_PARSE_SYSTEM = (
    "You convert a plant manager's plain-English disruption into a JSON "
    "constraint. Output ONLY JSON, no prose. Schema:\n"
    "{\n"
    '  "ctype": one of ["resource_down","material_delay","rush_order",'
    '"labour_reduction","priority_change","quality_hold","capacity_boost"],\n'
    '  "resource_group": one of ["CAL","BURNIN","HELIUM","SENSOR","SMT","ASSY",'
    '"TEST","QC","PACK","KITTING","DISPATCH"] or null,\n'
    '  "resource_id": a specific unit like "C2" or "B2" or null,\n'
    '  "order_code": like "SO-1044" or null,\n'
    '  "magnitude": integer or null,\n'
    '  "date_hint": a day-of-month integer or null,\n'
    '  "echo": a short human confirmation of what you understood\n'
    "}\n"
    "Calibration benches are CAL (C1-C3). Burn-in chambers are BURNIN (B1,B2). "
    "Helium leak is HELIUM (H1). If you cannot map it, set ctype to null."
)


def parse_freeform(text: str) -> Optional[dict]:
    """Return a dict describing the constraint, or None. The caller converts
    this into an engine Constraint (same as the rule parser produces)."""
    if not available():
        return None
    messages = [
        {"role": "system", "content": _PARSE_SYSTEM},
        {"role": "user", "content": text},
    ]
    raw = _call(messages, temperature=0.0, max_tokens=250, json_mode=True)
    if not raw:
        return None
    try:
        obj = json.loads(raw)
        return obj if obj.get("ctype") else None
    except json.JSONDecodeError:
        return None
