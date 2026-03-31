from __future__ import annotations

# =========================================================
# app.py — Betty Bots SaaS Platform
# Clean production version — single bot flow, no demo logic
# =========================================================

# Standard library
import os
import re
import sys
import json
import time
import base64
import hashlib
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urlencode

# Third-party
import psycopg2
import psycopg2.extras
import requests
import stripe
import yaml
from flask import (
    Flask, render_template, request, jsonify, redirect,
    url_for, session, send_from_directory, Response
)
from jinja2 import TemplateNotFound

# Local
from translations import translations
from site_crawler import crawl_site
from site_store import (
    ensure_tables as ensure_site_tables,
    save_crawled_site,
    search_robot_site,
)
from retrieval import search_site
from shopify_bridge import shopify_bp

# =========================================================
# GLOBAL ERROR HOOK
# =========================================================
sys.excepthook = lambda t, v, tb: traceback.print_exception(t, v, tb)

# =========================================================
# FLASK APP
# =========================================================
app = Flask(__name__)
app.register_blueprint(shopify_bp)

# =========================================================
# CONFIGURATION
# =========================================================

def _normalize_db_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url

# Security
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
SESSION_SECURE = os.getenv("SESSION_SECURE", "true").lower() == "true"
app.config.update(
    SESSION_COOKIE_SAMESITE="None",
    SESSION_COOKIE_SECURE=SESSION_SECURE,
)

# LLM
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY", "").strip()
TOGETHER_API_URL = "https://api.together.xyz/v1/chat/completions"
LLM_MODEL        = os.getenv("LLM_MODEL", "meta-llama/Meta-Llama-3-8B-Instruct-Turbo").strip()
LLM_MAX_TOKENS   = int(os.getenv("LLM_MAX_TOKENS", "200"))

# Stripe
stripe.api_key          = os.getenv("STRIPE_SECRET_KEY", "").strip()
PRICE_ID                = os.getenv("STRIPE_PRICE_ID", "").strip()
STRIPE_WEBHOOK_SECRET   = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
TRIAL_DAYS              = int(os.getenv("STRIPE_TRIAL_DAYS", "7"))
TRIAL_BLOCK_DAYS        = int(os.getenv("TRIAL_BLOCK_DAYS", str(TRIAL_DAYS)))
TRIAL_GRACE_HOURS       = int(os.getenv("TRIAL_GRACE_HOURS", "2"))
STRIPE_PORTAL_RETURN_URL = os.getenv("STRIPE_PORTAL_RETURN_URL", "").strip()

# Email (Mailjet)
MJ_API_KEY    = os.getenv("MJ_API_KEY", "").strip()
MJ_API_SECRET = os.getenv("MJ_API_SECRET", "").strip()
MJ_FROM_EMAIL = os.getenv("MJ_FROM_EMAIL", "no-reply@spectramedia.online").strip()
MJ_FROM_NAME  = os.getenv("MJ_FROM_NAME", "Betty Bots").strip()

# App
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:5000").rstrip("/")
DATABASE_URL = _normalize_db_url(os.getenv("DATABASE_URL", ""))
DB_SSLMODE   = os.getenv("DB_SSLMODE", "").strip()

app.jinja_env.globals["BASE_URL"] = BASE_URL

# =========================================================
# DATABASE
# =========================================================

@contextmanager
def db_connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL manquant.")
    kwargs = {"dsn": DATABASE_URL, "cursor_factory": psycopg2.extras.RealDictCursor}
    if DB_SSLMODE:
        kwargs["sslmode"] = DB_SSLMODE
    con = psycopg2.connect(**kwargs)
    try:
        yield con
    finally:
        con.close()


