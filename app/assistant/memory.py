"""Assistant memory — a vector store of past Q&A, decisions, and constraints so
the assistant can recall relevant history ("last time B2 went down we…").

Backed by Qdrant (runs in Docker alongside Postgres). Designed to DEGRADE
GRACEFULLY: if Qdrant isn't reachable or the embedder isn't configured, memory
simply returns nothing and the assistant works exactly as before — memory is
additive, never a hard dependency.

Honest notes:
  * Embeddings: uses OpenAI embeddings if AND_EMBED_PROVIDER=openai and a key is
    set (same provider the KC uses); otherwise a deterministic local hashing
    embedder so the pipeline works offline for demos. The local embedder is NOT
    semantically strong — it's a stand-in so nothing breaks without a key.
  * What we store: short, factual memory records (a question + the answer given,
    or a constraint + its outcome), each with metadata. We never store secrets.
  * Retrieval is advisory: recalled memories are shown to the assistant as
    context, clearly separable from live engine facts. Memory never overrides
    the engine's current computation.
"""
from __future__ import annotations

import os
import hashlib
from datetime import datetime
from typing import Optional

COLLECTION = "meridian_assistant_memory"
_VECTOR_SIZE = 1536  # OpenAI text-embedding-3-small; local embedder matches this
_client = None
_checked = False


# --------------------------------------------------------------------------
# Embeddings
# --------------------------------------------------------------------------
def _embed(text: str) -> list[float]:
    provider = os.environ.get("AND_EMBED_PROVIDER", "").lower()
    if provider == "openai" and os.environ.get("OPENAI_API_KEY"):
        try:
            return _embed_openai(text)
        except Exception:
            pass  # fall through to local
    return _embed_local(text)


def _embed_openai(text: str) -> list[float]:
    import urllib.request
    import json
    req = urllib.request.Request(
        "https://api.openai.com/v1/embeddings",
        data=json.dumps({"model": "text-embedding-3-small", "input": text}).encode(),
        headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"],
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    return data["data"][0]["embedding"]


def _embed_local(text: str) -> list[float]:
    """Deterministic hashing embedder — a stand-in so the pipeline runs without
    an API key. Not semantically strong; fine for demos, replace with a real
    embedder in production (set AND_EMBED_PROVIDER=openai)."""
    vec = [0.0] * _VECTOR_SIZE
    for token in text.lower().split():
        h = int(hashlib.md5(token.encode()).hexdigest(), 16)
        idx = h % _VECTOR_SIZE
        vec[idx] += 1.0
    # L2 normalise
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


# --------------------------------------------------------------------------
# Qdrant client (lazy, optional)
# --------------------------------------------------------------------------
def _get_client():
    global _client, _checked
    if _checked:
        return _client
    _checked = True
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams
        url = os.environ.get("QDRANT_URL", "http://localhost:6333")
        client = QdrantClient(url=url, timeout=3.0)
        # ensure collection exists
        existing = [c.name for c in client.get_collections().collections]
        if COLLECTION not in existing:
            client.create_collection(
                COLLECTION,
                vectors_config=VectorParams(size=_VECTOR_SIZE, distance=Distance.COSINE))
        _client = client
    except Exception:
        _client = None  # Qdrant not available -> memory disabled, app unaffected
    return _client


def available() -> bool:
    return _get_client() is not None


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def remember(text: str, *, kind: str = "note", meta: Optional[dict] = None) -> bool:
    """Store one memory record. Returns True if stored, False if memory is off."""
    client = _get_client()
    if client is None or not text.strip():
        return False
    try:
        from qdrant_client.models import PointStruct
        vec = _embed(text)
        pid = int(hashlib.md5((text + datetime.utcnow().isoformat()).encode())
                  .hexdigest()[:15], 16)
        payload = {"text": text, "kind": kind, "ts": datetime.utcnow().isoformat()}
        if meta:
            payload.update(meta)
        client.upsert(COLLECTION, points=[PointStruct(id=pid, vector=vec, payload=payload)])
        return True
    except Exception:
        return False


def recall(query: str, *, k: int = 3, kind: Optional[str] = None) -> list[dict]:
    """Return up to k relevant past memories for a query. Empty list if memory
    is off — callers treat that as 'no history', never an error."""
    client = _get_client()
    if client is None or not query.strip():
        return []
    try:
        vec = _embed(query)
        flt = None
        if kind:
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            flt = Filter(must=[FieldCondition(key="kind", match=MatchValue(value=kind))])
        hits = client.search(COLLECTION, query_vector=vec, limit=k, query_filter=flt)
        out = []
        for h in hits:
            p = h.payload or {}
            out.append({"text": p.get("text", ""), "kind": p.get("kind"),
                        "ts": p.get("ts"), "score": round(h.score, 3),
                        "meta": {kk: vv for kk, vv in p.items()
                                 if kk not in ("text", "kind", "ts")}})
        return out
    except Exception:
        return []


def stats() -> dict:
    client = _get_client()
    if client is None:
        return {"available": False, "count": 0}
    try:
        info = client.get_collection(COLLECTION)
        return {"available": True, "count": info.points_count or 0}
    except Exception:
        return {"available": False, "count": 0}
