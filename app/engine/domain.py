"""Scheduling domain — framework-free dataclasses.

These are the engine's own view of the world. They are deliberately independent
of Flask/SQLAlchemy so the engine can be unit-tested and reused. An adapter
(adapter.py) maps the web app's ORM rows to and from these.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------
class LineCode(str, Enum):
    PT = "PT"      # Pressure   — highest volume, wet build
    TT = "TT"      # Temperature — dry build
    DP = "DP"      # Differential pressure — wet build, longest
    LT = "LT"      # Level — dry build, lowest volume


class TimeBasis(str, Enum):
    WORKING = "working"        # consumes staffed shift minutes
    WALL_CLOCK = "wall-clock"  # runs through nights/weekends (burn-in)


class OrderStatus(str, Enum):
    ON_TRACK = "ON TRACK"
    RUNNING = "RUNNING"
    RESCHEDULED = "RESCHEDULED"
    AT_RISK = "AT RISK"
    HALTED = "HALTED"
    RUSH = "RUSH"
    DONE = "DONE"


# --------------------------------------------------------------------------
# Reference data (the "rules of the plant")
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Stage:
    """One routing stage. `applies_to` is what makes lines differ."""
    stage_id: str
    name: str
    resource_group: str          # which pool it runs on
    per_unit_min: int
    setup_min: int
    operators: float
    time_basis: TimeBasis
    applies_to: tuple[LineCode, ...]
    is_bottleneck: bool = False

    def runs_for(self, line: LineCode) -> bool:
        return line in self.applies_to


@dataclass(frozen=True)
class ResourceUnit:
    """A single named machine, e.g. Calibration Bench C2."""
    resource_id: str
    name: str
    group_id: str                # pool it belongs to


@dataclass
class ResourceGroup:
    """A pool of interchangeable machines."""
    group_id: str
    name: str
    members: list[ResourceUnit]

    @property
    def capacity(self) -> int:
        return len(self.members)


@dataclass(frozen=True)
class Shift:
    shift_id: str
    start_hour: int              # 6 -> 06:00
    length_hours: int            # 8
    availability: float = 0.92


# --------------------------------------------------------------------------
# Transactional data (live state)
# --------------------------------------------------------------------------
@dataclass
class Order:
    code: str
    line: LineCode
    product: str
    qty: int
    due: datetime
    priority: int = 5            # 1..10, higher = more important
    current_stage_id: Optional[str] = None
    status: OrderStatus = OrderStatus.ON_TRACK
    rush: bool = False
    held: bool = False
    # populated by the engine:
    stage_starts: dict = field(default_factory=dict)   # stage_id -> datetime
    stage_ends: dict = field(default_factory=dict)
    assigned_resource: dict = field(default_factory=dict)  # stage_id -> resource_id
    projected_finish: Optional[datetime] = None

    @property
    def effective_priority(self) -> int:
        p = self.priority
        if self.rush:
            p = max(p, 9)
        return p


# --------------------------------------------------------------------------
# Constraints (disruptions the engine schedules around)
# --------------------------------------------------------------------------
class ConstraintType(str, Enum):
    RESOURCE_DOWN = "resource_down"       # a machine/bench offline
    MATERIAL_DELAY = "material_delay"     # earliest-start pushed out
    RUSH_ORDER = "rush_order"             # new high-priority order
    LABOUR_REDUCTION = "labour_reduction" # fewer operators on a shift
    PRIORITY_CHANGE = "priority_change"   # bump an order's priority
    QUALITY_HOLD = "quality_hold"         # unit blocked pending sign-off
    CAPACITY_BOOST = "capacity_boost"     # extra shift/overtime
    RESOURCE_RESTORE = "resource_restore" # a machine/bench back in service


@dataclass
class Constraint:
    code: str
    ctype: ConstraintType
    # target — interpreted by type:
    resource_group: Optional[str] = None    # for resource_down / capacity
    resource_id: Optional[str] = None        # specific named unit
    order_code: Optional[str] = None         # for priority/hold/material
    magnitude: Optional[int] = None          # capacity delta, new priority, etc.
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    note: str = ""

    def human(self) -> str:
        t = self.ctype
        if t is ConstraintType.RESOURCE_DOWN:
            tgt = self.resource_id or self.resource_group
            until = f" until {self.ends_at:%d %b}" if self.ends_at else ""
            return f"{tgt} offline{until}"
        if t is ConstraintType.MATERIAL_DELAY:
            return f"{self.order_code} material delayed to {self.starts_at:%d %b}"
        if t is ConstraintType.RUSH_ORDER:
            return f"rush order {self.order_code} inserted"
        if t is ConstraintType.LABOUR_REDUCTION:
            return f"{self.resource_group} capacity down by {self.magnitude}"
        if t is ConstraintType.PRIORITY_CHANGE:
            return f"{self.order_code} priority set to {self.magnitude}"
        if t is ConstraintType.QUALITY_HOLD:
            return f"{self.order_code} on quality hold"
        if t is ConstraintType.CAPACITY_BOOST:
            return f"{self.resource_group} capacity up by {self.magnitude}"
        if t is ConstraintType.RESOURCE_RESTORE:
            tgt = self.resource_id or self.resource_group
            return f"{tgt} back in service"
        return self.note or t.value


# --------------------------------------------------------------------------
# Schedule output
# --------------------------------------------------------------------------
@dataclass
class ScheduleChange:
    """One before -> after line, for the approval card."""
    what: str
    from_value: str
    to_value: str
    note: str
    order: str = ""


@dataclass
class Schedule:
    """The full computed plan plus a diff against the previous one."""
    orders: list[Order]
    changes: list[ScheduleChange] = field(default_factory=list)
    summary: str = ""
    generated_at: datetime = field(default_factory=datetime.utcnow)

    def order(self, code: str) -> Optional[Order]:
        return next((o for o in self.orders if o.code == code), None)
