# site_store.py
from __future__ import annotations

import os
import json
import logging
from typing import Dict, List, Optional
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def get_conn():
    """
    Utilise DATABASE_URL (Postgres).
    Exemple Render : postgres://user:pass@host:5432/dbname
    """
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL manquant (Postgres requis pour le stockage du crawl).")
    return psycopg2.connect(dsn, sslmode=os.getenv("PGSSLMODE", "prefer"))


def ensure_tables():
    """
    Crée les tables minimales si elles n'existent pas.
    - robot_sites : meta par bot
    - robot_site_pages : pages crawlées + index full-text
    """
    sql = """
    CREATE TABLE IF NOT EXISTS robot_sites (
      robot_id TEXT PRIMARY KEY,
      site_url TEXT,
      status TEXT DEFAULT 'pending',
      last_crawled_at TIMESTAMPTZ,
      last_error TEXT
    );

    CREATE TABLE IF NOT EXISTS robot_site_pages (
      id BIGSERIAL PRIMARY KEY,
      robot_id TEXT NOT NULL REFERENCES robot_sites(robot_id) ON DELETE CASCADE,
      url TEXT NOT NULL,
      title TEXT,
      text_content TEXT,
      links_json JSONB,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      tsv tsvector
    );

    CREATE UNIQUE INDEX IF NOT EXISTS robot_site_pages_robot_url_idx
      ON robot_site_pages(robot_id, url);

    CREATE INDEX IF NOT EXISTS robot_site_pages_tsv_idx
      ON robot_site_pages USING GIN(tsv);

    CREATE INDEX IF NOT EXISTS robot_site_pages_robot_idx
      ON robot_site_pages(robot_id);
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()


def upsert_robot_site(robot_id: str, site_url: Optional[str] = None):
    ensure_tables()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO robot_sites(robot_id, site_url)
                VALUES(%s, %s)
                ON CONFLICT (robot_id) DO UPDATE
                SET site_url = COALESCE(EXCLUDED.site_url, robot_sites.site_url)
                """,
                (robot_id, site_url),
            )
        conn.commit()


def set_site_status(robot_id: str, status: str, error: Optional[str] = None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE robot_sites
                SET status=%s,
                    last_error=%s
                WHERE robot_id=%s
                """,
                (status, error, robot_id),
            )
        conn.commit()


def clear_pages(robot_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM robot_site_pages WHERE robot_id=%s", (robot_id,))
        conn.commit()


def save_crawled_site(robot_id: str, site_url: str, crawl_result: Dict, *, replace: bool = True):
    """
    Stocke tout ce qui a été crawlé :
    - meta robot_sites (status, last_crawled_at)
    - pages en robot_site_pages
    - prépare tsvector pour recherche full-text

    crawl_result format (depuis site_crawler.crawl_site):
      {"pages":[{"url","title","text","links"}...], "crawled_at": ..., "base_url": ...}
    """
    ensure_tables()
    upsert_robot_site(robot_id, site_url)

    pages: List[Dict] = crawl_result.get("pages", []) or []

    with get_conn() as conn:
        with conn.cursor() as cur:
            if replace:
                cur.execute("DELETE FROM robot_site_pages WHERE robot_id=%s", (robot_id,))

            for p in pages:
                url = p.get("url") or ""
                title = p.get("title") or ""
                text = p.get("text") or ""
                links = p.get("links") or []

                # tsvector based on title + text
                cur.execute(
                    """
                    INSERT INTO robot_site_pages(robot_id, url, title, text_content, links_json, updated_at, tsv)
                    VALUES (%s, %s, %s, %s, %s::jsonb, NOW(),
                            to_tsvector('simple', COALESCE(%s,'') || ' ' || COALESCE(%s,'')))
                    ON CONFLICT (robot_id, url) DO UPDATE
                    SET title = EXCLUDED.title,
                        text_content = EXCLUDED.text_content,
                        links_json = EXCLUDED.links_json,
                        updated_at = NOW(),
                        tsv = EXCLUDED.tsv
                    """,
                    (robot_id, url, title, text, json.dumps(links), title, text),
                )

            cur.execute(
                """
                UPDATE robot_sites
                SET status='ok',
                    last_crawled_at=NOW(),
                    last_error=NULL,
                    site_url=%s
                WHERE robot_id=%s
                """,
                (site_url, robot_id),
            )

        conn.commit()


def mark_crawl_error(robot_id: str, error: str):
    ensure_tables()
    upsert_robot_site(robot_id, None)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE robot_sites
                SET status='error',
                    last_error=%s
                WHERE robot_id=%s
                """,
                (error[:2000], robot_id),
            )
        conn.commit()


def get_robot_site_meta(robot_id: str) -> Dict:
    ensure_tables()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM robot_sites WHERE robot_id=%s", (robot_id,))
            row = cur.fetchone()
            return dict(row) if row else {}

def search_robot_site(robot_id: str, query: str, limit: int = 6) -> List[Dict]:
    """
    Retourne les pages les plus pertinentes du site pour une requête.
    """
    ensure_tables()
    q = (query or "").strip()
    if not q:
        return []

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT url, title,
                       left(text_content, 1200) AS snippet,
                       links_json,
                       ts_rank(tsv, plainto_tsquery('simple', %s)) AS score
                FROM robot_site_pages
                WHERE robot_id=%s
                  AND tsv @@ plainto_tsquery('simple', %s)
                ORDER BY score DESC, updated_at DESC
                LIMIT %s
                """,
                (q, robot_id, q, limit),
            )
            rows = cur.fetchall()
            return [dict(r) for r in rows] if rows else []