def db_init():
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS bots (
            public_id               TEXT PRIMARY KEY,
            bot_key                 TEXT NOT NULL,
            pack                    TEXT NOT NULL,
            name                    TEXT,
            color                   TEXT,
            avatar_file             TEXT,
            greeting                TEXT,
            buyer_email             TEXT,
            owner_name              TEXT,
            profile_json            TEXT,
            paid                    INTEGER DEFAULT 0,
            purchase_email_sent     INTEGER DEFAULT 0,
            stripe_customer_id      TEXT,
            stripe_subscription_id  TEXT,
            stripe_status           TEXT,
            created_at              BIGINT,
            trial_end               BIGINT,
            blocked                 INTEGER DEFAULT 0
        );
        """)
        con.commit()


def db_migrate():
    with db_connect() as con:
        cur = con.cursor()
        for col_sql in [
            "ALTER TABLE bots ADD COLUMN IF NOT EXISTS paid INTEGER DEFAULT 0;",
            "ALTER TABLE bots ADD COLUMN IF NOT EXISTS purchase_email_sent INTEGER DEFAULT 0;",
            "ALTER TABLE bots ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT;",
            "ALTER TABLE bots ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT;",
            "ALTER TABLE bots ADD COLUMN IF NOT EXISTS stripe_status TEXT;",
            "ALTER TABLE bots ADD COLUMN IF NOT EXISTS created_at BIGINT;",
            "ALTER TABLE bots ADD COLUMN IF NOT EXISTS trial_end BIGINT;",
            "ALTER TABLE bots ADD COLUMN IF NOT EXISTS blocked INTEGER DEFAULT 0;",
        ]:
            cur.execute(col_sql)
        con.commit()


def db_get_bot(public_id: str) -> dict | None:
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
    # Parse profile
    d["profile"] = {}
    if d.get("profile_json"):
        try:
            d["profile"] = json.loads(d["profile_json"])
        except Exception:
            d["profile"] = {}
    # Coerce types
    d["paid"]                   = int(d.get("paid") or 0)
    d["purchase_email_sent"]    = int(d.get("purchase_email_sent") or 0)
    d["blocked"]                = int(d.get("blocked") or 0)
    d["stripe_customer_id"]     = d.get("stripe_customer_id") or None
    d["stripe_subscription_id"] = d.get("stripe_subscription_id") or None
    d["stripe_status"]          = d.get("stripe_status") or None
    return d


def db_upsert_bot(bot: dict):
    profile_json = json.dumps(bot.get("profile") or {}, ensure_ascii=False)
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
            bot.get("public_id"), bot.get("bot_key"), bot.get("pack"),
            bot.get("name"), bot.get("color"), bot.get("avatar_file"),
            bot.get("greeting"), bot.get("buyer_email"), bot.get("owner_name"),
            profile_json,
            int(bot.get("paid") or 0), int(bot.get("purchase_email_sent") or 0),
            bot.get("stripe_customer_id") or None,
            bot.get("stripe_subscription_id") or None,
            bot.get("stripe_status") or None,
            bot.get("created_at"), bot.get("trial_end"),
            int(bot.get("blocked") or 0),
        ))
        con.commit()


def db_get_bot_by_subscription_id(sub_id: str) -> dict | None:
    sub_id = (sub_id or "").strip()
    if not sub_id:
        return None
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM bots WHERE stripe_subscription_id=%s LIMIT 1", (sub_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def db_set_paid_status(public_id: str, paid: int,
                        stripe_status: str | None = None, blocked: int | None = None):
    with db_connect() as con:
        cur = con.cursor()
        fields, vals = ["paid=%s"], [int(paid)]
        if stripe_status is not None:
            fields.append("stripe_status=%s"); vals.append(stripe_status)
        if blocked is not None:
            fields.append("blocked=%s"); vals.append(int(blocked))
        vals.append(public_id)
        cur.execute(f"UPDATE bots SET {', '.join(fields)} WHERE public_id=%s", tuple(vals))
        con.commit()


def db_set_trial(public_id: str, created_at: int | None = None, trial_end: int | None = None):
    fields, vals = [], []
    if created_at is not None:
        fields.append("created_at=%s"); vals.append(int(created_at))
    if trial_end is not None:
        fields.append("trial_end=%s"); vals.append(int(trial_end))
    if not fields:
        return
    vals.append(public_id)
    with db_connect() as con:
        cur = con.cursor()
        cur.execute(f"UPDATE bots SET {', '.join(fields)} WHERE public_id=%s", tuple(vals))
        con.commit()


def db_get_flags(public_id: str) -> tuple[int, int]:
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("SELECT paid, purchase_email_sent FROM bots WHERE public_id=%s LIMIT 1", (public_id,))
        row = cur.fetchone()
    if not row:
        return (0, 0)
    return (int(row.get("paid") or 0), int(row.get("purchase_email_sent") or 0))


def db_mark_purchase_email_sent(public_id: str):
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("UPDATE bots SET purchase_email_sent=1 WHERE public_id=%s", (public_id,))
        con.commit()


# =========================================================
# ACCESS / TRIAL LOGIC
# =========================================================

def _now_ts() -> int:
    return int(time.time())


def compute_access_state(bot: dict) -> tuple[str, str]:
    if not bot:
        return ("—", "unknown")
    paid    = int(bot.get("paid") or 0)
    blocked = int(bot.get("blocked") or 0)
    st      = (bot.get("stripe_status") or "unknown").strip().lower()
    if blocked == 1 or st in ("canceled", "unpaid", "trial_expired", "blocked"):
        return ("BLOQUÉ", "canceled")
    if paid == 1 and st in ("active", "trialing", "past_due", "unknown"):
        return ("PAYÉ", "active")
    return ("ESSAI", "trialing" if st == "trialing" else "unknown")


def should_block_bot(bot: dict) -> tuple[bool, str]:
    if not bot:
        return (False, "")
    if int(bot.get("paid") or 0) == 1:
        return (False, "")
    if int(bot.get("blocked") or 0) == 1:
        return (True, "blocked")
    now = _now_ts()
    created_at = bot.get("created_at") or now
    trial_end  = bot.get("trial_end")
    grace      = TRIAL_GRACE_HOURS * 3600
    if trial_end:
        return (now > int(trial_end) + grace, "trial_expired") if now > int(trial_end) + grace else (False, "")
    limit = int(created_at) + int(TRIAL_BLOCK_DAYS) * 86400 + grace
    return (True, "trial_expired") if now > limit else (False, "")


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
        bot = dict(bot)
        bot["blocked"] = 1
        bot["stripe_status"] = reason or bot.get("stripe_status")
    return bot


# =========================================================
# PACK / AVATAR HELPERS
# =========================================================

PACK_LABELS = {
    "avocat": "Avocat", "medecin": "Médecin",
    "immo": "Immobilier", "immobilier": "Immobilier",
    "betty_aide_a_domicile": "Aide à domicile", "betty_architecte": "Architecte",
    "betty_artisan": "Artisan", "betty_assurance": "Assurance",
    "betty_coach": "Coach", "betty_coiffeur": "Coiffeur",
    "betty_dentiste": "Dentiste", "betty_dj": "DJ",
    "betty_estheticienne": "Esthéticienne", "betty_garde_denfant": "Garde d'enfant",
    "betty_graphiste": "Graphiste", "betty_infirmiere": "Infirmière",
    "betty_kine": "Kiné", "betty_marketing": "Marketing",
    "betty_mecano": "Mécano", "betty_menage": "Ménage",
    "betty_nutritioniste": "Nutritionniste", "betty_osteopate": "Ostéopathe",
    "betty_paysagiste": "Paysagiste", "betty_photographe": "Photographe",
    "betty_plombier": "Plombier", "betty_serrurier": "Serrurier",
    "betty_sophrologue": "Sophrologue", "betty_soutien_scolaire": "Soutien scolaire",
    "betty_trader": "Trader", "betty_traiteur": "Traiteur",
    "betty_verrier": "Verrier", "betty_yoga": "Prof de yoga",
}

PACK_AVATAR = {
    "avocat": "avocat.jpg", "medecin": "medecin.jpg",
    "immo": "immo.jpg", "immobilier": "immo.jpg",
    "betty_architecte": "Betty_architecte.png",
    "betty_artisan": "Betty_artisan.png",
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
    "betty_aide_a_domicile": "Betty_aide_a_domicile.png",
}

CORE_BOTKEY_BY_PACK = {
    "avocat": "avocat-001",
    "medecin": "medecin-003",
    "immo": "immo-002",
    "immobilier": "immo-002",
}


def humanize_pack(pack: str) -> str:
    p = (pack or "").strip().lower()
    if p in PACK_LABELS:
        return PACK_LABELS[p]
    slug = re.sub(r"^betty_", "", p).replace("_", " ").strip()
    return (slug[:1].upper() + slug[1:]) if slug else "Métier"


def avatar_for_pack(pack: str, avatar_hint: str = "", fallback: str = "avocat.jpg") -> str:
    p = (pack or "").strip().lower()
    if p in PACK_AVATAR:
        return PACK_AVATAR[p]
    return avatar_hint or fallback


def botkey_for_pack(pack: str) -> str:
    p = (pack or "").strip().lower()
    if p in CORE_BOTKEY_BY_PACK:
        return CORE_BOTKEY_BY_PACK[p]
    slug = re.sub(r"[^a-z0-9]+", "", p)[:10] or "bot"
    return f"custom-{slug}"


def _gen_public_id(email: str, bot_key: str) -> str:
    h = hashlib.sha1((email + "|" + bot_key).encode()).hexdigest()[:8]
    return f"{bot_key}-{h}"


# =========================================================
# PROFILE / CONTACT HELPERS
# =========================================================

def parse_contact_info(raw: str) -> dict:
    raw = (raw or "").strip()
    if not raw:
        return {"raw": "", "name": "", "email": "", "phone": "", "address": "", "hours": ""}
    m_email = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', raw)
    m_phone = re.search(r'(\+?\d[\d \.\-]{6,})', raw)
    m_hours = re.search(r'(horaire|heures?|ouvertures?)\s*[:\-]?\s*(.+)', raw, re.I)
    m_name  = re.search(r'(?:nom|entreprise|cabinet)\s*[:\-]?\s*(.+)', raw, re.I)
    m_addr  = re.search(r'(?:adresse|address)\s*[:\-]?\s*(.+)', raw, re.I)
    return {
        "raw":     raw,
        "name":    m_name.group(1).strip() if m_name else "",
        "email":   m_email.group(0) if m_email else "",
        "phone":   m_phone.group(1).strip() if m_phone else "",
        "address": m_addr.group(1).strip() if m_addr else "",
        "hours":   m_hours.group(2).strip() if m_hours else "",
    }


def build_business_block(profile: dict) -> str:
    if not profile:
        return ""
    lines = ["\n---\nINFORMATIONS ÉTABLISSEMENT :"]
    for key, label in [
        ("name", "Nom"), ("phone", "Téléphone"),
        ("email", "Email"), ("address", "Adresse"), ("hours", "Horaires"),
    ]:
        if profile.get(key):
            lines.append(f"• {label} : {profile[key]}")
    lines.append("---\n")
    return "\n".join(lines)


# =========================================================
# SYSTEM PROMPT BUILDER
# =========================================================

def build_system_prompt(pack: str, profile: dict, greeting: str = "") -> str:
    """
    Builds the full system prompt for a bot:
    1. Load YAML from packs/<pack>.yaml
    2. Inject business profile block
    3. Inject greeting hint if present
    """
    pack = (pack or "avocat").strip().lower()
    yaml_path = os.path.join(os.getcwd(), "packs", f"{pack}.yaml")

    # --- Load YAML pack ---
    base_prompt = ""
    if os.path.exists(yaml_path):
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            base_prompt = (data.get("prompt") or "").strip()
        except Exception as e:
            app.logger.warning(f"[YAML] Failed to load {yaml_path}: {e}")

    if not base_prompt:
        pack_label = humanize_pack(pack)
        base_prompt = (
            f"Tu es Betty, une assistante virtuelle professionnelle pour un(e) {pack_label}. "
            "Tu es chaleureuse, claire et orientée conversion. "
            "Tu collectes les informations du prospect et transmets le lead au professionnel."
        )

    # --- Business profile block ---
    biz_block = build_business_block(profile or {})

    # --- Greeting hint ---
    greeting_line = f"\nMessage d'accueil recommandé : {greeting}\n" if greeting else ""

    # --- Lead collection instructions ---
    lead_instructions = """
