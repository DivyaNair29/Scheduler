# Meridian Scheduler — Working Backend

A real scheduling engine and AI assistant that drop into the existing Flask app.
This replaces the stubbed `build_schedule_changes()` with an engine that computes
schedules from actual order state.

## What this does

- **Scheduling engine** (`engine/`) — takes a list of orders + active constraints
  and computes when each runs each stage, on which named resource, and when it
  finishes. Respects resource capacity (3 cal benches, 2 burn-in chambers),
  shifts, wall-clock burn-in, and priority (rush/VIP first, low-priority slips).
- **AI assistant** (`assistant/`) — parses natural-language constraints into
  structured changes, answers questions about orders/constraints/suggestions,
  and generates optimisation suggestions from real schedule gaps. No API key
  required; deterministic and offline by default.
- **Flask integration** (`scheduler_api.py`) — endpoints that wire both into the
  app, including the apply step that reflects an approved change in Live Floor.

## Proof it works

```bash
cd backend
python -m tests.test_end_to_end
```

Shows the engine scheduling 7 orders, priority being respected, a parsed "Chamber
B4 is down" constraint emptying B4 and slipping dependent orders with real dates,
and the assistant answering about orders, constraints and suggestions.

## Wiring into meridian_app

1. Copy `engine/`, `assistant/`, and `scheduler_api.py` into `meridian_app/`.

2. Register the blueprint in `app.py` inside `create_app`:

   ```python
   from scheduler_api import scheduler_bp
   app.register_blueprint(scheduler_bp, url_prefix="/api/scheduler")
   ```

3. To make the existing approval chain drive the engine, call the helper from
   the app's `decide_schedule()` route when a schedule is approved:

   ```python
   from scheduler_api import apply_constraint_to_floor
   # inside decide_schedule, on the "approve" branch:
   apply_constraint_to_floor(code)   # recomputes + writes to the floor
   ```

4. No new dependencies for the base engine. (Optional CP-SAT refinement later
   needs `ortools`; optional LLM polish needs `anthropic` + `ANTHROPIC_API_KEY`.)

## How a new order enters the list

Every order enters through one function — `order_intake.create_order()` — no
matter the source, so the list, board and queues all pick it up automatically.

- **Manual entry** — the New Order form on the Orders page (Admin/Head only)
  POSTs to `/orders/new`. Pick line, quantity, due date (and optionally a model
  code); the code is auto-generated, line/product derived, and the order enters
  at `intake / Order Entry`.
- **Rush order** — when a rush-order constraint is *approved*, the approve branch
  commits it as a real Order via the same `create_order()` with `rush=True`. It
  becomes a real row on approval, not before — matching how every constraint
  works.
- **ERP sync** (later) — the adapter calls the same `create_order()`.

See `route_patches.py` blocks 8 (NEW_ORDER + form) and 9 (RUSH_ON_APPROVE), and
`tests/test_order_intake.py` which proves all paths write one consistent row.

## The flow you asked for

1. **Inputs** — orders come from the `Order` table via the adapter; the engine
   reads them as-is.
2. **Enter a constraint through the assistant** — `POST /api/scheduler/propose`
   with `{"text": "Burn-in Chamber B4 is down till the 24th"}` returns the echo,
   the summary, and the before→after changes.
3. **Updated schedule** — the response's `changes` are the proposal shown for
   approval.
4. **After approval → Live Floor** — `POST /api/scheduler/apply` with `{"code":
   "C-203"}` recomputes and writes each order's new status/finish/bench to the
   database, so the Live Floor tab reflects it on next load.
5. **Assistant Q&A** — `POST /api/scheduler/ask` with `{"question": "where is
   SO-1058?"}` / `"what's blocking the floor?"` / `"how can I improve
   throughput?"`.
6. **Suggestions** — `GET /api/scheduler/suggestions` returns gap-based
   optimisation cards for the suggestion columns.
7. **Reject & revise** — if the head rejects the proposal, `POST
   /api/scheduler/revise` with `{"code": "C-203", "feedback": "keep SO-1044 on
   its date, push the low-priority ones instead", "revision": 2}` parses the
   feedback into scheduling directives and returns a new before→after that
   honours it. The head can reject and revise repeatedly until they approve.

## The revision loop

The head rejects a proposed schedule and says *what* to change. The feedback is
parsed into directives the engine honours on the next pass:

| Feedback | Directive | Effect |
|---|---|---|
| "keep SO-1044 on its date" | hold date for SO-1044 | that order won't slip |
| "don't touch Line 3" | freeze DP line | Line 3 orders stay put |
| "protect the VIP orders" | protect priority ≥ 8 | high-priority held |
| "push the low-priority ones instead" | slip low-priority | slip absorbed elsewhere |
| "split SO-1042 into two batches" | split SO-1042 | smaller batch clears sooner |

Proven in `tests/test_revision_loop.py` — revision 2 measurably differs from
revision 1 in the way the head asked (SO-1044 held while low-priority orders
absorb the slip).

## Architecture notes

- `engine/domain.py` — framework-free dataclasses (the engine's world view).
- `engine/plant.py` — Meridian's routing, resources, shifts (the reference data;
  later served by the Knowledge Centre through the provider interface).
- `engine/scheduler.py` — the forward-pass finite-capacity engine + schedule diff.
- `engine/adapter.py` — the only file that bridges ORM rows and engine objects.
- `assistant/parser.py` — NL → structured constraint (7 disruption types).
- `assistant/suggestions.py` — schedule gaps → optimisation suggestions.
- `assistant/assistant.py` — Q&A routing + propose().

Reference times are engineering estimates, not floor-measured — the caveat that
holds throughout. Swap `plant.py` for the Knowledge Centre provider when ready;
nothing else changes.
