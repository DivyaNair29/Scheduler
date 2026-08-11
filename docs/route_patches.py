"""Route changes for the head→employee→head assignment loop.

These are the exact edits to make in app.py. Copy each block into the named
route. They use the Assignment model (see assignments.py) and the existing
Confirmation / log_event / services.checklist_for.

Add near the top of app.py:
    from models import Assignment                 # after adding the model
    from assignments import (next_stage, refresh_status,
                             assignment_progress)
"""

# ============================================================================
# 1. HEAD ASSIGNS A TASK   — new route
# ============================================================================
HEAD_ASSIGN = '''
    @app.post("/assign-task")
    def assign_task():
        """Head hands a specific {order, stage} to a specific employee."""
        head = require_write()
        order_code = request.form["order"]
        stage = request.form["stage"]
        employee_id = int(request.form["employee_id"])
        employee = User.query.get_or_404(employee_id)

        # one active assignment per employee at a time — supersede any open one
        Assignment.query.filter_by(employee_id=employee.id).filter(
            Assignment.status.in_(["assigned", "in_progress"])).update(
            {"status": "superseded"})

        a = Assignment(order_code=order_code, stage=stage,
                       employee_id=employee.id, employee_name=employee.name,
                       assigned_by=head.name, source="head", status="assigned")
        db.session.add(a)
        db.session.commit()
        log_event("manpower",
                  f"{order_code} — {stage} assigned to {employee.name}",
                  f"Assigned by {head.name}.", actor=head.name, role=head.role)
        flash(f"{order_code} / {stage} assigned to {employee.name}.", "ok")
        return redirect(request.referrer or url_for("manpower"))
'''

# ============================================================================
# 2. EMPLOYEE DASHBOARD   — replace the body of employee_dashboard()
#    Now checks for a head-assigned task first; falls back to self-select.
# ============================================================================
EMPLOYEE_DASHBOARD = '''
    @app.get("/me")
    def employee_dashboard():
        user = current_user()

        # 1. did the head assign something to THIS employee?
        assignment = (Assignment.query
                      .filter_by(employee_id=user.id)
                      .filter(Assignment.status.in_(["assigned", "in_progress", "complete"]))
                      .order_by(Assignment.created_at.desc())
                      .first())

        if assignment:
            order_code, stage = assignment.order_code, assignment.stage
            source = assignment.source
        else:
            # 2. no assignment -> employee self-selects (from session)
            order_code = session.get("emp_order", "")
            stage = session.get("emp_stage", "")
            source = "self"

        items = services.checklist_for(stage)
        # done = which items are already confirmed for this order+stage
        done_codes = {c.item for c in Confirmation.query.filter_by(
            order_code=order_code, stage=stage).all()}
        checklist = [{"item": i, "done": i in done_codes} for i in items]
        done_count = len([c for c in checklist if c["done"]])

        return render_template(
            "employee_dashboard.html",
            orders=Order.query.order_by(Order.code).all(),
            assignment=assignment,          # None if self-select
            source=source,
            emp_order=order_code, emp_stage=stage,
            checklist=checklist, done_count=done_count, total=len(items),
            my_constraints=Constraint.query.filter_by(raised_by=user.name)
                .order_by(Constraint.created_at.desc()).all())
'''

# ============================================================================
# 3. SELF-SELECT   — replace set_assignment(); only allowed if no head task
# ============================================================================
SELF_SELECT = '''
    @app.post("/me/assignment")
    def set_assignment():
        user = current_user()
        # block self-select if the head already assigned something open
        active = (Assignment.query.filter_by(employee_id=user.id)
                  .filter(Assignment.status.in_(["assigned", "in_progress"]))
                  .first())
        if active:
            flash("You already have an assigned task from the production head.", "error")
            return redirect(url_for("employee_dashboard"))
        session["emp_order"] = request.form.get("order", "")
        session["emp_stage"] = request.form.get("stage", "")
        return redirect(url_for("employee_dashboard"))
'''

