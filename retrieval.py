# retrieval.py
from __future__ import annotations

import os
import re
import yaml
import logging
from typing import Dict, List, Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)


def get_conn():
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL manquant (Postgres requis pour retrieval).")
    return psycopg2.connect(dsn, sslmode=os.getenv("PGSSLMODE", "prefer"))


def _snippet(text: str, query: str, max_len: int = 420) -> str:
    if not text:
        return ""
    q = query.strip().lower()
    if not q:
        return text[:max_len].strip()
    pos = text.lower().find(q.split()[0])
    if pos < 0:
        return text[:max_len].strip()
    start = max(0, pos - 120)
    chunk = text[start:start + max_len]
    return chunk.strip()


def search_site(robot_id: str, query: str, *, top_k: int = 6) -> Dict:
    """
    Tool: recherche dans les pages crawlées (full-text Postgres).
    Retourne:
    {
      "hits": [
        {"url": "...", "title": "...", "snippet": "...", "score": 0.123},
        ...
      ]
    }
    """
    q = (query or "").strip()
    if not q:
        return {"hits": []}

    # websearch_to_tsquery est pratique (Postgres >= 11). sinon fallback plainto_tsquery.
    sql = """
    SELECT url, title, text_content,
           ts_rank(tsv, websearch_to_tsquery('simple', %s)) AS score
    FROM robot_site_pages
    WHERE robot_id = %s
      AND tsv @@ websearch_to_tsquery('simple', %s)
    ORDER BY score DESC, updated_at DESC
    LIMIT %s
    """

    hits: List[Dict] = []
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                cur.execute(sql, (q, robot_id, q, top_k))
            except psycopg2.errors.UndefinedFunction:
                # Fallback for older Postgres
                conn.rollback()
                sql2 = """
                SELECT url, title, text_content,
                       ts_rank(tsv, plainto_tsquery('simple', %s)) AS score
                FROM robot_site_pages
                WHERE robot_id = %s
                  AND tsv @@ plainto_tsquery('simple', %s)
                ORDER BY score DESC, updated_at DESC
                LIMIT %s
                """
                cur.execute(sql2, (q, robot_id, q, top_k))

            rows = cur.fetchall() or []
            for r in rows:
                hits.append(
                    {
                        "url": r.get("url") or "",
                        "title": r.get("title") or "",
                        "snippet": _snippet(r.get("text_content") or "", q),
                        "score": float(r.get("score") or 0.0),
                    }
                )

    return {"hits": hits}


def _flatten_yaml_text(data) -> str:
    # Convertit YAML en un gros texte (simple + efficace en V1)
    if data is None:
        return ""
    if isinstance(data, (str, int, float, bool)):
        return str(data)
    if isinstance(data, list):
        return "\n".join(_flatten_yaml_text(x) for x in data)
    if isinstance(data, dict):
        parts = []
        for k, v in data.items():
            parts.append(str(k))
            parts.append(_flatten_yaml_text(v))
        return "\n".join(parts)
    return str(data)


def search_pack(robot_id: str, query: str, *, pack_yaml_path: Optional[str] = None, top_k: int = 5) -> Dict:
    """
    Tool: recherche dans le pack métier (YAML).
    V1 simple : on transforme le YAML en texte + on extrait 1-5 lignes pertinentes.

    Paramètres:
    - pack_yaml_path: chemin vers le YAML du pack (ou via env PACKS_DIR + robot_id, etc.)
    """
    q = (query or "").strip().lower()
    if not q:
        return {"hits": []}

    # Stratégie simple : tu passes pack_yaml_path depuis ton app (recommandé).
    if not pack_yaml_path:
        packs_dir = os.getenv("PACKS_DIR", "packs")
        # fallback: suppose robot_id == nom de pack (à adapter chez toi si besoin)
        pack_yaml_path = os.path.join(packs_dir, f"{robot_id}.yaml")

    if not os.path.exists(pack_yaml_path):
        logger.warning("Pack YAML introuvable: %s", pack_yaml_path)
        return {"hits": []}

    with open(pack_yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    big_text = _flatten_yaml_text(data)
    lines = [ln.strip() for ln in big_text.splitlines() if ln.strip()]

    # scoring naive par nombre de mots qui match
    q_words = [w for w in re.split(r"\W+", q) if w]
    scored = []
    for ln in lines:
        low = ln.lower()
        score = sum(1 for w in q_words if w and w in low)
        if score > 0:
            scored.append((score, ln))

    scored.sort(key=lambda x: x[0], reverse=True)
    hits = [{"text": ln, "score": sc} for sc, ln in scored[:top_k]]
    return {"hits": hits}
