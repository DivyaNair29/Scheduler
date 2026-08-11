"""Task assignment — the head→employee→head loop.

Drop into meridian_app/ and import the model into models.py (or paste the class
there). This replaces the session-only emp_order/emp_stage with a real record so:

  1. A head assigns {order, stage} to a specific employee -> shows in THAT
     employee's account.
  2. If unassigned, the employee self-selects {order, stage} -> checklist appears.
  3. When every checklist item is confirmed -> assignment flips to 'complete'
     and surfaces for the head.
  4. The head then triggers a schedule recompute (the "updated schedule pops").

Assignment lifecycle:
    assigned    -> head gave it to the employee, not yet started
    in_progress -> employee has ticked at least one item
    complete    -> all checklist items confirmed; waiting for head
    accepted    -> head acknowledged; order advances to next stage
"""
from datetime import datetime

# --- paste this class into models.py, below Confirmation ---------------------
MODEL_SOURCE = '''
class Assignment(db.Model):
    """A specific {order, stage} handed to (or picked by) one employee."""
    __tablename__ = "assignments"
    id = db.Column(db.Integer, primary_key=True)
    order_code = db.Column(db.String(20), nullable=False)
    stage = db.Column(db.String(40), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    employee_name = db.Column(db.String(80))          # denormalised for display
    assigned_by = db.Column(db.String(80))            # head name, or "self"
    source = db.Column(db.String(12), default="head") # head | self
    status = db.Column(db.String(16), default="assigned")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    accepted_at = db.Column(db.DateTime)

    employee = db.relationship("User")

    def to_dict(self):
        return {"id": self.id, "order": self.order_code, "stage": self.stage,
                "employee": self.employee_name, "assigned_by": self.assigned_by,
                "source": self.source, "status": self.status,
                "created_at": self.created_at.isoformat() if self.created_at else None,
                "completed_at": self.completed_at.isoformat() if self.completed_at else None}
'''
# ----------------------------------------------------------------------------


# Stage order used to advance an order when an assignment is accepted.
STAGE_SEQUENCE = [
    "Kitting", "Assembly", "Calibration", "Burn-in",
    "Final QC", "Packing", "Dispatch",
]


def next_stage(current: str):
    """The stage that follows `current`, or None if it's the last."""
    try:
        i = STAGE_SEQUENCE.index(current)
    except ValueError:
        return None
    return STAGE_SEQUENCE[i + 1] if i + 1 < len(STAGE_SEQUENCE) else None


def assignment_progress(db, Confirmation, checklist_for, assignment):
    """How many checklist items are ticked for this assignment."""
    items = checklist_for(assignment.stage)
    if not items:
        return 0, 0
    done = Confirmation.query.filter_by(
        order_code=assignment.order_code, stage=assignment.stage).count()
    return min(done, len(items)), len(items)


def refresh_status(db, Confirmation, checklist_for, assignment, log_event=None):
    """Recompute an assignment's status from its confirmations.
    Called after every checklist tick."""
    done, total = assignment_progress(db, Confirmation, checklist_for, assignment)
    old = assignment.status
    if total and done >= total:
        if assignment.status != "accepted":
            assignment.status = "complete"
            if not assignment.completed_at:
                assignment.completed_at = datetime.utcnow()
    elif done > 0:
        assignment.status = "in_progress"
        if not assignment.started_at:
            assignment.started_at = datetime.utcnow()
    else:
        assignment.status = "assigned"
    db.session.commit()
    if old != assignment.status and assignment.status == "complete":
        # STAGE 2: record the measured actual — real elapsed time for this stage.
        try:
            from models import record_stage_actual, Order
            start = assignment.started_at
            end = assignment.completed_at or datetime.utcnow()
            dur = int((end - start).total_seconds() / 60) if start else None
            order = Order.query.filter_by(code=assignment.order_code).first()
            record_stage_actual(
                assignment.order_code, assignment.stage,
                line_code=(order.line_code if order else None),
                product=(order.product if order else None),
                started_at=start, duration_min=dur,
                estimate_min=(order.duration_min if order else None),
                operator=assignment.employee_name, outcome="pass")
        except Exception:
            pass
        if log_event:
            log_event("confirm",
                      f"{assignment.order_code} — {assignment.stage} completed",
                      f"All checklist items confirmed by {assignment.employee_name}. "
                      f"Ready for the head to advance the schedule.",
                      actor=assignment.employee_name, role="Employee")
    return assignment.status
