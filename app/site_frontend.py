"""Serves the static multipage frontend (site/) and feeds it live data.

The frontend (Claude Design output) reads a single window.MERIDIAN_DATA object
and mutates it via store.js. This blueprint:

  1. serves the static pages from site/ at clean URLs
  2. exposes /api/bootstrap returning MERIDIAN_DATA in the EXACT shape the pages
     expect, but built from the live database
  3. exposes action endpoints the store repoints to (raise/approve/revise/
     confirm/intake), each mapping to the real models + engine

Wire in app.py's create_app:
    from site_frontend import site_bp
    app.register_blueprint(site_bp)

Then in site/js/store.js, replace the localStorage init with a fetch of
/api/bootstrap, and point the mutation methods at these endpoints (see
site/js/store.api.js, the drop-in replacement this module ships alongside).
"""
from __future__ import annotations

import os
from datetime import datetime

from flask import Blueprint, jsonify, request, send_from_directory, abort

# site/ lives next to the app package
SITE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "site")

site_bp = Blueprint("site", __name__)


def _models():
    from models import (Order, Constraint, Confirmation, Operator, LogEvent,
                        MonthlyReport, User, db, log_event)
    return (Order, Constraint, Confirmation, Operator, LogEvent, MonthlyReport,
            User, db, log_event)


# --------------------------------------------------------------------------
# camelCase mappers — the ORM is snake_case, the frontend expects camelCase
# --------------------------------------------------------------------------
def order_to_json(o) -> dict:
    return {
        "code": o.code, "product": o.product, "family": o.family,
        "lineCode": o.line_code, "line": o.line, "qty": o.qty,
        "phase": o.phase, "stage": o.current_stage, "status": o.status,
        "due": o.due, "promised": o.promised, "source": o.update_source,
        "startMin": o.start_min or 0, "durationMin": o.duration_min or 240,
        "rush": bool(o.rush), "held": bool(o.held),
        "shipReady": bool(o.ship_ready),
    }


def constraint_to_json(c) -> dict:
    return {
        "code": c.code, "raisedBy": c.raised_by, "role": c.raised_role,
        "order": c.order_code, "stage": c.stage, "type": c.ctype,
        "note": c.note, "status": c.status, "revision": c.revision,
        "feedback": c.feedback or "",
        "ts": c.created_at.strftime("%d %b %H:%M") if c.created_at else "",
    }


def confirmation_to_json(c) -> dict:
    return {
        "order": c.order_code, "stage": c.stage, "item": c.item,
        "operator": c.operator,
        "ts": c.confirmed_at.strftime("%d %b %H:%M") if c.confirmed_at else "",
    }


# --------------------------------------------------------------------------
# Static file serving
# --------------------------------------------------------------------------
PAGES = {"": "index.html", "dashboard": "index.html", "board": "board.html",
         "orders": "orders.html", "intake": "intake.html",
         "quality": "quality.html"}


@site_bp.get("/")
@site_bp.get("/<page>")
def page(page: str = ""):
    fname = PAGES.get(page)
    if not fname:
        # not a known page route — maybe a real asset request handled below
        abort(404)
    return send_from_directory(SITE_DIR, fname)


@site_bp.get("/css/<path:f>")
def css(f): return send_from_directory(os.path.join(SITE_DIR, "css"), f)


@site_bp.get("/js/<path:f>")
def js(f): return send_from_directory(os.path.join(SITE_DIR, "js"), f)


@site_bp.get("/img/<path:f>")
def img(f): return send_from_directory(os.path.join(SITE_DIR, "img"), f)


@site_bp.get("/assets/<path:f>")
def assets(f): return send_from_directory(os.path.join(SITE_DIR, "assets"), f)


# --------------------------------------------------------------------------
# Bootstrap — MERIDIAN_DATA built from the live database
# --------------------------------------------------------------------------
@site_bp.get("/api/bootstrap")
def bootstrap():
    (Order, Constraint, Confirmation, Operator, LogEvent, MonthlyReport,
     User, db, _) = _models()

    orders = [order_to_json(o) for o in Order.query.order_by(Order.code).all()]
    constraints = [constraint_to_json(c) for c in
                   Constraint.query.order_by(Constraint.created_at.desc()).all()]
    confirmations = [confirmation_to_json(c) for c in
                     Confirmation.query.order_by(
                         Confirmation.confirmed_at.desc()).limit(20).all()]
    users = [{"id": u.id, "name": u.name, "role": u.role, "short": u.role}
             for u in User.query.order_by(User.id).all()]

    # phases / lines / stages / checklists are reference-ish — served from config
    from config import Config
    checklists = getattr(Config, "CHECKLISTS", {})
    stages = getattr(Config, "ASSIGN_STAGES", [])

    return jsonify({
        "session": {"userId": _current_user_id(User)},
        "users": users,
        "brand": {"name": "Meridian Instruments", "tagline": "Production Scheduler",
                  "logo": "img/logo.svg"},
        "phases": [
            {"key": "intake", "label": "Front end & planning", "color": "var(--phase-intake)"},
            {"key": "mfg", "label": "Manufacturing", "color": "var(--phase-mfg)"},
            {"key": "quality", "label": "Quality", "color": "var(--phase-quality)"},
            {"key": "dispatch", "label": "Dispatch", "color": "var(--phase-dispatch, #b07bc4)"},
            {"key": "closed", "label": "Closed / shipped", "color": "var(--phase-closed)"},
        ],
        "lines": [
            {"code": "PT", "label": "Line 1"}, {"code": "TT", "label": "Line 2"},
            {"code": "DP", "label": "Line 3"}, {"code": "LT", "label": "Line 4"},
        ],
        "stages": stages,
        "constraintTypes": getattr(__import__("services"), "CONSTRAINT_TYPES", []),
        "orders": orders,
        "constraints": constraints,
        "confirmations": confirmations,
        "checklists": checklists,
        "boardInsights": _board_insights(),
    })


