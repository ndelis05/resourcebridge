from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "resourcebridge.db"
LOCAL_CSV_PATH = BASE_DIR / "Community Resources - Housing - categorized.csv"
FRONT_DOOR_CACHE = BASE_DIR / "frontdoor_il_cache.json"
FRONT_DOOR_CACHE_META = BASE_DIR / "frontdoor_il_cache_meta.json"
FRONT_DOOR_SAMPLE_URL = "https://localcarehub.org/open-data/hsds/sample/il.json.gz"
FRONT_DOOR_PARTNER_DOWNLOAD_URL = "https://localcarehub.org/api/hsds/il/download"
FRONT_DOOR_SERVICE_URL = "https://localcarehub.org/api/hsds/il/services/{service_id}"
CONSENSUS_THRESHOLD = max(2, int(os.environ.get("RESOURCEBRIDGE_CONSENSUS_THRESHOLD", "3")))
FRONT_DOOR_API_KEY = os.environ.get("FRONT_DOOR_API_KEY", "").strip()
ZIP_CACHE = BASE_DIR / "zip_centroids.json"
ZIP_LOOKUP_URL = "https://api.zippopotam.us/us/{zip_code}"
HOST = "127.0.0.1"
PORT = 4173
USER_AGENT = "ResourceBridge-CS338/1.0 (+Northwestern student project)"
FRONT_DOOR_CACHE_DAYS = 7
FRESHNESS_INTERVAL_DAYS = 7
SOURCE_STALE_DAYS = 90
NOTE_STALE_DAYS = 90
WEBSITE_TIMEOUT_SECONDS = 8
MAX_PAGE_BYTES = 750_000

VOLATILE_NOTE_TERMS = {
    "today", "currently", "current", "accepting", "availability", "available",
    "waitlist", "walk-in", "walk in", "hours", "open", "closed", "intake",
    "phone", "call", "appointment", "applications", "application window",
}

CLOSURE_SIGNALS = {
    "permanently closed": ("Program status", "Official page contains 'permanently closed'."),
    "temporarily closed": ("Program status", "Official page contains 'temporarily closed'."),
    "program has ended": ("Program status", "Official page says the program has ended."),
    "program ended": ("Program status", "Official page says the program ended."),
    "services are suspended": ("Program status", "Official page says services are suspended."),
    "services suspended": ("Program status", "Official page says services are suspended."),
    "no longer accepting applications": ("Availability / waitlist", "Official page says applications are no longer being accepted."),
    "applications are closed": ("Availability / waitlist", "Official page says applications are closed."),
    "applications closed": ("Availability / waitlist", "Official page says applications are closed."),
    "not accepting new clients": ("Availability / waitlist", "Official page says new clients are not being accepted."),
    "waitlist is closed": ("Availability / waitlist", "Official page says the waitlist is closed."),
}

# Additional deterministic change-language patterns found on official resource pages.
# These patterns never edit trusted data; they only create a field-specific review flag.
WEBSITE_CHANGE_PATTERNS = [
    (re.compile(r"\b(?:updated|new|revised|changed|reduced|limited|temporary)\s+(?:business\s+|service\s+|office\s+)?hours\b", re.I),
     "Hours", "Official page contains language indicating that hours may have changed."),
    (re.compile(r"\bhours\s+(?:have|has)\s+(?:changed|been updated|been revised)\b", re.I),
     "Hours", "Official page says that hours have changed or been updated."),
    (re.compile(r"\b(?:new|updated|revised|changed)\s+eligibility\s+(?:requirements|criteria)\b", re.I),
     "Eligibility requirements", "Official page contains language indicating updated eligibility requirements."),
    (re.compile(r"\beligibility(?:\s+(?:requirements|criteria))?\s+(?:have|has)\s+(?:changed|been updated|been revised)\b", re.I),
     "Eligibility requirements", "Official page says eligibility has changed or been updated."),
    (re.compile(r"\bno longer eligible\b", re.I),
     "Eligibility requirements", "Official page contains language that may indicate a change in eligibility."),
    (re.compile(r"\b(?:new|updated)\s+(?:location|address)\b", re.I),
     "Address", "Official page contains language indicating a new or updated location."),
    (re.compile(r"\b(?:moved|relocated)\s+to\b", re.I),
     "Address", "Official page contains language indicating that services may have moved or relocated."),
    (re.compile(r"\b(?:intake|applications?)\s+(?:is|are|has been|have been)\s+(?:currently\s+)?(?:paused|suspended|temporarily paused)\b", re.I),
     "Availability / waitlist", "Official page contains language indicating that intake or applications may be paused."),
    (re.compile(r"\b(?:waitlist|waiting list)\s+(?:is|has been)\s+(?:full|paused)\b", re.I),
     "Availability / waitlist", "Official page contains language indicating that the waitlist may be full or paused."),
    (re.compile(r"\b(?:currently\s+)?at capacity\b|\bcurrently full\b", re.I),
     "Availability / waitlist", "Official page contains language indicating that the program may currently be at capacity."),
    (re.compile(r"\bapplication\s+window\s+(?:is|has been)\s+(?:closed|paused|suspended)\b", re.I),
     "Availability / waitlist", "Official page contains language indicating that the application window may be closed or paused."),
    (re.compile(r"\bwalk[- ]?ins?\s+(?:are|is)\s+(?:not available|paused|suspended|not accepted)\b", re.I),
     "Availability / waitlist", "Official page contains language indicating that walk-in access may have changed."),
    (re.compile(r"\bclosed\s+on\s+(?:mondays?|tuesdays?|wednesdays?|thursdays?|fridays?|saturdays?|sundays?)\b", re.I),
     "Hours", "Official page contains day-specific closure language that may indicate changed hours."),
    (re.compile(r"\bservices?\s+(?:are|is)\s+now\s+located\s+at\b", re.I),
     "Address", "Official page contains language indicating a new service location."),
    (re.compile(r"\b(?:service|program)\s+(?:has been|is)\s+discontinued\b", re.I),
     "Program status", "Official page contains language indicating that the program or service may be discontinued."),
    (re.compile(r"\b(?:new|updated|changed)\s+(?:phone|telephone|contact)\s+(?:number|information)\b|\bour\s+(?:phone|telephone)\s+number\s+(?:has\s+changed|is\s+now)\b", re.I),
     "Phone number", "Official page contains language indicating that contact information may have changed."),
    (re.compile(r"\b(?:services?|support|assistance)\s+(?:is|are)\s+(?:now\s+)?available\s+in\s+(?:spanish|english|polish|arabic|chinese|mandarin|cantonese)\b|\b(?:spanish|polish|arabic|chinese)[- ]speaking\s+(?:staff|services?)\b", re.I),
     "Languages", "Official page contains specific language-access information that should be compared with the stored resource record."),
    (re.compile(r"\b(?:must|need to)\s+be\s+(?:a|an)?\s*(?:veteran|resident|family|parent|adult|youth|senior|woman|women|man|men)\b|\b(?:only|exclusively)\s+(?:serves?|for)\s+(?:veterans?|families|women|men|youth|seniors?|adults?)\b", re.I),
     "Eligibility requirements", "Official page contains a specific population restriction that may affect eligibility."),
]

SEARCH_STOPWORDS = {
    "a", "an", "and", "are", "for", "from", "i", "in", "is", "it", "me",
    "my", "near", "need", "of", "on", "please", "resource", "resources", "the",
    "to", "with", "who", "that", "this", "someone", "person", "client", "patient",
}

