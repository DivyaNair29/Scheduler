"""Populate the database with a realistic starting floor."""
from datetime import datetime, timedelta

from models import (Confirmation, Constraint, LogEvent, MonthlyReport, Operator,
                    Order, ScheduleChange, User, db)
from services import build_schedule_changes

ORDERS = [
    # code, product, family, line_code, line, qty, phase, stage, status, due, start, dur
    # Due dates give most orders comfortable buffer (so they project ON TRACK);
    # a deliberate few are left tight or flagged to show AT RISK / HALTED /
    # RESCHEDULED / RUSH. Status here is a seed hint — the board recomputes it
    # live from projected-finish vs due, so the buffer is what actually decides.
    # Due dates are set relative to each order's ENGINE-projected finish so the
    # live status comes out as intended: most orders comfortably ON TRACK, with
    # a deliberate few AT RISK (due just before projection), one HALTED, one
    # RESCHEDULED, one RUSH, one DONE. Projections (per current engine layout):
    #   1028~18Aug 1031~20 1042~13 1044~10 1049~01Sep 1054~27 1058~12 1061~02Sep
    #   1073~25 1075~15 1077~17 1079~01Sep 1080~21 1082~04Sep 1085~25 1086~11
    #   1088~24 1090~22 1092~03Sep
    # code, product, family, line_code, line, qty, phase, stage, status, due, start, dur
    # --- Line 3 (DP) ---
    ("SO-1042", "DPT-7100", "Diff. pressure", "DP", "Line 3", 40, "mfg", "Calibration", "RESCHEDULED", "13 Aug", 240, 300),
    ("SO-1061", "DPT-7100", "Diff. pressure", "DP", "Line 3", 20, "intake", "Engineering", "ON TRACK", "10 Sep", 0, 120),
    ("SO-1073", "DPT-7100", "Diff. pressure", "DP", "Line 3", 15, "mfg", "Assembly", "RUNNING", "01 Sep", 500, 240),
    ("SO-1080", "DPT-7100", "Diff. pressure", "DP", "Line 3", 35, "mfg", "Burn-in", "RUNNING", "28 Aug", 700, 400),
    ("SO-1088", "DPT-7100", "Diff. pressure", "DP", "Line 3", 10, "quality", "Final QC", "ON TRACK", "31 Aug", 950, 180),
    # --- Line 1 (PT) ---
    ("SO-1044", "PT-3051", "Pressure", "PT", "Line 1", 25, "mfg", "Burn-in", "RUNNING", "18 Aug", 420, 360),
    ("SO-1049", "PT-3051", "Pressure", "PT", "Line 1", 100, "intake", "Kitting", "ON TRACK", "10 Sep", 60, 180),
    ("SO-1028", "PT-3051", "Pressure", "PT", "Line 1", 50, "quality", "Dispatch", "ON TRACK", "26 Aug", 1100, 160),
    ("SO-1075", "PT-3051", "Pressure", "PT", "Line 1", 60, "mfg", "Calibration", "RUSH", "14 Aug", 300, 320),
    ("SO-1082", "PT-3051", "Pressure", "PT", "Line 1", 30, "mfg", "Sensor", "RUNNING", "12 Sep", 120, 260),
    ("SO-1090", "PT-3051", "Pressure", "PT", "Line 1", 45, "quality", "Packing", "ON TRACK", "30 Aug", 880, 160),
    # --- Line 2 (TT) ---
    ("SO-1054", "TT-4400", "Temperature", "TT", "Line 2", 60, "mfg", "Assembly", "RUNNING", "03 Sep", 300, 260),
    ("SO-1031", "TT-4400", "Temperature", "TT", "Line 2", 80, "quality", "Packing", "ON TRACK", "27 Aug", 900, 200),
    ("SO-1077", "TT-4400", "Temperature", "TT", "Line 2", 25, "mfg", "Electronics", "HALTED", "16 Aug", 200, 240),
    ("SO-1085", "TT-4400", "Temperature", "TT", "Line 2", 40, "mfg", "Calibration", "AT RISK", "22 Aug", 450, 300),
    ("SO-1092", "TT-4400", "Temperature", "TT", "Line 2", 15, "intake", "Kitting", "ON TRACK", "12 Sep", 40, 160),
    # --- Line 4 (LT) ---
    ("SO-1058", "LT-6200", "Level", "LT", "Line 4", 30, "mfg", "Burn-in", "AT RISK", "10 Aug", 640, 380),
    ("SO-1079", "LT-6200", "Level", "LT", "Line 4", 20, "mfg", "Calibration", "RUNNING", "08 Sep", 380, 300),
    ("SO-1086", "LT-6200", "Level", "LT", "Line 4", 12, "quality", "Final QC", "DONE", "7 Aug", 1000, 150),
]

