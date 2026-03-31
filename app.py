
    
# app.py — VERSION PRO (Stripe webhook = SEUL déclencheur)
# ✅ CORRIGÉ :
# - build_system_prompt() retourne TOUJOURS une string
# - build_system_prompt_for_bot() ne “return” plus trop tôt (plus de code mort)
# - rule_based_next_question() respecte l’ordre : Nom -> Téléphone -> Email
# - /api/bettybot renvoie stage = effective_stage (cohérent avec ton calcul)
# - (conserve tes patchs Stripe / Postgres / pack->avatar)


from __future__ import annotations

# Standard library
import os
import json
import time
import base64
import hashlib
from contextlib import contextmanager
from urllib.parse import urlencode
import sys
import traceback
from flask import send_from_directory

from translations import translations

# ✅ Site crawler + stockage (Postgres)
from site_crawler import crawl_site
from site_store import (
    ensure_tables as ensure_site_tables,
    save_crawled_site,
    search_robot_site,
)

# Third-party
from flask import (
    Flask, render_template, request, jsonify, redirect,
    url_for, session, send_from_directory, Response
)
import requests
import stripe
import yaml
from jinja2 import TemplateNotFound

# ✅ Postgres
import psycopg2
import psycopg2.extras
from retrieval import search_site

# --- Gestion globale des exceptions non interceptées (log) ---
sys.excepthook = lambda t, v, tb: traceback.print_exception(t, v, tb)

# ==== APP FLASK ====
app = Flask(__name__)

import uuid
from datetime import datetime, timezone

def _utc_ts():
    return datetime.now(timezone.utc)

def db_demo_upsert_session(session_id: str, bot_id: str, ip: str | None, user_agent: str | None):
    now = _utc_ts()
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO demo_sessions (id, bot_id, started_at, last_seen_at, ip, user_agent)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET last_seen_at = EXCLUDED.last_seen_at,
                ip = COALESCE(demo_sessions.ip, EXCLUDED.ip),
                user_agent = COALESCE(demo_sessions.user_agent, EXCLUDED.user_agent)
        """, (session_id, bot_id, now, now, ip, user_agent))

def db_demo_ensure_session(session_id: str, bot_id: str):
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO demo_sessions (session_id, bot_id, created_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (session_id) DO NOTHING
        """, (session_id, bot_id, _utc_ts()))
        con.commit()

def db_demo_add_message(session_id: str, role: str, content: str):
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO demo_messages (session_id, role, content, created_at)
            VALUES (%s, %s, %s, %s)
        """, (session_id, role, content, _utc_ts()))
        con.commit()

def get_or_create_demo_session_id(payload: dict) -> str:
    # Priorité: conv_id envoyé par le front, sinon header, sinon génération
    sid = (payload.get("conv_id") or "").strip()
    if not sid:
        sid = str(uuid.uuid4())
    return sid


from shopify_bridge import shopify_bp
app.register_blueprint(shopify_bp)

@app.route("/carte")
def carte():
    return redirect("https://group1-6y79.onrender.com/", code=302)

app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

# ---- Cookies / sécurité iframe ----
SESSION_SECURE = os.getenv("SESSION_SECURE", "true").lower() == "true"
app.config.update(
    SESSION_COOKIE_SAMESITE="None",
    SESSION_COOKIE_SECURE=SESSION_SECURE,
)

# ---- Config LLM / Stripe / Mailjet / Base ----
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY", "").strip()
TOGETHER_API_URL = "https://api.together.xyz/v1/chat/completions"
LLM_MODEL = os.getenv("LLM_MODEL", "meta-llama/Meta-Llama-3-8B-Instruct-Turbo").strip()
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "60"))

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
PRICE_ID = os.getenv("STRIPE_PRICE_ID", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()

# ✅ TRIAL : 7 jours (configurable via env STRIPE_TRIAL_DAYS)
TRIAL_DAYS = int(os.getenv("STRIPE_TRIAL_DAYS", "7"))

# ✅ Auto-blocage si non payé après X jours
TRIAL_BLOCK_DAYS = int(os.getenv("TRIAL_BLOCK_DAYS", str(TRIAL_DAYS)))
TRIAL_GRACE_HOURS = int(os.getenv("TRIAL_GRACE_HOURS", "2"))

BASE_URL = (os.getenv("BASE_URL", "http://127.0.0.1:5000")).rstrip("/")

# Stripe Customer Portal return url (optionnel)
STRIPE_PORTAL_RETURN_URL = (os.getenv("STRIPE_PORTAL_RETURN_URL", "") or "").strip()

MJ_API_KEY    = os.getenv("MJ_API_KEY", "").strip()
MJ_API_SECRET = os.getenv("MJ_API_SECRET", "").strip()
MJ_FROM_EMAIL = os.getenv("MJ_FROM_EMAIL", "no-reply@spectramedia.online").strip()
MJ_FROM_NAME  = os.getenv("MJ_FROM_NAME", "Spectra Media AI").strip()

# ➕ Nouveaux env pour routage des leads en démo
DEMO_LEAD_EMAIL = os.getenv("DEMO_LEAD_EMAIL", "").strip()

app.jinja_env.globals["BASE_URL"] = BASE_URL

# =========================================================
# PACK / AVATAR HELPERS  (Patch: pack doit primer partout)
# =========================================================

PACK_LABELS = {
    "avocat": "Avocat",
    "medecin": "Médecin",
    "immo": "Immobilier",
    "immobilier": "Immobilier",
    "agent_immobilier": "Immobilier",
    "betty_dj": "DJ",
    "betty_trader": "Trader",
    "betty_plombier": "Plombier",
    "betty_serrurier": "Serrurier",
    "betty_yoga": "Prof de yoga",
    "betty_coiffeur": "Coiffeur",
    "betty_estheticienne": "Esthéticienne",
    "betty_mecano": "Mécano",
    "betty_kine": "Kiné",
    "betty_osteopate": "Ostéopathe",
    "betty_dentiste": "Dentiste",
    "betty_infirmiere": "Infirmière",
    "betty_nutritioniste": "Nutritionniste",
    "betty_paysagiste": "Paysagiste",
    "betty_photographe": "Photographe",
    "betty_graphiste": "Graphiste",
    "betty_architecte": "Architecte",
    "betty_artisan": "Artisan",
    "betty_assurance": "Assurance",
    "betty_marketing": "Marketing",
    "betty_coach": "Coach",
    "betty_menage": "Ménage",
    "betty_traiteur": "Traiteur",
    "betty_verrier": "Verrier",
    "betty_garde_denfant": "Garde d’enfant",
    "betty_assistance_scolaire": "Assistance scolaire",
    "betty_soutien_scolaire": "Soutien scolaire",
    "betty_sophrologue": "Sophrologue",
    "betty_aide_a_domicile": "Aide à domicile",
    "betty_aide_a_dom": "Aide à domicile (rapide)",
}

PACK_AVATAR = {
    "avocat": "avocat.jpg",
    "medecin": "medecin.jpg",
    "immo": "immo.jpg",
    "immobilier": "immo.jpg",
    "agent_immobilier": "immo.jpg",

    "betty_aide_a_domicile": "Betty_aide_a_domicile.png",
    "betty_aide_a_dom": "Betty_aide_a_dom.png",
    "betty_architecte": "Betty_architecte.png",
    "betty_artisan": "Betty_artisan.png",
    "betty_assistance_scolaire": "Betty_assistance_scolaire.png",
    "betty_assurance": "Betty_assurance.png",
    "betty_coach": "Betty_coach.png",
    "betty_coiffeur": "Betty_coiffeur.png",
    "betty_dentiste": "Betty_dentiste.png",
    "betty_dj": "Betty_DJ.png",
    "betty_estheticienne": "Betty_estheticienne.png",
    "betty_garde_denfant": "Betty_garde_denfant.png",
    "betty_graphiste": "Betty_graphiste.png",
    "betty_infirmiere": "Betty_infirmiere.png",
    "betty_kine": "Betty_kine.png",
    "betty_marketing": "Betty_marketing.png",
    "betty_mecano": "Betty_mecano.png",
    "betty_menage": "Betty_menage.png",
    "betty_nutritioniste": "Betty_nutritioniste.png",
    "betty_osteopate": "Betty_osteopate.png",
    "betty_paysagiste": "Betty_paysagiste.png",
    "betty_photographe": "Betty_photographe.png",
    "betty_plombier": "Betty_plombier.png",
    "betty_serrurier": "Betty_serrurier.png",
    "betty_sophrologue": "Betty_sophrologue.png",
    "betty_soutien_scolaire": "Betty_soutien_scolaire.png",
    "betty_trader": "Betty_trader.png",
    "betty_traiteur": "Betty_traiteur.png",
    "betty_verrier": "Betty_verrier.png",
    "betty_yoga": "Betty_yoga.png",
}

CORE_BOTKEY_BY_PACK = {
    "avocat": "avocat-001",
    "medecin": "medecin-003",
    "immo": "immo-002",
    "immobilier": "immo-002",
    "agent_immobilier": "immo-002",
}

def _safe_pack(pack: str) -> str:
    return (pack or "").strip()

def humanize_pack(pack: str) -> str:
    p = _safe_pack(pack).lower()
    if p in PACK_LABELS:
        return PACK_LABELS[p]
    p2 = re.sub(r"^betty_", "", p)
    p2 = p2.replace("_", " ").strip()
    return p2[:1].upper() + p2[1:] if p2 else "Métier"

def avatar_for_pack(pack: str, avatar_hint: str = "", fallback: str = "avocat.jpg") -> str:
    p = _safe_pack(pack).lower()
    if p in PACK_AVATAR:
        return PACK_AVATAR[p]
    if avatar_hint:
        return avatar_hint
    return fallback

def botkey_for_pack(pack: str) -> str:
    p = _safe_pack(pack).lower()
    if p in CORE_BOTKEY_BY_PACK:
        return CORE_BOTKEY_BY_PACK[p]
    slug = re.sub(r"[^a-z0-9]+", "", p)[:10] or "bot"
    return f"custom-{slug}"

# =========================================================
# DB (PostgreSQL via DATABASE_URL) — VERSION PRO VERCEL
# =========================================================

def _normalize_db_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url

DATABASE_URL = _normalize_db_url(os.getenv("DATABASE_URL", "").strip())
DB_SSLMODE = (os.getenv("DB_SSLMODE", "") or "").strip()  # ex: require

@contextmanager
def db_connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL manquant. Ajoute-le dans Vercel (env vars).")

    connect_kwargs = {
        "dsn": DATABASE_URL,
        "cursor_factory": psycopg2.extras.RealDictCursor,
    }
    if DB_SSLMODE:
        connect_kwargs["sslmode"] = DB_SSLMODE

    con = psycopg2.connect(**connect_kwargs)
    try:
        yield con
    finally:
        con.close()

def db_init():
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS bots (
            public_id    TEXT PRIMARY KEY,
            bot_key      TEXT NOT NULL,
            pack         TEXT NOT NULL,
            name         TEXT,
            color        TEXT,
            avatar_file  TEXT,
            greeting     TEXT,
            buyer_email  TEXT,
            owner_name   TEXT,
            profile_json TEXT,

            paid INTEGER DEFAULT 0,
            purchase_email_sent INTEGER DEFAULT 0,

            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            stripe_status TEXT,

            created_at BIGINT,
            trial_end BIGINT,
            blocked INTEGER DEFAULT 0
        );
        """)
        con.commit()

