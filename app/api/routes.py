"""JSON API - the same domain actions as the HTML pages.

Every mutating endpoint writes to the audit log, so an integration (ERP, MES,
mobile terminal) is captured exactly like a UI action.
"""
from datetime import datetime

from flask import Blueprint, jsonify, request

import services
from models import (Confirmation, Constraint, LogEvent, MonthlyReport, Operator,
                    Order, User, db, log_event)

api_bp = Blueprint("api", __name__)


def _actor():
    name = request.headers.get("X-User", "api")
    user = User.query.filter_by(name=name).first()
    return (user.name, user.role) if user else (name, "System")


def _require_write():
    name, role = _actor()
    if role not in ("Department Head", "Admin"):
        return None, (jsonify(error="requires Department Head or Admin"), 403)
    return (name, role), None


# ------------------------------------------------------------------- reads
@api_bp.get("/orders")
def list_orders():
    query = Order.query
    if phase := request.args.get("phase"):
        query = query.filter_by(phase=phase)
    if line := request.args.get("line"):
        query = query.filter_by(line=line)
    return jsonify([o.to_dict() for o in query.order_by(Order.code)])


@api_bp.get("/orders/<code>")
def get_order(code):
    order = Order.query.filter_by(code=code).first_or_404()
    return jsonify(order.to_dict())


@api_bp.get("/operators")
def list_operators():
    query = Operator.query
    if shift := request.args.get("shift"):
        query = query.filter_by(shift=shift)
    return jsonify([o.to_dict() for o in query.order_by(Operator.code)])


@api_bp.get("/stage-load")
def stage_load():
    return jsonify(services.stage_load())


@api_bp.get("/suggestions")
def suggestions():
    return jsonify(services.optimization_suggestions())


@api_bp.get("/floor-insights")
def floor_insights():
    return jsonify(services.floor_insights())


@api_bp.get("/constraints")
def list_constraints():
    query = Constraint.query
    if status := request.args.get("status"):
        query = query.filter_by(status=status)
    return jsonify([c.to_dict() for c in query.order_by(Constraint.created_at.desc())])


@api_bp.get("/confirmations")
def list_confirmations():
    return jsonify([c.to_dict() for c in Confirmation.query
                    .order_by(Confirmation.confirmed_at.desc()).limit(100)])


@api_bp.get("/log")
def list_log():
    query = LogEvent.query.order_by(LogEvent.ts.desc())
    if kind := request.args.get("kind"):
        query = query.filter_by(kind=kind)
    return jsonify([e.to_dict() for e in query.limit(int(request.args.get("limit", 200)))])


@api_bp.get("/reports")
def api_reports():
    months = MonthlyReport.query.order_by(MonthlyReport.month).all()
    return jsonify(services.report_rows(months))


@api_bp.get("/insights")
def api_insights():
    months = MonthlyReport.query.order_by(MonthlyReport.month).all()
    return jsonify(services.insight_charts(months))


# ------------------------------------------------------------------ writes
@api_bp.post("/constraints")
def create_constraint():
    payload = request.get_json(silent=True) or {}
    note = (payload.get("note") or "").strip()
    if not note:
        return jsonify(error="note is required"), 400
    name, role = _actor()
    constraint = Constraint(
        code=services.next_constraint_code(), raised_by=name, raised_role=role,
        order_code=payload.get("order", "-"), stage=payload.get("stage", "-"),
        ctype=payload.get("type", "Material shortage"), note=note)
    db.session.add(constraint)
    db.session.commit()
    log_event("constraint",
              f"Constraint {constraint.code} raised - {constraint.order_code} "
              f"at {constraint.stage}",
              f"{constraint.ctype} - {note}", actor=name, role=role)
    return jsonify(constraint.to_dict()), 201


