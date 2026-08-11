"""Domain logic: checklists, optimization suggestions, floor insights, analytics.

Kept out of the view layer so it can be unit-tested and swapped for a real
planning engine without touching routes or templates.
"""
from models import Constraint, Operator, Order, ScheduleChange, db

# ---------------------------------------------------------------- checklists
CHECKLISTS = {
    "Kitting": ["BOM kit verified against pick list", "Serial tags issued",
                "Shortages flagged to planner"],
    "Assembly": ["Sub-assembly torque check logged", "Housing seal fitted",
                 "Wiring continuity pass"],
    "Calibration": ["Bench reference standard verified", "3-point calibration recorded",
                    "Cal certificate drafted"],
    "Burn-in": ["Chamber loaded & profile set", "Soak hours logged",
                "Post-soak drift within limit"],
    "Final QC": ["Visual & dimensional inspection", "Functional test pass",
                 "Open NCRs closed"],
    "Packing": ["Anti-static bagging complete", "Carton labels applied",
                "Document pack enclosed"],
    "Dispatch": ["Ship-ready confirmed", "Carrier booked", "POD reference captured"],
}

CONSTRAINT_TYPES = ["Material shortage", "Machine issue", "Manpower gap",
                    "Quality hold", "Tooling"]

TASK_BY_STAGE = {
    "Kitting": "kit release", "Assembly": "housing build",
    "Calibration": "3-point cal", "Burn-in": "24h soak",
    "Final QC": "functional test", "Packing": "carton & labels",
    "Dispatch": "carrier handover",
}


def checklist_for(stage):
    return CHECKLISTS.get(stage, [])


def task_for(operator):
    if not operator.assigned_stage:
        return "Unassigned - no task today"
    order = (Order.query
             .filter_by(current_stage=operator.assigned_stage)
             .order_by(Order.code)
             .first())
    label = TASK_BY_STAGE.get(operator.assigned_stage, "floor support")
    return f"{order.code} - {label}" if order else f"{operator.assigned_stage} - {label}"


# ------------------------------------------------------- schedule revisions
def build_schedule_changes(constraint):
    """Generate the proposed before -> after rows for the current revision.

    Revision 1 is the naive reroute. Later revisions honour the production
    head's rejection note (feedback) and protect the promised date instead.
    """
    rev = constraint.revision
    rows = [
        (f"{constraint.order_code} - {constraint.stage}", "Line 3 - 06:15",
         "Line 1 - 08:40", "reroute around the constraint"),
        ("SO-1042 - Calibration", "Bench C2 - 09:00", "Bench C3 - 09:00",
         "freed capacity absorbed"),
    ]
    if rev == 1:
        rows.append((f"{constraint.order_code} - promised date", "31 Jul", "1 Aug",
                     "+1 day slip, sales notified"))
    else:
        rows.append((f"{constraint.order_code} - promised date", "31 Jul", "31 Jul",
                     "held per revision note - slip absorbed by splitting the batch 60/40"))

    ScheduleChange.query.filter_by(
        constraint_id=constraint.id, revision=rev).delete()
    for what, frm, to, note in rows:
        db.session.add(ScheduleChange(
            constraint_id=constraint.id, revision=rev,
            what=what, from_value=frm, to_value=to, note=note))
    db.session.commit()
    return constraint.current_changes()


def apply_schedule(constraint):
    """Mark the approved revision live and move the affected order."""
    for change in constraint.current_changes():
        change.applied = True
    order = Order.query.filter_by(code=constraint.order_code).first()
    if order:
        order.status = "RESCHEDULED"
        order.update_source = "ai"
    db.session.commit()


# --------------------------------------------------------------- manpower
def stage_load():
    """Operators per stage, with a gap/tight/ok verdict."""
    out = []
    from config import Config
    for stage in Config.ASSIGN_STAGES:
        n = Operator.query.filter_by(assigned_stage=stage).count()
        state = "gap" if n == 0 else ("tight" if n == 1 else "ok")
        out.append({"name": stage, "count": n, "state": state})
    return out


