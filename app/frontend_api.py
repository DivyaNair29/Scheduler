"""Frontend integration — serves the multipage static site and feeds it live
data in the exact shape data.js defines.

Drop into app/ and register in app.py:

    from frontend_api import frontend_bp, init_frontend
    init_frontend(app, site_dir="../site")     # path to the static site folder
    app.register_blueprint(frontend_bp)

Then browse http://127.0.0.1:5000/app/  -> the multipage site, live.

Design: rather than repoint every store.js getter at its own endpoint, we serve
the WHOLE MERIDIAN_DATA object from /api/bootstrap, assembled from the database
and the engine. store.js fetches it once at init; if the fetch fails it falls
back to the bundled data.js, so the site still opens standalone.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request, send_from_directory

frontend_bp = Blueprint("frontend", __name__)


# --------------------------------------------------------------------------
# SKILL -> STAGE gating. Operators are NOT interchangeable: each stage needs a
# specific certified skill. A QC inspector does Final QC, not Calibration. This
# is the single source of truth for who-can-do-what, enforced at assignment.
# --------------------------------------------------------------------------
STAGE_SKILL = {
    "Kitting": ["Assembly"], "Sensor Module": ["Assembly"],
    "Electronics": ["Assembly"], "Assembly": ["Assembly"],
    "Calibration": ["Calibration tech"], "Burn-in": ["Burn-in"],
    "Hydro": ["Calibration tech"], "Helium Leak": ["Calibration tech"],
    "Certification": ["QC inspector"],
    "Final QC": ["QC inspector"], "Packing": ["Packer"],
    "Documentation": ["QC inspector"], "Dispatch": ["Packer"],
}


def stages_for_skill(skill):
    """The stages an operator with this skill is trained/certified to perform."""
    return [stage for stage, needed in STAGE_SKILL.items() if skill in needed]


def _extra_stages(op):
    """Extra individual stages a head has trained this operator on."""
    raw = getattr(op, "extra_skills", None) or ""
    return [s.strip() for s in raw.split(",") if s.strip()]


def operator_stages(op):
    """Every stage this operator can do = base skill stages + extra trained
    stages, de-duplicated in a stable order."""
    out = list(stages_for_skill(op.skill)) if op and op.skill else []
    for s in _extra_stages(op):
        if s not in out:
            out.append(s)
    return out


def operator_can_do(op, stage):
    """Skill gate at the operator level (base skill OR an extra trained task)."""
    if op and skill_can_do(op.skill, stage):
        return True
    # match extra stages loosely (normalise names like the base gate does)
    for s in _extra_stages(op or None):
        if s == stage or s.lower() in (stage or "").lower() or (stage or "").lower() in s.lower():
            return True
    return False


def skill_can_do(skill, stage):
    """Can an operator with `skill` perform `stage`? Normalises stage names."""
    needed = STAGE_SKILL.get(stage)
    if needed is None:
        # normalise loosely (e.g. "Final QC" vs "QC")
        for k, v in STAGE_SKILL.items():
            if k.lower() in (stage or "").lower():
                needed = v
                break
    return bool(needed) and skill in needed

_SITE_DIR = None


def init_frontend(app, site_dir="../site"):
    """Register the static site directory (relative to app/)."""
    global _SITE_DIR
    base = os.path.dirname(os.path.abspath(__file__))
    _SITE_DIR = os.path.normpath(os.path.join(base, site_dir))
    app.logger.info("frontend site dir: %s", _SITE_DIR)


# --------------------------------------------------------------------------
# Static file serving  (/app/  -> the multipage site)
# --------------------------------------------------------------------------
@frontend_bp.get("/app/")
def site_index():
    return _nocache(send_from_directory(_SITE_DIR, "index.html"))


@frontend_bp.get("/app/<path:path>")
def site_file(path):
    # serves board.html, css/*, js/*, img/*, assets/* — everything under site/
    return _nocache(send_from_directory(_SITE_DIR, path))


def _nocache(resp):
    """Tell the browser never to cache the app's HTML/JS/CSS. Without this the
    browser can keep running an OLD cached main.js/store.js after the files on
    disk have been updated — which produces confusing "old behaviour" bugs. The
    scheduler's own data is dynamic anyway, so there's nothing to gain from
    caching the shell during development."""
    try:
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    except Exception:
        pass
    return resp


# --------------------------------------------------------------------------
# Lazy model access (avoid import cycle with app.py)
# --------------------------------------------------------------------------
def _models():
    from models import (Confirmation, Constraint, LogEvent, Operator, Order,
                        User, db)
    return Order, Constraint, Confirmation, Operator, User, LogEvent, db


def _active_orders(Order):
    """All orders that are live on the floor — excludes parents retired by a
    batch split (their child A/B orders are the live ones). Falls back safely
    if the `active` column isn't present yet."""
    try:
        return Order.query.filter(
            (Order.active.is_(True)) | (Order.active.is_(None))).all()
    except Exception:
        return Order.query.all()