@api_bp.post("/constraints/<code>/approve")
def approve_constraint(code):
    who, err = _require_write()
    if err:
        return err
    name, role = who
    constraint = Constraint.query.filter_by(code=code).first_or_404()
    if constraint.status != "pending":
        return jsonify(error=f"constraint is {constraint.status}"), 409
    constraint.status = "approved"
    constraint.decided_at = datetime.utcnow()
    db.session.commit()
    services.build_schedule_changes(constraint)
    log_event("approval", f"Constraint {code} approved",
              "Schedule proposal generated and sent for sign-off.",
              actor=name, role=role)
    return jsonify(constraint.to_dict())


@api_bp.post("/constraints/<code>/reject")
def reject_constraint(code):
    who, err = _require_write()
    if err:
        return err
    name, role = who
    constraint = Constraint.query.filter_by(code=code).first_or_404()
    constraint.status = "rejected"
    constraint.decided_at = datetime.utcnow()
    db.session.commit()
    log_event("approval", f"Constraint {code} rejected",
              "No schedule change made; floor plan unchanged.", actor=name, role=role)
    return jsonify(constraint.to_dict())


@api_bp.post("/constraints/<code>/schedule/approve")
def approve_schedule(code):
    who, err = _require_write()
    if err:
        return err
    name, role = who
    constraint = Constraint.query.filter_by(code=code).first_or_404()
    if constraint.status != "approved":
        return jsonify(error="no schedule revision awaiting approval"), 409
    constraint.status = "applied"
    db.session.commit()
    services.apply_schedule(constraint)
    log_event("schedule", f"Schedule change approved for {code}",
              f"Revision {constraint.revision} applied to the live floor.",
              actor=name, role=role)
    return jsonify(constraint.to_dict())


@api_bp.post("/constraints/<code>/schedule/reject")
def reject_schedule(code):
    who, err = _require_write()
    if err:
        return err
    name, role = who
    payload = request.get_json(silent=True) or {}
    feedback = (payload.get("feedback") or "").strip()
    if not feedback:
        return jsonify(error="feedback is required so the plan can be revised"), 400
    constraint = Constraint.query.filter_by(code=code).first_or_404()
    constraint.revision += 1
    constraint.feedback = feedback
    db.session.commit()
    services.build_schedule_changes(constraint)
    log_event("schedule",
              f"Schedule rejected for {code} - revision {constraint.revision} requested",
              feedback, actor=name, role=role)
    return jsonify(constraint.to_dict())


@api_bp.post("/operators/<code>/assign")
def assign(code):
    who, err = _require_write()
    if err:
        return err
    name, role = who
    payload = request.get_json(silent=True) or {}
    operator = Operator.query.filter_by(code=code).first_or_404()
    was = operator.assigned_stage or "unassigned"
    operator.assigned_stage = payload.get("stage") or None
    db.session.commit()
    log_event("manpower",
              f"{operator.name} reassigned to {operator.assigned_stage or 'unassigned'}",
              f"Shift {operator.shift} - was {was}", actor=name, role=role)
    return jsonify(operator.to_dict())


@api_bp.post("/confirmations")
def create_confirmation():
    payload = request.get_json(silent=True) or {}
    name, role = _actor()
    confirmation = Confirmation(
        order_code=payload.get("order"), stage=payload.get("stage"),
        item=payload.get("item"), operator=name)
    db.session.add(confirmation)
    log_event("confirm",
              f"Confirmed - {confirmation.order_code} at {confirmation.stage}",
              confirmation.item, actor=name, role=role, commit=False)
    db.session.commit()
    return jsonify(confirmation.to_dict()), 201


@api_bp.patch("/orders/<code>")
def patch_order(code):
    who, err = _require_write()
    if err:
        return err
    name, role = who
    payload = request.get_json(silent=True) or {}
    reason = (payload.get("reason") or "").strip()
    if not reason:
        return jsonify(error="reason is required for a manual override"), 400
    order = Order.query.filter_by(code=code).first_or_404()
    for field in ("status", "current_stage", "phase", "held", "ship_ready"):
        if field in payload:
            setattr(order, field, payload[field])
    order.update_source = "override"
    order.updated_by = name
    order.updated_at = datetime.utcnow()
    db.session.commit()
    log_event("order", f"Planner override - {code} -> {order.status}", reason,
              actor=name, role=role)
    return jsonify(order.to_dict())
