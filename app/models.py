"""Data model for the Meridian production scheduler."""
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

ROLES = ("Employee", "Department Head", "Admin")

# constraint lifecycle:
#   pending  -> production head has not decided
#   approved -> constraint accepted, a schedule revision is awaiting sign-off
#   applied  -> schedule revision approved; the change is live on the floor
#   rejected -> constraint refused, nothing changes
CONSTRAINT_STATUSES = ("pending", "approved", "applied", "rejected")


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    role = db.Column(db.String(40), nullable=False, default="Employee")
    username = db.Column(db.String(80), unique=True, nullable=True)
    password_hash = db.Column(db.String(255), nullable=True)

    def set_password(self, raw):
        from werkzeug.security import generate_password_hash
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        from werkzeug.security import check_password_hash
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, raw)

    @property
    def initials(self):
        parts = [p for p in self.name.replace(".", " ").split() if p]
        return "".join(p[0] for p in parts)[:2].upper()

    @property
    def can_write(self):
        return self.role in ("Department Head", "Admin")

    def to_dict(self):
        return {"id": self.id, "name": self.name, "role": self.role,
                "initials": self.initials, "can_write": self.can_write}


class Order(db.Model):
    __tablename__ = "orders"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)   # SO-1044
    product = db.Column(db.String(40), nullable=False)             # PT-3051
    family = db.Column(db.String(60))                              # Pressure transmitter
    line_code = db.Column(db.String(4))                            # PT / TT / DP / LT
    line = db.Column(db.String(20))                                # Line 1..4
    qty = db.Column(db.Integer, default=1)
    phase = db.Column(db.String(20), default="mfg")                # intake|mfg|quality|closed
    current_stage = db.Column(db.String(40))
    status = db.Column(db.String(20), default="RUNNING")
    due = db.Column(db.String(20))
    promised = db.Column(db.String(20))
    ship_ready = db.Column(db.Boolean, default=False)
    held = db.Column(db.Boolean, default=False)
    rush = db.Column(db.Boolean, default=False)
    update_source = db.Column(db.String(20), default="erp")        # dept|erp|ai|override
    updated_by = db.Column(db.String(80))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)
    start_min = db.Column(db.Integer, default=0)                  # gantt offset, minutes
    duration_min = db.Column(db.Integer, default=240)
    # --- batch split lineage -------------------------------------------------
    # When a head splits an order, the original is retired (active=False) and two
    # child orders are created: <code>·A and <code>·B. Children carry parent_code
    # and a split_part label; the parent carries split_into (the child codes).
    active = db.Column(db.Boolean, default=True)          # False once split/superseded
    parent_code = db.Column(db.String(24))               # this order's split parent
    split_part = db.Column(db.String(2))                 # "A" | "B" (which part)
    split_into = db.Column(db.String(64))                # parent -> "SO-1049·A,SO-1049·B"
    # --- priority protection (#4) -------------------------------------------
    # A protected/prioritised order is flagged so the board warns before it can
    # be dragged/moved, and so the engine holds its slot.
    locked = db.Column(db.Boolean, default=False)
    lock_reason = db.Column(db.String(160))
    # --- quality hold / rework (#5) -----------------------------------------
    qhold = db.Column(db.Boolean, default=False)         # on quality hold
    qhold_reason = db.Column(db.String(200))
    rework_stage = db.Column(db.String(40))              # stage to rework back to

    def to_dict(self):
        return {
            "code": self.code, "product": self.product, "family": self.family,
            "line": self.line, "line_code": self.line_code, "qty": self.qty,
            "phase": self.phase, "stage": self.current_stage, "status": self.status,
            "due": self.due, "promised": self.promised, "ship_ready": self.ship_ready,
            "held": self.held, "rush": self.rush, "source": self.update_source,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "start_min": self.start_min, "duration_min": self.duration_min,
            "active": self.active if self.active is not None else True,
            "parent_code": self.parent_code, "split_part": self.split_part,
            "split_into": [c for c in (self.split_into or "").split(",") if c],
        }