def _current_user_id(User):
    from flask import session
    uid = session.get("user_id")
    if uid:
        return uid
    head = User.query.filter_by(role="Department Head").first()
    return head.id if head else 1


def _board_insights():
    """Live optimisation suggestions from the engine, in the frontend's shape."""
    try:
        from engine.adapter import to_engine_order
        from engine.scheduler import SchedulerEngine
        from assistant.suggestions import generate
        from models import Order
        now = datetime.utcnow()
        eng = SchedulerEngine(now).compute(
            [to_engine_order(r, now) for r in Order.query.all()], [])
        return [{"kind": s.category.upper(), "ref": s.title,
                 "title": s.title, "detail": s.effect, "gain": s.severity}
                for s in generate(eng, now)[:4]]
    except Exception:
        return []


# --------------------------------------------------------------------------
# Action endpoints — the store's mutations, mapped to real models + engine
# --------------------------------------------------------------------------
@site_bp.post("/api/constraints/raise")
def api_raise():
    (Order, Constraint, _, _, _, _, User, db, log_event) = _models()
    import services
    from flask import session
    body = request.json or {}
    user = User.query.get(session.get("user_id")) or \
        User.query.filter_by(role="Department Head").first()
    c = Constraint(
        code=services.next_constraint_code(),
        raised_by=user.name, raised_role=user.role,
        order_code=body.get("order", "-"), stage=body.get("stage", "-"),
        ctype=body.get("type", "Material shortage"), note=body.get("note", ""))
    db.session.add(c)
    db.session.commit()
    log_event("constraint", f"Constraint {c.code} raised — {c.order_code}",
              f"{c.ctype} — {c.note}", actor=user.name, role=user.role)
    return jsonify(ok=True, constraint=constraint_to_json(c))


@site_bp.post("/api/constraints/<code>/propose")
def api_propose(code):
    """Parse nothing here — the constraint already exists; run the engine to
    produce the before→after for this constraint's disruption."""
    from assistant.assistant import Assistant
    from engine.adapter import to_engine_order
    (Order, Constraint, *_rest) = _models()
    c = Constraint.query.filter_by(code=code).first_or_404()
    now = datetime.utcnow()
    orders = [to_engine_order(r, now) for r in Order.query.all()]
    # map the stored constraint to an engine constraint via the parser on its note
    from assistant import parser as P
    parsed = P.parse(f"{c.ctype} {c.note} {c.order_code}", now, lambda: c.code)
    changes = []
    summary = ""
    if parsed.constraint:
        from engine.scheduler import SchedulerEngine, diff_schedules
        eng = SchedulerEngine(now)
        before = eng.compute([to_engine_order(r, now) for r in Order.query.all()], [])
        after = eng.compute([to_engine_order(r, now) for r in Order.query.all()],
                            [parsed.constraint])
        changes = [ch.__dict__ for ch in diff_schedules(before, after, parsed.constraint)]
        summary = after.summary
    return jsonify(ok=True, code=code, summary=summary, changes=changes)


@site_bp.post("/api/constraints/<code>/approve")
def api_approve(code):
    (_, Constraint, *_r, db, log_event) = _models()
    c = Constraint.query.filter_by(code=code).first_or_404()
    c.status = "approved"; c.decided_at = datetime.utcnow()
    db.session.commit()
    log_event("approval", f"Constraint {code} approved",
              "Schedule proposal generated.", actor="Head", role="Department Head")
    return jsonify(ok=True, status="approved")


@site_bp.post("/api/constraints/<code>/reject")
def api_reject(code):
    (_, Constraint, *_r, db, log_event) = _models()
    c = Constraint.query.filter_by(code=code).first_or_404()
    c.status = "rejected"; c.decided_at = datetime.utcnow()
    db.session.commit()
    log_event("approval", f"Constraint {code} rejected", "No change.",
              actor="Head", role="Department Head")
    return jsonify(ok=True, status="rejected")


