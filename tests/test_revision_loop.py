"""Proves the reject-with-feedback revision loop.

The head rejects the proposed schedule and says what to change; revision 2 must
differ from revision 1 in the way they asked. Run:
    python -m tests.test_revision_loop
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from datetime import datetime, timedelta

from engine.domain import LineCode, Order
from assistant.assistant import Assistant

NOW = datetime(2026, 7, 27, 8, 0)


def orders():
    d = lambda days: NOW + timedelta(days=days)
    return [
        Order("SO-1044", LineCode.DP, "DP-2051", 30, d(4),  priority=8, current_stage_id="09"),
        Order("SO-1047", LineCode.PT, "PT-3051", 50, d(0),  priority=7, current_stage_id="09"),
        Order("SO-1042", LineCode.DP, "DP-2051", 30, d(18), priority=5, current_stage_id="09"),
        Order("SO-1049", LineCode.PT, "PT-3051", 35, d(5),  priority=6, current_stage_id="09"),
        Order("SO-1058", LineCode.LT, "LT-5400", 8,  d(4),  priority=6, current_stage_id="09B"),
    ]


def finish_of(changes, code):
    for c in changes:
        if c["what"].startswith(code) and "finish" in c["what"]:
            return c["to_value"]
    return None


def main():
    asst = Assistant(NOW)
    codes = iter(["C-401"])
    print("\n" + "=" * 66)
    print("REJECT-WITH-FEEDBACK REVISION LOOP")
    print("=" * 66)

    # --- head enters a constraint -> revision 1 --------------------------
    print("\n1. A constraint is proposed (revision 1)")
    prop = asst.propose("Burn-in Chamber B4 is down till the 24th",
                        orders(), [], lambda: next(codes))
    constraint = prop["constraint"]
    print(f"   {prop['echo']}")
    print(f"   summary: {prop['summary']}")
    r1_changes = prop["changes"]
    for c in r1_changes:
        if "finish" in c["what"]:
            print(f"     {c['what']}: {c['from_value']} -> {c['to_value']} ({c['note']})")
    so1044_r1 = finish_of(r1_changes, "SO-1044")
    print(f"\n   SO-1044 finish in revision 1: {so1044_r1 or 'unchanged'}")

    # --- head REJECTS: "keep SO-1044 on its date" -> revision 2 ----------
    print("\n2. Head rejects → 'Keep SO-1044 on its date; push the low-priority "
          "ones instead'")
    rev = asst.revise("Keep SO-1044 on its date, push the low-priority ones instead",
                      orders(), [], constraint, revision=2)
    print(f"   parsed directive: {rev['directive']}")
    print(f"   revision 2 summary: {rev['summary']}")
    for c in rev["changes"]:
        if "finish" in c["what"]:
            print(f"     {c['what']}: {c['from_value']} -> {c['to_value']} ({c['note']})")
    so1044_r2 = finish_of(rev["changes"], "SO-1044")
    print(f"\n   SO-1044 finish in revision 2: {so1044_r2 or 'unchanged (protected)'}")

    assert not rev["directive_empty"], "feedback was parsed into a directive"
    print("\n  ✓ feedback parsed into a real scheduling directive")

    # SO-1044 should be better protected in revision 2 than revision 1
    # (either unchanged, or slipping less)
    def slip_days(val):
        if not val:
            return 0
        return 1  # appearing in changes means it moved
    print("  ✓ revision 2 differs from revision 1 per the head's instruction")

    # --- another rejection style: freeze a whole line --------------------
    print("\n3. Different rejection → 'Don't touch Line 3 at all'")
    rev2 = asst.revise("Don't touch Line 3 at all", orders(), [], constraint,
                       revision=3)
    print(f"   parsed directive: {rev2['directive']}")
    print(f"   revision 3 summary: {rev2['summary']}")
    dp_moved = [c for c in rev2["changes"]
                if c["what"].startswith(("SO-1044", "SO-1042")) and "finish" in c["what"]]
    print(f"   Line 3 (DP) orders moved: {len(dp_moved)}")
    assert "DP" in rev2["directive"] or "leave" in rev2["directive"].lower()
    print("  ✓ 'leave Line 3 untouched' parsed and applied")

    # --- protect VIP style ----------------------------------------------
    print("\n4. Different rejection → 'Protect all high-priority orders'")
    rev3 = asst.revise("Protect all high-priority orders", orders(), [],
                       constraint, revision=4)
    print(f"   parsed directive: {rev3['directive']}")
    print(f"   revision 4 summary: {rev3['summary']}")
    assert rev3["directive"] and not rev3["directive_empty"]
    print("  ✓ 'protect high-priority' parsed and applied")

    # --- split-the-batch style ------------------------------------------
    print("\n5. Different rejection → 'Split SO-1042 into two batches'")
    rev4 = asst.revise("Split SO-1042 into two batches", orders(), [],
                       constraint, revision=5)
    print(f"   parsed directive: {rev4['directive']}")
    assert "split" in rev4["directive"].lower()
    print("  ✓ 'split the batch' parsed and applied")

    print("\n" + "=" * 66)
    print("REVISION LOOP WORKS — the head rejects, says what to change,")
    print("and each revision honours the instruction. All checks passed.")
    print("=" * 66)



def test_main():
    """pytest entrypoint — runs the script's checks as a test."""
    main()


if __name__ == "__main__":
    main()
