"""Order creation — one code path for every way an order enters the system.

Whether the source is a human typing the New Order form, an approved rush-order
constraint, or a future ERP sync, they all call create_order(). One place that
generates the code, fills the line/product defaults, sets the intake phase, and
writes the audit log.

Drop into meridian_app/ and import in app.py:
    from order_intake import create_order, next_order_code, LINE_DEFAULTS
"""
from __future__ import annotations

from datetime import datetime


# line_code -> (Line name, default product, family)
LINE_DEFAULTS = {
    "PT": ("Line 1", "PT-3051", "Pressure transmitter"),
    "TT": ("Line 2", "TT-644",  "Temperature transmitter"),
    "DP": ("Line 3", "DP-2051", "DP transmitter"),
    "LT": ("Line 4", "LT-5400", "Level transmitter"),
}

# every new order starts at the front of the flow
INTAKE_STAGE = "Order Entry"


def next_order_code(Order) -> str:
    """SO-#### one past the current maximum."""
    codes = [o.code for o in Order.query.all() if o.code.startswith("SO-")]
    nums = []
    for c in codes:
        try:
            nums.append(int(c.split("-")[1]))
        except (IndexError, ValueError):
            continue
    nxt = (max(nums) + 1) if nums else 1041
    return f"SO-{nxt}"


def create_order(db, Order, log_event, *,
                 line_code: str,
                 qty: int,
                 due: str,
                 product: str | None = None,
                 family: str | None = None,
                 priority_rush: bool = False,
                 code: str | None = None,
                 source: str = "manual",
                 stage: str | None = None,
                 customer: str | None = None,
                 notes: str | None = None,
                 actor: str = "system",
                 role: str = "System"):
    """Create and persist a new order. Returns the Order row.

    line_code  : PT | TT | DP | LT
    qty        : integer quantity
    due        : display date string, e.g. '15 Aug'
    product    : model code; defaults from the line
    stage      : starting stage name (default 'Order Entry'); lets an order be
                 entered partway through the route (rework, transferred order)
    customer   : customer name (recorded in the audit/notes)
    notes      : free-text planning notes (recorded in the audit)
    priority_rush : marks it RUSH (priority 10) — used by the rush-order path
    source     : manual | kc | rush | erp  (goes to update_source / audit)
    """
    line_code = (line_code or "PT").upper()
    line_name, def_product, def_family = LINE_DEFAULTS.get(
        line_code, ("Line 1", "GEN-0000", "Transmitter"))

    start_stage = (stage or INTAKE_STAGE).strip() or INTAKE_STAGE
    # an order entered mid-route is already in manufacturing, not intake
    in_manufacturing = start_stage not in (INTAKE_STAGE, "Order Entry", "Quote")
    phase = "mfg" if in_manufacturing else "intake"

    order = Order(
        code=code or next_order_code(Order),
        product=product or def_product,
        family=family or def_family,
        line_code=line_code,
        line=line_name,
        qty=int(qty or 1),
        phase=phase,
        current_stage=start_stage,
        status="RUSH" if priority_rush else ("RUNNING" if in_manufacturing else "ON TRACK"),
        due=due or "",
        promised=due or "",
        ship_ready=False,
        held=False,
        rush=bool(priority_rush),
        update_source={"manual": "dept", "kc": "erp", "rush": "ai",
                       "erp": "erp"}.get(source, "dept"),
        updated_by=actor,
        updated_at=datetime.utcnow(),
        start_min=0,
        duration_min=240,
    )
    db.session.add(order)
    db.session.commit()

    bits = [f"{order.product} ×{order.qty}", f"{line_name}"]
    if customer:
        bits.append(f"for {customer}")
    if start_stage and in_manufacturing:
        bits.append(f"entering at {start_stage}")
    if notes:
        bits.append(f"notes: {notes}")
    src_label = {"manual": "manual intake", "kc": "Knowledge Centre sync",
                 "rush": "rush order", "erp": "ERP sync"}.get(source, source)
    log_event("order",
              f"New order {order.code} created — {order.product} "
              f"×{order.qty} on {line_name}",
              f"Source: {src_label}. " + " · ".join(bits),
              actor=actor, role=role)
    return order