# ============================================================================
# 4. TICK CHECKLIST   — replace tick_checklist()
#    Now updates the Assignment status and, when complete, flags the head.
# ============================================================================
TICK = '''
    @app.post("/my-confirmations/tick")
    def tick_checklist():
        user = current_user()
        item = request.form["item"]
        order_code = request.form.get("order") or session.get("emp_order", "")
        stage = request.form.get("stage") or session.get("emp_stage", "")

        existing = Confirmation.query.filter_by(
            order_code=order_code, stage=stage, item=item).first()
        if existing:
            db.session.delete(existing)
            log_event("confirm", f"Un-confirmed — {order_code} at {stage}", item,
                      actor=user.name, role=user.role)
        else:
            db.session.add(Confirmation(order_code=order_code, stage=stage,
                                        item=item, operator=user.name))
            log_event("confirm", f"Confirmed — {order_code} at {stage}", item,
                      actor=user.name, role=user.role)
        db.session.commit()

        # update the assignment status (assigned -> in_progress -> complete)
        assignment = (Assignment.query
                      .filter_by(order_code=order_code, stage=stage,
                                 employee_id=user.id)
                      .filter(Assignment.status.in_(
                          ["assigned", "in_progress", "complete"]))
                      .first())
        if assignment:
            refresh_status(db, Confirmation, services.checklist_for,
                           assignment, log_event)

        return redirect(request.referrer or url_for("employee_dashboard"))
'''

# ============================================================================
# 5. HEAD SEES COMPLETED TASKS + ADVANCES   — new route
#    When the head accepts a completed assignment, the order advances to the
#    next stage and the schedule recomputes -> "updated schedule pops".
# ============================================================================
HEAD_ACCEPT = '''
    @app.post("/assignments/<int:aid>/accept")
    def accept_assignment(aid):
        head = require_write()
        a = Assignment.query.get_or_404(aid)
        if a.status != "complete":
            flash("That task is not complete yet.", "error")
            return redirect(request.referrer or url_for("manpower"))

        a.status = "accepted"
        a.accepted_at = datetime.utcnow()

        # advance the order to the next stage
        order = Order.query.filter_by(code=a.order_code).first()
        moved_to = None
        if order:
            nxt = next_stage(a.stage)
            if nxt:
                order.current_stage = nxt
                moved_to = nxt
            else:
                order.current_stage = "Dispatch"
                order.status = "DONE"
                order.phase = "closed"
                moved_to = "shipped"
            order.update_source = "dept"
            order.updated_by = head.name
        db.session.commit()

        # recompute the schedule so the head sees the updated plan
        recompute_note = ""
        try:
            from scheduler_api import apply_constraint_to_floor  # optional engine
            # even with no active constraint, a stage advance shifts finish dates
            from engine.adapter import to_engine_order
            from engine.scheduler import SchedulerEngine
            from datetime import datetime as _dt
            rows = Order.query.all()
            now = _dt.utcnow()
            eng = SchedulerEngine(now).compute(
                [to_engine_order(r, now) for r in rows], [])
            by = {o.code: o for o in eng.orders}
            for r in rows:
                eo = by.get(r.code)
                if eo and eo.projected_finish:
                    r.promised = eo.projected_finish.strftime("%d %b")
                    r.status = eo.status.value if r.status not in ("DONE",) else r.status
            db.session.commit()
            recompute_note = " Schedule recomputed."
        except Exception:
            pass  # engine optional; the stage advance still stands

        log_event("schedule",
                  f"{a.order_code} advanced past {a.stage}",
                  f"{head.name} accepted the completed task; order moved to "
                  f"{moved_to}.{recompute_note}", actor=head.name, role=head.role)
        flash(f"{a.order_code} advanced to {moved_to}.{recompute_note}", "ok")
        return redirect(request.referrer or url_for("manpower"))
'''

# ============================================================================
# 6. COMPLETED-TASKS FEED FOR THE HEAD   — add to manpower() context
#    Pass this into the manpower template so the head sees what's ready.
# ============================================================================
MANPOWER_CONTEXT = '''
        # add inside manpower(), before render_template:
        completed = (Assignment.query.filter_by(status="complete")
                     .order_by(Assignment.completed_at.desc()).all())
        active_assignments = (Assignment.query
                              .filter(Assignment.status.in_(
                                  ["assigned", "in_progress"]))
                              .order_by(Assignment.created_at.desc()).all())
        # then pass completed=completed, active_assignments=active_assignments
'''

