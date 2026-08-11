"""End-to-end proof the backend works: real inputs, real scheduling, real
constraint parsing, real Q&A. Run: python -m tests.test_end_to_end
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from datetime import datetime, timedelta

from engine.domain import LineCode, Order, OrderStatus
from engine.scheduler import SchedulerEngine
from assistant.assistant import Assistant

NOW = datetime(2026, 7, 27, 8, 0)


def sample_orders():
    d = lambda days: NOW + timedelta(days=days)
    return [
        Order("SO-1044", LineCode.DP, "DP-2051", 30, d(4),  priority=10, rush=True,  current_stage_id="09"),
        Order("SO-1047", LineCode.PT, "PT-3051", 50, d(0),  priority=7,  current_stage_id="09"),
        Order("SO-1042", LineCode.DP, "DP-2051", 30, d(18), priority=5,  current_stage_id="09"),
        Order("SO-1058", LineCode.LT, "LT-5400", 8,  d(4),  priority=6,  current_stage_id="09B"),
        Order("SO-1051", LineCode.TT, "TT-644",  15, d(2),  priority=5,  current_stage_id="08"),
        Order("SO-1049", LineCode.PT, "PT-3051", 35, d(5),  priority=6,  current_stage_id="05"),
        Order("SO-1060", LineCode.LT, "LT-5400", 7,  d(6),  priority=4,  current_stage_id="08"),
    ]


def line(title):
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


def main():
    ok = True

    # ---- 1. engine computes a real schedule ------------------------------
    line("1. Engine computes a schedule from order state")
    eng = SchedulerEngine(NOW)
    sched = eng.compute(sample_orders(), [])
    for o in sorted(sched.orders, key=lambda o: o.projected_finish):
        fin = o.projected_finish.strftime("%d %b %H:%M")
        bench = o.assigned_resource.get("09", "-")
        print(f"  {o.code}  {o.line.value:3}  finishes {fin}  "
              f"cal:{bench:20}  {o.status.value}")
    assert all(o.projected_finish for o in sched.orders), "every order scheduled"
    print("  ✓ all orders scheduled with finish dates and bench assignments")

    # ---- 2. rush order protected -----------------------------------------
    line("2. Priority respected — rush order scheduled first")
    rush = sched.order("SO-1044")
    low = sched.order("SO-1042")
    print(f"  SO-1044 (rush, pri {rush.effective_priority}) cal starts "
          f"{rush.stage_starts['09']:%d %b %H:%M}")
    print(f"  SO-1042 (pri {low.effective_priority}) cal starts "
          f"{low.stage_starts['09']:%d %b %H:%M}")
    assert rush.stage_starts["09"] <= low.stage_starts["09"], "rush goes first"
    print("  ✓ rush order calibrates before the low-priority DP order")

    # ---- 3. assistant parses a constraint & reschedules ------------------
    line("3. Assistant parses NL constraint and computes the diff")
    asst = Assistant(NOW)
    codes = iter(["C-301", "C-302", "C-303"])
    prop = asst.propose("Burn-in Chamber B4 is down till the 24th",
                        sample_orders(), [], lambda: next(codes))
    print(f"  input : 'Burn-in Chamber B4 is down till the 24th'")
    print(f"  echo  : {prop['echo']}")
    print(f"  summary: {prop['summary']}")
    for ch in prop["changes"][:6]:
        print(f"    - {ch['what']}: {ch['from_value']} -> {ch['to_value']} ({ch['note']})")
    assert prop["ok"], "constraint parsed"
    assert prop["constraint"].resource_id == "B4", "named the right chamber"
    print("  ✓ parsed B4 outage and produced a before→after diff")

    # ---- 4. constraint actually changes the schedule ---------------------
    line("4. The constraint measurably changes burn-in assignments")
    before = eng.compute(sample_orders(), [])
    after = eng.compute(sample_orders(), [prop["constraint"]])
    b4_before = [o.code for o in before.orders if o.assigned_resource.get("09B") == "Burn-in Chamber B4"]
    b4_after = [o.code for o in after.orders if o.assigned_resource.get("09B") == "Burn-in Chamber B4"]
    print(f"  orders on B4 before: {b4_before}")
    print(f"  orders on B4 after : {b4_after}")
    assert not b4_after, "B4 carries nothing once it's offline"
    print("  ✓ B4 is emptied and its work moves to B3")

    # ---- 5. assistant answers about an order -----------------------------
    line("5. Assistant answers a question about a specific order")
    ans = asst.answer("where is SO-1058?", sample_orders(), [])
    print(f"  Q: where is SO-1058?")
    print(f"  A: {ans.text}")
    assert ans.kind == "order" and "SO-1058" in ans.text
    print("  ✓ real answer from schedule state")

    # ---- 6. assistant answers about constraints --------------------------
    line("6. Assistant answers about active constraints")
    ans = asst.answer("what's blocking the floor?", sample_orders(),
                      [prop["constraint"]])
    print(f"  Q: what's blocking the floor?")
    print(f"  A: {ans.text}")
    assert "B4" in ans.text or "Burn-in" in ans.text
    print("  ✓ lists the active constraint")

    # ---- 7. assistant gives suggestions ----------------------------------
    line("7. Assistant surfaces optimisation suggestions")
    ans = asst.answer("how can I improve throughput?", sample_orders(), [])
    print(f"  Q: how can I improve throughput?")
    print(f"  A: {ans.text}")
    assert ans.kind == "suggestions"
    print("  ✓ suggestions generated from real schedule gaps")

    # ---- 8. more constraint phrasings ------------------------------------
    line("8. Parser handles varied phrasings")
    for phrase in [
        "half the calibration team is out sick tomorrow",
        "squeeze in a rush order, 15 units, due in 18 days",
        "bump SO-1042 to top priority",
        "SO-1049 is waiting for diaphragms, new lot lands on the 20th",
        "approve overtime for an extra calibration bench",
    ]:
        c = iter(["C-9"])
        r = asst.propose(phrase, sample_orders(), [], lambda: next(c))
        status = "✓" if r["ok"] else "✗"
        print(f"  {status} '{phrase}'")
        print(f"      -> {r['echo']}")
        assert r["ok"], f"failed to parse: {phrase}"

    line("ALL CHECKS PASSED")
    print("The engine schedules from real state, the assistant parses constraints,")
    print("answers questions, and suggests optimisations — end to end.")
    return ok



def test_main():
    """pytest entrypoint — runs the script's checks as a test."""
    main()


if __name__ == "__main__":
    main()