class Operator(db.Model):
    __tablename__ = "operators"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(12), unique=True, nullable=False)   # OP-01
    name = db.Column(db.String(80), nullable=False)
    skill = db.Column(db.String(60))
    # absence marker (#2): set when a head records the operator unavailable via
    # a manpower-absence constraint or the manpower tab.
    absent = db.Column(db.Boolean, default=False)
    absent_note = db.Column(db.String(120))
    # extra individual stages this operator has been trained on beyond their
    # base skill (comma-separated stage names), e.g. "Burn-in,Packing". Set by
    # a head once the person completes training in that task.
    extra_skills = db.Column(db.String(240))
    shift = db.Column(db.String(2))                                # A|B|C
    assigned_stage = db.Column(db.String(40))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    user = db.relationship("User")

    @property
    def initials(self):
        parts = [p for p in self.name.replace(".", " ").split() if p]
        return "".join(p[0] for p in parts)[:2].upper()

    def to_dict(self):
        return {"code": self.code, "name": self.name, "skill": self.skill,
                "shift": self.shift, "stage": self.assigned_stage,
                "initials": self.initials}


class Constraint(db.Model):
    __tablename__ = "constraints"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(12), unique=True, nullable=False)   # C-201
    raised_by = db.Column(db.String(80), nullable=False)
    raised_role = db.Column(db.String(40), default="Employee")
    order_code = db.Column(db.String(20))
    stage = db.Column(db.String(40))
    ctype = db.Column(db.String(40))
    note = db.Column(db.Text)
    status = db.Column(db.String(20), default="pending")
    revision = db.Column(db.Integer, default=1)
    feedback = db.Column(db.Text)                                  # head's revision note
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    decided_at = db.Column(db.DateTime)

    changes = db.relationship(
        "ScheduleChange", back_populates="constraint",
        cascade="all, delete-orphan", order_by="ScheduleChange.id")

    @property
    def awaiting_constraint_decision(self):
        return self.status == "pending"

    @property
    def awaiting_schedule_decision(self):
        return self.status == "approved"

    @property
    def is_live(self):
        return self.status == "applied"

    def current_changes(self):
        return [c for c in self.changes if c.revision == self.revision]

    def to_dict(self):
        return {
            "code": self.code, "raised_by": self.raised_by, "role": self.raised_role,
            "order": self.order_code, "stage": self.stage, "type": self.ctype,
            "note": self.note, "status": self.status, "revision": self.revision,
            "feedback": self.feedback,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "changes": [c.to_dict() for c in self.current_changes()],
        }


class ScheduleChange(db.Model):
    """One before -> after line of a proposed schedule revision."""
    __tablename__ = "schedule_changes"
    id = db.Column(db.Integer, primary_key=True)
    constraint_id = db.Column(db.Integer, db.ForeignKey("constraints.id"), nullable=False)
    revision = db.Column(db.Integer, default=1)
    what = db.Column(db.String(120))
    from_value = db.Column(db.String(80))
    to_value = db.Column(db.String(80))
    note = db.Column(db.String(160))
    applied = db.Column(db.Boolean, default=False)

    constraint = db.relationship("Constraint", back_populates="changes")

    def to_dict(self):
        return {"what": self.what, "from": self.from_value, "to": self.to_value,
                "note": self.note, "applied": self.applied, "revision": self.revision}


class Confirmation(db.Model):
    """A checklist item an operator ticked off on the shop floor."""
    __tablename__ = "confirmations"
    id = db.Column(db.Integer, primary_key=True)
    order_code = db.Column(db.String(20))
    stage = db.Column(db.String(40))
    item = db.Column(db.String(160))
    operator = db.Column(db.String(80))
    confirmed_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {"order": self.order_code, "stage": self.stage, "item": self.item,
                "operator": self.operator,
                "at": self.confirmed_at.isoformat() if self.confirmed_at else None}


class StageSubmission(db.Model):
    """An employee's submission that a stage's checklist is complete, awaiting
    a head's approval. Only on APPROVE does the order advance on the live board.
    Reject sends it back to the employee with remarks."""
    __tablename__ = "stage_submissions"
    id = db.Column(db.Integer, primary_key=True)
    order_code = db.Column(db.String(20), nullable=False)
    stage = db.Column(db.String(40), nullable=False)
    submitted_by = db.Column(db.String(80))
    submitted_by_id = db.Column(db.Integer)
    status = db.Column(db.String(12), default="submitted")   # submitted|approved|rejected
    items_done = db.Column(db.Integer, default=0)
    items_total = db.Column(db.Integer, default=0)
    reviewed_by = db.Column(db.String(80))
    remarks = db.Column(db.String(400))
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_at = db.Column(db.DateTime)

    def to_dict(self):
        return {"id": self.id, "order": self.order_code, "stage": self.stage,
                "submittedBy": self.submitted_by, "status": self.status,
                "itemsDone": self.items_done, "itemsTotal": self.items_total,
                "reviewedBy": self.reviewed_by, "remarks": self.remarks,
                "submittedAt": self.submitted_at.isoformat() if self.submitted_at else None,
                "reviewedAt": self.reviewed_at.isoformat() if self.reviewed_at else None}


