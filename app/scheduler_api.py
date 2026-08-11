"""Flask integration for the scheduling engine + AI assistant.

Drop this file into meridian_app/ alongside the engine/ and assistant/
packages, then register it in app.py:

    from scheduler_api import scheduler_bp, apply_constraint_to_floor
    app.register_blueprint(scheduler_bp, url_prefix="/api/scheduler")

Endpoints
---------
POST /api/scheduler/ask        {question}          -> assistant answer
POST /api/scheduler/propose    {text}              -> parsed constraint + diff
POST /api/scheduler/apply      {code}              -> write schedule to the floor
GET  /api/scheduler/schedule                       -> current computed schedule
GET  /api/scheduler/suggestions                    -> optimisation suggestions

The 'apply' endpoint is what makes an approved change reflect in the Live Floor
tab: it recomputes the schedule with the constraint active and writes each
order's new status/finish/bench back to the database.
"""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, request

# these imports resolve once the packages sit in meridian_app/
from engine.adapter import to_engine_order, apply_to_row
from engine.scheduler import SchedulerEngine
from assistant.assistant import Assistant

scheduler_bp = Blueprint("scheduler", __name__)


# The app is expected to expose these — imported lazily to avoid a hard cycle.
def _models():
    from models import Order, Constraint, db, log_event
    return Order, Constraint, db, log_event


# In-memory store of active engine constraints, keyed by the app's constraint
# code. Backed by the Constraint table: applied constraints are persisted and
# rehydrated on first use so they survive refreshes and restarts.
_ACTIVE: dict[str, object] = {}
_REHYDRATED = False


def _ctype_to_str(ctype):
    """Engine ConstraintType enum -> the human label stored in Constraint.ctype."""
    try:
        return ctype.value if hasattr(ctype, "value") else str(ctype)
    except Exception:
        return str(ctype)


def _persist_constraint_record(constraint, db, status="applied"):
    """Upsert an engine constraint into the Constraint table so it survives a
    refresh/restart. Stores enough to rebuild the engine object on reload."""
    from models import Constraint as ConstraintModel
    import json
    row = ConstraintModel.query.filter_by(code=constraint.code).first()
    # pack the engine fields we need to rebuild into note-adjacent columns; the
    # human-readable note stays in `note`, the machine fields go in `feedback`
    # as a small JSON blob (reusing an existing column to avoid a migration).
    payload = {
        "ctype": _ctype_to_str(constraint.ctype),
        "resource_group": getattr(constraint, "resource_group", None),
        "resource_id": getattr(constraint, "resource_id", None),
        "order_code": getattr(constraint, "order_code", None),
        "magnitude": getattr(constraint, "magnitude", None),
        "starts_at": constraint.starts_at.isoformat() if getattr(constraint, "starts_at", None) else None,
        "ends_at": constraint.ends_at.isoformat() if getattr(constraint, "ends_at", None) else None,
    }
    blob = "__ENGINE__" + json.dumps(payload)
    if row:
        row.status = status
        row.ctype = payload["ctype"]
        row.order_code = payload["order_code"]
        row.note = getattr(constraint, "note", "") or constraint.human()
        row.feedback = blob
    else:
        db.session.add(ConstraintModel(
            code=constraint.code, raised_by="Scheduler", raised_role="System",
            order_code=payload["order_code"], stage=None,
            ctype=payload["ctype"],
            note=getattr(constraint, "note", "") or constraint.human(),
            status=status, feedback=blob))


def _rebuild_engine_constraint(row):
    """Rebuild an engine Constraint from a persisted Constraint row (or None)."""
    import json
    from datetime import datetime as _dt
    from engine.domain import Constraint as EC, ConstraintType
    blob = row.feedback or ""
    if not blob.startswith("__ENGINE__"):
        return None
    try:
        p = json.loads(blob[len("__ENGINE__"):])
    except Exception:
        return None
    def _pd(s):
        try:
            return _dt.fromisoformat(s) if s else None
        except Exception:
            return None
    try:
        ct = ConstraintType(p.get("ctype")) if p.get("ctype") else None
    except Exception:
        ct = None
    if ct is None:
        return None
    return EC(code=row.code, ctype=ct,
              resource_group=p.get("resource_group"),
              resource_id=p.get("resource_id"),
              order_code=p.get("order_code"),
              magnitude=p.get("magnitude"),
              starts_at=_pd(p.get("starts_at")),
              ends_at=_pd(p.get("ends_at")),
              note=row.note or "")