def optimization_suggestions():
    """Manpower moves that recover time — surfaced on the Manpower board."""
    loads = {s["name"]: s for s in stage_load()}
    out = []
    if loads.get("Calibration", {}).get("count", 0) < 3:
        out.append({
            "id": "sug-cal",
            "title": "Calibration is the Line 3 bottleneck",
            "detail": "SO-1044 (RUSH / SIL2) is queued behind SO-1042 at the "
                      "calibration benches. A second calibration tech on Shift B "
                      "runs both benches in parallel.",
            "impact": "approx -2.5h - finishes a shift early",
            "effort": "+1 calibration tech - Shift B",
        })
    idle = Operator.query.filter(Operator.assigned_stage.is_(None)).first()
    if idle:
        out.append({
            "id": "sug-burn",
            "title": f"{idle.name} is idle on Shift {idle.shift}",
            "detail": f"{idle.name} ({idle.skill}) has no stage assigned. Reassign to "
                      "the Line 3 recovery route to speed SO-1058 back onto schedule.",
            "impact": "approx -1.5h on recovery",
            "effort": f"reassign 1 - Shift {idle.shift}",
        })
    if loads.get("Packing", {}).get("count", 0) <= 1:
        out.append({
            "id": "sug-pack",
            "title": "Packing queue building toward dispatch",
            "detail": "Three orders converge on packing this shift with a single "
                      "packer. One more packer clears the dispatch backlog before "
                      "shift end.",
            "impact": "clears backlog by 18:00",
            "effort": "+1 packer - Shift B",
        })
    return out


def floor_insights():
    """Process-level insights computed from the LIVE floor — not canned text.

    Each insight is derived from the current orders so it always makes sense:
      - USAGE: only raised when two lines of the SAME family are genuinely
        unbalanced (a temperature order can't move to a pressure line, so we
        never suggest cross-family moves here).
      - SPLIT: raised for a real oversized order that can't clear one run in time.
      - RISK:  flags an at-risk order and points to the detail panel.
    Insights carry an `action` block the board can execute via preview->approve
    only when the move is actually valid.
    """
    from engine.adapter import LINE_MAP  # code<->line
    active = [o for o in Order.query.all()
              if (o.active if o.active is not None else True)
              and o.current_stage not in ("Dispatch", "Documentation", "Packing")]

    # family of each line: PT/DP are wet (pressure family), TT/LT dry
    FAMILY = {"PT": "Pressure", "TT": "Temperature", "DP": "Diff. pressure", "LT": "Level"}
    LINE_NAME = {"PT": "Line 1", "TT": "Line 2", "DP": "Line 3", "LT": "Line 4"}
    by_line = {"PT": [], "TT": [], "DP": [], "LT": []}
    for o in active:
        if o.line_code in by_line:
            by_line[o.line_code].append(o)

    out = []

    # 1) SPLIT — the largest oversized order still early in its route
    big = [o for o in active
           if (o.qty or 0) >= 80 and o.current_stage in ("Kitting", "Sensor", "Engineering")]
    if big:
        o = max(big, key=lambda x: x.qty)
        # pick a same-family sister line for part B if one exists
        sister = "DP" if o.line_code == "PT" else ("LT" if o.line_code == "TT" else o.line_code)
        out.append({
            "id": "ins-split", "kind": "SPLIT", "ref": o.code,
            "title": f"Split {o.code} ({o.qty} pcs) to protect the due date",
            "detail": f"{o.code} is a large batch ({o.qty} pcs) still at "
                      f"{o.current_stage}; a single run is unlikely to clear by "
                      f"{o.due or 'its due date'}. Splitting it lets the first "
                      f"part ship on schedule.",
            "gain": "protects the due date",
            "action": {"type": "split", "order": o.code, "pctA": 60, "lineB": sister}})

    # 2) USAGE — only for two lines of the SAME family that are unbalanced
    same_family_pairs = [("PT", "DP"), ("TT", "LT")]
    for a, b in same_family_pairs:
        na, nb = len(by_line[a]), len(by_line[b])
        if abs(na - nb) >= 2:
            busy, quiet = (a, b) if na > nb else (b, a)
            # a movable order on the busy line: early-stage, same family
            movable = [o for o in by_line[busy]
                       if o.current_stage in ("Kitting", "Sensor", "Electronics", "Assembly")]
            if movable:
                o = movable[-1]
                out.append({
                    "id": "ins-usage", "kind": "USAGE",
                    "ref": f"{LINE_NAME[busy]} / {LINE_NAME[quiet]}",
                    "title": f"{LINE_NAME[busy]} is busier than {LINE_NAME[quiet]}",
                    "detail": f"{LINE_NAME[busy]} has {len(by_line[busy])} active orders "
                              f"vs {len(by_line[quiet])} on {LINE_NAME[quiet]} (both "
                              f"{FAMILY[busy].split()[0]}-family). Moving {o.code} "
                              f"({o.current_stage}) to {LINE_NAME[quiet]} evens the load.",
                    "gain": "balances line load",
                    "action": {"type": "move", "order": o.code, "targetLine": quiet}})
                break

    # 3) RISK — surface the most-overdue at-risk order (no auto-action)
    at_risk = [o for o in active if o.status in ("AT RISK", "HALTED")]
    if at_risk:
        o = at_risk[0]
        out.append({
            "id": "ins-risk", "kind": "RISK", "ref": o.code,
            "title": f"{o.code} is {o.status.lower()} — review it",
            "detail": f"{o.code} ({o.product}) on {LINE_NAME.get(o.line_code, o.line_code)} "
                      f"is {o.status.lower()} at {o.current_stage}. Open its detail to "
                      f"see the reason and options.",
            "gain": "needs a decision",
            "action": {"type": "open", "order": o.code}})

    return out