OPERATORS = [
    ("OP-01", "Rajesh Nair", "Calibration tech", "A", "Calibration"),
    ("OP-02", "Priya Sharma", "Assembly", "A", "Assembly"),
    ("OP-03", "Arjun Reddy", "Burn-in", "A", "Burn-in"),
    ("OP-04", "Kavya Menon", "QC inspector", "A", "Final QC"),
    ("OP-05", "Vikram Iyer", "Calibration tech", "B", "Calibration"),
    ("OP-06", "Deepak Rao", "Assembly", "B", "Assembly"),
    ("OP-07", "Harish Pillai", "Packer", "B", "Packing"),
    ("OP-08", "Meera Krishnan", "QC inspector", "B", "Final QC"),
    ("OP-09", "Ananya Gupta", "Burn-in", "C", None),
    ("OP-10", "Aditya Desai", "Assembly", "C", "Kitting"),
    ("OP-11", "Wasim Khan", "Packer", "C", "Dispatch"),
    ("OP-12", "Gautam Patel", "Calibration tech", "C", "Calibration"),
]

MONTHS = [
    ("2026-03", "Mar 2026", 204, 190, 11, 93, 4.7),
    ("2026-04", "Apr 2026", 219, 197, 17, 90, 4.8),
    ("2026-05", "May 2026", 238, 222, 12, 93, 4.5),
    ("2026-06", "Jun 2026", 261, 247, 9, 95, 4.3),
    ("2026-07", "Jul 2026", 274, 262, 7, 96, 4.1),
    ("2026-08", "Aug 2026", 168, 159, 5, 95, 4.2),
]