def _ensure_rehydrated():
    """Load applied constraints from the DB into _ACTIVE once per process, so a
    page refresh (or a server restart) doesn't lose applied constraints."""
    global _REHYDRATED
    if _REHYDRATED:
        return
    _REHYDRATED = True
    try:
        from models import Constraint as ConstraintModel
        for row in ConstraintModel.query.filter_by(status="applied").all():
            if row.code in _ACTIVE:
                continue
            ec = _rebuild_engine_constraint(row)
            if ec is not None:
                _ACTIVE[row.code] = ec
    except Exception:
        pass


def _persist_constraint_effects(constraint, rows, db):
    """Write per-type side-effects onto the order rows so the board shows them:
      - PRIORITY_CHANGE -> lock/protect the order (board warns before a drag)
      - QUALITY_HOLD    -> flag qhold + held so the order shows on hold
      - RUSH_ORDER      -> flag rush
    (RESOURCE_DOWN / LABOUR / CAPACITY act on capacity, not a single order, and
    already surface via the resource rows and recomputed dates.)"""
    from engine.domain import ConstraintType
    by_code = {r.code: r for r in rows}
    ctype = getattr(constraint, "ctype", None)
    ocode = getattr(constraint, "order_code", None)
    row = by_code.get(ocode) if ocode else None
    if ctype is ConstraintType.PRIORITY_CHANGE and row:
        row.locked = True
        row.lock_reason = (getattr(constraint, "note", "") or "Protected by planner")[:160]
        if row.status not in ("DONE",):
            row.status = "RUSH" if row.rush else row.status
    elif ctype is ConstraintType.QUALITY_HOLD and row:
        row.qhold = True
        row.held = True
        row.qhold_reason = (getattr(constraint, "note", "") or "Quality hold")[:200]
        row.status = "HALTED"
    elif ctype is ConstraintType.RUSH_ORDER and row:
        row.rush = True
        row.status = "RUSH"
    elif ctype is ConstraintType.LABOUR_REDUCTION:
        # #2: if the absence names a person, flag them absent so the Manpower
        # roster shows them OUT (the re-plan handles the capacity math).
        note = (getattr(constraint, "note", "") or "")
        try:
            from models import Operator
            for op in Operator.query.all():
                if op.name and op.name.lower() in note.lower():
                    op.absent = True
                    op.absent_note = note[:120]
                    break
        except Exception:
            pass


def active_resource_outages():
    """Return applied resource-down/maintenance constraints as simple dicts the
    board can render on the matching chamber/bench row:
      {group, unit, kind ('maintenance'|'down'), startMin, endMin, note}
    Times are minutes-from-midnight within the board's day window; None means
    open-ended. Only constraints that name a specific unit (e.g. B2) or a whole
    group are returned."""
    from engine.domain import ConstraintType
    _ensure_rehydrated()
    out = []
    for c in _ACTIVE.values():
        if getattr(c, "ctype", None) != ConstraintType.RESOURCE_DOWN:
            continue
        note = (getattr(c, "note", "") or "").lower()
        is_maint = any(w in note for w in ("maintenance", "servicing", "service",
                                           "repair", "pm ", "preventive", "preventative"))
        out.append({
            "group": getattr(c, "resource_group", None),
            "unit": getattr(c, "resource_id", None),
            "kind": "maintenance" if is_maint else "down",
            "starts_at": getattr(c, "starts_at", None),
            "ends_at": getattr(c, "ends_at", None),
            "note": getattr(c, "note", ""),
            "code": getattr(c, "code", None),
        })
    return out


def _now():
    return datetime.utcnow()


def _load_orders():
    Order, *_ = _models()
    now = _now()
    return [to_engine_order(r, now) for r in Order.query.all()], now


# --------------------------------------------------------------------------
@scheduler_bp.post("/ask")
def ask():
    question = (request.json or {}).get("question", "").strip()
    if not question:
        return jsonify(error="ask a question"), 400
    orders, now = _load_orders()
    asst = Assistant(now)
    _ensure_rehydrated()
    ans = asst.answer(question, orders, list(_ACTIVE.values()))

    # memory: recall relevant past exchanges (advisory), then store this one.
    from assistant import memory
    recalled = memory.recall(question, k=3)
    # store the Q + a short form of the answer for future recall
    memory.remember(f"Q: {question}\nA: {ans.text}", kind="qa",
                    meta={"answer_kind": ans.kind})

    # attach citations (real sources the answer was grounded in) into data
    data = ans.data or {}
    if getattr(ans, "citations", None):
        data = dict(data)
        data["citations"] = ans.citations

    return jsonify(answer=ans.text, kind=ans.kind, data=data,
                   memory=[{"text": r["text"], "ts": r["ts"]} for r in recalled])