class LogEvent(db.Model):
    """Append-only audit trail: every change and confirmation lands here."""
    __tablename__ = "log_events"
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(20), nullable=False)   # order|confirm|constraint|approval|schedule|manpower|sync
    title = db.Column(db.String(200), nullable=False)
    detail = db.Column(db.Text)
    actor = db.Column(db.String(80))
    role = db.Column(db.String(40))
    ts = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {"id": self.id, "kind": self.kind, "title": self.title,
                "detail": self.detail, "actor": self.actor, "role": self.role,
                "ts": self.ts.isoformat() if self.ts else None}


class MonthlyReport(db.Model):
    __tablename__ = "monthly_reports"
    id = db.Column(db.Integer, primary_key=True)
    month = db.Column(db.String(20), unique=True)      # 2026-07
    label = db.Column(db.String(20))                    # Jul 2026
    total_orders = db.Column(db.Integer)
    shipped = db.Column(db.Integer)
    constraints_raised = db.Column(db.Integer)
    on_time_pct = db.Column(db.Integer)
    avg_cycle_days = db.Column(db.Float)

    def to_dict(self):
        return {"month": self.month, "label": self.label, "total": self.total_orders,
                "shipped": self.shipped, "constraints": self.constraints_raised,
                "on_time": self.on_time_pct, "cycle": self.avg_cycle_days}


def log_event(kind, title, detail=None, actor=None, role=None, commit=True):
    """Single funnel for the audit trail — call this from every mutation."""
    event = LogEvent(kind=kind, title=title, detail=detail,
                     actor=actor or "system", role=role or "System")
    db.session.add(event)
    if commit:
        db.session.commit()
    return event


# ==========================================================================
# STAGE 2 — MEASURED ACTUALS (append-only floor history)
# ==========================================================================
# These tables record what ACTUALLY happened on the floor, as distinct from the
# engine's ESTIMATES. This is the ground-truth history the insights layer will
# eventually learn from. Rules:
#   * append-only — corrections are new superseding rows, never edits/deletes
#   * `measured=True` marks this as real observed data (vs synthetic history,
#     which is stamped synthetic and lives outside the app)
#   * every row is timestamped in UTC at write time

class StageActual(db.Model):
    """One completed stage on one order: when it really started/finished and how
    long it actually took. Written when a stage is confirmed/advanced."""
    __tablename__ = "stage_actuals"
    id = db.Column(db.Integer, primary_key=True)
    order_code = db.Column(db.String(20), nullable=False, index=True)
    line_code = db.Column(db.String(4))
    product = db.Column(db.String(40))
    stage = db.Column(db.String(40), nullable=False)
    resource = db.Column(db.String(40))          # named bench/chamber if known
    started_at = db.Column(db.DateTime)          # when the stage began (if known)
    finished_at = db.Column(db.DateTime, default=datetime.utcnow)
    duration_min = db.Column(db.Integer)         # actual elapsed working minutes
    estimate_min = db.Column(db.Integer)         # what the engine had estimated
    operator = db.Column(db.String(80))
    outcome = db.Column(db.String(20), default="pass")   # pass | rework | scrap
    superseded_by = db.Column(db.Integer)        # id of a correcting row, if any
    measured = db.Column(db.Boolean, default=True)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {"id": self.id, "order": self.order_code, "line": self.line_code,
                "product": self.product, "stage": self.stage,
                "resource": self.resource,
                "startedAt": self.started_at.isoformat() if self.started_at else None,
                "finishedAt": self.finished_at.isoformat() if self.finished_at else None,
                "durationMin": self.duration_min, "estimateMin": self.estimate_min,
                "variancePct": (round((self.duration_min - self.estimate_min)
                                       / self.estimate_min * 100)
                                if self.estimate_min and self.duration_min else None),
                "operator": self.operator, "outcome": self.outcome,
                "measured": self.measured}


