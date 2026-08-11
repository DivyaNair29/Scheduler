"""Knowledge-Centre order extraction.

Turns an uploaded order document into structured order records the scheduler can
create. Until the live KC (RAG/GraphRAG) is connected, this is the extraction
seam: it accepts a clearly-specified document format and pulls the fields out
deterministically, so the demo mirrors "drop the customer PO into the KC and the
scheduler picks up the new order".

Two input shapes are accepted, in priority order:

1. JSON  — a single object or a list of objects. This is the recommended
   machine format the KC should emit. Example:

       {
         "orders": [
           {
             "customer": "Reliance Refineries",
             "product": "PT-3051", "family": "Pressure transmitter",
             "line": "PT", "qty": 40, "due": "22 Aug",
             "stage": "Order Entry", "rush": false,
             "notes": "NACE certification required"
           }
         ]
       }

   (a bare object, or a bare list without the "orders" wrapper, also works.)

2. Labelled text — one order per blank-line-separated block, `Key: value`
   lines. This is the human-friendly format a PO or spec sheet can use:

       Customer: Reliance Refineries
       Product: PT-3051
       Line: PT
       Quantity: 40
       Due: 22 Aug
       Stage: Order Entry
       Rush: no
       Notes: NACE certification required

Every extracted order is validated and normalised; problems are returned as
per-field warnings rather than hard failures so the head can review and fix in
the preview before importing.
"""
from __future__ import annotations

import json
import re

# accepted line codes and the product/family defaults per line
_LINES = {"PT", "TT", "DP", "LT"}
_LINE_ALIASES = {
    "line 1": "PT", "line1": "PT", "l1": "PT", "pressure": "PT", "pt": "PT",
    "line 2": "TT", "line2": "TT", "l2": "TT", "temperature": "TT", "tt": "TT",
    "line 3": "DP", "line3": "DP", "l3": "DP", "diff pressure": "DP",
    "differential pressure": "DP", "dp": "DP",
    "line 4": "LT", "line4": "LT", "l4": "LT", "level": "LT", "lt": "LT",
}

# canonical stage names (must match adapter.STAGE_NAME_TO_ID keys / entry)
_STAGES = [
    "Order Entry", "Kitting", "Sensor Module", "Helium Leak", "Electronics",
    "Assembly", "Calibration", "Burn-in", "Hydro", "Certification",
    "Final QC", "Documentation", "Packing", "Dispatch",
]
_STAGE_LC = {s.lower(): s for s in _STAGES}

# product -> line hint, so a document that gives a product but no line still maps
_PRODUCT_LINE = {"PT-3051": "PT", "TT-644": "TT", "DP-2051": "DP", "LT-5400": "LT"}

_KEYMAP = {
    "customer": "customer", "client": "customer", "buyer": "customer",
    "product": "product", "model": "product", "part": "product",
    "family": "family",
    "line": "line", "production line": "line",
    "qty": "qty", "quantity": "qty", "units": "qty", "pcs": "qty",
    "due": "due", "due date": "due", "requested date": "due",
    "delivery": "due", "delivery date": "due", "required by": "due",
    "stage": "stage", "start stage": "stage", "starting stage": "stage",
    "current stage": "stage",
    "rush": "rush", "priority": "rush", "urgent": "rush",
    "notes": "notes", "note": "notes", "remarks": "notes", "comments": "notes",
}

_TRUE = {"yes", "y", "true", "1", "rush", "urgent", "high", "priority"}


def _norm_line(val, product=None):
    if val:
        key = str(val).strip().lower()
        if key.upper() in _LINES:
            return key.upper()
        if key in _LINE_ALIASES:
            return _LINE_ALIASES[key]
    if product and product.upper() in _PRODUCT_LINE:
        return _PRODUCT_LINE[product.upper()]
    return None


