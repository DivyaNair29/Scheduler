# Meridian Scheduler Agent

AI-driven production scheduler for transmitter manufacturing. A Flask app with a
real finite-capacity scheduling engine, a natural-language assistant, and the
head↔employee approval loops.

## Folder structure

```
Scheduler_Agent/
├── run.py                  # start the app:  python run.py  (--seed to reset data)
├── conftest.py             # lets `pytest` find the app package
├── requirements.txt
├── .env.example            # copy to .env and fill in secrets
│
├── app/                    # all application code
│   ├── app.py              # Flask app factory, routes, CLI
│   ├── config.py           # configuration (DB points at ../data/)
│   ├── models.py           # SQLAlchemy models
│   ├── services.py         # app-level helpers (checklists, reports, insights)
│   ├── seed.py             # demo-floor seed data
│   │
│   ├── engine/             # the scheduling engine (framework-free)
│   │   ├── domain.py       #   dataclasses: Order, Stage, Resource, Constraint
│   │   ├── plant.py        #   Meridian routing, resources, shifts (reference data)
│   │   ├── scheduler.py    #   finite-capacity forward-pass + schedule diff
│   │   └── adapter.py      #   maps ORM rows <-> engine objects
│   │
│   ├── assistant/          # the AI assistant
│   │   ├── parser.py       #   natural language -> structured constraint
│   │   ├── directives.py   #   rejection feedback -> scheduling directives
│   │   ├── suggestions.py  #   schedule gaps -> optimisation suggestions
│   │   └── assistant.py    #   Q&A routing, propose(), revise()
│   │
│   ├── api/                # JSON API blueprint (mirrors page actions)
│   ├── scheduler_api.py    # engine/assistant endpoints (/api/scheduler/*)
│   ├── order_intake.py     # create_order() — one path for every new order
│   ├── assignments.py      # head->employee->head task loop model + helpers
│   │
│   ├── templates/          # Jinja templates (+ partials/)
│   └── static/             # css, js
│
├── tests/                  # runnable proofs (python tests/<name>.py or pytest)
│   ├── test_end_to_end.py      # engine + assistant
│   ├── test_assignment_loop.py # head->employee->head
│   ├── test_revision_loop.py   # reject-with-feedback revision
│   └── test_order_intake.py    # new orders enter the list
│
├── data/                   # runtime database lives here (git-ignored)
│
├── docs/                   # documentation & integration guides
│   ├── README.md               # detailed backend/engine documentation
│   ├── ASSIGNMENT_LOOP.md       # wiring guide for the task loop
│   ├── route_patches.py         # exact route code to add to app.py
│   └── AND_Scheduling_Assistant_PRD_v1.0.docx
│
└── reference/              # process knowledge & datasets (not code)
    ├── Meridian_Instruments_Scheduler_Dataset.xlsx
    ├── Meridian_Instruments_Scheduler_Reference.docx
    ├── Workflow Process and Failure possibilities.docx
    ├── transmitter_production_flowchart.html
    └── AND_Scheduling_Assistant_Visual_Options.pdf
```

## Why this layout

- **`app/` holds only code.** Nothing a user edits or reviews (docs, datasets,
  the database) lives among the modules.
- **`engine/` and `assistant/` are packages**, importable and testable on their
  own — the engine has no Flask dependency.
- **`data/` isolates runtime state.** The SQLite database is created here, not
  next to the code, so it never gets committed or confused with source.
- **`docs/` vs `reference/`** — `docs/` is about *this system* (how it's built,
  how to wire it); `reference/` is the *domain* input (process, dataset, spec).
- **`tests/` at the root** run both as scripts and under `pytest`.

## Setup

```bash
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                # then edit
```

## Run

```bash
python run.py --seed     # create + seed data/meridian.db, then start
python run.py            # start using the existing database
```

Open http://127.0.0.1:5000. Switch roles (Admin / Department Head / Employee)
from the sidebar.

Alternatively, with Flask's CLI from inside `app/`:

```bash
cd app
flask --app app init-db
flask --app app run --debug
```

## Test

```bash
python tests/test_end_to_end.py       # or any of the four
pytest                                # runs all four
```

All four suites prove real behaviour: the engine schedules from live state, the
assistant parses constraints and answers questions, the head↔employee loop
completes, rejection-with-feedback reshapes the schedule, and new orders enter
the list.

## Integration status

The engine, assistant, order intake and assignment loop are built and tested.
Wiring them into `app.py`'s routes is documented in `docs/route_patches.py` and
`docs/ASSIGNMENT_LOOP.md` — copy the named blocks into `app.py` to activate the
routes. The engine packages are already in place under `app/`, so the imports
resolve as soon as the routes are added.
