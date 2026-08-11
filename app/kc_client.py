"""Knowledge Center (KC) client — the scheduler's seam to the AND KC platform.

The KC is a FastAPI + Postgres/pgvector RAG service. For ORDER INTAKE we use two
of its endpoints:

    POST /login                       -> a JWT carrying department/role clearance
    GET  /documents?department=...    -> list docs the caller is cleared to see
    GET  /documents/{doc_id}/text     -> a document's plain text

Auto-detection is done by POLLING /documents for the `scheduler` department and
diffing the returned doc_ids against orders we've already imported (see
kc_intake.detect_new_orders). The KC has no "changed since" endpoint and DocOut
carries no timestamp, so detection is NEW-document detection, not edit tracking.

Everything is behind is_online(): if the KC isn't reachable, the scheduler keeps
working (manual/paste intake still available) and KC features simply report
offline — mirroring the KC frontend's own api.js graceful-fallback pattern.

Config via env:
    KC_BASE_URL   default http://127.0.0.1:8000
    KC_USERNAME   default emp        (a 'production'-cleared demo account)
    KC_PASSWORD   default emp123
    KC_DEPARTMENT default scheduler
    KC_TIMEOUT    default 4 (seconds)
"""
from __future__ import annotations

import os
import json
import time
import urllib.request
import urllib.error


class KCClient:
    def __init__(self, base_url=None, username=None, password=None, timeout=None):
        self.base = (base_url or os.environ.get("KC_BASE_URL", "http://127.0.0.1:8000")).rstrip("/")
        self.username = username or os.environ.get("KC_USERNAME", "emp")
        self.password = password or os.environ.get("KC_PASSWORD", "emp123")
        self.department = os.environ.get("KC_DEPARTMENT", "scheduler")
        self.timeout = float(timeout or os.environ.get("KC_TIMEOUT", "4"))
        self._token = None
        self._token_at = 0.0

    # ---- low-level HTTP ---------------------------------------------------
    def _req(self, method, path, body=None, auth=True):
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if auth and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else None

    # ---- health / auth ----------------------------------------------------
    def is_online(self):
        """True if the KC responds to /health within the timeout."""
        try:
            self._req("GET", "/health", auth=False)
            return True
        except Exception:
            return False

    def login(self):
        """Obtain (and cache) a JWT. Tries /login first, falls back to /dev/token
        with a production clearance. Returns the token or raises."""
        # cached and fresh enough?
        if self._token and (time.time() - self._token_at) < 1800:
            return self._token
        # try the real login (demo accounts admin/head/emp)
        try:
            out = self._req("POST", "/login",
                            {"username": self.username, "password": self.password},
                            auth=False)
            tok = (out or {}).get("access_token") or (out or {}).get("token")
            if tok:
                self._token, self._token_at = tok, time.time()
                return tok
        except Exception:
            pass
        # fall back to the dev token minting (production clearance so it can see
        # the scheduler department's order docs)
        out = self._req("POST", "/dev/token", {
            "user_id": self.username, "tenant_id": "meridian",
            "department": self.department, "role": "production",
            "extra_roles": ["production"],
        }, auth=False)
        tok = (out or {}).get("access_token") or (out or {}).get("token")
        if not tok:
            raise RuntimeError("KC did not return a token")
        self._token, self._token_at = tok, time.time()
        return tok

    # ---- documents --------------------------------------------------------
    def list_documents(self, department=None):
        """List documents the caller is cleared to see (RBAC-filtered by the KC).
        Returns a list of {doc_id, title, department, format, restricted}."""
        self.login()
        dept = department or self.department
        path = f"/documents?department={urllib.parse.quote(dept)}" if dept else "/documents"
        return self._req("GET", path) or []

    def document_text(self, doc_id):
        """Fetch a document's plain text: {doc_id, title, department, text, ...}."""
        self.login()
        return self._req("GET", f"/documents/{urllib.parse.quote(doc_id)}/text")

    def query(self, question):
        """RAG query (used for reference lookups, not intake): {kind, text, citations}."""
        self.login()
        return self._req("POST", "/query", {"question": question})


# a module-level default client the scheduler can share
import urllib.parse  # noqa: E402  (used above)

_default = None


def client():
    global _default
    if _default is None:
        _default = KCClient()
    return _default