def _norm_stage(val):
    if not val:
        return "Order Entry"
    key = str(val).strip().lower()
    if key in _STAGE_LC:
        return _STAGE_LC[key]
    # loose contains match (e.g. "final qc inspection" -> "Final QC")
    for lc, canonical in _STAGE_LC.items():
        if lc in key or key in lc:
            return canonical
    return None  # unknown -> warning


def _norm_qty(val):
    if val is None:
        return None
    try:
        n = int(re.sub(r"[^\d]", "", str(val)))
        return n if n > 0 else None
    except (ValueError, TypeError):
        return None


def _one(record: dict) -> dict:
    """Validate + normalise a single raw {field: value} dict into an order dict
    plus a list of warnings."""
    warnings = []
    product = (str(record.get("product")).strip()
               if record.get("product") else None)
    line = _norm_line(record.get("line"), product)
    if not line:
        warnings.append("line could not be determined (defaulting to PT)")
        line = "PT"

    qty = _norm_qty(record.get("qty"))
    if not qty:
        warnings.append("quantity missing or invalid (defaulting to 1)")
        qty = 1

    stage = _norm_stage(record.get("stage"))
    if stage is None:
        warnings.append(f"stage '{record.get('stage')}' not recognised "
                        "(defaulting to Order Entry)")
        stage = "Order Entry"

    due = (str(record.get("due")).strip() if record.get("due") else "")
    if not due:
        warnings.append("no due date given")

    rush_raw = record.get("rush")
    rush = (str(rush_raw).strip().lower() in _TRUE) if rush_raw is not None else False

    return {
        "order": {
            "customer": (str(record.get("customer")).strip()
                         if record.get("customer") else None),
            "product": product,
            "family": (str(record.get("family")).strip()
                       if record.get("family") else None),
            "line": line,
            "qty": qty,
            "due": due,
            "stage": stage,
            "rush": rush,
            "notes": (str(record.get("notes")).strip()
                      if record.get("notes") else None),
        },
        "warnings": warnings,
    }


def _remap_keys(raw: dict) -> dict:
    out = {}
    for k, v in raw.items():
        mk = _KEYMAP.get(str(k).strip().lower())
        if mk:
            out[mk] = v
    return out


def _parse_text_blocks(text: str) -> list[dict]:
    """Split labelled text into per-order raw dicts on blank lines."""
    blocks = re.split(r"\n\s*\n", text.strip())
    records = []
    for block in blocks:
        raw = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            raw[key.strip()] = val.strip()
        if raw:
            records.append(_remap_keys(raw))
    return [r for r in records if r]


def extract_orders(text: str) -> dict:
    """Main entry. Returns {orders: [...], warnings-per-order, format, error?}."""
    text = (text or "").strip()
    if not text:
        return {"orders": [], "format": None, "error": "empty document"}

    raw_records = []
    fmt = None

    # 1) try JSON
    try:
        data = json.loads(text)
        fmt = "json"
        if isinstance(data, dict) and "orders" in data:
            data = data["orders"]
        if isinstance(data, dict):
            data = [data]
        if isinstance(data, list):
            raw_records = [_remap_keys(r) if _looks_labelled(r) else r
                           for r in data if isinstance(r, dict)]
    except (ValueError, TypeError):
        fmt = None

    # 2) fall back to labelled text
    if not raw_records:
        raw_records = _parse_text_blocks(text)
        fmt = "text" if raw_records else fmt

    if not raw_records:
        return {"orders": [], "format": fmt,
                "error": "no orders could be read from the document. Use the "
                         "JSON or labelled-text format shown in the upload help."}

    parsed = [_one(r) for r in raw_records]
    return {"orders": parsed, "format": fmt, "count": len(parsed)}


def _looks_labelled(d: dict) -> bool:
    """A JSON object whose keys are human labels (Customer, Quantity) rather than
    canonical field names still gets remapped."""
    keys = {str(k).strip().lower() for k in d.keys()}
    canonical = {"customer", "product", "line", "qty", "due", "stage", "rush",
                 "notes", "family"}
    return bool(keys and not (keys & canonical)) and bool(keys & set(_KEYMAP))
