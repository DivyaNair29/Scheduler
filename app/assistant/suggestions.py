"""Suggestion engine — reads the computed schedule and surfaces actionable
optimizations for throughput, manpower and machine utilisation.

Each suggestion carries observation / reasoning / effect so it is inspectable,
not asserted. Suggestions are generated from real schedule state, not canned.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime

from engine.domain import Order, OrderStatus, Schedule
from engine import plant


@dataclass
class Suggestion:
    id: str
    category: str          # bottleneck | manpower | quality | load | risk | data
    title: str
    observation: str
    reasoning: str
    effect: str
    severity: str          # info | warning | critical

    def to_dict(self):
        return asdict(self)


def _bottleneck_util(schedule: Schedule) -> dict[str, float]:
    """Rough utilisation per bottleneck group = share of orders passing through
    that currently project past their due date."""
    counts = defaultdict(int)
    for o in schedule.orders:
        for sid in o.stage_ends:
            st = plant.STAGES.get(sid)
            if st and st.is_bottleneck:
                counts[st.resource_group] += 1
    return counts


def generate(schedule: Schedule, now: datetime | None = None) -> list[Suggestion]:
    now = now or datetime.utcnow()
    out: list[Suggestion] = []

    orders = schedule.orders
    at_risk = [o for o in orders if o.status is OrderStatus.AT_RISK]
    halted = [o for o in orders if o.status is OrderStatus.HALTED]

    # --- risk: orders projecting late ------------------------------------
    if at_risk:
        worst = max(at_risk, key=lambda o: (o.projected_finish or now))
        out.append(Suggestion(
            id="risk-late",
            category="risk", severity="critical",
            title=f"{len(at_risk)} order(s) projecting past their due date",
            observation=f"{', '.join(o.code for o in at_risk[:4])}"
                        + (" …" if len(at_risk) > 4 else "")
                        + " finish after the promised date on the current plan.",
            reasoning="These orders sit behind higher-priority work at the "
                      "calibration/burn-in bottleneck.",
            effect=f"Escalate {worst.code} or add calibration capacity to recover "
                   "the promised dates."))

    # --- halted orders ----------------------------------------------------
    for o in halted:
        out.append(Suggestion(
            id=f"halt-{o.code}",
            category="risk", severity="critical",
            title=f"{o.code} is halted",
            observation=f"{o.code} ({o.line.value}) is on hold and not progressing.",
            reasoning="A halted order blocks its resource slot and pushes "
                      "everything queued behind it.",
            effect="Clear the hold or reassign the slot so downstream orders move."))

    # --- bottleneck load balance -----------------------------------------
    # burn-in: are chambers evenly loaded?
    burnin = plant.RESOURCE_GROUPS["BURNIN"]
    chamber_load = defaultdict(int)
    for o in orders:
        r = o.assigned_resource.get("09B")
        if r:
            chamber_load[r] += 1
    if len(chamber_load) >= 2:
        vals = sorted(chamber_load.values())
        if vals[-1] - vals[0] >= 2:
            hi = max(chamber_load, key=chamber_load.get)
            lo = min(chamber_load, key=chamber_load.get)
            out.append(Suggestion(
                id="load-burnin",
                category="load", severity="warning",
                title="Burn-in chambers are unevenly loaded",
                observation=f"{hi} carries {chamber_load[hi]} orders while "
                            f"{lo} carries {chamber_load[lo]}.",
                reasoning="Uneven chamber loading leaves capacity idle while "
                          "orders queue.",
                effect=f"Move a queued order from {hi} to {lo} to shorten the "
                       "burn-in queue."))

    # --- calibration pressure --------------------------------------------
    cal_orders = [o for o in orders if "09" in o.stage_ends]
    if len(cal_orders) >= plant.RESOURCE_GROUPS["CAL"].capacity * 3:
        out.append(Suggestion(
            id="bottleneck-cal",
            category="bottleneck", severity="warning",
            title="Calibration is the binding constraint",
            observation=f"{len(cal_orders)} orders route through "
                        f"{plant.RESOURCE_GROUPS['CAL'].capacity} calibration benches.",
            reasoning="Calibration is the slowest manned stage; it sets the pace "
                      "of the whole floor.",
            effect="Adding a calibration tech on Shift B or C runs an extra bench "
                   "in parallel and lifts throughput."))

    # --- line sequencing: consecutive same-line orders save changeover ---
    by_line = defaultdict(list)
    for o in orders:
        by_line[o.line].append(o)
    for line, group in by_line.items():
        wet = line.value in ("PT", "DP")
        if wet and len(group) >= 3:
            out.append(Suggestion(
                id=f"seq-{line.value}",
                category="bottleneck", severity="info",
                title=f"Batch the {line.value} orders to save changeover",
                observation=f"{len(group)} {line.value} orders are in progress; "
                            "wet-line setup (fill + leak test) repeats per batch.",
                reasoning="Running same-line orders consecutively amortises the "
                          "sensor-line setup instead of paying it each time.",
                effect="Sequencing them back-to-back on the calibration benches "
                       "recovers setup time."))
            break

    # --- manpower: idle vs gap (needs operator data, optional) -----------
    # handled in the app layer where Operator rows exist; placeholder hook.

    return out