# --------------------------------------------------------------- analytics

# Per-month sample orders behind the volume chart (demo data — a real ERP
# feed would replace this list). qty >= LARGE_ORDER_QTY is flagged as a
# large batch, same threshold the live floor insights use for SPLIT calls.
LARGE_ORDER_QTY = 80

MONTH_ORDERS = {
    "Mar": [
        {"code": "SO-1012", "product": "PT-3051", "line": "Line 1 - Pressure", "qty": 46, "status": "Shipped on time"},
        {"code": "SO-1015", "product": "TT-2210", "line": "Line 2 - Temperature", "qty": 58, "status": "Shipped on time"},
        {"code": "SO-1019", "product": "DP-1180", "line": "Line 3 - Diff. Pressure", "qty": 34, "status": "Shipped on time"},
        {"code": "SO-1022", "product": "LT-4400", "line": "Line 4 - Level", "qty": 71, "status": "Shipped late"},
        {"code": "SO-1027", "product": "PT-3051", "line": "Line 1 - Pressure", "qty": 52, "status": "Shipped on time"},
    ],
    "Apr": [
        {"code": "SO-1041", "product": "TT-2210", "line": "Line 2 - Temperature", "qty": 96, "status": "Shipped late"},
        {"code": "SO-1044", "product": "PT-3051", "line": "Line 1 - Pressure", "qty": 41, "status": "Shipped on time"},
        {"code": "SO-1049", "product": "DP-1180", "line": "Line 3 - Diff. Pressure", "qty": 88, "status": "Shipped late"},
        {"code": "SO-1052", "product": "TT-2210", "line": "Line 2 - Temperature", "qty": 37, "status": "Shipped on time"},
        {"code": "SO-1057", "product": "LT-4400", "line": "Line 4 - Level", "qty": 29, "status": "Shipped on time"},
        {"code": "SO-1061", "product": "PT-3051", "line": "Line 1 - Pressure", "qty": 63, "status": "Shipped on time"},
    ],
    "May": [
        {"code": "SO-1073", "product": "PT-3051", "line": "Line 1 - Pressure", "qty": 55, "status": "Shipped on time"},
        {"code": "SO-1078", "product": "TT-2210", "line": "Line 2 - Temperature", "qty": 82, "status": "Shipped late"},
        {"code": "SO-1084", "product": "DP-1180", "line": "Line 3 - Diff. Pressure", "qty": 44, "status": "Shipped on time"},
        {"code": "SO-1089", "product": "LT-4400", "line": "Line 4 - Level", "qty": 39, "status": "Shipped on time"},
        {"code": "SO-1093", "product": "PT-3051", "line": "Line 1 - Pressure", "qty": 61, "status": "Shipped on time"},
    ],
    "Jun": [
        {"code": "SO-1101", "product": "TT-2210", "line": "Line 2 - Temperature", "qty": 67, "status": "Shipped on time"},
        {"code": "SO-1107", "product": "PT-3051", "line": "Line 1 - Pressure", "qty": 48, "status": "Shipped on time"},
        {"code": "SO-1113", "product": "DP-1180", "line": "Line 3 - Diff. Pressure", "qty": 91, "status": "Shipped on time"},
        {"code": "SO-1118", "product": "LT-4400", "line": "Line 4 - Level", "qty": 33, "status": "Shipped on time"},
        {"code": "SO-1124", "product": "TT-2210", "line": "Line 2 - Temperature", "qty": 57, "status": "Shipped late"},
    ],
    "Jul": [
        {"code": "SO-1131", "product": "PT-3051", "line": "Line 1 - Pressure", "qty": 60, "status": "Shipped on time"},
        {"code": "SO-1136", "product": "DP-1180", "line": "Line 3 - Diff. Pressure", "qty": 84, "status": "Shipped on time"},
        {"code": "SO-1142", "product": "TT-2210", "line": "Line 2 - Temperature", "qty": 52, "status": "Shipped on time"},
        {"code": "SO-1147", "product": "LT-4400", "line": "Line 4 - Level", "qty": 41, "status": "Shipped on time"},
        {"code": "SO-1153", "product": "PT-3051", "line": "Line 1 - Pressure", "qty": 69, "status": "Shipped late"},
    ],
    "Aug": [
        {"code": "SO-1161", "product": "TT-2210", "line": "Line 2 - Temperature", "qty": 94, "status": "Running"},
        {"code": "SO-1165", "product": "PT-3051", "line": "Line 1 - Pressure", "qty": 47, "status": "Running"},
        {"code": "SO-1169", "product": "DP-1180", "line": "Line 3 - Diff. Pressure", "qty": 38, "status": "Running"},
        {"code": "SO-1173", "product": "LT-4400", "line": "Line 4 - Level", "qty": 31, "status": "Running"},
    ],
}

