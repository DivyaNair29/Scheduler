"""Natural-language -> structured Constraint.

Rule-based parser with a wide vocabulary. No external API required, so the
assistant works offline and deterministically. If an ANTHROPIC_API_KEY is set,
llm.py can override this with a model call, but the rule parser is the reliable
default and the fallback.

The parser returns a Constraint plus a confidence and an echo string, so the UI
can show the "Constraint applied: ..." confirmation chip before acting.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from engine.domain import Constraint, ConstraintType
from engine import plant


@dataclass
class ParseResult:
    constraint: Optional[Constraint]
    confidence: float          # 0..1
    echo: str                  # human confirmation string
    unparsed: bool = False


MONTHS = {m.lower(): i for i, m in enumerate(
    ["", "January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}


# A skill maps to the resource group(s) that skill staffs. Used to turn a named
# operator's absence into a labour reduction on the right group.
SKILL_GROUP = {
    "Assembly": "ASSY", "Calibration tech": "CAL", "Burn-in": "BURNIN",
    "QC inspector": "CAL", "Packer": "ASSY",
}


def _resolve_person(text: str, roster):
    """If the text names an operator on the roster, return (name, group) for
    their skill's resource group, else (None, None). Matches full name or a
    reasonably distinctive first/last name."""
    if not roster:
        return None, None
    tl = text.lower()
    best = None
    for op in roster:
        nm = (op.get("name") or "").strip()
        if not nm:
            continue
        if nm.lower() in tl:                       # full name present
            best = op
            break
        parts = [p for p in nm.lower().split() if len(p) >= 3]
        # match a name part only if it stands as a whole word
        for p in parts:
            if re.search(r"\b" + re.escape(p) + r"\b", tl):
                best = op
                break
        if best:
            break
    if not best:
        return None, None
    grp = SKILL_GROUP.get(best.get("skill"))
    return best.get("name"), grp


def _find_date(text: str, now: datetime) -> Optional[datetime]:
    t = text.lower()
    # "till the 24th" / "until the 24" / "by the 3rd"
    m = re.search(r"(?:till|until|by|on)\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?"
                  r"(?:\s+of\s+(\w+))?", t)
    if m:
        day = int(m.group(1))
        month = now.month
        if m.group(2) and m.group(2) in MONTHS:
            month = MONTHS[m.group(2)]
        year = now.year
        if month < now.month:
            year += 1
        try:
            return datetime(year, month, day)
        except ValueError:
            return None
    # "in 18 days"
    m = re.search(r"in\s+(\d+)\s+days?", t)
    if m:
        return now + timedelta(days=int(m.group(1)))
    # "tomorrow"
    if "tomorrow" in t:
        return now + timedelta(days=1)
    return None


# Shift windows on the floor: A 06-14, B 14-22, C 22-06 (next day).
_SHIFTS = {"a": (6, 14), "b": (14, 22), "c": (22, 30)}  # C ends 06:00 next day


def _find_shift(text: str, now: datetime):
    """Detect 'shift A/B/C' (or 'A shift') and return (label, start, end) as
    datetimes for the NEXT occurrence of that shift window, else None."""
    t = text.lower()
    m = re.search(r"\bshift\s+([abc])\b", t) or re.search(r"\b([abc])\s+shift\b", t)
    if not m:
        return None
    label = m.group(1).upper()
    sh, eh = _SHIFTS[label.lower()]
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = base + timedelta(hours=sh)
    end = base + timedelta(hours=eh)
    # if that window already ended today, roll to tomorrow
    if end <= now:
        start += timedelta(days=1)
        end += timedelta(days=1)
    return (label, start, end)


def _find_order(text: str) -> Optional[str]:
    m = re.search(r"\b(SO[-\s]?\d{3,5})\b", text, re.I)
    if m:
        return m.group(1).upper().replace(" ", "-").replace("SO", "SO-").replace("SO--", "SO-")
    return None


def _find_resource_group(text: str) -> tuple[Optional[str], Optional[str]]:
    """Return (group_id, specific_unit_id or None)."""
    t = text.lower()
    # specific named unit first: C2, B2, bench c2, chamber b2
    m = re.search(r"\b(bench\s+)?c(\d)\b", t)
    if m:
        return "CAL", f"C{m.group(2)}"
    m = re.search(r"\b(chamber\s+)?b(\d)\b", t)
    if m:
        return "BURNIN", f"B{m.group(2)}"
    # group aliases (longest match wins)
    for alias in sorted(plant.RESOURCE_ALIASES, key=len, reverse=True):
        if alias in t:
            return plant.RESOURCE_ALIASES[alias], None
    return None, None


def _find_number(text: str) -> Optional[int]:
    m = re.search(r"\b(\d{1,2})\b", text)
    return int(m.group(1)) if m else None


def _find_qty(text: str) -> Optional[int]:
    m = re.search(r"(\d+)[\s-]*unit", text.lower())
    return int(m.group(1)) if m else None


def parse(text: str, now: Optional[datetime] = None,
          next_code=lambda: "C-NEW", roster=None) -> ParseResult:
    now = now or datetime.utcnow()
    t = text.lower().strip()

    order = _find_order(text)
    date = _find_date(text, now)
    group, unit = _find_resource_group(text)
    shift = _find_shift(text, now)
    person_name, person_group = _resolve_person(text, roster)

    # ---- rush order ------------------------------------------------------
    if any(w in t for w in ("rush", "squeeze in", "squeeze", "urgent order",
                            "urgent", "expedite", "asap", "fast-track", "fast track",
                            "hot order", "priority order", "jump the queue",
                            "jump queue", "insert order", "emergency order")) \
            and ("order" in t or _find_qty(t)):
        qty = _find_qty(t) or 10
        c = Constraint(
            code=next_code(), ctype=ConstraintType.RUSH_ORDER,
            order_code=order or "SO-NEW", magnitude=10,
            starts_at=now, ends_at=date,
            note=f"{qty}-unit rush order" + (f", due {date:%d %b}" if date else ""))
        return ParseResult(c, 0.8,
                           f"Rush order inserted ({qty} units"
                           + (f", due {date:%d %b}" if date else "") + ")")

    # ---- resource RESTORED / back in service -----------------------------
    # "maintenance over for C3", "C3 is back", "bring C2 back online", "clear the
    # outage on B2" — the OPPOSITE of taking a resource down. This must be
    # checked BEFORE the down/maintenance block, because phrases like
    # "maintenance over" also contain the word "maintenance" and would otherwise
    # be misread as a NEW outage.
    _RESTORE_CUES = ("back online", "back on line", "back in service", "back up",
                     "is back", "are back", "now available", "make it available",
                     "available again", "returned to service", "return to service",
                     "resume", "resumed", "restored", "restore", "up and running",
                     "maintenance over", "maintenance complete", "maintenance done",
                     "maintenance finished", "servicing done", "servicing complete",
                     "repair done", "repaired", "fixed", "no longer down",
                     "no longer offline", "clear the outage", "clear outage",
                     "lift the outage", "outage over", "outage cleared",
                     "back to normal", "operational again")
    # A CANCELLATION verb ("remove/cancel/call off/drop the maintenance on C3")
    # is also a restore — the user wants the outage GONE, not a new one. Detect a
    # cancel verb sitting alongside a maintenance/outage/down word so phrases like
    # "remove the preventive maintenance of C3" clear rather than re-add it.
    _CANCEL_VERBS = ("remove", "cancel", "call off", "called off", "drop the",
                     "delete", "clear", "lift", "end the", "stop the", "take off",
                     "no more", "scrap the", "undo", "revoke")
    _OUTAGE_WORDS = ("maintenance", "servicing", "outage", "down", "offline",
                     "repair", "shutdown", "preventive")
    _is_cancel = (any(v in t for v in _CANCEL_VERBS)
                  and any(w in t for w in _OUTAGE_WORDS))
    if group and (_is_cancel or any(cue in t for cue in _RESTORE_CUES)):
        tgt = unit or plant.RESOURCE_GROUPS[group].name
        c = Constraint(
            code=next_code(), ctype=ConstraintType.RESOURCE_RESTORE,
            resource_group=group, resource_id=unit,
            starts_at=now, ends_at=None, note=text.strip())
        return ParseResult(c, 0.85, f"{tgt} back in service")

    # ---- resource down / maintenance -------------------------------------
    # A machine taken out of service — whether it broke or is scheduled for
    # maintenance/servicing — is the same scheduling fact: that resource is
    # unavailable for a window. A shift ("in shift C") scopes the window; a
    # date ("till the 24th") scopes a longer outage.
    if any(w in t for w in ("down", "offline", "off-line", "broken", "faulty",
                            "fault", "out of action", "not working", "tripped",
                            "maintenance", "servicing", "service", "repair",
                            "planned pm", "preventive", "preventative",
                            "out of service", "take offline", "taken offline")):
        if group:
            # window: prefer an explicit date; else a named shift; else open-ended
            starts_at = now
            ends_at = date
            window_note = ""
            if shift and not date:
                starts_at, ends_at = shift[1], shift[2]
                window_note = f" during Shift {shift[0]}"
            is_maint = any(w in t for w in ("maintenance", "servicing", "service",
                                            "repair", "pm", "preventive", "preventative"))
            c = Constraint(
                code=next_code(), ctype=ConstraintType.RESOURCE_DOWN,
                resource_group=group, resource_id=unit,
                magnitude=1 if not unit else None,
                starts_at=starts_at, ends_at=ends_at, note=text.strip())
            tgt = unit or plant.RESOURCE_GROUPS[group].name
            verb = "under maintenance" if is_maint else "offline"
            echo = f"{tgt} {verb}"
            if window_note:
                echo += window_note
            elif date:
                echo += f" until {date:%d %b}"
            return ParseResult(c, 0.85, echo)

    # ---- labour reduction (absence / short-staffed) ----------------------
    # Wide vocabulary for someone being unavailable, plus resolution of a named
    # operator to their skill's resource group.
    absence_words = (
        "out sick", "off sick", "sick", "short-staffed", "short staffed",
        "shorthanded", "short-handed", "understaffed", "half the",
        "team is out", "fewer", "absent", "no operators", "not available",
        "unavailable", "off today", "on leave", "annual leave", "sick leave",
        "called in sick", "not in", "not coming in", "won't be in", "wont be in",
        "away", "off duty", "no-show", "no show", "operator down", "person down",
        "man down", "one down", "two down", "missing", "can't make it",
        "cant make it", "is out", "are out", "off work", "operators short",
        "short on operators", "short of operators", "operator short",
    )
    mentions_people = any(w in t for w in (
        "operator", "operators", "staff", "worker", "workers", "technician",
        "technicians", "tech", "techs", "crew", "person", "people", "inspector",
        "inspectors", "assembler", "assemblers", "packer", "packers", "hands",
        "calibration tech", "qc")) or person_name is not None
    # "operators short/off/out on calibration" is a labour issue, not material
    if mentions_people and any(w in t for w in ("short", "few", "down", "out",
                                                "off", "away", "gone", "missing")):
        absence_words = absence_words + ("short", "off", "away", "gone", "out")
    if any(w in t for w in absence_words) or person_group:
        # group precedence: named person's group > explicit group/line > a stage
        # mention. For a line reference (Line 2 = TT etc.) the binding crew is
        # Assembly. If we still can't tell but people are named, default Assembly.
        g = person_group or group or (
            "CAL" if ("calibrat" in t or "cal tech" in t) else None)
        if g is None:
            # map a named line to its assembly crew, or a role word to its group
            for lc, gid in (("line 1", "ASSY"), ("line 2", "ASSY"),
                            ("line 3", "ASSY"), ("line 4", "ASSY"),
                            ("burn", "BURNIN"), ("assembly", "ASSY"),
                            ("assembler", "ASSY"), ("packing", "ASSY"),
                            ("packer", "ASSY"), ("qc", "CAL"), ("inspector", "CAL"),
                            ("certification", "CAL")):
                if lc in t:
                    g = gid
                    break
        if g is None and (person_name or mentions_people):
            g = "ASSY"
        if g:
            mag = 1
            if "half" in t:
                mag = max(1, plant.RESOURCE_GROUPS[g].capacity // 2)
            else:
                # a leading number ("2 techs are off", "3 operators out")
                nm = re.search(r"\b(\d{1,2})\b", t)
                if nm and any(w in t for w in ("operator", "operators", "tech",
                              "techs", "technician", "technicians", "staff",
                              "people", "person", "hands", "inspector", "packer",
                              "assembler", "crew")):
                    mag = max(1, min(int(nm.group(1)), plant.RESOURCE_GROUPS[g].capacity))
                elif any(w in t for w in ("two", "2 ", "couple", "pair")):
                    mag = 2
                elif "three" in t:
                    mag = 3
            starts_at, ends_at = now, date
            window_note = ""
            if not date and ("today" in t or "todays" in t):
                ends_at = now.replace(hour=23, minute=59, second=0, microsecond=0)
                window_note = " today"
            elif not date and "tomorrow" in t:
                from datetime import timedelta as _td
                ends_at = (now + _td(days=1)).replace(hour=23, minute=59)
                window_note = " tomorrow"
            elif shift and not date:
                starts_at, ends_at = shift[1], shift[2]
                window_note = f" during Shift {shift[0]}"
            c = Constraint(
                code=next_code(), ctype=ConstraintType.LABOUR_REDUCTION,
                resource_group=g, magnitude=mag,
                starts_at=starts_at, ends_at=ends_at, note=text.strip())
            who = person_name or f"{plant.RESOURCE_GROUPS[g].name}"
            if person_name:
                echo = (f"{person_name} unavailable{window_note} — "
                        f"{plant.RESOURCE_GROUPS[g].name} down 1")
            else:
                echo = (f"{plant.RESOURCE_GROUPS[g].name} capacity down by {mag}"
                        + window_note)
            if date:
                echo += f" until {date:%d %b}"
            return ParseResult(c, 0.78, echo)

    # ---- material delay --------------------------------------------------
    if any(w in t for w in ("material", "materials", "stock", "out of stock",
                            "part", "parts", "component", "components", "diaphragm",
                            "sensor element", "shortage", "short on", "waiting for",
                            "waiting on", "delayed", "supplier", "vendor",
                            "back-order", "backorder", "back order", "not arrived",
                            "hasn't arrived", "kit short", "awaiting parts",
                            "raw material")):
        c = Constraint(
            code=next_code(), ctype=ConstraintType.MATERIAL_DELAY,
            order_code=order, starts_at=date or (now + timedelta(days=3)),
            note=text.strip())
        return ParseResult(c, 0.7,
                           (f"{order} " if order else "")
                           + f"material delayed"
                           + (f" to {date:%d %b}" if date else ""))

    # ---- priority change -------------------------------------------------
    if any(w in t for w in ("priority", "prioritise", "prioritize", "bump",
                            "escalate", "move up", "top priority", "high priority",
                            "needs it early", "pull forward", "pull in", "push up",
                            "make it first", "front of the line", "protect",
                            "important customer", "vip")):
        c = Constraint(
            code=next_code(), ctype=ConstraintType.PRIORITY_CHANGE,
            order_code=order, magnitude=10, note=text.strip())
        return ParseResult(c, 0.7,
                           f"{order or 'order'} priority raised to 10")

    # ---- quality hold ----------------------------------------------------
    if any(w in t for w in ("hold", "quarantine", "ncr", "non-conformance",
                            "nonconformance", "failed", "fail", "quality issue",
                            "quality problem", "re-test", "retest", "rework",
                            "reject", "rejected", "scrap", "defect", "defective",
                            "on hold", "stop shipment", "block shipment",
                            "out of spec", "oos", "deviation")):
        c = Constraint(
            code=next_code(), ctype=ConstraintType.QUALITY_HOLD,
            order_code=order, note=text.strip())
        return ParseResult(c, 0.7,
                           f"{order or 'order'} placed on quality hold")

    # ---- capacity boost --------------------------------------------------
    if any(w in t for w in ("overtime", "extra shift", "add capacity", "weekend shift",
                            "more benches", "extra bench", "more operators",
                            "add operators", "add a bench", "add bench", "extra chamber",
                            "extra rig", "extra line", "double shift", "night shift added",
                            "bring in", "extra hands", "add staff", "more capacity",
                            "spare bench", "additional bench")):
        g = group or "CAL"
        c = Constraint(
            code=next_code(), ctype=ConstraintType.CAPACITY_BOOST,
            resource_group=g, magnitude=_find_number(t) or 1, note=text.strip())
        return ParseResult(c, 0.65,
                           f"{plant.RESOURCE_GROUPS[g].name} capacity increased")

    # ---- unparsed --------------------------------------------------------
    return ParseResult(None, 0.0,
                       "I couldn't turn that into a scheduling constraint. Try naming "
                       "a resource (e.g. 'Burn-in Chamber B2 is down till the 24th') "
                       "or an order (e.g. 'bump SO-1044 to top priority').",
                       unparsed=True)
