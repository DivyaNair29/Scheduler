"""Meridian plant definition — the reference data the engine schedules against.

In production this comes from the Knowledge Centre through the provider
interface. Here it is a concrete, correct baseline so the engine runs today.
Times are engineering estimates (per the reference dataset), not floor-measured.
"""
from __future__ import annotations

from .domain import (LineCode, ResourceGroup, ResourceUnit, Shift, Stage,
                     TimeBasis)

L = LineCode
ALL = (L.PT, L.TT, L.DP, L.LT)
WET = (L.PT, L.DP)          # helium leak + hydro
DRY = (L.TT, L.LT)


# --------------------------------------------------------------------------
# Resource groups (named machines, split from pools)
# --------------------------------------------------------------------------
def _group(gid, name, prefix, n):
    members = [ResourceUnit(f"{prefix}{i}", f"{name} {prefix}{i}", gid)
               for i in range(1, n + 1)]
    return ResourceGroup(gid, name, members)


RESOURCE_GROUPS: dict[str, ResourceGroup] = {
    "KITTING":  _group("KITTING", "Kitting Station", "K", 2),
    "SENSOR":   _group("SENSOR", "Sensor Line", "S", 2),
    "HELIUM":   _group("HELIUM", "Helium Leak Rig", "H", 1),
    "SMT":      _group("SMT", "SMT Line", "E", 1),
    "ASSY":     _group("ASSY", "Final Assembly", "A", 2),
    "CAL":      _group("CAL", "Calibration Bench", "C", 3),
    "BURNIN":   _group("BURNIN", "Burn-in Chamber", "B", 2),
    "TEST":     _group("TEST", "Test Station", "T", 2),
    "CERT":     _group("CERT", "Cert Bay", "X", 1),
    "QC":       _group("QC", "Final QC", "Q", 2),
    "PACK":     _group("PACK", "Packing", "P", 2),
    "DISPATCH": _group("DISPATCH", "Dispatch", "D", 1),
}
# Burn-in chambers use the demo names B1/B2 (cal benches C1..C3 already ok)
RESOURCE_GROUPS["BURNIN"].members = [
    ResourceUnit("B1", "Burn-in Chamber B1", "BURNIN"),
    ResourceUnit("B2", "Burn-in Chamber B2", "BURNIN"),
]


# --------------------------------------------------------------------------
# Routing — shared spine + line-specific stages
# per_unit minutes vary by line via PER_LINE overrides
# --------------------------------------------------------------------------
# (stage_id, name, group, default_per_unit, setup, operators, basis, applies, bottleneck)
_STAGE_DEFS = [
    ("05",  "Kitting & Staging",         "KITTING", 3,   20, 1,   TimeBasis.WORKING,    ALL, False),
    ("06",  "Sensor Module",             "SENSOR",  55,  40, 2,   TimeBasis.WORKING,    ALL, False),
    ("06H", "Helium Leak Test",          "HELIUM",  12,  10, 1,   TimeBasis.WORKING,    WET, True),
    ("07",  "Electronics Assembly",      "SMT",     35,  60, 1,   TimeBasis.WORKING,    ALL, False),
    ("08",  "Final Assembly",            "ASSY",    22,  25, 2,   TimeBasis.WORKING,    ALL, False),
    ("09",  "Calibration",               "CAL",     40,  20, 1,   TimeBasis.WORKING,    ALL, True),
    ("09B", "Burn-in",                   "BURNIN",  2880, 5, 0.2, TimeBasis.WALL_CLOCK, ALL, True),
    ("09T", "Hydro / Proof-Pressure",    "TEST",    18,  15, 1,   TimeBasis.WORKING,    WET, False),
    ("10",  "Certification",             "CERT",    25,  30, 1,   TimeBasis.WORKING,    ALL, False),
    ("11",  "Final QC",                  "QC",      15,  20, 1,   TimeBasis.WORKING,    ALL, False),
    ("12",  "Documentation",             "DISPATCH", 8,  60, 1,   TimeBasis.WORKING,    ALL, False),
    ("13",  "Packing",                   "PACK",    10,  15, 1,   TimeBasis.WORKING,    ALL, False),
    ("14",  "Dispatch",                  "DISPATCH", 30, 45, 1,   TimeBasis.WORKING,    ALL, False),
]

# Per-line per-unit overrides (minutes). Missing -> default above.
PER_LINE_MIN = {
    "05":  {L.TT: 2,  L.PT: 3,  L.DP: 3,  L.LT: 4},
    "06":  {L.TT: 35, L.PT: 50, L.DP: 70, L.LT: 55},
    "06H": {L.PT: 10, L.DP: 15},
    "07":  {L.TT: 28, L.PT: 32, L.DP: 38, L.LT: 38},
    "08":  {L.TT: 16, L.PT: 20, L.DP: 28, L.LT: 34},
    "09":  {L.TT: 28, L.PT: 36, L.DP: 48, L.LT: 55},
    "09B": {L.TT: 1440, L.PT: 2880, L.DP: 2880, L.LT: 3600},
    "09T": {L.PT: 16, L.DP: 22},
    "10":  {L.TT: 18, L.PT: 22, L.DP: 30, L.LT: 30},
    "11":  {L.TT: 10, L.PT: 12, L.DP: 18, L.LT: 20},
    "13":  {L.TT: 5,  L.PT: 6,  L.DP: 8,  L.LT: 12},
}

STAGES: dict[str, Stage] = {}
for sid, name, grp, pu, setup, ops, basis, applies, bottleneck in _STAGE_DEFS:
    STAGES[sid] = Stage(sid, name, grp, pu, setup, ops, basis, applies, bottleneck)


def per_unit_minutes(stage_id: str, line: LineCode) -> int:
    override = PER_LINE_MIN.get(stage_id, {})
    if line in override:
        return override[line]
    return STAGES[stage_id].per_unit_min


def routing_for(line: LineCode) -> list[Stage]:
    """The ordered stages a given line actually runs."""
    return [s for s in STAGES.values() if s.runs_for(line)]


# --------------------------------------------------------------------------
# Shifts (three-shift, 24h)
# --------------------------------------------------------------------------
SHIFTS = [
    Shift("A", 6, 8),
    Shift("B", 14, 8),
    Shift("C", 22, 8),
]
WORKING_DAYS = {0, 1, 2, 3, 4, 5}   # Mon–Sat (Python weekday: Mon=0)


# maps free-text resource words -> group id (used by the assistant)
RESOURCE_ALIASES = {
    "calibration": "CAL", "cal bench": "CAL", "bench": "CAL", "calibration bench": "CAL",
    "burn-in": "BURNIN", "burnin": "BURNIN", "chamber": "BURNIN", "burn in": "BURNIN",
    "helium": "HELIUM", "leak": "HELIUM", "helium leak": "HELIUM",
    "assembly": "ASSY", "final assembly": "ASSY",
    "packing": "PACK", "pack": "PACK",
    "qc": "QC", "final qc": "QC", "quality": "QC",
    "kitting": "KITTING", "dispatch": "DISPATCH",
}
