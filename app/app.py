"""Meridian Instruments - AI Production Scheduler (Flask backend).

Run:
    pip install -r requirements.txt
    flask --app app init-db      # create + seed meridian.db
    flask --app app run --debug  # http://127.0.0.1:5000
"""
from datetime import datetime

import click
from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   render_template_string, request, session, url_for)

import services
from api import api_bp
from config import Config
from models import (Confirmation, Constraint, LogEvent, MonthlyReport, Operator,
                    Order, User, db, log_event)

PHASE_LABELS = [("intake", "Front end & planning"), ("mfg", "Manufacturing"),
                ("quality", "Quality & dispatch"), ("closed", "Closed / shipped")]
PHASE_COLORS = {"intake": "#1c3d6e", "mfg": "#3f7d52", "quality": "#8a6fb0",
                "closed": "#6b7783"}
LINE_COLORS = {"PT": "#5878b5", "TT": "#bd7b3f", "DP": "#9c6bab", "LT": "#3f8fb0"}


def _ensure_columns(db):
    """Idempotent, additive column migration for SQLite. create_all() creates
    missing tables but never alters existing ones, so newly-added columns on
    the orders table (batch-split lineage) are added here if absent. Existing
    rows and data are untouched."""
    from sqlalchemy import inspect, text
    wanted = {
        "orders": [
            ("active", "BOOLEAN DEFAULT 1"),
            ("parent_code", "VARCHAR(24)"),
            ("split_part", "VARCHAR(2)"),
            ("split_into", "VARCHAR(64)"),
            ("locked", "BOOLEAN DEFAULT 0"),
            ("lock_reason", "VARCHAR(160)"),
            ("qhold", "BOOLEAN DEFAULT 0"),
            ("qhold_reason", "VARCHAR(200)"),
            ("rework_stage", "VARCHAR(40)"),
        ],
        "operators": [
            ("extra_skills", "VARCHAR(240)"),
            ("absent", "BOOLEAN DEFAULT 0"),
            ("absent_note", "VARCHAR(120)"),
        ],
        "users": [
            ("username", "VARCHAR(80)"),
            ("password_hash", "VARCHAR(255)"),
        ],
    }
    insp = inspect(db.engine)
    for table, cols in wanted.items():
        if not insp.has_table(table):
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        for name, ddl in cols:
            if name not in existing:
                db.session.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
        db.session.commit()
    # backfill: any pre-existing row with NULL active -> active
    db.session.execute(text("UPDATE orders SET active = 1 WHERE active IS NULL"))
    db.session.commit()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    db.init_app(app)

    # Create any tables that don't yet exist (idempotent; existing data and
    # rows are untouched). Lets new models — e.g. board ordering, insight
    # decisions — appear without forcing a full re-seed.
    with app.app_context():
        try:
            db.create_all()
            _ensure_columns(db)
        except Exception as exc:
            app.logger.warning("db.create_all skipped: %s", exc)

    app.register_blueprint(api_bp, url_prefix="/api")

    register_cli(app)
    register_context(app)
    register_pages(app)

    # multipage static frontend (site/) + live /api/bootstrap
    try:
        from frontend_api import frontend_bp, init_frontend
        init_frontend(app, site_dir="../site")
        app.register_blueprint(frontend_bp)
    except Exception as exc:  # frontend optional — app still runs without it
        app.logger.warning("frontend not mounted: %s", exc)

    # scheduling engine + AI assistant endpoints (/api/scheduler/*)
    try:
        from scheduler_api import scheduler_bp
        app.register_blueprint(scheduler_bp, url_prefix="/api/scheduler")
    except Exception as exc:
        app.logger.warning("scheduler API not mounted: %s", exc)

    _start_nightly_rollup(app)

    return app