SEARCH_SYNONYMS = {
    "housing": {"housing", "shelter", "homeless", "homelessness", "rent", "eviction", "transitional", "supportive"},
    "shelter": {"shelter", "housing", "homeless", "homelessness", "emergency", "overnight"},
    "food": {"food", "pantry", "groceries", "meal", "meals", "nutrition"},
    "mental": {"mental", "behavioral", "counseling", "therapy", "psychiatric", "crisis"},
    "health": {"health", "clinic", "medical", "care", "primary", "wellness"},
    "transportation": {"transportation", "transport", "ride", "bus", "transit"},
    "legal": {"legal", "law", "attorney", "lawyer", "court"},
    "utilities": {"utilities", "utility", "energy", "electric", "gas", "water"},
    "insurance": {"insurance", "medicaid", "medicare", "coverage"},
    "domestic": {"domestic", "violence", "abuse", "safety", "survivor"},
    "substance": {"substance", "addiction", "recovery", "treatment", "drug", "alcohol"},
    "child": {"child", "children", "kid", "kids", "family", "families", "parent", "parents", "mother", "mom", "father", "dad"},
    "children": {"child", "children", "kid", "kids", "family", "families", "parent", "parents"},
    "family": {"family", "families", "child", "children", "kid", "kids", "parent", "parents"},
    "mother": {"mother", "mom", "parent", "family", "families", "women", "children"},
    "parent": {"parent", "parents", "family", "families", "child", "children"},
    "spanish": {"spanish", "espanol", "español", "bilingual", "language"},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                external_source TEXT NOT NULL DEFAULT 'local_csv',
                external_id TEXT NOT NULL,
                organization TEXT NOT NULL,
                category TEXT,
                phone TEXT,
                website TEXT,
                address TEXT,
                neighborhood TEXT,
                hours TEXT,
                services TEXT,
                eligibility TEXT,
                source_payload TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                UNIQUE(external_source, external_id)
            );

            CREATE TABLE IF NOT EXISTS corrections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resource_id INTEGER NOT NULL,
                author TEXT NOT NULL,
                initials TEXT NOT NULL,
                issue_type TEXT,
                message TEXT NOT NULL,
                location TEXT,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'proposed',
                FOREIGN KEY(resource_id) REFERENCES resources(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                correction_id INTEGER NOT NULL,
                author TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(correction_id) REFERENCES corrections(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                correction_id INTEGER NOT NULL,
                user_identifier TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(correction_id, user_identifier),
                FOREIGN KEY(correction_id) REFERENCES corrections(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS network_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resource_id INTEGER,
                author TEXT NOT NULL,
                initials TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'Note',
                message TEXT NOT NULL,
                location TEXT,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                FOREIGN KEY(resource_id) REFERENCES resources(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS network_post_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                author TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(post_id) REFERENCES network_posts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS network_post_votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                user_identifier TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(post_id, user_identifier),
                FOREIGN KEY(post_id) REFERENCES network_posts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS flags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resource_id INTEGER NOT NULL,
                note_id INTEGER,
                field TEXT NOT NULL,
                signal TEXT NOT NULL,
                reason TEXT NOT NULL,
                stored_value TEXT,
                observed_value TEXT,
                evidence_url TEXT,
                detected_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'unresolved',
                FOREIGN KEY(resource_id) REFERENCES resources(id) ON DELETE CASCADE,
                FOREIGN KEY(note_id) REFERENCES network_posts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS freshness_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resource_id INTEGER NOT NULL,
                checked_at TEXT NOT NULL,
                source TEXT NOT NULL,
                outcome TEXT NOT NULL,
                details TEXT,
                FOREIGN KEY(resource_id) REFERENCES resources(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS page_snapshots (
                resource_id INTEGER PRIMARY KEY,
                url TEXT,
                content_hash TEXT,
                content_text TEXT,
                checked_at TEXT NOT NULL,
                FOREIGN KEY(resource_id) REFERENCES resources(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS approved_overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resource_id INTEGER NOT NULL,
                field TEXT NOT NULL,
                approved_value TEXT NOT NULL,
                source_correction_id INTEGER NOT NULL,
                approved_at TEXT NOT NULL,
                UNIQUE(resource_id, field),
                FOREIGN KEY(resource_id) REFERENCES resources(id) ON DELETE CASCADE,
                FOREIGN KEY(source_correction_id) REFERENCES corrections(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS version_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resource_id INTEGER NOT NULL,
                field TEXT NOT NULL,
                previous_value TEXT,
                approved_value TEXT NOT NULL,
                source_correction_id INTEGER NOT NULL,
                changed_at TEXT NOT NULL,
                FOREIGN KEY(resource_id) REFERENCES resources(id) ON DELETE CASCADE,
                FOREIGN KEY(source_correction_id) REFERENCES corrections(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_corrections_resource_id ON corrections(resource_id);
            CREATE INDEX IF NOT EXISTS idx_comments_correction_id ON comments(correction_id);
            CREATE INDEX IF NOT EXISTS idx_votes_correction_id ON votes(correction_id);
            CREATE INDEX IF NOT EXISTS idx_network_posts_resource_id ON network_posts(resource_id);
            CREATE INDEX IF NOT EXISTS idx_post_comments_post_id ON network_post_comments(post_id);
            CREATE INDEX IF NOT EXISTS idx_post_votes_post_id ON network_post_votes(post_id);
            CREATE INDEX IF NOT EXISTS idx_flags_resource_id ON flags(resource_id);
            CREATE INDEX IF NOT EXISTS idx_flags_note_id ON flags(note_id);
            CREATE INDEX IF NOT EXISTS idx_freshness_resource_id ON freshness_checks(resource_id);
            CREATE INDEX IF NOT EXISTS idx_overrides_resource_id ON approved_overrides(resource_id);
            CREATE INDEX IF NOT EXISTS idx_history_resource_id ON version_history(resource_id);
            """
        )

        # Migrate the Deliverable 2 database in place.
        ensure_column(conn, "resources", "service_name", "TEXT")
        ensure_column(conn, "resources", "source_url", "TEXT")
        ensure_column(conn, "resources", "application_process", "TEXT")
        ensure_column(conn, "resources", "external_status", "TEXT")
        ensure_column(conn, "resources", "external_assured_date", "TEXT")
        ensure_column(conn, "resources", "latest_external_payload", "TEXT")
        ensure_column(conn, "resources", "last_external_checked", "TEXT")
        ensure_column(conn, "resources", "last_website_checked", "TEXT")
        ensure_column(conn, "resources", "postal_code", "TEXT")
        ensure_column(conn, "resources", "latitude", "REAL")
        ensure_column(conn, "resources", "longitude", "REAL")
        ensure_column(conn, "resources", "languages", "TEXT")
        ensure_column(conn, "resources", "service_area", "TEXT")
        ensure_column(conn, "corrections", "current_value", "TEXT")
        ensure_column(conn, "corrections", "suggested_value", "TEXT")
        ensure_column(conn, "corrections", "evidence", "TEXT")
        ensure_column(conn, "corrections", "verified_at", "TEXT")
        setting_set(conn, "consensus_threshold", str(CONSENSUS_THRESHOLD))
        conn.commit()


def setting_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def setting_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def clean(value) -> str:
    return str(value or "").strip()


def normalize_zip(value) -> str:
    match = re.search(r"\b(\d{5})(?:-\d{4})?\b", clean(value))
    return match.group(1) if match else ""


def extract_zip(value) -> str:
    return normalize_zip(value)


def to_float(value):
    try:
        return float(value) if clean(value) else None
    except (TypeError, ValueError):
        return None


def haversine_miles(lat1, lon1, lat2, lon2) -> float:
    r = 3958.7613
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp = math.radians(float(lat2) - float(lat1))
    dl = math.radians(float(lon2) - float(lon1))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def normalize_resource_payload(payload: dict) -> dict:
    organization = clean(payload.get("organization"))
    external_id = clean(payload.get("external_id") or organization)
    if not organization:
        raise ValueError("organization is required")
    if not external_id:
        raise ValueError("external_id is required")
    return {
        "external_source": clean(payload.get("external_source") or "local_csv"),
        "external_id": external_id,
        "organization": organization,
        "service_name": clean(payload.get("service_name")),
        "category": clean(payload.get("category")),
        "phone": clean(payload.get("phone")),
        "website": clean(payload.get("website")),
        "source_url": clean(payload.get("source_url") or payload.get("website")),
        "address": clean(payload.get("address")),
        "postal_code": normalize_zip(payload.get("postal_code") or extract_zip(payload.get("address"))),
        "latitude": to_float(payload.get("latitude")),
        "longitude": to_float(payload.get("longitude")),
        "neighborhood": clean(payload.get("neighborhood")),
        "hours": clean(payload.get("hours")),
        "languages": clean(payload.get("languages")),
        "service_area": clean(payload.get("service_area")),
        "services": clean(payload.get("services")),
        "eligibility": clean(payload.get("eligibility")),
        "application_process": clean(payload.get("application_process")),
        "external_status": clean(payload.get("external_status")),
        "external_assured_date": clean(payload.get("external_assured_date")),
        "source_payload": json.dumps(
            {k: v for k, v in payload.items() if k not in {"legacy", "score", "source_label", "was_known", "knowledge_match", "resource_id", "new_to_resourcebridge", "counts", "flags"} and not str(k).startswith("__")},
            ensure_ascii=False,
        ),
    }


def upsert_resource(conn: sqlite3.Connection, payload: dict, preserve_trusted: bool = True) -> tuple[sqlite3.Row, bool]:
    """
    Add an unseen resource. On later encounters, preserve the trusted ResourceBridge
    fields and update only last_seen/latest_external_payload. This is intentional:
    external changes should become flags, not silent trusted-data edits.
    """
    p = normalize_resource_payload(payload)
    existing = conn.execute(
        "SELECT * FROM resources WHERE external_source = ? AND external_id = ?",
        (p["external_source"], p["external_id"]),
    ).fetchone()
    now = utc_now()
    if existing:
        if preserve_trusted:
            conn.execute(
                """
                UPDATE resources SET
                    last_seen = ?,
                    latest_external_payload = ?,
                    service_name = CASE WHEN COALESCE(service_name,'') = '' THEN ? ELSE service_name END,
                    source_url = CASE WHEN COALESCE(source_url,'') = '' THEN ? ELSE source_url END,
                    application_process = CASE WHEN COALESCE(application_process,'') = '' THEN ? ELSE application_process END,
                    external_status = CASE WHEN COALESCE(external_status,'') = '' THEN ? ELSE external_status END,
                    external_assured_date = CASE WHEN COALESCE(external_assured_date,'') = '' THEN ? ELSE external_assured_date END,
                    postal_code = CASE WHEN COALESCE(postal_code,'') = '' THEN ? ELSE postal_code END,
                    latitude = COALESCE(latitude, ?), longitude = COALESCE(longitude, ?),
                    languages = CASE WHEN COALESCE(languages,'') = '' THEN ? ELSE languages END,
                    service_area = CASE WHEN COALESCE(service_area,'') = '' THEN ? ELSE service_area END
                WHERE id = ?
                """,
                (
                    now,
                    p["source_payload"],
                    p["service_name"],
                    p["source_url"],
                    p["application_process"],
                    p["external_status"],
                    p["external_assured_date"],
                    p["postal_code"], p["latitude"], p["longitude"], p["languages"], p["service_area"],
                    existing["id"],
                ),
            )
        else:
            conn.execute(
                """
                UPDATE resources SET
                    organization = ?, service_name = ?, category = ?, phone = ?, website = ?, source_url = ?,
                    address = ?, postal_code = ?, latitude = ?, longitude = ?, neighborhood = ?, hours = ?, languages = ?, service_area = ?,
                    services = ?, eligibility = ?, application_process = ?, external_status = ?, external_assured_date = ?,
                    source_payload = ?, latest_external_payload = ?, last_seen = ?
                WHERE id = ?
                """,
                (
                    p["organization"], p["service_name"], p["category"], p["phone"], p["website"], p["source_url"],
                    p["address"], p["postal_code"], p["latitude"], p["longitude"], p["neighborhood"], p["hours"], p["languages"], p["service_area"],
                    p["services"], p["eligibility"], p["application_process"], p["external_status"], p["external_assured_date"],
                    p["source_payload"], p["source_payload"], now,
                    existing["id"],
                ),
            )
        row = conn.execute("SELECT * FROM resources WHERE id = ?", (existing["id"],)).fetchone()
        return row, True

    conn.execute(
        """
        INSERT INTO resources (
            external_source, external_id, organization, service_name, category, phone, website, source_url,
            address, postal_code, latitude, longitude, neighborhood, hours, languages, service_area, services, eligibility, application_process, external_status,
            external_assured_date, source_payload, latest_external_payload, first_seen, last_seen
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            p["external_source"], p["external_id"], p["organization"], p["service_name"], p["category"],
            p["phone"], p["website"], p["source_url"], p["address"], p["postal_code"], p["latitude"], p["longitude"], p["neighborhood"], p["hours"],
            p["languages"], p["service_area"], p["services"], p["eligibility"], p["application_process"], p["external_status"],
            p["external_assured_date"], p["source_payload"], p["source_payload"], now, now,
        ),
    )
    row = conn.execute(
        "SELECT * FROM resources WHERE external_source = ? AND external_id = ?",
        (p["external_source"], p["external_id"]),
    ).fetchone()
    return row, False


TRUSTED_FIELD_MAP = {
    "phone": "phone",
    "phone number": "phone",
    "hours": "hours",
    "address": "address",
    "zip": "postal_code",
    "zip code": "postal_code",
    "postal code": "postal_code",
    "website": "website",
    "languages": "languages",
    "language": "languages",
    "eligibility": "eligibility",
    "eligibility requirements": "eligibility",
    "program status": "external_status",
    "status": "external_status",
}

def correction_field(issue_type: str | None) -> str | None:
    return TRUSTED_FIELD_MAP.get(clean(issue_type).lower())

def approved_overrides(conn: sqlite3.Connection, resource_id: int) -> dict:
    rows = conn.execute(
        """SELECT o.field, o.approved_value, o.source_correction_id, o.approved_at,
                  c.author, c.issue_type, c.evidence
           FROM approved_overrides o
           JOIN corrections c ON c.id = o.source_correction_id
           WHERE o.resource_id = ?""",
        (resource_id,),
    ).fetchall()
    return {r["field"]: dict(r) for r in rows}

def resource_history(conn: sqlite3.Connection, resource_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT h.*, c.author, c.issue_type, c.evidence
           FROM version_history h
           JOIN corrections c ON c.id = h.source_correction_id
           WHERE h.resource_id = ? ORDER BY h.id DESC""",
        (resource_id,),
    ).fetchall()
    return [dict(r) for r in rows]

def apply_consensus_if_ready(conn: sqlite3.Connection, correction_id: int) -> bool:
    row = conn.execute(
        """SELECT c.*, r.external_source, r.external_id FROM corrections c
           JOIN resources r ON r.id = c.resource_id WHERE c.id = ?""",
        (correction_id,),
    ).fetchone()
    if not row or row["status"] == "verified":
        return bool(row and row["status"] == "verified")
    votes = conn.execute("SELECT COUNT(*) FROM votes WHERE correction_id = ?", (correction_id,)).fetchone()[0]
    field = correction_field(row["issue_type"])
    suggested = clean(row["suggested_value"])
    if votes < CONSENSUS_THRESHOLD or not field or not suggested:
        return False
    resource = conn.execute("SELECT * FROM resources WHERE id = ?", (row["resource_id"],)).fetchone()
    existing_override = conn.execute(
        "SELECT approved_value FROM approved_overrides WHERE resource_id = ? AND field = ?",
        (row["resource_id"], field),
    ).fetchone()
    previous = existing_override["approved_value"] if existing_override else clean(resource[field])
    now = utc_now()
    conn.execute(
        """INSERT INTO approved_overrides(resource_id, field, approved_value, source_correction_id, approved_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(resource_id, field) DO UPDATE SET
             approved_value = excluded.approved_value,
             source_correction_id = excluded.source_correction_id,
             approved_at = excluded.approved_at""",
        (row["resource_id"], field, suggested, correction_id, now),
    )
    conn.execute(
        """INSERT INTO version_history(resource_id, field, previous_value, approved_value, source_correction_id, changed_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (row["resource_id"], field, previous, suggested, correction_id, now),
    )
    conn.execute("UPDATE corrections SET status='verified', verified_at=? WHERE id=?", (now, correction_id))
    flag_labels = {
        "phone": ["Phone", "Phone number"],
        "hours": ["Hours"],
        "address": ["Address"],
        "postal_code": ["ZIP code", "Postal code", "Address"],
        "website": ["Website"],
        "languages": ["Languages"],
        "eligibility": ["Eligibility", "Eligibility requirements"],
        "external_status": ["Program status", "Availability / waitlist"],
    }.get(field, [field])
    placeholders = ",".join("?" for _ in flag_labels)
    conn.execute(
        f"UPDATE flags SET status='resolved' WHERE resource_id=? AND status='unresolved' AND field IN ({placeholders})",
        (row["resource_id"], *flag_labels),
    )
    return True

def unresolved_flags(conn: sqlite3.Connection, resource_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, resource_id, note_id, field, signal, reason, stored_value, observed_value,
               evidence_url, detected_at, status
        FROM flags WHERE resource_id = ? AND status = 'unresolved'
        ORDER BY id DESC
        """,
        (resource_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def resource_counts(conn: sqlite3.Connection, resource_id: int) -> dict:
    return {
        "corrections": conn.execute("SELECT COUNT(*) FROM corrections WHERE resource_id = ?", (resource_id,)).fetchone()[0],
        "verified": conn.execute("SELECT COUNT(*) FROM corrections WHERE resource_id = ? AND status = 'verified'", (resource_id,)).fetchone()[0],
        "notes": conn.execute("SELECT COUNT(*) FROM network_posts WHERE resource_id = ? AND status = 'active'", (resource_id,)).fetchone()[0],
        "flags": conn.execute("SELECT COUNT(*) FROM flags WHERE resource_id = ? AND status = 'unresolved'", (resource_id,)).fetchone()[0],
    }


def serialize_resource(conn: sqlite3.Connection, row: sqlite3.Row, include_flags: bool = True) -> dict:
    data = dict(row)
    data["counts"] = resource_counts(conn, row["id"])
    data["overrides"] = approved_overrides(conn, row["id"])
    effective = {}
    for field in ("phone", "hours", "address", "postal_code", "website", "languages", "eligibility", "external_status"):
        effective[field] = data["overrides"].get(field, {}).get("approved_value", clean(row[field]))
    data["effective"] = effective
    data["history_count"] = conn.execute("SELECT COUNT(*) FROM version_history WHERE resource_id=?", (row["id"],)).fetchone()[0]
    if include_flags:
        data["flags"] = unresolved_flags(conn, row["id"])
    return data


def serialize_correction(conn: sqlite3.Connection, row: sqlite3.Row, current_user: str) -> dict:
    comments = conn.execute(
        "SELECT id, author, message, created_at FROM comments WHERE correction_id = ? ORDER BY id ASC",
        (row["id"],),
    ).fetchall()
    vote_count = conn.execute("SELECT COUNT(*) AS n FROM votes WHERE correction_id = ?", (row["id"],)).fetchone()["n"]
    user_voted = bool(
        conn.execute(
            "SELECT 1 FROM votes WHERE correction_id = ? AND user_identifier = ?",
            (row["id"], current_user),
        ).fetchone()
    )
    return {
        "id": row["id"],
        "resource_id": row["resource_id"],
        "organization": row["organization"],
        "resource_key": f"{row['external_source']}:{row['external_id']}",
        "category": "Resource correction",
        "kind": "correction",
        "issueType": row["issue_type"] or "Resource correction",
        "author": row["author"],
        "initials": row["initials"],
        "location": row["location"] or "",
        "timestamp": row["created_at"],
        "message": row["message"],
        "currentValue": row["current_value"] or "",
        "suggestedValue": row["suggested_value"] or "",
        "evidence": row["evidence"] or "",
        "status": row["status"],
        "verifiedAt": row["verified_at"] or "",
        "votes": vote_count,
        "userVoted": user_voted,
        "consensusThreshold": CONSENSUS_THRESHOLD,
        "remainingConfirmations": max(0, CONSENSUS_THRESHOLD - vote_count),
        "comments": [dict(c) for c in comments],
    }


def list_corrections(current_user: str = "alex.martinez") -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT c.*, r.organization, r.external_source, r.external_id
            FROM corrections c
            JOIN resources r ON r.id = c.resource_id
            ORDER BY c.id DESC
            """
        ).fetchall()
        return [serialize_correction(conn, row, current_user) for row in rows]


def serialize_network_post(conn: sqlite3.Connection, row: sqlite3.Row, current_user: str) -> dict:
    comments = conn.execute(
        "SELECT id, author, message, created_at FROM network_post_comments WHERE post_id = ? ORDER BY id ASC",
        (row["id"],),
    ).fetchall()
    vote_count = conn.execute("SELECT COUNT(*) FROM network_post_votes WHERE post_id = ?", (row["id"],)).fetchone()[0]
    user_voted = bool(
        conn.execute(
            "SELECT 1 FROM network_post_votes WHERE post_id = ? AND user_identifier = ?",
            (row["id"], current_user),
        ).fetchone()
    )
    note_flagged = bool(
        conn.execute(
            "SELECT 1 FROM flags WHERE note_id = ? AND status = 'unresolved' LIMIT 1",
            (row["id"],),
        ).fetchone()
    )
    return {
        "id": row["id"],
        "resource_id": row["resource_id"],
        "organization": row["organization"] or "",
        "resource_key": f"{row['external_source']}:{row['external_id']}" if row["external_source"] else "",
        "category": row["category"],
        "kind": "post",
        "author": row["author"],
        "initials": row["initials"],
        "location": row["location"] or "",
        "timestamp": row["created_at"],
        "message": row["message"],
        "status": row["status"],
        "votes": vote_count,
        "userVoted": user_voted,
        "comments": [dict(c) for c in comments],
        "noteFlagged": note_flagged,
    }


def list_network_posts(current_user: str = "alex.martinez", search: str = "") -> list[dict]:
    with db() as conn:
        params: list[str] = []
        where = "WHERE p.status = 'active'"
        if search.strip():
            like = f"%{search.strip()}%"
            where += " AND (p.message LIKE ? OR p.category LIKE ? OR r.organization LIKE ?)"
            params.extend([like, like, like])
        rows = conn.execute(
            f"""
            SELECT p.*, r.organization, r.external_source, r.external_id
            FROM network_posts p
            LEFT JOIN resources r ON r.id = p.resource_id
            {where}
            ORDER BY p.id DESC
            """,
            params,
        ).fetchall()
        return [serialize_network_post(conn, row, current_user) for row in rows]


# -------------------- Front Door external catalog --------------------

_frontdoor_lock = threading.Lock()
_frontdoor_index_cache: tuple[float, dict] | None = None


def request_bytes(url: str, timeout: int = 20, headers: dict | None = None) -> tuple[bytes, str, int, dict]:
    merged = {"User-Agent": USER_AGENT, "Accept": "application/json,text/html;q=0.9,*/*;q=0.8"}
    if headers:
        merged.update(headers)
    req = Request(url, headers=merged)
    with urlopen(req, timeout=timeout) as response:
        body = response.read(MAX_PAGE_BYTES if "text/html" in (response.headers.get("Content-Type") or "") else None)
        return body, response.geturl(), int(getattr(response, "status", 200)), dict(response.headers)


def frontdoor_headers() -> dict:
    if FRONT_DOOR_API_KEY:
        return {"Authorization": f"Bearer {FRONT_DOOR_API_KEY}"}
    return {}


def frontdoor_cache_age_days() -> float | None:
    if not FRONT_DOOR_CACHE.exists():
        return None
    return (time.time() - FRONT_DOOR_CACHE.stat().st_mtime) / 86400


def refresh_frontdoor_cache(force: bool = False) -> dict:
    global _frontdoor_index_cache
    with _frontdoor_lock:
        age = frontdoor_cache_age_days()
        if not force and age is not None and age < FRONT_DOOR_CACHE_DAYS:
            try:
                meta = json.loads(FRONT_DOOR_CACHE_META.read_text(encoding="utf-8")) if FRONT_DOOR_CACHE_META.exists() else {}
            except Exception:
                meta = {}
            return {"ok": True, "cached": True, "age_days": round(age, 2), **meta}

        download_urls = [FRONT_DOOR_PARTNER_DOWNLOAD_URL] if FRONT_DOOR_API_KEY else [FRONT_DOOR_SAMPLE_URL, FRONT_DOOR_PARTNER_DOWNLOAD_URL]
        last_error = None
        for download_url in download_urls:
            try:
                body, final_url, status, headers = request_bytes(
                    download_url, timeout=10, headers=frontdoor_headers()
                )
                break
            except Exception as exc:
                last_error = exc
        else:
            raise last_error or RuntimeError("Front Door download failed")
        if body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        data = json.loads(body.decode("utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("services"), list):
            raise ValueError("Front Door download did not contain an HSDS services table")

        temp = FRONT_DOOR_CACHE.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temp.replace(FRONT_DOOR_CACHE)
        meta = {
            "fetched_at": utc_now(),
            "generated_on": clean(data.get("generated_on")),
            "hsds_version": clean(data.get("hsds_version") or data.get("version")),
            "access": data.get("access") or ("partner" if FRONT_DOOR_API_KEY else "sample"),
            "services": len(data.get("services") or []),
            "final_url": final_url,
            "http_status": status,
        }
        FRONT_DOOR_CACHE_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        _frontdoor_index_cache = None
        return {"ok": True, "cached": False, "age_days": 0, **meta}


def load_frontdoor_data(allow_refresh: bool = True, force_refresh: bool = False) -> tuple[dict | None, dict]:
    error = ""
    if allow_refresh:
        try:
            refresh_frontdoor_cache(force=force_refresh)
        except Exception as exc:
            error = str(exc)
    if not FRONT_DOOR_CACHE.exists():
        return None, {"available": False, "error": error or "Front Door cache has not been downloaded yet"}
    try:
        data = json.loads(FRONT_DOOR_CACHE.read_text(encoding="utf-8"))
        meta = json.loads(FRONT_DOOR_CACHE_META.read_text(encoding="utf-8")) if FRONT_DOOR_CACHE_META.exists() else {}
        return data, {"available": True, "error": error, "age_days": round(frontdoor_cache_age_days() or 0, 2), **meta}
    except Exception as exc:
        return None, {"available": False, "error": str(exc)}


def listify(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def frontdoor_indices(data: dict) -> dict:
    global _frontdoor_index_cache
    mtime = FRONT_DOOR_CACHE.stat().st_mtime if FRONT_DOOR_CACHE.exists() else 0
    if _frontdoor_index_cache and _frontdoor_index_cache[0] == mtime:
        return _frontdoor_index_cache[1]

    organizations = {clean(x.get("id")): x for x in data.get("organizations") or []}
    locations = {clean(x.get("id")): x for x in data.get("locations") or []}
    addresses_by_location: dict[str, list] = {}
    for a in data.get("addresses") or []:
        addresses_by_location.setdefault(clean(a.get("location_id")), []).append(a)

    service_locations: dict[str, list[str]] = {}
    for link in data.get("service_at_locations") or []:
        service_locations.setdefault(clean(link.get("service_id")), []).append(clean(link.get("location_id")))

    phones_by_service: dict[str, list] = {}
    phones_by_org: dict[str, list] = {}
    phones_by_location: dict[str, list] = {}
    for p in data.get("phones") or []:
        if clean(p.get("service_id")):
            phones_by_service.setdefault(clean(p.get("service_id")), []).append(p)
        if clean(p.get("organization_id")):
            phones_by_org.setdefault(clean(p.get("organization_id")), []).append(p)
        if clean(p.get("location_id")):
            phones_by_location.setdefault(clean(p.get("location_id")), []).append(p)

    schedules_by_service: dict[str, list] = {}
    schedules_by_location: dict[str, list] = {}
    for s in data.get("schedules") or []:
        if clean(s.get("service_id")):
            schedules_by_service.setdefault(clean(s.get("service_id")), []).append(s)
        if clean(s.get("location_id")):
            schedules_by_location.setdefault(clean(s.get("location_id")), []).append(s)

    areas_by_service: dict[str, list] = {}
    for a in data.get("service_areas") or []:
        areas_by_service.setdefault(clean(a.get("service_id")), []).append(a)

    languages_by_service: dict[str, list[str]] = {}
    languages_by_location: dict[str, list[str]] = {}
    for lang in data.get("languages") or []:
        value = first_nonempty(lang.get("language"), lang.get("name"), lang.get("value"), lang.get("language_code"))
        if not value:
            continue
        sid = clean(lang.get("service_id"))
        lid = clean(lang.get("location_id"))
        if sid:
            languages_by_service.setdefault(sid, []).append(value)
        if lid:
            languages_by_location.setdefault(lid, []).append(value)

    terms = {clean(x.get("id")): x for x in data.get("taxonomy_terms") or []}
    term_names_by_link: dict[str, set[str]] = {}
    for attr in data.get("attributes") or []:
        link_id = clean(attr.get("link_id"))
        term = terms.get(clean(attr.get("taxonomy_term_id")))
        value = attr.get("value")
        if link_id and term and value not in (False, 0, "0", "false", "False", None):
            term_names_by_link.setdefault(link_id, set()).add(clean(term.get("name")))

    index = {
        "organizations": organizations,
        "locations": locations,
        "addresses_by_location": addresses_by_location,
        "service_locations": service_locations,
        "phones_by_service": phones_by_service,
        "phones_by_org": phones_by_org,
        "phones_by_location": phones_by_location,
        "schedules_by_service": schedules_by_service,
        "schedules_by_location": schedules_by_location,
        "areas_by_service": areas_by_service,
        "languages_by_service": languages_by_service,
        "languages_by_location": languages_by_location,
        "term_names_by_link": term_names_by_link,
        "services_by_id": {clean(x.get("id")): x for x in data.get("services") or []},
    }
    _frontdoor_index_cache = (mtime, index)
    return index


def first_nonempty(*values) -> str:
    for value in values:
        value = clean(value)
        if value:
            return value
    return ""


def format_address(a: dict) -> str:
    parts = [clean(a.get("address_1")), clean(a.get("city")), clean(a.get("state_province")), clean(a.get("postal_code"))]
    return ", ".join([p for p in parts if p])


def format_schedule(s: dict) -> str:
    desc = first_nonempty(s.get("description"), s.get("notes"))
    if desc:
        return desc
    days = clean(s.get("byday"))
    opens = clean(s.get("opens_at"))
    closes = clean(s.get("closes_at"))
    return " ".join(x for x in [days, f"{opens}-{closes}" if opens or closes else ""] if x)


def frontdoor_payload(service: dict, index: dict) -> dict:
    sid = clean(service.get("id"))
    org = index["organizations"].get(clean(service.get("organization_id"))) or service.get("organization") or {}
    org_id = clean(org.get("id") or service.get("organization_id"))
    location_ids = index["service_locations"].get(sid, [])
    location = index["locations"].get(location_ids[0], {}) if location_ids else {}
    address_rows = index["addresses_by_location"].get(clean(location.get("id")), []) if location else []
    address_row = address_rows[0] if address_rows else {}
    address = format_address(address_row) if address_row else ""
    postal_code = normalize_zip(address_row.get("postal_code"))

    phones = index["phones_by_service"].get(sid, [])
    if not phones:
        for lid in location_ids:
            phones.extend(index["phones_by_location"].get(lid, []))
    if not phones:
        phones = index["phones_by_org"].get(org_id, [])
    phone = ""
    if phones:
        phone = clean(phones[0].get("number"))
        ext = clean(phones[0].get("extension"))
        if ext:
            phone += f" ext. {ext}"

    schedules = list(index["schedules_by_service"].get(sid, []))
    if not schedules:
        for lid in location_ids:
            schedules.extend(index["schedules_by_location"].get(lid, []))
    hours = "; ".join(filter(None, [format_schedule(x) for x in schedules[:3]]))

    areas = index["areas_by_service"].get(sid, [])
    area_text = "; ".join(filter(None, [first_nonempty(x.get("name"), x.get("description")) for x in areas[:3]]))
    terms = sorted(x for x in index["term_names_by_link"].get(sid, set()) if x)
    category = ", ".join(terms[:4]) or "Community service"
    service_name = first_nonempty(service.get("name"), service.get("alternate_name"))
    organization = first_nonempty(org.get("name"), service_name, "Unknown organization")
    website = first_nonempty(service.get("url"), org.get("website"))
    official_source = website

    langs = list(index.get("languages_by_service", {}).get(sid, []))
    if not langs:
        for lid in location_ids:
            langs.extend(index.get("languages_by_location", {}).get(lid, []))
    languages = ", ".join(dict.fromkeys(clean(x) for x in langs if clean(x)))

    neighborhood = first_nonempty(location.get("name"), area_text)
    return {
        "external_source": "front_door",
        "external_id": sid,
        "organization": organization,
        "service_name": service_name,
        "category": category,
        "phone": phone,
        "website": website,
        "source_url": official_source,
        "address": address,
        "postal_code": postal_code,
        "latitude": to_float(location.get("latitude")),
        "longitude": to_float(location.get("longitude")),
        "neighborhood": neighborhood,
        "hours": hours,
        "languages": languages,
        "service_area": area_text,
        "services": first_nonempty(service.get("description"), service_name),
        "eligibility": clean(service.get("eligibility_description")),
        "application_process": clean(service.get("application_process")),
        "external_status": clean(service.get("status")),
        "external_assured_date": clean(service.get("assured_date")),
        "front_door_categories": terms,
    }


def local_csv_payloads() -> list[dict]:
    if not LOCAL_CSV_PATH.exists():
        return []
    rows: list[dict] = []
    with LOCAL_CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            org = clean(r.get("Organization"))
            if not org:
                continue
            eligibility = " | ".join(
                clean(r.get(k))
                for k in [
                    "Age Restrictions", "Gender restrictions?", "Current Housing Status",
                    "Family or household status", "Insurance status",
                    "Immigration Status / Documentation Required", "Income eligibility",
                    "Other program-specific requirements",
                ]
                if clean(r.get(k))
            )
            rows.append(
                {
                    "external_source": "local_csv",
                    "external_id": normalize_simple(org),
                    "organization": org,
                    "service_name": clean(r.get("Type of Resource")),
                    "category": clean(r.get("Type of Resource")) or "Housing",
                    "phone": clean(r.get("Phone")),
                    "website": clean(r.get("Website")),
                    "source_url": clean(r.get("Website")),
                    "address": clean(r.get("Address")),
                    "postal_code": extract_zip(r.get("Address")),
                    "latitude": None,
                    "longitude": None,
                    "neighborhood": clean(r.get("Neighborhood")),
                    "hours": clean(r.get("Hours")),
                    "languages": clean(r.get("Languages served")),
                    "service_area": clean(r.get("Neighborhood")),
                    "services": clean(r.get("Services")),
                    "eligibility": eligibility,
                    "application_process": clean(r.get("Application Process") or r.get("How to Apply")),
                    "external_status": "",
                    "external_assured_date": "",
                    "legacy": r,
                }
            )
    return rows


def normalize_simple(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", clean(text).lower()).strip("-")


def query_tokens(query: str) -> set[str]:
    raw = {t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) >= 3} - SEARCH_STOPWORDS
    expanded = set(raw)
    for token in list(raw):
        expanded.update(SEARCH_SYNONYMS.get(token, set()))
        if token.endswith("s"):
            expanded.update(SEARCH_SYNONYMS.get(token[:-1], set()))
        else:
            expanded.update(SEARCH_SYNONYMS.get(token + "s", set()))
    return expanded


def search_score(payload: dict, query: str, knowledge_text: str = "", city: str = "Chicago") -> tuple[float, bool]:
    tokens = query_tokens(query)
    if not tokens:
        return 0.0, False
    fields = {
        "organization": (clean(payload.get("organization")).lower(), 4.0),
        "service_name": (clean(payload.get("service_name")).lower(), 5.0),
        "category": (clean(payload.get("category")).lower(), 4.0),
        "services": (clean(payload.get("services")).lower(), 3.0),
        "eligibility": (clean(payload.get("eligibility")).lower(), 2.0),
        "languages": (clean(payload.get("languages")).lower(), 2.5),
        "service_area": (clean(payload.get("service_area")).lower(), 2.0),
        "address": (clean(payload.get("address")).lower(), 1.5),
        "neighborhood": (clean(payload.get("neighborhood")).lower(), 1.5),
        "application": (clean(payload.get("application_process")).lower(), 1.0),
    }
    score = 0.0
    for token in tokens:
        for text, weight in fields.values():
            if token and token in text:
                score += weight
    phrase = query.strip().lower()
    if phrase and any(phrase in text for text, _ in fields.values() if text):
        score += 6
    city_lower = city.lower().strip()
    if city_lower and city_lower in (fields["address"][0] + " " + fields["neighborhood"][0] + " " + fields["service_area"][0]):
        score += 2
    knowledge_match = False
    ktext = knowledge_text.lower()
    if ktext:
        hits = sum(1 for token in tokens if token in ktext)
        if hits:
            score += min(3.0, hits * 1.25)
            knowledge_match = True
    return score, knowledge_match


def frontdoor_zip_centroids(index: dict) -> dict[str, tuple[float, float]]:
    points: dict[str, list[tuple[float, float]]] = {}
    for location_id, addresses in index.get("addresses_by_location", {}).items():
        loc = index.get("locations", {}).get(location_id) or {}
        lat, lon = to_float(loc.get("latitude")), to_float(loc.get("longitude"))
        if lat is None or lon is None:
            continue
        for address in addresses:
            z = normalize_zip(address.get("postal_code"))
            if z:
                points.setdefault(z, []).append((lat, lon))
    return {z: (sum(p[0] for p in pts)/len(pts), sum(p[1] for p in pts)/len(pts)) for z, pts in points.items() if pts}


def load_zip_cache() -> dict:
    if not ZIP_CACHE.exists():
        return {}
    try:
        return json.loads(ZIP_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def lookup_zip_centroid(zip_code: str, frontdoor_map: dict | None = None, allow_network: bool = True) -> tuple[float, float] | None:
    z = normalize_zip(zip_code)
    if not z:
        return None
    if frontdoor_map and z in frontdoor_map:
        return frontdoor_map[z]
    cache = load_zip_cache()
    if z in cache:
        try:
            return float(cache[z]["latitude"]), float(cache[z]["longitude"])
        except Exception:
            pass
    if not allow_network:
        return None
    try:
        body, _, _, _ = request_bytes(ZIP_LOOKUP_URL.format(zip_code=z), timeout=4)
        payload = json.loads(body.decode("utf-8"))
        places = payload.get("places") or []
        if not places:
            return None
        lat, lon = float(places[0]["latitude"]), float(places[0]["longitude"])
        cache[z] = {"latitude": lat, "longitude": lon, "source": "Zippopotam.us"}
        ZIP_CACHE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        return lat, lon
    except Exception:
        return None


def zip_proximity(payload: dict, user_zip: str, user_coords, frontdoor_map: dict | None = None, allow_network: bool = False) -> tuple[float, float | None, bool]:
    z = normalize_zip(payload.get("postal_code") or extract_zip(payload.get("address")))
    service_area = clean(payload.get("service_area"))
    exact = bool(user_zip and (z == user_zip or user_zip in service_area))
    bonus = 24.0 if exact else 0.0
    coords = None
    lat, lon = to_float(payload.get("latitude")), to_float(payload.get("longitude"))
    if lat is not None and lon is not None:
        coords = (lat, lon)
    elif z:
        coords = lookup_zip_centroid(z, frontdoor_map, allow_network=allow_network)
    distance = None
    if user_coords and coords:
        distance = round(haversine_miles(user_coords[0], user_coords[1], coords[0], coords[1]), 1)
        if distance <= 2:
            bonus = max(bonus, 18.0)
        elif distance <= 5:
            bonus = max(bonus, 14.0)
        elif distance <= 10:
            bonus = max(bonus, 10.0)
        elif distance <= 20:
            bonus = max(bonus, 5.0)
        elif distance <= 40:
            bonus = max(bonus, 2.0)
    return bonus, distance, exact


def internal_knowledge_text(conn: sqlite3.Connection, resource_id: int) -> str:
    parts = [
        r[0] for r in conn.execute(
            "SELECT message FROM network_posts WHERE resource_id = ? AND status = 'active' ORDER BY id DESC LIMIT 20",
            (resource_id,),
        ).fetchall()
    ]
    parts.extend(
        r[0] for r in conn.execute(
            "SELECT message FROM corrections WHERE resource_id = ? ORDER BY id DESC LIMIT 20",
            (resource_id,),
        ).fetchall()
    )
    return " ".join(parts)


def row_to_payload(row: sqlite3.Row) -> dict:
    return {
        "external_source": row["external_source"],
        "external_id": row["external_id"],
        "organization": row["organization"],
        "service_name": row["service_name"] or "",
        "category": row["category"] or "",
        "phone": row["phone"] or "",
        "website": row["website"] or "",
        "source_url": row["source_url"] or row["website"] or "",
        "address": row["address"] or "",
        "postal_code": row["postal_code"] or extract_zip(row["address"]),
        "latitude": row["latitude"],
        "longitude": row["longitude"],
        "neighborhood": row["neighborhood"] or "",
        "hours": row["hours"] or "",
        "languages": row["languages"] or "",
        "service_area": row["service_area"] or "",
        "services": row["services"] or "",
        "eligibility": row["eligibility"] or "",
        "application_process": row["application_process"] or "",
        "external_status": row["external_status"] or "",
        "external_assured_date": row["external_assured_date"] or "",
    }


def discovery_search(query: str, limit: int = 12, city: str = "Chicago", zip_code: str = "") -> dict:
    q = query.strip()
    requested_zip = normalize_zip(zip_code)
    if not requested_zip:
        inline_zip = extract_zip(q)
        if inline_zip:
            requested_zip = inline_zip
            q = re.sub(rf"\b{re.escape(inline_zip)}(?:-\d{{4}})?\b", " ", q).strip()
    if not q and not requested_zip:
        return {"results": [], "external": {"available": False, "message": "Enter a need, ZIP code, or both."}, "location": {"zip_code": ""}}

    candidates: dict[str, dict] = {}
    external_meta = {"available": False, "error": ""}

    # Load Front Door once so the same catalog can support both discovery and ZIP centroids.
    fd_data, external_meta = load_frontdoor_data(allow_refresh=True)
    fd_idx = frontdoor_indices(fd_data) if fd_data else None
    fd_zip_map = frontdoor_zip_centroids(fd_idx) if fd_idx else {}
    user_coords = lookup_zip_centroid(requested_zip, fd_zip_map, allow_network=True) if requested_zip else None

    with db() as conn:
        # 1) ResourceBridge internal memory.
        internal_rows = conn.execute("SELECT * FROM resources ORDER BY id DESC").fetchall()
        for row in internal_rows:
            payload = row_to_payload(row)
            score, knowledge_match = search_score(payload, q, internal_knowledge_text(conn, row["id"]), city) if q else (0.0, False)
            zip_bonus, distance, exact_zip = zip_proximity(payload, requested_zip, user_coords, fd_zip_map, allow_network=False) if requested_zip else (0.0, None, False)
            if q and score <= 0:
                continue
            if not q and requested_zip and zip_bonus <= 0 and distance is None:
                continue
            key = f"{payload['external_source']}:{payload['external_id']}"
            candidates[key] = {
                **payload,
                "score": score + zip_bonus + 1.5,
                "text_score": score,
                "zip_bonus": zip_bonus,
                "distance_miles": distance,
                "zip_match": exact_zip,
                "source_label": "ResourceBridge memory",
                "was_known": True,
                "knowledge_match": knowledge_match,
                "resource_id": row["id"],
            }

        # 2) Legacy local housing list as seed catalog.
        for payload in local_csv_payloads():
            key = f"{payload['external_source']}:{payload['external_id']}"
            if key in candidates:
                continue
            score, knowledge_match = search_score(payload, q, "", city) if q else (0.0, False)
            zip_bonus, distance, exact_zip = zip_proximity(payload, requested_zip, user_coords, fd_zip_map, allow_network=False) if requested_zip else (0.0, None, False)
            if q and score <= 0:
                continue
            if not q and requested_zip and zip_bonus <= 0 and distance is None:
                continue
            candidates[key] = {
                **payload, "score": score + zip_bonus, "text_score": score, "zip_bonus": zip_bonus,
                "distance_miles": distance, "zip_match": exact_zip, "source_label": "Local housing seed",
                "was_known": False, "knowledge_match": knowledge_match, "resource_id": None,
            }

        # 3) Front Door Illinois sample (or partner dataset if a key is configured).
        if fd_data and fd_idx:
            for service in fd_data.get("services") or []:
                payload = frontdoor_payload(service, fd_idx)
                key = f"front_door:{payload['external_id']}"
                if key in candidates:
                    continue
                score, knowledge_match = search_score(payload, q, "", city) if q else (0.0, False)
                zip_bonus, distance, exact_zip = zip_proximity(payload, requested_zip, user_coords, fd_zip_map, allow_network=False) if requested_zip else (0.0, None, False)
                if q and score <= 0:
                    continue
                if not q and requested_zip and zip_bonus <= 0 and distance is None:
                    continue
                candidates[key] = {
                    **payload, "score": score + zip_bonus, "text_score": score, "zip_bonus": zip_bonus,
                    "distance_miles": distance, "zip_match": exact_zip, "source_label": "Front Door Illinois",
                    "was_known": False, "knowledge_match": knowledge_match, "resource_id": None,
                }

        # Resolve approximate ZIP-centroid distance for the strongest local candidates when needed.
        if requested_zip and user_coords:
            prelim = sorted(candidates.values(), key=lambda x: (-x["score"], x["organization"].lower()))[:60]
            network_budget = 12
            resolved: dict[str, tuple[float, float] | None] = {}
            for result in prelim:
                if result.get("distance_miles") is not None or result.get("zip_match"):
                    continue
                z = normalize_zip(result.get("postal_code"))
                if not z:
                    continue
                if z not in resolved:
                    cached_coords = lookup_zip_centroid(z, fd_zip_map, allow_network=False)
                    if cached_coords:
                        coords = cached_coords
                    elif network_budget > 0:
                        coords = lookup_zip_centroid(z, fd_zip_map, allow_network=True)
                        if coords:
                            network_budget -= 1
                    else:
                        coords = None
                    resolved[z] = coords
                coords = resolved.get(z)
                if coords:
                    distance = round(haversine_miles(user_coords[0], user_coords[1], coords[0], coords[1]), 1)
                    bonus, _, _ = zip_proximity({**result, "latitude": coords[0], "longitude": coords[1]}, requested_zip, user_coords, fd_zip_map, allow_network=False)
                    result["distance_miles"] = distance
                    result["zip_bonus"] = max(result.get("zip_bonus", 0), bonus)
                    result["score"] = result.get("text_score", 0) + result["zip_bonus"] + (1.5 if result.get("was_known") else 0)

        ordered = sorted(
            candidates.values(),
            key=lambda x: (
                -x["score"],
                x.get("distance_miles") if x.get("distance_miles") is not None else 9999,
                x["organization"].lower(),
            ),
        )[: max(1, min(limit, 30))]

        # Encountered search results enter ResourceBridge. Existing trusted fields are not overwritten.
        serialized = []
        for result in ordered:
            row, was_known_before = upsert_resource(conn, result, preserve_trusted=True)
            result["was_known"] = result["was_known"] or was_known_before
            result["new_to_resourcebridge"] = not was_known_before
            result["resource_id"] = row["id"]
            result["counts"] = resource_counts(conn, row["id"])
            result["flags"] = unresolved_flags(conn, row["id"])
            result.pop("legacy", None)
            serialized.append(result)
        conn.commit()

    return {
        "results": serialized,
        "external": external_meta,
        "location": {
            "zip_code": requested_zip,
            "resolved": bool(user_coords),
            "method": "ZIP centroid; approximate distance only" if requested_zip else "",
        },
    }


# -------------------- Deterministic freshness checker --------------------


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if len(digits) >= 10 else digits


def normalize_compare(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", clean(value).lower()).strip()


def normalize_url(value: str) -> str:
    value = clean(value)
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else "https://" + value)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = (parsed.path or "/").rstrip("/") or "/"
    return f"{host}{path}"


def values_differ(field: str, stored: str, observed: str) -> bool:
    if not clean(stored) or not clean(observed):
        return False
    if field == "phone":
        return bool(normalize_phone(stored) and normalize_phone(observed) and normalize_phone(stored) != normalize_phone(observed))
    if field in {"website", "source_url"}:
        return normalize_url(stored) != normalize_url(observed)
    if field == "postal_code":
        return bool(normalize_zip(stored) and normalize_zip(observed) and normalize_zip(stored) != normalize_zip(observed))
    return normalize_compare(stored) != normalize_compare(observed)


def signal_priority(signal: str) -> int:
    signal = clean(signal)
    if signal == "external_structured_change" or "structured_" in signal:
        return 100
    if signal in {"stored_phone_not_on_official_page", "official_site_missing", "official_site_redirected_domain"}:
        return 95
    if signal in {"official_page_status_language", "external_record_missing", "local_seed_record_missing"}:
        return 90
    if signal.startswith("official_page_keyword_"):
        return 75
    if "concept_mismatch" in signal:
        return 80
    if "missing_from_record" in signal:
        return 65
    if signal in {"external_source_old", "aged_time_sensitive_note"}:
        return 55
    return 50


def create_flag(
    conn: sqlite3.Connection,
    *,
    resource_id: int,
    field: str,
    signal: str,
    reason: str,
    stored_value: str = "",
    observed_value: str = "",
    evidence_url: str = "",
    note_id: int | None = None,
) -> tuple[int, bool]:
    # Keep the UI actionable: one unresolved warning per resource field (or per CHW note).
    # If stronger evidence arrives later in the same check, update the existing flag instead
    # of stacking several warnings that all point to the same field.
    existing = conn.execute(
        """SELECT id, signal, observed_value FROM flags
           WHERE resource_id = ? AND COALESCE(note_id,0) = COALESCE(?,0)
             AND field = ? AND status = 'unresolved'
           ORDER BY id DESC LIMIT 1""",
        (resource_id, note_id, field),
    ).fetchone()
    if existing:
        if existing["signal"] == signal and clean(existing["observed_value"]) == clean(observed_value):
            return existing["id"], False
        if signal_priority(signal) >= signal_priority(existing["signal"]):
            conn.execute(
                """UPDATE flags SET signal=?, reason=?, stored_value=?, observed_value=?, evidence_url=?, detected_at=?
                   WHERE id=?""",
                (signal, reason, clean(stored_value), clean(observed_value), clean(evidence_url), utc_now(), existing["id"]),
            )
        return existing["id"], False
    cur = conn.execute(
        """INSERT INTO flags (
            resource_id, note_id, field, signal, reason, stored_value, observed_value,
            evidence_url, detected_at, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'unresolved')""",
        (
            resource_id, note_id, field, signal, reason, clean(stored_value), clean(observed_value),
            clean(evidence_url), utc_now(),
        ),
    )
    return cur.lastrowid, True


class VisibleTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.ignore_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.ignore_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "noscript", "svg"} and self.ignore_depth:
            self.ignore_depth -= 1

    def handle_data(self, data):
        if not self.ignore_depth:
            text = re.sub(r"\s+", " ", data).strip()
            if text:
                self.parts.append(text)


def html_to_text(raw: str) -> str:
    parser = VisibleTextParser()
    try:
        parser.feed(raw)
    except Exception:
        return re.sub(r"<[^>]+>", " ", raw)
    return " ".join(parser.parts)


def extract_phones(text: str) -> list[str]:
    pattern = re.compile(r"(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}")
    seen = []
    for match in pattern.findall(text or ""):
        normalized = normalize_phone(match)
        if len(normalized) == 10 and normalized not in seen:
            seen.append(normalized)
    return seen


def context_snippet(text: str, start: int, end: int, radius: int = 135) -> str:
    raw = re.sub(r"\s+", " ", clean(text)).strip()
    if not raw:
        return ""
    left = max(0, start - radius)
    right = min(len(raw), end + radius)
    snippet = raw[left:right].strip(" -–—|•;,")
    if left > 0:
        snippet = "…" + snippet
    if right < len(raw):
        snippet += "…"
    return snippet[:420]


def scan_website_keyword_signals(text: str) -> list[dict]:
    visible = re.sub(r"\s+", " ", clean(text)).strip()
    hits: list[dict] = []
    seen_fields: set[str] = set()
    for pattern, field, reason in WEBSITE_CHANGE_PATTERNS:
        if field in seen_fields:
            continue
        match = pattern.search(visible)
        if not match:
            continue
        seen_fields.add(field)
        hits.append({
            "field": field,
            "reason": reason,
            "matched_text": match.group(0),
            "snippet": context_snippet(visible, match.start(), match.end()),
        })
    return hits


def _walk_json(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _format_structured_address(value) -> str:
    if isinstance(value, str):
        return clean(value)
    if not isinstance(value, dict):
        return ""
    parts = [value.get("streetAddress"), value.get("addressLocality"), value.get("addressRegion"), value.get("postalCode")]
    return ", ".join(clean(x) for x in parts if clean(x))


def extract_structured_web_facts(raw_html: str) -> dict:
    facts = {"phones": [], "addresses": [], "postal_codes": [], "hours": [], "urls": [], "languages": []}
    blocks = re.findall(r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", raw_html or "", flags=re.I | re.S)
    for block in blocks:
        try:
            payload = json.loads(block.strip())
        except Exception:
            continue
        for obj in _walk_json(payload):
            phone = clean(obj.get("telephone"))
            if phone:
                n = normalize_phone(phone)
                if n and n not in facts["phones"]:
                    facts["phones"].append(n)
            url = clean(obj.get("url"))
            if url and url not in facts["urls"]:
                facts["urls"].append(url)
            addr = _format_structured_address(obj.get("address"))
            if addr and addr not in facts["addresses"]:
                facts["addresses"].append(addr)
                z = extract_zip(addr)
                if z and z not in facts["postal_codes"]:
                    facts["postal_codes"].append(z)
            z = normalize_zip(obj.get("postalCode"))
            if z and z not in facts["postal_codes"]:
                facts["postal_codes"].append(z)
            opening = obj.get("openingHours")
            if isinstance(opening, str):
                opening = [opening]
            if isinstance(opening, list):
                for item in opening:
                    item = clean(item)
                    if item and item not in facts["hours"]:
                        facts["hours"].append(item)
            specs = obj.get("openingHoursSpecification")
            if isinstance(specs, dict):
                specs = [specs]
            if isinstance(specs, list):
                for spec in specs:
                    if not isinstance(spec, dict):
                        continue
                    days = spec.get("dayOfWeek")
                    if isinstance(days, list):
                        days = ", ".join(clean(x).split("/")[-1] for x in days)
                    else:
                        days = clean(days).split("/")[-1]
                    line = " ".join(x for x in [clean(days), clean(spec.get("opens")), clean(spec.get("closes"))] if x)
                    if line and line not in facts["hours"]:
                        facts["hours"].append(line)
            langs = obj.get("availableLanguage") or obj.get("inLanguage")
            if isinstance(langs, (str, dict)):
                langs = [langs]
            if isinstance(langs, list):
                for lang in langs:
                    if isinstance(lang, dict):
                        lang = first_nonempty(lang.get("name"), lang.get("alternateName"))
                    lang = clean(lang)
                    if lang and lang not in facts["languages"]:
                        facts["languages"].append(lang)
    # tel: links are often more reliable than visible formatting.
    for phone in re.findall(r"href=[\"']tel:([^\"']+)", raw_html or "", flags=re.I):
        n = normalize_phone(phone)
        if n and n not in facts["phones"]:
            facts["phones"].append(n)
    return facts


def explicit_eligibility_snippet(text: str) -> str:
    patterns = [
        r"\b(?:eligibility|eligible|who can apply|who qualifies|requirements?)\b",
        r"\b(?:must be|only serves?|exclusively for)\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, text or "", re.I)
        if m:
            snippet = context_snippet(text, m.start(), m.end(), radius=180)
            if re.search(r"\b(?:must|only|eligible|qualif|age|income|resident|veteran|family|families|women|men|youth|children|required)\b", snippet, re.I):
                return snippet
    return ""


def explicit_intake_snippet(text: str) -> str:
    m = re.search(r"\b(?:intake|apply|application|waitlist|waiting list|walk[- ]?in|appointment)\b", text or "", re.I)
    return context_snippet(text, m.start(), m.end(), radius=170) if m else ""


def format_phone_number(value: str) -> str:
    n = normalize_phone(value)
    return f"{n[:3]}-{n[3:6]}-{n[6:]}" if len(n) == 10 else clean(value)


def schedule_signature(text: str) -> tuple[set[str], set[int]]:
    raw = clean(text).lower()
    day_aliases = {
        "mon":"mon","monday":"mon","mo":"mon", "tue":"tue","tues":"tue","tuesday":"tue","tu":"tue",
        "wed":"wed","wednesday":"wed","we":"wed", "thu":"thu","thur":"thu","thurs":"thu","thursday":"thu","th":"thu",
        "fri":"fri","friday":"fri","fr":"fri", "sat":"sat","saturday":"sat","sa":"sat", "sun":"sun","sunday":"sun","su":"sun",
    }
    days=set()
    for token in re.findall(r"\b[a-z]{2,9}\b", raw):
        if token in day_aliases:
            days.add(day_aliases[token])
    times=set()
    for h,m,ampm in re.findall(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", raw):
        hour=int(h); minute=int(m or 0)
        if minute>59 or hour>23:
            continue
        if ampm:
            if hour==12: hour=0
            if ampm=="pm": hour+=12
        times.add(hour*60+minute)
    return days,times


def eligibility_concepts(text: str) -> set[str]:
    raw=normalize_compare(text)
    concepts=set()
    patterns={
        "veteran": r"\bveterans?\b", "family": r"\bfamil(?:y|ies)\b", "children": r"\b(?:child|children|kids?)\b",
        "women": r"\bwom(?:an|en)\b", "men": r"\bmen\b", "youth": r"\b(?:youth|teen|adolescent)\b",
        "senior": r"\b(?:senior|older adult|elderly)\b", "income": r"\b(?:income|poverty|ami)\b",
        "resident": r"\b(?:resident|residency)\b", "insurance": r"\b(?:medicaid|medicare|insurance|uninsured)\b",
        "documentation": r"\b(?:documentation|immigration|citizen|citizenship)\b",
    }
    for name,pattern in patterns.items():
        if re.search(pattern,raw,re.I): concepts.add(name)
    for age in re.findall(r"\bage\s*(\d{1,2})\+?\b|\b(\d{1,2})\s*(?:years? old|and older|or older)\b",raw,re.I):
        value=next((x for x in age if x),"")
        if value: concepts.add(f"age:{value}")
    return concepts


def fetch_website(url: str) -> dict:
    url = clean(url)
    if not url:
        return {"ok": False, "error": "no website URL"}
    if "://" not in url:
        url = "https://" + url
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with urlopen(req, timeout=WEBSITE_TIMEOUT_SECONDS) as response:
            content_type = (response.headers.get("Content-Type") or "").lower()
            body = response.read(MAX_PAGE_BYTES)
            charset = response.headers.get_content_charset() or "utf-8"
            raw = body.decode(charset, errors="replace") if "text" in content_type or "html" in content_type else ""
            text = html_to_text(raw)
            normalized = re.sub(r"\s+", " ", text).strip().lower()
            return {
                "ok": True,
                "status": int(getattr(response, "status", 200)),
                "final_url": response.geturl(),
                "content_type": content_type,
                "text": text,
                "normalized_text": normalized[:80_000],
                "structured_facts": extract_structured_web_facts(raw),
                "hash": hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else "",
            }
    except HTTPError as exc:
        return {"ok": False, "status": exc.code, "final_url": exc.geturl(), "error": f"HTTP {exc.code}"}
    except URLError as exc:
        return {"ok": False, "error": clean(getattr(exc, "reason", exc))}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def record_check(conn: sqlite3.Connection, resource_id: int, source: str, outcome: str, details: dict | str = "") -> None:
    conn.execute(
        "INSERT INTO freshness_checks(resource_id, checked_at, source, outcome, details) VALUES (?, ?, ?, ?, ?)",
        (resource_id, utc_now(), source, outcome, json.dumps(details, ensure_ascii=False) if isinstance(details, dict) else clean(details)),
    )


def compare_structured_source(conn: sqlite3.Connection, row: sqlite3.Row, observed: dict, evidence_url: str) -> int:
    created = 0
    field_map = {
        "phone": ("Phone number", "external_structured_change"),
        "website": ("Website", "external_structured_change"),
        "address": ("Address", "external_structured_change"),
        "postal_code": ("ZIP code", "external_structured_change"),
        "hours": ("Hours", "external_structured_change"),
        "languages": ("Languages", "external_structured_change"),
        "eligibility": ("Eligibility requirements", "external_structured_change"),
        "external_status": ("Program status", "external_structured_change"),
    }
    for db_field, (label, signal) in field_map.items():
        stored_value = clean(row[db_field])
        observed_value = clean(observed.get(db_field))
        if values_differ(db_field, stored_value, observed_value):
            _, is_new = create_flag(
                conn,
                resource_id=row["id"],
                field=label,
                signal=signal,
                reason=f"The current external source differs from the value stored in ResourceBridge for {label.lower()}.",
                stored_value=stored_value,
                observed_value=observed_value,
                evidence_url=evidence_url,
            )
            created += int(is_new)

    assured = clean(observed.get("external_assured_date") or row["external_assured_date"])
    if assured:
        try:
            date = datetime.fromisoformat(assured[:10]).replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - date).days
            if age_days >= SOURCE_STALE_DAYS:
                _, is_new = create_flag(
                    conn,
                    resource_id=row["id"],
                    field="Verification age",
                    signal="external_source_old",
                    reason=f"The external source says this record was last assured {age_days} days ago.",
                    stored_value=assured,
                    observed_value=f"{age_days} days since assured date",
                    evidence_url=evidence_url,
                )
                created += int(is_new)
        except ValueError:
            pass

    conn.execute(
        "UPDATE resources SET latest_external_payload = ?, last_external_checked = ? WHERE id = ?",
        (json.dumps(observed, ensure_ascii=False), utc_now(), row["id"]),
    )
    return created


def check_official_website(conn: sqlite3.Connection, row: sqlite3.Row) -> int:
    url = first_nonempty(row["source_url"], row["website"])
    if not url:
        record_check(conn, row["id"], "official_website", "skipped", {"reason": "no website URL"})
        return 0

    result = fetch_website(url)
    conn.execute("UPDATE resources SET last_website_checked = ? WHERE id = ?", (utc_now(), row["id"]))
    created = 0
    if not result.get("ok"):
        status = result.get("status")
        if status in {404, 410}:
            _, is_new = create_flag(
                conn, resource_id=row["id"], field="Website", signal="official_site_missing",
                reason=f"The stored official resource page returned HTTP {status}.",
                stored_value=url, observed_value=f"HTTP {status}", evidence_url=url,
            )
            created += int(is_new)
            record_check(conn, row["id"], "official_website", "missing", result)
        else:
            record_check(conn, row["id"], "official_website", "inconclusive_connection_error", result)
        return created

    final_url = clean(result.get("final_url"))
    evidence_url = final_url or url
    original_host = (urlparse(url if "://" in url else "https://" + url).hostname or "").lower().removeprefix("www.")
    final_host = (urlparse(final_url).hostname or "").lower().removeprefix("www.")
    if original_host and final_host and original_host != final_host:
        _, is_new = create_flag(
            conn, resource_id=row["id"], field="Website", signal="official_site_redirected_domain",
            reason="The stored official website now redirects to a different domain.",
            stored_value=url, observed_value=final_url, evidence_url=evidence_url,
        )
        created += int(is_new)

    page_text = clean(result.get("text"))
    lower = page_text.lower()
    facts = result.get("structured_facts") or {}

    # 1) Explicit high-confidence status / availability language.
    for phrase, (field, reason) in CLOSURE_SIGNALS.items():
        if phrase in lower:
            _, is_new = create_flag(
                conn, resource_id=row["id"], field=field, signal="official_page_status_language",
                reason=reason, stored_value=clean(row["external_status"]),
                observed_value=context_snippet(page_text, lower.find(phrase), lower.find(phrase)+len(phrase)), evidence_url=evidence_url,
            )
            created += int(is_new)

    # 2) Field-specific change language across the entire visible page.
    keyword_hits = scan_website_keyword_signals(page_text)
    label_to_db_field = {
        "Phone number": "phone", "Hours": "hours", "Address": "address", "ZIP code": "postal_code",
        "Website": "website", "Languages": "languages", "Eligibility requirements": "eligibility",
        "Program status": "external_status", "Availability / waitlist": "external_status",
    }
    for hit in keyword_hits:
        field = hit["field"]
        db_field = label_to_db_field.get(field)
        stored_value = clean(row[db_field]) if db_field else ""
        _, is_new = create_flag(
            conn, resource_id=row["id"], field=field,
            signal=f"official_page_keyword_{re.sub(r'[^a-z0-9]+', '_', field.lower()).strip('_')}",
            reason=hit["reason"], stored_value=stored_value,
            observed_value=hit["snippet"] or hit["matched_text"], evidence_url=evidence_url,
        )
        created += int(is_new)

    # 3) Phone values from visible text, tel: links, and JSON-LD.
    stored_phone = normalize_phone(clean(row["phone"]))
    page_phones = list(dict.fromkeys(extract_phones(page_text) + list(facts.get("phones") or [])))
    if stored_phone and page_phones and stored_phone not in page_phones:
        pretty = ", ".join(format_phone_number(x) for x in page_phones[:3])
        _, is_new = create_flag(
            conn, resource_id=row["id"], field="Phone number", signal="stored_phone_not_on_official_page",
            reason="The stored phone number was not found on the official page, but other phone numbers were published there.",
            stored_value=clean(row["phone"]), observed_value=pretty, evidence_url=evidence_url,
        )
        created += int(is_new)

    # 4) Address / ZIP values from structured official-page metadata.
    structured_addresses = [clean(x) for x in facts.get("addresses") or [] if clean(x)]
    structured_zips = list(dict.fromkeys(normalize_zip(x) for x in facts.get("postal_codes") or [] if normalize_zip(x)))
    stored_zip = normalize_zip(row["postal_code"] or extract_zip(row["address"]))
    if stored_zip and structured_zips and stored_zip not in structured_zips and len(structured_zips) <= 2:
        _, is_new = create_flag(
            conn, resource_id=row["id"], field="ZIP code", signal="official_structured_zip_mismatch",
            reason="The official page publishes a ZIP code that differs from the ZIP stored in ResourceBridge.",
            stored_value=stored_zip, observed_value=", ".join(structured_zips), evidence_url=evidence_url,
        )
        created += int(is_new)
    if clean(row["address"]) and len(structured_addresses) == 1:
        official_address = structured_addresses[0]
        stored_norm = normalize_compare(row["address"])
        official_norm = normalize_compare(official_address)
        stored_number = re.match(r"\s*(\d+)", clean(row["address"]))
        official_number = re.match(r"\s*(\d+)", official_address)
        if stored_number and official_number and stored_number.group(1) != official_number.group(1):
            _, is_new = create_flag(
                conn, resource_id=row["id"], field="Address", signal="official_structured_address_mismatch",
                reason="The official page publishes a structured street address with a different street number.",
                stored_value=clean(row["address"]), observed_value=official_address, evidence_url=evidence_url,
            )
            created += int(is_new)
        elif stored_zip and extract_zip(official_address) and stored_zip != extract_zip(official_address):
            pass  # ZIP-specific flag above is more precise.

    # 5) Structured hours and languages. We compare only when the page explicitly publishes these facts.
    official_hours = [clean(x) for x in facts.get("hours") or [] if clean(x)]
    if official_hours:
        official_hours_text = "; ".join(official_hours[:3])
        if not clean(row["hours"]):
            _, is_new = create_flag(
                conn, resource_id=row["id"], field="Hours", signal="official_hours_missing_from_record",
                reason="The official page publishes structured hours, but ResourceBridge currently has no hours stored.",
                stored_value="Not stated", observed_value=official_hours_text, evidence_url=evidence_url,
            )
            created += int(is_new)
        else:
            stored_days, stored_times = schedule_signature(row["hours"])
            official_days, official_times = schedule_signature(official_hours_text)
            if stored_times and official_times and stored_times != official_times:
                _, is_new = create_flag(
                    conn, resource_id=row["id"], field="Hours", signal="official_structured_hours_mismatch",
                    reason="Structured hours published on the official page differ from the hours stored in ResourceBridge.",
                    stored_value=clean(row["hours"]), observed_value=official_hours_text, evidence_url=evidence_url,
                )
                created += int(is_new)
    official_languages = [clean(x) for x in facts.get("languages") or [] if clean(x)]
    if official_languages:
        stored_languages = normalize_compare(row["languages"])
        missing = [x for x in official_languages if normalize_compare(x) not in stored_languages]
        if missing and (not stored_languages or len(missing) == len(official_languages)):
            _, is_new = create_flag(
                conn, resource_id=row["id"], field="Languages", signal="official_language_information_differs",
                reason="The official page publishes language-access information that is not reflected in the stored record.",
                stored_value=clean(row["languages"]) or "Not stated", observed_value=", ".join(official_languages[:5]), evidence_url=evidence_url,
            )
            created += int(is_new)

    # 6) Eligibility and intake evidence when the record is missing that information.
    eligibility_snippet = explicit_eligibility_snippet(page_text)
    if eligibility_snippet:
        if not clean(row["eligibility"]):
            _, is_new = create_flag(
                conn, resource_id=row["id"], field="Eligibility requirements", signal="official_eligibility_missing_from_record",
                reason="The official page contains specific eligibility information, but ResourceBridge currently lists eligibility as not stated.",
                stored_value="Not stated", observed_value=eligibility_snippet, evidence_url=evidence_url,
            )
            created += int(is_new)
        else:
            official_terms=eligibility_concepts(eligibility_snippet)
            stored_terms=eligibility_concepts(row["eligibility"])
            new_terms=official_terms-stored_terms
            if new_terms and re.search(r"\b(?:must|only|eligib|qualif|required|requirements?)\b",eligibility_snippet,re.I):
                _, is_new = create_flag(
                    conn, resource_id=row["id"], field="Eligibility requirements", signal="official_eligibility_concept_mismatch",
                    reason="The official page contains a specific eligibility restriction not represented in the stored eligibility summary.",
                    stored_value=clean(row["eligibility"]), observed_value=eligibility_snippet, evidence_url=evidence_url,
                )
                created += int(is_new)
    intake_snippet = explicit_intake_snippet(page_text)
    if intake_snippet and not clean(row["application_process"]) and re.search(r"\b(?:closed|paused|suspended|waitlist|walk[- ]?in|appointment|required|apply|application)\b", intake_snippet, re.I):
        _, is_new = create_flag(
            conn, resource_id=row["id"], field="Availability / waitlist", signal="official_intake_information_missing_from_record",
            reason="The official page contains intake or application information that is not reflected in the stored record.",
            stored_value="Not stated", observed_value=intake_snippet, evidence_url=evidence_url,
        )
        created += int(is_new)

    # 7) Snapshot the page. A changed page alone is deliberately not a flag.
    previous = conn.execute("SELECT * FROM page_snapshots WHERE resource_id = ?", (row["id"],)).fetchone()
    current_hash = clean(result.get("hash"))
    current_text = clean(result.get("normalized_text"))[:60_000]
    changed = bool(previous and previous["content_hash"] and current_hash and previous["content_hash"] != current_hash)
    conn.execute(
        """INSERT INTO page_snapshots(resource_id, url, content_hash, content_text, checked_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(resource_id) DO UPDATE SET
            url = excluded.url, content_hash = excluded.content_hash,
            content_text = excluded.content_text, checked_at = excluded.checked_at""",
        (row["id"], evidence_url, current_hash, current_text, utc_now()),
    )
    record_check(
        conn, row["id"], "official_website", "changed_no_direct_claim" if changed and not created else "ok",
        {
            "status": result.get("status"), "final_url": final_url, "page_changed": changed,
            "strong_flags_created": created,
            "checked": ["reachability", "redirects", "phone", "address", "ZIP", "hours", "languages", "eligibility", "intake/waitlist", "program status", "page snapshot"],
            "structured_facts": {k: v[:5] if isinstance(v, list) else v for k, v in facts.items()},
            "keyword_signals": [{"field": h["field"], "matched_text": h["matched_text"], "snippet": h["snippet"]} for h in keyword_hits],
        },
    )
    return created


def flag_aged_notes(conn: sqlite3.Connection, resource_id: int) -> int:
    rows = conn.execute(
        "SELECT id, message, created_at FROM network_posts WHERE resource_id = ? AND status = 'active' AND category = 'Note'",
        (resource_id,),
    ).fetchall()
    created = 0
    now = datetime.now(timezone.utc)
    for note in rows:
        created_at = parse_dt(note["created_at"])
        if not created_at:
            continue
        age = (now - created_at).days
        lower = note["message"].lower()
        if age >= NOTE_STALE_DAYS and any(term in lower for term in VOLATILE_NOTE_TERMS):
            _, is_new = create_flag(
                conn,
                resource_id=resource_id,
                note_id=note["id"],
                field="CHW note",
                signal="aged_time_sensitive_note",
                reason=f"This CHW note is {age} days old and contains time-sensitive language.",
                stored_value=note["message"][:240],
                observed_value=f"{age} days old",
            )
            created += int(is_new)
    return created


def run_freshness_checks(force_frontdoor_refresh: bool = True) -> dict:
    init_db()
    started = utc_now()
    fd_data, fd_meta = load_frontdoor_data(allow_refresh=True, force_refresh=force_frontdoor_refresh)
    fd_index = frontdoor_indices(fd_data) if fd_data else None
    local_by_id = {p["external_id"]: p for p in local_csv_payloads()}

    summary = {
        "started_at": started,
        "resources_checked": 0,
        "flags_created": 0,
        "website_checks": 0,
        "source_checks": 0,
        "note_checks": 0,
        "front_door": fd_meta,
        "errors": [],
    }

    with db() as conn:
        resources = conn.execute("SELECT * FROM resources ORDER BY id").fetchall()
        for row in resources:
            summary["resources_checked"] += 1
            try:
                observed = None
                evidence_url = ""
                if row["external_source"] == "front_door":
                    summary["source_checks"] += 1
                    if fd_data and fd_index:
                        service = fd_index["services_by_id"].get(row["external_id"])
                        if service:
                            observed = frontdoor_payload(service, fd_index)
                            evidence_url = FRONT_DOOR_SERVICE_URL.format(service_id=row["external_id"])
                        else:
                            # Absence from the free ~1-in-10 sample is not evidence that a
                            # service disappeared. Only a partner/full dataset can support
                            # a missing-record flag.
                            if FRONT_DOOR_API_KEY:
                                _, is_new = create_flag(
                                    conn,
                                    resource_id=row["id"],
                                    field="Program status",
                                    signal="external_record_missing",
                                    reason="This previously stored Front Door service was not found in the latest full dataset.",
                                    stored_value=row["external_id"],
                                    observed_value="not found in latest full dataset",
                                    evidence_url="https://localcarehub.org/open-data/illinois",
                                )
                                summary["flags_created"] += int(is_new)
                                record_check(conn, row["id"], "front_door", "record_missing", {"external_id": row["external_id"]})
                            else:
                                record_check(conn, row["id"], "front_door", "not_in_current_free_sample_inconclusive", {"external_id": row["external_id"]})
                elif row["external_source"] == "local_csv":
                    summary["source_checks"] += 1
                    observed = local_by_id.get(row["external_id"])
                    evidence_url = first_nonempty(row["website"], row["source_url"])
                    if not observed:
                        _, is_new = create_flag(
                            conn,
                            resource_id=row["id"],
                            field="Program status",
                            signal="local_seed_record_missing",
                            reason="This resource was previously in the local housing seed file but is no longer present.",
                            stored_value=row["organization"],
                            observed_value="not found in current local seed",
                            evidence_url=evidence_url,
                        )
                        summary["flags_created"] += int(is_new)
                        record_check(conn, row["id"], "local_csv", "record_missing", {"external_id": row["external_id"]})

                if observed:
                    new_flags = compare_structured_source(conn, row, observed, evidence_url)
                    summary["flags_created"] += new_flags
                    record_check(conn, row["id"], row["external_source"], "compared", {"new_flags": new_flags})

                summary["website_checks"] += 1
                summary["flags_created"] += check_official_website(conn, row)
                summary["note_checks"] += 1
                summary["flags_created"] += flag_aged_notes(conn, row["id"])
                conn.commit()
            except Exception as exc:
                conn.rollback()
                summary["errors"].append({"resource_id": row["id"], "organization": row["organization"], "error": str(exc)})
                try:
                    record_check(conn, row["id"], "freshness", "error", {"error": str(exc)})
                    conn.commit()
                except Exception:
                    conn.rollback()

        setting_set(conn, "last_freshness_run", utc_now())
        conn.commit()

    summary["finished_at"] = utc_now()
    return summary


def freshness_status() -> dict:
    with db() as conn:
        last = setting_get(conn, "last_freshness_run")
        unresolved = conn.execute("SELECT COUNT(*) FROM flags WHERE status = 'unresolved'").fetchone()[0]
        resources = conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
        oldest = conn.execute("SELECT MIN(first_seen) FROM resources").fetchone()[0] if resources else None
    due = False
    dt = parse_dt(last)
    if dt:
        due = datetime.now(timezone.utc) - dt >= timedelta(days=FRESHNESS_INTERVAL_DAYS)
    elif resources and oldest:
        first_seen = parse_dt(oldest)
        due = bool(first_seen and datetime.now(timezone.utc) - first_seen >= timedelta(days=FRESHNESS_INTERVAL_DAYS))
    _, fd_meta = load_frontdoor_data(allow_refresh=False)
    return {
        "last_run": last,
        "due": due,
        "interval_days": FRESHNESS_INTERVAL_DAYS,
        "unresolved_flags": unresolved,
        "resources": resources,
        "front_door": fd_meta,
    }


def background_freshness_loop() -> None:
    # Give the server time to start, then check periodically. The manual endpoint
    # and weekly_freshness.py remain available for demos / Windows Task Scheduler.
    time.sleep(3)
    while True:
        try:
            status = freshness_status()
            if status["resources"] and status["due"]:
                print("[freshness] Weekly check is due; running deterministic checker...")
                result = run_freshness_checks(force_frontdoor_refresh=True)
                print(f"[freshness] Checked {result['resources_checked']} resources; created {result['flags_created']} flags.")
        except Exception as exc:
            print(f"[freshness] background check failed: {exc}")
        time.sleep(6 * 60 * 60)


# -------------------- HTTP API --------------------

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path == "/api/health":
                with db() as conn:
                    stats = {
                        "resources": conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0],
                        "corrections": conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0],
                        "comments": conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0],
                        "votes": conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0],
                        "network_posts": conn.execute("SELECT COUNT(*) FROM network_posts WHERE status='active'").fetchone()[0],
                        "flags": conn.execute("SELECT COUNT(*) FROM flags WHERE status='unresolved'").fetchone()[0],
                        "verified_corrections": conn.execute("SELECT COUNT(*) FROM corrections WHERE status='verified'").fetchone()[0],
                    }
                return self.send_json({"ok": True, "database": DB_PATH.name, "stats": stats, "freshness": freshness_status(), "consensus_threshold": CONSENSUS_THRESHOLD})

            if path == "/api/resources":
                organization = (query.get("organization") or [""])[0]
                key = (query.get("key") or [""])[0]
                with db() as conn:
                    if key and ":" in key:
                        source, external_id = key.split(":", 1)
                        rows = conn.execute(
                            "SELECT * FROM resources WHERE external_source = ? AND external_id = ?",
                            (source, external_id),
                        ).fetchall()
                    elif organization:
                        rows = conn.execute("SELECT * FROM resources WHERE organization = ? ORDER BY id DESC", (organization,)).fetchall()
                    else:
                        rows = conn.execute("SELECT * FROM resources ORDER BY id DESC").fetchall()
                    payload = [serialize_resource(conn, r) for r in rows]
                return self.send_json({"resources": payload})

            if path == "/api/discovery/search":
                q = (query.get("q") or [""])[0]
                city = (query.get("city") or ["Chicago"])[0]
                zip_code = (query.get("zip") or [""])[0]
                try:
                    limit = int((query.get("limit") or ["12"])[0])
                except ValueError:
                    limit = 12
                return self.send_json(discovery_search(q, limit=limit, city=city, zip_code=zip_code))

            if path == "/api/external/status":
                _, meta = load_frontdoor_data(allow_refresh=False)
                return self.send_json({"front_door": meta})

            if path == "/api/corrections":
                current_user = (query.get("user") or ["alex.martinez"])[0]
                return self.send_json({"corrections": list_corrections(current_user)})

            if path == "/api/network/posts":
                current_user = (query.get("user") or ["alex.martinez"])[0]
                search = (query.get("search") or [""])[0]
                return self.send_json({"posts": list_network_posts(current_user, search)})

            if path == "/api/flags":
                resource_id = (query.get("resource_id") or [""])[0]
                with db() as conn:
                    if resource_id:
                        rows = conn.execute(
                            "SELECT * FROM flags WHERE resource_id = ? AND status = 'unresolved' ORDER BY id DESC",
                            (int(resource_id),),
                        ).fetchall()
                    else:
                        rows = conn.execute("SELECT * FROM flags WHERE status = 'unresolved' ORDER BY id DESC").fetchall()
                return self.send_json({"flags": [dict(r) for r in rows]})

            if path == "/api/history":
                resource_id = int((query.get("resource_id") or ["0"])[0])
                with db() as conn:
                    return self.send_json({"history": resource_history(conn, resource_id), "overrides": approved_overrides(conn, resource_id)})

            if path == "/api/freshness/status":
                return self.send_json(freshness_status())

            return super().do_GET()
        except Exception as exc:
            traceback.print_exc()
            self.send_json({"error": str(exc)}, status=500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            payload = self.read_json()

            if path == "/api/resources/upsert":
                with db() as conn:
                    row, was_known = upsert_resource(conn, payload)
                    conn.commit()
                    result = serialize_resource(conn, row)
                return self.send_json({"resource": result, "was_known": was_known}, status=200 if was_known else 201)

            if path == "/api/external/refresh":
                meta = refresh_frontdoor_cache(force=True)
                return self.send_json({"front_door": meta})

            if path == "/api/freshness/run":
                result = run_freshness_checks(force_frontdoor_refresh=True)
                return self.send_json(result)

            if path == "/api/corrections":
                resource_payload = payload.get("resource") or {}
                author = clean(payload.get("author") or "Alex Martinez")
                initials = clean(payload.get("initials") or "AM")
                issue_type = clean(payload.get("issue_type") or "Other")
                message = clean(payload.get("message"))
                location = clean(payload.get("location"))
                if not message:
                    raise ValueError("message is required")
                with db() as conn:
                    resource, _ = upsert_resource(conn, resource_payload)
                    current_value = clean(payload.get("current_value"))
                    suggested_value = clean(payload.get("suggested_value"))
                    evidence = clean(payload.get("evidence"))
                    cur = conn.execute(
                        """
                        INSERT INTO corrections (resource_id, author, initials, issue_type, message, location, created_at, status, current_value, suggested_value, evidence)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?)
                        """,
                        (resource["id"], author, initials, issue_type, message, location, utc_now(), current_value, suggested_value, evidence),
                    )
                    correction_id = cur.lastrowid
                    conn.commit()
                    row = conn.execute(
                        """
                        SELECT c.*, r.organization, r.external_source, r.external_id
                        FROM corrections c JOIN resources r ON r.id = c.resource_id WHERE c.id = ?
                        """,
                        (correction_id,),
                    ).fetchone()
                    result = serialize_correction(conn, row, clean(payload.get("user_identifier") or "alex.martinez"))
                return self.send_json({"correction": result}, status=201)

            if path.startswith("/api/corrections/") and path.endswith("/comments"):
                correction_id = int(path.split("/")[3])
                author = clean(payload.get("author") or "Alex Martinez")
                message = clean(payload.get("message"))
                if not message:
                    raise ValueError("message is required")
                with db() as conn:
                    exists = conn.execute("SELECT 1 FROM corrections WHERE id = ?", (correction_id,)).fetchone()
                    if not exists:
                        return self.send_json({"error": "correction not found"}, status=404)
                    cur = conn.execute(
                        "INSERT INTO comments (correction_id, author, message, created_at) VALUES (?, ?, ?, ?)",
                        (correction_id, author, message, utc_now()),
                    )
                    conn.commit()
                    row = conn.execute("SELECT id, author, message, created_at FROM comments WHERE id = ?", (cur.lastrowid,)).fetchone()
                return self.send_json({"comment": dict(row)}, status=201)

            if path.startswith("/api/corrections/") and path.endswith("/vote"):
                correction_id = int(path.split("/")[3])
                user_identifier = clean(payload.get("user_identifier") or "alex.martinez")
                with db() as conn:
                    exists = conn.execute("SELECT 1 FROM corrections WHERE id = ?", (correction_id,)).fetchone()
                    if not exists:
                        return self.send_json({"error": "correction not found"}, status=404)
                    existing = conn.execute(
                        "SELECT id FROM votes WHERE correction_id = ? AND user_identifier = ?",
                        (correction_id, user_identifier),
                    ).fetchone()
                    if existing:
                        conn.execute("DELETE FROM votes WHERE id = ?", (existing["id"],))
                        voted = False
                    else:
                        conn.execute(
                            "INSERT INTO votes (correction_id, user_identifier, created_at) VALUES (?, ?, ?)",
                            (correction_id, user_identifier, utc_now()),
                        )
                        voted = True
                    verified = apply_consensus_if_ready(conn, correction_id) if voted else bool(conn.execute("SELECT 1 FROM corrections WHERE id=? AND status='verified'", (correction_id,)).fetchone())
                    conn.commit()
                    count = conn.execute("SELECT COUNT(*) FROM votes WHERE correction_id = ?", (correction_id,)).fetchone()[0]
                    status_row = conn.execute("SELECT status, verified_at FROM corrections WHERE id=?", (correction_id,)).fetchone()
                return self.send_json({"votes": count, "userVoted": voted, "verified": verified, "status": status_row["status"], "verifiedAt": status_row["verified_at"] or "", "consensusThreshold": CONSENSUS_THRESHOLD})

            if path == "/api/network/posts":
                resource_payload = payload.get("resource") or None
                resource_id = None
                with db() as conn:
                    if resource_payload:
                        resource, _ = upsert_resource(conn, resource_payload)
                        resource_id = resource["id"]
                    author = clean(payload.get("author") or "Alex Martinez")
                    initials = clean(payload.get("initials") or "AM")
                    category = clean(payload.get("category") or "Note")
                    message = clean(payload.get("message"))
                    location = clean(payload.get("location"))
                    if not message:
                        raise ValueError("message is required")
                    cur = conn.execute(
                        """
                        INSERT INTO network_posts(resource_id, author, initials, category, message, location, created_at, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
                        """,
                        (resource_id, author, initials, category, message, location, utc_now()),
                    )
                    conn.commit()
                    row = conn.execute(
                        """
                        SELECT p.*, r.organization, r.external_source, r.external_id
                        FROM network_posts p LEFT JOIN resources r ON r.id = p.resource_id WHERE p.id = ?
                        """,
                        (cur.lastrowid,),
                    ).fetchone()
                    result = serialize_network_post(conn, row, clean(payload.get("user_identifier") or "alex.martinez"))
                return self.send_json({"post": result}, status=201)

            if path.startswith("/api/network/posts/") and path.endswith("/comments"):
                post_id = int(path.split("/")[4])
                author = clean(payload.get("author") or "Alex Martinez")
                message = clean(payload.get("message"))
                if not message:
                    raise ValueError("message is required")
                with db() as conn:
                    if not conn.execute("SELECT 1 FROM network_posts WHERE id = ?", (post_id,)).fetchone():
                        return self.send_json({"error": "post not found"}, status=404)
                    cur = conn.execute(
                        "INSERT INTO network_post_comments(post_id, author, message, created_at) VALUES (?, ?, ?, ?)",
                        (post_id, author, message, utc_now()),
                    )
                    conn.commit()
                    row = conn.execute("SELECT id, author, message, created_at FROM network_post_comments WHERE id = ?", (cur.lastrowid,)).fetchone()
                return self.send_json({"comment": dict(row)}, status=201)

            if path.startswith("/api/network/posts/") and path.endswith("/vote"):
                post_id = int(path.split("/")[4])
                user_identifier = clean(payload.get("user_identifier") or "alex.martinez")
                with db() as conn:
                    if not conn.execute("SELECT 1 FROM network_posts WHERE id = ?", (post_id,)).fetchone():
                        return self.send_json({"error": "post not found"}, status=404)
                    existing = conn.execute(
                        "SELECT id FROM network_post_votes WHERE post_id = ? AND user_identifier = ?",
                        (post_id, user_identifier),
                    ).fetchone()
                    if existing:
                        conn.execute("DELETE FROM network_post_votes WHERE id = ?", (existing["id"],))
                        voted = False
                    else:
                        conn.execute(
                            "INSERT INTO network_post_votes(post_id, user_identifier, created_at) VALUES (?, ?, ?)",
                            (post_id, user_identifier, utc_now()),
                        )
                        voted = True
                    conn.commit()
                    count = conn.execute("SELECT COUNT(*) FROM network_post_votes WHERE post_id = ?", (post_id,)).fetchone()[0]
                return self.send_json({"votes": count, "userVoted": voted})

            return self.send_json({"error": "not found"}, status=404)
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            traceback.print_exc()
            self.send_json({"error": str(exc)}, status=500)

    def do_PATCH(self):
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            if path.startswith("/api/corrections/"):
                correction_id = int(path.split("/")[3])
                message = clean(payload.get("message"))
                if not message:
                    raise ValueError("message is required")
                with db() as conn:
                    cur = conn.execute("UPDATE corrections SET message = ? WHERE id = ?", (message, correction_id))
                    if not cur.rowcount:
                        return self.send_json({"error": "correction not found"}, status=404)
                    conn.commit()
                return self.send_json({"ok": True})
            if path.startswith("/api/network/posts/"):
                post_id = int(path.split("/")[4])
                message = clean(payload.get("message"))
                if not message:
                    raise ValueError("message is required")
                with db() as conn:
                    cur = conn.execute("UPDATE network_posts SET message = ? WHERE id = ?", (message, post_id))
                    if not cur.rowcount:
                        return self.send_json({"error": "post not found"}, status=404)
                    conn.commit()
                return self.send_json({"ok": True})
            return self.send_json({"error": "not found"}, status=404)
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            traceback.print_exc()
            self.send_json({"error": str(exc)}, status=500)

    def do_DELETE(self):
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/corrections/"):
                correction_id = int(path.split("/")[3])
                with db() as conn:
                    cur = conn.execute("DELETE FROM corrections WHERE id = ?", (correction_id,))
                    conn.commit()
                if not cur.rowcount:
                    return self.send_json({"error": "correction not found"}, status=404)
                return self.send_json({"ok": True})
            if path.startswith("/api/network/posts/"):
                post_id = int(path.split("/")[4])
                with db() as conn:
                    cur = conn.execute("DELETE FROM network_posts WHERE id = ?", (post_id,))
                    conn.commit()
                if not cur.rowcount:
                    return self.send_json({"error": "post not found"}, status=404)
                return self.send_json({"ok": True})
            return self.send_json({"error": "not found"}, status=404)
        except Exception as exc:
            traceback.print_exc()
            self.send_json({"error": str(exc)}, status=500)


def main() -> None:
    init_db()
    threading.Thread(target=background_freshness_loop, daemon=True, name="resourcebridge-freshness").start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("ResourceBridge — Final CHW Resource Knowledge Platform")
    print(f"Open http://{HOST}:{PORT}")
    print(f"Database: {DB_PATH}")
    print("External discovery: Front Door Illinois free sample (or partner key if FRONT_DOOR_API_KEY is set)")
    print("Freshness: deterministic weekly checks + official-site signals")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping ResourceBridge.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
