# site_refresh.py
from __future__ import annotations

import logging
from typing import Dict

from site_crawler import crawl_site
from site_store import save_crawled_site, mark_crawl_error, upsert_robot_site

logger = logging.getLogger(__name__)


def refresh_robot_site(robot_id: str, site_url: str, *, max_pages: int = 80) -> Dict:
    """
    Relance un crawl + remplace le stock en DB.
    Appelable :
    - manuellement (bouton)
    - via cron (Render)
    - via webhook interne
    """
    try:
        upsert_robot_site(robot_id, site_url)
        result = crawl_site(site_url, max_pages=max_pages)
        save_crawled_site(robot_id, site_url, result, replace=True)
        return {"ok": True, "pages": len(result.get("pages", []) or [])}
    except Exception as e:
        logger.exception("Refresh crawl failed for %s", robot_id)
        mark_crawl_error(robot_id, str(e))
        return {"ok": False, "error": str(e)}