# --------------------------------------------------------------------------
# BOARD — time-positioned Gantt data from real engine schedule
# --------------------------------------------------------------------------
@frontend_bp.get("/api/board")
def board():
    """Returns the schedule board as line-rows + resource-rows, each order
    positioned by its REAL engine-computed stage times."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from datetime import datetime, timedelta

    try:
        from engine.adapter import to_engine_order
        from engine.scheduler import SchedulerEngine
        from engine import plant
    except Exception as exc:
        return jsonify(error=f"engine unavailable: {exc}"), 500

    now = datetime.utcnow()
    # anchor the board to the start of "today" so the time axis is stable
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = _active_orders(Order)
    sched = SchedulerEngine(now).compute(
        [to_engine_order(r, now) for r in rows], [])

    # display-name lookup for stages
    from engine.adapter import STAGE_ID_TO_NAME, _parse_due

    def mins_from_day_start(dt):
        return int((dt - day_start).total_seconds() / 60)

    # The board window starts at Shift A (06:00 = 360) and runs one full cycle
    # of three shifts to 06:00 next day (1800). Orders span multiple days, so
    # clamp each block to the visible window and flag when it starts before or
    # ends after the window, so the UI can show a continuation hint.
    WIN_START, WIN_END = 360, 1800

    def clamp_block(raw_start, raw_end):
        vs = max(WIN_START, min(WIN_END, raw_start))
        ve = max(WIN_START, min(WIN_END, raw_end))
        return vs, ve, (raw_start < WIN_START), (raw_end > WIN_END)

    # ---- per-order timeline blocks (one per scheduled stage) -------------
    def order_blocks(o):
        blocks = []
        for sid, start in o.stage_starts.items():
            end = o.stage_ends.get(sid)
            if not end:
                continue
            raw_s = mins_from_day_start(start)
            raw_e = mins_from_day_start(end)
            # skip blocks entirely outside the visible window
            if raw_e < WIN_START or raw_s > WIN_END:
                continue
            vs, ve, before, after = clamp_block(raw_s, raw_e)
            blocks.append({
                "stageId": sid,
                "stage": STAGE_ID_TO_NAME.get(sid, sid),
                "startMin": vs, "endMin": ve,
                "spillBefore": before, "spillAfter": after,
                "resource": o.assigned_resource.get(sid),
            })
        return blocks

    line_names = {"PT": "Line 1", "TT": "Line 2", "DP": "Line 3", "LT": "Line 4"}
    line_family = {"PT": "Pressure", "TT": "Temperature",
                   "DP": "Diff. Pressure", "LT": "Level"}

    orders_out = []
    row_by_code = {r.code: r for r in rows}

    def stage_progress(o):
        route = plant.routing_for(o.line)
        ids = [s.stage_id for s in route]
        cur = o.current_stage_id
        idx = (ids.index(cur) + 1) if cur in ids else 0
        return idx, len(ids)

    def updated_ago(code):
        r = row_by_code.get(code)
        if not r or not getattr(r, "updated_at", None):
            return None, None
        m = int((now - r.updated_at).total_seconds() / 60)
        src = (r.update_source or "erp").upper()
        if m < 60:
            return f"{m}m ago", src
        if m < 1440:
            return f"{m // 60}h ago", src
        return f"{m // 1440}d ago", src

    # An order is STALE if nothing has touched it (no stage advance, confirm, or
    # update) for a while — a sign it may be stuck/forgotten. Threshold scales
    # with whether it's actively running.
    STALE_HOURS = 36
    def staleness(code):
        r = row_by_code.get(code)
        if not r or not getattr(r, "updated_at", None):
            return False, None
        hrs = (now - r.updated_at).total_seconds() / 3600.0
        if hrs >= STALE_HOURS:
            days = int(hrs // 24)
            return True, (f"no update in {days}d" if days >= 1
                          else f"no update in {int(hrs)}h")
        return False, None

    # Board window: one working cycle from Shift A start (06:00 = 360 min)
    # through 06:00 the next day (1800 min) — Shifts A, B, C laid end to end.
    SHIFT_A_START = 360
    AXIS_END = 1800
    DOWNSTREAM_IDS = {"11", "12", "13", "14"}   # Final QC onward -> own rows
    KITTING_ID = "05"                            # hide until kitting completes

    def stage_seq_index(o, sid):
        route = plant.routing_for(o.line)
        ids = [s.stage_id for s in route]
        return ids.index(sid) if sid in ids else 0

    # Build order records WITHOUT positioning yet (serial layout comes next).
    for o in sched.orders:
        cur = o.current_stage_id
        blocks = order_blocks(o)
        idx, total = stage_progress(o)
        ago, src = updated_ago(o.code)
        is_stale, stale_note = staleness(o.code)
        row = row_by_code.get(o.code)
        eng_status = o.status.value
        display_status = row.status if row else eng_status
        # The engine's live risk assessment WINS over a stored status: an order
        # whose projected finish is past its due date can never read "ON TRACK",
        # even if the persisted row still says so. We also compare the stored
        # PROMISED date against the DUE date — if delivery has been pushed past
        # the due date, the order is delayed and must not read on-track.
        _late = bool(o.projected_finish and o.due and o.projected_finish > o.due)
        _delayed = False
        if row and row.due and row.promised and row.due != row.promised:
            try:
                dd = _parse_due(row.due, now)
                pp = _parse_due(row.promised, now)
                _delayed = pp.date() > dd.date()
            except Exception:
                _delayed = False
        if eng_status in ("AT RISK", "HALTED") or _late or _delayed:
            if display_status in ("ON TRACK", "RUNNING", "RESCHEDULED"):
                display_status = (eng_status if eng_status in ("AT RISK", "HALTED")
                                  else "AT RISK")
        # real duration of the current stage (for the serial bar width)
        cs = o.stage_starts.get(cur) if cur else None
        ce = o.stage_ends.get(cur) if cur else None
        real_dur = int((ce - cs).total_seconds() / 60) if (cs and ce) else 180
        _row = row_by_code.get(o.code)
        _split_part = getattr(_row, "split_part", None) if _row else None
        _parent = getattr(_row, "parent_code", None) if _row else None
        orders_out.append({
            "code": o.code, "line": o.line.value,
            "lineRow": line_names.get(o.line.value, o.line.value),
            "product": o.product, "qty": o.qty,
            "status": display_status, "rush": o.rush,
            "currentStage": STAGE_ID_TO_NAME.get(cur, cur),
            "currentStageId": cur,
            "seqIndex": stage_seq_index(o, cur) if cur else 0,
            "stageIndex": idx, "stageTotal": total,
            "realDur": real_dur,
            "updatedAgo": ago, "updateSource": src,
            "stale": is_stale, "staleNote": stale_note,
            "blocks": blocks, "current": None,   # filled by serial layout
            "resource": o.assigned_resource.get(cur) if cur else None,
            "due": o.due.strftime("%d %b") if o.due else None,
            "finish": o.projected_finish.strftime("%d %b") if o.projected_finish else None,
            "splitPart": _split_part, "parentCode": _parent,
            "locked": bool(getattr(_row, "locked", False)) if _row else False,
            "lockReason": getattr(_row, "lock_reason", None) if _row else None,
            "qhold": bool(getattr(_row, "qhold", False)) if _row else False,
            "qholdReason": getattr(_row, "qhold_reason", None) if _row else None,
        })

    # ---- SERIAL per-line layout ------------------------------------------
    # One order occupies the line at a time, laid end-to-end from Shift A.
    # Long stages are COMPRESSED so the board stays readable. Burn-in is an
    # unattended soak: it RELEASES the line, so the next order's bar may begin
    # while the previous order is still soaking (overlap only across burn-in).
    def compress(mins):
        # keep bars readable: 1 real hour -> ~22 board-min, floor 70, cap 300
        return max(70, min(int(mins * 0.36), 300))

    # A head may have manually re-ordered a line by dragging cards; that saved
    # sequence (if any) overrides the engine's default ordering for that line.
    try:
        from models import BoardOrder
        manual_seq = {b.line_code: b.codes() for b in BoardOrder.query.all()}
    except Exception:
        manual_seq = {}

    BURNIN_ID = "09B"
    for lc in ("PT", "TT", "DP", "LT"):
        line_orders = [o for o in orders_out
                       if o["line"] == lc
                       and o["currentStageId"] not in DOWNSTREAM_IDS
                       # hide kitting-stage orders EXCEPT fresh split children,
                       # so a just-created part-batch shows on the board at once
                       and (o["currentStageId"] != KITTING_ID or o.get("splitPart"))]
        saved = manual_seq.get(lc)
        if saved:
            # honour the saved manual order; any order not in the saved list
            # (e.g. newly arrived) falls to the end in engine order.
            pos = {code: i for i, code in enumerate(saved)}
            line_orders.sort(key=lambda x: (pos.get(x["code"], 10_000), -x["seqIndex"]))
            for o in line_orders:
                o["manualOrder"] = True
        else:
            # default: the most-advanced order runs first (it entered first)
            line_orders.sort(key=lambda x: -x["seqIndex"])
        cursor = SHIFT_A_START
        for o in line_orders:
            w = compress(o["realDur"])
            start = cursor
            end = min(AXIS_END, start + w)
            o["current"] = {
                "stageId": o["currentStageId"],
                "stage": o["currentStage"],
                "startMin": start, "endMin": end,
                "resource": o["resource"], "anchoredNow": False,
                "spillAfter": (start + w) > AXIS_END,
            }
            # advance the cursor. Burn-in releases the line early (soak is
            # unattended), so the next order can overlap into the soak window.
            if o["currentStageId"] == BURNIN_ID:
                cursor = start + int(w * 0.45)   # next order starts partway in
            else:
                cursor = end + 12                # small changeover gap
            if cursor >= AXIS_END:
                cursor = AXIS_END - 90

    # ---- line rows -------------------------------------------------------
    # Serial per line: only orders that got positioned (past kitting, pre-QC).
    line_rows = [{
        "key": lc, "name": line_names[lc], "family": line_family[lc],
        "color": f"var(--line-{lc.lower()})",
        "manual": lc in manual_seq,
        "orders": [o for o in orders_out
                   if o["line"] == lc and o["current"] is not None],
    } for lc in ("PT", "TT", "DP", "LT")]

    # ---- downstream stage rows (Final QC, Docs, Packing, Dispatch) -------
    downstream_defs = [("Final QC", "11"), ("Documentation", "12"),
                       ("Packing", "13"), ("Dispatch", "14")]
    downstream_rows = []
    for name, sid in downstream_defs:
        blocks = []
        for o in orders_out:
            b = next((x for x in o["blocks"] if x["stageId"] == sid), None)
            if b and o["currentStageId"] == sid:
                blocks.append({**b, "code": o["code"], "product": o["product"],
                               "status": o["status"], "qty": o["qty"]})
        downstream_rows.append({"name": name, "stageId": sid, "blocks": blocks})

    # ---- named-resource rows (burn-in chambers, cal benches) -------------
    # Active resource-down / maintenance constraints get drawn as a labelled
    # block on the matching chamber/bench row, so a maintenance window on B2 is
    # visible on the B2 row (not only implied by the re-planned orders).
    try:
        from scheduler_api import active_resource_outages
        outages = active_resource_outages()
    except Exception:
        outages = []

    def _outage_block(oc):
        raw_s = mins_from_day_start(oc["starts_at"]) if oc.get("starts_at") else WIN_START
        raw_e = mins_from_day_start(oc["ends_at"]) if oc.get("ends_at") else WIN_END
        # open-ended -> fill to the window edge so it's visible
        if raw_e <= raw_s:
            raw_e = WIN_END
        vs, ve, _, _ = clamp_block(raw_s, raw_e)
        if ve <= vs:
            return None
        label = "Maintenance" if oc["kind"] == "maintenance" else "Offline"
        return {"startMin": vs, "endMin": ve, "kind": oc["kind"],
                "label": label, "note": oc.get("note") or "",
                "code": oc.get("code")}

    resource_rows = []
    for gid in ("BURNIN", "CAL"):
        grp = plant.RESOURCE_GROUPS.get(gid)
        if not grp:
            continue
        for unit in grp.members:
            unit_blocks = []
            for o in orders_out:
                for b in o["blocks"]:
                    if b.get("resource") == unit.name:
                        unit_blocks.append({
                            **b, "code": o["code"], "product": o["product"],
                            "status": o["status"],
                            "isCurrent": o["currentStageId"] == b["stageId"],
                        })
            # maintenance / downtime windows for THIS unit
            maint_blocks = []
            unit_state = "available"
            for oc in outages:
                if oc["group"] != gid:
                    continue
                # a named unit matches that unit; a group-wide outage applies to all
                if oc["unit"] and oc["unit"] != unit.resource_id:
                    continue
                mb = _outage_block(oc)
                if mb:
                    maint_blocks.append(mb)
                    unit_state = oc["kind"]
            resource_rows.append({
                "name": unit.name, "group": grp.name,
                "state": unit_state, "blocks": unit_blocks,
                "maintenance": maint_blocks,
            })

    return jsonify({
        "dayStart": day_start.isoformat(),
        "now": now.isoformat(),
        "nowMin": mins_from_day_start(now),
        "shifts": [
            {"key": "A", "label": "Shift A", "time": "06:00–14:00", "startMin": 360, "endMin": 840},
            {"key": "B", "label": "Shift B", "time": "14:00–22:00", "startMin": 840, "endMin": 1320},
            {"key": "C", "label": "Shift C", "time": "22:00–06:00", "startMin": 1320, "endMin": 1800},
        ],
        "lineRows": line_rows,
        "downstreamRows": downstream_rows,
        "resourceRows": resource_rows,
    })


# --------------------------------------------------------------------------
# PLAN — forward multi-day schedule (week = per-stage bars by resource;
#        month = order-level bars by line). Real engine timeline.
# --------------------------------------------------------------------------
@frontend_bp.get("/api/plan")
def plan():
    from datetime import datetime, timedelta
    scope = request.args.get("scope", "week")   # week | month
    try:
        from engine.adapter import to_engine_order, STAGE_ID_TO_NAME
        from engine.scheduler import SchedulerEngine
        from engine import plant
    except Exception as exc:
        return jsonify(error=f"engine unavailable: {exc}"), 500

    Order, *_ = _models()
    now = datetime.utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    span_days = 7 if scope == "week" else 35
    span_min = span_days * 1440
    horizon = day_start + timedelta(days=span_days)

    rows = _active_orders(Order)
    _row_by_code = {r.code: r for r in rows}
    sched = SchedulerEngine(now).compute(
        [to_engine_order(r, now) for r in rows], [])

    def mins(dt):
        return int((dt - day_start).total_seconds() / 60)

    def clamp(s, e):
        return max(0, min(span_min, s)), max(0, min(span_min, e))

    line_names = {"PT": "Line 1", "TT": "Line 2", "DP": "Line 3", "LT": "Line 4"}
    STATUS = lambda o: o.status.value

    # day gridlines / labels
    day_marks = []
    for d in range(span_days + 1):
        dt = day_start + timedelta(days=d)
        day_marks.append({"min": d * 1440, "label": dt.strftime("%a %d"),
                          "weekend": dt.weekday() >= 5})

    if scope == "week":
        # per-stage bars, grouped by named resource (contention view)
        resource_rows = []
        groups = ["CAL", "BURNIN", "HELIUM", "ASSY", "TEST", "QC"]
        for gid in groups:
            grp = plant.RESOURCE_GROUPS.get(gid)
            if not grp:
                continue
            for unit in grp.members:
                bars = []
                for o in sched.orders:
                    for sid, st in o.stage_starts.items():
                        en = o.stage_ends.get(sid)
                        if not en or o.assigned_resource.get(sid) != unit.name:
                            continue
                        s, e = mins(st), mins(en)
                        if e < 0 or s > span_min:
                            continue
                        cs, ce = clamp(s, e)
                        bars.append({
                            "code": o.code, "product": o.product,
                            "stage": STAGE_ID_TO_NAME.get(sid, sid),
                            "startMin": cs, "endMin": ce,
                            "status": STATUS(o), "rush": o.rush,
                            "line": o.line.value})
                resource_rows.append({"name": unit.name, "group": grp.name,
                                      "bars": bars})
        return jsonify({
            "scope": "week", "dayStart": day_start.isoformat(),
            "spanDays": span_days, "spanMin": span_min,
            "nowMin": mins(now), "dayMarks": day_marks,
            "resourceRows": resource_rows,
        })

    # month: order-level bars grouped by line (commitment view)
    line_rows = []
    for lc in ("PT", "TT", "DP", "LT"):
        bars = []
        for o in sched.orders:
            if o.line.value != lc:
                continue
            starts = [v for v in o.stage_starts.values()]
            ends = [v for v in o.stage_ends.values()]
            if not starts or not ends:
                continue
            s, e = mins(min(starts)), mins(max(ends))
            if e < 0 or s > span_min:
                continue
            cs, ce = clamp(s, e)
            due_min = None
            if o.due:
                due_min = mins(o.due)
                if due_min < 0 or due_min > span_min:
                    due_min = max(0, min(span_min, due_min))
            bars.append({
                "code": o.code, "product": o.product, "qty": o.qty,
                "startMin": cs, "endMin": ce, "status": STATUS(o),
                "rush": o.rush, "dueMin": due_min,
                "due": o.due.strftime("%d %b") if o.due else None,
                "finish": o.projected_finish.strftime("%d %b") if o.projected_finish else None,
                "splitPart": getattr(_row_by_code.get(o.code), "split_part", None),
                "parentCode": getattr(_row_by_code.get(o.code), "parent_code", None),
            })
        line_rows.append({"key": lc, "name": line_names[lc],
                          "color": f"var(--line-{lc.lower()})", "bars": bars})
    return jsonify({
        "scope": "month", "dayStart": day_start.isoformat(),
        "spanDays": span_days, "spanMin": span_min,
        "nowMin": mins(now), "dayMarks": day_marks,
        "lineRows": line_rows,
    })


# --------------------------------------------------------------------------
# ORDER DETAIL — routing stepper + KC reference data + audit trail
# --------------------------------------------------------------------------
@frontend_bp.get("/api/order/<code>")
def order_detail(code):
    from datetime import datetime, timedelta
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    try:
        from engine.adapter import to_engine_order, STAGE_ID_TO_NAME
        from engine.scheduler import SchedulerEngine
        from engine import plant
    except Exception as exc:
        return jsonify(error=f"engine unavailable: {exc}"), 500

    row = Order.query.filter_by(code=code).first()
    if not row:
        return jsonify(error="not found"), 404

    now = datetime.utcnow()
    eo = to_engine_order(row, now)
    SchedulerEngine(now).compute([eo], [])

    route = plant.routing_for(eo.line)
    ids = [s.stage_id for s in route]
    cur = eo.current_stage_id
    cur_idx = ids.index(cur) if cur in ids else -1

    steps = []
    for i, s in enumerate(route):
        state = ("done" if (cur_idx >= 0 and i < cur_idx)
                 else "active" if i == cur_idx else "pending")
        steps.append({"stage": s.name, "stageId": s.stage_id, "state": state})

    def stage_time(sid):
        st = plant.STAGES.get(sid)
        return plant.per_unit_minutes(sid, eo.line) if st else None

    bi = stage_time("09B")
    reference = [
        {"label": "Cal temp", "value": "23 °C", "source": "Knowledge Centre"},
        {"label": "Burn-in", "value": f"{bi // 60 if bi else 48} h", "source": "Knowledge Centre"},
        {"label": "Cal cycle", "value": f"{stage_time('09') or 40} min", "source": "Knowledge Centre"},
        {"label": "Hydro", "value": "1.5×MAWP", "source": "Knowledge Centre"},
    ]

    events = (LogEvent.query
              .filter(LogEvent.title.contains(code) | LogEvent.detail.contains(code))
              .order_by(LogEvent.ts.desc()).limit(12).all())
    audit = [{"ts": e.ts.strftime("%d %b %H:%M") if e.ts else "",
              "title": e.title, "detail": e.detail,
              "actor": e.actor, "role": e.role, "kind": e.kind}
             for e in events]

    ago = None
    if getattr(row, "updated_at", None):
        m = int((now - row.updated_at).total_seconds() / 60)
        ago = f"{m}m ago" if m < 60 else (f"{m//60}h ago" if m < 1440 else f"{m//1440}d ago")

    # planned vs actual — progress measured by real stage DURATIONS, not by
    # wall-clock-since-start (which breaks if the schedule starts in the future).
    from engine.domain import TimeBasis
    total_stages = len(route)
    done_stages = cur_idx if cur_idx >= 0 else total_stages
    qty = row.qty or 1

    def _stage_minutes(s):
        if s.time_basis is TimeBasis.WALL_CLOCK:
            return s.setup_min + s.per_unit_min          # flat dwell (soak)
        return s.setup_min + s.per_unit_min * qty         # per-unit work

    elapsed_min = 0.0
    total_min = 0.0
    for i, s in enumerate(route):
        m = _stage_minutes(s)
        total_min += m
        if i < done_stages:            # fully-completed stages
            elapsed_min += m
    total_days = max(0.1, round(total_min / 1440, 1))
    day_of = round(elapsed_min / 1440, 1)
    if day_of > total_days:
        day_of = total_days

    # planned finish = due; projected finish = start + total lead time. Variance
    # is projected minus due, in days. Use the engine's projection when present,
    # else derive it from now + remaining work so it's never a wild date.
    due_dt = eo.due
    planned_finish = eo.projected_finish
    if planned_finish is None:
        remaining_min = max(0.0, total_min - elapsed_min)
        planned_finish = now + timedelta(minutes=remaining_min)

    variance_days = None
    on_time = None
    if planned_finish and due_dt:
        variance_days = round((planned_finish - due_dt).total_seconds() / 86400, 1)
        on_time = planned_finish <= due_dt

    # --- status + REASON: why is the order in this state? -----------------
    # For a halted / at-risk / rescheduled order, surface the cause: the most
    # relevant active constraint on this order, else the latest status-changing
    # audit entry (override/schedule/constraint).
    status_val = row.status
    status_info = {"status": status_val, "reason": None, "reasonType": None,
                   "since": None, "by": None, "ref": None}
    try:
        Constraint = _models()[1]
        con = (Constraint.query.filter_by(order_code=row.code)
               .order_by(Constraint.created_at.desc()).first())
        if con and con.note:
            status_info.update({
                "reason": con.note, "reasonType": con.ctype or "Constraint",
                "by": con.raised_by, "ref": con.code,
                "since": con.created_at.strftime("%d %b %H:%M") if con.created_at else None,
                "atStage": con.stage,
            })
    except Exception:
        pass
    # if no constraint gave a reason, fall back to the latest status-relevant
    # audit entry (override / schedule change / constraint).
    if not status_info["reason"]:
        for a in audit:
            if a.get("kind") in ("override", "schedule", "constraint", "approval"):
                status_info.update({
                    "reason": a.get("detail") or a.get("title"),
                    "reasonType": a.get("kind", "").capitalize() or "Update",
                    "by": a.get("actor"), "since": a.get("ts"),
                })
                break

    stress = status_val in ("HALTED", "AT RISK", "RESCHEDULED", "RUSH")

    # Every stressed order should carry a concrete reason. If neither a
    # constraint nor an audit entry supplied one, synthesise it from the order's
    # actual situation (variance vs due, current stage, bottleneck) so the panel
    # never shows a bare "no recorded reason".
    if stress and not status_info["reason"]:
        cur = row.current_stage or "its current stage"
        if status_val == "AT RISK":
            if variance_days and variance_days > 0:
                status_info["reason"] = (
                    f"Projected to finish about {abs(variance_days):.0f} day(s) "
                    f"after the due date — currently at {cur}, behind the pace "
                    f"needed to ship on time.")
            else:
                status_info["reason"] = (
                    f"Little schedule buffer left at {cur}; any further slip on "
                    f"this line puts the due date at risk.")
            status_info["reasonType"] = "Schedule risk"
        elif status_val == "HALTED":
            status_info["reason"] = (
                f"Work is stopped at {cur} pending a decision (quality hold or a "
                f"blocked resource). It won't progress until released.")
            status_info["reasonType"] = "Halted"
        elif status_val == "RESCHEDULED":
            status_info["reason"] = (
                f"Re-planned around a floor constraint; dates were shifted while "
                f"the order sits at {cur}.")
            status_info["reasonType"] = "Rescheduled"
        elif status_val == "RUSH":
            status_info["reason"] = (
                f"Flagged as a rush order — prioritised ahead of standard work "
                f"and expedited through {cur}.")
            status_info["reasonType"] = "Rush"
        status_info["atStage"] = row.current_stage

    analysis = {
        "dayOf": day_of, "totalDays": total_days,
        "stagesDone": done_stages, "stagesTotal": total_stages,
        "due": due_dt.strftime("%d %b") if due_dt else None,
        "projectedFinish": planned_finish.strftime("%d %b") if planned_finish else None,
        "varianceDays": variance_days, "onTime": on_time,
    }

    # batch-split lineage: parent shows its children; a child shows its parent
    # and sibling, so the panel makes the split explicit.
    split_info = None
    _into = [c for c in (getattr(row, "split_into", None) or "").split(",") if c]
    if _into:
        split_info = {"role": "parent", "children": _into}
    elif getattr(row, "parent_code", None):
        sib = [o.code for o in Order.query.filter_by(parent_code=row.parent_code).all()
               if o.code != row.code]
        split_info = {"role": "child", "part": row.split_part,
                      "parent": row.parent_code, "siblings": sib}

    return jsonify({
        "code": row.code, "product": row.product, "line": row.line_code,
        "lineName": row.line, "family": row.family, "qty": row.qty,
        "status": row.status, "source": (row.update_source or "erp").upper(),
        "updatedAgo": ago, "steps": steps, "reference": reference,
        "statuses": ["ON TRACK", "RUNNING", "RESCHEDULED", "AT RISK", "HALTED", "RUSH", "DONE"],
        "audit": audit, "analysis": analysis, "statusInfo": status_info,
        "splitInfo": split_info,
        "canSplit": (not _into and not getattr(row, "parent_code", None)
                     and (row.qty or 1) >= 2 and row.active is not False),
        "locked": bool(getattr(row, "locked", False)),
        "lockReason": getattr(row, "lock_reason", None),
        "qhold": bool(getattr(row, "qhold", False)),
        "qholdReason": getattr(row, "qhold_reason", None),
        "reworkStage": getattr(row, "rework_stage", None),
    })


@frontend_bp.post("/api/order/<code>/override")
def order_override(code):
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from models import log_event
    body = request.json or {}
    new_status = (body.get("status") or "").strip()
    reason = (body.get("reason") or "").strip()
    if not new_status or not reason:
        return jsonify(error="status and reason are both required"), 400
    row = Order.query.filter_by(code=code).first()
    if not row:
        return jsonify(error="not found"), 404
    old = row.status
    row.status = new_status
    row.update_source = "dept"
    db.session.commit()
    log_event("override", f"Planner override — {code}: {old} → {new_status}",
              f"Reason: {reason}", actor="Planner", role="Department Head")
    # STAGE 2: record into the analytics event stream
    try:
        from models import record_actual_event
        record_actual_event("override", order_code=code, line_code=row.line_code,
                            stage=row.current_stage, detail=f"{old}->{new_status}: {reason}",
                            actor="Planner")
    except Exception:
        pass
    return jsonify(ok=True, code=code, status=new_status)


# --------------------------------------------------------------------------
# FLOOR-INSIGHT DECISIONS — a head Applies or Rejects each insight, with an
# optional remark. Append-only history; latest row per insight_id is current.
# Everything is written to the audit log.
# --------------------------------------------------------------------------
@frontend_bp.get("/api/floor-insights/decisions")
def floor_insight_decisions():
    """Latest decision per insight_id, so the board can badge each card."""
    try:
        from models import InsightDecision
        rows = (InsightDecision.query
                .order_by(InsightDecision.ts.desc()).all())
    except Exception:
        return jsonify({})
    latest = {}
    for r in rows:                       # newest first -> first seen wins
        if r.insight_id not in latest:
            latest[r.insight_id] = r.to_dict()
    return jsonify(latest)


@frontend_bp.post("/api/floor-insights/decide")
def floor_insight_decide():
    """Record an Apply / Reject decision on a floor insight, with remarks."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from models import InsightDecision, log_event
    body = request.json or {}
    insight_id = (body.get("id") or "").strip()
    decision = (body.get("decision") or "").strip().lower()
    remarks = (body.get("remarks") or "").strip()
    title = (body.get("title") or "").strip()
    by = (body.get("by") or "Department Head").strip()
    role = (body.get("role") or "Department Head").strip()
    if not insight_id or decision not in ("applied", "rejected"):
        return jsonify(error="id and a decision of 'applied' or 'rejected' are required"), 400
    if decision == "rejected" and not remarks:
        return jsonify(error="remarks are required when rejecting an insight"), 400

    rec = InsightDecision(insight_id=insight_id, insight_title=title,
                          decision=decision, remarks=remarks,
                          decided_by=by, role=role)
    db.session.add(rec)
    db.session.commit()

    verb = "applied" if decision == "applied" else "rejected"
    log_event("schedule", f"Floor insight {verb} — {title or insight_id}",
              (f"Remarks: {remarks}" if remarks else
               ("Accepted by planner." if decision == "applied" else "Dismissed.")),
              actor=by, role=role)
    return jsonify(ok=True, decision=rec.to_dict())