def db_migrate():
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("ALTER TABLE bots ADD COLUMN IF NOT EXISTS paid INTEGER DEFAULT 0;")
        cur.execute("ALTER TABLE bots ADD COLUMN IF NOT EXISTS purchase_email_sent INTEGER DEFAULT 0;")

        cur.execute("ALTER TABLE bots ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT;")
        cur.execute("ALTER TABLE bots ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT;")
        cur.execute("ALTER TABLE bots ADD COLUMN IF NOT EXISTS stripe_status TEXT;")

        cur.execute("ALTER TABLE bots ADD COLUMN IF NOT EXISTS created_at BIGINT;")
        cur.execute("ALTER TABLE bots ADD COLUMN IF NOT EXISTS trial_end BIGINT;")
        cur.execute("ALTER TABLE bots ADD COLUMN IF NOT EXISTS blocked INTEGER DEFAULT 0;")
        con.commit()
        

    
def db_upsert_bot(bot: dict):
    profile_json = json.dumps(bot.get("profile") or {}, ensure_ascii=False)

    paid = int(bot.get("paid") or 0)
    sent = int(bot.get("purchase_email_sent") or 0)

    stripe_customer_id = (bot.get("stripe_customer_id") or "").strip() or None
    stripe_subscription_id = (bot.get("stripe_subscription_id") or "").strip() or None
    stripe_status = (bot.get("stripe_status") or "").strip() or None

    created_at = bot.get("created_at")
    trial_end = bot.get("trial_end")
    blocked = int(bot.get("blocked") or 0)

    with db_connect() as con:
        cur = con.cursor()
        cur.execute("""
        INSERT INTO bots(
          public_id, bot_key, pack, name, color, avatar_file, greeting,
          buyer_email, owner_name, profile_json,
          paid, purchase_email_sent,
          stripe_customer_id, stripe_subscription_id, stripe_status,
          created_at, trial_end, blocked
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(public_id) DO UPDATE SET
          bot_key=EXCLUDED.bot_key,
          pack=EXCLUDED.pack,
          name=EXCLUDED.name,
          color=EXCLUDED.color,
          avatar_file=EXCLUDED.avatar_file,
          greeting=EXCLUDED.greeting,
          buyer_email=EXCLUDED.buyer_email,
          owner_name=EXCLUDED.owner_name,
          profile_json=EXCLUDED.profile_json,
          paid=EXCLUDED.paid,
          purchase_email_sent=EXCLUDED.purchase_email_sent,
          stripe_customer_id=EXCLUDED.stripe_customer_id,
          stripe_subscription_id=EXCLUDED.stripe_subscription_id,
          stripe_status=EXCLUDED.stripe_status,
          created_at=COALESCE(EXCLUDED.created_at, bots.created_at),
          trial_end=COALESCE(EXCLUDED.trial_end, bots.trial_end),
          blocked=EXCLUDED.blocked
        """, (
            bot.get("public_id"),
            bot.get("bot_key"),
            bot.get("pack"),
            bot.get("name"),
            bot.get("color"),
            bot.get("avatar_file"),
            bot.get("greeting"),
            bot.get("buyer_email"),
            bot.get("owner_name"),
            profile_json,
            paid,
            sent,
            stripe_customer_id,
            stripe_subscription_id,
            stripe_status,
            created_at,
            trial_end,
            blocked
        ))
        con.commit()

def db_get_bot(public_id: str):
    public_id = (public_id or "").strip()
    if not public_id:
        return None

    with db_connect() as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM bots WHERE public_id=%s LIMIT 1", (public_id,))
        row = cur.fetchone()

    if not row:
        return None

    d = dict(row)

    d["profile"] = {}
    if d.get("profile_json"):
        try:
            d["profile"] = json.loads(d["profile_json"])
        except Exception:
            d["profile"] = {}

    d["paid"] = int(d.get("paid") or 0)
    d["purchase_email_sent"] = int(d.get("purchase_email_sent") or 0)
    d["stripe_customer_id"] = d.get("stripe_customer_id") or None
    d["stripe_subscription_id"] = d.get("stripe_subscription_id") or None
    d["stripe_status"] = d.get("stripe_status") or None
    d["created_at"] = d.get("created_at")
    d["trial_end"] = d.get("trial_end")
    d["blocked"] = int(d.get("blocked") or 0)
    return d
    
from datetime import datetime