@scheduler_bp.post("/propose")
def propose():
    text = (request.json or {}).get("text", "").strip()
    if not text:
        return jsonify(error="describe a disruption"), 400
    orders, now = _load_orders()
    _, Constraint, *_ = _models()

    def next_code():
        n = Constraint.query.count() + 201
        return f"C-{n}"

    asst = Assistant(now)
    # operator roster (name -> skill) so the parser can resolve a named person's
    # absence to the right resource group.
    roster = []
    try:
        from models import Operator
        roster = [{"name": o.name, "skill": o.skill} for o in Operator.query.all()]
    except Exception:
        roster = []
    _ensure_rehydrated()
    result = asst.propose(text, orders, list(_ACTIVE.values()), next_code, roster=roster)
    if not result["ok"]:
        return jsonify(ok=False, echo=result["echo"])

    # stash the parsed constraint so /apply can find it by code
    c = result["constraint"]
    _ACTIVE[c.code] = c
    changes = result["changes"]
    n = len(changes) if changes else 0

    # #2: if this is a named-person absence, surface a reassign hint listing
    # their pending tasks and where to reassign them.
    reassign = None
    from engine.domain import ConstraintType
    if getattr(c, "ctype", None) is ConstraintType.LABOUR_REDUCTION:
        note = (getattr(c, "note", "") or "")
        try:
            from models import Operator, Assignment
            # find the named operator whose name appears in the note
            person = None
            for op in Operator.query.all():
                if op.name.lower() in note.lower():
                    person = op
                    break
            if person:
                pend = [a.order_code + " \u00b7 " + (a.stage or "")
                        for a in Assignment.query.filter_by(
                            employee_name=person.name).all()
                        if getattr(a, "status", "") not in ("done", "completed")]
                reassign = {"person": person.name, "shift": person.shift,
                            "tasks": pend,
                            "hint": "Reassign their work on the Manpower tab."}
        except Exception:
            reassign = None

    return jsonify(
        ok=True, echo=result["echo"], confidence=result["confidence"],
        code=c.code, summary=result["summary"], changes=changes,
        badges=result.get("badges"), reassign=reassign,
        citations=[
            {"label": f"Constraint {c.code}", "detail": result["echo"]},
            {"label": "Scheduling engine (CP re-plan)",
             "detail": f"{n} order(s) rescheduled around the constraint"},
        ])


@scheduler_bp.post("/revise")
def revise():
    """Head rejected a proposal and said what to change. Recompute honouring
    the feedback, return the new revision's before->after."""
    body = request.json or {}
    code = body.get("code", "").strip()
    feedback = body.get("feedback", "").strip()
    revision = int(body.get("revision", 2))
    constraint = _ACTIVE.get(code)
    if not constraint:
        return jsonify(error="no such proposed constraint"), 404
    if not feedback:
        return jsonify(error="say what should change"), 400

    orders, now = _load_orders()
    asst = Assistant(now)
    others = [c for k, c in _ACTIVE.items() if k != code]
    result = asst.revise(feedback, orders, others, constraint, revision)
    return jsonify(
        ok=True, code=code, revision=revision,
        directive=result["directive"], summary=result["summary"],
        changes=result["changes"])