# --------------------------------------------------------------------------
# MANUAL BOARD ORDERING — a head drags cards on a line to set the run order.
# Save persists the sequence (the board then honours it); Undo clears it back
# to the engine default. Both actions are audit-logged.
# --------------------------------------------------------------------------
@frontend_bp.post("/api/board/order")
def board_order_save():
    """Save a manual run-order for one line. Body: {line, sequence:[codes], by}."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from models import BoardOrder, log_event
    body = request.json or {}
    line_code = (body.get("line") or "").strip().upper()
    sequence = body.get("sequence") or []
    by = (body.get("by") or "Department Head").strip()
    role = (body.get("role") or "Department Head").strip()
    if line_code not in ("PT", "TT", "DP", "LT"):
        return jsonify(error="line must be one of PT, TT, DP, LT"), 400
    if not isinstance(sequence, list) or not sequence:
        return jsonify(error="a non-empty sequence of order codes is required"), 400
    sequence = [str(c).strip() for c in sequence if str(c).strip()]

    line_names = {"PT": "Line 1", "TT": "Line 2", "DP": "Line 3", "LT": "Line 4"}
    row = BoardOrder.query.filter_by(line_code=line_code).first()
    previous = row.codes() if row else None
    if row:
        row.sequence = ",".join(sequence)
        row.saved_by = by
        row.saved_at = datetime.utcnow()
    else:
        row = BoardOrder(line_code=line_code, sequence=",".join(sequence),
                         saved_by=by)
        db.session.add(row)
    db.session.commit()

    detail = f"{line_names.get(line_code, line_code)} run order → " + " · ".join(sequence)
    if previous and previous != sequence:
        detail += f"  (was {' · '.join(previous)})"
    log_event("schedule", f"Board re-ordered manually — {line_names.get(line_code, line_code)}",
              detail, actor=by, role=role)
    return jsonify(ok=True, order=row.to_dict())


@frontend_bp.post("/api/board/order/undo")
def board_order_undo():
    """Clear the manual order for one line -> revert to engine default."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from models import BoardOrder, log_event
    body = request.json or {}
    line_code = (body.get("line") or "").strip().upper()
    by = (body.get("by") or "Department Head").strip()
    role = (body.get("role") or "Department Head").strip()
    line_names = {"PT": "Line 1", "TT": "Line 2", "DP": "Line 3", "LT": "Line 4"}
    row = BoardOrder.query.filter_by(line_code=line_code).first()
    if not row:
        return jsonify(ok=True, cleared=False)   # nothing to undo
    db.session.delete(row)
    db.session.commit()
    log_event("schedule",
              f"Manual board order cleared — {line_names.get(line_code, line_code)}",
              "Reverted to the engine's default run order.", actor=by, role=role)
    return jsonify(ok=True, cleared=True)


@frontend_bp.get("/api/board/order")
def board_order_list():
    """Return all saved manual line orderings (for state on load)."""
    try:
        from models import BoardOrder
        return jsonify({b.line_code: b.to_dict() for b in BoardOrder.query.all()})
    except Exception:
        return jsonify({})


# --------------------------------------------------------------------------
# PRIORITY LOCK (#4) — a protected/prioritised order is locked; the board warns
# before it can be dragged/moved, and a head can override or clear the lock.
# --------------------------------------------------------------------------
@frontend_bp.post("/api/order/<code>/lock")
def order_lock(code):
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from models import log_event
    body = request.json or {}
    role = (body.get("role") or "Department Head").strip()
    by = (body.get("by") or "Department Head").strip()
    if role not in ("Department Head", "Admin"):
        return jsonify(error="only a Department Head or Admin can lock an order"), 403
    row = Order.query.filter_by(code=code).first()
    if not row:
        return jsonify(error="order not found"), 404
    row.locked = bool(body.get("locked", True))
    row.lock_reason = (body.get("reason") or "Protected by planner")[:160] if row.locked else None
    db.session.commit()
    log_event("schedule",
              f"{code} {'locked (protected)' if row.locked else 'unlocked'}",
              row.lock_reason or "Protection removed.", actor=by, role=role)
    return jsonify(ok=True, code=code, locked=row.locked)


# --------------------------------------------------------------------------
# QUALITY HOLD + REWORK (#5) — resolve a hold: release, or send to rework at a
# chosen earlier stage (the order steps back and re-runs from there).
# --------------------------------------------------------------------------
@frontend_bp.post("/api/order/<code>/quality")
def order_quality(code):
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from models import log_event, record_actual_event
    body = request.json or {}
    role = (body.get("role") or "Department Head").strip()
    by = (body.get("by") or "Department Head").strip()
    action = (body.get("action") or "").strip()   # release | rework | scrap
    if role not in ("Department Head", "Admin"):
        return jsonify(error="only a Department Head or Admin can resolve a hold"), 403
    row = Order.query.filter_by(code=code).first()
    if not row:
        return jsonify(error="order not found"), 404

    if action == "release":
        row.qhold = False; row.held = False; row.qhold_reason = None
        row.rework_stage = None
        row.status = "RUNNING"
        # also clear any active QUALITY_HOLD constraint on this order, otherwise
        # the scheduling engine keeps re-halting it and the board stays red.
        try:
            from scheduler_api import clear_quality_holds_for_order
            clear_quality_holds_for_order(code, db)
        except Exception:
            pass
        log_event("quality", f"{code} released from quality hold",
                  "Passed re-inspection; back into flow.", actor=by, role=role)
    elif action == "rework":
        stage = (body.get("stage") or "").strip()
        if not stage:
            return jsonify(error="a rework stage is required"), 400
        row.qhold = False; row.held = False
        row.rework_stage = stage
        row.current_stage = stage       # step the order back to rework
        row.status = "RESCHEDULED"
        row.qhold_reason = None
        log_event("quality", f"{code} sent to rework at {stage}",
                  (body.get("reason") or "Rework required after quality hold."),
                  actor=by, role=role)
        try:
            record_actual_event("rework", order_code=code, stage=stage,
                                 detail="rework after quality hold", actor=by)
        except Exception:
            pass
    elif action == "scrap":
        row.qhold = False; row.held = False; row.active = False
        row.status = "DONE"
        log_event("quality", f"{code} scrapped after quality hold",
                  (body.get("reason") or "Scrapped."), actor=by, role=role)
    else:
        return jsonify(error="action must be release, rework or scrap"), 400
    db.session.commit()
    return jsonify(ok=True, code=code, action=action,
                   status=row.status, stage=row.current_stage)