class ActualEvent(db.Model):
    """A general append-only floor event stream: confirmations, advances,
    constraints raised, overrides, outcomes. The raw material for later
    baselines. Complements the audit LogEvent, but structured for analytics."""
    __tablename__ = "actual_events"
    id = db.Column(db.Integer, primary_key=True)
    ts = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    kind = db.Column(db.String(24), index=True)   # stage_complete | constraint | override | outcome | advance
    order_code = db.Column(db.String(20), index=True)
    line_code = db.Column(db.String(4))
    stage = db.Column(db.String(40))
    resource = db.Column(db.String(40))
    category = db.Column(db.String(16))           # machine|material|quality|people (for constraints)
    value_num = db.Column(db.Float)               # duration, variance, cost, etc.
    detail = db.Column(db.String(400))
    actor = db.Column(db.String(80))
    measured = db.Column(db.Boolean, default=True)

    def to_dict(self):
        return {"id": self.id, "ts": self.ts.isoformat() if self.ts else None,
                "kind": self.kind, "order": self.order_code, "line": self.line_code,
                "stage": self.stage, "resource": self.resource,
                "category": self.category, "value": self.value_num,
                "detail": self.detail, "actor": self.actor, "measured": self.measured}


class Assignment(db.Model):
    """A specific {order, stage} handed to (or picked by) one employee."""
    __tablename__ = "assignments"
    id = db.Column(db.Integer, primary_key=True)
    order_code = db.Column(db.String(20), nullable=False)
    stage = db.Column(db.String(40), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    employee_name = db.Column(db.String(80))
    assigned_by = db.Column(db.String(80))
    source = db.Column(db.String(12), default="head")   # head | self
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


class Notification(db.Model):
    """A message to a specific user — e.g. 'you've been assigned SO-1044 · Burn-in'.
    Unread until the user opens it. Powers the employee's assignment pop-up."""
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, index=True)        # recipient
    user_name = db.Column(db.String(80))
    kind = db.Column(db.String(20), default="assignment")
    title = db.Column(db.String(140))
    detail = db.Column(db.String(300))
    order_code = db.Column(db.String(20))
    stage = db.Column(db.String(40))
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {"id": self.id, "userId": self.user_id, "kind": self.kind,
                "title": self.title, "detail": self.detail,
                "order": self.order_code, "stage": self.stage, "read": self.read,
                "createdAt": self.created_at.isoformat() if self.created_at else None}


def notify(user_id, title, *, user_name=None, detail=None, kind="assignment",
           order_code=None, stage=None, commit=True):
    """Create a notification for a user. Never raises into the caller."""
    try:
        n = Notification(user_id=user_id, user_name=user_name, title=title,
                         detail=detail, kind=kind, order_code=order_code, stage=stage)
        db.session.add(n)
        if commit:
            db.session.commit()
        return n
    except Exception:
        db.session.rollback()
        return None


def record_actual_event(kind, *, order_code=None, line_code=None, stage=None,
                        resource=None, category=None, value_num=None,
                        detail=None, actor=None, commit=True):
    """Append one structured floor event. Never raises into the caller."""
    try:
        ev = ActualEvent(kind=kind, order_code=order_code, line_code=line_code,
                         stage=stage, resource=resource, category=category,
                         value_num=value_num, detail=detail, actor=actor)
        db.session.add(ev)
        if commit:
            db.session.commit()
        return ev
    except Exception:
        db.session.rollback()
        return None


class BoardOrder(db.Model):
    """Manual per-line ordering set by a head dragging cards on the board.

    When a row exists for a line, the board lays that line's orders in this
    saved sequence instead of the engine's default (most-advanced-first). One
    row per line; `sequence` is a comma-separated list of order codes.
    Append-only in spirit: each save overwrites, and every save is logged.
    """
    __tablename__ = "board_orders"
    id = db.Column(db.Integer, primary_key=True)
    line_code = db.Column(db.String(4), unique=True, nullable=False)  # PT/TT/DP/LT
    sequence = db.Column(db.Text, nullable=False)                     # "SO-1044,SO-1042,..."
    saved_by = db.Column(db.String(80))
    saved_at = db.Column(db.DateTime, default=datetime.utcnow)

    def codes(self):
        return [c for c in (self.sequence or "").split(",") if c]

    def to_dict(self):
        return {"line_code": self.line_code, "sequence": self.codes(),
                "saved_by": self.saved_by,
                "saved_at": self.saved_at.isoformat() if self.saved_at else None}


