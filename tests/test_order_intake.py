"""Proves new orders enter the list — manual, and rush-becomes-real.
Run: python -m tests.test_order_intake
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from datetime import datetime
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Order(db.Model):
    __tablename__ = "orders"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)
    product = db.Column(db.String(40))
    family = db.Column(db.String(60))
    line_code = db.Column(db.String(4))
    line = db.Column(db.String(20))
    qty = db.Column(db.Integer, default=1)
    phase = db.Column(db.String(20), default="mfg")
    current_stage = db.Column(db.String(40))
    status = db.Column(db.String(20), default="RUNNING")
    due = db.Column(db.String(20))
    promised = db.Column(db.String(20))
    ship_ready = db.Column(db.Boolean, default=False)
    held = db.Column(db.Boolean, default=False)
    rush = db.Column(db.Boolean, default=False)
    update_source = db.Column(db.String(20), default="erp")
    updated_by = db.Column(db.String(80))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)
    start_min = db.Column(db.Integer, default=0)
    duration_min = db.Column(db.Integer, default=240)


_LOG = []
def log_event(kind, title, detail=None, actor=None, role=None):
    _LOG.append((kind, title))


def check(label, cond):
    print(f"  {'✓' if cond else '✗'} {label}")
    assert cond, label


def main():
    from order_intake import create_order, next_order_code

    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    db.init_app(app)

    with app.app_context():
        db.create_all()
        # seed two existing orders so code generation has a baseline
        db.session.add(Order(code="SO-1044", product="DP-2051", line_code="DP",
                             line="Line 3", qty=30, current_stage="Calibration"))
        db.session.add(Order(code="SO-1047", product="PT-3051", line_code="PT",
                             line="Line 1", qty=50, current_stage="Calibration"))
        db.session.commit()

        print("\n" + "=" * 64)
        print("NEW ORDER INTAKE")
        print("=" * 64)

        start = Order.query.count()
        print(f"\nStarting order count: {start}")

        # --- 1. manual create -------------------------------------------
        print("\n1. Manual new order (Head fills the form)")
        o = create_order(db, Order, log_event,
                         line_code="LT", qty=6, due="15 Aug",
                         source="manual", actor="M. Okafor", role="Department Head")
        print(f"   created {o.code} — {o.product} ({o.line_code}) "
              f"qty {o.qty}, {o.line}, due {o.due}")
        check("order count increased", Order.query.count() == start + 1)
        check("code auto-generated past the max", o.code == "SO-1048")
        check("line + product derived from line_code",
              o.line == "Line 4" and o.product == "LT-5400")
        check("enters at intake / Order Entry",
              o.phase == "intake" and o.current_stage == "Order Entry")
        check("appears in the order list",
              Order.query.filter_by(code=o.code).first() is not None)
        check("source tagged as dept", o.update_source == "dept")

        # --- 2. explicit product override -------------------------------
        print("\n2. Manual order with an explicit model code")
        o2 = create_order(db, Order, log_event,
                          line_code="PT", qty=25, due="20 Aug",
                          product="PT-3051-SIL2", source="manual",
                          actor="S. Kane", role="Admin")
        print(f"   created {o2.code} — {o2.product}")
        check("explicit product kept", o2.product == "PT-3051-SIL2")
        check("next code increments again", o2.code == "SO-1049")

        # --- 3. rush order becomes real ---------------------------------
        print("\n3. Rush order committed on approval")
        o3 = create_order(db, Order, log_event,
                         line_code="DP", qty=15, due="14 Aug",
                         priority_rush=True, source="rush",
                         actor="M. Okafor", role="Department Head")
        print(f"   created {o3.code} — RUSH, qty {o3.qty}, due {o3.due}")
        check("rush flag set", o3.rush is True)
        check("status is RUSH", o3.status == "RUSH")
        check("source tagged ai (from assistant)", o3.update_source == "ai")
        check("rush order is in the list like any other",
              Order.query.filter_by(code=o3.code, rush=True).first() is not None)

        # --- 4. all three show up in the list ---------------------------
        print("\n4. The order list now contains the new orders")
        codes = [r.code for r in Order.query.order_by(Order.code).all()]
        print(f"   {codes}")
        check("count is start + 3", Order.query.count() == start + 3)
        check("audit log recorded each creation",
              len([e for e in _LOG if e[0] == "order"]) == 3)

        print("\n" + "=" * 64)
        print("ORDER INTAKE WORKS — manual entry and rush approval both write")
        print("through one create_order(); every new order lands in the list.")
        print("=" * 64)



def test_main():
    """pytest entrypoint — runs the script's checks as a test."""
    main()


if __name__ == "__main__":
    main()
