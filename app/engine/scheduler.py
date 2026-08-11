"""The scheduling engine.

A finite-capacity forward-pass scheduler. Given the plant definition, a set of
orders, and a set of active constraints, it computes when each order runs each
stage, on which named resource, and when it finishes — respecting:

  * resource-group capacity (3 cal benches, 2 burn-in chambers, ...)
  * planned/forced outages from constraints (a bench offline reduces capacity)
  * working shifts for staffed stages; wall-clock for burn-in
  * priority (rush/VIP scheduled first; low-priority absorbs slip)

It is deterministic and explainable — every decision follows from the state,
not a script. This is the piece that replaces the stubbed build_schedule_changes.

The forward-pass is intentionally simple and fast. The bottleneck stages
(calibration, burn-in) are where sequencing matters; a CP-SAT refinement can be
dropped in later behind the same compute() call. The greedy pass already
produces feasible, priority-respecting schedules.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from .domain import (Constraint, ConstraintType, LineCode, Order, OrderStatus,
                     Schedule, ScheduleChange, TimeBasis)
from . import plant


# Directives are optional — imported lazily to avoid a hard dependency on the
# assistant package from the engine core.
def _empty_directives():
    class _D:
        protect_dates = set()
        freeze_orders = set()
        freeze_lines = set()
        prefer_slip = set()
        split_orders = set()
        protect_priority_at_least = None
        def is_empty(self): return True
    return _D()


# --------------------------------------------------------------------------
# Working-time calendar
# --------------------------------------------------------------------------
class Calendar:
    """Converts a start instant + working-minutes into an end instant,
    skipping non-working hours for staffed stages. Wall-clock stages
    (burn-in) run straight through."""

    def __init__(self):
        self.windows = []  # (start_hour, end_hour) per shift within a day
        for s in plant.SHIFTS:
            self.windows.append((s.start_hour, s.start_hour + s.length_hours))

    def _in_working_window(self, dt: datetime) -> bool:
        if dt.weekday() not in plant.WORKING_DAYS:
            return False
        h = dt.hour
        for start, end in self.windows:
            lo, hi = start % 24, end % 24
            if lo < hi:
                if lo <= h < hi:
                    return True
            else:  # wraps midnight (shift C 22->06)
                if h >= lo or h < hi:
                    return True
        return False

    def add_working_minutes(self, start: datetime, minutes: int) -> datetime:
        """Advance `minutes` of *staffed* time from `start`, skipping gaps."""
        if minutes <= 0:
            return start
        cur = start
        remaining = minutes
        # step in coarse then fine increments to stay cheap
        while remaining > 0:
            if self._in_working_window(cur):
                # consume up to the end of the current hour
                step = min(remaining, 60 - cur.minute)
                cur = cur + timedelta(minutes=step)
                remaining -= step
            else:
                cur = (cur + timedelta(hours=1)).replace(minute=0, second=0,
                                                          microsecond=0)
        return cur

    def add_wall_clock(self, start: datetime, minutes: int) -> datetime:
        return start + timedelta(minutes=minutes)


# --------------------------------------------------------------------------
# Resource availability derived from constraints
# --------------------------------------------------------------------------
class ResourcePool:
    """Tracks, per resource group, when each named unit is next free, and how
    many units are knocked out by constraints over a window."""

    def __init__(self, constraints: list[Constraint], horizon_start: datetime):
        self.free_at: dict[str, list[datetime]] = {}
        self.offline: dict[str, set[str]] = {}   # group -> disabled unit ids
        self.cap_delta: dict[str, int] = {}      # group -> +/- capacity

        for gid, group in plant.RESOURCE_GROUPS.items():
            self.free_at[gid] = [horizon_start for _ in group.members]
            self.offline[gid] = set()

        for c in constraints:
            if c.ctype is ConstraintType.RESOURCE_DOWN:
                gid = c.resource_group
                if not gid:
                    continue
                if c.resource_id:
                    self.offline.setdefault(gid, set()).add(c.resource_id)
                else:
                    # whole-group magnitude knockout
                    self.cap_delta[gid] = self.cap_delta.get(gid, 0) - (c.magnitude or 1)
            elif c.ctype is ConstraintType.LABOUR_REDUCTION:
                gid = c.resource_group
                self.cap_delta[gid] = self.cap_delta.get(gid, 0) - (c.magnitude or 1)
            elif c.ctype is ConstraintType.CAPACITY_BOOST:
                gid = c.resource_group
                self.cap_delta[gid] = self.cap_delta.get(gid, 0) + (c.magnitude or 1)

    def _active_unit_indices(self, gid: str) -> list[int]:
        group = plant.RESOURCE_GROUPS[gid]
        idxs = []
        for i, unit in enumerate(group.members):
            if unit.resource_id in self.offline.get(gid, set()):
                continue
            idxs.append(i)
        # apply capacity delta (labour/boost): trim or (boost) allow reuse
        delta = self.cap_delta.get(gid, 0)
        if delta < 0:
            idxs = idxs[: max(0, len(idxs) + delta)]
        return idxs

    def earliest_slot(self, gid: str, not_before: datetime) -> tuple[int, datetime]:
        """Pick the active unit in the group free soonest (but >= not_before)."""
        idxs = self._active_unit_indices(gid)
        if not idxs:
            # group fully offline — push far out so it surfaces as at-risk
            return -1, not_before + timedelta(days=3)
        best_i = min(idxs, key=lambda i: max(self.free_at[gid][i], not_before))
        start = max(self.free_at[gid][best_i], not_before)
        return best_i, start

    def commit(self, gid: str, unit_i: int, end: datetime):
        if unit_i >= 0:
            self.free_at[gid][unit_i] = end

    def unit_name(self, gid: str, unit_i: int) -> str:
        if unit_i < 0:
            return f"{plant.RESOURCE_GROUPS[gid].name} (none free)"
        return plant.RESOURCE_GROUPS[gid].members[unit_i].name


# --------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------
class SchedulerEngine:
    def __init__(self, now: Optional[datetime] = None):
        self.now = now or datetime.utcnow()
        self.cal = Calendar()

    def compute(self, orders: list[Order],
                constraints: Optional[list[Constraint]] = None,
                directives=None) -> Schedule:
        constraints = constraints or []
        directives = directives or _empty_directives()
        pool = ResourcePool(constraints, self.now)

        # material delays -> per-order earliest start
        earliest: dict[str, datetime] = {}
        holds: set[str] = set()
        for c in constraints:
            if c.ctype is ConstraintType.MATERIAL_DELAY and c.order_code:
                earliest[c.order_code] = c.starts_at or self.now
            if c.ctype is ConstraintType.QUALITY_HOLD and c.order_code:
                holds.add(c.order_code)
            if c.ctype is ConstraintType.PRIORITY_CHANGE and c.order_code:
                for o in orders:
                    if o.code == c.order_code and c.magnitude:
                        o.priority = c.magnitude

        # DIRECTIVES: apply the head's revision instructions
        #  - protect_dates / protected priority: bump effective priority so the
        #    order schedules first and holds its slot (won't slip)
        #  - freeze_orders / freeze_lines: same, strongest protection
        #  - prefer_slip: lower priority so these absorb the slip instead
        prio_floor = directives.protect_priority_at_least
        for o in orders:
            if prio_floor and o.effective_priority >= prio_floor:
                o.priority = max(o.priority, 10)
            if o.code in directives.protect_dates:
                o.priority = max(o.priority, 9)
            if o.code in directives.freeze_orders:
                o.priority = 10
            if o.line.value in directives.freeze_lines:
                o.priority = max(o.priority, 9)
            if o.code in directives.prefer_slip:
                o.priority = min(o.priority, 2)

        # schedule in priority order (higher first), then by due date
        ordered = sorted(
            orders,
            key=lambda o: (-o.effective_priority, o.due))

        # ENGINE SELECTION: the greedy forward-pass is instant and feasible, so
        # it's the default for interactive requests. CP-SAT (OR-Tools) is the
        # optimising engine — it produces better bottleneck sequencing but costs
        # ~1s per solve, so it's opt-in via SCHEDULER_ENGINE=cpsat (recommended
        # for the propose/apply re-plan, or when you want optimised plans over
        # raw latency). On any solver failure/timeout it falls back to greedy so
        # the board never breaks.
        import os
        want = os.environ.get("SCHEDULER_ENGINE", "greedy").lower()
        if want == "cpsat":
            try:
                from engine.cpsat_scheduler import solve as _cpsat_solve
                if _cpsat_solve(orders, constraints, self.now, directives):
                    self._apply_holds_status(orders, holds)
                    return Schedule(orders=orders)
            except Exception:
                pass   # fall through to greedy

        for o in ordered:
            split = o.code in getattr(directives, "split_orders", set())
            self._schedule_order(o, pool, earliest.get(o.code, self.now),
                                  held=o.code in holds, split=split)

        return Schedule(orders=orders)

    def _apply_holds_status(self, orders, holds):
        """After a CP-SAT solve, ensure held orders show HALTED (the solver
        schedules them but a quality hold means work is stopped)."""
        for o in orders:
            if o.code in holds:
                o.status = OrderStatus.HALTED

    def _schedule_order(self, o: Order, pool: ResourcePool,
                        not_before: datetime, held: bool, split: bool = False):
        cursor = max(not_before, self.now)
        route = plant.routing_for(o.line)

        # split: run the bottleneck stages at 60% batch size (part-ship the rest),
        # which lets the order clear calibration/burn-in sooner and hold its date.
        split_factor = 0.6 if split else 1.0

        # if a current stage is set, only schedule from there onward
        started = o.current_stage_id is None
        for stage in route:
            if not started:
                if stage.stage_id == o.current_stage_id:
                    started = True
                else:
                    continue

            # Duration estimate goes through the pluggable seam: today this is
            # the engineering estimate; a learned model can slot in later behind
            # the same call, returning a value + confidence the engine respects.
            from engine.estimators import estimate_stage_minutes
            est = estimate_stage_minutes(stage.stage_id, o.line.value)
            pu = est.minutes
            # low-confidence numbers get a small safety pad so an unsure estimate
            # never drives an over-optimistic committed date
            if est.confidence < 0.6:
                pu = int(pu * 1.05)
            eff_qty = max(1, int(o.qty * split_factor))
            if stage.time_basis is TimeBasis.WALL_CLOCK:
                # burn-in: one batch dwell, not per-unit-summed
                duration = stage.setup_min + pu
            else:
                duration = stage.setup_min + pu * eff_qty

            gid = stage.resource_group
            unit_i, start = pool.earliest_slot(gid, cursor)

            if stage.time_basis is TimeBasis.WALL_CLOCK:
                end = self.cal.add_wall_clock(start, duration)
            else:
                end = self.cal.add_working_minutes(start, duration)

            pool.commit(gid, unit_i, end)
            o.stage_starts[stage.stage_id] = start
            o.stage_ends[stage.stage_id] = end
            o.assigned_resource[stage.stage_id] = pool.unit_name(gid, unit_i)
            cursor = end

        o.projected_finish = cursor

        # derive status
        if held:
            o.status = OrderStatus.HALTED
        elif o.rush:
            o.status = OrderStatus.RUSH
        elif o.projected_finish and o.projected_finish > o.due:
            o.status = OrderStatus.AT_RISK
        elif o.status not in (OrderStatus.RUNNING, OrderStatus.DONE):
            o.status = OrderStatus.ON_TRACK


# --------------------------------------------------------------------------
# Diff — produce the before/after the approval card shows
# --------------------------------------------------------------------------
def diff_schedules(before: Schedule, after: Schedule,
                   constraint: Constraint) -> list[ScheduleChange]:
    changes: list[ScheduleChange] = []
    b_by = {o.code: o for o in before.orders}

    moved = 0
    slipped = 0
    protected = 0
    for a in after.orders:
        b = b_by.get(a.code)
        if not b:
            changes.append(ScheduleChange(
                f"{a.code} — new order", "—",
                a.projected_finish.strftime("%d %b") if a.projected_finish else "?",
                "inserted into the plan"))
            continue
        if b.projected_finish and a.projected_finish:
            if a.projected_finish.date() != b.projected_finish.date():
                delta_days = (a.projected_finish.date() - b.projected_finish.date()).days
                if delta_days > 0:
                    slipped += 1
                    note = f"slips {delta_days}d"
                else:
                    note = f"pulled in {-delta_days}d"
                changes.append(ScheduleChange(
                    f"{a.code} — finish",
                    b.projected_finish.strftime("%d %b"),
                    a.projected_finish.strftime("%d %b"),
                    note))
                moved += 1
            elif a.effective_priority >= 9:
                protected += 1

    # bottleneck reassignment lines (calibration bench / burn-in chamber swaps)
    # ONLY when the constraint actually concerns a named resource going down.
    # For a labour absence, material delay, priority change, etc., the CP solver
    # may still pick different chambers/benches on a fresh solve, but those swaps
    # are incidental noise — not an effect of the constraint — and showing them
    # (a) confuses the reader and (b) contradicts the "0 moved" summary. So we
    # suppress them unless the constraint is a resource-down/maintenance one.
    from engine.domain import ConstraintType
    is_resource_constraint = getattr(constraint, "ctype", None) is ConstraintType.RESOURCE_DOWN
    if is_resource_constraint:
        for a in after.orders:
            b = b_by.get(a.code)
            if not b:
                continue
            for sid in ("09", "09B"):
                ra = a.assigned_resource.get(sid)
                rb = b.assigned_resource.get(sid)
                if ra and rb and ra != rb:
                    changes.append(ScheduleChange(
                        f"{a.code} — {plant.STAGES[sid].name}", rb, ra,
                        "reassigned around the outage"))

    reassigned_n = sum(1 for c in changes if "reassign" in (c.note or "").lower())
    if moved == 0 and slipped == 0:
        # nothing had to move — say so plainly instead of a row of zeros, which
        # reads like "nothing happened". The floor absorbed the constraint.
        bits = ["No orders had to move — the floor absorbs this within the current plan"]
        if reassigned_n:
            bits.append(f"{reassigned_n} order(s) reassigned to another unit")
        if protected:
            bits.append("1 high-priority order held its slot")
        summary = " · ".join(bits) + "."
    else:
        summary = (f"{moved} order(s) moved · {slipped} slipped · "
                   f"{protected} high-priority protected")
    after.summary = summary
    after.changes = changes
    return changes