# --------------------------------------------------------------------------
# BATCH SPLIT — a head/admin splits one order into two real child orders that
# each show on the board and plan. Part A keeps the original line and current
# stage (the portion that can clear in time); Part B moves to a chosen line and
# restarts at Kitting (the part-ship). The parent is retired (active=False) so
# it drops off the live board; both children link back to it. Fully logged.
# --------------------------------------------------------------------------
@frontend_bp.post("/api/order/<code>/split")
def order_split(code):
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from models import log_event
    body = request.json or {}
    by = (body.get("by") or "Department Head").strip()
    role = (body.get("role") or "Department Head").strip()
    if role not in ("Department Head", "Admin"):
        return jsonify(error="only a Department Head or Admin can split an order"), 403

    parent = Order.query.filter_by(code=code).first()
    if not parent:
        return jsonify(error=f"order {code} not found"), 404
    if getattr(parent, "parent_code", None):
        return jsonify(error="this is already a split child and can't be split again"), 409
    if parent.active is False:
        return jsonify(error="order is not active"), 409
    if (parent.qty or 1) < 2:
        return jsonify(error="need at least 2 units to split"), 400

    line_names = {"PT": "Line 1", "TT": "Line 2", "DP": "Line 3", "LT": "Line 4"}
    line_codes = set(line_names.keys())

    # part-A percentage (default 60%); B line defaults to the same line
    try:
        pct_a = int(body.get("pct_a", 60))
    except (TypeError, ValueError):
        pct_a = 60
    pct_a = max(10, min(90, pct_a))
    line_b = (body.get("line_b") or parent.line_code or "PT").strip().upper()
    if line_b not in line_codes:
        return jsonify(error=f"line_b must be one of {sorted(line_codes)}"), 400

    total = parent.qty or 1
    qty_a = max(1, round(total * pct_a / 100))
    qty_b = total - qty_a
    if qty_b < 1:
        qty_a, qty_b = total - 1, 1

    code_a, code_b = f"{code}\u00b7A", f"{code}\u00b7B"
    if Order.query.filter(Order.code.in_([code_a, code_b])).first():
        return jsonify(error="split children already exist for this order"), 409

    line_b_name = line_names.get(line_b, line_b)
    now = datetime.utcnow()

    def _child(child_code, part, qty, line_code, line_name, stage):
        return Order(
            code=child_code, product=parent.product, family=parent.family,
            line_code=line_code, line=line_name, qty=qty,
            phase=parent.phase if part == "A" else "mfg",
            current_stage=stage,
            status=parent.status if part == "A" else "RUNNING",
            due=parent.due, promised=parent.promised,
            ship_ready=False, held=False, rush=parent.rush,
            update_source="dept", updated_by=by, updated_at=now,
            active=True, parent_code=code, split_part=part)

    # Part A: stays on the original line at the current stage (the runnable part)
    child_a = _child(code_a, "A", qty_a, parent.line_code, parent.line,
                     parent.current_stage)
    # Part B: the part-ship — moves to the chosen line, restarts at Kitting
    child_b = _child(code_b, "B", qty_b, line_b, line_b_name, "Kitting")

    # retire the parent
    parent.active = False
    parent.split_into = f"{code_a},{code_b}"
    parent.status = "RESCHEDULED"
    parent.update_source = "dept"
    parent.updated_by = by
    parent.updated_at = now

    db.session.add_all([child_a, child_b])
    db.session.commit()

    # --- per-part delivery dates -------------------------------------------
    # Re-run the engine on the two children and read each one's projected finish
    # from real stage durations, then commit dates per part:
    #   Part A keeps the customer DUE date (it's the on-time portion); its
    #          promised date is its own projection.
    #   Part B is the part-ship: both its due and promised move to its realistic
    #          projected finish (the remaining pieces slip, honestly dated).
    date_note = ""
    try:
        from engine.adapter import to_engine_order
        from engine.scheduler import SchedulerEngine
        eng = SchedulerEngine(now).compute(
            [to_engine_order(child_a, now), to_engine_order(child_b, now)], [])
        proj = {e.code: e.projected_finish for e in eng.orders}

        fa = proj.get(code_a)
        fb = proj.get(code_b)
        if fa:
            child_a.promised = fa.strftime("%d %b")
        if fb:
            child_b.promised = fb.strftime("%d %b")
            child_b.due = fb.strftime("%d %b")     # remaining batch's new committed date
            # if Part B now finishes after its (old) promise, it reads AT RISK
            if parent.due:
                child_b.status = "RESCHEDULED"
        db.session.commit()
        date_note = (f" Dates: {code_a} due {child_a.due or '—'}"
                     f" (proj {child_a.promised or '—'}); "
                     f"{code_b} due {child_b.due or '—'}.")
    except Exception:
        # dates are best-effort; the split itself already succeeded
        pass

    detail = (f"{code} ({total} pcs) split into "
              f"{code_a} ({qty_a} pcs on {parent.line}) + "
              f"{code_b} ({qty_b} pcs on {line_b_name}, restarts at Kitting)."
              + date_note)
    log_event("schedule", f"Order split — {code} \u2192 {code_a} + {code_b}",
              detail, actor=by, role=role)
    try:
        from models import record_actual_event
        record_actual_event("split", order_code=code, line_code=parent.line_code,
                            stage=parent.current_stage, detail=detail, actor=by)
    except Exception:
        pass

    return jsonify(ok=True, parent=code,
                   children=[child_a.to_dict(), child_b.to_dict()])


# --------------------------------------------------------------------------
# WHOLE-BOARD MOVE — a head drags an order onto a DIFFERENT line. This is a real
# reassignment: the order's line changes, its routing may differ (wet lines run
# helium leak + hydro; dry lines skip them), and its dates shift. Cross-line is
# always allowed; the preview spells out which tests are dropped/added so the
# head decides with eyes open. Nothing changes the live board until Approve.
# --------------------------------------------------------------------------
_LINE_NAMES = {"PT": "Line 1", "TT": "Line 2", "DP": "Line 3", "LT": "Line 4"}
_LINE_FAMILY = {"PT": "Pressure", "TT": "Temperature",
                "DP": "Diff. Pressure", "LT": "Level"}
_WET = {"PT", "DP"}


def _move_preview(row, target_lc, now):
    """Compute a before/after for moving `row` to line `target_lc`, without
    writing anything. Returns a dict the UI renders as a preview."""
    from engine.adapter import (to_engine_order, STAGE_NAME_TO_ID,
                                STAGE_ID_TO_NAME, LINE_MAP)
    from engine.scheduler import SchedulerEngine
    from engine import plant

    src_lc = row.line_code
    src_line = LINE_MAP.get(src_lc)
    tgt_line = LINE_MAP.get(target_lc)

    src_ids = [s.stage_id for s in plant.routing_for(src_line)] if src_line else []
    tgt_ids = [s.stage_id for s in plant.routing_for(tgt_line)] if tgt_line else []
    dropped = [STAGE_ID_TO_NAME.get(x, x) for x in src_ids if x not in tgt_ids]
    added = [STAGE_ID_TO_NAME.get(x, x) for x in tgt_ids if x not in src_ids]

    # where does the order land on the new line? keep the same stage if it still
    # exists there; otherwise snap to the nearest earlier stage that does.
    cur_id = STAGE_NAME_TO_ID.get(row.current_stage or "", None)
    new_stage_id = cur_id
    if cur_id not in tgt_ids:
        # find the position in the source route, walk back to a shared stage
        new_stage_id = None
        if cur_id in src_ids:
            for x in reversed(src_ids[:src_ids.index(cur_id) + 1]):
                if x in tgt_ids:
                    new_stage_id = x
                    break
        if new_stage_id is None and tgt_ids:
            new_stage_id = tgt_ids[0]
    new_stage_name = STAGE_ID_TO_NAME.get(new_stage_id, new_stage_id)

    # date impact: project current line vs target line
    def _finish(line_code, stage_id):
        clone = _clone_for_engine(row, line_code, stage_id)
        eng = SchedulerEngine(now).compute([to_engine_order(clone, now)], [])
        return eng.orders[0].projected_finish if eng.orders else None

    old_finish = _finish(src_lc, cur_id)
    new_finish = _finish(target_lc, new_stage_id)

    # wet/dry remark
    remark = None
    src_wet, tgt_wet = src_lc in _WET, target_lc in _WET
    if src_wet and not tgt_wet:
        remark = ("Moving from a wet line to a dry line: "
                  + (", ".join(dropped) if dropped else "some pressure tests")
                  + " will NOT be performed on this order.")
    elif not src_wet and tgt_wet:
        remark = ("Moving from a dry line to a wet line: "
                  + (", ".join(added) if added else "additional pressure tests")
                  + " will now be required.")

    return {
        "code": row.code, "product": row.product, "qty": row.qty,
        "fromLine": {"key": src_lc, "name": _LINE_NAMES.get(src_lc, src_lc),
                     "family": _LINE_FAMILY.get(src_lc, "")},
        "toLine": {"key": target_lc, "name": _LINE_NAMES.get(target_lc, target_lc),
                   "family": _LINE_FAMILY.get(target_lc, "")},
        "currentStage": row.current_stage,
        "newStage": new_stage_name, "newStageId": new_stage_id,
        "droppedStages": dropped, "addedStages": added,
        "oldDue": row.due, "oldFinish": old_finish.strftime("%d %b") if old_finish else None,
        "newFinish": new_finish.strftime("%d %b") if new_finish else None,
        "remark": remark,
    }


def _clone_for_engine(row, line_code, stage_id):
    """A throwaway ORM-like shim carrying the fields to_engine_order reads, so we
    can project a hypothetical (line, stage) without touching the DB row."""
    from engine.adapter import STAGE_ID_TO_NAME
    class _Shim:
        pass
    s = _Shim()
    s.code = row.code
    s.product = row.product
    s.line_code = line_code
    s.qty = row.qty
    s.due = row.due
    s.promised = row.promised
    s.rush = row.rush
    s.held = row.held
    s.status = row.status
    s.current_stage = STAGE_ID_TO_NAME.get(stage_id, stage_id)
    return s


@frontend_bp.post("/api/board/move/preview")
def board_move_preview():
    """Preview a cross-line move (no writes). Body: {code, line}."""
    Order, *_ = _models()
    body = request.json or {}
    code = (body.get("code") or "").strip()
    target = (body.get("line") or "").strip().upper()
    if target not in _LINE_NAMES:
        return jsonify(error="line must be PT, TT, DP or LT"), 400
    row = Order.query.filter_by(code=code).first()
    if not row:
        return jsonify(error=f"order {code} not found"), 404
    if row.active is False:
        return jsonify(error="order is not active"), 409
    if row.line_code == target:
        return jsonify(error="order is already on that line", noop=True), 200
    try:
        return jsonify(ok=True, preview=_move_preview(row, target, datetime.utcnow()))
    except Exception as exc:
        return jsonify(error=f"could not build preview: {exc}"), 500