def ensure_demo_tables():
    with db_connect() as con:
        cur = con.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS demo_sessions (
            id TEXT PRIMARY KEY,
            bot_id TEXT NOT NULL,
            started_at TIMESTAMP NOT NULL,
            last_seen_at TIMESTAMP NOT NULL,
            ip TEXT,
            user_agent TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS demo_messages (
            id SERIAL PRIMARY KEY,
            session_id TEXT REFERENCES demo_sessions(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL
        )
        """)
        con.commit()


def db_mark_purchase_email_sent(public_id: str):
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("UPDATE bots SET purchase_email_sent=1 WHERE public_id=%s", (public_id,))
        con.commit()

def db_get_flags(public_id: str) -> tuple[int, int]:
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("SELECT paid, purchase_email_sent FROM bots WHERE public_id=%s LIMIT 1", (public_id,))
        row = cur.fetchone()
    if not row:
        return (0, 0)
    return (int(row.get("paid") or 0), int(row.get("purchase_email_sent") or 0))

import re

def db_demo_list_sessions(limit: int = 200):
    with db_connect() as con:
        cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, bot_id, ip, user_agent, started_at, last_seen_at
            FROM demo_sessions
            ORDER BY last_seen_at DESC
            LIMIT %s
        """, (limit,))
        return cur.fetchall() or []

def db_demo_get_messages(session_id: str):
    with db_connect() as con:
        cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT role, content, created_at
            FROM demo_messages
            WHERE session_id = %s
            ORDER BY created_at ASC
        """, (session_id,))
        return cur.fetchall() or []

def guess_contact_from_messages(messages):
    """Heuristique simple : email + tel si trouvés dans la conversation."""
    full = "\n".join([(m.get("content") or "") for m in messages if m.get("content")])
    email = None
    phone = None

    m = re.search(r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})", full)
    if m:
        email = m.group(1)

    m = re.search(r"(\+33\s?[1-9](?:[\s.\-]?\d{2}){4}|0[1-9](?:[\s.\-]?\d{2}){4})", full)
    if m:
        phone = m.group(1)

    who = "—"
    if email and phone:
        who = f"{email} • {phone}"
    elif email:
        who = email
    elif phone:
        who = phone

    return {"email": email, "phone": phone, "who": who}

def db_get_bot_by_subscription_id(sub_id: str):
    sub_id = (sub_id or "").strip()
    if not sub_id:
        return None
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM bots WHERE stripe_subscription_id=%s LIMIT 1", (sub_id,))
        row = cur.fetchone()
    return dict(row) if row else None

def db_set_paid_status(public_id: str, paid: int, stripe_status: str | None = None, blocked: int | None = None):
    paid = int(paid)
    with db_connect() as con:
        cur = con.cursor()
        if stripe_status is None and blocked is None:
            cur.execute("UPDATE bots SET paid=%s WHERE public_id=%s", (paid, public_id))
        else:
            fields = ["paid=%s"]
            vals = [paid]
            if stripe_status is not None:
                fields.append("stripe_status=%s")
                vals.append(stripe_status)
            if blocked is not None:
                fields.append("blocked=%s")
                vals.append(int(blocked))
            vals.append(public_id)
            cur.execute(f"UPDATE bots SET {', '.join(fields)} WHERE public_id=%s", tuple(vals))
        con.commit()

def db_set_trial(public_id: str, created_at: int | None = None, trial_end: int | None = None):
    fields = []
    vals = []
    if created_at is not None:
        fields.append("created_at=%s")
        vals.append(int(created_at))
    if trial_end is not None:
        fields.append("trial_end=%s")
        vals.append(int(trial_end))
    if not fields:
        return
    vals.append(public_id)
    with db_connect() as con:
        cur = con.cursor()
        cur.execute(f"UPDATE bots SET {', '.join(fields)} WHERE public_id=%s", tuple(vals))
        con.commit()

# =========================================================
# STATUTS PRO : ESSAI / PAYÉ / BLOQUÉ
# =========================================================

def _now_ts() -> int:
    return int(time.time())

def compute_access_state(bot: dict) -> tuple[str, str]:
    if not bot:
        return ("—", "unknown")

    paid = int(bot.get("paid") or 0)
    blocked = int(bot.get("blocked") or 0)
    st = (bot.get("stripe_status") or "unknown").strip().lower()

    if blocked == 1 or st in ("canceled", "unpaid", "trial_expired", "blocked"):
        return ("BLOQUÉ", "canceled")

    if paid == 1 and st in ("active", "trialing", "past_due", "unknown"):
        return ("PAYÉ", "active")

    return ("ESSAI", "trialing" if st == "trialing" else "unknown")

def should_block_bot(bot: dict) -> tuple[bool, str]:
    if not bot:
        return (False, "")

    paid = int(bot.get("paid") or 0)
    if paid == 1:
        return (False, "")

    blocked = int(bot.get("blocked") or 0)
    if blocked == 1:
        return (True, "blocked")

    now = _now_ts()
    created_at = bot.get("created_at") or now
    trial_end = bot.get("trial_end")
    grace = TRIAL_GRACE_HOURS * 3600

    if trial_end:
        if now > int(trial_end) + grace:
            return (True, "trial_expired")
        return (False, "")

    limit = int(created_at) + int(TRIAL_BLOCK_DAYS) * 86400 + grace
    if now > limit:
        return (True, "trial_expired")
    return (False, "")

def enforce_block_if_needed(public_id: str, bot: dict) -> dict:
    if not public_id or not bot:
        return bot
    block, reason = should_block_bot(bot)
    if block:
        with db_connect() as con:
            cur = con.cursor()
            cur.execute(
                "UPDATE bots SET blocked=1, stripe_status=%s WHERE public_id=%s",
                (reason or "blocked", public_id)
            )
            con.commit()
        bot2 = dict(bot)
        bot2["blocked"] = 1
        bot2["stripe_status"] = reason or bot2.get("stripe_status")
        return bot2
    return bot

# =========================================================
# INIT DB
# =========================================================
db_init()
db_migrate()
ensure_site_tables()
ensure_demo_tables()


# ==== Favicons & manifest (anti 404->500) ====
@app.route("/favicon.ico")
def favicon_root():
    p = os.path.join(app.root_path, "static", "favicon.ico")
    if os.path.exists(p):
        return send_from_directory(os.path.dirname(p), os.path.basename(p))
    return "", 204

@app.route("/favicon.png")
def favicon_png():
    p = os.path.join(app.root_path, "static", "favicon.png")
    if os.path.exists(p):
        return send_from_directory(os.path.dirname(p), os.path.basename(p))
    return "", 204

@app.route("/favicon-16x16.png")
def fav16():
    p = os.path.join(app.root_path, "static", "favicon-16x16.png")
    if os.path.exists(p):
        return send_from_directory(os.path.dirname(p), os.path.basename(p))
    return "", 204

@app.route("/favicon-32x32.png")
def fav32():
    p = os.path.join(app.root_path, "static", "favicon-32x32.png")
    if os.path.exists(p):
        return send_from_directory(os.path.dirname(p), os.path.basename(p))
    return "", 204

@app.route("/site.webmanifest")
def site_manifest():
    p = os.path.join(app.root_path, "static", "site.webmanifest")
    if os.path.exists(p):
        return send_from_directory(os.path.dirname(p), os.path.basename(p))
    return jsonify({"name":"Betty Bots","short_name":"Betty","icons":[]}), 200

# ==== Helpers ====
def static_url(filename: str) -> str:
    return url_for("static", filename=filename)

def parse_contact_info(raw: str) -> dict:
    raw = (raw or "").strip()
    if not raw:
        return {"raw": "", "name": "", "email": "", "phone": "", "address": "", "hours": ""}
    m_email = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', raw)
    email = m_email.group(0) if m_email else ""
    m_phone = re.search(r'(\+?\d[\d \.\-]{6,})', raw)
    phone = m_phone.group(1).strip() if m_phone else ""
    m_hours = re.search(r'(horaire|heures?|ouvertures?)\s*[:\-]?\s*(.+)', raw, re.I)
    hours = m_hours.group(2).strip() if m_hours else ""
    m_name = re.search(r'(?:nom|entreprise|cabinet)\s*[:\-]?\s*(.+)', raw, re.I)
    name = m_name.group(1).strip() if m_name else ""
    m_addr = re.search(r'(?:adresse|address)\s*[:\-]?\s*(.+)', raw, re.I)
    address = m_addr.group(1).strip() if m_addr else ""
    return {"raw": raw, "name": name, "email": email, "phone": phone, "address": address, "hours": hours}

# ==== Profile safety (anti "str has no attribute get") ====
def ensure_profile_dict(profile):
    if isinstance(profile, dict):
        return profile
    if isinstance(profile, str):
        s = profile.strip()
        if not s:
            return {}
        try:
            obj = json.loads(s)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}

def build_business_block(profile) -> str:
    profile = ensure_profile_dict(profile)
    if not profile:
        return ""
    lines = ["\n---\nINFORMATIONS ETABLISSEMENT (utilise-les dans tes réponses) :"]
    if profile.get("name"):    lines.append(f"• Nom : {profile['name']}")
    if profile.get("phone"):   lines.append(f"• Téléphone : {profile['phone']}")
    if profile.get("email"):   lines.append(f"• Email : {profile['email']}")
    if profile.get("address"): lines.append(f"• Adresse : {profile['address']}")
    if profile.get("hours"):   lines.append(f"• Horaires : {profile['hours']}")
    lines.append("---\n")
    return "\n".join(lines)

# ✅ FIX: build_system_prompt retourne toujours une string
def build_system_prompt(base: str, path: str, profile: dict, greeting: str = "") -> str:
    biz = build_business_block(profile)

    # Charge YAML métier si présent
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            gender = (data.get("gender") or "f").strip().lower()
            if gender == "m":
                genre_prompt = "Tu es un assistant virtuel. Tu parles toujours au masculin. Tu ne dois jamais utiliser le feminin."
            else:
                genre_prompt = "Tu es une assistante virtuelle. Tu parles toujours au feminin. Tu ne dois jamais utiliser le masculin."

            base = genre_prompt + "\n\n" + (data.get("prompt") or base)
        except Exception:
            pass

    guide = client_guide

    greet = f"\nMessage d'accueil recommandé : {greeting}\n" if greeting else ""
    return f"{base}\n{biz}\n{guide}\n{greet}"

def build_dynamic_job_prompt(job: str) -> str:
    job = (job or "").strip()

    if not job:
        return ""

    return f"""
Tu es Betty, une assistante commerciale spécialisée dans le métier suivant :

{job}

Ton rôle est :
- répondre aux questions des visiteurs
- comprendre leur besoin
- qualifier leur demande
- récupérer leurs coordonnées
- transmettre le lead au professionnel

Tu réponds de manière naturelle, humaine et professionnelle.

Tu es experte dans ce domaine : {job}.
"""

# ✅ FIX: plus de return trop tôt / plus de code mort
def build_system_prompt_for_bot(pack: str, profile, greeting: str = "") -> str:
    pack_code = (pack or "avocat").strip().lower()
    pack_label = humanize_pack(pack_code)

    base = (
        f"Tu es Betty, une assistante virtuelle professionnelle ({pack_label}). "
        "Tu es chaleureuse, claire et orientée conversion."
    )

    path = os.path.join(app.root_path, "packs", f"{pack_code}.yaml")

    return build_system_prompt(
        base=base,
        path=path,
        profile=ensure_profile_dict(profile),
        greeting=greeting or ""
    )
def build_system_prompt_for_demo() -> str:
    """
    Démo = prompt UNIQUEMENT depuis packs/demo.yaml (aucun ajout).
    """
    path = os.path.join(app.root_path, "packs", "demo.yaml")

    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            p = (data.get("prompt") or "").strip()
            if p:
                return p
        except Exception as e:
            print("[DEMO][YAML] load failed:", type(e).__name__, e)

    # Fallback ultra minimal si demo.yaml absent/cassé
    return "Tu es Betty, assistante de démonstration de Spectra Media AI."

# ==== LLM ====
def call_llm_with_history(system_prompt: str, history: list, user_input: str) -> str:
    if not TOGETHER_API_KEY:
        return ""
    headers = {"Authorization": f"Bearer {TOGETHER_API_KEY}", "Content-Type": "application/json"}
    messages = [{"role": "system", "content": system_prompt or ""}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": user_input})
    payload = {
        "model": LLM_MODEL,
        "max_tokens": LLM_MAX_TOKENS,
        "temperature": 0.55,
        "messages": messages
    }
    backoffs = [0]
    last_err_text = None
    for wait in backoffs:
        try:
            r = requests.post(TOGETHER_API_URL, headers=headers, json=payload, timeout=30)
            if r.ok:
                data = r.json()
                content = (data.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
                if content:
                    return content
                last_err_text = "Réponse vide du modèle."
            else:
                try:
                    err = r.json()
                    last_err_text = f"{err.get('error',{}).get('message') or err}"
                except Exception:
                    last_err_text = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            last_err_text = f"{type(e).__name__}: {e}"
        time.sleep(wait)
    print("[LLM][Together][FAIL]", last_err_text or "unknown")
    return ""

# ==== LEAD JSON helpers ====
LEAD_TAG_RE = re.compile(
    r"<\s*LEAD_?JSON\s*>\s*(\{.*?\})\s*</\s*LEAD_?JSON\s*>",
    re.IGNORECASE | re.DOTALL
)

def extract_lead_json(text: str):
    if not text:
        return text, None
    matches = list(LEAD_TAG_RE.finditer(text))
    lead = None
    if matches:
        m = matches[-1]
        lead_raw = (m.group(1) or "").strip()
        try:
            lead = json.loads(lead_raw)
        except Exception:
            lead = None
        text = LEAD_TAG_RE.sub("", text)
    return (text or "").strip(), lead

def _lead_from_history(history: list) -> dict:
    user_msgs = [ (m.get("content") or "").strip() for m in history if m.get("role") == "user" ]
    user_text = " ".join(user_msgs)
    d = {"reason": "", "email": "", "phone": "", "name": "", "availability": "", "stage": "collecting"}
    if not user_msgs:
        return d
    m_email = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', user_text, flags=re.I)
    if m_email:
        d["email"] = m_email.group(0)
    m_phone = re.search(r'(\+?\d[\d \.\-\(\)]{6,})', user_text)
    if m_phone:
        d["phone"] = re.sub(r'[^0-9\+]', '', m_phone.group(1)).strip()
    m_name = re.search(r"(?:je m(?:'|e)appelle|nom\s*:?)\s*([A-Za-zÀ-ÖØ-öø-ÿ'\-\s]{2,80})", user_text, re.I)
    if m_name:
        d["name"] = m_name.group(1).strip()
    if not d["name"]:
        for msg in reversed(user_msgs):
            txt = msg.strip()
            if not txt or '@' in txt or re.search(r'\d', txt):
                continue
            if not re.fullmatch(r"[A-Za-zÀ-ÖØ-öø-ÿ' \-]{3,80}", txt):
                continue
            tokens = [t for t in re.split(r"\s+", txt) if t]
            if 2 <= len(tokens) <= 3:
                lower = txt.lower()
                if any(w in lower for w in ["bonjour", "bonsoir", "merci", "svp", "rdv", "rendez", "appel", "mail"]):
                    continue
                d["name"] = " ".join(t.capitalize() for t in tokens)
                break
    m_reason = re.search(r'(?:souhaite|veux|voudrais|besoin|motif|pour)\s*:?(.{5,140})', user_text, re.I)
    if m_reason:
        d["reason"] = m_reason.group(1).strip()
    m_avail = re.search(r'(demain|matin|après-midi|soir|lundi|mardi|mercredi|jeudi|vendredi)[^\.!?]{0,60}', user_text, re.I)
    if m_avail:
        d["availability"] = m_avail.group(0).strip()
    if d["phone"] and d["name"] and d["email"]:
        d["stage"] = "ready"
    return d

SENT_SPLIT_RE = re.compile(r"(?<=[\.\!\?])\s+")
def enforce_one_empathy_plus_question(text: str, max_sentences: int = 2) -> str:
    if not text:
        return text

    # garde max 2 phrases, mais autorise une phrase sans "?" avant la question
    sentences = SENT_SPLIT_RE.split(text)
    sentences = [s.strip() for s in sentences if s.strip()]

    kept = []
    has_question = False
    for s in sentences:
        if not kept:
            kept.append(s)
            has_question = "?" in s
            if has_question:
                break
            continue

        # deuxième phrase : on veut une question si possible
        kept.append(s)
        has_question = has_question or ("?" in s)
        break

    out = " ".join(kept[:max_sentences]).strip()
    out = re.sub(r"\s+", " ", out)
    return out

INTENT_RDV_RE = re.compile(r"\b(rendez[- ]?vous|rdv|prise? (de )?rendez[- ]?vous|prendre un rdv|booking|appointment)\b", re.I)
CONSENT_RE    = re.compile(r"\b(oui|ok|okay|yes|si|d['’ ]?accord|vas[- ]?y|go|let.?s go|ça marche|ca marche)\b", re.I)

def enforce_single_question(text: str) -> str:
    return text


def guardrailed_reply(history: list, user_input: str, llm_text: str, pack: str) -> tuple[str, dict, bool, str]:
    """
    Objectif:
    - Le LLM est MAÎTRE de la conversation (on garde sa réponse)
    - On collecte naturellement (post-traitement): si une info manque, on ajoute UNE question humaine
    - On n'écrase pas le style, on le "termine" proprement
    """

    augmented_history = history + ([{"role": "user", "content": user_input}] if user_input else [])
    lead = _lead_from_history(augmented_history)

    # 1) Cas tout début
    if len(history) == 0:
        msg = "Bonjour 🙂 Comment puis-je vous aider ?"
        return enforce_single_question(msg), lead, False, "collecting"

    # 2) On part TOUJOURS du texte LLM (il est maître)
    response_text_llm, _ = extract_lead_json(llm_text or "")
    # 🔒 Fallback si le LLM répond mal ou vide
    if not response_text_llm or len(response_text_llm.strip()) < 5:
        response_text_llm = "Bonjour 🙂 Comment puis-je vous aider aujourd’hui ?"
    response_text_llm = re.sub(
        r"<\s*LEAD_?JSON\s*>.*?</\s*LEAD_?JSON\s*>",
        "",
        response_text_llm or "",
        flags=re.DOTALL | re.IGNORECASE,
    ).strip()

    # Si LLM vide => fallback doux (mais humain)
    if not response_text_llm:
        response_text_llm = "D’accord 🙂 Dites-m’en un peu plus, et je m’occupe de transmettre."

    # 3) Déterminer l’info manquante (ordre EXACT que tu veux)
    missing = None

    if not lead.get("name"):
        missing = "name"

    if lead.get("name") and not lead.get("phone"):
        missing = "phone"

    if lead.get("name") and lead.get("phone") and not lead.get("email"):
        missing = "email"

    # 4) Questions HUMAINES (pas robot / pas argot / bon français)
    #    -> une seule question, courte, après la compréhension
    Q = {
        "name": [
            "Au passage, je peux avoir votre nom et prénom ?",
            "Je peux noter votre nom et prénom ?",
            "Pour que je transmette correctement, quel est votre nom et prénom ?",
        ],
        "phone": [
            "Et quel numéro puis-je donner pour vous rappeler ?",
            "Quel est le meilleur numéro pour vous joindre ?",
            "Vous préférez être rappelé sur quel numéro ?",
        ],
        "email": [
            "Et votre adresse e-mail, s’il vous plaît ?",
            "Je peux avoir votre e-mail (pour le suivi) ?",
            "À quelle adresse e-mail puis-je vous recontacter ?",
        ],
    }

    # 5) Rendre la réponse plus “humaine” sans casser le LLM :
    #    - On garde la réponse du LLM
    #    - Si une info manque, on ajoute UNE question à la fin (si aucune question pertinente n’est déjà posée)
    response_text = response_text_llm.strip()

    # petite hygiène
    response_text = enforce_one_empathy_plus_question(response_text, max_sentences=3)

    def is_low_intent(text):
        t = (text or "").lower().strip()
        return (
            len(t) < 4
            or t in ["ok", "cc", "oui", "non", "?", "pourquoi", "ça va", "ca va"]
        )

    if missing and not is_low_intent(user_input):

        has_q = "?" in response_text

        if not has_q:

            soft_followups = {
                "name": "Au fait 🙂 je peux avoir votre prénom pour mieux vous guider ?",
                "phone": "Si c’est ok pour vous, je peux noter votre numéro pour vous rappeler facilement ?",
                "email": "Parfait 👍 et votre e-mail pour que je puisse vous envoyer les infos ?",
            }

            response_text = response_text.rstrip()

            if len(response_text) < 60:
                response_text = (
                    response_text
                    + " Je peux vous aider à trouver exactement ce qu’il vous faut."
                )

            response_text = (
                response_text + " " + soft_followups.get(missing, "")
            ).strip()

        stage = "collecting"
        should_send_now = False
  
    else:

        stage = "ready"
        should_send_now = True

    if "?" not in response_text:
        response_text = (
            response_text.rstrip()
            + " Parfait, je transmets vos coordonnées. Vous serez rappelé rapidement."
        ).strip()
        
    return enforce_single_question(response_text), lead, should_send_now, stage

# 🔥 Force comportement commercial agressif (Spectra)

    
def rule_based_next_question(pack: str, history: list) -> str:
    lead = _lead_from_history(history)
    if not lead["phone"]:
        msg = "Quel est votre numéro de téléphone ?"
    elif not lead["name"]:
        msg = "Quel est votre nom et prénom complets ?"
    elif not lead["email"]:
        msg = "Quelle est votre adresse e-mail ?"
    else:
        msg = "Parfait, je transmets vos coordonnées. Vous serez rappelé rapidement."
        lead["stage"] = "ready"
    return f"{msg}\n<LEAD_JSON>{json.dumps(lead, ensure_ascii=False)}</LEAD_JSON>"


# ==== Email lead (Mailjet) ====
def send_lead_email(to_email: str, lead: dict, bot_name: str = "Betty Bot"):
    if not (MJ_API_KEY and MJ_API_SECRET and to_email):
        print("[LEAD][MAILJET] Config manquante ou email vide, email non envoyé.")
        return
    subject = f"Nouveau lead qualifié via {bot_name}"
    text = (
        f"Motif        : {lead.get('reason','')}\n"
        f"Nom          : {lead.get('name','')}\n"
        f"Email        : {lead.get('email','')}\n"
        f"Téléphone    : {lead.get('phone','')}\n"
        f"Disponibilités : {lead.get('availability','')}\n"
        f"Statut       : {lead.get('stage','')}\n"
    )
    payload = {
        "Messages": [{
            "From": {"Email": MJ_FROM_EMAIL, "Name": MJ_FROM_NAME},
            "To": [{"Email": to_email}],
            "Subject": subject,
            "TextPart": text
        }]
    }
    try:
        r = requests.post(
            "https://api.mailjet.com/v3.1/send",
            auth=(MJ_API_KEY, MJ_API_SECRET),
            json=payload,
            timeout=15
        )
        print("[LEAD][MAILJET]", "OK" if r.ok else f"KO {r.status_code} {r.text[:200]}")
    except Exception as e:
        print("[LEAD][MAILJET][EXC]", type(e).__name__, e)


def send_purchase_email(to_email: str, bot: dict):
    if not (MJ_API_KEY and MJ_API_SECRET and to_email):
        print("[PURCHASE][MAILJET] Config manquante ou email vide, email non envoyé.")
        return

    public_id = bot.get("public_id") or ""
    pack_code = (bot.get("pack") or "bot").strip()
    pack_label = humanize_pack(pack_code)
    name = bot.get("name") or "Betty Bot"
    embed_url = f"{BASE_URL}/chat?public_id={public_id}&embed=1"

    iframe_snippet = (
        f'<iframe src="{embed_url}" title="{name}" '
        'style="width:100%;max-width:420px;height:620px;border:0;border-radius:16px;'
        'box-shadow:0 10px 30px rgba(0,0,0,.25);background:#0b0f1e;" '
        'loading="lazy" referrerpolicy="no-referrer-when-downgrade" '
        'allow="clipboard-read; clipboard-write; microphone; autoplay"></iframe>'
    )

    subject = f"Votre Betty ({pack_label}) est activée ✅"
    text = (
        "Bonjour,\n\n"
        "Merci pour votre inscription à Betty Bots.\n\n"
        "Voici le récapitulatif :\n"
        f"- Pack : {pack_label} ({pack_code})\n"
        f"- Nom du bot : {name}\n"
        f"- Code public : {public_id}\n"
        f"- Lien de test : {embed_url}\n\n"
        "Pour intégrer Betty sur votre site, copiez/collez ce code HTML :\n\n"
        f"{iframe_snippet}\n\n"
        "À très vite,\n"
        "Spectra Media AI\n"
    )

    payload = {
        "Messages": [{
            "From": {"Email": MJ_FROM_EMAIL, "Name": MJ_FROM_NAME},
            "To": [{"Email": to_email}],
            "Subject": subject,
            "TextPart": text
        }]
    }

    try:
        r = requests.post(
            "https://api.mailjet.com/v3.1/send",
            auth=(MJ_API_KEY, MJ_API_SECRET),
            json=payload,
            timeout=15
        )
        print("[PURCHASE][MAILJET]", "OK" if r.ok else f"KO {r.status_code} {r.text[:200]}")
    except Exception as e:
        print("[PURCHASE][MAILJET][EXC]", type(e).__name__, e)


# ==== Bots en mémoire ====
BOTS = {
    "avocat-001": {"pack": "avocat", "name": "Betty Bot (Avocat)", "color": "#4F46E5", "avatar_file": "avocat.jpg", "profile": {}, "greeting": "", "buyer_email": None, "owner_name": None, "public_id": None},
    "immo-002": {"pack": "immo", "name": "Betty Bot (Immobilier)", "color": "#16A34A", "avatar_file": "immo.jpg", "profile": {}, "greeting": "", "buyer_email": None, "owner_name": None, "public_id": None},
    "medecin-003": {"pack": "medecin", "name": "Betty Bot (Médecin)", "color": "#0284C7", "avatar_file": "medecin.jpg", "profile": {}, "greeting": "", "buyer_email": None, "owner_name": None, "public_id": None},

    "spectra-demo": {
        "pack": "demo",
        "name": "Betty Bot (Spectra Media)",
        "color": "#4F46E5",
        "avatar_file": "avocat.jpg",
        "profile": {},
        "greeting": (
            "Bonjour 👋\n"
            "Je suis Betty.\n\n"
            "Je peux répondre à vos questions, vous guider, et transmettre une demande.\n"            
            "Si vous voulez la même Betty sur votre site : cliquez sur “Je crée ma Betty maintenant”."
        ),
        "buyer_email": None,
        "owner_name": "Spectra Media",
        "public_id": "spectra-demo"
    },

    "betty-spectra-core": {
        "pack": "spectrabot",
        "name": "Betty",
        "color": "#4F46E5",
        "avatar_file": "betty_neutre_001.webp",  # <-- ton avatar dans /static
        "profile": {
            "name": "Spectra Media",
            "email": "contact@spectramedia.online",
            "job": "assistante commerciale et guide experte de Spectra Media"
        },
        "greeting": "Bonjour, je suis Betty, l'assistante de Spectra Media. Comment puis-je vous aider ?",
        "buyer_email": "contact@spectramedia.online",
        "owner_name": "Spectra Media",
        "public_id": "betty-spectra-core"
    },
}


def _gen_public_id(email: str, bot_key: str) -> str:
    h = hashlib.sha1((email + "|" + bot_key).encode()).hexdigest()[:8]
    return f"{bot_key}-{h}"


def find_bot_by_public_id(public_id: str):
    if not public_id:
        return None, None
    bot = db_get_bot(public_id)
    if bot:
        return bot.get("bot_key"), bot
    parts = public_id.split("-")
    if len(parts) < 3:
        for k, b in BOTS.items():
            if b.get("public_id") == public_id:
                b2 = dict(b)
                b2["bot_key"] = k
                b2["public_id"] = public_id
                return k, b2
        return None, None
    bot_key = "-".join(parts[:2])
    b = BOTS.get(bot_key)
    if not b:
        return None, None
    b2 = dict(b)
    b2["bot_key"] = bot_key
    b2["public_id"] = public_id
    return bot_key, b2


# ==== Mémoire conversations ====
CONVS = {}


# ==== Pages ====
@app.get("/api")
def health():
    return "OK Betty via /api"


@app.route("/")
def index():
    try:
        lang = request.args.get("lang", "fr")
        t = translations.get(lang, translations["fr"])

        return render_template("index.html", t=t, lang=lang)
        
    except TemplateNotFound:
        return "<!doctype html><meta charset='utf-8'><h1>Betty Bots</h1><p>templates/index.html manquant.</p>", 200


@app.route("/config", methods=["GET", "POST"])
def config_page():
    if request.method == "POST":
        pack = (request.form.get("pack", "avocat") or "avocat").strip().lower()
        color = request.form.get("color", "#4F46E5")
        avatar_in = (request.form.get("avatar", "") or "").strip()
        greeting = request.form.get("greeting", "")
        contact = request.form.get("contact_info", "")
        persona_x = request.form.get("persona_x", "0")
        persona_y = request.form.get("persona_y", "0")
        website_url = (request.form.get("website_url", "") or "").strip()

        
    
        avatar = avatar_for_pack(pack, avatar_hint=avatar_in, fallback="avocat.jpg")

        return redirect(url_for(
            "inscription_page",
            pack=pack, color=color, avatar=avatar,
            greeting=greeting, contact=contact,
            px=persona_x, py=persona_y,
            website_url=website_url

        ))
    try:
        return render_template("config.html", title="Configurer votre bot")
    except TemplateNotFound:
        return "<!doctype html><meta charset='utf-8'><h1>/config</h1><p>templates/config.html manquant.</p>", 200


@app.route("/inscription", methods=["GET", "POST"])
def inscription_page():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()
        if not email or "@" not in email:
            return "Email invalide.", 400

        def _pick(key: str, default: str = "") -> str:
            v = (request.form.get(key) or "").strip()
            if v:
                return v
            return (request.args.get(key, default) or default).strip()

        pack = _pick("pack", "avocat") or "avocat"
        color = _pick("color", "#4F46E5") or "#4F46E5"
        avatar = _pick("avatar", "") or ""
        greet = _pick("greeting", "") or ""
        contact = _pick("contact", "") or ""
        px = _pick("px", "0") or "0"
        py = _pick("py", "0") or "0"
        website_url = _pick("website_url", "") or ""

        if not stripe.api_key or not PRICE_ID:
            app.logger.error("[STRIPE] STRIPE_SECRET_KEY/STRIPE_PRICE_ID manquant")
            return "Paiement indisponible (Stripe non configuré).", 500

        bot_key = botkey_for_pack(pack)
        public_id = _gen_public_id(email, bot_key)
        avatar_final = avatar_for_pack(pack, avatar_hint=avatar, fallback="avocat.jpg")

        try:
            cancel_params = {"pack": pack, "color": color, "avatar": avatar_final, "greeting": greet}
            cancel_url = f"{BASE_URL}/inscription?{urlencode(cancel_params)}"
            print("PUBLIC ID AVANT STRIPE:", public_id)
            session_obj = stripe.checkout.Session.create(
                mode="subscription",
                line_items=[{"price": PRICE_ID, "quantity": 1}],
                customer_email=email,
                subscription_data={"trial_period_days": TRIAL_DAYS},
                success_url=f"{BASE_URL}/recap?public_id={public_id}",
                cancel_url=cancel_url,
                metadata={
                    "public_id": public_id,
                    "bot_key": bot_key,
                    "pack": pack,
                    "color": color,
                    "avatar": avatar_final,
                    "greeting": greet,
                    "contact_info": contact,
                    "persona_x": px,
                    "persona_y": py,
                    "website_url": website_url,
                },
            )
            return redirect(session_obj.url, code=303)

        except Exception as e:
            app.logger.exception(f"[STRIPE] checkout session create failed: {e}")
            return "Erreur Stripe. Merci de réessayer.", 502

    cfg = {
        "pack": (request.args.get("pack", "avocat") or "avocat").strip(),
        "color": (request.args.get("color", "#4F46E5") or "#4F46E5").strip(),
        "avatar": (request.args.get("avatar", "") or "").strip(),
        "greeting": request.args.get("greeting", "") or "",
        "contact": request.args.get("contact", "") or "",
        "px": request.args.get("px", "0") or "0",
        "py": request.args.get("py", "0") or "0",
    }
    return render_template("inscription.html", title="Inscription", cfg=cfg)


# ✅ NEW: Stripe Customer Portal
@app.route("/billing_portal")
def billing_portal():
    public_id = (request.args.get("public_id") or "").strip()
    if not public_id:
        return "public_id manquant.", 400

    bot = db_get_bot(public_id)
    if not bot:
        return "Bot introuvable.", 404

    customer_id = (bot.get("stripe_customer_id") or "").strip()
    if not customer_id:
        return "Customer Stripe manquant (attends le webhook).", 400

    if not stripe.api_key:
        return "Stripe non configuré.", 500

    return_url = STRIPE_PORTAL_RETURN_URL.strip() if STRIPE_PORTAL_RETURN_URL else f"{BASE_URL}/recap?public_id={public_id}"

    try:
        portal = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url
        )
        return redirect(portal.url, code=303)
    except Exception as e:
        app.logger.exception(f"[STRIPE] billing portal failed: {e}")
        return "Erreur portail Stripe.", 502


@app.route("/recap")
def recap_page():
    public_id = (request.args.get("public_id") or "").strip()
    if not public_id:
        return "public_id manquant.", 400

    bot = db_get_bot(public_id)
    if not bot:
        return render_template("pending.html", title="Activation en cours"), 200

    bot = enforce_block_if_needed(public_id, bot)

    pack_code = (bot.get("pack") or "avocat").strip().lower()
    pack_label = humanize_pack(pack_code)

    display_name = bot.get("name") or "Betty Bot"
    owner = bot.get("owner_name") or ""
    full_name = f"{display_name} — {owner}" if owner else display_name

    avatar_file = avatar_for_pack(pack_code, avatar_hint=(bot.get("avatar_file") or ""), fallback="avocat.jpg")

    params = {"public_id": bot.get("public_id"), "embed": "1"}
    buyer = (bot.get("buyer_email") or "").strip()
    if buyer:
        params["buyer_email"] = buyer
    embed_url = f"{BASE_URL}/chat?{urlencode(params)}"

    iframe_snippet = (
        '<div style="position:relative;width:100%;max-width:420px;height:620px;margin:0 auto;">\n'
        f'  <iframe src="{embed_url}" title="{full_name}" '
        'style="width:100%;height:100%;border:0;border-radius:16px;'
        'box-shadow:0 10px 30px rgba(0,0,0,.25);background:#0b0f1e;" '
        'loading="lazy" referrerpolicy="no-referrer-when-downgrade" '
        'allow="clipboard-read; clipboard-write; microphone; autoplay"></iframe>\n'
        '</div>'
    )

    access_label, access_css = compute_access_state(bot)

    days_left = None
    trial_end = bot.get("trial_end")
    if trial_end:
        delta = int(trial_end) - _now_ts()
        if delta > 0:
            days_left = max(0, int(delta // 86400))

    cfg = {
        "pack": pack_code,
        "pack_label": pack_label,
        "color": bot.get("color") or "#4F46E5",
        "greeting": bot.get("greeting") or "Bonjour, qu’est-ce que je peux faire pour vous ?",
        "contact": (bot.get("profile") or {}).get("raw") or "",
        "px": request.args.get("px") if request.args.get("px") is not None else "0.5",
        "py": request.args.get("py") if request.args.get("py") is not None else "0.5",
        "avatar_url": static_url(avatar_file),
        "public_id": bot.get("public_id") or "",
        "buyer_email": bot.get("buyer_email") or "",
        "display_name": display_name,
        "owner_name": owner,
        "full_name": full_name,
        "embed_url": embed_url,
        "iframe_snippet": iframe_snippet,
        "access_label": access_label,
        "access_css": access_css,
        "trial_days_left": days_left,
        "billing_portal_url": f"{BASE_URL}/billing_portal?public_id={public_id}",
    }

    try:
        return render_template(
            "recap.html",
            title="Récapitulatif",
            cfg=cfg,
            info=cfg,
            base_url=BASE_URL,
            full_name=full_name
        )
    except TemplateNotFound:
        return f"<!doctype html><meta charset='utf-8'><pre>{json.dumps(cfg, ensure_ascii=False, indent=2)}</pre>", 200


@app.route("/chat")
def chat_page():
    public_id = (request.args.get("public_id") or "").strip()
    embed = request.args.get("embed", "0") == "1"
    buyer_email = (request.args.get("buyer_email") or "").strip()

    bot_key, bot = find_bot_by_public_id(public_id)

    if bot:
        bot = enforce_block_if_needed(public_id, bot)

    if not bot:
        bot_key = bot_key or "avocat-001"
        base = BOTS.get(bot_key, BOTS["avocat-001"])
        bot = {
            "public_id": public_id or f"{bot_key}-demo",
            "name": base["name"],
            "color": base["color"],
            "avatar_file": base["avatar_file"],
            "greeting": "Bonjour, qu’est-ce que je peux faire pour vous ?",
            "owner_name": "Client",
            "profile": {},
            "pack": base["pack"],
        }

    display_name = (bot.get("name") or "Betty Bot").strip()
    display_name = re.sub(r"\s*\([^)]*\)\s*$", "", display_name).strip()
    full_name = display_name
    print("[DEBUG PACK]", bot.get("pack"), bot.get("avatar_file"))
    pack_code = (bot.get("pack") or "").lower()
    avatar_file = avatar_for_pack(pack_code, avatar_hint=(bot.get("avatar_file") or ""), fallback="avocat.jpg")

    try:
        return render_template(
            "chat.html",
            title="Betty — Chat",
            base_url=BASE_URL,
            public_id=bot.get("public_id") or "",
            full_name=full_name,
            header_title="Betty Bot, votre assistante AI",
            color=bot.get("color") or "#4F46E5",
            avatar_url=static_url(avatar_file),
            greeting=bot.get("greeting") or "Bonjour, qu’est-ce que je peux faire pour vous ?",
            buyer_email=buyer_email,
            embed=embed
        )
    except TemplateNotFound:
        return "<!doctype html><meta charset='utf-8'><h1>Chat</h1><p>Template manquant.</p>", 200


@app.route("/api/bettybot", methods=["POST"])
def bettybot_reply():
    print("🔥 ROUTE BETTYBOT APPELÉE")
    payload = request.get_json(force=True, silent=True) or {}
    user_input = (payload.get("message") or "").strip()

    lower = user_input.lower()

    # ⚡ réponses instantanées (pas de LLM)
    if lower in ["bonjour", "salut", "hello", "coucou"]:
        return jsonify({
            "response": "Bonjour 🙂 Comment puis-je vous aider ?",
            "stage": "instant"
        })

    public_id = (payload.get("bot_id") or payload.get("public_id") or "").strip()
    conv_id = (payload.get("conv_id") or "").strip()

    if not user_input:
        return jsonify({"response": "Dites-moi ce dont vous avez besoin 🙂"}), 200
        
    public_id = (payload.get("bot_id") or payload.get("public_id") or "").strip()
    conv_id = (payload.get("conv_id") or "").strip()

    if not user_input:
        return jsonify({"response": "Dites-moi ce dont vous avez besoin 🙂"}), 200

    bot_key, bot = find_bot_by_public_id(public_id)
    if not bot:
        bot_key = "avocat-001"
        bot = BOTS[bot_key]
    is_demo = (public_id == "spectra-demo")
    if public_id and isinstance(bot, dict):
        bot = enforce_block_if_needed(public_id, bot)
        access_label, _ = compute_access_state(bot)
        if access_label == "BLOQUÉ":
            msg = (
                "Votre essai est terminé. Pour réactiver Betty, merci de régulariser l’abonnement "
                "via le lien de gestion. Souhaitez-vous que je vous le renvoie ?"
            )
            return jsonify({"response": msg, "stage": "blocked"}), 200

    if conv_id:
        history = CONVS.get(conv_id, [])
    else:
        key = f"conv_{public_id or bot_key}"
        history = session.get(key, [])
    history = history[-8:]

    demo_mode = (public_id == "spectra-demo")
    demo_session_id = None
    if demo_mode:
        demo_session_id = get_or_create_demo_session_id(payload)
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        ua = request.headers.get("User-Agent", "")
        db_demo_upsert_session(demo_session_id, public_id or "spectra-demo", ip, ua)
        db_demo_add_message(demo_session_id, "user", user_input)

    
    else:
        profile = bot.get("profile", {}) or {}
        system_prompt = build_system_prompt_for_bot(
            pack=bot.get("pack"),
            profile=bot.get("profile", {}),
            greeting=bot.get("greeting", "")
        )

    site_context = ""
    if public_id:
        try:
            results = search_site(public_id, user_input)
            for hit in results.get("hits", []):
                site_context += f"\nPAGE: {hit.get('title','')}\n{hit.get('snippet','')}\n"
        except Exception as e:
            app.logger.warning(f"[SITE SEARCH ERROR] {e}")

    if site_context:
        system_prompt += "\n\nInformations extraites du site du client :\n" + site_context

    llm_text = "TEST OK"
    
    if not llm_text:
        if demo_mode:
            llm_text = "Je vois 🙂 Vous cherchez un bot pour votre activité. Pouvez-vous me dire votre métier ou votre besoin principal ?"
        else:
            llm_text = rule_based_next_question(
                bot.get("pack", ""),
                history + [{"role": "user", "content": user_input}]
            )

    if demo_mode:
        response_text, _ = extract_lead_json(llm_text or "")
        response_text = enforce_single_question((response_text or "").strip())

        if demo_session_id:
            db_demo_add_message(demo_session_id, "assistant", response_text)

        augmented_history = history + [{"role": "user", "content": user_input}]
        lead = _lead_from_history(augmented_history)
        stage = lead.get("stage", "collecting")
        should_send_now = False
        effective_stage = stage
        have_any_info = any([
            isinstance(lead, dict) and lead.get("name"),
            isinstance(lead, dict) and lead.get("email"),
            isinstance(lead, dict) and lead.get("phone"),
            isinstance(lead, dict) and lead.get("reason"),
        ])
    else:
        try:
            response_text, lead, should_send_now, stage = guardrailed_reply(
                history,
                user_input,
                llm_text,
                bot.get("pack", "")
            )
        except Exception as e:
            print("🔥 guardrailed_reply CRASH:", str(e))
        
            response_text = "Bonjour 🙂 Comment puis-je vous aider ?"
            lead = {}
            should_send_now = False
            stage = "collecting"
            
        lead = lead or {}
        
        have_any_info = any([
            isinstance(lead, dict) and lead.get("name"),
            isinstance(lead, dict) and lead.get("email"),
            isinstance(lead, dict) and lead.get("phone"),
            isinstance(lead, dict) and lead.get("reason"),
        ])
        effective_stage = (
            "ready" if (lead.get("phone") and lead.get("name") and lead.get("email"))
            else lead.get("stage", "collecting")
        )
        # 🔒 UNIQUEMENT POUR LA DEMO (INDEX)
        is_demo_bot = (public_id == "spectra-demo")

        if is_demo and lead.get("name") and lead.get("phone") and lead.get("email"):
            name = lead.get("name", "")

            response_text = f"""Parfait {name} 👍  

        👉 Activez votre Betty ici :  
        /inscription  

        7 jours gratuits. Sans engagement."""

            history.append({"role": "assistant", "content": response_text})

            if conv_id:
                CONVS[conv_id] = history
            else:
                session[f"conv_{public_id or bot_key}"] = history

            return jsonify({
                "response": response_text,
                "stage": "redirect"
            
            })
              
    history.append({"role": "user", "content": user_input})
    history.append({"role": "assistant", "content": response_text})

    if conv_id:
        CONVS[conv_id] = history
    else:
        session[f"conv_{public_id or bot_key}"] = history

    default_fallback = os.getenv("DEFAULT_LEAD_EMAIL", "").strip() or MJ_FROM_EMAIL
    if demo_mode:
        buyer_email_ctx = (DEMO_LEAD_EMAIL or default_fallback)
    else:
        buyer_email_ctx = (
            (payload.get("buyer_email") or "").strip()
            or ((db_get_bot(public_id) or {}).get("buyer_email") if public_id else "")
            or (bot or {}).get("buyer_email")
            or default_fallback
        )

    may_send = (
        (effective_stage == "ready")
        or (not demo_mode and should_send_now and have_any_info)
        or (demo_mode and effective_stage == "ready")
    )

    if may_send and isinstance(lead, dict) and buyer_email_ctx:
        try:
            send_lead_email(
                to_email=buyer_email_ctx,
                lead={
                    "reason": lead.get("reason", ""),
                    "name": lead.get("name", ""),
                    "email": lead.get("email", ""),
                    "phone": lead.get("phone", ""),
                    "availability": lead.get("availability", ""),
                    "stage": effective_stage or "ready",
                },
                bot_name=(bot or {}).get("name") or ("Betty Bot (Démo)" if demo_mode else "Betty Bot"),
            )
        except Exception as e:
            app.logger.exception(f"[LEAD] Erreur envoi email -> {e}")
        # 🔒 Bloquer lien si hors contexte bot
        lower = user_input.lower()

        intent_keywords = [
            "bot", "assistant", "ia", "chatbot",
            "automatiser", "client", "clients",
            "lead", "leads", "site", "business",
            "entreprise"
        ]

        has_intent = any(word in lower for word in intent_keywords)

        # ❌ Si PAS d’intention → on bloque le flow commercial
        if not has_intent:
            response_text = "Je comprends 🙂 Pouvez-vous préciser votre besoin ?"
        return jsonify({
            "response": response_text,
            "stage": effective_stage,
            "conv_id": demo_session_id or conv_id
        })

@app.route("/api/embed_meta")
def embed_meta():
    public_id = (request.args.get("public_id") or "").strip()
    if not public_id:
        return jsonify({"error": "missing public_id"}), 400
    _, bot = find_bot_by_public_id(public_id)
    if not bot:
        return jsonify({"error": "bot_not_found"}), 404

    pack_code = (bot.get("pack") or "avocat").strip().lower()
    avatar_file = avatar_for_pack(pack_code, avatar_hint=(bot.get("avatar_file") or ""), fallback="avocat.jpg")

    return jsonify({
        "bot_id": public_id,
        "owner_name": bot.get("owner_name") or "Client",
        "display_name": bot.get("name") or "Betty Bot",
        "color_hex": bot.get("color") or "#4F46E5",
        "avatar_url": static_url(avatar_file),
        "greeting": bot.get("greeting") or "Bonjour, qu’est-ce que je peux faire pour vous ?"
    })


@app.route("/api/bot_meta")
def bot_meta():
    bot_id = (request.args.get("bot_id") or request.args.get("public_id") or "").strip()
    if bot_id == "spectra-demo":
        b = BOTS["spectra-demo"]
        return jsonify({
            "name": "Betty Bot (Spectra Media)",
            "color_hex": b.get("color") or "#4F46E5",
            "avatar_url": static_url(b.get("avatar_file") or "avocat.jpg"),
            "greeting": b.get("greeting") or "Bonjour et bienvenue chez Spectra Media. Souhaitez-vous créer votre Betty Bot métier ?"
        })
    if bot_id in BOTS:
        b = BOTS[bot_id]
        demo_greetings = {
            "avocat-001": "Bonjour et bienvenue au cabinet Werner & Werner. Que puis-je faire pour vous ?",
            "immo-002": "Bonjour et bienvenue à l’agence Werner Immobilier. Comment puis-je vous aider ?",
            "medecin-003": "Bonjour et bienvenue au cabinet Werner Santé. Que puis-je faire pour vous ?",
        }
        return jsonify({
            "name": b.get("name") or "Betty Bot",
            "color_hex": b.get("color") or "#4F46E5",
            "avatar_url": static_url(b.get("avatar_file") or "avocat.jpg"),
            "greeting": demo_greetings.get(bot_id, "Bonjour, qu’est-ce que je peux faire pour vous ?")
        })
    _, bot = find_bot_by_public_id(bot_id)
    if not bot:
        return jsonify({"error": "bot_not_found"}), 404

    pack_code = (bot.get("pack") or "avocat").strip().lower()
    avatar_file = avatar_for_pack(pack_code, avatar_hint=(bot.get("avatar_file") or ""), fallback="avocat.jpg")

    return jsonify({
        "name": bot.get("name") or "Betty Bot",
        "color_hex": bot.get("color") or "#4F46E5",
        "avatar_url": static_url(avatar_file),
        "greeting": bot.get("greeting") or "Bonjour, qu’est-ce que je peux faire pour vous ?"
    })


@app.route("/healthz")
def healthz():
    return "ok", 200


@app.route("/api/reset", methods=["POST"])
def reset_conv():
    key = (request.get_json(silent=True) or {}).get("key")
    if key and key in CONVS:
        CONVS.pop(key, None)
    return jsonify({"ok": True})


@app.route("/api/test_mailjet")
def test_mailjet():
    to = (request.args.get("to") or os.getenv("TEST_TO_EMAIL") or "").strip()
    if not to:
        return jsonify({"ok": False, "error": "missing 'to' param"}), 400
    lead = {
        "reason": "Test automatique",
        "name": "Lead Test",
        "email": "lead@example.com",
        "phone": "+33000000000",
        "availability": "demain 10h",
        "stage": "ready",
    }
    send_lead_email(to, lead, bot_name="Betty Bot (test)")
    return jsonify({"ok": True, "to": to})


@app.route("/avatar/<slug>")
def avatar(slug: str):
    static_dir = os.path.join(app.root_path, "static")
    filename = f"logo-{slug}.jpg"
    path = os.path.join(static_dir, filename)
    if os.path.exists(path):
        return send_from_directory(static_dir, filename)
    transparent_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+Xad8AAAAASUVORK5CYII="
    )
    return Response(transparent_png, mimetype="image/png")


# =========================
# /stripe_webhook (PRO)
# - SEUL endroit où on crée le bot (checkout.session.completed)
# - paid=1 UNIQUEMENT sur invoice.paid
# =========================

@app.route("/stripe_webhook", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature")

    if not STRIPE_WEBHOOK_SECRET:
        app.logger.error("[STRIPE][WEBHOOK] STRIPE_WEBHOOK_SECRET manquant")
        return jsonify({"error": "webhook secret not configured"}), 500

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except ValueError as e:
        app.logger.error(f"[STRIPE][WEBHOOK] Payload invalide : {e}")
        return jsonify({"error": "invalid payload"}), 400
    except stripe.error.SignatureVerificationError as e:
        app.logger.error(f"[STRIPE][WEBHOOK] Signature invalide : {e}")
        return jsonify({"error": "invalid signature"}), 400

    event_type = event.get("type")
    app.logger.info(f"[STRIPE][WEBHOOK] Event reçu : {event_type}")

    # =========================================================
    # 1) CRÉATION / ACTIVATION INITIALE DU BOT
    #    Compatible STARTER + BETTY MÉTIER
    # =========================================================
    if event_type == "checkout.session.completed":
        session_obj = event["data"]["object"]
        meta = session_obj.get("metadata") or {}

        public_id = (
            meta.get("public_id")
            or session_obj.get("client_reference_id")
            or ""
        ).strip()

        buyer = (
            (session_obj.get("customer_email") or "").strip()
            or ((session_obj.get("customer_details") or {}).get("email") or "").strip()
        )

        # Détection starter vs métier
        is_starter = public_id.startswith("starter-")

        # STARTER : fallback neutre si metadata absente/incomplète
        # MÉTIER  : on garde les vraies valeurs configurées
        bot_key = (
            (meta.get("bot_key") or "").strip()
            or ("betty-spectra-core" if is_starter else "")
        )

        pack = (
            (meta.get("pack") or "").strip()
            or ("spectrabot" if is_starter else "avocat")
        )

        color = (
            (meta.get("color") or "").strip()
            or ("#4F46E5" if is_starter else "")
        )

        avatar = (
            (meta.get("avatar") or "").strip()
            or ("betty_neutre_001.webp" if is_starter else "")
        )

        greet = (
            meta.get("greeting")
            or (BOTS["betty-spectra-core"]["greeting"] if is_starter else "")
        )

        contact = meta.get("contact_info") or ""

        print("WEBHOOK CHECK:", {
            "public_id": public_id,
            "bot_key": bot_key,
            "pack": pack,
            "buyer": buyer,
            "is_starter": is_starter,
        })

        if not public_id or not bot_key or not buyer:
            app.logger.error(
                f"[STRIPE][WEBHOOK] metadata incomplet "
                f"(public_id={public_id!r}, bot_key={bot_key!r}, buyer={buyer!r})"
            )
            return jsonify({"received": True}), 200

        profile = parse_contact_info(contact)
        avatar_final = avatar_for_pack(pack, avatar_hint=avatar, fallback="avocat.jpg")

        existing = db_get_bot(public_id)
        _, already_sent = db_get_flags(public_id)

        pack_label = humanize_pack(pack)
        bot_name = f"Betty Bot ({pack_label})"

        stripe_customer_id = session_obj.get("customer")
        stripe_subscription_id = session_obj.get("subscription")

        created_at = _now_ts()
        trial_end = None
        stripe_status = "trialing"

        try:
            if stripe_subscription_id and stripe.api_key:
                sub = stripe.Subscription.retrieve(stripe_subscription_id)
                stripe_status = (sub.get("status") or stripe_status)
                trial_end = sub.get("trial_end")
        except Exception as e:
            app.logger.warning(
                f"[STRIPE] sub retrieve failed (ok fallback): {type(e).__name__}: {e}"
            )

        if trial_end is None:
            trial_end = created_at + int(TRIAL_DAYS) * 86400

        bot_db = {
            "public_id": public_id,
            "bot_key": bot_key,
            "pack": pack,
            "name": bot_name,
            "color": color or "#4F46E5",
            "avatar_file": avatar_final,
            "greeting": greet or "",
            "buyer_email": buyer,
            "owner_name": buyer.split("@")[0].title(),
            "profile": profile,

            "paid": int((existing or {}).get("paid") or 0),
            "purchase_email_sent": int((existing or {}).get("purchase_email_sent") or 0),

            "stripe_customer_id": stripe_customer_id,
            "stripe_subscription_id": stripe_subscription_id,
            "stripe_status": stripe_status or "trialing",

            "created_at": (existing or {}).get("created_at") or created_at,
            "trial_end": (existing or {}).get("trial_end") or trial_end,
            "blocked": int((existing or {}).get("blocked") or 0),
        }

        db_upsert_bot(bot_db)

        site_url = meta.get("website_url")
        if site_url:
            try:
                app.logger.info(f"[CRAWL] Lancement crawl pour {site_url}")
                data = crawl_site(site_url)
                save_crawled_site(public_id, site_url, data)
            except Exception as e:
                app.logger.warning(f"[CRAWL ERROR] {e}")

        if already_sent == 0:
            try:
                send_purchase_email(to_email=buyer, bot=bot_db)
                db_mark_purchase_email_sent(public_id)
                app.logger.info(f"[PURCHASE] email envoyé à {buyer} (public_id={public_id})")
            except Exception as e:
                app.logger.exception(f"[PURCHASE] webhook send mail failed: {e}")

    # =========================================================
    # 2) PAIEMENT CONFIRMÉ APRÈS ESSAI / FACTURATION
    # =========================================================
    elif event_type == "invoice.paid":
        inv = event["data"]["object"]
        sub_id = inv.get("subscription")
        bot = db_get_bot_by_subscription_id(sub_id)
        if bot:
            public_id = bot["public_id"]
            db_set_paid_status(public_id, paid=1, stripe_status="active", blocked=0)
            app.logger.info(f"[STRIPE] invoice.paid => paid=1 (public_id={public_id})")

    elif event_type == "invoice.payment_failed":
        inv = event["data"]["object"]
        sub_id = inv.get("subscription")
        bot = db_get_bot_by_subscription_id(sub_id)
        if bot:
            public_id = bot["public_id"]
            db_set_paid_status(public_id, paid=0, stripe_status="past_due")
            app.logger.info(f"[STRIPE] invoice.payment_failed => past_due (public_id={public_id})")

    elif event_type == "customer.subscription.deleted":
        sub = event["data"]["object"]
        sub_id = sub.get("id")
        bot = db_get_bot_by_subscription_id(sub_id)
        if bot:
            public_id = bot["public_id"]
            db_set_paid_status(public_id, paid=0, stripe_status="canceled", blocked=1)
            app.logger.info(f"[STRIPE] subscription.deleted => blocked (public_id={public_id})")

    elif event_type == "customer.subscription.updated":
        sub = event["data"]["object"]
        sub_id = sub.get("id")
        bot = db_get_bot_by_subscription_id(sub_id)
        if bot:
            public_id = bot["public_id"]
            st = (sub.get("status") or "").strip() or None
            trial_end = sub.get("trial_end")

            if trial_end:
                db_set_trial(public_id, trial_end=int(trial_end))

            if st:
                with db_connect() as con:
                    cur = con.cursor()
                    cur.execute(
                        "UPDATE bots SET stripe_status=%s WHERE public_id=%s",
                        (st, public_id)
                    )
                    con.commit()

    return jsonify({"received": True}), 200
# =========================
# Dashboard admin (liste des bots) — Postgres safe + Démo sessions
# =========================
@app.route("/admin/bots")
def admin_bots():
    # 0) Sécurité: s'assurer que les tables démo existent (si tu as cette fonction)
    try:
        ensure_demo_tables()
    except Exception as e:
        # On ne bloque pas le dashboard si la démo n'est pas prête
        print("[admin_bots] ensure_demo_tables() failed:", e)

    # 1) Charger les bots
    with db_connect() as con:
        cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT public_id, name, buyer_email, paid, stripe_status, blocked, trial_end, created_at
            FROM bots
            ORDER BY created_at DESC NULLS LAST
        """)
        rows = cur.fetchall() or []

    bots = []
    for r in rows:
        b = {
            "public_id": r.get("public_id"),
            "name": (r.get("name") or "Betty Bot"),
            "buyer_email": (r.get("buyer_email") or ""),
            "paid": bool(r.get("paid")),
            "stripe_status": (r.get("stripe_status") or "").strip() or "unknown",
            "blocked": bool(r.get("blocked")),
            "trial_end": r.get("trial_end"),
        }

        # Si tu as déjà compute_access_state(b)
        try:
            label, css = compute_access_state(b)
        except Exception:
            label, css = ("—", "unknown")

        b["access_label"] = label
        b["access_css"] = css
        bots.append(b)

    # 2) Charger sessions démo + messages
    demo_sessions = []
    demo_conversations = []

    try:
        demo_sessions = db_demo_list_sessions(limit=200) or []
    except Exception as e:
        print("[admin_bots] db_demo_list_sessions failed:", e)
        demo_sessions = []

    for s in demo_sessions:
        sid = s.get("id")
        msgs = []
        if sid:
            try:
                msgs = db_demo_get_messages(sid) or []
            except Exception as e:
                print("[admin_bots] db_demo_get_messages failed:", e)
                msgs = []

        # guess_contact_from_messages doit retourner un dict, ex {"who": "..."}
        who = "—"
        try:
            who = (guess_contact_from_messages(msgs) or {}).get("who", "—")
        except Exception:
            who = "—"

        demo_conversations.append({
            "id": sid,
            "bot_id": s.get("bot_id") or "spectra-demo",
            "ip": s.get("ip") or "—",
            "user_agent": s.get("user_agent") or "—",
            "started_at": s.get("started_at"),
            "last_seen_at": s.get("last_seen_at"),
            "who": who,
            "messages": msgs,
        })

    # 3) Rendu template
    return render_template(
        "admin_bots.html",
        bots=bots,
        demo_conversations=demo_conversations,
        BASE_URL=BASE_URL,   # assure-toi que BASE_URL existe en global/config
    )



