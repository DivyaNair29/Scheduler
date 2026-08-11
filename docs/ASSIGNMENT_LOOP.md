# Assignment Loop — Wiring Guide

The head→employee→head task loop. Files in this folder:

- `assignments.py` — the `Assignment` model source + helper functions
- `route_patches.py` — exact route code to add/replace in `app.py`
- `templates/employee_dashboard.html` — assigned-vs-self-select + checklist
- `templates/partials/_completed_tasks.html` — head's sign-off panel + assign form
- `tests/test_assignment_loop.py` — proves the whole cycle

## What the loop does

1. **Head assigns** `{order, stage}` to a specific employee → shows in that
   employee's account only.
2. **If unassigned**, the employee self-selects order + stage → checklist appears.
3. Employee ticks items → assignment goes `assigned` → `in_progress` → `complete`.
4. On **complete**, it surfaces in the head's "awaiting sign-off" panel.
5. Head **accepts** → the order advances to the next stage → the schedule
   recomputes → the head sees the updated plan.

## Steps

### 1. Add the model
Copy the `Assignment` class from `assignments.py` (the `MODEL_SOURCE` string)
into `models.py`, below `Confirmation`. Run a migration, or for the demo:
`flask --app app reset-db`.

### 2. Add the imports to app.py
```python
from models import Assignment
from assignments import next_stage, refresh_status, assignment_progress
```

### 3. Apply the route changes
From `route_patches.py`, copy each block into `app.py`:
- `HEAD_ASSIGN` → new `assign_task()` route
- `EMPLOYEE_DASHBOARD` → replace `employee_dashboard()`
- `SELF_SELECT` → replace `set_assignment()`
- `TICK` → replace `tick_checklist()`
- `HEAD_ACCEPT` → new `accept_assignment()` route
- `MANPOWER_CONTEXT` → add `completed` + `active_assignments` to `manpower()`'s
  `render_template` call

### 4. Drop in the templates
- Replace `templates/employee_dashboard.html`
- Add `templates/partials/_completed_tasks.html`
- Include the partial in `manpower.html` (or the head dashboard):
  `{% include "partials/_completed_tasks.html" %}`

### 5. Verify
```bash
python -m tests.test_assignment_loop
```
Drives the full cycle against a real Flask+SQLite app and checks each transition.

## Design notes

- **One active assignment per employee.** Assigning a new task supersedes any
  open one, so an employee always has a single clear task.
- **Self-select is blocked when the head has assigned.** The employee can't
  wander off their assigned task; the check is in `set_assignment()`.
- **Completion is derived, not manual.** An assignment becomes `complete` only
  when every checklist item for its stage is confirmed — the employee can't mark
  it done early.
- **Accepting advances the order** through `STAGE_SEQUENCE` and recomputes via
  the engine. If the engine packages aren't present, the stage advance still
  works; the recompute is best-effort.
- **The audit log captures every transition** — assignment, completion,
  acceptance, and the stage advance — through the existing `log_event`.