@frontend_bp.post("/api/board/move/apply")
def board_move_apply():
    """Apply an approved cross-line move. Head/Admin only. Body: {code, line, by, role}."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from models import log_event
    body = request.json or {}
    by = (body.get("by") or "Department Head").strip()
    role = (body.get("role") or "Department Head").strip()
    if role not in ("Department Head", "Admin"):
        return jsonify(error="only a Department Head or Admin can move an order"), 403
    code = (body.get("code") or "").strip()
    target = (body.get("line") or "").strip().upper()
    if target not in _LINE_NAMES:
        return jsonify(error="line must be PT, TT, DP or LT"), 400
    row = Order.query.filter_by(code=code).first()
    if not row:
        return jsonify(error=f"order {code} not found"), 404
    if row.active is False:
        return jsonify(error="order is not active"), 409
    if row.line_code == target:
        return jsonify(ok=True, noop=True)
    # #4: a locked/protected order needs an explicit override to move
    if getattr(row, "locked", False) and not (request.json or {}).get("override"):
        return jsonify(error="locked", locked=True,
                       reason=row.lock_reason or "This order is protected.",
                       needsOverride=True), 409

    now = datetime.utcnow()
    pv = _move_preview(row, target, now)
    old_line = row.line
    # snapshot for undo
    prev = {"line_code": row.line_code, "line": row.line,
            "stage": row.current_stage, "promised": row.promised,
            "status": row.status}

    # perform the move
    row.line_code = target
    row.line = _LINE_NAMES.get(target, target)
    if pv["newStage"] and pv["newStage"] != row.current_stage:
        row.current_stage = pv["newStage"]
    if pv.get("newFinish"):
        row.promised = pv["newFinish"]
        # if it now finishes after its due date, reflect the slip
        row.status = "RESCHEDULED"
    row.update_source = "dept"
    row.updated_by = by
    row.updated_at = now

    # a manual cross-line move invalidates any saved run-order on either line
    try:
        from models import BoardOrder
        BoardOrder.query.filter(
            BoardOrder.line_code.in_([pv["fromLine"]["key"], target])).delete(
            synchronize_session=False)
    except Exception:
        pass
    db.session.commit()

    detail = (f"{code} moved {old_line} \u2192 {row.line}"
              f" (now at {row.current_stage}).")
    if pv.get("droppedStages"):
        detail += " Tests not performed: " + ", ".join(pv["droppedStages"]) + "."
    if pv.get("addedStages"):
        detail += " Tests now required: " + ", ".join(pv["addedStages"]) + "."
    if pv.get("newFinish"):
        detail += f" Projected finish {pv['newFinish']}."
    log_event("schedule", f"Order moved across lines — {code}", detail,
              actor=by, role=role)
    try:
        from models import record_actual_event
        record_actual_event("move", order_code=code, line_code=target,
                            stage=row.current_stage, detail=detail, actor=by)
    except Exception:
        pass
    return jsonify(ok=True, code=code, line=row.line, stage=row.current_stage,
                   finish=row.promised, previous=prev)


@frontend_bp.post("/api/board/move/undo")
def board_move_undo():
    """Undo the last cross-line move: put the order back on its previous line,
    stage, promised date and status. Head/Admin only.
    Body: {code, previous:{line_code,stage,promised,status}, by, role}."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from models import log_event
    body = request.json or {}
    role = (body.get("role") or "Department Head").strip()
    by = (body.get("by") or "Department Head").strip()
    if role not in ("Department Head", "Admin"):
        return jsonify(error="only a Department Head or Admin can undo a move"), 403
    code = (body.get("code") or "").strip()
    prev = body.get("previous") or {}
    row = Order.query.filter_by(code=code).first()
    if not row:
        return jsonify(error=f"order {code} not found"), 404
    if not prev.get("line_code"):
        return jsonify(error="no previous state to undo to"), 400
    from_line = row.line
    row.line_code = prev["line_code"]
    row.line = prev.get("line") or _LINE_NAMES.get(prev["line_code"], prev["line_code"])
    if prev.get("stage"):
        row.current_stage = prev["stage"]
    if prev.get("promised"):
        row.promised = prev["promised"]
    if prev.get("status"):
        row.status = prev["status"]
    row.updated_by = by
    row.updated_at = datetime.utcnow()
    db.session.commit()
    log_event("schedule", f"Move undone — {code}",
              f"{code} moved back {from_line} \u2192 {row.line} (undo).",
              actor=by, role=role)
    return jsonify(ok=True, code=code, line=row.line)


@frontend_bp.get("/api/actuals")
def actuals():
    """Read the accumulated measured actuals — the Stage-2 floor history.
    This is what the history-aware insights layer will consume."""
    from models import StageActual, ActualEvent
    stage_rows = (StageActual.query.filter_by(superseded_by=None)
                  .order_by(StageActual.recorded_at.desc()).limit(200).all())
    events = ActualEvent.query.order_by(ActualEvent.ts.desc()).limit(200).all()

    # quick variance summary per stage (actual vs estimate) — real, measured
    from collections import defaultdict
    agg = defaultdict(lambda: {"n": 0, "dur": 0, "est": 0})
    for r in stage_rows:
        if r.duration_min and r.estimate_min:
            a = agg[r.stage]
            a["n"] += 1
            a["dur"] += r.duration_min
            a["est"] += r.estimate_min
    variance = []
    for stage, a in agg.items():
        if a["n"]:
            avg_dur = a["dur"] / a["n"]
            avg_est = a["est"] / a["n"]
            variance.append({
                "stage": stage, "samples": a["n"],
                "avgActualMin": round(avg_dur),
                "avgEstimateMin": round(avg_est),
                "variancePct": round((avg_dur - avg_est) / avg_est * 100) if avg_est else None,
            })

    return jsonify({
        "measured": True,
        "note": "Measured floor actuals (Stage 2). Distinct from engine estimates "
                "and from synthetic history. Once enough accrues, this drives the "
                "insights baselines.",
        "stageActuals": [r.to_dict() for r in stage_rows],
        "events": [e.to_dict() for e in events],
        "stageVariance": variance,
        "counts": {"stageActuals": StageActual.query.count(),
                   "events": ActualEvent.query.count()},
    })


# --------------------------------------------------------------------------
# ASSIGNMENTS + NOTIFICATIONS — head assigns work; employee gets notified
# --------------------------------------------------------------------------
@frontend_bp.post("/api/assign")
def assign_task():
    """A head assigns {order, stage} to an employee. Creates an Assignment and
    a Notification for that employee."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from models import notify, log_event
    from models import Assignment
    body = request.json or {}
    order_code = (body.get("order") or "").strip()
    stage = (body.get("stage") or "").strip()
    emp_id = body.get("employeeId")
    by = (body.get("by") or "Department Head").strip()
    if not order_code or not stage or not emp_id:
        return jsonify(error="order, stage, and employee are all required"), 400

    emp = User.query.get(emp_id) if hasattr(User, "query") else None
    emp_name = emp.name if emp else (body.get("employeeName") or "Employee")

    # SKILL GATE: the operator must be trained for this stage. A QC inspector
    # can't be assigned Calibration, etc. Look up the operator's skill by name.
    Operator = _models()[3]
    op = Operator.query.filter_by(name=emp_name).first()
    emp_skill = op.skill if op else None
    if op and not operator_can_do(op, stage):
        needed = STAGE_SKILL.get(stage, [])
        return jsonify(
            error=f"{emp_name} is a {emp_skill} and isn't trained for {stage}. "
                  f"{stage} needs: {', '.join(needed) or 'a certified operator'}. "
                  f"Train them in {stage} first, then assign.",
            skillMismatch=True, need=needed, has=emp_skill), 400

    a = Assignment(order_code=order_code, stage=stage, employee_id=emp_id,
                   employee_name=emp_name, assigned_by=by, source="head",
                   status="assigned")
    db.session.add(a)
    db.session.commit()

    notify(emp_id, f"New task: {order_code} · {stage}",
           user_name=emp_name,
           detail=f"{by} assigned you {stage} on {order_code}. Open your "
                  f"confirmations to start the checklist.",
           order_code=order_code, stage=stage)
    log_event("assign", f"{order_code} · {stage} → {emp_name}",
              f"Assigned by {by}", actor=by, role="Department Head")
    return jsonify(ok=True, assignment=a.to_dict())


@frontend_bp.post("/api/unassign")
def api_unassign():
    """Remove an active assignment from an employee. Head/Admin only."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from models import Assignment, log_event
    body = request.json or {}
    by = (body.get("by") or "Department Head").strip()
    order_code = (body.get("order") or "").strip()
    stage = (body.get("stage") or "").strip()
    emp_id = body.get("employeeId")
    q = Assignment.query.filter_by(order_code=order_code, stage=stage)
    if emp_id:
        q = q.filter_by(employee_id=emp_id)
    removed = 0
    for a in q.filter(Assignment.status.in_(["assigned", "accepted", "started"])).all():
        a.status = "cancelled"
        removed += 1
    db.session.commit()
    if removed:
        log_event("assign", f"{order_code} · {stage} unassigned",
                  f"Removed by {by}.", actor=by, role="Department Head")
    return jsonify(ok=True, removed=removed)
def self_assign_task():
    """An employee picks up an unassigned {order, stage} themselves."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from models import log_event
    from models import Assignment
    body = request.json or {}
    order_code = (body.get("order") or "").strip()
    stage = (body.get("stage") or "").strip()
    emp_id = body.get("employeeId")
    emp_name = (body.get("employeeName") or "Employee").strip()
    if not order_code or not stage or not emp_id:
        return jsonify(error="order, stage, and employee are required"), 400
    a = Assignment(order_code=order_code, stage=stage, employee_id=emp_id,
                   employee_name=emp_name, assigned_by="self", source="self",
                   status="assigned")
    db.session.add(a)
    db.session.commit()
    log_event("assign", f"{order_code} · {stage} self-selected by {emp_name}",
              "Employee self-assignment", actor=emp_name, role="Employee")
    return jsonify(ok=True, assignment=a.to_dict())


@frontend_bp.get("/api/notifications")
def get_notifications():
    """Notifications for a user (poll from the client). ?userId=N&unread=1"""
    from models import Notification
    uid = request.args.get("userId", type=int)
    q = Notification.query
    if uid is not None:
        q = q.filter_by(user_id=uid)
    if request.args.get("unread"):
        q = q.filter_by(read=False)
    rows = q.order_by(Notification.created_at.desc()).limit(30).all()
    return jsonify({"notifications": [n.to_dict() for n in rows],
                    "unread": sum(1 for n in rows if not n.read)})


@frontend_bp.post("/api/notifications/read")
def mark_notifications_read():
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from models import Notification
    body = request.json or {}
    uid = body.get("userId")
    ids = body.get("ids")
    q = Notification.query
    if ids:
        q = q.filter(Notification.id.in_(ids))
    elif uid is not None:
        q = q.filter_by(user_id=uid)
    for n in q.all():
        n.read = True
    db.session.commit()
    return jsonify(ok=True)


@frontend_bp.get("/api/my-assignments")
def my_assignments():
    """Assignments for one employee (their task list)."""
    from models import Assignment
    uid = request.args.get("userId", type=int)
    q = Assignment.query
    if uid is not None:
        q = q.filter_by(employee_id=uid)
    rows = q.order_by(Assignment.created_at.desc()).limit(30).all()
    return jsonify({"assignments": [a.to_dict() for a in rows]})


# --------------------------------------------------------------------------
# MANPOWER — who's on shift, current assignment, load, skills, capacity
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# STAGE COMPLETION GATE — employee submits checklist -> head approves/rejects.
# Only APPROVE advances the order on the live board to the next stage.
# --------------------------------------------------------------------------
def _next_stage_in_routing(order):
    """The stage after the order's current one, per its LINE's routing (respects
    dry/wet differences). Returns None if it's the last stage."""
    from engine.plant import routing_for
    from engine.adapter import LINE_MAP
    line = LINE_MAP.get(order.line_code)
    if not line:
        return None
    names = [s.name for s in routing_for(line)]
    cur = order.current_stage
    idx = None
    for i, n in enumerate(names):
        if cur and (n == cur or cur.lower() in n.lower() or n.lower().startswith(cur.lower())):
            idx = i
            break
    if idx is None or idx + 1 >= len(names):
        return None
    return names[idx + 1]


@frontend_bp.post("/api/stage/submit")
def stage_submit():
    """Employee submits a completed checklist for a stage -> awaits head approval.
    Notifies the department head(s)."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from models import StageSubmission, log_event, notify
    body = request.json or {}
    order_code = (body.get("order") or "").strip()
    stage = (body.get("stage") or "").strip()
    by = (body.get("by") or "Employee").strip()
    by_id = body.get("byId")
    done = int(body.get("itemsDone") or 0)
    total = int(body.get("itemsTotal") or 0)
    if not order_code or not stage:
        return jsonify(error="order and stage are required"), 400

    sub = StageSubmission(order_code=order_code, stage=stage, submitted_by=by,
                          submitted_by_id=by_id, status="submitted",
                          items_done=done, items_total=total)
    db.session.add(sub)
    db.session.commit()
    log_event("confirm", f"{order_code} · {stage} submitted for approval",
              f"{done}/{total} checklist items done", actor=by, role="Employee")
    # notify department heads
    for h in User.query.filter_by(role="Department Head").all():
        notify(h.id, f"Approval needed: {order_code} · {stage}",
               user_name=h.name,
               detail=f"{by} submitted {stage} on {order_code} ({done}/{total} items). "
                      f"Approve to complete the stage, or reject with remarks.",
               kind="approval", order_code=order_code, stage=stage)
    return jsonify(ok=True, submission=sub.to_dict())


@frontend_bp.get("/api/stage/submissions")
def stage_submissions():
    """Pending (and recent) stage submissions for the head to review."""
    from models import StageSubmission
    status = request.args.get("status")
    q = StageSubmission.query
    if status:
        q = q.filter_by(status=status)
    rows = q.order_by(StageSubmission.submitted_at.desc()).limit(50).all()
    return jsonify({"submissions": [s.to_dict() for s in rows]})


@frontend_bp.post("/api/stage/approve")
def stage_approve():
    """Head APPROVES a submission -> the stage completes on the live board and
    the order advances to the next stage in its routing."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from models import StageSubmission, log_event, notify, record_stage_actual
    body = request.json or {}
    sub_id = body.get("id")
    by = (body.get("by") or "Department Head").strip()
    sub = StageSubmission.query.get(sub_id) if sub_id else None
    if not sub or sub.status != "submitted":
        return jsonify(error="no pending submission with that id"), 404

    order = Order.query.filter_by(code=sub.order_code).first()
    if not order:
        return jsonify(error=f"order {sub.order_code} not found"), 404

    # advance the order on the LIVE board
    nxt = _next_stage_in_routing(order)
    prev_stage = order.current_stage
    if nxt:
        order.current_stage = nxt
    else:
        order.phase = "closed"           # last stage done -> order complete
    order.update_source = "floor"
    order.updated_by = by
    order.updated_at = datetime.utcnow()

    sub.status = "approved"
    sub.reviewed_by = by
    sub.reviewed_at = datetime.utcnow()
    db.session.commit()

    # record a measured stage-actual (Stage-2 data) for the completed stage
    try:
        record_stage_actual(sub.order_code, prev_stage, line_code=order.line_code,
                            product=order.product, outcome="pass", operator=sub.submitted_by)
    except Exception:
        pass

    log_event("approval", f"{sub.order_code} · {prev_stage} approved",
              (f"Advanced to {nxt}." if nxt else "Order completed.") +
              f" Approved by {by}.", actor=by, role="Department Head")
    # notify the employee their stage was approved
    if sub.submitted_by_id:
        notify(sub.submitted_by_id,
               f"Approved: {sub.order_code} · {prev_stage}",
               detail=(f"Your {prev_stage} work was approved. " +
                       (f"The order is now at {nxt}." if nxt else "The order is complete.")),
               kind="approval", order_code=sub.order_code, stage=prev_stage)
    return jsonify(ok=True, advancedTo=nxt, completed=(nxt is None))


