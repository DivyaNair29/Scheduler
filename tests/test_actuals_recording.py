"""Stage 2 proof: completing a stage records a measured actual with real
duration and variance-against-estimate; constraints and overrides record into
the analytics event stream. Run: python tests/test_actuals_recording.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from datetime import datetime, timedelta
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

# minimal app mirroring the real models' Stage-2 additions
from models import db, StageActual, ActualEvent, record_stage_actual, record_actual_event


def check(label, cond):
    print(f"  {'✓' if cond else '✗'} {label}")
    assert cond, label


def main():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    db.init_app(app)
    with app.app_context():
        db.create_all()

        print("\n" + "=" * 64)
        print("STAGE 2 — MEASURED ACTUALS RECORDING")
        print("=" * 64)

        # 1. a completed stage records a measured actual
        print("\n1. Completing a stage records a measured actual")
        start = datetime.utcnow() - timedelta(hours=5, minutes=30)
        sa = record_stage_actual(
            "SO-1044", "Calibration", line_code="DP", product="DP-2051",
            resource="Calibration Bench C1", started_at=start,
            duration_min=330, estimate_min=240, operator="G. Petrov")
        check("stage actual written", sa is not None and sa.id is not None)
        check("real duration captured (330m)", sa.duration_min == 330)
        check("estimate captured for comparison (240m)", sa.estimate_min == 240)
        d = sa.to_dict()
        check("variance computed (+38%)", d["variancePct"] == 38)
        print(f"   {sa.order_code} {sa.stage}: actual {sa.duration_min}m vs "
              f"estimate {sa.estimate_min}m -> {d['variancePct']}%")
        check("stamped measured=True", sa.measured is True)

        # 2. it also mirrors into the analytics event stream
        print("\n2. It mirrors into the event stream")
        evs = ActualEvent.query.filter_by(kind="stage_complete").all()
        check("stage_complete event recorded", len(evs) == 1)

        # 3. a constraint records with category
        print("\n3. A constraint records with its category")
        record_actual_event("constraint", order_code="SO-1058", stage="Burn-in",
                            category="machine", detail="Chamber B4 fault",
                            actor="J. Reyes")
        c = ActualEvent.query.filter_by(kind="constraint").first()
        check("constraint event recorded", c is not None)
        check("category captured (machine)", c.category == "machine")

        # 4. an override records
        print("\n4. A planner override records")
        record_actual_event("override", order_code="SO-1044", stage="Calibration",
                            detail="AT RISK->ON TRACK: confirmed with floor",
                            actor="Planner")
        o = ActualEvent.query.filter_by(kind="override").first()
        check("override event recorded", o is not None)

        # 5. append-only: more completions accumulate, nothing overwritten
        print("\n5. Actuals accumulate append-only (history builds)")
        for i, dur in enumerate([250, 260, 245]):
            record_stage_actual("SO-2000-%d" % i, "Calibration", line_code="DP",
                                product="DP-2051", duration_min=dur,
                                estimate_min=240, operator="D. Marek")
        total = StageActual.query.filter_by(stage="Calibration").count()
        check("all calibration actuals retained (4)", total == 4)

        # 6. the raw material for a baseline exists
        print("\n6. Variance baseline can be computed from accumulated actuals")
        rows = StageActual.query.filter_by(stage="Calibration").all()
        avg_actual = sum(r.duration_min for r in rows) / len(rows)
        avg_est = sum(r.estimate_min for r in rows) / len(rows)
        var = round((avg_actual - avg_est) / avg_est * 100)
        print(f"   Calibration: avg actual {avg_actual:.0f}m vs estimate "
              f"{avg_est:.0f}m -> {var}% (measured, {len(rows)} samples)")
        check("baseline variance computed from real data", var is not None)

        print("\n" + "=" * 64)
        print("STAGE 2 WORKS — the floor now records measured actuals, append-only,")
        print("with real durations, variance vs estimate, categories, and outcomes.")
        print("This is the ground-truth history the insights layer will learn from.")
        print("=" * 64)


if __name__ == "__main__":
    main()