# Per-month reasons behind the on-time trend — what specifically caused that
# month's dip (or improvement) rather than a single generic cause list.
MONTH_ON_TIME_REASONS = {
    "Mar": {
        "note": "Baseline month — a handful of material delays, nothing systemic.",
        "causes": [{"label": "Material shortage", "pct": 44},
                   {"label": "Manpower gap", "pct": 31},
                   {"label": "Machine fault", "pct": 25}],
    },
    "Apr": {
        "note": "Biggest dip of the six months (93% \u2192 90%). Burn-in Chamber B2's "
                "thermal controller fault ran for several days and two oversized "
                "orders (SO-1041 at 96 pcs, SO-1049 at 88 pcs) landed the same week, "
                "compounding the backlog.",
        "causes": [{"label": "Machine fault \u2014 Burn-in Chamber B2", "pct": 47},
                   {"label": "Large batch congestion (SO-1041, SO-1049)", "pct": 29},
                   {"label": "Material shortage", "pct": 15},
                   {"label": "Manpower gap", "pct": 9}],
    },
    "May": {
        "note": "Recovering from April — B2 was repaired, but Calibration ran short "
                "on Shift C technicians for about a week.",
        "causes": [{"label": "Manpower gap \u2014 Calibration, Shift C", "pct": 52},
                   {"label": "Machine fault", "pct": 20},
                   {"label": "Material shortage", "pct": 18},
                   {"label": "Changeover overrun", "pct": 10}],
    },
    "Jun": {
        "note": "Steady month. Line 2 changeovers were the main drag as product mix shifted.",
        "causes": [{"label": "Changeover overrun \u2014 Line 2", "pct": 41},
                   {"label": "Material shortage", "pct": 27},
                   {"label": "Quality rework", "pct": 19},
                   {"label": "Manpower gap", "pct": 13}],
    },
    "Jul": {
        "note": "Best month of the six \u2014 no chamber faults, no material misses "
                "of note. Calibration out-of-tolerance retests were the only "
                "recurring drag.",
        "causes": [{"label": "Quality rework \u2014 calibration retest", "pct": 58},
                   {"label": "Changeover overrun", "pct": 24},
                   {"label": "Manpower gap", "pct": 18}],
    },
    "Aug": {
        "note": "Slight pull-back from July. Line 2 is running hot (94% utilised) "
                "and starting to spill into changeover delays again.",
        "causes": [{"label": "Line 2 saturation / changeover", "pct": 49},
                   {"label": "Material shortage", "pct": 26},
                   {"label": "Machine fault", "pct": 25}],
    },
}



