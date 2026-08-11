# Frontend Integration

The multipage static site (`site/`) is now served by Flask and fed live data.

## How it works

- **Flask serves the site** at `http://127.0.0.1:5000/app/` — all pages, CSS,
  JS, images. See `app/frontend_api.py`.
- **One live data endpoint**, `/api/bootstrap`, returns the entire
  `MERIDIAN_DATA` object assembled from the database and the scheduling engine —
  the exact shape `site/js/data.js` defines.
- **`store.js` fetches it** at boot via `loadAll()`, then every page renders from
  it unchanged. If the fetch fails (no server), it falls back to the bundled
  `data.js`, so the site still opens as a standalone static page.

Nothing in the page markup or page scripts changed — only `store.js` (added
`loadAll`) and `main.js` (awaits it). The `data-slot` / `data-region` hooks the
designer built are populated the same way, now from live data.

## Run it

```bash
python run.py --seed      # seed the db
python run.py             # start
```

Open **http://127.0.0.1:5000/app/**

## What's live vs still mock

**Live (from the database + engine):** users, orders, constraints,
confirmations, work-centres (named benches/chambers from the engine),
board suggestions (from the suggestion engine).

**Still placeholder** (shaped correctly, not yet wired to real sources):
customers, dispatch times, chat history. These read sensible defaults from the
bootstrap until their real sources exist.

## Wired actions

The assistant chat and the propose→approve→revise loop are **wired and working**
end to end from the site:

| Site action | Endpoint | Status |
|---|---|---|
| Ask the assistant (order status, constraints, suggestions) | `POST /api/scheduler/ask` | ✅ wired |
| Type a disruption → propose a constraint | `POST /api/scheduler/propose` | ✅ wired |
| Approve → apply to live floor | `POST /api/scheduler/apply` | ✅ wired |
| Reject with feedback → revised schedule | `POST /api/scheduler/revise` | ✅ wired |
| Create a new order | `POST /orders/new` | ⏳ next |
| Confirm a checklist item | `POST /my-confirmations/tick` | ⏳ next |

**How the chat decides:** if the message contains disruption words (down, offline,
delay, rush, bump, hold, overtime, split…) and the user can write, it *proposes a
constraint* and shows the before→after with Approve / Reject buttons. Otherwise it
*asks* and shows the answer. Approving applies to the floor and re-fetches
`/api/bootstrap` so every page reflects the change; rejecting prompts for a change
and produces a revision.

## Remaining actions (same pattern)

Create-order and confirm-checklist follow the identical shape: POST to the
built endpoint, then `store.refresh()` + re-render. The chat wiring in
`site/js/main.js` and the store methods in `site/js/store.js` are the template
to copy.

## Note on security

Role gating in the site JS is cosmetic — real enforcement is server-side in the
Flask routes (the existing `require_write()` and role checks). Never rely on the
client-side gating for access control; it only hides UI.