@site_bp.post("/api/constraints/<code>/revise")
def api_revise(code):
    """Reject-with-feedback: recompute honouring the head's instruction."""
    from assistant.assistant import Assistant
    from assistant import parser as P
    from engine.adapter import to_engine_order
    (Order, Constraint, *_r, db, log_event) = _models()
    c = Constraint.query.filter_by(code=code).first_or_404()
    feedback = (request.json or {}).get("feedback", "").strip()
    if not feedback:
        return jsonify(ok=False, error="feedback required"), 400
    c.revision += 1
    c.feedback = feedback
    db.session.commit()
    now = datetime.utcnow()
    parsed = P.parse(f"{c.ctype} {c.note} {c.order_code}", now, lambda: c.code)
    result = {"summary": "", "changes": [], "directive": ""}
    if parsed.constraint:
        orders = [to_engine_order(r, now) for r in Order.query.all()]
        result = Assistant(now).revise(feedback, orders, [], parsed.constraint,
                                       c.revision)
    log_event("schedule", f"Revision {c.revision} for {code}",
              result.get("directive", feedback), actor="Head", role="Department Head")
    return jsonify(ok=True, revision=c.revision, summary=result.get("summary", ""),
                   changes=result.get("changes", []),
                   directive=result.get("directive", ""))


@site_bp.post("/api/constraints/<code>/apply")
def api_apply(code):
    """Approve the schedule → write recomputed status/dates to the floor."""
    from engine.adapter import to_engine_order, apply_to_row
    from engine.scheduler import SchedulerEngine
    from assistant import parser as P
    (Order, Constraint, *_r, db, log_event) = _models()
    c = Constraint.query.filter_by(code=code).first_or_404()
    c.status = "applied"
    now = datetime.utcnow()
    parsed = P.parse(f"{c.ctype} {c.note} {c.order_code}", now, lambda: c.code)
    active = [parsed.constraint] if parsed.constraint else []
    rows = Order.query.all()
    sched = SchedulerEngine(now).compute([to_engine_order(r, now) for r in rows], active)
    by = {o.code: o for o in sched.orders}
    for r in rows:
        eo = by.get(r.code)
        if eo:
            apply_to_row(r, eo)
    db.session.commit()
    log_event("schedule", f"Schedule applied for {code}",
              f"{len(rows)} orders recomputed on the floor.",
              actor="Head", role="Department Head")
    return jsonify(ok=True, applied=code, orders=[order_to_json(r) for r in rows])


@site_bp.post("/api/confirm")
def api_confirm():
    (Order, _, Confirmation, *_r, db, log_event) = _models()
    from flask import session
    from models import User
    body = request.json or {}
    user = User.query.get(session.get("user_id"))
    name = user.name if user else "Operator"
    db.session.add(Confirmation(order_code=body["order"], stage=body["stage"],
                                item=body["item"], operator=name))
    db.session.commit()
    log_event("confirm", f"Confirmed — {body['order']} at {body['stage']}",
              body["item"], actor=name, role="Employee")
    return jsonify(ok=True)


@site_bp.post("/api/intake")
def api_intake():
    """Create a new order from the intake form (manual, all details)."""
    from order_intake import create_order
    (Order, *_r, db, log_event) = _models()
    from flask import session
    from models import User
    body = request.json or {}
    user = User.query.get(session.get("user_id"))
    o = create_order(db, Order, log_event,
                     line_code=body.get("lineCode", "PT"),
                     qty=body.get("qty", 1), due=body.get("due", ""),
                     product=body.get("product"),
                     family=body.get("family"),
                     stage=body.get("stage"),
                     customer=body.get("customer"),
                     notes=body.get("notes"),
                     priority_rush=bool(body.get("rush")),
                     source="manual",
                     actor=user.name if user else "Head",
                     role=user.role if user else "Department Head")
    return jsonify(ok=True, order=order_to_json(o))


@site_bp.post("/api/ask")
def api_ask():
    """The assistant chat panel."""
    from assistant.assistant import Assistant
    from engine.adapter import to_engine_order
    (Order, Constraint, *_r) = _models()
    from assistant import parser as P
    q = (request.json or {}).get("question", "").strip()
    now = datetime.utcnow()
    orders = [to_engine_order(r, now) for r in Order.query.all()]
    # rebuild active engine constraints from applied rows
    active = []
    for c in Constraint.query.filter_by(status="applied").all():
        pr = P.parse(f"{c.ctype} {c.note} {c.order_code}", now, lambda: c.code)
        if pr.constraint:
            active.append(pr.constraint)
    ans = Assistant(now).answer(q, orders, active)
    return jsonify(answer=ans.text, kind=ans.kind, data=ans.data)


@site_bp.post("/api/switch-user/<int:uid>")
def api_switch_user(uid):
    from flask import session
    from models import User
    u = User.query.get_or_404(uid)
    session["user_id"] = u.id
    return jsonify(ok=True, userId=u.id, role=u.role)