def seed():
    db.drop_all()
    db.create_all()
    now = datetime.utcnow()

    # Login accounts. The Employee account is "Priya Sharma", who is also
    # operator OP-02 on the floor — so assigning work to that operator shows up
    # for this logged-in employee, and the assignment loop can be demoed.
    employee = User(name="Priya Sharma", role="Employee", username="priya")
    head = User(name="Manish Agarwal", role="Department Head", username="manish")
    admin = User(name="Sanjay Kapoor", role="Admin", username="sanjay")
    # seed a shared demo password so a team can log in immediately; override with
    # the DEMO_PASSWORD env var, and users can be given real passwords later.
    try:
        from flask import current_app
        pw = current_app.config.get("DEMO_PASSWORD", "meridian123")
    except Exception:
        pw = "meridian123"
    for u in (employee, head, admin):
        u.set_password(pw)
    db.session.add_all([employee, head, admin])

    # Most orders got an update in the last shift; a few haven't been touched in
    # a couple of days so the "stale" indicator is visible in the demo.
    STALE = {"SO-1080": 2, "SO-1054": 3, "SO-1044": 2}   # code -> days since update

    # The board recomputes each order's live status from projected-finish vs due.
    # To keep a REALISTIC MIX of statuses regardless of what today's date is, we
    # set each due date as an offset from `now` driven by the seed's intended
    # status — rather than hardcoded calendar dates that go stale as time passes
    # (which made every order drift to AT RISK). Offsets in DAYS from today:
    #   comfortable future  -> ON TRACK / RUNNING / RUSH
    #   tight / just past    -> AT RISK / HALTED / RESCHEDULED
    #   in the past          -> DONE
    STATUS_DUE_DAYS = {
        "ON TRACK": 26, "RUNNING": 20, "RUSH": 4, "AT RISK": 1,
        "HALTED": 2, "RESCHEDULED": 3, "DONE": -6,
    }
    def _due_for(status, code):
        # a little per-order variety so bars don't all share one date
        base = STATUS_DUE_DAYS.get(status, 21)
        jitter = (sum(ord(ch) for ch in code) % 9)   # 0..8 deterministic
        if status in ("ON TRACK", "RUNNING"):
            base += jitter                # 20..34 days out
        elif status == "DONE":
            base -= (jitter % 4)
        due_dt = now + timedelta(days=base)
        return due_dt.strftime("%d %b")

    for code, product, family, lc, line, qty, phase, stage, status, due, start, dur in ORDERS:
        upd = now - timedelta(days=STALE[code]) if code in STALE else now - timedelta(minutes=18)
        due_str = _due_for(status, code)
        db.session.add(Order(
            code=code, product=product, family=family, line_code=lc, line=line,
            qty=qty, phase=phase, current_stage=stage, status=status, due=due_str,
            promised=due_str, ship_ready=(stage == "Dispatch"),
            rush=(code == "SO-1044"), update_source="erp", updated_by="ERP",
            updated_at=upd, start_min=start, duration_min=dur))

    db.session.commit()

    # Every operator also needs a User row, because assignments reference the
    # User table (employee_id). Without this only operators who were separately
    # created as users (Priya) could hold an assignment — which is why work
    # assigned to Rajesh Nair and others failed / didn't show. Priya keeps the
    # existing employee login; the rest get a matching Employee user.
    existing_user_names = {"Priya Sharma", "Manish Agarwal", "Sanjay Kapoor"}
    user_by_name = {"Priya Sharma": employee.id}
    for code, name, skill, shift, stage in OPERATORS:
        if name not in existing_user_names:
            u = User(name=name, role="Employee")
            db.session.add(u)
            db.session.flush()          # get u.id
            user_by_name[name] = u.id
    db.session.commit()

    for code, name, skill, shift, stage in OPERATORS:
        db.session.add(Operator(code=code, name=name, skill=skill, shift=shift,
                                assigned_stage=stage,
                                user_id=user_by_name.get(name)))

    for month, label, total, shipped, cons, on_time, cycle in MONTHS:
        db.session.add(MonthlyReport(
            month=month, label=label, total_orders=total, shipped=shipped,
            constraints_raised=cons, on_time_pct=on_time, avg_cycle_days=cycle))

    # one constraint still awaiting the head, one already live on the floor
    pending = Constraint(
        code="C-201", raised_by="Priya Sharma", raised_role="Employee",
        order_code="SO-1044", stage="Calibration", ctype="Material shortage",
        note="Only one reference standard available at the benches - the second "
             "bench is idle until the spare is released from stores.",
        status="pending", revision=1, created_at=now - timedelta(hours=2))
    live = Constraint(
        code="C-202", raised_by="Priya Sharma", raised_role="Employee",
        order_code="SO-1058", stage="Burn-in", ctype="Machine issue",
        note="Chamber B2 thermal controller trips intermittently before the soak "
             "completes.",
        status="applied", revision=2,
        feedback="Keep SO-1044 on Line 3 - do not push its due date.",
        created_at=now - timedelta(hours=4), decided_at=now - timedelta(hours=3))
    db.session.add_all([pending, live])
    db.session.commit()

    build_schedule_changes(live)
    for change in live.current_changes():
        change.applied = True

    db.session.add(Confirmation(
        order_code="SO-1042", stage="Calibration",
        item="3-point calibration recorded", operator="G. Petrov",
        confirmed_at=now - timedelta(hours=9)))

    for kind, title, detail, actor, role, mins in [
        ("sync", "ERP sync completed", "18 orders reconciled, 3 status changes inbound.", "ERP", "System", 600),
        ("confirm", "Calibration confirmed - SO-1042", "3-point calibration recorded, certificate drafted.", "G. Petrov", "Employee", 540),
        ("constraint", "Constraint C-202 raised - SO-1058 at Burn-in", "Machine issue - chamber B2 controller tripping before soak completes.", "Priya Sharma", "Employee", 240),
        ("approval", "Constraint C-202 approved", "Chamber B2 thermal fault accepted - schedule proposal generated.", "Manish Agarwal", "Department Head", 232),
        ("schedule", "Schedule revision 2 approved for C-202", "SO-1058 rerouted to Line 3; SO-1044 held on its 31 Jul promise.", "Manish Agarwal", "Department Head", 180),
        ("constraint", "Constraint C-201 raised - SO-1044 at Calibration", "Material shortage - only one reference standard available.", "Priya Sharma", "Employee", 120),
    ]:
        db.session.add(LogEvent(kind=kind, title=title, detail=detail, actor=actor,
                                role=role, ts=now - timedelta(minutes=mins)))

    db.session.commit()

    # Load the bundled 15-year synthetic incident history into external_history
    # and compute an initial insight rollup, so the Insights page shows history-
    # refined charts out of the box. Kept under source="hist15yr" (synthetic),
    # separate from real actuals. Safe to skip if the file isn't present.
    try:
        _load_history()
    except Exception as e:  # noqa: BLE001
        print("  (history load skipped:", e, ")")

    print("Seeded:",
          Order.query.count(), "orders,",
          Operator.query.count(), "operators,",
          Constraint.query.count(), "constraints,",
          LogEvent.query.count(), "log events")