@app.route("/admin/init_betty_spectra")
def init_betty_spectra():
    public_id = "betty-spectra-core"
    site_url = "https://www.spectramedia.online"

    bot = {
        "public_id": public_id,
        "bot_key": "betty-spectra-core",
        "pack": "kine",
        "name": "Betty",
        "color": "#4F46E5",
        "avatar_file": "neutre_femme.webp",
        "greeting": "Bonjour, je suis Betty. Je suis la pour vous aider à trouver votre assisant idéal.",
        "buyer_email": "contact@spectramedia.online",
        "owner_name": "Spectra Media",
        "profile": {
            "name": "Spectra Media",
            "email": "contact@spectramedia.online",
            "job": "kine"
        },
        "paid": 1,
        "purchase_email_sent": 1,
        "stripe_customer_id": None,
        "stripe_subscription_id": None,
        "stripe_status": "active",
        "created_at": _now_ts(),
        "trial_end": _now_ts() + 3650 * 86400,
        "blocked": 0,
    }

    db_upsert_bot(bot)

    try:
        data = crawl_site(site_url)
        save_crawled_site(public_id, site_url, data)
    except Exception as e:
        app.logger.warning(f"[INIT BETTY SPECTRA][CRAWL ERROR] {e}")

    return jsonify({
        "ok": True,
        "public_id": public_id,
        "chat_url": f"{BASE_URL}/chat?public_id={public_id}&embed=1"
    })
