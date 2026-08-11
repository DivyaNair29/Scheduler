# Meridian Instruments — AI Production Scheduler (front end)

Static multipage front end. Plain HTML + CSS + JS, no build step, no framework.
Open `index.html` over HTTP (VS Code Live Server, or `python -m http.server`).

## Folders

```
/css      base.css (tokens, layout, shared components) + one per view + print.css
/js       data.js (mock data) · util.js · store.js · components.js
          main.js (shared chrome) · page-*.js (one per view)
/img      logo.svg and any icons
/assets   fonts/fonts.css (swap the @import for @font-face to self-host)
*.html    index (Dashboard) · board (Schedule Board) · orders · intake · quality
```

## Pages

| File | Page | Notes |
| --- | --- | --- |
| `index.html` | Dashboard | phase kanban + approvals (head) / job picker + constraints (employee) |
| `board.html` | Schedule Board | 24h gantt: 4 lines, downstream stages, work-centres. Dept Head / Admin only |
| `orders.html` | Orders | searchable register, phase filters |
| `intake.html` | Order Intake | new-order form + pre-release queue |
| `quality.html` | Quality & Dispatch | approvals + dispatch queue (head) / checklist (employee) |

## How the markup is wired for a backend

Nothing is hardcoded in the HTML. Three attribute conventions:

- `data-slot="name"` — a container a list gets rendered into.
- `data-field="name"` — a leaf node that receives one value.
- `data-region="name"` — a block that gets shown or hidden.

Repeated markup lives in `<template>` elements at the bottom of each page.
`js/util.js` clones a template and fills its `data-field` nodes:

```js
const row = MU.fromTemplate("tpl-order-row");
MU.fill(row, { code: "SO-1044", qty: "25", status: "RUNNING" });
```

The shared chrome (sidebar, topbar, chat panel) uses identical markup and class
names on all five pages — lift it into `templates/base.html` as a Jinja block
and the pages become Flask templates with no CSS or JS changes.

## Swapping mock data for Flask

`js/data.js` is the only file holding sample data, and `js/store.js` is the only
file that reads it. Two ways to connect a backend:

**Server-rendered** — populate the same `data-field` nodes in Jinja and delete
`data.js`; the page scripts skip anything already filled.

**JSON API** — keep the page scripts as they are and change one method:

```js
// js/store.js
init: async function () {
  this.data = await fetch("/api/bootstrap").then(r => r.json());
  ...
}
```

Suggested endpoints, matching what the store reads today:

```
GET  /api/bootstrap                     users, nav, phases, lines, stages
GET  /api/orders                        ?phase= ?q=
GET  /api/intake
POST /api/intake                        {customer, product, qty, due, notes}
GET  /api/constraints
POST /api/constraints                   {order, stage, type, note}
POST /api/constraints/<code>/approve
POST /api/constraints/<code>/reject
POST /api/constraints/<code>/schedule/approve
POST /api/constraints/<code>/schedule/reject   {feedback}
GET  /api/confirmations
POST /api/confirmations                 {order, stage, item}
GET  /api/chat
POST /api/chat                          {text}
```

## The approval rule

The floor plan never changes until the production head signs off, in two steps:

1. Operator raises a constraint → `pending`.
2. Head approves the constraint → `approved`, and a schedule revision is
   generated (`store.scheduleChanges`).
3. Head approves the revision → `applied`; it appears on the Schedule Board.
4. Or the head rejects it **with a note** → the revision number increments, a
   new proposal is generated honouring the note, and it returns for approval.

Enforce this server-side too — the client check is convenience, not security.

## Roles

Role chips in the sidebar footer switch Employee / Dept Head / Admin.
`WRITE_ONLY` in `js/main.js` lists the pages Employees cannot open
(currently the Schedule Board); Dashboard and Quality swap to operator views.

State lives in `localStorage` under `meridian.site.state.v1` — "Reset demo data"
in the topbar clears it. Drop that whole mechanism once the server owns state.
