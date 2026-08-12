# Deploying the AND Scheduling Assistant on Render

The build now includes everything Render needs: `gunicorn` in requirements, a
`render.yaml` blueprint, and `render_start.py` (seeds the DB once, never wipes it
on restart). Two ways to deploy — the blueprint is easiest.

---

## Path A — One-click via the blueprint (recommended)

1. **Put the code on GitHub.** Create a repo and push the project (the folder
   that contains `render.yaml`, `requirements.txt`, `run.py`, and `app/`).
2. In Render: **New + → Blueprint → connect your repo.** Render reads
   `render.yaml` and sets up the web service automatically.
3. When prompted, fill the values it can't generate:
   - **DEMO_PASSWORD** — the shared password your team logs in with.
   - (SECRET_KEY is auto-generated; DATABASE_URL, DEMO_MODE, COOKIE_SECURE are
     already set in the blueprint.)
4. Click **Apply / Create**. Render builds, seeds on first boot, and starts.
5. Open the service URL → you get the **login page**. Sign in with:
   - `manish` / *(your DEMO_PASSWORD)* — Department Head
   - `priya` / *(same)* — operator
   - `sanjay` / *(same)* — admin

That's it. The blueprint uses a **1 GB persistent disk** for the SQLite file, so
data survives restarts and redeploys.

---

## Path B — Manual (no blueprint)

If you'd rather click through the dashboard:

1. **New + → Web Service → connect the repo.**
2. Settings:
   - **Runtime:** Python 3
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:**
     `python render_start.py && cd app && gunicorn --workers 1 --bind 0.0.0.0:$PORT app:app`
   - **Health check path:** `/login`
3. **Add a disk** (Settings → Disks): mount path `/var/data`, size 1 GB.
4. **Environment variables:**
   | Key | Value |
   |---|---|
   | `PYTHON_VERSION` | `3.12.3` |
   | `DATABASE_URL` | `sqlite:////var/data/meridian.db` (four slashes) |
   | `DEMO_MODE` | `0` (real login) or `1` (no-login role switch) |
   | `COOKIE_SECURE` | `1` (Render is HTTPS) |
   | `SECRET_KEY` | click **Generate** |
   | `DEMO_PASSWORD` | your shared trial password |
5. **Create Web Service.** First deploy seeds the DB; opens at the login page.

---

## Free vs paid

- **`plan: free`** in the blueprint (or pick Free in the dashboard): **$0**, but
  the service **sleeps after ~15 min idle** and takes ~30–60s to wake. Fine to
  let people poke at it; mildly annoying for a live demo.
  - Also note: Render's **free tier has no persistent disk**, so on the free plan
    the SQLite DB resets on redeploy. For a throwaway trial that's OK. To keep
    data on free, add a free Postgres (see below).
- **`plan: starter`**: **$7/mo**, always on, supports the persistent disk. Best
  for an ongoing team trial.

---

## If you want data to persist on the free plan → free Postgres

1. In `render.yaml`, remove the `disk:` block and the `DATABASE_URL` sqlite line,
   then uncomment the `databases:` block and the `fromDatabase` env var (both are
   in the file, ready to go).
2. Render provisions a managed Postgres and injects its connection string as
   `DATABASE_URL`. The app already reads that — no code change.
3. `render_start.py` seeds it once on first boot, same as SQLite.

Postgres also lets you raise workers later (`--workers 3`) for more users.

---

## Common gotchas (all already handled in this build)

- **"gunicorn: command not found"** → it's in `requirements.txt` now. ✓
- **App can't be reached / wrong port** → the start command binds
  `0.0.0.0:$PORT`, which is what Render requires. ✓
- **Data wiped on every deploy** → `render_start.py` only seeds when the DB is
  empty, and the disk (or Postgres) persists it. ✓
- **WSGI import error** → the target is `app:app` run from inside the `app/`
  folder (the start command `cd app` first). ✓
- **Insecure cookies over HTTPS** → `COOKIE_SECURE=1` is set; Render terminates
  TLS for you. ✓

---

## After the trial, before going wider

Still recommended before a customer/public deployment (not needed for a trusted
team trial): add **CSRF tokens**, move to **Postgres** if still on SQLite, and
give each user their **own account/password** instead of the shared demo one.
