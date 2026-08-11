"""Adapter: Flask/SQLAlchemy rows  <->  engine domain objects.

The engine knows nothing about the web app. This module is the only place that
imports both, so the two stay decoupled. Drop it into meridian_app/ alongside
the engine/ and assistant/ packages.

Stage name mapping: the app stores current_stage as a display name
("Calibration"); the engine uses stage ids ("09"). This maps between them.
"""
from __future__ import annotations

from datetime import datetime

from .domain import LineCode, Order as EngineOrder, OrderStatus
from . import plant

# app display-stage  ->  engine stage id
STAGE_NAME_TO_ID = {
    "Kitting": "05", "Procurement": "05", "Engineering": "05",
    "Sensor": "06", "Sensor Module": "06",
    "Helium Leak": "06H",
    "Electronics": "07", "Electronics Assembly": "07",
    "Assembly": "08", "Final Assembly": "08",
    "Calibration": "09",
    "Burn-in": "09B",
    "Hydro": "09T", "Test": "09T",
    "Cert": "10", "Certification": "10",
    "Final QC": "11", "QC": "11",
    "Documentation": "12", "Docs": "12",
    "Packing": "13",
    "Dispatch": "14", "Shipping & Dispatch": "14",
}
STAGE_ID_TO_NAME = {
    "05": "Kitting", "06": "Sensor Module", "06H": "Helium Leak",
    "07": "Electronics", "08": "Assembly", "09": "Calibration",
    "09B": "Burn-in", "09T": "Hydro", "10": "Certification",
    "11": "Final QC", "12": "Documentation", "13": "Packing",
    "14": "Dispatch",
}

LINE_MAP = {"PT": LineCode.PT, "TT": LineCode.TT, "DP": LineCode.DP,
            "LT": LineCode.LT}

_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _parse_due(due: str, now: datetime) -> datetime:
    """App stores due as '31 Jul' — turn into a datetime. An order due a little
    while ago is OVERDUE (keep it in the current year); only treat it as a
    next-year date if it's far in the past (> ~6 months), which is the real
    "wraps to next year" case. This avoids turning a slightly-late order into one
    that looks ~a year early."""
    if not due:
        return now
    try:
        parts = due.strip().split()
        day = int(parts[0])
        month = _MONTHS.get(parts[1][:3].lower(), now.month)
        candidate = datetime(now.year, month, day)
        # if the candidate is more than ~6 months in the past, it's a next-year
        # date; otherwise it's simply recent/overdue and stays this year.
        if (now - candidate).days > 182:
            candidate = datetime(now.year + 1, month, day)
        return candidate
    except (ValueError, IndexError):
        return now


def to_engine_order(row, now: datetime) -> EngineOrder:
    """SQLAlchemy Order row -> engine Order."""
    line = LINE_MAP.get(row.line_code, LineCode.PT)
    stage_id = STAGE_NAME_TO_ID.get(row.current_stage or "", None)
    return EngineOrder(
        code=row.code,
        line=line,
        product=row.product,
        qty=row.qty or 1,
        due=_parse_due(row.due or row.promised or "", now),
        priority=10 if row.rush else 5,
        current_stage_id=stage_id,
        status=OrderStatus(row.status) if _is_status(row.status) else OrderStatus.ON_TRACK,
        rush=bool(row.rush),
        held=bool(row.held),
    )


def _is_status(s: str) -> bool:
    try:
        OrderStatus(s)
        return True
    except ValueError:
        return False


def apply_to_row(row, eng_order: EngineOrder):
    """Write the engine's computed status/finish back onto the ORM row.

    Only touches transactional fields — never reference data. Caller commits.
    """
    row.status = eng_order.status.value
    if eng_order.projected_finish:
        row.promised = eng_order.projected_finish.strftime("%d %b")
    # record the bench/chamber the engine assigned at the bottleneck
    bench = eng_order.assigned_resource.get("09")
    if bench:
        row.update_source = "ai"
    return row