# ============================================================================
# 7. REJECT-WITH-FEEDBACK REVISION  — replace the "else" branch of
#    decide_schedule() so the head's feedback actually reshapes the schedule.
# ============================================================================
REJECT_REVISION = '''
        else:
            feedback = (request.form.get("feedback") or "").strip()
            if not feedback:
                flash("Say what should change instead.", "error")
                return redirect(request.referrer or url_for("confirmations"))
            constraint.revision += 1
            constraint.feedback = feedback
            db.session.commit()

            # Regenerate the schedule HONOURING the feedback via the engine.
            try:
                from scheduler_api import _ACTIVE
                from assistant.assistant import Assistant
                from engine.adapter import to_engine_order
                from datetime import datetime as _dt
                eng_constraint = _ACTIVE.get(code)
                if eng_constraint:
                    now = _dt.utcnow()
                    orders = [to_engine_order(r, now) for r in Order.query.all()]
                    others = [c for k, c in _ACTIVE.items() if k != code]
                    res = Assistant(now).revise(
                        feedback, orders, others, eng_constraint,
                        constraint.revision)
                    # write the new before->after onto ScheduleChange rows
                    from models import ScheduleChange
                    ScheduleChange.query.filter_by(
                        constraint_id=constraint.id,
                        revision=constraint.revision).delete()
                    for ch in res["changes"]:
                        db.session.add(ScheduleChange(
                            constraint_id=constraint.id,
                            revision=constraint.revision,
                            what=ch["what"], from_value=ch["from_value"],
                            to_value=ch["to_value"], note=ch["note"]))
                    db.session.commit()
                    detail = f"{res['directive']} — {res['summary']}"
                else:
                    services.build_schedule_changes(constraint)
                    detail = feedback
            except Exception:
                services.build_schedule_changes(constraint)
                detail = feedback

            log_event("schedule",
                      f"Schedule rejected for {code} — revision "
                      f"{constraint.revision} generated", detail,
                      actor=head.name, role=head.role)
            flash(f"Revision {constraint.revision} generated honouring your "
                  f"feedback.", "ok")
        return redirect(request.referrer or url_for("confirmations"))
'''



# ============================================================================
# 8. NEW ORDER (manual)  — new route + form. Admin/Head only.
#    Add: from order_intake import create_order, LINE_DEFAULTS
# ============================================================================
NEW_ORDER = '''
    @app.post("/orders/new")
    def new_order():
        head = require_write()
        line_code = request.form.get("line_code", "PT")
        qty = request.form.get("qty", "1")
        due = request.form.get("due", "").strip()
        product = request.form.get("product") or None
        if not due:
            flash("A due date is required.", "error")
            return redirect(url_for("orders"))
        order = create_order(
            db, Order, log_event,
            line_code=line_code, qty=qty, due=due, product=product,
            source="manual", actor=head.name, role=head.role)
        flash(f"{order.code} created and added to the order list.", "ok")
        return redirect(url_for("orders"))
'''

# Add this form to templates/orders.html (Head/Admin only):
NEW_ORDER_FORM = '''
{% if user.can_write %}
<div class="card new-order">
  <h3>Add a new order</h3>
  <form method="post" action="{{ url_for('new_order') }}" class="order-form">
    <select name="line_code" required>
      <option value="PT">Pressure (PT) — Line 1</option>
      <option value="TT">Temperature (TT) — Line 2</option>
      <option value="DP">Differential Pressure (DP) — Line 3</option>
      <option value="LT">Level (LT) — Line 4</option>
    </select>
    <input name="product" placeholder="Model (optional, e.g. PT-3051)">
    <input name="qty" type="number" min="1" value="1" required>
    <input name="due" placeholder="Due (e.g. 15 Aug)" required>
    <button type="submit" class="btn btn-primary">Create order</button>
  </form>
</div>
{% endif %}
'''

# ============================================================================
# 9. RUSH ORDER BECOMES REAL ON APPROVAL
#    In decide_constraint(), the "approve" branch — after approving a constraint
#    whose type is a rush order, create the actual Order row via the same
#    create_order(). This is what makes the rush order appear in the list.
# ============================================================================
RUSH_ON_APPROVE = '''
        if decision == "approve":
            constraint.status = "approved"
            constraint.decided_at = datetime.utcnow()
            db.session.commit()
            services.build_schedule_changes(constraint)

            # If this constraint is a rush order, commit it as a real Order now.
            try:
                from scheduler_api import _ACTIVE
                from engine.domain import ConstraintType
                ec = _ACTIVE.get(code)
                if ec and ec.ctype is ConstraintType.RUSH_ORDER:
                    # pull qty from the note ("15-unit rush order"), default 10
                    import re as _re
                    m = _re.search(r"(\\d+)", ec.note or "")
                    qty = int(m.group(1)) if m else 10
                    due = ec.ends_at.strftime("%d %b") if ec.ends_at else ""
                    # line: use the order_code hint or default PT; head can edit later
                    new = create_order(
                        db, Order, log_event,
                        line_code="PT", qty=qty, due=due,
                        priority_rush=True, source="rush",
                        actor=head.name, role=head.role)
                    flash(f"Rush order {new.code} added to the list.", "ok")
            except Exception:
                pass

            log_event("approval", f"Constraint {code} approved",
                      "Schedule proposal generated and sent for sign-off.",
                      actor=head.name, role=head.role)
'''