@frontend_bp.post("/api/stage/reject")
def stage_reject():
    """Head REJECTS a submission with remarks -> back to the employee; the board
    does NOT advance."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from models import StageSubmission, log_event, notify
    body = request.json or {}
    sub_id = body.get("id")
    by = (body.get("by") or "Department Head").strip()
    remarks = (body.get("remarks") or "").strip()
    if not remarks:
        return jsonify(error="remarks are required when rejecting"), 400
    sub = StageSubmission.query.get(sub_id) if sub_id else None
    if not sub or sub.status != "submitted":
        return jsonify(error="no pending submission with that id"), 404

    sub.status = "rejected"
    sub.reviewed_by = by
    sub.remarks = remarks
    sub.reviewed_at = datetime.utcnow()
    db.session.commit()

    log_event("approval", f"{sub.order_code} · {sub.stage} rejected",
              f"Sent back to {sub.submitted_by}. Remarks: {remarks}",
              actor=by, role="Department Head")
    if sub.submitted_by_id:
        notify(sub.submitted_by_id,
               f"Rework needed: {sub.order_code} · {sub.stage}",
               detail=f"{by} sent this back. Remarks: {remarks}",
               kind="approval", order_code=sub.order_code, stage=sub.stage)
    return jsonify(ok=True)


# --------------------------------------------------------------------------
# SKILL -> STAGE gating (existing)
# --------------------------------------------------------------------------
def _skill_gate_marker():
    pass


@frontend_bp.post("/api/manpower/add")
def manpower_add():
    """Add an operator to a shift. Because one person can work two shifts, this
    can either create a new operator or place an existing operator's name onto a
    second shift (a second Operator row with the same name)."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from models import log_event
    body = request.json or {}
    name = (body.get("name") or "").strip()
    shift = (body.get("shift") or "").strip().upper()
    skill = (body.get("skill") or "Assembly").strip()
    if not name or shift not in ("A", "B", "C"):
        return jsonify(error="name and a valid shift (A/B/C) are required"), 400

    # next operator code
    n = Operator.query.count() + 1
    code = f"OP-{n:02d}"
    # initials from the name
    parts = name.split()
    initials = (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()

    op = Operator(code=code, name=name, skill=skill, shift=shift,
                  assigned_stage=None)
    db.session.add(op)
    db.session.commit()
    log_event("manpower", f"{name} added to Shift {shift}",
              f"Skill: {skill}", actor="Department Head", role="Department Head")
    return jsonify(ok=True, operator={"code": code, "name": name,
                                      "shift": shift, "skill": skill})


@frontend_bp.get("/api/manpower")
def manpower():
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    ops = Operator.query.order_by(Operator.shift, Operator.code).all()

    # who's on which shift, with their assignment
    from models import Assignment
    assigns_by_name = {}
    for a in Assignment.query.filter(
            Assignment.status.in_(["assigned", "accepted", "started"])).all():
        assigns_by_name.setdefault(a.employee_name, []).append(
            {"order": a.order_code, "stage": a.stage, "status": a.status})
    shifts = {"A": [], "B": [], "C": []}
    # map operator name -> user id so the frontend can assign/notify the RIGHT
    # person (operator id and user id are different sequences; using the operator
    # id as the notify target sends the notification to the wrong user).
    _uid_by_name = {u.name: u.id for u in User.query.all()}
    for o in ops:
        base = stages_for_skill(o.skill)
        working = assigns_by_name.get(o.name, [])
        shifts.setdefault(o.shift or "?", []).append({
            "id": o.id, "userId": _uid_by_name.get(o.name),
            "code": o.code, "name": o.name, "initials": o.initials,
            "skill": o.skill, "assignedStage": o.assigned_stage,
            "canDo": operator_stages(o),
            "baseStages": base,
            "extraStages": _extra_stages(o),
            "working": working,                       # real assignments
            "idle": o.assigned_stage is None and not working,
            "absent": bool(getattr(o, "absent", False)),
            "absentNote": getattr(o, "absent_note", None),
        })

    # skills / certification matrix: which skills cover which stages
    skills = {}
    for o in ops:
        skills.setdefault(o.skill or "Other", []).append(o.name)
    matrix = []
    for stage, needed in STAGE_SKILL.items():
        # someone covers a stage via their base skill OR an extra trained task
        covering = [o.name for o in ops if operator_can_do(o, stage)]
        matrix.append({"stage": stage, "skills": needed,
                       "operators": covering, "count": len(covering),
                       "thin": len(covering) <= 1})

    # capacity: demand per line vs operators available for its stages
    orders = Order.query.all()
    from collections import Counter
    line_demand = Counter(o.line_code for o in orders
                          if o.phase not in ("shipped", "closed"))
    capacity = []
    for lc, label in [("PT", "Line 1"), ("TT", "Line 2"),
                      ("DP", "Line 3"), ("LT", "Line 4")]:
        capacity.append({"line": label, "code": lc,
                         "activeOrders": line_demand.get(lc, 0)})

    idle_ops = [o.name for o in ops if o.assigned_stage is None]

    # employees the head can assign to (users with the Employee role). We attach
    # each one's skill (matched to their operator record by name) and the stages
    # that skill is trained for — so the UI only offers valid task/operator pairs.
    op_by_name = {o.name: o for o in ops}
    employees = []
    try:
        for u in User.query.all():
            if getattr(u, "role", "") == "Employee":
                op = op_by_name.get(u.name)
                employees.append({"id": u.id, "name": u.name,
                                  "skill": op.skill if op else None,
                                  "canDo": operator_stages(op) if op else []})
    except Exception:
        pass

    # assignable work: active orders + their current stage
    assignable = []
    for o in Order.query.all():
        if o.phase not in ("shipped", "closed"):
            assignable.append({"order": o.code, "stage": o.current_stage,
                               "product": o.product, "line": o.line})

    return jsonify({
        "shifts": shifts,
        "shiftTimes": {"A": "06:00–14:00", "B": "14:00–22:00", "C": "22:00–06:00"},
        "skillMatrix": matrix,
        "skillGroups": skills,
        "capacity": capacity,
        "totals": {"operators": len(ops), "idle": len(idle_ops),
                   "onShift": len([o for o in ops if o.assigned_stage])},
        "idleOperators": idle_ops,
        "employees": employees,
        "assignableWork": assignable,
        "allStages": list(STAGE_SKILL.keys()),   # every trainable task
    })


@frontend_bp.post("/api/manpower/train")
def manpower_train():
    """A head trains an operator in an extra task (once they've completed
    training). Adds the stage to the operator's extra_skills so they can then
    be assigned that stage. Head/Admin only. Body: {operatorId, stage, by, role}."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from models import log_event, notify
    body = request.json or {}
    by = (body.get("by") or "Department Head").strip()
    role = (body.get("role") or "Department Head").strip()
    if role not in ("Department Head", "Admin"):
        return jsonify(error="only a Department Head or Admin can record training"), 403
    op_id = body.get("operatorId")
    stage = (body.get("stage") or "").strip()
    if not op_id or not stage:
        return jsonify(error="operatorId and stage are required"), 400
    if stage not in STAGE_SKILL:
        return jsonify(error=f"'{stage}' is not a known task"), 400
    op = Operator.query.get(op_id)
    if not op:
        return jsonify(error="operator not found"), 404

    # already able to do it (base skill or already trained)?
    if operator_can_do(op, stage):
        return jsonify(ok=True, alreadyTrained=True,
                       canDo=operator_stages(op),
                       extraStages=_extra_stages(op))

    extras = _extra_stages(op)
    extras.append(stage)
    op.extra_skills = ",".join(extras)
    db.session.commit()

    log_event("manpower", f"{op.name} trained in {stage}",
              f"{by} recorded {op.name} as trained/certified for {stage}. "
              f"They can now be assigned {stage}.", actor=by, role=role)
    # let the operator know they're now certified for the task
    try:
        u = User.query.filter_by(name=op.name).first()
        if u:
            notify(u.id, f"You're now trained for {stage}",
                   user_name=op.name,
                   detail=f"{by} certified you for {stage}. You may be assigned "
                          f"this task going forward.", kind="assignment")
    except Exception:
        pass

    return jsonify(ok=True, operator=op.name, stage=stage,
                   canDo=operator_stages(op), extraStages=_extra_stages(op))


@frontend_bp.post("/api/manpower/untrain")
def manpower_untrain():
    """Remove an extra trained task from an operator (base-skill stages can't be
    removed). Head/Admin only. Body: {operatorId, stage, by, role}."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from models import log_event
    body = request.json or {}
    role = (body.get("role") or "Department Head").strip()
    by = (body.get("by") or "Department Head").strip()
    if role not in ("Department Head", "Admin"):
        return jsonify(error="only a Department Head or Admin can change training"), 403
    op = Operator.query.get(body.get("operatorId"))
    stage = (body.get("stage") or "").strip()
    if not op:
        return jsonify(error="operator not found"), 404
    extras = _extra_stages(op)
    if stage in extras:
        extras.remove(stage)
        op.extra_skills = ",".join(extras)
        db.session.commit()
        log_event("manpower", f"{op.name} training removed — {stage}",
                  f"{by} removed {stage} from {op.name}'s extra tasks.",
                  actor=by, role=role)
    return jsonify(ok=True, canDo=operator_stages(op), extraStages=_extra_stages(op))


@frontend_bp.get("/api/optimizations")
def api_optimizations():
    """Optimization suggestions for the dashboard, grouped into three families:
      - floor    : line/order moves (from floor_insights)
      - manpower : staffing moves (from optimization_suggestions)
      - business : throughput/cost actions (from the insights 'actions')
    Each item: {id, title, detail, gain}. Items already applied/rejected (in
    InsightDecision) are filtered out so decided suggestions don't reappear."""
    import services, hashlib
    def _mkid(prefix, title):
        h = hashlib.md5((title or "").encode()).hexdigest()[:8]
        return f"{prefix}-{h}"
    # decided ids to exclude
    decided = set()
    try:
        from models import InsightDecision
        for d in InsightDecision.query.all():
            decided.add(d.insight_id)
    except Exception:
        pass
    floor, manpower, business = [], [], []
    try:
        for i in services.floor_insights():
            iid = _mkid("floor", i.get("title"))
            if iid in decided:
                continue
            floor.append({"id": iid, "title": i.get("title"), "detail": i.get("detail"),
                          "gain": i.get("gain"), "ref": i.get("ref")})
    except Exception:
        pass
    try:
        for s in services.optimization_suggestions():
            iid = _mkid("manpower", s.get("title"))
            if iid in decided:
                continue
            manpower.append({"id": iid, "title": s.get("title"), "detail": s.get("detail"),
                             "gain": s.get("impact") or s.get("effort")})
    except Exception:
        pass
    try:
        from models import MonthlyReport
        reports = MonthlyReport.query.order_by(MonthlyReport.id).all()
    except Exception:
        reports = []
    try:
        charts = services.insight_charts(reports)
        for a in (charts.get("actions") or []):
            iid = _mkid("business", a.get("title"))
            if iid in decided:
                continue
            business.append({"id": iid, "title": a.get("title"), "detail": a.get("detail"),
                             "gain": a.get("gain")})
    except Exception:
        pass
    return jsonify({
        "floor": floor, "manpower": manpower, "business": business,
        "counts": {"floor": len(floor), "manpower": len(manpower),
                   "business": len(business)},
    })


@frontend_bp.get("/api/my-work")
def api_my_work():
    """The logged-in employee's work history + productivity summary, built from
    their stage submissions (what they did, the shift, and whether it was
    approved or rejected)."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from models import StageSubmission
    from flask import session
    uid = session.get("user_id")
    user = User.query.get(uid) if uid else None
    name = user.name if user else None
    q = StageSubmission.query
    if name:
        q = q.filter((StageSubmission.submitted_by == name) |
                     (StageSubmission.submitted_by_id == uid))
    rows = q.order_by(StageSubmission.submitted_at.desc()).limit(200).all()

    def shift_of(dt):
        if not dt:
            return "-"
        h = dt.hour
        return "A" if 6 <= h < 14 else "B" if 14 <= h < 22 else "C"

    items = []
    approved = rejected = pending = 0
    for r in rows:
        if r.status == "approved":
            approved += 1
        elif r.status == "rejected":
            rejected += 1
        else:
            pending += 1
        items.append({
            "order": r.order_code, "stage": r.stage, "status": r.status,
            "shift": shift_of(r.submitted_at),
            "itemsDone": r.items_done, "itemsTotal": r.items_total,
            "submittedAt": r.submitted_at.isoformat() if r.submitted_at else None,
            "reviewedBy": r.reviewed_by, "remarks": r.remarks,
        })
    total = len(rows)
    decided = approved + rejected
    approval_rate = round(approved / decided * 100) if decided else None
    return jsonify({
        "name": name,
        "summary": {
            "total": total, "approved": approved, "rejected": rejected,
            "pending": pending, "approvalRate": approval_rate,
        },
        "items": items,
    })


@frontend_bp.get("/api/rework")
def api_rework():
    """Orders currently in rework (sent back after a quality hold) — shown in the
    employee dashboard so the floor sees what needs re-doing and at which stage."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    out = []
    try:
        rows = Order.query.filter(Order.rework_stage.isnot(None)).all()
    except Exception:
        rows = []
    for r in rows:
        if r.active is False:
            continue
        out.append({
            "code": r.code, "product": r.product, "line": r.line,
            "reworkStage": r.rework_stage, "currentStage": r.current_stage,
            "status": r.status, "qty": r.qty,
            "reason": getattr(r, "qhold_reason", None) or "Sent back for rework after a quality check.",
        })
    return jsonify(out)


@frontend_bp.get("/api/manpower/attention")
def manpower_attention():
    """Recent manpower events (absences, reassignments) for the head's
    Needs-Attention panel."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from datetime import datetime
    rows = (LogEvent.query
            .filter(LogEvent.kind.in_(["manpower", "assignment"]))
            .order_by(LogEvent.ts.desc())
            .limit(8).all())
    out = []
    now = datetime.utcnow()
    for e in rows:
        mins = int((now - e.ts).total_seconds() // 60) if e.ts else 0
        ago = ("just now" if mins < 1 else f"{mins}m ago" if mins < 60
               else f"{mins // 60}h ago" if mins < 1440 else f"{mins // 1440}d ago")
        out.append({"title": e.title, "detail": e.detail, "by": e.actor,
                    "ago": ago, "kind": e.kind})
    return jsonify(out)


@frontend_bp.post("/api/manpower/absence")
def manpower_absence():
    """Mark an operator absent or present (Head/Admin). Body:
    {operatorId, absent:bool, note, by, role}."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from models import log_event
    body = request.json or {}
    role = (body.get("role") or "Department Head").strip()
    by = (body.get("by") or "Department Head").strip()
    if role not in ("Department Head", "Admin"):
        return jsonify(error="only a Department Head or Admin can set absence"), 403
    op = Operator.query.get(body.get("operatorId"))
    if not op:
        return jsonify(error="operator not found"), 404
    op.absent = bool(body.get("absent", True))
    op.absent_note = (body.get("note") or "Unavailable")[:120] if op.absent else None
    db.session.commit()
    log_event("manpower",
              f"{op.name} marked {'absent' if op.absent else 'present'}",
              op.absent_note or "Back on the floor.", actor=by, role=role)
    return jsonify(ok=True, operator=op.name, absent=op.absent)


@frontend_bp.get("/api/manpower/reassign-candidates/<code>/<stage>")
def reassign_candidates(code, stage):
    """Operators trained for `stage` and NOT absent — used to offer inline
    reassignment of an absent person's task."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    out = []
    for op in Operator.query.all():
        if getattr(op, "absent", False):
            continue
        if operator_can_do(op, stage):
            u = User.query.filter_by(name=op.name).first()
            out.append({"id": u.id if u else None, "operatorId": op.id,
                        "name": op.name, "skill": op.skill, "shift": op.shift})
    return jsonify({"order": code, "stage": stage, "candidates": out})


# --------------------------------------------------------------------------
# ORDER INTAKE — manual entry (all details incl. starting stage) and
# Knowledge-Centre document sync (parse a PO/spec doc, review, import).
# --------------------------------------------------------------------------
@frontend_bp.post("/api/intake")
def api_intake():
    """Create a new order from the manual intake form (all details)."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from order_intake import create_order
    from flask import session
    body = request.json or {}
    user = User.query.get(session.get("user_id")) if session.get("user_id") else None
    o = create_order(db, Order, log_event_shim(), 
                     line_code=body.get("lineCode", "PT"),
                     qty=body.get("qty", 1), due=body.get("due", ""),
                     product=body.get("product"),
                     family=body.get("family"),
                     stage=body.get("stage"),
                     customer=body.get("customer"),
                     notes=body.get("notes"),
                     priority_rush=bool(body.get("rush")),
                     source="manual",
                     actor=(body.get("by") or (user.name if user else "Head")),
                     role=(body.get("role") or (user.role if user else "Department Head")))
    return jsonify(ok=True, order=o.to_dict())


@frontend_bp.post("/api/intake/kc-parse")
def api_intake_kc_parse():
    """Knowledge-Centre sync — parse an uploaded order document and return the
    extracted order(s) for review. NO writes. Body: {text}."""
    from order_extract import extract_orders
    body = request.json or {}
    return jsonify(extract_orders(body.get("text", "")))


@frontend_bp.post("/api/intake/kc-import")
def api_intake_kc_import():
    """Create orders from KC-extracted records the head approved.
    Body: {orders: [ {customer, product, family, line, qty, due, stage, rush, notes} ]}."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from order_intake import create_order
    from flask import session
    body = request.json or {}
    records = body.get("orders") or []
    if not records:
        return jsonify(error="no orders to import"), 400
    user = User.query.get(session.get("user_id")) if session.get("user_id") else None
    actor = body.get("by") or (user.name if user else "Head")
    role = body.get("role") or (user.role if user else "Department Head")
    created = []
    for r in records:
        o = create_order(db, Order, log_event_shim(),
                         line_code=r.get("line", "PT"),
                         qty=r.get("qty", 1), due=r.get("due", ""),
                         product=r.get("product"), family=r.get("family"),
                         stage=r.get("stage"), customer=r.get("customer"),
                         notes=r.get("notes"), priority_rush=bool(r.get("rush")),
                         source="kc", actor=actor, role=role)
        created.append(o.to_dict())
    return jsonify(ok=True, created=created, count=len(created))


@frontend_bp.get("/api/insights/history-overview")
def api_history_overview():
    """Top-level 15-year history overview for the Insights page header."""
    from history_insight import overview
    o = overview()
    return jsonify(o or {"incidentCount": 0})


@frontend_bp.post("/api/insights/import-history")
def api_import_history():
    """Import external historical outcomes (from ERP/MES/KC/CSV) into the
    external_history table, deduped on (source, ext_id). Body:
    {source, rows:[ {ext_id, order_code, product, line_code, stage, resource,
                     category, duration_min, outcome, occurred_at(ISO)} ]}.
    Head/Admin only. Folds into the next insight rollup automatically."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from flask import session
    from models import ExternalHistory
    from datetime import datetime
    user = User.query.get(session.get("user_id")) if session.get("user_id") else None
    if (user.role if user else "") not in ("Department Head", "Admin"):
        return jsonify(error="only a Department Head or Admin can import history"), 403
    body = request.json or {}
    source = (body.get("source") or "csv").strip()[:24]
    rows = body.get("rows") or []
    if not rows:
        return jsonify(error="no rows to import"), 400
    added, skipped = 0, 0
    for r in rows:
        ext_id = str(r.get("ext_id") or r.get("id") or "").strip()
        if not ext_id:
            skipped += 1
            continue
        if ExternalHistory.query.filter_by(source=source, ext_id=ext_id).first():
            skipped += 1
            continue
        occ = None
        try:
            if r.get("occurred_at"):
                occ = datetime.fromisoformat(str(r["occurred_at"]).replace("Z", "+00:00"))
        except Exception:
            occ = None
        db.session.add(ExternalHistory(
            source=source, ext_id=ext_id,
            order_code=r.get("order_code"), product=r.get("product"),
            line_code=r.get("line_code"), stage=r.get("stage"),
            resource=r.get("resource"), category=(r.get("category") or "").lower() or None,
            duration_min=r.get("duration_min"), outcome=r.get("outcome"),
            occurred_at=occ or datetime.utcnow()))
        added += 1
    db.session.commit()
    # recompute the rollup so the import is reflected immediately
    try:
        from services import compute_insight_rollup
        compute_insight_rollup(db, window_days=90, source="manual")
    except Exception:
        pass
    return jsonify(ok=True, added=added, skipped=skipped)


@frontend_bp.post("/api/insights/rollup")
def api_insights_rollup():
    """Recompute the insight rollup now (manual 'Refresh insights' button).
    Head/Admin only."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from flask import session
    from services import compute_insight_rollup
    user = User.query.get(session.get("user_id")) if session.get("user_id") else None
    role = (user.role if user else "")
    if role not in ("Department Head", "Admin"):
        return jsonify(error="only a Department Head or Admin can refresh insights"), 403
    days = int((request.json or {}).get("windowDays", 90))
    payload = compute_insight_rollup(db, window_days=days, source="manual")
    return jsonify(ok=True, eventsSeen=payload.get("events_seen", 0),
                   computedAt=payload.get("computed_at"))


@frontend_bp.get("/api/insights/rollup-status")
def api_insights_rollup_status():
    """When was the rollup last computed, and from how much history."""
    from services import latest_insight_rollup
    from app import db as _db
    roll = latest_insight_rollup(_db)
    if not roll:
        return jsonify(live=False, computedAt=None, eventsSeen=0)
    return jsonify(live=(roll.get("events_seen", 0) >= 10),
                   computedAt=roll.get("computed_at"),
                   eventsSeen=roll.get("events_seen", 0),
                   windowDays=roll.get("window_days", 90))




def log_event_shim():
    """create_order expects a log_event(kind,title,detail,actor,role) callable."""
    from models import log_event
    return log_event


# --------------------------------------------------------------------------
# KC LIVE SYNC — auto-detect new order documents in the Knowledge Center
# --------------------------------------------------------------------------
@frontend_bp.get("/api/intake/kc-status")
def api_kc_status():
    """Is the Knowledge Center reachable? (for the intake UI to show online/offline)"""
    try:
        from kc_client import client
        return jsonify(online=client().is_online(), base=client().base,
                       department=client().department)
    except Exception as e:
        return jsonify(online=False, error=str(e))


@frontend_bp.get("/api/intake/kc-detect")
def api_kc_detect():
    """Poll the KC for NEW scheduler order documents and return the parsed
    orders we haven't imported yet, for the review queue. NO writes."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from kc_client import client
    from kc_intake import detect_new_orders
    # codes we already have (any source) — so a KC order is never imported twice
    imported = set()
    try:
        for o in Order.query.all():
            imported.add(o.code)
            # also remember the KC doc id if we stored it in notes/source
    except Exception:
        pass
    # plus codes already imported from KC in this process (tracked in a KV row)
    imported |= _kc_imported_codes(db)
    res = detect_new_orders(client(), imported)
    return jsonify(res)


@frontend_bp.post("/api/intake/kc-sync-import")
def api_kc_sync_import():
    """Create scheduler orders from KC-detected order docs the head approved.
    Body: {orders: [ parsed-order dicts from kc-detect ], by, role}.
    Preserves each order's KC order_code and records the doc id so it won't be
    re-detected."""
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()
    from order_intake import create_order
    from flask import session
    body = request.json or {}
    records = body.get("orders") or []
    if not records:
        return jsonify(error="no orders to import"), 400
    user = User.query.get(session.get("user_id")) if session.get("user_id") else None
    actor = body.get("by") or (user.name if user else "Head")
    role = body.get("role") or (user.role if user else "Department Head")
    created, skipped = [], []
    for r in records:
        code = r.get("code")
        # guard: don't double-create if the code already exists
        if code and Order.query.filter_by(code=code).first():
            skipped.append(code)
            continue
        if not r.get("lineCode"):
            skipped.append(code or "(no code)")
            continue
        o = create_order(db, Order, log_event_shim(),
                         line_code=r.get("lineCode"),
                         qty=r.get("qty", 1), due=r.get("due", ""),
                         product=r.get("product"), family=r.get("family"),
                         stage=r.get("startStage"), customer=r.get("customer"),
                         notes=r.get("notes"), priority_rush=bool(r.get("rush")),
                         code=code, source="kc", actor=actor, role=role)
        created.append(o.to_dict())
        _kc_mark_imported(db, r.get("docId") or code)
    db.session.commit()
    return jsonify(ok=True, created=created, count=len(created), skipped=skipped)


# tiny KV to remember which KC doc ids we've already imported (survives restart)
def _kc_imported_codes(db):
    try:
        from models import AppKV
        row = AppKV.query.filter_by(k="kc_imported").first()
        if row and row.v:
            import json as _j
            return set(_j.loads(row.v))
    except Exception:
        pass
    return set()


def _kc_mark_imported(db, doc_id):
    if not doc_id:
        return
    try:
        from models import AppKV
        import json as _j
        row = AppKV.query.filter_by(k="kc_imported").first()
        cur = set(_j.loads(row.v)) if (row and row.v) else set()
        cur.add(doc_id)
        if row:
            row.v = _j.dumps(sorted(cur))
        else:
            db.session.add(AppKV(k="kc_imported", v=_j.dumps(sorted(cur))))
    except Exception:
        pass


@frontend_bp.get("/api/bootstrap")
def bootstrap():
    # ---- inserted endpoints live above; see manpower/reports/insights ----
    Order, Constraint, Confirmation, Operator, User, LogEvent, db = _models()

    users = [{"id": u.id, "name": u.name, "role": u.role,
              "short": {"Department Head": "Dept Head"}.get(u.role, u.role)}
             for u in User.query.order_by(User.id).all()]

    # current user: session if present, else first Dept Head (mirrors app.py)
    from flask import session
    uid = session.get("user_id")
    if not uid:
        head = User.query.filter_by(role="Department Head").first()
        uid = head.id if head else (users[0]["id"] if users else 1)

    from engine.plant import routing_for
    from engine.adapter import LINE_MAP

    from engine.plant import STAGES
    from engine.domain import TimeBasis

    def lead_days(o):
        """Honest lead time in DAYS for this order, and how far along it is.

        NOT a stage count. We sum each stage's real estimated duration:
          * working stages: setup + per_unit * qty  (per-unit work scales w/ qty)
          * wall-clock stages (e.g. Burn-in 48h soak): the flat dwell
        Total days = that sum / (24*60). Day-of = the sum of stages already
        completed. This is why 13 stages can be ~4 days: burn-in is the only long
        one, most stages are minutes-to-hours. These are ENGINEERING ESTIMATES.
        """
        line = LINE_MAP.get(o.line_code)
        if not line:
            return None, None, None
        route = routing_for(line)
        qty = o.qty or 1

        def stage_minutes(s):
            if s.time_basis is TimeBasis.WALL_CLOCK:
                return s.setup_min + s.per_unit_min      # flat dwell (soak)
            return s.setup_min + s.per_unit_min * qty     # per-unit work

        cur = (o.current_stage or "").lower()
        elapsed = 0.0
        total = 0.0
        reached = False
        for s in route:
            m = stage_minutes(s)
            total += m
            nm = s.name.lower()
            if not reached:
                if nm == cur or nm.startswith(cur) or (cur and cur in nm):
                    reached = True          # current stage: count it as in-progress
                else:
                    elapsed += m            # fully-completed prior stage
        total_days = round(total / 1440, 1)
        day_of = round(elapsed / 1440, 1)
        return day_of, total_days, len(route)

    def order_dict(o):
        day, total_days, stages = lead_days(o)
        return {"code": o.code, "product": o.product, "family": o.family,
                "lineCode": o.line_code, "line": o.line, "qty": o.qty,
                "phase": o.phase, "stage": o.current_stage, "status": o.status,
                "due": o.due, "source": o.update_source or "erp",
                "startMin": o.start_min or 0, "durationMin": o.duration_min or 240,
                "rush": bool(o.rush),
                "locked": bool(getattr(o, "locked", False)),
                "lockReason": getattr(o, "lock_reason", None),
                "qhold": bool(getattr(o, "qhold", False)),
                "leadDay": day, "leadTotalDays": total_days, "stageCount": stages}

    orders = [order_dict(o) for o in Order.query.order_by(Order.code).all()]

    # Live risk: an order whose projected finish is past its due date must not
    # read "ON TRACK" in the register. Run the engine once and override a stale
    # on-track/running/rescheduled status to AT RISK where it's genuinely late.
    try:
        from engine.scheduler import SchedulerEngine
        from engine.adapter import to_engine_order
        _now = datetime.utcnow()
        _rows = Order.query.all()
        _sched = SchedulerEngine(_now).compute(
            [to_engine_order(r, _now) for r in _rows], [])
        _st = {}
        for eo in _sched.orders:
            late = bool(eo.projected_finish and eo.due and eo.projected_finish > eo.due)
            _st[eo.code] = (eo.status.value, late)
        for od in orders:
            info = _st.get(od["code"])
            if not info:
                continue
            eng_status, late = info
            if (eng_status in ("AT RISK", "HALTED") or late) and \
               od["status"] in ("ON TRACK", "RUNNING", "RESCHEDULED"):
                od["status"] = eng_status if eng_status in ("AT RISK", "HALTED") else "AT RISK"
    except Exception:
        pass

    constraints = [{
        "code": c.code, "raisedBy": c.raised_by, "role": c.raised_role,
        "order": c.order_code, "stage": c.stage, "type": c.ctype,
        "note": c.note, "status": c.status, "revision": c.revision,
        "feedback": c.feedback or "",
        "ts": c.created_at.strftime("%d %b %H:%M") if c.created_at else ""}
        for c in Constraint.query.order_by(Constraint.created_at.desc()).all()]

    confirmations = [{
        "order": cf.order_code, "stage": cf.stage, "item": cf.item,
        "operator": cf.operator,
        "ts": cf.confirmed_at.strftime("%d %b %H:%M") if cf.confirmed_at else ""}
        for cf in Confirmation.query.order_by(
            Confirmation.confirmed_at.desc()).limit(20).all()]

    # engine-derived suggestions for the board (best-effort)
    board_insights = _board_insights(orders)

    data = {
        "session": {"userId": uid},
        "users": users,
        "brand": {"name": "MERIDIAN INSTRUMENTS",
                  "tagline": "AI Production Scheduler", "logo": "img/logo.svg"},
        "logCount": LogEvent.query.count(),
        "sync": {"source": "ERP", "state": "synced",
                 "at": datetime.utcnow().strftime("%H:%M")},
        "nav": [
            {"group": None, "items": [
                {"id": "dashboard", "label": "Dashboard", "href": "index.html", "icon": "grid"},
                {"id": "board", "label": "Schedule Board", "href": "board.html",
                 "write": True, "badgeKey": "mfgOrders", "icon": "board"},
                {"id": "orders", "label": "Orders", "href": "orders.html",
                 "employeeLabel": "My Work", "employeeHref": "mywork.html",
                 "badgeKey": "orders", "icon": "list"},
                {"id": "manpower", "label": "Manpower", "href": "manpower.html",
                 "write": True, "icon": "people"},
            ]},
            {"group": "PHASES", "items": [
                {"id": "quality", "label": "Stage Confirmation", "href": "quality.html",
                 "employeeLabel": "Stage Confirmations", "badgeKey": "approvals", "icon": "check"},
            ]},
            {"group": None, "items": [
                {"id": "activity", "label": "Activity Log", "href": "activity.html",
                 "write": True, "badgeKey": "log", "icon": "log"},
                {"id": "reports", "label": "Reports", "href": "reports.html",
                 "write": True, "icon": "report"},
                {"id": "insights", "label": "Insights", "href": "insights.html",
                 "write": True, "icon": "insights"},
            ]},
        ],
        "phases": [
            {"key": "intake", "label": "Front end & planning", "color": "var(--phase-intake)"},
            {"key": "mfg", "label": "Manufacturing", "color": "var(--phase-mfg)"},
            {"key": "quality", "label": "Quality", "color": "var(--phase-quality)"},
            {"key": "dispatch", "label": "Dispatch", "color": "var(--phase-dispatch, #b07bc4)"},
            {"key": "closed", "label": "Closed / shipped", "color": "var(--phase-closed)"},
        ],
        "lines": [
            {"code": "PT", "name": "Line 1", "family": "Pressure", "color": "var(--line-pt)"},
            {"code": "TT", "name": "Line 2", "family": "Temperature", "color": "var(--line-tt)"},
            {"code": "DP", "name": "Line 3", "family": "Diff. Pressure", "color": "var(--line-dp)"},
            {"code": "LT", "name": "Line 4", "family": "Level", "color": "var(--line-lt)"},
        ],
        "downstream": [
            {"name": "Final QC", "match": ["Final QC"]},
            {"name": "Documentation", "match": ["Docs", "Documentation"]},
            {"name": "Packing", "match": ["Packing"]},
            {"name": "Shipping & Dispatch", "match": ["Shipping", "Dispatch", "Closure"]},
        ],
        "workCentres": _work_centres(),
        "stages": ["Kitting", "Assembly", "Calibration", "Burn-in",
                   "Final QC", "Packing", "Dispatch"],
        "constraintTypes": ["Material shortage", "Machine issue", "Manpower gap",
                            "Quality hold", "Tooling"],
        "products": ["PT-3051", "TT-644", "DP-2051", "LT-5400"],
        "customers": ["Northwind Energy", "Kova Automotive", "Medisys Devices",
                      "Brightline Utilities"],
        "orders": orders,
        "intakeQueue": [o for o in orders if o["phase"] == "intake"],
        "constraints": constraints,
        "confirmations": confirmations,
        "dispatches": [o for o in orders if o["phase"] in ("quality", "closed")][:6]
                       and _dispatches(orders),
        "checklists": _checklists(),
        "boardInsights": board_insights,
        "chat": [
            {"from": "ai", "author": "Scheduler AI", "ts": "09:00",
             "text": "Ask me about an order (\u201cwhere is SO-1044?\u201d), the active "
                     "constraints, or how to improve throughput. To change the plan, "
                     "describe a disruption \u2014 e.g. \u201cBurn-in Chamber B2 is down "
                     "till the 24th.\u201d"}
        ],
        "constraintStates": {
            "pending": {"tone": "warn", "head": "AWAITING YOUR APPROVAL", "mine": "Awaiting production head"},
            "approved": {"tone": "info", "head": "CONSTRAINT APPROVED · SCHEDULE PENDING", "mine": "Approved · schedule under review"},
            "applied": {"tone": "ok", "head": "APPLIED TO LIVE FLOOR", "mine": "Applied to live floor"},
            "rejected": {"tone": "bad", "head": "REJECTED", "mine": "Rejected"},
        },
    }
    return jsonify(data)


def _work_centres():
    """From the engine plant definition if available, else a sane default."""
    try:
        from engine import plant
        out = []
        for gid in ("BURNIN", "CAL"):
            grp = plant.RESOURCE_GROUPS.get(gid)
            if not grp:
                continue
            for u in grp.members:
                out.append({"name": u.name,
                            "sub": f"{grp.name} work-centre",
                            "state": "available"})
        return out or _default_centres()
    except Exception:
        return _default_centres()


def _default_centres():
    return [
        {"name": "Burn-in Chamber B1", "sub": "Burn-in work-centre", "state": "available"},
        {"name": "Burn-in Chamber B2", "sub": "Burn-in work-centre", "state": "available"},
        {"name": "Calibration Bench C1", "sub": "Calibration work-centre", "state": "available"},
        {"name": "Calibration Bench C2", "sub": "Calibration work-centre", "state": "maintenance",
         "label": "MAINTENANCE", "note": "Planned outage 02:00–06:00", "startMin": 1200, "durationMin": 240},
        {"name": "Calibration Bench C3", "sub": "Calibration work-centre", "state": "available"},
    ]


def _dispatches(orders):
    """Orders shipping or ready to ship today — the dispatch/closed phase, plus
    manufacturing orders already at the Packing/Dispatch stages."""
    out = []
    ship_stages = ("Packing", "Dispatch", "Shipping", "Shipping & Dispatch")
    for o in orders:
        at_ship = o.get("stage") in ship_stages
        if o["phase"] in ("dispatch", "quality", "closed") or at_ship:
            shipped = o["phase"] == "closed" or o.get("stage") == "Dispatch"
            out.append({
                "time": o.get("due") or "today",
                "item": f"{o['code']} · {o['product']} ×{o['qty']}",
                "customer": f"{o.get('line', '')} · {o.get('family', '')}".strip(" ·"),
                "status": "SHIPPED" if shipped else o["status"],
            })
    return out[:8]


def _checklists():
    return {
        "Kitting": ["BOM kit verified against pick list", "Serial tags issued", "Shortages flagged to planner"],
        "Assembly": ["Sub-assembly torque check logged", "Housing seal fitted", "Wiring continuity pass"],
        "Calibration": ["Bench reference standard verified", "3-point calibration recorded", "Cal certificate drafted"],
        "Burn-in": ["Chamber loaded & profile set", "Soak hours logged", "Post-soak drift within limit"],
        "Final QC": ["Visual & dimensional inspection", "Functional test pass", "Open NCRs closed"],
        "Packing": ["Anti-static bagging complete", "Carton labels applied", "Document pack enclosed"],
        "Dispatch": ["Ship-ready confirmed", "Carrier booked", "POD reference captured"],
    }


def _board_insights(orders):
    """Use the suggestion engine if present, mapped to the board card shape."""
    try:
        from engine.adapter import to_engine_order
        from engine.scheduler import SchedulerEngine
        from assistant.suggestions import generate

        Order, *_ = _models()
        now = datetime.utcnow()
        rows = _active_orders(Order)
        sched = SchedulerEngine(now).compute(
            [to_engine_order(r, now) for r in rows], [])
        sugs = generate(sched, now)
        kind_map = {"load": "USAGE", "bottleneck": "MOVE", "risk": "MOVE",
                    "quality": "SPLIT", "data": "USAGE"}
        return [{
            "kind": kind_map.get(s.category, "USAGE"),
            "ref": s.title.split("—")[0].strip()[:24],
            "title": s.title,
            "detail": s.observation + " " + s.effect,
            "gain": s.effect[:40],
        } for s in sugs[:4]]
    except Exception:
        return []