@scheduler_bp.post("/apply")
def apply():
    """Approve a proposed constraint: recompute and write to the floor.
    THIS is what makes the change show up in the Live Floor tab."""
    code = (request.json or {}).get("code", "").strip()
    _ensure_rehydrated()
    constraint = _ACTIVE.get(code)
    if not constraint:
        return jsonify(error="no such proposed constraint"), 404

    Order, _, db, log_event = _models()
    now = _now()

    # A RESTORE isn't an outage to add — it CLEARS the active outage on the named
    # resource. Remove the matching RESOURCE_DOWN from _ACTIVE and the DB, then
    # recompute the floor without it.
    from engine.domain import ConstraintType
    if getattr(constraint, "ctype", None) is ConstraintType.RESOURCE_RESTORE:
        tgt_unit = getattr(constraint, "resource_id", None)
        tgt_group = getattr(constraint, "resource_group", None)
        cleared = []
        for k, c in list(_ACTIVE.items()):
            if getattr(c, "ctype", None) is not ConstraintType.RESOURCE_DOWN:
                continue
            # match on the specific unit if named, else the whole group
            if tgt_unit and getattr(c, "resource_id", None) != tgt_unit:
                continue
            if not tgt_unit and getattr(c, "resource_group", None) != tgt_group:
                continue
            cleared.append(k)
            _ACTIVE.pop(k, None)
        # mark those DB rows as cleared so they don't rehydrate
        try:
            from models import Constraint as CM
            for k in cleared:
                row = CM.query.filter_by(code=k).first()
                if row:
                    row.status = "cleared"
        except Exception:
            pass
        # the restore "constraint" itself was stashed in _ACTIVE by propose;
        # drop it (it's an instruction, not an ongoing constraint)
        _ACTIVE.pop(code, None)

        rows = Order.query.all()
        sched = SchedulerEngine(now).compute(
            [to_engine_order(r, now) for r in rows], [c for c in _ACTIVE.values()])
        by_code = {o.code: o for o in sched.orders}
        for row in rows:
            eo = by_code.get(row.code)
            if eo:
                apply_to_row(row, eo)
        db.session.commit()
        tgt = tgt_unit or tgt_group or "resource"
        if not cleared:
            log_event("schedule", f"{tgt} marked available",
                      "No active outage was on record for it.",
                      actor="Scheduler", role="System")
            return jsonify(ok=True, applied=code, cleared=0,
                           message=f"{tgt} is available — there was no active outage to clear.")
        log_event("schedule", f"{tgt} back in service",
                  f"Cleared outage(s): {', '.join(cleared)}. Floor recomputed.",
                  actor="Scheduler", role="System")
        return jsonify(ok=True, applied=code, cleared=len(cleared),
                       clearedCodes=cleared)

    rows = Order.query.all()
    eng_orders = [to_engine_order(r, now) for r in rows]

    sched = SchedulerEngine(now).compute(
        eng_orders, [c for c in _ACTIVE.values()])

    by_code = {o.code: o for o in sched.orders}
    touched = 0
    for row in rows:
        eo = by_code.get(row.code)
        if eo:
            apply_to_row(row, eo)
            touched += 1

    # persist per-constraint side-effects on the affected order so the board
    # reflects them (beyond the recomputed dates/status):
    _persist_constraint_effects(constraint, rows, db)

    # persist the CONSTRAINT ITSELF to the Constraint table with status
    # "applied", so it survives a refresh/restart (it previously lived only in
    # the in-memory _ACTIVE dict and vanished on reload).
    try:
        _persist_constraint_record(constraint, db, status="applied")
    except Exception:
        db.session.rollback()

    db.session.commit()

    log_event("schedule", f"Schedule applied for {code}",
              f"{constraint.human()} — {touched} orders recomputed on the floor.",
              actor="Scheduler", role="System")
    return jsonify(ok=True, applied=code, orders_updated=touched)


@scheduler_bp.get("/schedule")
def schedule():
    orders, now = _load_orders()
    _ensure_rehydrated()
    sched = SchedulerEngine(now).compute(orders, list(_ACTIVE.values()))
    return jsonify(orders=[{
        "code": o.code, "line": o.line.value, "status": o.status.value,
        "finish": o.projected_finish.strftime("%d %b") if o.projected_finish else None,
        "due": o.due.strftime("%d %b"),
        "calibration": o.assigned_resource.get("09"),
        "burn_in": o.assigned_resource.get("09B"),
    } for o in sched.orders], summary=sched.summary)


@scheduler_bp.get("/suggestions")
def suggestions():
    from assistant.suggestions import generate
    orders, now = _load_orders()
    _ensure_rehydrated()
    sched = SchedulerEngine(now).compute(orders, list(_ACTIVE.values()))
    sugs = generate(sched, now)
    return jsonify(suggestions=[s.to_dict() for s in sugs])


# --------------------------------------------------------------------------
def apply_constraint_to_floor(constraint_code: str):
    """Helper the existing decide_schedule() route can call directly, so the
    app's approval chain drives the engine without an HTTP round-trip."""
    constraint = _ACTIVE.get(constraint_code)
    if not constraint:
        return 0
    Order, _, db, log_event = _models()
    now = _now()
    rows = Order.query.all()
    eng_orders = [to_engine_order(r, now) for r in rows]
    sched = SchedulerEngine(now).compute(eng_orders, list(_ACTIVE.values()))
    by_code = {o.code: o for o in sched.orders}
    for row in rows:
        eo = by_code.get(row.code)
        if eo:
            apply_to_row(row, eo)
    _persist_constraint_effects(constraint, rows, db)
    db.session.commit()
    return len(rows)
