# shopify_bridge.py
import os
import hmac
import hashlib
import secrets
import sqlite3
from urllib.parse import urlencode

import requests
from flask import Blueprint, request, redirect, abort

shopify_bp = Blueprint("shopify_bp", __name__)

# ---------
# Config ENV
# ---------
SHOPIFY_API_KEY = os.environ.get("SHOPIFY_API_KEY", "").strip()
SHOPIFY_API_SECRET = os.environ.get("SHOPIFY_API_SECRET", "").strip()
SHOPIFY_SCOPES = (os.environ.get("SHOPIFY_SCOPES") or "read_products,read_orders").strip()
SHOPIFY_APP_URL = (os.environ.get("SHOPIFY_APP_URL") or "").strip().rstrip("/")  # ex: https://betty.spectramedia.online

# DB simple pour stocker les tokens
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database", "betty.db")


def _db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shopify_tokens (
            shop TEXT PRIMARY KEY,
            access_token TEXT NOT NULL,
            installed_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()
    return conn


def _verify_hmac(query_params: dict) -> bool:
    """
    Vérifie la signature Shopify (HMAC SHA256) sur le callback OAuth.
    Shopify envoie un param "hmac". On recalcule sur le reste des params triés.
    """
    if not SHOPIFY_API_SECRET:
        return False

    hmac_from_shopify = query_params.get("hmac", "")
    params = {k: v for k, v in query_params.items() if k != "hmac" and k != "signature"}

    # Shopify: concat "key=value" trié par clé, séparé par &
    message = "&".join([f"{k}={params[k]}" for k in sorted(params.keys())])

    digest = hmac.new(
        SHOPIFY_API_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(digest, hmac_from_shopify)


@shopify_bp.get("/shopify/install")
def shopify_install():
    """
    Démarre l'installation Shopify.
    URL appelée avec: /shopify/install?shop=xxxx.myshopify.com
    """
    if not SHOPIFY_API_KEY or not SHOPIFY_API_SECRET or not SHOPIFY_APP_URL:
        return "Shopify not configured. Missing env vars.", 500

    shop = (request.args.get("shop") or "").strip()
    if not shop or "myshopify.com" not in shop:
        return "Missing or invalid 'shop' parameter (ex: xxx.myshopify.com).", 400

    state = secrets.token_urlsafe(16)
    redirect_uri = f"{SHOPIFY_APP_URL}/auth/callback"

    auth_params = {
        "client_id": SHOPIFY_API_KEY,
        "scope": SHOPIFY_SCOPES,
        "redirect_uri": redirect_uri,
        "state": state,
    }

    # Note: si tu veux vraiment sécuriser "state", stocke-le en session.
    auth_url = f"https://{shop}/admin/oauth/authorize?{urlencode(auth_params)}"
    return redirect(auth_url)


@shopify_bp.get("/auth/callback")
def shopify_callback():
    """
    Shopify renvoie ici avec shop, code, hmac, state, timestamp...
    On vérifie HMAC puis échange code -> access_token.
    """
    if not SHOPIFY_API_KEY or not SHOPIFY_API_SECRET or not SHOPIFY_APP_URL:
        return "Shopify not configured. Missing env vars.", 500

    qp = request.args.to_dict(flat=True)

    shop = (qp.get("shop") or "").strip()
    code = (qp.get("code") or "").strip()

    if not shop or not code:
        # Si tu arrives ici en tapant l'URL à la main => normal que ça ne marche pas.
        return "Callback endpoint OK. Missing 'shop' or 'code' (normal if opened manually).", 200

    if not _verify_hmac(qp):
        abort(401, "Invalid HMAC")

    token_url = f"https://{shop}/admin/oauth/access_token"
    payload = {
        "client_id": SHOPIFY_API_KEY,
        "client_secret": SHOPIFY_API_SECRET,
        "code": code,
    }

    r = requests.post(token_url, json=payload, timeout=15)
    if r.status_code != 200:
        return f"Token exchange failed: {r.status_code} {r.text}", 500

    access_token = (r.json() or {}).get("access_token")
    if not access_token:
        return "Token exchange failed: no access_token in response.", 500

    conn = _db()
    conn.execute(
        "INSERT OR REPLACE INTO shopify_tokens(shop, access_token) VALUES(?, ?)",
        (shop, access_token)
    )
    conn.commit()
    conn.close()

    # Ici tu rediriges où tu veux (dashboard, page de confirmation, etc.)
    return redirect(f"{SHOPIFY_APP_URL}/?shopify=installed&shop={shop}")