def _load_history():
    """Import the bundled synthetic incident archive into external_history and
    compute the first insight rollup."""
    import os
    import json as _json
    from datetime import datetime
    from models import ExternalHistory
    path = os.path.join(os.path.dirname(__file__), "data", "history", "incidents.json")
    if not os.path.exists(path):
        return
    if ExternalHistory.query.filter_by(source="hist15yr").first():
        return  # already loaded
    RES = {"CAL-C1": "Calibration Bench C1", "CAL-C2": "Calibration Bench C2",
           "CAL-C3": "Calibration Bench C3", "HEL-H1": "Helium Leak Station",
           "BURN-B3": "Burn-in Chamber B3", "BURN-B4": "Burn-in Chamber B4",
           "TEST-T1": "Test Rig T1", "TEST-T2": "Test Rig T2", "SMT-E1": "SMT Line E1"}
    incidents = _json.load(open(path))["incidents"]
    n = 0
    for x in incidents:
        res_list = x["entities"].get("resources", []) or [None]
        for r in res_list:
            ext_id = x["id"] + ("_" + r if r else "")
            try:
                occ = datetime.fromisoformat(x["date"] + "T09:00:00")
            except Exception:
                occ = datetime.utcnow()
            db.session.add(ExternalHistory(
                source="hist15yr", ext_id=ext_id,
                order_code=(x["entities"].get("orders") or [None])[0],
                product=(x["entities"].get("products") or [None])[0],
                resource=RES.get(r, r), category=x["category"],
                outcome=("late" if x.get("orderLost") else "pass"),
                occurred_at=occ))
            n += 1
    db.session.commit()
    try:
        from services import compute_insight_rollup
        compute_insight_rollup(db, window_days=90, source="seed")
    except Exception:
        pass
    print(f"  history: {len(incidents)} incidents -> {n} rows loaded, rollup computed")
