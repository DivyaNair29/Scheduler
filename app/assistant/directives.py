"""Revision directives — turn the head's rejection feedback into scheduling
instructions the engine honours on the next pass.

When the head rejects a proposed schedule, they say *what should change*:
  "keep SO-1044 on its 31 Jul date"
  "don't touch Line 3"
  "protect the VIP orders"
  "push the low-priority ones instead"
  "split SO-1042 into two batches"

This parses those into a Directives object the engine reads as extra soft/hard
constraints, so revision 2 differs from revision 1 in the way the head asked.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Directives:
    """Extra rules layered onto the engine for a revision."""
    protect_dates: set[str] = field(default_factory=set)     # order codes: don't slip
    freeze_orders: set[str] = field(default_factory=set)     # don't move at all
    freeze_lines: set[str] = field(default_factory=set)      # PT/TT/DP/LT untouched
    prefer_slip: set[str] = field(default_factory=set)       # absorb slip here
    split_orders: set[str] = field(default_factory=set)      # split into batches
    protect_priority_at_least: Optional[int] = None          # protect >= N
    raw: str = ""

    def is_empty(self) -> bool:
        return not any([self.protect_dates, self.freeze_orders, self.freeze_lines,
                        self.prefer_slip, self.split_orders,
                        self.protect_priority_at_least])

    def human(self) -> str:
        parts = []
        if self.protect_dates:
            parts.append(f"hold dates for {', '.join(sorted(self.protect_dates))}")
        if self.freeze_orders:
            parts.append(f"don't move {', '.join(sorted(self.freeze_orders))}")
        if self.freeze_lines:
            parts.append(f"leave {', '.join(sorted(self.freeze_lines))} untouched")
        if self.prefer_slip:
            parts.append(f"absorb slip on {', '.join(sorted(self.prefer_slip))}")
        if self.split_orders:
            parts.append(f"split {', '.join(sorted(self.split_orders))}")
        if self.protect_priority_at_least:
            parts.append(f"protect priority ≥ {self.protect_priority_at_least}")
        return "; ".join(parts) if parts else "no specific directive parsed"


_ORDER = re.compile(r"\b(SO[-\s]?\d{3,5})\b", re.I)
_LINE_WORDS = {
    "line 1": "PT", "pressure": "PT", "pt": "PT",
    "line 2": "TT", "temperature": "TT", "temp": "TT", "tt": "TT",
    "line 3": "DP", "differential": "DP", "dp": "DP",
    "line 4": "LT", "level": "LT", "lt": "LT",
}


def _orders(text: str) -> list[str]:
    out = []
    for m in _ORDER.finditer(text):
        c = m.group(1).upper().replace(" ", "-")
        if not c.startswith("SO-"):
            c = c.replace("SO", "SO-")
        out.append(c)
    return out


def _lines(text: str) -> list[str]:
    t = text.lower()
    found = []
    for word, code in _LINE_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", t):
            found.append(code)
    return list(dict.fromkeys(found))


def parse_feedback(text: str) -> Directives:
    t = text.lower()
    d = Directives(raw=text.strip())
    orders = _orders(text)
    lines = _lines(text)

    # keep / hold / protect a date
    if any(w in t for w in ("keep", "hold", "don't push", "dont push", "do not push",
                            "protect", "on its date", "on time", "meet the date",
                            "no slip", "don't slip", "same date")):
        for o in orders:
            d.protect_dates.add(o)
        if "vip" in t or "key account" in t or "priority" in t:
            d.protect_priority_at_least = 8

    # don't touch / leave alone -> freeze
    if any(w in t for w in ("don't touch", "dont touch", "do not touch",
                            "leave", "untouched", "freeze", "don't move",
                            "dont move", "keep as is", "stay on")):
        for o in orders:
            d.freeze_orders.add(o)
        for ln in lines:
            d.freeze_lines.add(ln)

    # push / slip the low-priority ones instead
    if any(w in t for w in ("push the low", "slip the low", "low-priority",
                            "low priority", "absorb", "instead", "delay those",
                            "move those")):
        # "push the low-priority ones instead" means slip OTHER (low-pri) orders,
        # not the ones being protected. Only attach prefer_slip to orders that
        # are NOT already being held/frozen.
        protected = d.protect_dates | d.freeze_orders
        for o in orders:
            if o not in protected:
                d.prefer_slip.add(o)
        # the common intent is "protect the important ones, slip the rest"
        if "low" in t:
            d.protect_priority_at_least = d.protect_priority_at_least or 7

    # resolve any conflict: an order can't be both protected and slipped
    d.prefer_slip -= (d.protect_dates | d.freeze_orders)

    # split the batch
    if any(w in t for w in ("split", "break up", "two batches", "partial",
                            "part ship", "60/40", "half now")):
        for o in orders:
            d.split_orders.add(o)

    # protect all high priority
    if any(w in t for w in ("protect the vip", "protect vip", "protect priority",
                            "high-priority", "high priority", "rush orders")):
        d.protect_priority_at_least = d.protect_priority_at_least or 8

    return d