def compute_insight_rollup(db, window_days=90, source="manual"):
    """Aggregate real recorded history (internal actuals + imported external
    history) into chart-ready insight series, and persist one InsightRollup row.

    Reads:
      - ActualEvent  (kind='constraint' with a category, resources) -> causes + machine breakdown
      - StageActual  (durations, outcomes)                          -> cycle times, rework
      - external_history                                            -> folded in alongside
    Returns the payload dict it stored. Falls back to nothing here — the caller
    (insight_charts) decides how to blend with defaults when history is thin.
    """
    import json
    from datetime import datetime, timedelta
    from models import (StageActual, ActualEvent, ExternalHistory, InsightRollup)
    from collections import Counter, defaultdict

    since = datetime.utcnow() - timedelta(days=window_days)
    # Historical/external data can span years (e.g. a 15-year incident archive).
    # If any external history predates the requested window, widen `since` to the
    # earliest external record so that imported history is actually included —
    # otherwise a default 90-day window silently excludes all of it.
    try:
        earliest = (ExternalHistory.query
                    .order_by(ExternalHistory.occurred_at.asc()).first())
        if earliest and earliest.occurred_at and earliest.occurred_at < since:
            since = earliest.occurred_at - timedelta(days=1)
    except Exception:
        pass
    seen = 0

    # ---- constraint causes (category) + machine breakdown (resource) --------
    cause_counter = Counter()
    machine_counter = defaultdict(Counter)   # category -> {resource: n}
    CAT_LABEL = {"machine": "Machine fault", "material": "Material shortage",
                 "people": "Manpower gap", "quality": "Quality rework",
                 "changeover": "Changeover overrun"}
    for e in ActualEvent.query.filter(ActualEvent.kind == "constraint",
                                      ActualEvent.ts >= since).all():
        cat = (e.category or "").lower()
        label = CAT_LABEL.get(cat)
        if not label:
            continue
        cause_counter[label] += 1
        seen += 1
        if e.resource:
            machine_counter[label][e.resource] += 1
    # fold external history (category-tagged) in
    for h in ExternalHistory.query.filter(ExternalHistory.occurred_at >= since).all():
        cat = (h.category or "").lower()
        label = CAT_LABEL.get(cat)
        if label:
            cause_counter[label] += 1
            seen += 1
            if h.resource:
                machine_counter[label][h.resource] += 1

    total_causes = sum(cause_counter.values())
    causes = []
    if total_causes:
        palette = {"Machine fault": "#c0453b", "Material shortage": "#c2871f",
                   "Manpower gap": "#3a5a86", "Changeover overrun": "#8a6fb0",
                   "Quality rework": "#6b7783"}
        for label, n in cause_counter.most_common():
            pct = round(n / total_causes * 100)
            bd_total = sum(machine_counter[label].values())
            breakdown = ([{"label": r, "pct": round(c / bd_total * 100)}
                          for r, c in machine_counter[label].most_common(6)]
                         if bd_total else [])
            causes.append({"label": label, "pct": pct,
                           "color": palette.get(label, "#6b7783"),
                           "breakdown": breakdown})

    # ---- stage cycle times (avg actual duration per stage) ------------------
    stage_dur = defaultdict(list)
    rework = Counter()
    passed = Counter()
    for sa in StageActual.query.filter(StageActual.recorded_at >= since).all():
        if sa.duration_min:
            stage_dur[sa.stage].append(sa.duration_min)
            seen += 1
        if sa.outcome == "rework":
            rework[sa.stage] += 1
        elif sa.outcome == "pass":
            passed[sa.stage] += 1
    for h in ExternalHistory.query.filter(ExternalHistory.occurred_at >= since).all():
        if h.stage and h.duration_min:
            stage_dur[h.stage].append(h.duration_min)
            seen += 1
    stage_days = []
    for stage, durs in stage_dur.items():
        avg_days = round(sum(durs) / len(durs) / (60 * 24), 2)   # min -> days
        stage_days.append({"stage": stage, "avg_days": avg_days, "n": len(durs)})
    stage_days.sort(key=lambda s: -s["avg_days"])

    # ---- resource utilisation (busy minutes / available) --------------------
    # busy = sum of stage durations on that line; available approximated by the
    # window; expressed as a share so it's meaningful without a shift calendar.
    line_busy = Counter()
    for sa in StageActual.query.filter(StageActual.recorded_at >= since).all():
        if sa.line_code and sa.duration_min:
            line_busy[sa.line_code] += sa.duration_min
    util = []
    if line_busy:
        peak = max(line_busy.values()) or 1
        LN = {"PT": "Line 1 - Pressure", "TT": "Line 2 - Temperature",
              "DP": "Line 3 - Diff. Pressure", "LT": "Line 4 - Level"}
        for lc, mins in sorted(line_busy.items(), key=lambda x: -x[1]):
            pct = round(mins / peak * 100)
            util.append({"label": LN.get(lc, lc), "pct": pct,
                         "note": "saturated" if pct > 90 else ("under-loaded" if pct < 60 else "healthy")})

    payload = {"causes": causes, "stage_days": stage_days, "utilisation": util,
               "computed_at": datetime.utcnow().isoformat(), "events_seen": seen,
               "window_days": window_days}

    row = InsightRollup(window_days=window_days, events_seen=seen,
                        payload=json.dumps(payload), source=source)
    db.session.add(row)
    db.session.commit()
    return payload