def _start_nightly_rollup(app):
    """Recompute the insight rollup once at startup and then every 24h, in a
    daemon thread. Self-contained (no external cron). Safe if it fails — the
    Insights page falls back to the last rollup or the illustrative defaults."""
    import threading

    def _run_once():
        try:
            with app.app_context():
                from services import compute_insight_rollup
                compute_insight_rollup(db, window_days=90, source="nightly")
        except Exception as exc:                       # noqa: BLE001
            app.logger.warning("insight rollup failed: %s", exc)

    def _loop():
        import time
        # small delay so startup (db.create_all) finishes first
        time.sleep(5)
        _run_once()
        while True:
            time.sleep(24 * 60 * 60)
            _run_once()

    # avoid double-starting under the reloader's parent process
    import os
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        t = threading.Thread(target=_loop, daemon=True)
        t.start()


# --------------------------------------------------------------------- auth
_LOGIN_HTML = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in \u2014 AND Scheduling Assistant</title>
<style>
  :root{--ink:#1c2530;--muted:#6b7885;--line:#dfe5ec;--brand:#e6b34d;}
  *{box-sizing:border-box}
  body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
    background:#0f141a;color:var(--ink);display:flex;min-height:100vh;
    align-items:center;justify-content:center;padding:20px}
  .card{background:#fff;border-radius:14px;padding:34px 30px;width:100%;max-width:360px;
    box-shadow:0 12px 40px rgba(0,0,0,.35)}
  .brand{font-weight:800;letter-spacing:.14em;font-size:15px;color:var(--muted);margin-bottom:2px}
  h1{font-size:19px;margin:0 0 18px}
  label{display:block;font-size:12px;font-weight:700;color:var(--muted);margin:12px 0 5px}
  input{width:100%;padding:11px 12px;border:1px solid var(--line);border-radius:9px;font-size:14px}
  button{width:100%;margin-top:20px;padding:12px;border:0;border-radius:9px;background:var(--ink);
    color:#fff;font-weight:800;font-size:14px;cursor:pointer}
  button:hover{background:#2a3644}
  .err{background:#fbe7e4;color:#8a3227;border:1px solid #f0cdc7;border-radius:8px;
    padding:9px 11px;font-size:13px;margin-bottom:6px}
  .hint{font-size:11.5px;color:var(--muted);margin-top:16px;line-height:1.6;
    border-top:1px solid var(--line);padding-top:12px}
</style></head><body>
  <form class="card" method="post" action="/login">
    <div class="brand">AND</div>
    <h1>Scheduling Assistant</h1>
    {% if error %}<div class="err">{{ error }}</div>{% endif %}
    <label>Username</label>
    <input name="username" autofocus autocomplete="username">
    <label>Password</label>
    <input name="password" type="password" autocomplete="current-password">
    <button type="submit">Sign in</button>
    {{ demo_hint|safe }}
  </form>
</body></html>"""


def current_user():
    """Session-based identity. In DEMO_MODE, falls back to a Department Head so
    the role-switcher works without login. With real auth (DEMO_MODE off), returns
    the logged-in user or None (routes enforce login)."""
    from flask import current_app
    uid = session.get("user_id")
    user = User.query.get(uid) if uid else None
    if user:
        return user
    if current_app.config.get("DEMO_MODE", True):
        return User.query.filter_by(role="Department Head").first()
    return None


def require_write():
    user = current_user()
    if not user or not user.can_write:
        abort(403, "This action needs a Department Head or Admin account.")
    return user


def greeting():
    hour = datetime.now().hour
    if hour < 12:
        return "Good morning"
    return "Good afternoon" if hour < 17 else "Good evening"


# ------------------------------------------------------------------ context
def register_context(app):
    @app.context_processor
    def inject_globals():
        user = current_user()
        return {
            "user": user,
            "users": User.query.order_by(User.id).all(),
            "greeting": greeting(),
            "now": datetime.now(),
            "nav_active": request.endpoint,
            "pending_approvals": Constraint.query.filter(
                Constraint.status.in_(["pending", "approved"])).count(),
            "order_count": Order.query.count(),
            "log_count": LogEvent.query.count(),
            "assign_stages": app.config["ASSIGN_STAGES"],
            "constraint_types": services.CONSTRAINT_TYPES,
            "phase_colors": PHASE_COLORS,
            "line_colors": LINE_COLORS,
        }


# -------------------------------------------------------------------- pages
def register_pages(app):

    @app.get("/")
    def dashboard():
        # the real application is the single-page UI served at /app/. The old
        # server-rendered dashboard below is legacy; send users into the SPA.
        if current_user() is None and not app.config.get("DEMO_MODE", True):
            return redirect(url_for("login"))
        return redirect("/app/")

    @app.get("/_legacy")
    def dashboard_legacy():
        user = current_user()
        if not user or not user.can_write:
            return redirect(url_for("employee_dashboard"))
        columns = []
        for key, label in PHASE_LABELS:
            orders = Order.query.filter_by(phase=key).order_by(Order.code).all()
            columns.append({"key": key, "label": label, "color": PHASE_COLORS[key],
                            "orders": orders})
        return render_template(
            "dashboard.html", columns=columns,
            approvals=Constraint.query.order_by(Constraint.created_at.desc()).all())

    @app.get("/me")
    def employee_dashboard():
        user = current_user()
        stage = session.get("emp_stage", "")
        order_code = session.get("emp_order", "")
        done = set(session.get("emp_done", []))
        items = services.checklist_for(stage)
        return render_template(
            "employee_dashboard.html",
            orders=Order.query.order_by(Order.code).all(),
            emp_order=order_code, emp_stage=stage,
            checklist=[{"item": i, "done": i in done} for i in items],
            done_count=len([i for i in items if i in done]),
            my_constraints=Constraint.query.filter_by(raised_by=user.name)
                .order_by(Constraint.created_at.desc()).all())

    @app.post("/me/assignment")
    def set_assignment():
        session["emp_order"] = request.form.get("order", "")
        session["emp_stage"] = request.form.get("stage", "")
        session["emp_done"] = []
        return redirect(url_for("employee_dashboard"))

    @app.get("/floor")
    def live_floor():
        require_write()
        lines = []
        for code, name in [("PT", "Line 1"), ("TT", "Line 2"), ("DP", "Line 3"),
                           ("LT", "Line 4")]:
            orders = Order.query.filter_by(line=name).order_by(Order.start_min).all()
            lines.append({"name": name, "code": code, "color": LINE_COLORS[code],
                          "orders": orders})
        downstream = []
        for stage in ["Final QC", "Documentation", "Packing", "Shipping & Dispatch"]:
            lookup = {"Shipping & Dispatch": ["Shipping", "Dispatch", "Closure"],
                      "Documentation": ["Docs", "Documentation"]}.get(stage, [stage])
            downstream.append({
                "name": stage,
                "orders": Order.query.filter(Order.current_stage.in_(lookup)).all()})
        return render_template(
            "live_floor.html", lines=lines, downstream=downstream,
            insights=services.floor_insights(),
            applied=Constraint.query.filter_by(status="applied").all(),
            confirmations=Confirmation.query
                .order_by(Confirmation.confirmed_at.desc()).limit(8).all())

    @app.get("/orders")
    def orders():
        return render_template("orders.html",
                               orders=Order.query.order_by(Order.code).all())

    @app.get("/manpower")
    def manpower():
        require_write()
        shifts = []
        for key in ("A", "B", "C"):
            people = Operator.query.filter_by(shift=key).order_by(Operator.code).all()
            shifts.append({"key": key, "time": Config.SHIFT_TIME[key],
                           "people": people})
        return render_template(
            "manpower.html", shifts=shifts,
            stage_load=services.stage_load(),
            suggestions=services.optimization_suggestions(),
            tasks=[{"operator": o, "task": services.task_for(o)}
                   for o in Operator.query.order_by(Operator.code).all()])

    @app.post("/manpower/assign")
    def assign_operator():
        head = require_write()
        op = Operator.query.filter_by(code=request.form["code"]).first_or_404()
        stage = request.form.get("stage") or None
        was = op.assigned_stage or "unassigned"
        op.assigned_stage = stage
        db.session.commit()
        log_event("manpower", f"{op.name} reassigned to {stage or 'unassigned'}",
                  f"Shift {op.shift} - was {was} - task now {services.task_for(op)}",
                  actor=head.name, role=head.role)
        return redirect(url_for("manpower"))

    @app.get("/confirmations")
    def confirmations():
        user = current_user()
        if not user.can_write:
            return redirect(url_for("employee_confirmations"))
        return render_template(
            "confirmations.html",
            approvals=Constraint.query.order_by(Constraint.created_at.desc()).all(),
            floor_confirms=Confirmation.query
                .order_by(Confirmation.confirmed_at.desc()).limit(12).all())

    @app.get("/my-confirmations")
    def employee_confirmations():
        stage = session.get("emp_stage", "")
        done = set(session.get("emp_done", []))
        items = services.checklist_for(stage)
        return render_template(
            "employee_confirmations.html",
            emp_order=session.get("emp_order", ""), emp_stage=stage,
            checklist=[{"item": i, "done": i in done} for i in items],
            done_count=len([i for i in items if i in done]), total=len(items))

    @app.post("/my-confirmations/tick")
    def tick_checklist():
        user = current_user()
        item = request.form["item"]
        stage = session.get("emp_stage", "")
        order_code = session.get("emp_order", "")
        done = list(session.get("emp_done", []))
        if item in done:
            done.remove(item)
            Confirmation.query.filter_by(order_code=order_code, stage=stage,
                                         item=item, operator=user.name).delete()
            log_event("confirm", f"Un-confirmed - {order_code} at {stage}", item,
                      actor=user.name, role=user.role)
        else:
            done.append(item)
            db.session.add(Confirmation(order_code=order_code, stage=stage,
                                        item=item, operator=user.name))
            log_event("confirm", f"Confirmed - {order_code} at {stage}", item,
                      actor=user.name, role=user.role)
        session["emp_done"] = done
        db.session.commit()
        return redirect(url_for("employee_confirmations"))

    @app.post("/constraints")
    def raise_constraint():
        user = current_user()
        note = (request.form.get("note") or "").strip()
        if not note:
            flash("Describe what is blocking the work.", "error")
            return redirect(request.referrer or url_for("employee_dashboard"))
        constraint = Constraint(
            code=services.next_constraint_code(), raised_by=user.name,
            raised_role=user.role,
            order_code=request.form.get("order") or session.get("emp_order") or "-",
            stage=request.form.get("stage") or session.get("emp_stage") or "-",
            ctype=request.form.get("type") or "Material shortage", note=note)
        db.session.add(constraint)
        db.session.commit()
        log_event("constraint",
                  f"Constraint {constraint.code} raised - {constraint.order_code} "
                  f"at {constraint.stage}",
                  f"{constraint.ctype} - {note}", actor=user.name, role=user.role)
        # STAGE 2: record with an inferred category for the insights layer
        try:
            from models import record_actual_event
            ctype = (constraint.ctype or "").lower()
            category = ("machine" if any(w in ctype for w in ("machine", "equipment", "bench", "chamber", "rig"))
                        else "material" if any(w in ctype for w in ("material", "shortage", "supplier", "part", "stock"))
                        else "quality" if any(w in ctype for w in ("quality", "ncr", "defect", "hold"))
                        else "people" if any(w in ctype for w in ("manpower", "operator", "staff", "labour", "labor"))
                        else "other")
            record_actual_event("constraint", order_code=constraint.order_code,
                                stage=constraint.stage, category=category,
                                detail=f"{constraint.ctype}: {note}", actor=user.name)
        except Exception:
            pass
        flash(f"{constraint.code} sent to the production head.", "ok")
        return redirect(url_for("employee_dashboard"))

    @app.post("/constraints/<code>/decision")
    def decide_constraint(code):
        head = require_write()
        constraint = Constraint.query.filter_by(code=code).first_or_404()
        decision = request.form["decision"]
        if decision == "approve":
            constraint.status = "approved"
            constraint.decided_at = datetime.utcnow()
            db.session.commit()
            services.build_schedule_changes(constraint)
            log_event("approval", f"Constraint {code} approved",
                      "Schedule proposal generated and sent for sign-off.",
                      actor=head.name, role=head.role)
        else:
            constraint.status = "rejected"
            constraint.decided_at = datetime.utcnow()
            db.session.commit()
            log_event("approval", f"Constraint {code} rejected",
                      "No schedule change made; floor plan unchanged.",
                      actor=head.name, role=head.role)
        return redirect(request.referrer or url_for("confirmations"))

    @app.post("/constraints/<code>/schedule")
    def decide_schedule(code):
        head = require_write()
        constraint = Constraint.query.filter_by(code=code).first_or_404()
        decision = request.form["decision"]
        if decision == "approve":
            constraint.status = "applied"
            db.session.commit()
            services.apply_schedule(constraint)
            log_event("schedule", f"Schedule change approved for {code}",
                      f"Revision {constraint.revision} applied to the live floor.",
                      actor=head.name, role=head.role)
        else:
            feedback = (request.form.get("feedback") or "").strip()
            if not feedback:
                flash("Say what should change instead.", "error")
                return redirect(request.referrer or url_for("confirmations"))
            constraint.revision += 1
            constraint.feedback = feedback
            db.session.commit()
            services.build_schedule_changes(constraint)
            log_event("schedule",
                      f"Schedule rejected for {code} - revision "
                      f"{constraint.revision} requested", feedback,
                      actor=head.name, role=head.role)
        return redirect(request.referrer or url_for("confirmations"))

    @app.get("/log")
    def activity_log():
        require_write()
        kind = request.args.get("kind", "all")
        query = LogEvent.query.order_by(LogEvent.ts.desc())
        if kind != "all":
            query = query.filter_by(kind=kind)
        kinds = ["all", "constraint", "approval", "schedule", "confirm", "manpower",
                 "order", "sync"]
        counts = {k: (LogEvent.query.count() if k == "all"
                      else LogEvent.query.filter_by(kind=k).count()) for k in kinds}
        return render_template("activity_log.html", events=query.all(),
                               kinds=kinds, counts=counts, active_kind=kind)

    @app.get("/reports")
    def reports():
        require_write()
        months = MonthlyReport.query.order_by(MonthlyReport.month).all()
        return render_template("reports.html", rows=services.report_rows(months),
                               latest=months[-1] if months else None)

    @app.get("/insights")
    def insights():
        require_write()
        months = MonthlyReport.query.order_by(MonthlyReport.month).all()
        return render_template("insights.html", charts=services.insight_charts(months))

    @app.post("/switch-role/<int:user_id>")
    def switch_role(user_id):
        # demo-only convenience: instant role switch without a password. Disabled
        # when real auth is on so it can't be used to escalate to Admin.
        if not app.config.get("DEMO_MODE", True):
            abort(403, "Role switching is disabled. Please log in.")
        user = User.query.get_or_404(user_id)
        session["user_id"] = user.id
        return redirect("/app/")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            user = User.query.filter_by(username=username).first()
            if user and user.check_password(password):
                session.clear()
                session["user_id"] = user.id
                return redirect("/app/")
            error = "Incorrect username or password."
        # simple inline login page (no template dependency)
        demo_hint = ""
        if app.config.get("DEMO_MODE", True):
            demo_hint = ("<p class='hint'>Demo accounts: <b>priya</b> (operator), "
                         "<b>manish</b> (dept head), <b>sanjay</b> (admin) \u2014 "
                         "password <b>%s</b></p>" % app.config.get("DEMO_PASSWORD", ""))
        return render_template_string(_LOGIN_HTML, error=error, demo_hint=demo_hint)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.before_request
    def _require_login():
        # when real auth is on, everything except the login page + static assets
        # requires a logged-in session.
        if app.config.get("DEMO_MODE", True):
            return
        p = request.path
        if (p.startswith("/login") or p.startswith("/static")
                or p.startswith("/app/") or p == "/logout"
                or p.startswith("/img/") or p.startswith("/css/")
                or p.startswith("/js/")):
            return
        if not session.get("user_id"):
            if p.startswith("/api/"):
                return jsonify(error="authentication required"), 401
            return redirect(url_for("login"))


# ---------------------------------------------------------------------- cli
def register_cli(app):
    @app.cli.command("init-db")
    def init_db():
        """Create tables and load the demo floor."""
        from seed import seed
        seed()

    @app.cli.command("reset-db")
    def reset_db():
        """Drop everything and re-seed."""
        from seed import seed
        seed()
        click.echo("database reset")


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
