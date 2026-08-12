# Cheapest Way to Let Your Team Try the Scheduler

The app now has **real login** (username + password), plus a **DEMO_MODE** flag so
you can pick how locked-down the try-out is. Below are the cheapest ways to get
your team using it, from free to a few dollars a month.

## What changed (auth)

- **Real accounts**: `priya` (operator), `manish` (dept head), `sanjay` (admin),
  seeded with a shared password (default `meridian123`, override with the
  `DEMO_PASSWORD` env var).
- **Two modes:**
  - `DEMO_MODE=1` (default) — quick role-switcher stays on, no login wall. Good
    for a fast internal look where you trust everyone on the network.
  - `DEMO_MODE=0` — real login required; every page/API needs a signed-in
    session; the no-password role switch is disabled. Use this the moment the
    link is reachable by anyone you don't fully trust.
- Passwords are hashed (Werkzeug), cookies are HttpOnly + SameSite=Lax, and
  `SECRET_KEY` / `DEMO_PASSWORD` / `COOKIE_SECURE` come from the environment.

Set a real secret and turn auth on for a shared trial:
```bash
export SECRET_KEY="$(python -c 'import secrets;print(secrets.token_hex(32))')"
export DEMO_MODE=0
export DEMO_PASSWORD="pick-something"
export COOKIE_SECURE=1        # only if you're serving HTTPS
```

---

## Option 1 — Free / near-free cloud (fastest for a remote team)

Best when your team is not on one network and you just want to send a link.

### Render (free web service)
- Push the repo to GitHub, create a **Web Service** on Render, free tier.
- Build: `pip install -r requirements.txt`
- Start (from the `app` folder): `gunicorn --workers 1 --bind 0.0.0.0:$PORT app:app`
  (add `gunicorn` to requirements or the start command).
- Set env vars: `SECRET_KEY`, `DEMO_MODE=0`, `DEMO_PASSWORD`, `COOKIE_SECURE=1`.
- First deploy: run `python run.py --seed` once (Render shell) to create the DB.
- **Cost: $0.** Caveat: the free service **sleeps after ~15 min idle** and takes
  ~30–60s to wake — fine for an occasional try-out, annoying for daily use.
  Upgrade to the $7/mo always-on tier if the sleeping bothers the team.
- SQLite note: Render's free disk is ephemeral, so the demo DB resets on redeploy.
  For a throwaway trial that's fine; for a persistent trial add their free/cheap
  Postgres and set `DATABASE_URL`.

### Railway / Fly.io
Same shape, both have small free/trial credit. Fly can keep a tiny always-on VM
cheaply and gives a persistent volume for the SQLite file.

---

## Option 2 — Cheapest always-on VPS (best value for a real trial)

Best when you want it always up, persistent data, and maybe LAN-only.

- **Hostinger KVM 1** or **Hetzner CX22**: ~**$5–7/mo**, 1–2 vCPU, 4 GB RAM —
  plenty for a team trial.
- One box runs the app (SQLite is fine for a trial; no Postgres needed yet).
- Steps:
  ```bash
  unzip Scheduler_Agent_FULL.zip && cd Scheduler_Agent
  python3 -m venv venv && source venv/bin/activate
  pip install -r requirements.txt gunicorn
  python run.py --seed
  export SECRET_KEY="$(python -c 'import secrets;print(secrets.token_hex(32))')"
  export DEMO_MODE=0 DEMO_PASSWORD="pick-something"
  cd app
  gunicorn --workers 1 --bind 0.0.0.0:8000 app:app
  ```
- Reach it at `http://SERVER_IP:8000`. Add Nginx + a free Let's Encrypt cert when
  you want HTTPS (then set `COOKIE_SECURE=1`).
- **Cost: ~$5–7/mo, always on, data persists.**

---

## Option 3 — Zero-cost, on your own machine + a tunnel (quickest trial ever)

Best for a same-day trial with zero hosting spend.

- Run it locally:
  ```bash
  cd Scheduler_Agent && python run.py --seed && python run.py
  ```
- Expose it to teammates with a free tunnel:
  - **Cloudflare Tunnel** (`cloudflared tunnel --url http://localhost:5000`) or
  - **ngrok** free tier.
- They get a public HTTPS URL that points at your machine. Turn on `DEMO_MODE=0`
  so the link needs a login.
- **Cost: $0.** Caveat: only up while your machine + tunnel are running; not for
  ongoing use, but perfect for a live walkthrough or a short trial.

---

## Which to pick

| You want… | Use | Cost |
|---|---|---|
| Send a link to a remote team, occasional use | Render free | $0 (sleeps) |
| Same-day live trial, no hosting | Local + Cloudflare Tunnel/ngrok | $0 |
| Always-on trial, data persists, cheap | Hostinger KVM 1 / Hetzner CX22 | ~$5–7/mo |
| Always-on, no sleep, managed | Render Starter | $7/mo |

**My suggestion for a team try-out:** if they're remote and you want it always
reachable, a **$5–7 VPS** is the sweet spot — cheap, always on, data sticks, and
you're not fighting cold-starts. If you just want them to click around this week
with zero spend, **local + a Cloudflare Tunnel** with `DEMO_MODE=0` gets you there
in minutes.

---

## Before you widen the trial beyond your team

This build is now safe for a trusted-team trial. Before it's reachable by the
public / a customer, still do:
- **Move SQLite → Postgres** (set `DATABASE_URL`) so data is durable + multi-worker.
- **Add CSRF tokens** on state-changing requests. (Current protection: SameSite=Lax
  cookies + session auth — reasonable for an internal trial, not sufficient as the
  only defence for a public deployment.)
- **HTTPS everywhere** + `COOKIE_SECURE=1`.
- Give each person their **own account + password** instead of the shared demo one.

I can do the Postgres switch and CSRF layer whenever you're ready to go wider.