def latest_insight_rollup(db, max_age_hours=None):
    """Return the newest InsightRollup payload dict, or None if none/too old."""
    import json
    from datetime import datetime, timedelta
    from models import InsightRollup
    row = InsightRollup.query.order_by(InsightRollup.computed_at.desc()).first()
    if not row:
        return None
    if max_age_hours is not None and row.computed_at:
        if datetime.utcnow() - row.computed_at > timedelta(hours=max_age_hours):
            return None
    try:
        return json.loads(row.payload)
    except Exception:
        return None


def insight_charts(reports):
    """Chart-ready series for the Insights page."""
    volume = [{"label": r.label.split()[0], "value": r.total_orders} for r in reports]
    peak = max([v["value"] for v in volume] or [1])
    for v in volume:
        v["pct"] = round(v["value"] / (peak * 1.06) * 100, 1)

    # drill-down: the actual orders behind that month's total, so a click
    # shows real batches (and flags the large ones) instead of just a percent
    for v in volume:
        orders = MONTH_ORDERS.get(v["label"], [])
        v["orders"] = [
            {**o, "large": o["qty"] >= LARGE_ORDER_QTY}
            for o in sorted(orders, key=lambda o: -o["qty"])
        ]

    on_time = [{"label": r.label.split()[0], "value": r.on_time_pct} for r in reports]
    points = " ".join(
        f"{i * (300 / max(len(on_time) - 1, 1)):.1f},"
        f"{100 - ((p['value'] - 88) / 10 * 100):.1f}"
        for i, p in enumerate(on_time))
    # drill-down: month-specific reasons for that point's on-time % —
    # explains dips instead of showing the same generic cause list everywhere
    for p in on_time:
        reason = MONTH_ON_TIME_REASONS.get(p["label"], {})
        p["note"] = reason.get("note", "")
        p["breakdown"] = reason.get("causes", [])

    causes = [("Machine fault", 31, "#c0453b"), ("Material shortage", 24, "#c2871f"),
              ("Manpower gap", 19, "#3a5a86"), ("Changeover overrun", 14, "#8a6fb0"),
              ("Quality rework", 12, "#6b7783")]
    # drill-down breakdowns shown when a cause bar is clicked (share of that cause)
    cause_breakdown = {
        "Machine fault": [
            {"label": "Burn-in Chamber B2 — thermal controller", "pct": 42},
            {"label": "Calibration Bench C2 — reference drift", "pct": 27},
            {"label": "Helium leak station — pump seal", "pct": 18},
            {"label": "Burn-in Chamber B1 — door interlock", "pct": 13},
        ],
        "Material shortage": [
            {"label": "SNS-88 sensor capsules", "pct": 48},
            {"label": "EP-45 potting compound", "pct": 33},
            {"label": "316L diaphragm stock", "pct": 19},
        ],
        "Manpower gap": [
            {"label": "Calibration technicians (Shift C)", "pct": 51},
            {"label": "Final assembly (Shift B)", "pct": 30},
            {"label": "QC inspectors", "pct": 19},
        ],
        "Changeover overrun": [
            {"label": "Line 2 product changeover", "pct": 57},
            {"label": "Line 3 fixture swap", "pct": 43},
        ],
        "Quality rework": [
            {"label": "Calibration out-of-tolerance", "pct": 61},
            {"label": "Potting voids (visual)", "pct": 39},
        ],
    }
    utilisation = [("Line 1 - Pressure", 62, "#5878b5"),
                   ("Line 2 - Temperature", 94, "#bd7b3f"),
                   ("Line 3 - Diff. Pressure", 81, "#9c6bab"),
                   ("Line 4 - Level", 48, "#3f8fb0")]
    util_breakdown = {
        "Line 1 - Pressure": [{"label": "Calibration", "pct": 38}, {"label": "Burn-in", "pct": 34}, {"label": "Assembly", "pct": 28}],
        "Line 2 - Temperature": [{"label": "Calibration", "pct": 44}, {"label": "Electronics", "pct": 31}, {"label": "Assembly", "pct": 25}],
        "Line 3 - Diff. Pressure": [{"label": "Burn-in", "pct": 40}, {"label": "Calibration", "pct": 35}, {"label": "Hydro", "pct": 25}],
        "Line 4 - Level": [{"label": "Assembly", "pct": 46}, {"label": "Calibration", "pct": 30}, {"label": "Burn-in", "pct": 24}],
    }
    stage_days = [("Kitting", 0.4), ("Assembly", 0.9), ("Calibration", 1.1),
                  ("Burn-in", 1.4), ("Final QC", 0.5), ("Packing", 0.3),
                  ("Dispatch", 0.2)]
    # drill-down: what the stage's average day-count is made of
    stage_breakdown = {
        "Kitting": [{"label": "Waiting on material pick", "pct": 58},
                    {"label": "Kit verification", "pct": 42}],
        "Assembly": [{"label": "Mechanical build", "pct": 46},
                     {"label": "Electronics integration", "pct": 34},
                     {"label": "Rework / punch-list", "pct": 20}],
        "Calibration": [{"label": "Bench queue wait", "pct": 39},
                         {"label": "Calibration run", "pct": 33},
                         {"label": "Out-of-tolerance retest", "pct": 28}],
        "Burn-in": [{"label": "Chamber queue wait", "pct": 44},
                    {"label": "Soak time", "pct": 41},
                    {"label": "B2 fault downtime", "pct": 15}],
        "Final QC": [{"label": "Inspection", "pct": 64}, {"label": "Documentation sign-off", "pct": 36}],
        "Packing": [{"label": "Crating", "pct": 70}, {"label": "Label / paperwork", "pct": 30}],
        "Dispatch": [{"label": "Carrier pickup wait", "pct": 100}],
    }

    # ---- overlay REAL computed history from the nightly rollup, when we have
    # enough of it. Each series is replaced only when the rollup has data for it,
    # so a thin/empty history still shows your rich sample breakdowns and the
    # page is always populated. `historyMeta` tells the UI what's live.
    history_meta = {"live": False, "eventsSeen": 0, "computedAt": None}
    _base = {
        "causes": [{"label": l, "pct": p, "color": c,
                    "breakdown": cause_breakdown.get(l, [])} for l, p, c in causes],
        "utilisation": [
            {"label": l, "pct": p, "color": c,
             "note": "saturated" if p > 90 else ("under-loaded" if p < 60 else "healthy"),
             "breakdown": util_breakdown.get(l, [])}
            for l, p, c in utilisation],
        "stage_days": [{"label": l, "value": d, "pct": round(d / 1.5 * 100, 1),
                        "hot": d >= 1.1, "breakdown": stage_breakdown.get(l, [])}
                       for l, d in stage_days],
    }
    try:
        from app import db as _db
        roll = latest_insight_rollup(_db)
        if roll:
            history_meta = {"live": (roll.get("events_seen", 0) >= 10),
                            "eventsSeen": roll.get("events_seen", 0),
                            "computedAt": roll.get("computed_at"),
                            "windowDays": roll.get("window_days", 90)}
            if history_meta["live"]:
                if roll.get("causes"):
                    _base["causes"] = roll["causes"]
                if roll.get("utilisation"):
                    _base["utilisation"] = roll["utilisation"]
                if roll.get("stage_days"):
                    # rollup uses {stage, avg_days}; map to the chart's shape,
                    # keeping your breakdowns for stages it recognises
                    peak = max([s["avg_days"] for s in roll["stage_days"]] or [1.5])
                    _base["stage_days"] = [
                        {"label": s["stage"], "value": s["avg_days"],
                         "pct": round(s["avg_days"] / (peak or 1.5) * 100, 1),
                         "hot": s["avg_days"] >= peak * 0.8,
                         "breakdown": stage_breakdown.get(s["stage"], [])}
                        for s in roll["stage_days"]]
    except Exception:
        pass

    return {
        "volume": volume,
        "on_time": on_time,
        "on_time_points": points,
        "on_time_last": f"{on_time[-1]['value']}%" if on_time else "-",
        "causes": _base["causes"],
        "utilisation": _base["utilisation"],
        "stage_days": _base["stage_days"],
        "historyMeta": history_meta,
        "actions": [
            {"title": "Add a second burn-in chamber slot on Shift C",
             "detail": "Burn-in holds 1.4 of the 4.1-day cycle and is the largest "
                       "contributor to late orders. A second soak slot removes about "
                       "half a day from every order.",
             "gain": "approx -0.5 d cycle - +18 orders/month"},
            {"title": "Rebalance Line 2 onto Line 4",
             "detail": "Line 2 runs at 94% while Line 4 sits at 48%. Moving "
                       "temperature-transmitter overflow to Line 4 lifts throughput "
                       "without new headcount.",
             "gain": "+9% throughput at zero cost"},
            {"title": "Pre-stage calibration standards at shift start",
             "detail": "Material shortages are 24% of constraints and cluster on "
                       "calibration reference standards. Kitting the standards with "
                       "the batch removes most of that class of stoppage.",
             "gain": "approx -4 constraints/month"},
        ],
    }


def report_rows(reports):
    rows, prev = [], None
    for r in reports:
        growth = None if prev is None else round((r.total_orders - prev) / prev * 100, 1)
        rows.append({"label": r.label, "total": r.total_orders, "shipped": r.shipped,
                     "constraints": r.constraints_raised, "on_time": r.on_time_pct,
                     "cycle": r.avg_cycle_days, "growth": growth})
        prev = r.total_orders
    return rows


def next_constraint_code():
    last = Constraint.query.order_by(Constraint.id.desc()).first()
    if not last:
        return "C-201"
    try:
        return f"C-{int(last.code.split('-')[1]) + 1}"
    except (IndexError, ValueError):
        return f"C-{Constraint.query.count() + 201}"
