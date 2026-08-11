# Changed files — fix wrong "Day of cycle" (0) and impossible variance (-358 d)

Two related bugs in the order detail PLANNED vs ACTUAL numbers.

## Bug 1 — variance showed -358 days
Cause: the due-date parser (_parse_due) rolled ANY earlier-month due date to
NEXT year. With now = Aug 2026 and due "31 Jul", it parsed due as 31 Jul 2027,
so variance = projected(Aug 2026) - due(Jul 2027) ~= -358 days.
Fix: an order due a little while ago is OVERDUE, not next-year. Only roll to next
year if the date is > ~6 months in the past (the real wrap-around case). Now a
late order shows a correct small POSITIVE variance (e.g. +6.7 d late).

## Bug 2 — "Day of cycle" showed 0 despite being mid-process
Cause: day-of was wall-clock elapsed since the schedule's first-stage start. If
the engine start was in the future (or projected_finish was None), elapsed went
negative and clamped to 0 — so an order at stage 6 still read day 0.
Fix: day-of and total-days are now computed from real STAGE DURATIONS (the same
honest method as the lead-days fix): day-of = summed duration of COMPLETED stages,
total = summed duration of all stages. Projected finish falls back to now +
remaining work when the engine has no projection, so it's never a wild date.

## Result (SO-1058, from the screenshot)
Before: day 0 / 4.9 d, variance -358.3 d.
After:  day 3.3 / 7.3 d (5 of 11 stages done), due 31 Jul, projected 06 Aug,
        variance +6.7 d late. Realistic and consistent.

## Files
- app/engine/adapter.py   (_parse_due: overdue vs next-year)
- app/frontend_api.py       (analysis: duration-based day-of; safe projected finish)

## After dropping in
Re-seed not required. Hard-refresh, open an order's detail — Day of cycle and
Variance now read correctly.

## Note
Still engineering estimates. The point of this fix is correctness/consistency of
the math, not measured accuracy — that still needs the time-and-motion pass.