---
COLLECTE DU LEAD (obligatoire) :
- Tu collectes naturellement, en une question à la fois, dans cet ordre :
  1) nom
  2) email
  3) telephone
- Pose toujours UNE SEULE question à la fois.
- Ne demande pas une information déjà donnée.
- Quand tu as nom + email + telephone, produis obligatoirement un bloc LEAD_JSON :

<LEAD_JSON>{"nom": "...", "email": "...", "telephone": "...", "besoin": "..."}</LEAD_JSON>

- Ajoute tout champ optionnel (besoin, budget, type_projet, etc.) si l'utilisateur l'a mentionné.
- Après le LEAD_JSON, conclus par une phrase courtoise.
---
"""

    return f"{base_prompt}\n{biz_block}\n{lead_instructions}\n{greeting_line}"


# =========================================================
# LLM
# =========================================================

def call_llm_with_history(system_prompt, history, user_input):
    print("API KEY:", TOGETHER_API_KEY)

    if not TOGETHER_API_KEY:
        print("❌ PAS DE CLE API")
        return "ERREUR: PAS DE CLE API"

    try:
        r = requests.post(
            TOGETHER_API_URL,
            headers={
                "Authorization": f"Bearer {TOGETHER_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    *history,
                    {"role": "user", "content": user_input}
                ],
                "max_tokens": 200
            },
            timeout=30
        )

        print("STATUS:", r.status_code)
        print("RAW:", r.text[:300])

        if r.ok:
            return r.json()["choices"][0]["message"]["content"]

        return "ERREUR API"

    except Exception as e:
        print("EXCEPTION:", e)
        return "ERREUR EXCEPTION"


# =========================================================
# LEAD EXTRACTION
# =========================================================

LEAD_TAG_RE = re.compile(
    r"<\s*LEAD_?JSON\s*>\s*(\{.*?\})\s*</\s*LEAD_?JSON\s*>",
    re.IGNORECASE | re.DOTALL
)


def extract_lead_json(text: str) -> tuple[str, dict | None]:
    """Strip LEAD_JSON block from text and return (clean_text, lead_dict)."""
    if not text:
        return text, None
    lead = None
    matches = list(LEAD_TAG_RE.finditer(text))
    if matches:
        raw = matches[-1].group(1).strip()
        try:
            lead = json.loads(raw)
        except Exception:
            lead = None
        text = LEAD_TAG_RE.sub("", text).strip()
    return text, lead


def is_lead_complete(lead: dict) -> bool:
    """A lead is complete when nom + email + telephone are all present."""
    if not lead:
        return False
    return all(bool((lead.get(k) or "").strip()) for k in ("nom", "email", "telephone"))


# =========================================================
# EMAIL
# =========================================================

def send_lead_email(to_email: str, lead: dict, bot_name: str = "Betty Bot"):
    if not (MJ_API_KEY and MJ_API_SECRET and to_email):
        app.logger.warning("[LEAD][MAILJET] Config manquante ou email vide.")
        return
    subject = f"Nouveau lead qualifié via {bot_name}"
    lines = [f"{k.capitalize():<16}: {v}" for k, v in lead.items() if v]
    text = "\n".join(lines)
    payload = {
        "Messages": [{
            "From":     {"Email": MJ_FROM_EMAIL, "Name": MJ_FROM_NAME},
            "To":       [{"Email": to_email}],
            "Subject":  subject,
            "TextPart": text,
        }]
    }
    try:
        r = requests.post(
            "https://api.mailjet.com/v3.1/send",
            auth=(MJ_API_KEY, MJ_API_SECRET),
            json=payload, timeout=15,
        )
        app.logger.info(f"[LEAD][MAILJET] {'OK' if r.ok else f'KO {r.status_code} {r.text[:200]}'}")
    except Exception as e:
        app.logger.error(f"[LEAD][MAILJET] {type(e).__name__}: {e}")


def send_purchase_email(to_email: str, bot: dict):
    if not (MJ_API_KEY and MJ_API_SECRET and to_email):
        return
    public_id  = bot.get("public_id") or ""
    pack_label = humanize_pack(bot.get("pack") or "")
    name       = bot.get("name") or "Betty Bot"
    embed_url  = f"{BASE_URL}/chat?public_id={public_id}&embed=1"
    iframe     = (
        f'<iframe src="{embed_url}" title="{name}" '
        'style="width:100%;max-width:420px;height:620px;border:0;border-radius:16px;" '
        'loading="lazy" allow="clipboard-read; clipboard-write"></iframe>'
    )
    text = (
        f"Bonjour,\n\nMerci pour votre inscription à Betty Bots.\n\n"
        f"Pack      : {pack_label}\nNom du bot: {name}\nCode      : {public_id}\n"
        f"Lien test : {embed_url}\n\nCode HTML à coller sur votre site :\n\n{iframe}\n\n"
        f"À très vite,\nBetty Bots\n"
    )
    payload = {
        "Messages": [{
            "From":     {"Email": MJ_FROM_EMAIL, "Name": MJ_FROM_NAME},
            "To":       [{"Email": to_email}],
            "Subject":  f"Votre Betty ({pack_label}) est activée ✅",
            "TextPart": text,
        }]
    }
    try:
        r = requests.post(
            "https://api.mailjet.com/v3.1/send",
            auth=(MJ_API_KEY, MJ_API_SECRET),
            json=payload, timeout=15,
        )
        app.logger.info(f"[PURCHASE][MAILJET] {'OK' if r.ok else f'KO {r.status_code}'}")
    except Exception as e:
        app.logger.error(f"[PURCHASE][MAILJET] {type(e).__name__}: {e}")


# =========================================================
# CONVERSATION HISTORY (in-memory)
# =========================================================

CONVS: dict[str, list] = {}  # conv_id -> list of {role, content}

MAX_HISTORY = 8


def get_history(conv_id: str) -> list:
    return list(CONVS.get(conv_id, []))


def save_history(conv_id: str, history: list):
    CONVS[conv_id] = history[-MAX_HISTORY:]


# =========================================================
# STATIC / FAVICON ROUTES
# =========================================================

@app.route("/favicon.ico")
def favicon_root():
    p = os.path.join(app.root_path, "static", "favicon.ico")
    return send_from_directory(os.path.dirname(p), os.path.basename(p)) if os.path.exists(p) else ("", 204)

@app.route("/favicon.png")
def favicon_png():
    p = os.path.join(app.root_path, "static", "favicon.png")
    return send_from_directory(os.path.dirname(p), os.path.basename(p)) if os.path.exists(p) else ("", 204)

@app.route("/site.webmanifest")
def site_manifest():
    p = os.path.join(app.root_path, "static", "site.webmanifest")
    if os.path.exists(p):
        return send_from_directory(os.path.dirname(p), os.path.basename(p))
    return jsonify({"name": "Betty Bots", "short_name": "Betty", "icons": []}), 200

@app.route("/sitemap.xml")
def sitemap():
    return send_from_directory("static", "sitemap.xml")

@app.route("/avatar/<slug>")
def avatar(slug: str):
    static_dir = os.path.join(app.root_path, "static")
    filename   = f"logo-{slug}.jpg"
    path       = os.path.join(static_dir, filename)
    if os.path.exists(path):
        return send_from_directory(static_dir, filename)
    transparent = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+Xad8AAAAASUVORK5CYII="
    )
    return Response(transparent, mimetype="image/png")


# =========================================================
# UTILITY
# =========================================================

def static_url(filename: str) -> str:
    return url_for("static", filename=filename)


# =========================================================
# PAGE ROUTES
# =========================================================

@app.route("/")
def index():
    try:
        lang = request.args.get("lang", "fr")
        t = translations.get(lang, translations["fr"])
        return render_template("index.html", t=t, lang=lang)
    except TemplateNotFound:
        return "<h1>Betty Bots</h1><p>templates/index.html manquant.</p>", 200


@app.route("/config", methods=["GET", "POST"])
def config_page():
    if request.method == "POST":
        pack       = (request.form.get("pack", "avocat") or "avocat").strip().lower()
        color      = request.form.get("color", "#4F46E5")
        avatar_in  = (request.form.get("avatar", "") or "").strip()
        greeting   = request.form.get("greeting", "")
        contact    = request.form.get("contact_info", "")
        persona_x  = request.form.get("persona_x", "0")
        persona_y  = request.form.get("persona_y", "0")
        website_url = (request.form.get("website_url", "") or "").strip()
        avatar     = avatar_for_pack(pack, avatar_hint=avatar_in)
        return redirect(url_for(
            "inscription_page",
            pack=pack, color=color, avatar=avatar,
            greeting=greeting, contact=contact,
            px=persona_x, py=persona_y, website_url=website_url,
        ))
    try:
        return render_template("config.html", title="Configurer votre bot")
    except TemplateNotFound:
        return "<h1>/config</h1><p>templates/config.html manquant.</p>", 200


@app.route("/inscription", methods=["GET", "POST"])
def inscription_page():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()
        if not email or "@" not in email:
            return "Email invalide.", 400

        def _pick(key: str, default: str = "") -> str:
            v = (request.form.get(key) or "").strip()
            return v or (request.args.get(key, default) or default).strip()

        pack        = _pick("pack", "avocat") or "avocat"
        color       = _pick("color", "#4F46E5") or "#4F46E5"
        avatar      = _pick("avatar", "")
        greet       = _pick("greeting", "")
        contact     = _pick("contact", "")
        px          = _pick("px", "0")
        py          = _pick("py", "0")
        website_url = _pick("website_url", "")

        if not stripe.api_key or not PRICE_ID:
            return "Paiement indisponible (Stripe non configuré).", 500

        bot_key      = botkey_for_pack(pack)
        public_id    = _gen_public_id(email, bot_key)
        avatar_final = avatar_for_pack(pack, avatar_hint=avatar)

        try:
            cancel_params = {"pack": pack, "color": color, "avatar": avatar_final, "greeting": greet}
            session_obj = stripe.checkout.Session.create(
                mode="subscription",
                line_items=[{"price": PRICE_ID, "quantity": 1}],
                customer_email=email,
                subscription_data={"trial_period_days": TRIAL_DAYS},
                success_url=f"{BASE_URL}/recap?public_id={public_id}",
                cancel_url=f"{BASE_URL}/inscription?{urlencode(cancel_params)}",
                metadata={
                    "public_id":    public_id,
                    "bot_key":      bot_key,
                    "pack":         pack,
                    "color":        color,
                    "avatar":       avatar_final,
                    "greeting":     greet,
                    "contact_info": contact,
                    "persona_x":    px,
                    "persona_y":    py,
                    "website_url":  website_url,
                },
            )
            return redirect(session_obj.url, code=303)
        except Exception as e:
            app.logger.exception(f"[STRIPE] checkout failed: {e}")
            return "Erreur Stripe. Merci de réessayer.", 502

    cfg = {
        "pack":     (request.args.get("pack", "avocat") or "avocat").strip(),
        "color":    (request.args.get("color", "#4F46E5") or "#4F46E5").strip(),
        "avatar":   (request.args.get("avatar", "") or "").strip(),
        "greeting": request.args.get("greeting", "") or "",
        "contact":  request.args.get("contact", "") or "",
        "px":       request.args.get("px", "0") or "0",
        "py":       request.args.get("py", "0") or "0",
    }
    return render_template("inscription.html", title="Inscription", cfg=cfg)


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
        return "Customer Stripe manquant.", 400
    if not stripe.api_key:
        return "Stripe non configuré.", 500
    return_url = STRIPE_PORTAL_RETURN_URL or f"{BASE_URL}/recap?public_id={public_id}"
    try:
        portal = stripe.billing_portal.Session.create(
            customer=customer_id, return_url=return_url
        )
        return redirect(portal.url, code=303)
    except Exception as e:
        app.logger.exception(f"[STRIPE] billing portal: {e}")
        return "Erreur portail Stripe.", 502


@app.route("/recap")
def recap_page():
    public_id = (request.args.get("public_id") or "").strip()
    if not public_id:
        return "public_id manquant.", 400

    bot = db_get_bot(public_id)
    if not bot:
        return render_template("pending.html", title="Activation en cours"), 200

    bot          = enforce_block_if_needed(public_id, bot)
    pack_code    = (bot.get("pack") or "avocat").strip().lower()
    pack_label   = humanize_pack(pack_code)
    display_name = bot.get("name") or "Betty Bot"
    owner        = bot.get("owner_name") or ""
    full_name    = f"{display_name} — {owner}" if owner else display_name
    avatar_file  = avatar_for_pack(pack_code, avatar_hint=bot.get("avatar_file") or "")
    buyer        = (bot.get("buyer_email") or "").strip()

    params = {"public_id": public_id, "embed": "1"}
    if buyer:
        params["buyer_email"] = buyer
    embed_url = f"{BASE_URL}/chat?{urlencode(params)}"
    iframe_snippet = (
        f'<iframe src="{embed_url}" title="{full_name}" '
        'style="width:100%;max-width:420px;height:620px;border:0;border-radius:16px;" '
        'loading="lazy" allow="clipboard-read; clipboard-write"></iframe>'
    )

    access_label, access_css = compute_access_state(bot)
    days_left = None
    if bot.get("trial_end"):
        delta = int(bot["trial_end"]) - _now_ts()
        if delta > 0:
            days_left = max(0, int(delta // 86400))

    cfg = {
        "pack": pack_code, "pack_label": pack_label,
        "color": bot.get("color") or "#4F46E5",
        "greeting": bot.get("greeting") or "Bonjour, qu'est-ce que je peux faire pour vous ?",
        "avatar_url": static_url(avatar_file),
        "public_id": public_id,
        "buyer_email": buyer,
        "display_name": display_name, "owner_name": owner, "full_name": full_name,
        "embed_url": embed_url, "iframe_snippet": iframe_snippet,
        "access_label": access_label, "access_css": access_css,
        "trial_days_left": days_left,
        "billing_portal_url": f"{BASE_URL}/billing_portal?public_id={public_id}",
    }
    try:
        return render_template("recap.html", title="Récapitulatif", cfg=cfg,
                               info=cfg, base_url=BASE_URL, full_name=full_name)
    except TemplateNotFound:
        return f"<pre>{json.dumps(cfg, ensure_ascii=False, indent=2)}</pre>", 200


@app.route("/chat")
def chat_page():
    public_id    = (request.args.get("public_id") or "").strip()
    embed        = request.args.get("embed", "0") == "1"
    buyer_email  = (request.args.get("buyer_email") or "").strip()
    bot          = db_get_bot(public_id) if public_id else None

    if bot:
        bot = enforce_block_if_needed(public_id, bot)
    else:
        bot = {
            "public_id":  public_id or "unknown",
            "name":       "Betty Bot",
            "color":      "#4F46E5",
            "avatar_file": "avocat.jpg",
            "greeting":   "Bonjour, qu'est-ce que je peux faire pour vous ?",
            "pack":       "avocat",
        }

    pack_code    = (bot.get("pack") or "avocat").lower()
    avatar_file  = avatar_for_pack(pack_code, avatar_hint=bot.get("avatar_file") or "")
    display_name = re.sub(r"\s*\([^)]*\)\s*$", "", (bot.get("name") or "Betty Bot")).strip()

    try:
        return render_template(
            "chat.html",
            title="Betty — Chat",
            base_url=BASE_URL,
            public_id=bot.get("public_id") or "",
            full_name=display_name,
            header_title="Betty Bot, votre assistante AI",
            color=bot.get("color") or "#4F46E5",
            avatar_url=static_url(avatar_file),
            greeting=bot.get("greeting") or "Bonjour, qu'est-ce que je peux faire pour vous ?",
            buyer_email=buyer_email,
            embed=embed,
        )
    except TemplateNotFound:
        return "<h1>Chat</h1><p>Template manquant.</p>", 200


@app.route("/betty")
def betty_page():
    return render_template("betty.html")


@app.route("/chatbot-site-web")
def chatbot_site_web():
    return render_template("chatbot-site-web.html")


@app.route("/carte")
def carte():
    return redirect("https://group1-6y79.onrender.com/", code=302)


# =========================================================
# HEALTH
# =========================================================

@app.get("/api")
def health():
    return "OK Betty", 200

@app.route("/healthz")
def healthz():
    return "ok", 200


# =========================================================
# API — MAIN CHATBOT ENDPOINT
# =========================================================

@app.route("/api/bettybot", methods=["POST"])
def bettybot_reply():
    # ----------------------------------------------------------
    # 1. Parse request
    # ----------------------------------------------------------
    payload    = request.get_json(force=True, silent=True) or {}
    user_input = (payload.get("message") or "").strip()
    public_id  = (payload.get("bot_id") or payload.get("public_id") or "").strip()
    conv_id    = (payload.get("conv_id") or "").strip() or str(uuid.uuid4())

    if not user_input:
        return jsonify({"response": "Dites-moi ce dont vous avez besoin 🙂", "stage": "collecting"}), 200

    # ----------------------------------------------------------
    # 2. Load bot config
    # ----------------------------------------------------------
    bot = db_get_bot(public_id) if public_id else None
    if not bot:
        return jsonify({"response": "Bot introuvable.", "stage": "error"}), 404

    # Check subscription status
    bot = enforce_block_if_needed(public_id, bot)
    access_label, _ = compute_access_state(bot)
    if access_label == "BLOQUÉ":
        return jsonify({
            "response": (
                "Votre essai est terminé. Pour réactiver Betty, "
                "merci de régulariser l'abonnement via le lien de gestion."
            ),
            "stage": "blocked",
        }), 200

    pack        = (bot.get("pack") or "avocat").strip().lower()
    profile     = bot.get("profile") or {}
    greeting    = bot.get("greeting") or ""
    owner_email = (bot.get("buyer_email") or "").strip()
    bot_name    = bot.get("name") or "Betty Bot"

    # ----------------------------------------------------------
    # 3. Build system prompt
    # ----------------------------------------------------------
    system_prompt = build_system_prompt(pack=pack, profile=profile, greeting=greeting)

    # ----------------------------------------------------------
    # 4. Retrieve site context (top 3 snippets)
    # ----------------------------------------------------------
    try:
        results   = search_site(public_id, user_input)
        hits      = (results.get("hits") or [])[:3]
        if hits:
            context_lines = ["Informations extraites du site du client :"]
            for hit in hits:
                snippet = (hit.get("snippet") or "").strip()
                if snippet:
                    context_lines.append(f"- {snippet}")
            system_prompt += "\n\n" + "\n".join(context_lines)
    except Exception as e:
        app.logger.warning(f"[SITE SEARCH] {e}")

    # ----------------------------------------------------------
    # 5. Load conversation history
    # ----------------------------------------------------------
    history = get_history(conv_id)

    # ----------------------------------------------------------
    # 6. Call LLM
    # ----------------------------------------------------------
    llm_text = call_llm_with_history(system_prompt, history, user_input)

    if not llm_text:
        llm_text = "Bonjour 🙂 Comment puis-je vous aider ?"

    # ----------------------------------------------------------
    # 7. Extract lead from LLM response
    # ----------------------------------------------------------
    response_text, lead = extract_lead_json(llm_text)
    response_text = (response_text or llm_text or "").strip()
    
    # ----------------------------------------------------------
    # 8. Determine stage + send lead email if complete
    # ----------------------------------------------------------
    stage = "collecting"
    if lead and is_lead_complete(lead):
        stage = "ready"
        if owner_email:
            try:
                send_lead_email(
                    to_email=owner_email,
                    lead=lead,
                    bot_name=bot_name,
                )
            except Exception as e:
                app.logger.exception(f"[LEAD EMAIL] {e}")

    # ----------------------------------------------------------
    # 9. Update history
    # ----------------------------------------------------------
    history.append({"role": "user",      "content": user_input})
    history.append({"role": "assistant", "content": response_text})
    save_history(conv_id, history)

    # ----------------------------------------------------------
    # 10. Return response
    # ----------------------------------------------------------
    return jsonify({
        "response": response_text,
        "stage":    stage,
        "conv_id":  conv_id,
    })


# =========================================================
# API — BOT META (used by embed)
# =========================================================

@app.route("/api/bot_meta")
def bot_meta():
    bot_id = (request.args.get("bot_id") or request.args.get("public_id") or "").strip()
    bot    = db_get_bot(bot_id)
    if not bot:
        return jsonify({"error": "bot_not_found"}), 404

    pack_code   = (bot.get("pack") or "avocat").strip().lower()
    avatar_file = avatar_for_pack(pack_code, avatar_hint=bot.get("avatar_file") or "")

    return jsonify({
        "name":        bot.get("name") or "Betty Bot",
        "color_hex":   bot.get("color") or "#4F46E5",
        "avatar_url":  static_url(avatar_file),
        "greeting":    bot.get("greeting") or "Bonjour, qu'est-ce que je peux faire pour vous ?",
    })


@app.route("/api/embed_meta")
def embed_meta():
    public_id = (request.args.get("public_id") or "").strip()
    if not public_id:
        return jsonify({"error": "missing public_id"}), 400
    bot = db_get_bot(public_id)
    if not bot:
        return jsonify({"error": "bot_not_found"}), 404

    pack_code   = (bot.get("pack") or "avocat").strip().lower()
    avatar_file = avatar_for_pack(pack_code, avatar_hint=bot.get("avatar_file") or "")

    return jsonify({
        "bot_id":       public_id,
        "owner_name":   bot.get("owner_name") or "Client",
        "display_name": bot.get("name") or "Betty Bot",
        "color_hex":    bot.get("color") or "#4F46E5",
        "avatar_url":   static_url(avatar_file),
        "greeting":     bot.get("greeting") or "Bonjour, qu'est-ce que je peux faire pour vous ?",
    })


@app.route("/api/reset", methods=["POST"])
def reset_conv():
    key = (request.get_json(silent=True) or {}).get("key")
    if key and key in CONVS:
        CONVS.pop(key, None)
    return jsonify({"ok": True})


# =========================================================
# STRIPE WEBHOOK
# =========================================================

@app.route("/stripe_webhook", methods=["POST"])
def stripe_webhook():
    payload    = request.data
    sig_header = request.headers.get("Stripe-Signature")

    if not STRIPE_WEBHOOK_SECRET:
        app.logger.error("[STRIPE][WEBHOOK] STRIPE_WEBHOOK_SECRET manquant")
        return jsonify({"error": "webhook secret not configured"}), 500

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except ValueError:
        return jsonify({"error": "invalid payload"}), 400
    except stripe.error.SignatureVerificationError:
        return jsonify({"error": "invalid signature"}), 400

    event_type = event.get("type")
    app.logger.info(f"[STRIPE][WEBHOOK] {event_type}")

    # --- Bot creation on successful checkout ---
    if event_type == "checkout.session.completed":
        session_obj = event["data"]["object"]
        meta        = session_obj.get("metadata") or {}

        public_id = (meta.get("public_id") or session_obj.get("client_reference_id") or "").strip()
        buyer     = (
            (session_obj.get("customer_email") or "").strip()
            or ((session_obj.get("customer_details") or {}).get("email") or "").strip()
        )
        bot_key = (meta.get("bot_key") or "").strip()
        pack    = (meta.get("pack") or "avocat").strip()
        color   = (meta.get("color") or "#4F46E5").strip()
        avatar  = (meta.get("avatar") or "").strip()
        greet   = meta.get("greeting") or ""
        contact = meta.get("contact_info") or ""

        if not public_id or not bot_key or not buyer:
            app.logger.error(f"[STRIPE][WEBHOOK] metadata incomplet")
            return jsonify({"received": True}), 200

        profile      = parse_contact_info(contact)
        avatar_final = avatar_for_pack(pack, avatar_hint=avatar)
        pack_label   = humanize_pack(pack)

        stripe_customer_id     = session_obj.get("customer")
        stripe_subscription_id = session_obj.get("subscription")
        created_at             = _now_ts()
        trial_end              = created_at + TRIAL_DAYS * 86400
        stripe_status          = "trialing"

        try:
            if stripe_subscription_id and stripe.api_key:
                sub           = stripe.Subscription.retrieve(stripe_subscription_id)
                stripe_status = sub.get("status") or stripe_status
                trial_end     = sub.get("trial_end") or trial_end
        except Exception as e:
            app.logger.warning(f"[STRIPE] sub retrieve: {e}")

        existing  = db_get_bot(public_id)
        _, already_sent = db_get_flags(public_id)

        bot_db = {
            "public_id":               public_id,
            "bot_key":                 bot_key,
            "pack":                    pack,
            "name":                    f"Betty Bot ({pack_label})",
            "color":                   color,
            "avatar_file":             avatar_final,
            "greeting":                greet,
            "buyer_email":             buyer,
            "owner_name":              buyer.split("@")[0].title(),
            "profile":                 profile,
            "paid":                    int((existing or {}).get("paid") or 0),
            "purchase_email_sent":     int((existing or {}).get("purchase_email_sent") or 0),
            "stripe_customer_id":      stripe_customer_id,
            "stripe_subscription_id":  stripe_subscription_id,
            "stripe_status":           stripe_status,
            "created_at":              (existing or {}).get("created_at") or created_at,
            "trial_end":               (existing or {}).get("trial_end") or trial_end,
            "blocked":                 int((existing or {}).get("blocked") or 0),
        }
        db_upsert_bot(bot_db)

        site_url = meta.get("website_url")
        if site_url:
            try:
                data = crawl_site(site_url)
                save_crawled_site(public_id, site_url, data)
            except Exception as e:
                app.logger.warning(f"[CRAWL] {e}")

        if already_sent == 0:
            try:
                send_purchase_email(to_email=buyer, bot=bot_db)
                db_mark_purchase_email_sent(public_id)
            except Exception as e:
                app.logger.exception(f"[PURCHASE EMAIL] {e}")

    elif event_type == "invoice.paid":
        sub_id = event["data"]["object"].get("subscription")
        bot    = db_get_bot_by_subscription_id(sub_id)
        if bot:
            db_set_paid_status(bot["public_id"], paid=1, stripe_status="active", blocked=0)

    elif event_type == "invoice.payment_failed":
        sub_id = event["data"]["object"].get("subscription")
        bot    = db_get_bot_by_subscription_id(sub_id)
        if bot:
            db_set_paid_status(bot["public_id"], paid=0, stripe_status="past_due")

    elif event_type == "customer.subscription.deleted":
        sub_id = event["data"]["object"].get("id")
        bot    = db_get_bot_by_subscription_id(sub_id)
        if bot:
            db_set_paid_status(bot["public_id"], paid=0, stripe_status="canceled", blocked=1)

    elif event_type == "customer.subscription.updated":
        sub    = event["data"]["object"]
        sub_id = sub.get("id")
        bot    = db_get_bot_by_subscription_id(sub_id)
        if bot:
            st        = (sub.get("status") or "").strip() or None
            trial_end = sub.get("trial_end")
            if trial_end:
                db_set_trial(bot["public_id"], trial_end=int(trial_end))
            if st:
                with db_connect() as con:
                    cur = con.cursor()
                    cur.execute("UPDATE bots SET stripe_status=%s WHERE public_id=%s", (st, bot["public_id"]))
                    con.commit()

    return jsonify({"received": True}), 200


# =========================================================
# STRIPE — STARTER CHECKOUT
# =========================================================

@app.route("/checkout_starter")
def checkout_starter():
    price_id = os.getenv("STRIPE_PRICE_ID_STARTER", "").strip()
    if not stripe.api_key or not price_id:
        return "Paiement indisponible (Stripe non configuré).", 500

    public_id = f"starter-{uuid.uuid4().hex[:8]}"
    try:
        session_obj = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            subscription_data={"trial_period_days": TRIAL_DAYS},
            success_url=f"{BASE_URL}/recap?public_id={public_id}",
            cancel_url=BASE_URL,
            client_reference_id=public_id,
            metadata={
                "public_id":    public_id,
                "bot_key":      "betty-starter",
                "pack":         "avocat",
                "color":        "#4F46E5",
                "avatar":       "avocat.jpg",
                "greeting":     "",
                "contact_info": "",
                "persona_x":    "0",
                "persona_y":    "0",
                "website_url":  "",
            },
        )
        return redirect(session_obj.url, code=303)
    except Exception as e:
        app.logger.exception(f"[STRIPE] checkout_starter: {e}")
        return "Erreur Stripe.", 502


# =========================================================
# ADMIN
# =========================================================

@app.route("/admin/bots")
def admin_bots():
    with db_connect() as con:
        cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT public_id, name, buyer_email, paid, stripe_status, blocked, trial_end, created_at
            FROM bots ORDER BY created_at DESC NULLS LAST
        """)
        rows = cur.fetchall() or []

    bots = []
    for r in rows:
        b = {
            "public_id":    r.get("public_id"),
            "name":         r.get("name") or "Betty Bot",
            "buyer_email":  r.get("buyer_email") or "",
            "paid":         bool(r.get("paid")),
            "stripe_status": (r.get("stripe_status") or "unknown"),
            "blocked":      bool(r.get("blocked")),
            "trial_end":    r.get("trial_end"),
        }
        b["access_label"], b["access_css"] = compute_access_state(b)
        bots.append(b)

    return render_template("admin_bots.html", bots=bots, BASE_URL=BASE_URL)


@app.route("/api/test_mailjet")
def test_mailjet():
    to = (request.args.get("to") or os.getenv("TEST_TO_EMAIL") or "").strip()
    if not to:
        return jsonify({"ok": False, "error": "missing 'to' param"}), 400
    send_lead_email(
        to_email=to,
        lead={"nom": "Test", "email": "test@example.com", "telephone": "+33000000000", "besoin": "Test"},
        bot_name="Betty Bot (test)",
    )
    return jsonify({"ok": True, "to": to})


# =========================================================
# INIT DB + RUN
# =========================================================

db_init()
db_migrate()
ensure_site_tables()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
