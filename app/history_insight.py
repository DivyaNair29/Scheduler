"""Historical incident analysis for the Insights drill-downs.

Loads the bundled 15-year synthetic incident history (app/data/history) and
produces the DEEP drill-down payloads the Insights charts show when a bar is
clicked: not just "which resource, what %", but the actual incidents behind a
cause — dates, severity, INR loss, root cause, the fix that was applied, whether
it recurred — plus the recurring-theme lesson.

All records are synthetic (flagged in the data); this exercises the
insight/anomaly reasoning, it is not real measured statistics.
"""
from __future__ import annotations

import os
import json
from collections import defaultdict, Counter

_DIR = os.path.join(os.path.dirname(__file__), "data", "history")
_cache = {}


def _load(name):
    if name in _cache:
        return _cache[name]
    path = os.path.join(_DIR, name)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = None
    _cache[name] = data
    return data


def _incidents():
    d = _load("incidents.json")
    if not d:
        return []
    return d if isinstance(d, list) else d.get("incidents", [])


def _baselines():
    return _load("baselines.json") or {}


def available():
    return bool(_incidents())


CATEGORY_LABEL = {
    "machine": "Machine fault", "material": "Material shortage",
    "people": "Manpower gap", "quality": "Quality rework",
    "changeover": "Changeover overrun",
}
_LABEL_TO_CAT = {v: k for k, v in CATEGORY_LABEL.items()}


def _inr(n):
    """Format an INR amount compactly: 2100000 -> ₹21.0L, 21000000 -> ₹2.1cr."""
    if not n:
        return "—"
    if n >= 10000000:
        return f"\u20b9{n / 10000000:.2f}cr"
    if n >= 100000:
        return f"\u20b9{n / 100000:.1f}L"
    return f"\u20b9{n:,.0f}"


def cause_detail(cause_label):
    """Deep drill-down for a constraint cause (e.g. 'Machine fault').

    Returns a dict with:
      summary        : one-line headline (count, total loss, orders lost)
      byResource     : [{label, pct, count}]  — which units, share of this cause
      incidents      : [{id, date, title, severity, loss, rootCause, fix, recurred}]
      themes         : [{theme, lesson, incidents}]  recurring patterns touching it
      totalLossINR   : number
    Returns None if there's no history for this cause.
    """
    cat = _LABEL_TO_CAT.get(cause_label, cause_label.lower())
    inc = [i for i in _incidents() if i.get("category") == cat]
    if not inc:
        return None

    # by-resource share
    res_counter = Counter()
    for i in inc:
        for r in i.get("entities", {}).get("resources", []):
            res_counter[r] += 1
    total_res = sum(res_counter.values()) or 1
    by_resource = [{"label": r, "pct": round(n / total_res * 100), "count": n}
                   for r, n in res_counter.most_common(8)]

    # which incident ids recur across themes
    baselines = _baselines()
    themes = baselines.get("recurringThemes", baselines.get("recurring_themes", []))
    recurring_ids = set()
    related_themes = []
    inc_ids = {i["id"] for i in inc}
    for t in themes:
        t_ids = set(t.get("incidents", []))
        if t_ids & inc_ids:
            related_themes.append({"theme": t.get("theme"), "lesson": t.get("lesson"),
                                   "incidents": t.get("incidents", [])})
            recurring_ids |= t_ids

    # incident cards (most severe / costliest first)
    sev_rank = {"critical": 3, "high": 2, "medium": 1, "low": 0}
    inc_sorted = sorted(
        inc, key=lambda i: (sev_rank.get(i.get("severity", "low"), 0),
                            i.get("inrLoss", 0)), reverse=True)
    cards = []
    for i in inc_sorted:
        cards.append({
            "id": i["id"], "date": i.get("date"), "title": i.get("title"),
            "severity": i.get("severity"), "loss": i.get("inrLoss", 0),
            "lossFmt": _inr(i.get("inrLoss", 0)),
            "orderLost": bool(i.get("orderLost")),
            "rootCause": i.get("rootCause"),
            "fix": i.get("correctiveAction") or i.get("systemChange"),
            "recurred": i["id"] in recurring_ids,
        })

    total_loss = sum(i.get("inrLoss", 0) for i in inc)
    orders_lost = sum(1 for i in inc if i.get("orderLost"))
    summary = (f"{len(inc)} incidents over 15 years \u00b7 {_inr(total_loss)} total loss"
               + (f" \u00b7 {orders_lost} order(s) lost" if orders_lost else ""))

    return {
        "summary": summary,
        "byResource": by_resource,
        "incidents": cards,
        "themes": related_themes,
        "totalLossINR": total_loss,
        "synthetic": True,
    }


def resource_detail(resource_label):
    """Deep drill-down for a utilisation/resource bar: the incident history of
    that resource (or line). Matches on resource entity or line name."""
    key = resource_label.lower()
    # match a resource whose name appears in the label, or vice versa
    hits = []
    for i in _incidents():
        for r in i.get("entities", {}).get("resources", []):
            if r.lower() in key or key.split(" - ")[0].lower() in r.lower():
                hits.append((i, r))
                break
    if not hits:
        return None
    sev_rank = {"critical": 3, "high": 2, "medium": 1, "low": 0}
    hits.sort(key=lambda h: (sev_rank.get(h[0].get("severity", "low"), 0),
                             h[0].get("inrLoss", 0)), reverse=True)
    cards = [{
        "id": i["id"], "date": i.get("date"), "title": i.get("title"),
        "severity": i.get("severity"), "lossFmt": _inr(i.get("inrLoss", 0)),
        "rootCause": i.get("rootCause"),
        "fix": i.get("correctiveAction") or i.get("systemChange"),
    } for i, _ in hits]
    total = sum(i.get("inrLoss", 0) for i, _ in hits)
    return {"summary": f"{len(cards)} incidents \u00b7 {_inr(total)} total loss",
            "incidents": cards, "synthetic": True}


def overview():
    """Top-level history overview for the Insights page header."""
    inc = _incidents()
    if not inc:
        return None
    b = _baselines()
    by_cat = Counter(i.get("category") for i in inc)
    loss_cat = defaultdict(int)
    for i in inc:
        loss_cat[i.get("category")] += i.get("inrLoss", 0)
    years = sorted({i.get("date", "")[:4] for i in inc if i.get("date")})
    return {
        "incidentCount": len(inc),
        "yearsSpan": (f"{years[0]}\u2013{years[-1]}" if years else ""),
        "totalLossINR": sum(i.get("inrLoss", 0) for i in inc),
        "totalLossFmt": _inr(sum(i.get("inrLoss", 0) for i in inc)),
        "byCategory": {CATEGORY_LABEL.get(k, k): v for k, v in by_cat.items()},
        "themeCount": len(b.get("recurringThemes", [])),
        "synthetic": True,
    }
