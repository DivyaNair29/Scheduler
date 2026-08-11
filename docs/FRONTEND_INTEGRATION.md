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

## Wiring the action endpoints (next)

Reads are done. To make the site's *actions* hit the backend — raising a
constraint, approving, confirming a checklist item, creating an order, asking
the assistant — point each form/button at the matching endpoint:

| Site action | Endpoint (already built) |
|---|---|
| Ask the assistant / propose a constraint | `POST /api/scheduler/propose`, `/ask` |
| Approve → reflect on board | `POST /api/scheduler/apply` |
| Reject with feedback | `POST /api/scheduler/revise` |
| Create a new order | `POST /orders/new` (route_patches block 8) |
| Confirm a checklist item | `POST /my-confirmations/tick` |

These are POSTs from the page JS; the read path (`/api/bootstrap`) already
proves the wiring pattern. Do them one at a time, re-fetching bootstrap after
each so the UI reflects the change.

## Note on security

Role gating in the site JS is cosmetic — real enforcement is server-side in the
Flask routes (the existing `require_write()` and role checks). Never rely on the
client-side gating for access control; it only hides UI.
