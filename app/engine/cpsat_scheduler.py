"""CP-SAT scheduling engine (OR-Tools).

A constraint-programming refinement that slots in behind the same
SchedulerEngine.compute() call as the greedy forward-pass. It models the plant
as a disjunctive/cumulative resource-scheduling problem and optimises a
priority-weighted objective, then writes the SAME per-order fields the greedy
engine writes (stage_starts, stage_ends, assigned_resource, projected_finish,
status) so nothing downstream changes.

Why keep both engines:
- CP-SAT gives genuinely optimised sequencing at the bottlenecks (calibration
  benches, burn-in chambers) instead of a first-fit greedy pass.
- But a solver can be slow or return INFEASIBLE on pathological inputs. So this
  module ALWAYS has a time limit and, on any failure, the caller falls back to
  the greedy engine. The board must never break because the solver had a bad day.

Model (per order, per stage in its routing):
- one interval var (start, size=duration, end) on the stage's resource GROUP,
  modelled as a cumulative resource with capacity = number of units in the group
- precedence: stage k+1 starts at/after stage k ends (the routing spine)
- earliest start per order (material delays), release at `now`
- objective: minimise sum over orders of  weight(order) * tardiness
  where tardiness = max(0, finish - due) and weight rises steeply with priority
  (rush/VIP dominate), so high-priority orders hold their slots.

Everything is in integer MINUTES from a common epoch (`now`). Wall-clock stages
(burn-in) are modelled in real minutes; working-time stages are approximated in
elapsed minutes here (the greedy engine does exact shift calendars — CP-SAT is
used for SEQUENCING, then the same calendar post-pass could refine if needed).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from .domain import (Constraint, ConstraintType, Order, OrderStatus, Schedule,
                     TimeBasis)
from . import plant


# solver time budget — keep the UI responsive; greedy fallback covers timeouts.
# The model converges to a good solution in well under a second for ~20 orders;
# the limit is a safety ceiling, not a target. Raise it for larger plants.
_TIME_LIMIT_S = float(__import__("os").environ.get("CPSAT_TIME_LIMIT", "1.5"))
_HORIZON_PAD_DAYS = 45          # scheduling window past `now`


def _priority_weight(order: Order) -> int:
    """Steeply increasing weight so higher-priority orders dominate tardiness."""
    p = getattr(order, "effective_priority", None) or getattr(order, "priority", 5)
    # 1..10 -> 1,2,4,...; rush/protected end up worth far more than normal work
    return int(2 ** max(0, min(10, p)))


def available_units(gid: str, constraints, now: datetime) -> int:
    """Capacity of a resource group at `now`, reduced by active outages on it."""
    grp = plant.RESOURCE_GROUPS.get(gid)
    if not grp:
        return 1
    base = len(grp.members) if getattr(grp, "members", None) else getattr(grp, "capacity", 1)
    down = 0
    for c in (constraints or []):
        if c.ctype is not ConstraintType.RESOURCE_DOWN:
            continue
        # a named unit down removes 1; a whole-group outage removes all
        if getattr(c, "resource_group", None) == gid:
            if getattr(c, "resource_id", None):
                down += 1
            else:
                down = base
    return max(0, base - down)


def solve(orders: list[Order], constraints: Optional[list[Constraint]],
          now: datetime, directives=None) -> bool:
    """Populate each order's schedule using CP-SAT. Returns True on success,
    False if OR-Tools is unavailable or the model didn't solve (caller falls
    back to the greedy engine)."""
    try:
        from ortools.sat.python import cp_model
    except Exception:
        return False

    constraints = constraints or []
    orders = [o for o in orders if o is not None]
    if not orders:
        return True

    # ---- gather per-order material delays / holds ------------------------
    earliest: dict[str, int] = {}
    holds: set[str] = set()
    for c in constraints:
        if c.ctype is ConstraintType.MATERIAL_DELAY and c.order_code:
            dt = c.starts_at or now
            earliest[c.order_code] = max(0, int((dt - now).total_seconds() // 60))
        if c.ctype is ConstraintType.QUALITY_HOLD and c.order_code:
            holds.add(c.order_code)
        if c.ctype is ConstraintType.PRIORITY_CHANGE and c.order_code and c.magnitude:
            for o in orders:
                if o.code == c.order_code:
                    o.priority = c.magnitude

    horizon = int((_HORIZON_PAD_DAYS) * 24 * 60)

    model = cp_model.CpModel()
    from engine.estimators import estimate_stage_minutes

    # group -> list of (interval, demand=1) for a cumulative constraint
    group_intervals: dict[str, list] = {}
    group_demands: dict[str, list] = {}
    order_end_vars = {}
    order_stage_vars = {}   # code -> {stage_id: (start_var, end_var, gid, dur)}

    def due_minutes(o):
        due = getattr(o, "due", None)
        if isinstance(due, datetime):
            return int((due - now).total_seconds() // 60)
        return horizon   # unknown due -> no tardiness pressure

    for o in orders:
        routing = plant.routing_for(o.line)
        if not routing:
            continue
        prev_end = None
        rel = earliest.get(o.code, 0)
        stage_vars = {}
        for stage in routing:
            est = estimate_stage_minutes(stage.stage_id, o.line.value)
            pu = est.minutes
            if est.confidence < 0.6:
                pu = int(pu * 1.05)
            qty = max(1, int(o.qty))
            if stage.time_basis is TimeBasis.WALL_CLOCK:
                dur = stage.setup_min + pu
            else:
                dur = stage.setup_min + pu * qty
            dur = max(1, int(dur))

            s = model.NewIntVar(0, horizon, f"s_{o.code}_{stage.stage_id}")
            e = model.NewIntVar(0, horizon, f"e_{o.code}_{stage.stage_id}")
            iv = model.NewIntervalVar(s, dur, e, f"i_{o.code}_{stage.stage_id}")
            model.Add(s >= rel)                       # release / material delay
            if prev_end is not None:
                model.Add(s >= prev_end)              # routing precedence
            prev_end = e
            stage_vars[stage.stage_id] = (s, e, stage.resource_group, dur)

            gid = stage.resource_group
            group_intervals.setdefault(gid, []).append(iv)
            group_demands.setdefault(gid, []).append(1)
        if prev_end is not None:
            order_end_vars[o.code] = prev_end
        order_stage_vars[o.code] = stage_vars

    # ---- cumulative capacity per resource group --------------------------
    for gid, ivs in group_intervals.items():
        cap = available_units(gid, constraints, now)
        if cap <= 0:
            cap = 1   # a fully-down group would make the model infeasible; leave
                      # 1 so we still get a (degraded) plan rather than nothing
        model.AddCumulative(ivs, group_demands[gid], cap)

    # ---- objective: priority-weighted tardiness --------------------------
    tardiness_terms = []
    for o in orders:
        end = order_end_vars.get(o.code)
        if end is None:
            continue
        due = due_minutes(o)
        lateness = model.NewIntVar(-horizon, horizon, f"late_{o.code}")
        model.Add(lateness == end - due)
        tard = model.NewIntVar(0, horizon, f"tard_{o.code}")
        model.AddMaxEquality(tard, [lateness, 0])
        tardiness_terms.append(_priority_weight(o) * tard)
        # secondary: small weight on finish time so we compact idle gaps
        tardiness_terms.append(end)

    if tardiness_terms:
        model.Minimize(sum(tardiness_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = _TIME_LIMIT_S
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return False

    # ---- write results back onto the orders (same fields as greedy) ------
    # assign concrete unit names within each group by ordering start times
    group_unit_cursor: dict[str, list] = {}
    for gid in group_intervals:
        cap = available_units(gid, constraints, now) or 1
        group_unit_cursor[gid] = [0] * cap   # next-free end per unit slot

    for o in orders:
        stage_vars = order_stage_vars.get(o.code, {})
        last_end = 0
        # deterministic unit assignment: process this order's stages in time
        for stage_id, (s, e, gid, dur) in sorted(
                stage_vars.items(), key=lambda kv: solver.Value(kv[1][0])):
            start_min = solver.Value(s)
            end_min = solver.Value(e)
            o.stage_starts[stage_id] = now + timedelta(minutes=start_min)
            o.stage_ends[stage_id] = now + timedelta(minutes=end_min)
            # pick the unit slot that's free earliest (first-fit by availability)
            slots = group_unit_cursor.get(gid, [0])
            ui = min(range(len(slots)), key=lambda i: slots[i])
            slots[ui] = end_min
            o.assigned_resource[stage_id] = _unit_name(gid, ui)
            last_end = max(last_end, end_min)
        o.projected_finish = now + timedelta(minutes=last_end) if stage_vars else None

        # status (mirror greedy)
        if o.code in holds:
            o.status = OrderStatus.HALTED
        elif getattr(o, "rush", False):
            o.status = OrderStatus.RUSH
        elif o.projected_finish and getattr(o, "due", None) and o.projected_finish > o.due:
            o.status = OrderStatus.AT_RISK
        elif o.status not in (OrderStatus.RUNNING, OrderStatus.DONE):
            o.status = OrderStatus.ON_TRACK

    return True


def _unit_name(gid: str, unit_index: int) -> str:
    grp = plant.RESOURCE_GROUPS.get(gid)
    if grp and getattr(grp, "members", None) and unit_index < len(grp.members):
        return grp.members[unit_index].name
    prefix = getattr(grp, "unit_prefix", gid[:1]) if grp else gid[:1]
    return f"{prefix}{unit_index + 1}"
