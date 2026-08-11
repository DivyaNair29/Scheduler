"""KC order-intake: parse scheduler order documents and detect NEW ones.

The scheduler polls the KC for documents in the `scheduler` department, parses
the strict ```order block in each (see _ORDER_DOC_FORMAT.md), and returns the
ones we haven't imported yet, ready for the intake review queue.
"""
from __future__ import annotations

import re

# line resolution: code / label / family -> line code
_LINE_BY_TOKEN = {
    "pt": "PT", "tt": "TT", "dp": "DP", "lt": "LT",
    "line 1": "PT", "line 2": "TT", "line 3": "DP", "line 4": "LT",
    "line1": "PT", "line2": "TT", "line3": "DP", "line4": "LT",
    "pressure": "PT", "temperature": "TT", "diff. pressure": "DP",
    "differential pressure": "DP", "diff pressure": "DP", "level": "LT",
}
_LINE_NAME = {"PT": "Line 1", "TT": "Line 2", "DP": "Line 3", "LT": "Line 4"}
_FAMILY = {"PT": "Pressure", "TT": "Temperature", "DP": "Diff. pressure", "LT": "Level"}


def resolve_line(raw):
    if not raw:
        return None, None
    key = raw.strip().lower()
    lc = _LINE_BY_TOKEN.get(key)
    if not lc:
        return None, None
    return lc, _LINE_NAME[lc]


def parse_order_block(text):
    """Extract the fenced ```order block from a document's text into a dict of
    fields, plus a list of human-readable warnings. Returns (fields, warnings)
    or (None, [...]) if there's no order block."""
    if not text:
        return None, ["empty document"]
    m = re.search(r"```order\s*(.*?)```", text, re.S | re.I)
    if not m:
        # tolerate a doc that starts with the block without fences, up to a blank line
        m2 = re.match(r"\s*(order_code\s*:.*?)(?:\n\s*\n|$)", text, re.S | re.I)
        if not m2:
            return None, ["no ```order block found"]
        body = m2.group(1)
    else:
        body = m.group(1)

    fields = {}
    for line in body.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        fields[k.strip().lower()] = v.strip()

    warnings = []
    order = {}
    order["code"] = fields.get("order_code") or ""
    if not order["code"]:
        warnings.append("missing order_code")
    order["product"] = fields.get("product") or ""
    if not order["product"]:
        warnings.append("missing product")

    # line resolution: explicit line, else family, else product prefix
    lc, ln = resolve_line(fields.get("line"))
    if not lc:
        lc, ln = resolve_line(fields.get("family"))
    if not lc:
        warnings.append("needs line (could not resolve line/family)")
    order["lineCode"] = lc
    order["line"] = ln
    order["family"] = fields.get("family") or (_FAMILY.get(lc) if lc else "")

    # qty
    try:
        order["qty"] = int(re.sub(r"[^\d]", "", fields.get("qty", "")) or "0")
    except Exception:
        order["qty"] = 0
    if order["qty"] <= 0:
        warnings.append("missing/!invalid qty")

    order["due"] = fields.get("due") or ""
    if not order["due"]:
        warnings.append("missing due date")
    order["startStage"] = fields.get("start_stage") or "Order Entry"
    order["priority"] = (fields.get("priority") or "normal").lower()
    order["rush"] = (fields.get("rush") or "no").strip().lower() in ("yes", "true", "1", "y")
    order["customer"] = fields.get("customer") or ""
    order["notes"] = (fields.get("notes") or "").replace("|", "\n")

    return order, warnings


def detect_new_orders(kc, imported_codes):
    """Poll the KC for scheduler-department docs and return the ones whose
    doc_id is NOT already in imported_codes, each parsed into an order dict.

    Returns {"online": bool, "new": [ {order fields + warnings + docId} ],
             "seen": int, "error": str|None}
    """
    result = {"online": False, "new": [], "seen": 0, "error": None}
    try:
        if not kc.is_online():
            result["error"] = "KC offline"
            return result
        result["online"] = True
        docs = kc.list_documents()  # scheduler department
        result["seen"] = len(docs)
        have = set(imported_codes or [])
        for d in docs:
            doc_id = d.get("doc_id")
            if not doc_id or doc_id in have:
                continue
            # fetch + parse only the new ones
            try:
                txt = kc.document_text(doc_id) or {}
                order, warnings = parse_order_block(txt.get("text", ""))
            except Exception as e:  # noqa: BLE001
                order, warnings = None, [f"could not read document: {e}"]
            if order is None:
                # not an order doc (or unparseable) — skip silently unless it
                # clearly looks like one
                continue
            order["docId"] = doc_id
            order["docTitle"] = d.get("title")
            order["warnings"] = warnings
            # if the doc's own code is blank, fall back to the doc_id
            if not order.get("code"):
                order["code"] = doc_id
            result["new"].append(order)
    except Exception as e:  # noqa: BLE001
        result["error"] = str(e)
    return result