class InsightDecision(db.Model):
    """A head's Apply / Reject decision on a floor insight, with remarks.

    Append-only: every decision is a new row, so the history of what was
    accepted or dismissed (and why) is preserved. The latest row per
    insight_id is the current state shown on the board.
    """
    __tablename__ = "insight_decisions"
    id = db.Column(db.Integer, primary_key=True)
    insight_id = db.Column(db.String(40), nullable=False, index=True)  # e.g. ins-move
    insight_title = db.Column(db.String(200))
    decision = db.Column(db.String(12), nullable=False)   # applied | rejected
    remarks = db.Column(db.Text)
    decided_by = db.Column(db.String(80))
    role = db.Column(db.String(40))
    ts = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {"id": self.id, "insight_id": self.insight_id,
                "insight_title": self.insight_title, "decision": self.decision,
                "remarks": self.remarks, "decided_by": self.decided_by,
                "role": self.role, "ts": self.ts.isoformat() if self.ts else None}


def record_stage_actual(order_code, stage, *, line_code=None, product=None,
                        resource=None, started_at=None, duration_min=None,
                        estimate_min=None, operator=None, outcome="pass",
                        commit=True):
    """Append one measured stage completion. Never raises into the caller."""
    sa = None
    try:
        sa = StageActual(order_code=order_code, stage=stage, line_code=line_code,
                         product=product, resource=resource, started_at=started_at,
                         duration_min=duration_min, estimate_min=estimate_min,
                         operator=operator, outcome=outcome)
        db.session.add(sa)
        if commit:
            db.session.commit()
    except Exception:
        db.session.rollback()
        return None
    # mirror into the event stream — independent; its failure must not undo `sa`
    try:
        record_actual_event("stage_complete", order_code=order_code,
                             line_code=line_code, stage=stage, resource=resource,
                             value_num=duration_min,
                             detail=f"{stage} {outcome}", actor=operator,
                             commit=commit)
    except Exception:
        db.session.rollback()
    return sa


class AppKV(db.Model):
    """Tiny key-value store for small app state that must survive restarts
    (e.g. which KC document ids have already been imported as orders)."""
    __tablename__ = "app_kv"
    k = db.Column(db.String(64), primary_key=True)
    v = db.Column(db.Text)


class InsightRollup(db.Model):
    """Nightly-computed insight aggregates. The Insights page reads the latest
    row here instead of recomputing from raw events on every load. One row per
    rollup run; `payload` is the JSON blob of chart-ready series.

    Written by services.compute_insight_rollup() (run nightly or on demand).
    Reading the newest row is O(1); computing it scans the actuals once."""
    __tablename__ = "insight_rollups"
    id = db.Column(db.Integer, primary_key=True)
    computed_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    window_days = db.Column(db.Integer, default=90)
    events_seen = db.Column(db.Integer, default=0)     # how much history fed it
    payload = db.Column(db.Text)                       # JSON: causes, machines, utilisation, cycle...
    source = db.Column(db.String(16), default="nightly")  # nightly | manual | seed


class ExternalHistory(db.Model):
    """Historical order outcomes imported from an outside system (ERP/MES/KC).
    Kept in its own table (not mixed with live orders) and folded into the
    insight rollup alongside internal actuals. Deduped on (source, ext_id)."""
    __tablename__ = "external_history"
    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(24), index=True)      # erp | mes | kc | csv
    ext_id = db.Column(db.String(48), index=True)      # the source's own id
    order_code = db.Column(db.String(24))
    product = db.Column(db.String(40))
    line_code = db.Column(db.String(4))
    stage = db.Column(db.String(40))
    resource = db.Column(db.String(40))
    category = db.Column(db.String(16))                # machine|material|quality|people
    duration_min = db.Column(db.Integer)
    outcome = db.Column(db.String(20))                 # pass|rework|scrap|late|ontime
    occurred_at = db.Column(db.DateTime, index=True)
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("source", "ext_id", name="uq_ext_hist"),)