# app.py (vers la fin, avant /stripe_webhook)

@app.route('/sitemap.xml')
def sitemap():
    return send_from_directory('static', 'sitemap.xml')
    
@app.route("/betty")
def betty_page():
    return render_template("betty.html")
    
@app.route('/chatbot-site-web')
def chatbot_site_web():
    return render_template('chatbot-site-web.html')

@app.route("/checkout_starter")
def checkout_starter():
    # Vérifie que la clé Stripe et l'ID du prix Starter sont présents
    price_id = os.getenv("STRIPE_PRICE_ID_STARTER", "").strip()
    if not stripe.api_key or not price_id:
        return "Paiement indisponible (Stripe non configuré).", 500

    # Génère un identifiant public aléatoire pour le bot Starter
    public_id = f"starter-{uuid.uuid4().hex[:8]}"

    # Prépare la session de paiement
    print("PUBLIC ID AVANT STRIPE:", public_id)

    session_obj = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        subscription_data={"trial_period_days": TRIAL_DAYS},

        success_url=f"{BASE_URL}/recap?public_id={public_id}",
        cancel_url=f"{BASE_URL}",

        client_reference_id=public_id,  # 🔥 AJOUT CRITIQUE

        metadata={
            "public_id": public_id,
            "bot_key": "betty-spectra-core",
            "pack": "spectrabot",
            "color": "#4F46E5",
            "avatar": "betty_neutre_001.webp",
            "greeting": BOTS["betty-spectra-core"]["greeting"],
            "contact_info": "",
            "persona_x": "0",
            "persona_y": "0",
            "website_url": "",
        },
    )
    return redirect(session_obj.url, code=303)
# ==== Main ====
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
