"""The AI assistant's brain.

Two things it does:
  1. answer(question)  — routes a natural-language question to a real answer
                         about a specific order, the active constraints, or the
                         current suggestions.
  2. propose(text)     — parses a constraint, runs the engine, returns the
                         before->after schedule for approval.

Rule-based and deterministic by default (no API key). If ANTHROPIC_API_KEY is
present, llm.polish() can rephrase answers more naturally — but the facts always
come from the engine, never invented by a model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from engine.domain import (Constraint, Order, OrderStatus, Schedule)
from engine.scheduler import SchedulerEngine, diff_schedules
from engine import plant
from . import parser as _parser
from . import suggestions as _suggestions


@dataclass
class Answer:
    text: str
    kind: str                 # order | constraints | suggestions | help | unknown
    data: Optional[dict] = None
    citations: Optional[list] = None


class Assistant:
    def __init__(self, now: Optional[datetime] = None):
        self.now = now or datetime.utcnow()
        self.engine = SchedulerEngine(self.now)

    # ------------------------------------------------------------------ ask
    def answer(self, question: str, orders: list[Order],
               constraints: list[Constraint]) -> Answer:
        q = question.lower().strip()
        schedule = self.engine.compute(_clone(orders), constraints)

        # order lookup: "where is SO-1058" / "status of SO-1044"
        m = re.search(r"\b(SO[-\s]?\d{3,5})\b", question, re.I)
        if m:
            code = m.group(1).upper().replace(" ", "-")
            if not code.startswith("SO-"):
                code = code.replace("SO", "SO-")
            # If they're asking how to make THIS order faster/better, that's a
            # suggestion about the order — not a status lookup.
            optimise_words = ("faster", "speed up", "sooner", "earlier",
                              "improve", "optimi", "expedite", "pull in",
                              "pull forward", "how can", "reduce", "shorten",
                              "catch up", "recover", "on time", "accelerate")
            if any(w in q for w in optimise_words):
                return self._answer_order_optimisation(code, schedule)
            return self._answer_order(code, schedule)

        # constraint lookup by code: "what is C-203?"
        cm = re.search(r"\b(C[-\s]?\d{2,4})\b", question, re.I)
        if cm:
            ccode = cm.group(1).upper().replace(" ", "-")
            if not ccode.startswith("C-"):
                ccode = ccode.replace("C", "C-")
            return self._answer_constraint_code(ccode, constraints)

        # high-priority / protected orders
        if (("priorit" in q or "protected" in q or "locked" in q or "vip" in q)
                and any(w in q for w in ("which", "what", "list", "show", "any",
                                          "highest", "high", "top"))):
            return self._answer_priority_orders(schedule)

        # status listing: "which orders are at risk / halted / delayed / late?"
        status_words = {
            "at risk": "AT RISK", "at-risk": "AT RISK", "risk": "AT RISK",
            "halted": "HALTED", "on hold": "HALTED", "held": "HALTED",
            "rescheduled": "RESCHEDULED", "resched": "RESCHEDULED",
            "rush": "RUSH", "delayed": "AT RISK", "late": "AT RISK",
            "behind": "AT RISK", "on track": "ON TRACK", "done": "DONE",
            "running": "RUNNING", "stale": "STALE",
        }
        if any(w in q for w in ("which", "what", "list", "show", "how many", "any")):
            for kw, st in status_words.items():
                if kw in q:
                    return self._answer_status_list(schedule, st, kw)

        # constraints
        if any(w in q for w in ("constraint", "disruption", "what's blocking",
                                "whats blocking", "blocking", "issues", "problems",
                                "blocked", "stopping", "what stops")):
            return self._answer_constraints(constraints)

        # suggestions
        if any(w in q for w in ("suggest", "optimi", "improve", "recommend",
                                "bottleneck", "efficiency", "what should")):
            return self._answer_suggestions(schedule)

        # capacity / throughput
        if any(w in q for w in ("capacity", "throughput", "how many", "utilis",
                                "utiliz")):
            return self._answer_capacity(schedule)

        # help
        if any(w in q for w in ("help", "what can you", "how do i")):
            return Answer(
                "Ask me about a specific order ('where is SO-1058?'), the active "
                "constraints ('what's blocking the floor?'), or optimisation "
                "suggestions ('how can I improve throughput?'). To change the "
                "schedule, describe a disruption — e.g. 'Burn-in Chamber B2 is "
                "down till the 24th'.", kind="help")

        # --- rules couldn't route it: try the LLM, grounded in real data -----
        from . import llm
        if llm.available():
            snapshot = self._snapshot(schedule, constraints)
            # recall relevant past exchanges (advisory context only)
            try:
                from . import memory
                past = memory.recall(question, k=3, kind="qa")
                if past:
                    snapshot["_recalled_history"] = [p["text"] for p in past]
            except Exception:
                pass
            text = llm.answer_freeform(question, snapshot)
            if text:
                return Answer(text, kind="llm")

        return Answer(
            "I can help with orders, constraints, suggestions and capacity. "
            "Try: \u201cwhat's blocking the floor?\u201d, \u201chow can I improve "
            "throughput?\u201d, or ask about a specific order by its code. To "
            "change the plan, describe a disruption and I'll show a preview to "
            "approve.", kind="unknown")

    def _snapshot(self, schedule, constraints) -> dict:
        """Compact, factual view of the floor for the LLM to answer FROM.
        Only real computed values — the model never sees anything invented."""
        return {
            "orders": [{
                "code": o.code, "line": o.line.value, "product": o.product,
                "qty": o.qty, "status": o.status.value,
                "current_stage": o.current_stage_id,
                "due": o.due.strftime("%d %b") if o.due else None,
                "projected_finish": o.projected_finish.strftime("%d %b")
                    if o.projected_finish else None,
                "calibration_bench": o.assigned_resource.get("09"),
                "burn_in_chamber": o.assigned_resource.get("09B"),
            } for o in schedule.orders],
            "active_constraints": [c.human() for c in constraints],
            "bottlenecks": ["Calibration (3 benches)", "Burn-in (2 chambers)",
                            "Helium leak (1 rig)"],
            "lines": {"PT": "Pressure", "TT": "Temperature",
                      "DP": "Differential Pressure", "LT": "Level"},
        }

    def _answer_order_optimisation(self, code: str, schedule: Schedule) -> Answer:
        """How to deliver a specific order faster — real, order-specific advice
        derived from where it sits and what's ahead of it."""
        o = schedule.order(code)
        if not o:
            return Answer(f"I don't have an order {code} on the floor.",
                          kind="order")

        finish = o.projected_finish.strftime("%d %b") if o.projected_finish else "?"
        due = o.due.strftime("%d %b") if o.due else "?"
        late = (o.projected_finish and o.due and o.projected_finish > o.due)

        # what stage is it at, and is that a bottleneck?
        stage_id = o.current_stage_id
        levers = []

        # orders of higher/equal priority queued ahead at the bottleneck stages
        for sid, label in (("09B", "burn-in"), ("09", "calibration")):
            mine_start = o.stage_starts.get(sid)
            if not mine_start:
                continue
            ahead = [x for x in schedule.orders
                     if x.code != o.code
                     and x.stage_ends.get(sid)
                     and x.stage_ends[sid] <= mine_start
                     and x.effective_priority <= o.effective_priority]
            if ahead:
                names = ", ".join(x.code for x in ahead[:3])
                levers.append(
                    f"resequence {label}: {code} is queued behind {names} "
                    f"of equal-or-lower priority — moving it ahead pulls its "
                    f"{label} slot earlier")

        # priority lever
        if o.effective_priority < 9:
            levers.append(
                f"raise its priority (currently {o.effective_priority}) so it "
                f"holds a slot ahead of routine work")

        # burn-in is wall-clock — the one thing you can't compress
        if stage_id == "09B" or (o.stage_starts.get("09B")
                                 and not o.stage_ends.get("09B", None) is None):
            levers.append(
                "note burn-in is a fixed soak (wall-clock) — it can't be shortened, "
                "only started sooner by clearing the stages before it")

        # capacity lever at the bottleneck
        levers.append(
            "add a calibration bench on another shift (overtime) so bottleneck "
            "work runs more in parallel")

        # split lever for larger batches
        if o.qty >= 20:
            levers.append(
                f"part-ship: split the {o.qty}-unit batch and dispatch the first "
                "units on time while the rest follow")

        head = (f"{code} is projected to finish {finish}, "
                + ("after" if late else "by") + f" its {due} due date. ")
        if late:
            head += "To pull it in:"
        else:
            head += "It's already on time, but to bring it earlier:"

        body = "\n".join(f"• {l}" for l in levers[:4])
        return Answer(head + "\n" + body, kind="suggestions",
                      data={"order": code, "levers": levers})

    def _answer_priority_orders(self, schedule) -> Answer:
        # highest effective priority orders (locked / rush / manually raised)
        ranked = sorted(schedule.orders,
                        key=lambda o: -(o.effective_priority or 0))
        top = [o for o in ranked if (o.effective_priority or 0) >= 9]
        if not top:
            # fall back to the single highest
            top = ranked[:1]
        lines = []
        for o in top[:6]:
            tags = []
            if getattr(o, "rush", False):
                tags.append("rush")
            if (o.effective_priority or 0) >= 10:
                tags.append("protected")
            tag = f" ({', '.join(tags)})" if tags else ""
            lines.append(f"{o.code} — {o.product}{tag}, priority {o.effective_priority}")
        if len(top) == 1:
            msg = f"The highest-priority order is {lines[0]}."
        else:
            msg = "Highest-priority orders right now: " + "; ".join(lines) + "."
        return Answer(msg, kind="orders",
                      citations=[{"label": "Scheduling engine",
                                  "detail": "ranked by effective priority (rush + manual protection)"}])

    def _answer_status_list(self, schedule, status, kw) -> Answer:
        matched = [o for o in schedule.orders
                   if (o.status.value if hasattr(o.status, "value") else o.status) == status]
        label = {"AT RISK": "at risk", "HALTED": "halted", "RESCHEDULED": "rescheduled",
                 "RUSH": "rush", "ON TRACK": "on track", "DONE": "done",
                 "RUNNING": "running"}.get(status, status.lower())
        if not matched:
            return Answer(f"No orders are currently {label}.", kind="orders")
        codes = ", ".join(f"{o.code} ({o.product})" for o in matched[:10])
        n = len(matched)
        msg = (f"{n} order{'s' if n != 1 else ''} {label}: {codes}"
               + ("…" if n > 10 else "") + ".")
        return Answer(msg, kind="orders",
                      citations=[{"label": "Scheduling engine",
                                  "detail": f"live status = {status}"}])

    def _answer_order(self, code: str, schedule: Schedule) -> Answer:
        o = schedule.order(code)
        if not o:
            return Answer(f"I don't have an order {code} on the floor.",
                          kind="order")
        route = plant.routing_for(o.line)
        # where is it now
        current = o.current_stage_id
        cur_name = plant.STAGES[current].name if current in plant.STAGES else "in queue"
        finish = o.projected_finish.strftime("%d %b") if o.projected_finish else "?"
        due = o.due.strftime("%d %b")
        risk = ""
        if o.status is OrderStatus.AT_RISK:
            risk = (f" It is AT RISK — projected to finish {finish}, after its "
                    f"{due} due date.")
        elif o.status is OrderStatus.HALTED:
            risk = " It is currently HALTED."
        else:
            risk = f" On the current plan it finishes {finish} (due {due})."

        # next bottleneck resource
        bench = o.assigned_resource.get("09")
        chamber = o.assigned_resource.get("09B")
        assign = ""
        if bench or chamber:
            parts = []
            if bench:
                parts.append(f"calibration on {bench}")
            if chamber:
                parts.append(f"burn-in in {chamber}")
            assign = " Scheduled for " + " and ".join(parts) + "."

        text = (f"{o.code} — {o.product} ({o.line.value}), qty {o.qty}. "
                f"Currently at {cur_name}, status {o.status.value}.{risk}{assign}")

        # --- visual stepper: each stage with its state (done|current|todo) ---
        route = plant.routing_for(o.line)
        cur_idx = next((i for i, s in enumerate(route)
                        if s.stage_id == current), 0)
        steps = []
        for i, s in enumerate(route):
            state = "done" if i < cur_idx else ("current" if i == cur_idx else "todo")
            steps.append({"stage": s.name, "state": state})

        # a short "held since / delivery at risk" line if applicable
        held = None
        if o.status in (OrderStatus.AT_RISK, OrderStatus.HALTED):
            res = chamber or bench
            held = {
                "stage": cur_name,
                "resource": res,
                "due": due, "projected": finish,
                "atRisk": o.status is OrderStatus.AT_RISK,
                "halted": o.status is OrderStatus.HALTED,
            }

        return Answer(text, kind="order", data={
            "code": o.code, "product": o.product, "line": o.line.value,
            "family": getattr(o, "family", None) or o.line.value,
            "status": o.status.value,
            "current_stage": cur_name, "projected_finish": finish, "due": due,
            "calibration": bench, "burn_in": chamber,
            "steps": steps, "held": held,
        }, citations=[
            {"label": f"Order record {o.code}",
             "detail": f"{o.product} · {o.line.value} · qty {o.qty} · due {due}"},
            {"label": "Scheduling engine",
             "detail": f"projected finish {finish}, status {o.status.value}"},
            {"label": f"Line routing — {o.line.value}",
             "detail": "plant reference (engineering estimates)"},
        ])

    def _answer_constraint_code(self, code, constraints) -> Answer:
        # look in the active in-memory constraints, then the DB register
        found = None
        for c in constraints:
            if getattr(c, "code", "").upper() == code:
                found = c
                break
        human = None
        note = None
        ctype = None
        if found:
            try:
                human = found.human()
            except Exception:
                human = None
            note = getattr(found, "note", None)
            ctype = getattr(found, "ctype", None)
        if not found:
            try:
                from models import Constraint as DBC
                row = DBC.query.filter_by(code=code).first()
                if row:
                    note = row.note
                    ctype = row.ctype
                    human = row.note or (row.ctype or "constraint")
            except Exception:
                pass
        if not human and not note:
            return Answer(
                f"I don't have a constraint on record with code {code}. Active "
                f"constraints: " + (", ".join(c.code for c in constraints) or "none")
                + ".", kind="constraints")
        txt = f"{code} is a {ctype or 'scheduling'} constraint"
        if human and human != (ctype or ""):
            txt += f": {human}"
        txt += "."
        if note and note != human:
            txt += f" Note: {note}"
        return Answer(txt, kind="constraints",
                      data={"constraints": [human or note]},
                      citations=[{"label": f"Constraint {code}",
                                  "detail": note or human or ctype or ""}])

    def _answer_constraints(self, constraints: list[Constraint]) -> Answer:
        if not constraints:
            return Answer("There are no active constraints on the floor — the "
                          "plan is running clean.", kind="constraints",
                          citations=[{"label": "Constraint register",
                                      "detail": "0 active constraints"}])
        lines = [f"{c.code}: {c.human()}" for c in constraints]
        return Answer(
            f"{len(constraints)} active constraint(s):\n"
            + "\n".join(f"• {l}" for l in lines),
            kind="constraints",
            data={"constraints": [c.human() for c in constraints]},
            citations=[{"label": "Constraint register",
                        "detail": f"{len(constraints)} active — "
                                  + ", ".join(c.code for c in constraints)}])

    def _answer_suggestions(self, schedule: Schedule) -> Answer:
        sugs = _suggestions.generate(schedule, self.now)
        if not sugs:
            return Answer("Nothing stands out to optimise right now — no orders "
                          "at risk and the bottlenecks are balanced.",
                          kind="suggestions",
                          citations=[{"label": "Scheduling engine",
                                      "detail": "no at-risk orders; bottlenecks balanced"}])
        top = sugs[:3]
        body = "\n".join(f"• {s.title} — {s.effect}" for s in top)
        return Answer(f"Top suggestion(s):\n{body}", kind="suggestions",
                      data={"suggestions": [s.to_dict() for s in sugs]},
                      citations=[
                          {"label": "Scheduling engine",
                           "detail": "greedy forward-pass + bottleneck analysis"},
                          {"label": "Floor plan",
                           "detail": f"{len(sugs)} optimisation(s) found"},
                      ])

    def _answer_capacity(self, schedule: Schedule) -> Answer:
        # units/day per line from the bottleneck (calibration)
        lines = []
        chart = []
        for lc in ("TT", "PT", "DP", "LT"):
            from engine.domain import LineCode
            line = LineCode(lc)
            cyc = plant.per_unit_minutes("09", line)
            benches = plant.RESOURCE_GROUPS["CAL"].capacity
            per_day = int(benches * 22 * 60 * 0.92 / cyc)
            lines.append(f"{lc}: ~{per_day}/day")
            names = {"TT": "Line 2 · TT", "PT": "Line 1 · PT",
                     "DP": "Line 3 · DP", "LT": "Line 4 · LT"}
            chart.append({"label": names[lc], "value": per_day, "unit": "/day"})
        return Answer(
            "Calibration-limited capacity (3 benches, 24h): " + ", ".join(lines)
            + ". Calibration is the binding bottleneck.",
            kind="capacity",
            data={"chart": {"title": "Throughput by line (units/day)",
                            "kind": "bar", "series": chart}},
            citations=[
                {"label": "Plant reference — Calibration",
                 "detail": "3 benches · per-unit cycle times (engineering estimates)"},
                {"label": "Scheduling engine",
                 "detail": "bottleneck = Calibration"},
            ])

    # ---------------------------------------------------------------- propose
    def propose(self, text: str, orders: list[Order],
                constraints: list[Constraint],
                next_code: Callable[[], str], roster: Optional[list] = None) -> dict:
        """Parse a constraint and compute the resulting schedule diff."""
        result = _parser.parse(text, self.now, next_code, roster=roster)
        constraint = result.constraint if not result.unparsed else None
        echo = result.echo

        # rules couldn't parse it -> try the LLM to map it to a constraint
        if constraint is None:
            from . import llm
            if llm.available():
                obj = llm.parse_freeform(text)
                if obj:
                    constraint, echo = self._constraint_from_llm(obj, next_code)

        if constraint is None:
            return {"ok": False, "echo": echo}

        before = self.engine.compute(_clone(orders), constraints)
        after = self.engine.compute(_clone(orders),
                                    constraints + [constraint])
        changes = diff_schedules(before, after, constraint)

        # tag each change with its order code (for the card table's first column)
        for c in changes:
            if not getattr(c, "order", ""):
                c.order = (c.what or "").split(" ")[0]

        # summary badges: moved / slipped / protected counts.
        # Count ONLY rows that represent an actual finish-date change — resource
        # reassignment rows (chamber/bench swaps) are not "moves" and must not
        # inflate the badge, or it contradicts the "N moved" summary line.
        def _is_move(c):
            note = (c.note or "").lower()
            return ("slip" in note) or ("pulled in" in note) or ("finish" in (c.what or "").lower())
        moved = sum(1 for c in changes if _is_move(c))
        slipped = sum(1 for c in changes if "slip" in (c.note or "").lower())
        reassigned = sum(1 for c in changes if "reassign" in (c.note or "").lower())
        protected = sum(1 for c in changes if "protect" in (c.note or "").lower()
                        or "vip" in (c.note or "").lower() or "rush" in (c.note or "").lower())
        badges = []
        if moved:
            badges.append({"label": f"Moved {moved} order" + ("s" if moved != 1 else ""),
                           "tone": "good"})
        if slipped:
            badges.append({"label": f"{slipped} slipped 1\u20132 days", "tone": "warn"})
        if reassigned:
            badges.append({"label": f"{reassigned} reassigned", "tone": "info"})
        if protected:
            badges.append({"label": "VIP / rush protected", "tone": "info"})

        return {
            "ok": True,
            "echo": echo,
            "confidence": getattr(result, "confidence", 0.6),
            "constraint": constraint,
            "summary": after.summary,
            "changes": [c.__dict__ for c in changes],
            "badges": badges,
        }

    def _constraint_from_llm(self, obj: dict, next_code):
        """Turn the LLM's JSON into an engine Constraint (same shape the rule
        parser produces)."""
        from engine.domain import Constraint, ConstraintType
        from datetime import datetime
        try:
            ctype = ConstraintType(obj["ctype"])
        except (ValueError, KeyError):
            return None, "I couldn't map that to a scheduling constraint."
        ends_at = None
        if obj.get("date_hint"):
            try:
                day = int(obj["date_hint"])
                month = self.now.month + (1 if day < self.now.day else 0)
                year = self.now.year + (1 if month > 12 else 0)
                ends_at = datetime(year, ((month - 1) % 12) + 1, day)
            except (ValueError, TypeError):
                pass
        c = Constraint(
            code=next_code(), ctype=ctype,
            resource_group=obj.get("resource_group"),
            resource_id=obj.get("resource_id"),
            order_code=obj.get("order_code"),
            magnitude=obj.get("magnitude"),
            ends_at=ends_at,
            note=obj.get("echo", ""))
        return c, obj.get("echo") or c.human()

    def revise(self, feedback: str, orders: list[Order],
               constraints: list[Constraint], constraint,
               revision: int) -> dict:
        """The head rejected the proposal and said what to change. Parse the
        feedback into directives and recompute so this revision honours it."""
        from .directives import parse_feedback
        directives = parse_feedback(feedback)

        before = self.engine.compute(_clone(orders), constraints)
        after = self.engine.compute(_clone(orders),
                                    constraints + [constraint], directives)
        changes = diff_schedules(before, after, constraint)
        return {
            "ok": True,
            "revision": revision,
            "directive": directives.human(),
            "directive_empty": directives.is_empty(),
            "summary": after.summary,
            "changes": [c.__dict__ for c in changes],
        }


def _clone(orders: list[Order]) -> list[Order]:
    """Deep-enough copy so scheduling doesn't mutate the caller's orders."""
    import copy
    return [copy.deepcopy(o) for o in orders]
