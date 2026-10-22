"""Lumber inventory tracker.

A small Flask + SQLite app for tracking milled lumber from log to sale:
dimensions, board feet, drying progress, photos, asking prices, and what
actually sold. Designed to be used from a phone while standing in the stack.

Run:
    python app.py
    python app.py --port 5300 --debug
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import secrets
import socket
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from flask import (
    Flask,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from PIL import Image, ImageOps
from werkzeug.utils import secure_filename

import kiln
import logscale
import vision

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "lumber.db"
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".webp"}
MAX_IMAGE_EDGE = 1600

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["SECRET_KEY"] = "lumber-inventory-local"
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024

STATUSES = ["green", "drying", "kiln", "ready", "listed", "sold"]

DRY_METHODS = ["air", "kiln"]

#: Shared secret for the sensor ingest endpoint. When set, POSTs to
#: /api/kiln/reading must present it. Unset means anything on the LAN can write
#: readings, which is fine on a home network and not fine anywhere else -- the
#: app binds 0.0.0.0 by default so the phone can reach it.
KILN_TOKEN = os.environ.get("LUMBER_KILN_TOKEN", "").strip()

# Dollars per board foot for kiln-dried rough stock, calibrated against DFW
# Facebook Marketplace asking prices surveyed 2026-08-27 (Celina pecan $14,
# Melissa walnut $15, cedar $4-6, mesquite $15-30). Override per species on
# the Settings page; the Sold report is what should correct these over time.
SPECIES_PRESETS: dict[str, dict[str, Any]] = {
    "Eastern Redcedar": {
        "price_per_bf": 6.0,
        "blurb": "Salmon-red heartwood with cream sapwood edge. Naturally rot "
        "and insect resistant, aromatic.",
    },
    "Pecan": {
        "price_per_bf": 12.0,
        "blurb": "Blonde sapwood against chocolate heartwood with strong "
        "figure. Excellent for cutting boards and shelving.",
    },
    "Mesquite": {
        "price_per_bf": 22.0,
        "blurb": "Dense Texas hardwood, deep amber-brown, extremely stable.",
    },
    "Red Oak": {"price_per_bf": 9.0, "blurb": "Open grain, warm pink-brown tone."},
    "White Oak": {"price_per_bf": 12.0, "blurb": "Tight grain, tan to olive, very durable."},
    "Post Oak": {"price_per_bf": 8.0, "blurb": "Dense native Texas white oak."},
    "Black Walnut": {"price_per_bf": 15.0, "blurb": "Rich chocolate heartwood. Premium species."},
    "Elm": {"price_per_bf": 8.0, "blurb": "Interlocked grain, strong figure."},
    "Hackberry": {"price_per_bf": 6.0, "blurb": "Pale, takes stain well, spalts readily."},
}


# --------------------------------------------------------------------------
# Wood math
# --------------------------------------------------------------------------


def board_feet(thickness_in: float, width_in: float, length_in: float) -> float:
    """Return board feet. One board foot is 144 cubic inches."""
    return (thickness_in * width_in * length_in) / 144.0


def estimated_ready_date(mill_date: date, thickness_in: float) -> date:
    """Air-drying rule of thumb: roughly one year per inch of thickness."""
    return mill_date + timedelta(days=int(round(thickness_in * 365)))


def dry_progress_pct(mill_date: date, thickness_in: float, today: date | None = None) -> int:
    """Percent of the estimated air-drying window elapsed, clamped to 0-100."""
    today = today or date.today()
    total_days = max(int(round(thickness_in * 365)), 1)
    elapsed = (today - mill_date).days
    return max(0, min(100, int(round(elapsed / total_days * 100))))


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS pieces (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    species       TEXT    NOT NULL,
    thickness_in  REAL    NOT NULL,
    width_in      REAL    NOT NULL,
    width_max_in  REAL,
    length_in     REAL    NOT NULL,
    mill_date     TEXT,
    moisture_pct  REAL,
    status        TEXT    NOT NULL DEFAULT 'green',
    location      TEXT,
    price_asking  REAL,
    price_sold    REAL,
    sold_date     TEXT,
    buyer         TEXT,
    notes         TEXT,
    already_dry   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS photos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    piece_id   INTEGER NOT NULL REFERENCES pieces(id) ON DELETE CASCADE,
    filename   TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS kiln_charges (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    started_at    TEXT,
    ended_at      TEXT,
    target_mc_pct REAL    NOT NULL DEFAULT 8.0,
    notes         TEXT,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS kiln_readings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    charge_id    INTEGER REFERENCES kiln_charges(id) ON DELETE SET NULL,
    recorded_at  TEXT    NOT NULL,
    temp_f       REAL,
    rh_pct       REAL,
    wood_mc_pct  REAL,
    source       TEXT    NOT NULL DEFAULT 'manual',
    created_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS logs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    client_uuid        TEXT,
    species            TEXT,
    small_diameter_in  REAL,
    large_diameter_in  REAL,
    length_ft          REAL,
    location           TEXT,
    notes              TEXT,
    status             TEXT NOT NULL DEFAULT 'on_deck',
    ai_analysis        TEXT,
    ai_analyzed_at     TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS log_photos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    log_id     INTEGER NOT NULL REFERENCES logs(id) ON DELETE CASCADE,
    filename   TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_photos_piece ON photos(piece_id);
CREATE INDEX IF NOT EXISTS idx_pieces_status ON pieces(status);
CREATE INDEX IF NOT EXISTS idx_readings_time ON kiln_readings(recorded_at);
CREATE INDEX IF NOT EXISTS idx_readings_charge ON kiln_readings(charge_id);
"""

#: Columns added to ``pieces`` after the first release. SQLite has no
#: "ADD COLUMN IF NOT EXISTS", so migration is: read what is there, add what is
#: missing. Safe to run on every start.
PIECE_MIGRATIONS: list[tuple[str, str]] = [
    ("dry_method", "TEXT NOT NULL DEFAULT 'air'"),
    ("kiln_charge_id", "INTEGER REFERENCES kiln_charges(id)"),
    ("target_mc_pct", "REAL"),
    # Idempotency key for the field app's offline sync: a queued item that
    # syncs twice (flaky cell signal, retry after timeout) must not become two
    # pieces. The phone stamps each capture with a UUID; the API returns the
    # existing row instead of inserting when it has seen the UUID before.
    ("client_uuid", "TEXT"),
    # Listing workflow: where a piece is listed, the link back to it, when it
    # went up, and when it was last renewed. Marketplace listings go stale in
    # about a week, so bumped_at drives the "renew this" nudge.
    ("listed_on", "TEXT"),
    ("listing_url", "TEXT"),
    ("listed_at", "TEXT"),
    ("bumped_at", "TEXT"),
]

LOG_STATUSES = ["on_deck", "milled", "skipped"]

LISTING_PLATFORMS = ["facebook", "website", "other"]

#: Marketplace buries listings after roughly this long without activity.
STALE_LISTING_DAYS = 7


def get_db() -> sqlite3.Connection:
    """Return the request-scoped SQLite connection."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc: BaseException | None) -> None:
    """Close the connection at the end of the request."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    """Create tables, run migrations, and ensure the upload directory exists."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(SCHEMA)
        _migrate_pieces(conn)
    logger.info("Database ready at %s", DB_PATH)


def _migrate_pieces(conn: sqlite3.Connection) -> None:
    """Add any columns missing from an older ``pieces`` table."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(pieces)")}
    for column, ddl in PIECE_MIGRATIONS:
        if column not in existing:
            conn.execute(f"ALTER TABLE pieces ADD COLUMN {column} {ddl}")
            logger.info("Migrated pieces: added %s", column)


# --------------------------------------------------------------------------
# Row helpers
# --------------------------------------------------------------------------


def _parse_date(value: str | None) -> date | None:
    """Parse an ISO date string, returning None when absent or malformed."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _float_or_none(value: str | None) -> float | None:
    """Coerce a form value to float, returning None for blanks and junk."""
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def piece_context(db: sqlite3.Connection) -> dict[str, Any]:
    """Everything :func:`decorate` needs, fetched once for a whole page.

    Bundled rather than passed as loose arguments because it is read per row and
    every item in it costs a query: live kiln EMC, the tunable model settings,
    and the per-species rates.
    """
    return {
        "emc_pct": kiln_emc(db),
        "settings": get_settings(db),
        "rates": species_rates(db),
    }


def decorate(row: sqlite3.Row, ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    """Turn a piece row into a dict with computed board feet and drying info.

    Pass the bundle from :func:`piece_context` to get predictions based on
    measured kiln conditions and the user's tuned settings. Without it, the
    shipped defaults apply and drying falls back to the rule of thumb.
    """
    ctx = ctx or {}
    settings = ctx.get("settings") or dict(SETTING_DEFAULTS)

    piece = dict(row)
    piece["board_feet"] = round(
        board_feet(piece["thickness_in"], piece["width_in"], piece["length_in"]), 2
    )

    piece.update(_drying_state(piece, ctx.get("emc_pct"), settings))

    if piece.get("price_asking"):
        piece["price_per_bf"] = round(piece["price_asking"] / piece["board_feet"], 2) if piece["board_feet"] else None
    else:
        piece["price_per_bf"] = None

    piece["suggested_price"] = suggested_price(piece, ctx.get("rates"))
    piece["dimensions"] = _dimension_string(piece)
    return piece


def _drying_state(
    piece: dict[str, Any], emc_pct: float | None, settings: dict[str, float]
) -> dict[str, Any]:
    """Work out where a piece is in its drying, and when it will be ready.

    Three levels of confidence, best first:

    1. Kiln stock with live sensor readings -- the exponential model driven by
       the EMC those conditions imply. This is the only branch that can tell you
       the target is unreachable.
    2. A measured moisture percentage, with EMC assumed from the method.
    3. Nothing but a mill date -- the old flat rule of thumb, unchanged.
    """
    method = piece.get("dry_method") or "air"
    default_target = settings["target_kiln"] if method == "kiln" else settings["target_air"]
    target = piece.get("target_mc_pct") or default_target
    tau = settings["tau_kiln"] if method == "kiln" else settings["tau_air"]
    green = settings["green_mc"]
    thickness = piece["thickness_in"]
    milled = _parse_date(piece.get("mill_date"))

    state: dict[str, Any] = {
        "dry_method": method,
        "target_mc_pct": target,
        "ready_date": None,
        "dry_pct": 0,
        "dry_label": "No mill date",
        "current_mc": None,
        "unreachable": False,
    }

    if piece.get("already_dry"):
        return {**state, "dry_pct": 100, "dry_label": "Dry when milled", "current_mc": None}

    if not milled:
        return state

    # Assume the ambient equilibrium for air drying in North Texas when no
    # sensor is speaking for this piece.
    effective_emc = emc_pct if (method == "kiln" and emc_pct is not None) else settings["target_air"] - 3.0

    measured = piece.get("moisture_pct")
    days_elapsed = (date.today() - milled).days
    if measured is not None:
        current = float(measured)
    else:
        current = kiln.project_mc(green, effective_emc, days_elapsed, thickness, method, tau)
    state["current_mc"] = round(current, 1)

    remaining = kiln.days_to_target(current, target, effective_emc, thickness, method, tau)

    if remaining is None:
        state["unreachable"] = True
        state["dry_label"] = f"Conditions cap at {effective_emc:.1f}%"
        state["dry_pct"] = 0
        return state

    if remaining <= 0:
        return {**state, "dry_pct": 100, "dry_label": "Estimated dry", "ready_date": None}

    ready = date.today() + timedelta(days=int(round(remaining)))
    state["ready_date"] = ready.isoformat()

    # Progress measured against the whole journey from green, so the bar does
    # not jump when a moisture reading is entered mid-way.
    span = max(green - target, 1.0)
    state["dry_pct"] = max(0, min(100, int(round((green - current) / span * 100))))

    if remaining < 45:
        state["dry_label"] = f"~{int(round(remaining))} days left"
    else:
        state["dry_label"] = f"~{max(0, round(remaining / 30.4))} mo left"
    if method == "kiln":
        state["dry_label"] += " (kiln)"

    return state


def suggested_price(piece: dict[str, Any], rates: dict[str, float] | None = None) -> float | None:
    """Asking price implied by the species preset and the board footage.

    The presets carry a dollars-per-board-foot figure that nothing used to read.
    Kiln-dried stock is worth meaningfully more than green, so green pieces get
    marked down rather than quoted at the dry rate.
    """
    table = rates if rates is not None else {
        name: float(spec["price_per_bf"]) for name, spec in SPECIES_PRESETS.items()
    }
    rate = table.get(piece.get("species", ""))
    if rate is None or not piece.get("board_feet"):
        return None
    if piece.get("dry_method") == "kiln" and piece.get("dry_pct") == 100:
        pass  # full rate: kiln dried and ready
    elif piece.get("already_dry") or piece.get("dry_pct") == 100:
        rate *= 0.75  # air dried, not kiln dried
    else:
        rate *= 0.45  # still wet; buyers discount hard for drying risk

    return round(rate * piece["board_feet"], 2)


def _dimension_string(piece: dict[str, Any]) -> str:
    """Human-readable dimensions, showing a width range for live edge."""
    length = _trim_number(piece["length_in"])
    thickness = _trim_number(piece["thickness_in"])
    if piece.get("width_max_in") and piece["width_max_in"] > piece["width_in"]:
        width = f'{_trim_number(piece["width_in"])}-{_trim_number(piece["width_max_in"])}'
    else:
        width = _trim_number(piece["width_in"])
    return f'{length}" L x {width}" W x {thickness}" T'


def _trim_number(value: float) -> str:
    """Render 48.0 as '48' and 1.5 as '1.5'."""
    return f"{value:g}"


def fetch_photos(db: sqlite3.Connection, piece_id: int) -> list[sqlite3.Row]:
    """Return photo rows for a piece, oldest first."""
    return db.execute(
        "SELECT * FROM photos WHERE piece_id = ? ORDER BY id", (piece_id,)
    ).fetchall()


# --------------------------------------------------------------------------
# Photos
# --------------------------------------------------------------------------


def _store_image(stream: Any, prefix: str) -> str | None:
    """Process one image stream into the uploads folder; returns the filename.

    Auto-rotates (phones lie about orientation), downscales to
    :data:`MAX_IMAGE_EDGE`, and re-encodes as JPEG, so the folder stays small
    no matter what the camera produced.
    """
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{prefix}_{uuid.uuid4().hex[:10]}.jpg"
    try:
        image = Image.open(stream)
        image = ImageOps.exif_transpose(image)
        image = image.convert("RGB")
        image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
        image.save(UPLOAD_DIR / filename, "JPEG", quality=85)
    except Exception:  # noqa: BLE001 - a bad upload should not 500 the app
        logger.exception("Could not process uploaded image")
        return None
    return filename


def save_photo(piece_id: int, file_storage: Any) -> str | None:
    """Save an uploaded image for a piece, or None if empty or rejected."""
    if not file_storage or not file_storage.filename:
        return None
    suffix = Path(secure_filename(file_storage.filename)).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        logger.warning("Rejected upload with extension %s", suffix)
        return None
    return _store_image(file_storage.stream, str(piece_id))


def save_photo_b64(prefix: str, b64_data: str) -> str | None:
    """Save a base64 image from the field app's sync queue.

    Caps the decoded size at the app's upload limit -- a queued photo already
    went through client-side downscaling, so anything enormous is not a photo.
    """
    import io

    try:
        payload = b64_data.split(",", 1)[1] if "," in b64_data else b64_data
        data = base64.b64decode(payload, validate=False)
    except (ValueError, TypeError):
        return None
    if not data or len(data) > app.config["MAX_CONTENT_LENGTH"]:
        return None
    return _store_image(io.BytesIO(data), prefix)


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

#: Everything the drying model and the alerts key off, exposed for editing.
#:
#: The shipped numbers are back-calculated from trade rules of thumb, not
#: measured on this kiln. They are starting points and are meant to be replaced
#: once a real charge has been logged -- which is the whole reason this table
#: exists rather than the values living as constants in ``kiln.py``.
SETTING_SPECS: list[dict[str, Any]] = [
    {
        "key": "tau_air",
        "label": "Air drying time constant",
        "unit": "days per inch",
        "default": kiln.TAU_DAYS_PER_INCH["air"],
        "help": "120 reproduces the one-year-per-inch rule. Raise it if your stack dries slower.",
    },
    {
        "key": "tau_kiln",
        "label": "Kiln drying time constant",
        "unit": "days per inch",
        "default": kiln.TAU_DAYS_PER_INCH["kiln"],
        "help": "18 is a solar kiln taking air-dried 4/4 to 8% in about six weeks. "
        "Measure a charge and let the calibrator correct this.",
    },
    {
        "key": "target_air",
        "label": "Default target, air dried",
        "unit": "% MC",
        "default": kiln.TARGET_MC_AIR,
        "help": "Outdoor equilibrium where you are. Around 15% in North Texas.",
    },
    {
        "key": "target_kiln",
        "label": "Default target, kiln dried",
        "unit": "% MC",
        "default": kiln.TARGET_MC_KILN,
        "help": "6-8% is furniture and epoxy ready.",
    },
    {
        "key": "green_mc",
        "label": "Assumed green moisture",
        "unit": "% MC",
        "default": kiln.DEFAULT_GREEN_MC_PCT,
        "help": "Starting point when a piece has no measured moisture reading.",
    },
    {
        "key": "kiln_entry_max_mc",
        "label": "Max moisture to load the kiln",
        "unit": "% MC",
        "default": kiln.KILN_ENTRY_MAX_MC_PCT,
        "help": "A solar kiln is a finisher. Wetter than this case-hardens.",
    },
    {
        "key": "max_safe_temp_f",
        "label": "Overheating alert above",
        "unit": "F",
        "default": kiln.MAX_SAFE_TEMP_F,
        "help": "Surface checking and glue-line risk above this.",
    },
    {
        "key": "high_rh_pct",
        "label": "High humidity alert above",
        "unit": "% RH",
        "default": kiln.HIGH_RH_PCT,
        "help": "Sustained humidity this high means moist air is not leaving the box.",
    },
    {
        "key": "stale_reading_hours",
        "label": "Sensor considered dead after",
        "unit": "hours",
        "default": kiln.STALE_READING_HOURS,
        "help": "Set this well above your logging interval. For a weekly SD card pull, "
        "raise it to about 200 so you are not permanently alarmed.",
    },
    {
        "key": "cool_kiln_temp_f",
        "label": "Cool kiln note below",
        "unit": "F",
        "default": 90.0,
        "help": "Informational only. Below this the kiln is not doing much work.",
    },
    {
        "key": "aggressive_emc_pct",
        "label": "Too-fast drying warning below",
        "unit": "% EMC",
        "default": 8.0,
        "help": "Warn when conditions this dry meet wood above fiber saturation.",
    },
    {
        "key": "sterilize_temp_f",
        "label": "Sterilizing temperature",
        "unit": "F",
        "default": kiln.STERILIZE_TEMP_F,
        "help": "133 F is the standard heat-treatment spec for powderpost beetles.",
    },
    {
        "key": "assume_kiln",
        "label": "Assume kiln drying is available",
        "unit": "1 = yes, 0 = no",
        "default": 1.0,
        "help": "Drives the thickness recommendation on log pages. With the kiln, pecan's "
        "center cuts go 8/4; without it they cap at 6/4 to avoid honeycomb.",
    },
    {
        "key": "sterilize_minutes",
        "label": "Sterilizing hold time",
        "unit": "minutes",
        "default": float(kiln.STERILIZE_MINUTES),
        "help": "Continuous minutes at temperature. Gaps in readings break the run.",
    },
]

SETTING_DEFAULTS: dict[str, float] = {spec["key"]: float(spec["default"]) for spec in SETTING_SPECS}

#: Species rates are stored under this prefix so they share the settings table.
SPECIES_RATE_PREFIX = "rate:"


def get_settings(db: sqlite3.Connection) -> dict[str, float]:
    """Model and alert settings, with shipped defaults filling any gaps."""
    values = dict(SETTING_DEFAULTS)
    for row in db.execute("SELECT key, value FROM settings"):
        if row["key"] in values:
            try:
                values[row["key"]] = float(row["value"])
            except (TypeError, ValueError):
                continue
    return values


def species_rates(db: sqlite3.Connection) -> dict[str, float]:
    """Dollars per board foot per species, preset defaults plus any overrides."""
    rates = {name: float(spec["price_per_bf"]) for name, spec in SPECIES_PRESETS.items()}
    for row in db.execute(
        "SELECT key, value FROM settings WHERE key LIKE ?", (SPECIES_RATE_PREFIX + "%",)
    ):
        name = row["key"][len(SPECIES_RATE_PREFIX):]
        try:
            rates[name] = float(row["value"])
        except (TypeError, ValueError):
            continue
    return rates


def put_setting(db: sqlite3.Connection, key: str, value: float | None) -> None:
    """Store one setting, or clear it so the shipped default applies again."""
    if value is None:
        db.execute("DELETE FROM settings WHERE key = ?", (key,))
    else:
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


def thresholds_from(settings: dict[str, float]) -> dict[str, float]:
    """The subset of settings that :func:`kiln.evaluate_alerts` consumes."""
    return {key: settings[key] for key in kiln.DEFAULT_THRESHOLDS if key in settings}


# --------------------------------------------------------------------------
# Kiln data access
# --------------------------------------------------------------------------


def fetch_readings(
    db: sqlite3.Connection, hours: float = 24.0, charge_id: int | None = None
) -> list[dict[str, Any]]:
    """Sensor readings from the last ``hours``, oldest first."""
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    sql = "SELECT * FROM kiln_readings WHERE recorded_at >= ?"
    params: list[Any] = [cutoff]
    if charge_id is not None:
        sql += " AND charge_id = ?"
        params.append(charge_id)
    sql += " ORDER BY recorded_at"
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def active_charge(db: sqlite3.Connection) -> dict[str, Any] | None:
    """The kiln charge currently running, if any."""
    row = db.execute(
        "SELECT * FROM kiln_charges WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def current_conditions(
    db: sqlite3.Connection, hours: float = 24.0
) -> tuple[kiln.ConditionSummary, float | None]:
    """Summarised conditions over a window, plus hours since the last reading."""
    readings = fetch_readings(db, hours=hours)
    summary = kiln.summarize(readings)

    hours_since = None
    if summary.latest_at:
        try:
            delta = datetime.now() - datetime.fromisoformat(summary.latest_at)
            hours_since = round(delta.total_seconds() / 3600.0, 2)
        except ValueError:
            hours_since = None
    return summary, hours_since


def kiln_emc(db: sqlite3.Connection) -> float | None:
    """EMC implied by recent conditions, or None when nothing is reporting."""
    summary, _ = current_conditions(db)
    return summary.emc_pct


# --------------------------------------------------------------------------
# Listing text
# --------------------------------------------------------------------------


#: Words in a piece's notes that buyers search for and pay for. Each maps to
#: the tag that goes in the title and the sentence that goes in the body.
FIGURE_KEYWORDS: dict[str, tuple[str, str]] = {
    "ambrosia": ("Ambrosia", "Ambrosia figure -- the gray-blue streaking is fungal color from beetles that attacked the green log; they are long gone."),
    "spalt": ("Spalted", "Spalted figure with dark zone lines."),
    "curly": ("Curly", "Curly figure."),
    "crotch": ("Crotch", "Crotch figure where the trunk forked."),
    "mineral": ("", "Mineral streaking."),
    "burl": ("Burl", "Burl figure."),
}


def _figure_tags(notes: str | None) -> tuple[list[str], list[str]]:
    """Title tags and body sentences for any figure named in the notes."""
    text = (notes or "").lower()
    tags, sentences = [], []
    for keyword, (tag, sentence) in FIGURE_KEYWORDS.items():
        if keyword in text:
            if tag:
                tags.append(tag)
            sentences.append(sentence)
    return tags, sentences


def _length_label(length_in: float) -> str:
    """87 -> '87" (7\\' 3")', because buyers think in feet for long stock."""
    feet, inches = divmod(int(round(length_in)), 12)
    if feet and inches:
        return f'{_trim_number(length_in)}" ({feet}\' {inches}")'
    if feet:
        return f'{_trim_number(length_in)}" ({feet}\')'
    return f'{_trim_number(length_in)}"'


def generate_listing(piece: dict[str, Any]) -> str:
    """Build marketplace-ready listing text for a piece.

    Title is keyword-first because that is what Marketplace search indexes:
    condition, cut, species, "slab", then dimensions and figure. The body leads
    with the two lines that separate this from every other small-mill listing
    -- moisture content and sterilization -- then the facts a maker wants.
    """
    preset = SPECIES_PRESETS.get(piece["species"], {})
    blurb = preset.get("blurb", "")

    kiln_dried = piece.get("dry_method") == "kiln" and piece.get("dry_pct") == 100
    dry = kiln_dried or piece.get("already_dry") or piece.get("dry_pct") == 100
    tags, figure_lines = _figure_tags(piece.get("notes"))
    notes_lower = (piece.get("notes") or "").lower()
    borate = "tim-bor" in notes_lower or "borate" in notes_lower

    prefix = "Kiln Dried " if kiln_dried else ""
    figure = f' {" ".join(tags)}' if tags else ""
    dims = (
        f'{_trim_number(piece["length_in"])}" x '
        f'{_trim_number(piece["width_max_in"] or piece["width_in"])}" x '
        f'{_trim_number(piece["thickness_in"])}"'
    )
    title = f'{prefix}Live Edge {piece["species"]}{figure} Slab {dims}'
    lines = [title, ""]

    if kiln_dried:
        mc = piece.get("moisture_pct")
        target = piece.get("target_mc_pct") or kiln.TARGET_MC_KILN
        reading = _trim_number(mc) if mc is not None else _trim_number(target)
        lines.append(f"Kiln dried to {reading}% and ready to work -- most small mills sell green.")
        if piece.get("sterilized"):
            lines.append(
                f"Heat sterilized at {_trim_number(kiln.STERILIZE_TEMP_F)}F in the kiln"
                + (", borate treated at milling" if borate else "")
                + " -- no live insects."
            )
        elif borate:
            lines.append("Borate treated at milling.")
        lines.append("")
    elif borate:
        lines += ["Borate treated at milling.", ""]

    if blurb:
        lines += [blurb, ""]
    if figure_lines:
        lines += figure_lines + [""]

    lines.append(f'Length: {_length_label(piece["length_in"])}')
    if piece.get("width_max_in") and piece["width_max_in"] > piece["width_in"]:
        lines.append(f'Width: {_trim_number(piece["width_in"])}" to {_trim_number(piece["width_max_in"])}" (live edge)')
    else:
        lines.append(f'Width: {_trim_number(piece["width_in"])}"')
    lines.append(f'Thickness: {_trim_number(piece["thickness_in"])}"')
    lines.append(f'Board feet: {piece["board_feet"]}')
    lines.append(f'Species: {piece["species"]}')

    if piece.get("moisture_pct") is not None:
        lines.append(f'Moisture: {_trim_number(piece["moisture_pct"])}%')

    if kiln_dried:
        lines.append("Condition: kiln dried, stable, ready to work")
    elif piece.get("already_dry"):
        lines.append("Condition: dry, ready to work")
    elif dry:
        lines.append("Condition: air dried")
    else:
        # Do not advertise a drying countdown -- nobody buys a slab that is
        # ready in eighteen months. Say it is not for sale yet instead.
        lines.append("Condition: still drying -- not yet for sale")

    if piece.get("notes"):
        lines += ["", piece["notes"]]

    if piece.get("price_asking"):
        price = f'${_trim_number(piece["price_asking"])}'
        if piece.get("price_per_bf"):
            price += f' (about ${_trim_number(piece["price_per_bf"])}/bd ft)'
        lines += ["", price]

    lines += ["", "Cash or Zelle, local pickup. Message with questions -- happy to send more photos."]
    return "\n".join(lines)


def listing_readiness(piece: dict[str, Any], photo_count: int) -> dict[str, Any]:
    """Can this piece go up for sale, and if not, what is missing?

    Blockers are the rules that protect the reputation: never list green wood
    (it cups on the buyer's wall and comes back as a bad review), never list
    without a photo, never list without a price. Warnings are things a better
    listing would have.
    """
    blockers: list[str] = []
    warnings: list[str] = []

    dry = piece.get("already_dry") or piece.get("dry_pct") == 100
    if not dry:
        blockers.append(
            f"Still drying ({piece.get('dry_label', '')}). Green wood moves after it is sold -- wait."
        )
    if photo_count == 0:
        blockers.append("No photo. Shoot it wet, in shade, with the figure showing.")
    if not piece.get("price_asking"):
        suggestion = piece.get("suggested_price")
        blockers.append(
            "No asking price." + (f" Suggested: ${suggestion:,.0f}." if suggestion else "")
        )

    if not piece.get("species") or piece["species"] == "Unknown":
        warnings.append("Species is unknown -- buyers search by species.")
    if piece.get("dry_method") == "kiln" and piece.get("moisture_pct") is None:
        warnings.append("Kiln dried but no meter reading logged; a number in the listing sells.")
    if photo_count == 1:
        warnings.append("Only one photo. Add the end grain and a wet close-up of the figure.")
    if piece.get("status") == "listed" and not piece.get("listing_url"):
        warnings.append("Marked listed but no listing link saved.")

    return {"ok": not blockers, "blockers": blockers, "warnings": warnings}


def _days_since(stamp: str | None) -> int | None:
    """Whole days since an ISO timestamp, or None."""
    if not stamp:
        return None
    try:
        return (datetime.now() - datetime.fromisoformat(stamp)).days
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@app.route("/")
def index() -> str:
    """Inventory list with optional status and species filters."""
    db = get_db()
    status = request.args.get("status", "")
    species = request.args.get("species", "")

    sql = "SELECT * FROM pieces WHERE 1=1"
    params: list[Any] = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if species:
        sql += " AND species = ?"
        params.append(species)
    sql += " ORDER BY (status = 'sold'), datetime(updated_at) DESC"

    ctx = piece_context(db)
    pieces = [decorate(r, ctx) for r in db.execute(sql, params).fetchall()]

    thumbs: dict[int, str] = {}
    for piece in pieces:
        row = db.execute(
            "SELECT filename FROM photos WHERE piece_id = ? ORDER BY id LIMIT 1",
            (piece["id"],),
        ).fetchone()
        if row:
            thumbs[piece["id"]] = row["filename"]

    known_species = [
        r["species"] for r in db.execute("SELECT DISTINCT species FROM pieces ORDER BY species")
    ]

    unsold = [p for p in pieces if p["status"] != "sold"]
    totals = {
        "count": len(unsold),
        "board_feet": round(sum(p["board_feet"] for p in unsold), 1),
        "asking": round(sum(p["price_asking"] or 0 for p in unsold), 2),
    }

    return render_template(
        "index.html",
        pieces=pieces,
        thumbs=thumbs,
        totals=totals,
        statuses=STATUSES,
        known_species=known_species,
        cur_status=status,
        cur_species=species,
    )


@app.route("/piece/new", methods=["GET", "POST"])
def new_piece() -> Any:
    """Create a piece."""
    if request.method == "POST":
        piece_id = _save_piece(None)
        return redirect(url_for("piece_detail", piece_id=piece_id))
    return render_template(
        "piece_form.html",
        piece=None,
        statuses=STATUSES,
        presets=SPECIES_PRESETS,
        today=date.today().isoformat(),
    )


@app.route("/piece/<int:piece_id>/edit", methods=["GET", "POST"])
def edit_piece(piece_id: int) -> Any:
    """Edit an existing piece."""
    db = get_db()
    row = db.execute("SELECT * FROM pieces WHERE id = ?", (piece_id,)).fetchone()
    if row is None:
        return redirect(url_for("index"))

    if request.method == "POST":
        _save_piece(piece_id)
        return redirect(url_for("piece_detail", piece_id=piece_id))

    return render_template(
        "piece_form.html",
        piece=decorate(row, piece_context(db)),
        statuses=STATUSES,
        presets=SPECIES_PRESETS,
        today=date.today().isoformat(),
    )


def _save_piece(piece_id: int | None) -> int:
    """Insert or update a piece from the submitted form. Returns its id."""
    db = get_db()
    form = request.form
    now = datetime.now().isoformat(timespec="seconds")

    species = (form.get("species") or "").strip() or "Unknown"
    thickness = _float_or_none(form.get("thickness_in")) or 1.0
    width = _float_or_none(form.get("width_in")) or 1.0
    width_max = _float_or_none(form.get("width_max_in"))
    length = _float_or_none(form.get("length_in")) or 1.0
    status = form.get("status") or "green"
    already_dry = 1 if form.get("already_dry") else 0

    dry_method = form.get("dry_method") or "air"
    if dry_method not in DRY_METHODS:
        dry_method = "air"
    charge_id = _float_or_none(form.get("kiln_charge_id"))

    fields = {
        "species": species,
        "thickness_in": thickness,
        "width_in": width,
        "width_max_in": width_max,
        "length_in": length,
        "mill_date": form.get("mill_date") or None,
        "moisture_pct": _float_or_none(form.get("moisture_pct")),
        "status": status,
        "dry_method": dry_method,
        "kiln_charge_id": int(charge_id) if charge_id else None,
        "target_mc_pct": _float_or_none(form.get("target_mc_pct")),
        "location": (form.get("location") or "").strip() or None,
        "price_asking": _float_or_none(form.get("price_asking")),
        "price_sold": _float_or_none(form.get("price_sold")),
        "sold_date": form.get("sold_date") or None,
        "buyer": (form.get("buyer") or "").strip() or None,
        "notes": (form.get("notes") or "").strip() or None,
        "already_dry": already_dry,
        "updated_at": now,
    }

    if piece_id is None:
        fields["created_at"] = now
        columns = ", ".join(fields)
        placeholders = ", ".join(["?"] * len(fields))
        cur = db.execute(
            f"INSERT INTO pieces ({columns}) VALUES ({placeholders})", list(fields.values())
        )
        piece_id = int(cur.lastrowid)
    else:
        assignments = ", ".join(f"{k} = ?" for k in fields)
        db.execute(
            f"UPDATE pieces SET {assignments} WHERE id = ?", [*fields.values(), piece_id]
        )

    for upload in request.files.getlist("photos"):
        filename = save_photo(piece_id, upload)
        if filename:
            db.execute(
                "INSERT INTO photos (piece_id, filename, created_at) VALUES (?, ?, ?)",
                (piece_id, filename, now),
            )

    db.commit()
    return piece_id


@app.route("/piece/<int:piece_id>")
def piece_detail(piece_id: int) -> Any:
    """Show one piece with its photos and generated listing text."""
    db = get_db()
    row = db.execute("SELECT * FROM pieces WHERE id = ?", (piece_id,)).fetchone()
    if row is None:
        return redirect(url_for("index"))

    piece = decorate(row, piece_context(db))
    piece["sterilized"] = _piece_sterilized(db, piece)
    photos = fetch_photos(db, piece_id)
    return render_template(
        "piece_detail.html",
        piece=piece,
        photos=photos,
        listing=generate_listing(piece),
        readiness=listing_readiness(piece, len(photos)),
        platforms=LISTING_PLATFORMS,
        days_listed=_days_since(piece.get("listed_at")),
        days_since_bump=_days_since(piece.get("bumped_at")),
        stale_days=STALE_LISTING_DAYS,
        statuses=STATUSES,
    )


def _piece_sterilized(db: sqlite3.Connection, piece: dict[str, Any]) -> bool:
    """Whether the kiln charge this piece went through hit the sterilizing hold.

    Only ever claimed from logged readings -- this line goes into a listing a
    buyer relies on, so it is data or nothing.
    """
    charge_id = piece.get("kiln_charge_id")
    if not charge_id:
        return False
    settings = get_settings(db)
    readings = [
        dict(r)
        for r in db.execute(
            "SELECT * FROM kiln_readings WHERE charge_id = ? ORDER BY recorded_at", (charge_id,)
        )
    ]
    return bool(
        kiln.sterilization_window(
            readings,
            temp_f=settings["sterilize_temp_f"],
            minutes=int(settings["sterilize_minutes"]),
        )["sterilized"]
    )


@app.route("/piece/<int:piece_id>/status/<new_status>", methods=["POST"])
def set_status(piece_id: int, new_status: str) -> Any:
    """Quick status change from the list or detail view."""
    if new_status not in STATUSES:
        return redirect(url_for("piece_detail", piece_id=piece_id))

    db = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    if new_status == "sold":
        db.execute(
            "UPDATE pieces SET status = ?, sold_date = COALESCE(sold_date, ?), updated_at = ? WHERE id = ?",
            (new_status, date.today().isoformat(), now, piece_id),
        )
    else:
        db.execute(
            "UPDATE pieces SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, now, piece_id),
        )
    db.commit()
    return redirect(request.referrer or url_for("piece_detail", piece_id=piece_id))


@app.route("/piece/<int:piece_id>/delete", methods=["POST"])
def delete_piece(piece_id: int) -> Any:
    """Delete a piece and its photo files."""
    db = get_db()
    for photo in fetch_photos(db, piece_id):
        (UPLOAD_DIR / photo["filename"]).unlink(missing_ok=True)
    db.execute("DELETE FROM photos WHERE piece_id = ?", (piece_id,))
    db.execute("DELETE FROM pieces WHERE id = ?", (piece_id,))
    db.commit()
    flash("Piece deleted.")
    return redirect(url_for("index"))


@app.route("/photo/<int:photo_id>/delete", methods=["POST"])
def delete_photo(photo_id: int) -> Any:
    """Delete a single photo."""
    db = get_db()
    row = db.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if row is None:
        return redirect(url_for("index"))
    (UPLOAD_DIR / row["filename"]).unlink(missing_ok=True)
    db.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
    db.commit()
    return redirect(url_for("piece_detail", piece_id=row["piece_id"]))


@app.route("/stats")
def stats() -> str:
    """What sold, for how much, and what the stack is worth."""
    db = get_db()
    ctx = piece_context(db)
    pieces = [decorate(r, ctx) for r in db.execute("SELECT * FROM pieces").fetchall()]

    by_species: dict[str, dict[str, float]] = {}
    for piece in pieces:
        entry = by_species.setdefault(
            piece["species"],
            {"count": 0, "board_feet": 0.0, "sold_count": 0, "sold_value": 0.0, "sold_bf": 0.0},
        )
        entry["count"] += 1
        entry["board_feet"] += piece["board_feet"]
        if piece["status"] == "sold":
            entry["sold_count"] += 1
            entry["sold_value"] += piece["price_sold"] or 0.0
            entry["sold_bf"] += piece["board_feet"]

    for entry in by_species.values():
        entry["board_feet"] = round(entry["board_feet"], 1)
        entry["sold_value"] = round(entry["sold_value"], 2)
        entry["realized_per_bf"] = (
            round(entry["sold_value"] / entry["sold_bf"], 2) if entry["sold_bf"] else None
        )

    sold = [p for p in pieces if p["status"] == "sold"]
    unsold = [p for p in pieces if p["status"] != "sold"]
    summary = {
        "total_pieces": len(pieces),
        "sold_count": len(sold),
        "revenue": round(sum(p["price_sold"] or 0 for p in sold), 2),
        "on_hand_bf": round(sum(p["board_feet"] for p in unsold), 1),
        "on_hand_asking": round(sum(p["price_asking"] or 0 for p in unsold), 2),
    }

    return render_template(
        "stats.html",
        by_species=dict(sorted(by_species.items())),
        summary=summary,
        sold=sorted(sold, key=lambda p: p.get("sold_date") or "", reverse=True),
    )


# --------------------------------------------------------------------------
# Kiln routes
# --------------------------------------------------------------------------


def _kiln_context(db: sqlite3.Connection, hours: float = 24.0) -> dict[str, Any]:
    """Everything the kiln dashboard and the status API both need."""
    settings = get_settings(db)
    charge = active_charge(db)
    target = float(charge["target_mc_pct"]) if charge else settings["target_kiln"]
    summary, hours_since = current_conditions(db, hours=hours)

    ctx = {"emc_pct": summary.emc_pct, "settings": settings, "rates": species_rates(db)}
    in_kiln = [
        decorate(r, ctx)
        for r in db.execute(
            "SELECT * FROM pieces WHERE status = 'kiln' OR dry_method = 'kiln' ORDER BY id"
        ).fetchall()
    ]
    drying_now = [p for p in in_kiln if p["dry_pct"] < 100]
    wettest = max((p["current_mc"] for p in drying_now if p["current_mc"] is not None), default=None)

    alerts = kiln.evaluate_alerts(
        summary,
        target_mc_pct=target,
        hours_since_last=hours_since,
        wettest_piece_mc=wettest,
        thresholds=thresholds_from(settings),
    )

    window = fetch_readings(db, hours=max(hours, 48.0))
    return {
        "charge": charge,
        "target_mc_pct": target,
        "settings": settings,
        "summary": summary,
        "hours_since": hours_since,
        "alerts": [a.as_dict() for a in alerts],
        "pieces": in_kiln,
        "wettest_mc": wettest,
        "sterilization": kiln.sterilization_window(
            window,
            temp_f=settings["sterilize_temp_f"],
            minutes=int(settings["sterilize_minutes"]),
        ),
        "readings": window,
        "calibration": _observed_tau(db, charge, summary.emc_pct, settings),
    }


def _observed_tau(
    db: sqlite3.Connection,
    charge: dict[str, Any] | None,
    _unused_emc: float | None,
    settings: dict[str, float],
) -> dict[str, Any] | None:
    """Fit the drying time constant this kiln actually achieved, if it can.

    Needs two or more ``wood_mc_pct`` readings inside the current charge --
    which is exactly what a handheld moisture meter and the manual reading form
    produce. This is what turns the shipped guess into a measured number.

    The equilibrium used for the fit is averaged over the span the moisture
    readings cover, *not* over the last 24 hours. The wood was drying toward the
    conditions that existed while it dried, and with an SD-card workflow the
    readings arrive in a batch long after the fact -- keying off "recent"
    conditions would mean the calibration never appeared at all.
    """
    if not charge:
        return None

    rows = db.execute(
        "SELECT recorded_at, wood_mc_pct FROM kiln_readings"
        " WHERE charge_id = ? AND wood_mc_pct IS NOT NULL ORDER BY recorded_at",
        (charge["id"],),
    ).fetchall()
    if len(rows) < 2:
        return None

    try:
        origin = datetime.fromisoformat(rows[0]["recorded_at"])
        observations = [
            ((datetime.fromisoformat(r["recorded_at"]) - origin).total_seconds() / 86400.0,
             float(r["wood_mc_pct"]))
            for r in rows
        ]
    except ValueError:
        return None

    span = db.execute(
        "SELECT * FROM kiln_readings WHERE charge_id = ? AND recorded_at BETWEEN ? AND ?"
        " ORDER BY recorded_at",
        (charge["id"], rows[0]["recorded_at"], rows[-1]["recorded_at"]),
    ).fetchall()
    emc_pct = kiln.summarize([dict(r) for r in span]).emc_pct
    if emc_pct is None:
        return None

    thickness = db.execute(
        "SELECT AVG(thickness_in) t FROM pieces WHERE kiln_charge_id = ? OR status = 'kiln'",
        (charge["id"],),
    ).fetchone()["t"] or 1.0

    result = kiln.calibrate_tau(observations, float(thickness), emc_pct)
    if result:
        result["configured"] = settings["tau_kiln"]
    return result


@app.route("/kiln")
def kiln_dashboard() -> str:
    """Current kiln conditions, alerts, and what is in the box."""
    db = get_db()
    context = _kiln_context(db)
    charges = [
        dict(r) for r in db.execute("SELECT * FROM kiln_charges ORDER BY id DESC LIMIT 20")
    ]
    return render_template(
        "kiln.html",
        charges=charges,
        now=datetime.now().isoformat(timespec="minutes"),
        token_set=bool(KILN_TOKEN),
        **context,
    )


@app.route("/kiln/charge", methods=["POST"])
def start_charge() -> Any:
    """Start a new kiln charge, ending any that is still open."""
    db = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    open_charge = active_charge(db)
    if open_charge:
        db.execute(
            "UPDATE kiln_charges SET ended_at = ?, updated_at = ? WHERE id = ?",
            (now, now, open_charge["id"]),
        )

    name = (request.form.get("name") or "").strip() or f"Charge {date.today().isoformat()}"
    target = _float_or_none(request.form.get("target_mc_pct")) or kiln.TARGET_MC_KILN
    db.execute(
        "INSERT INTO kiln_charges (name, started_at, target_mc_pct, notes, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (name, now, target, (request.form.get("notes") or "").strip() or None, now, now),
    )
    db.commit()
    flash(f"Started {name}.")
    return redirect(url_for("kiln_dashboard"))


@app.route("/kiln/charge/<int:charge_id>/end", methods=["POST"])
def end_charge(charge_id: int) -> Any:
    """Close out a charge."""
    db = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    db.execute(
        "UPDATE kiln_charges SET ended_at = ?, updated_at = ? WHERE id = ?", (now, now, charge_id)
    )
    db.commit()
    flash("Charge ended.")
    return redirect(url_for("kiln_dashboard"))


@app.route("/kiln/reading", methods=["POST"])
def add_reading_form() -> Any:
    """Add a reading by hand, for before the sensor exists."""
    db = get_db()
    _insert_reading(
        db,
        temp_f=_float_or_none(request.form.get("temp_f")),
        rh_pct=_float_or_none(request.form.get("rh_pct")),
        wood_mc_pct=_float_or_none(request.form.get("wood_mc_pct")),
        recorded_at=request.form.get("recorded_at") or None,
        source="manual",
    )
    db.commit()
    return redirect(url_for("kiln_dashboard"))


def _charge_for_timestamp(db: sqlite3.Connection, stamp: str) -> int | None:
    """The charge that was running at ``stamp``.

    Matters for batch imports off an SD card: a month of logged readings should
    land on the charge that was actually in the kiln at the time, not on
    whatever happens to be open when the card gets plugged in.
    """
    row = db.execute(
        "SELECT id FROM kiln_charges"
        " WHERE (started_at IS NULL OR started_at <= ?)"
        "   AND (ended_at IS NULL OR ended_at >= ?)"
        " ORDER BY id DESC LIMIT 1",
        (stamp, stamp),
    ).fetchone()
    return int(row["id"]) if row else None


def _insert_reading(
    db: sqlite3.Connection,
    *,
    temp_f: float | None,
    rh_pct: float | None,
    wood_mc_pct: float | None = None,
    recorded_at: str | None = None,
    source: str = "manual",
) -> int:
    """Store one reading against whichever charge was running at its timestamp."""
    now = datetime.now().isoformat(timespec="seconds")
    stamp = kiln.parse_timestamp(recorded_at) or now

    cur = db.execute(
        "INSERT INTO kiln_readings (charge_id, recorded_at, temp_f, rh_pct, wood_mc_pct, source, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (_charge_for_timestamp(db, stamp), stamp, temp_f, rh_pct, wood_mc_pct, source, now),
    )
    return int(cur.lastrowid)


def bulk_insert_readings(
    db: sqlite3.Connection, readings: list[dict[str, Any]], source: str
) -> dict[str, int]:
    """Insert many readings at once, skipping ones already stored.

    Deduplicates on ``(recorded_at, source)``. This is not a nicety: re-importing
    the same SD card, or a card whose window overlaps the last one, would
    otherwise double-count samples and quietly skew the cycle-average EMC that
    the predictions and alerts are built on.
    """
    if not readings:
        return {"imported": 0, "duplicates": 0}

    stamps = [r["recorded_at"] for r in readings]
    existing = {
        row["recorded_at"]
        for row in db.execute(
            "SELECT recorded_at FROM kiln_readings"
            " WHERE source = ? AND recorded_at BETWEEN ? AND ?",
            (source, min(stamps), max(stamps)),
        )
    }

    now = datetime.now().isoformat(timespec="seconds")
    rows: list[tuple[Any, ...]] = []
    seen: set[str] = set()
    duplicates = 0

    for reading in readings:
        stamp = reading["recorded_at"]
        if stamp in existing or stamp in seen:  # also dedupes within the file
            duplicates += 1
            continue
        seen.add(stamp)
        rows.append(
            (
                _charge_for_timestamp(db, stamp),
                stamp,
                reading.get("temp_f"),
                reading.get("rh_pct"),
                reading.get("wood_mc_pct"),
                source,
                now,
            )
        )

    db.executemany(
        "INSERT INTO kiln_readings (charge_id, recorded_at, temp_f, rh_pct, wood_mc_pct, source, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return {"imported": len(rows), "duplicates": duplicates}


@app.route("/kiln/import", methods=["POST"])
def import_readings() -> Any:
    """Import a CSV pulled off the logger's SD card."""
    upload = request.files.get("csv")
    if not upload or not upload.filename:
        flash("No file chosen.")
        return redirect(url_for("kiln_dashboard"))

    try:
        text = upload.read().decode("utf-8-sig", errors="replace")
    except Exception:  # noqa: BLE001 - a bad file should not 500 the app
        logger.exception("Could not read uploaded CSV")
        flash("Could not read that file.")
        return redirect(url_for("kiln_dashboard"))

    readings, problems = kiln.parse_readings_csv(text)
    source = (request.form.get("source") or "sd-card").strip()[:64]

    db = get_db()
    result = bulk_insert_readings(db, readings, source)
    db.commit()

    message = f"Imported {result['imported']} readings"
    if result["duplicates"]:
        message += f", skipped {result['duplicates']} already on file"
    if problems:
        message += f". {len(problems)} row(s) had problems: {problems[0]}"
        if len(problems) > 1:
            message += f" (and {len(problems) - 1} more)"
    flash(message + ".")
    return redirect(url_for("kiln_dashboard"))


@app.route("/kiln/charge/<int:charge_id>/edit", methods=["POST"])
def edit_charge(charge_id: int) -> Any:
    """Rename a charge, change its target, or fix its dates after the fact."""
    db = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    name = (request.form.get("name") or "").strip()
    target = _float_or_none(request.form.get("target_mc_pct"))

    db.execute(
        "UPDATE kiln_charges SET name = COALESCE(NULLIF(?, ''), name),"
        " target_mc_pct = COALESCE(?, target_mc_pct),"
        " started_at = COALESCE(NULLIF(?, ''), started_at),"
        " ended_at = NULLIF(?, ''),"
        " notes = NULLIF(?, ''), updated_at = ? WHERE id = ?",
        (
            name,
            target,
            request.form.get("started_at") or "",
            request.form.get("ended_at") or "",
            (request.form.get("notes") or "").strip(),
            now,
            charge_id,
        ),
    )
    db.commit()
    flash("Charge updated.")
    return redirect(url_for("kiln_dashboard"))


@app.route("/kiln/reading/<int:reading_id>/edit", methods=["POST"])
def edit_reading(reading_id: int) -> Any:
    """Correct a single reading.

    Sensors drift and fingers slip, and one bad sample skews the cycle-average
    EMC that every prediction leans on. Re-attaches to whichever charge covers
    the corrected timestamp.
    """
    db = get_db()
    stamp = kiln.parse_timestamp(request.form.get("recorded_at"))
    temp = _float_or_none(request.form.get("temp_f"))
    rh = _float_or_none(request.form.get("rh_pct"))
    mc = _float_or_none(request.form.get("wood_mc_pct"))

    if stamp is None or temp is None or rh is None:
        flash("A reading needs a valid time, temperature, and humidity.")
        return redirect(url_for("kiln_readings_page"))

    db.execute(
        "UPDATE kiln_readings SET recorded_at = ?, temp_f = ?, rh_pct = ?,"
        " wood_mc_pct = ?, charge_id = ? WHERE id = ?",
        (stamp, temp, rh, mc, _charge_for_timestamp(db, stamp), reading_id),
    )
    db.commit()
    flash("Reading updated.")
    return redirect(url_for("kiln_readings_page"))


@app.route("/kiln/reading/<int:reading_id>/delete", methods=["POST"])
def delete_reading(reading_id: int) -> Any:
    """Drop a bad sample."""
    db = get_db()
    db.execute("DELETE FROM kiln_readings WHERE id = ?", (reading_id,))
    db.commit()
    flash("Reading deleted.")
    return redirect(url_for("kiln_readings_page"))


@app.route("/kiln/readings/purge", methods=["POST"])
def purge_readings() -> Any:
    """Delete every reading from one source.

    The escape hatch for clearing out test or simulated data without hand-writing
    SQL against lumber.db.
    """
    source = (request.form.get("source") or "").strip()
    if not source:
        return redirect(url_for("kiln_readings_page"))
    db = get_db()
    cur = db.execute("DELETE FROM kiln_readings WHERE source = ?", (source,))
    db.commit()
    flash(f"Deleted {cur.rowcount} readings from '{source}'.")
    return redirect(url_for("kiln_readings_page"))


@app.route("/kiln/readings")
def kiln_readings_page() -> str:
    """Editable table of logged readings, newest first."""
    db = get_db()
    source = request.args.get("source", "")
    limit = max(1, min(int(_coerce_float(request.args.get("limit")) or 200), 2000))

    sql = "SELECT * FROM kiln_readings"
    params: list[Any] = []
    if source:
        sql += " WHERE source = ?"
        params.append(source)
    sql += " ORDER BY recorded_at DESC LIMIT ?"
    params.append(limit)

    readings = []
    for row in db.execute(sql, params):
        reading = dict(row)
        if reading["temp_f"] is not None and reading["rh_pct"] is not None:
            reading["emc_pct"] = kiln.emc_from_conditions(reading["temp_f"], reading["rh_pct"])
        readings.append(reading)

    sources = [
        dict(r)
        for r in db.execute(
            "SELECT source, COUNT(*) n, MIN(recorded_at) lo, MAX(recorded_at) hi"
            " FROM kiln_readings GROUP BY source ORDER BY n DESC"
        )
    ]
    return render_template(
        "kiln_readings.html", readings=readings, sources=sources, cur_source=source, limit=limit
    )


@app.route("/settings", methods=["GET", "POST"])
def settings_page() -> Any:
    """Tune the drying model, alert thresholds, and species rates.

    These ship as guesses back-calculated from rules of thumb. Once a real charge
    has been logged they should be replaced with measured numbers, and that
    should not require editing Python.
    """
    db = get_db()

    if request.method == "POST":
        for spec in SETTING_SPECS:
            put_setting(db, spec["key"], _float_or_none(request.form.get(spec["key"])))
        for name in SPECIES_PRESETS:
            put_setting(
                db,
                SPECIES_RATE_PREFIX + name,
                _float_or_none(request.form.get(SPECIES_RATE_PREFIX + name)),
            )
        db.commit()
        flash("Settings saved. Blank fields fall back to the shipped defaults.")
        return redirect(url_for("settings_page"))

    stored = {row["key"]: row["value"] for row in db.execute("SELECT key, value FROM settings")}
    return render_template(
        "settings.html",
        specs=SETTING_SPECS,
        stored=stored,
        presets=SPECIES_PRESETS,
        rates=species_rates(db),
        rate_prefix=SPECIES_RATE_PREFIX,
        effective=get_settings(db),
    )


@app.route("/settings/reset", methods=["POST"])
def reset_settings() -> Any:
    """Throw away every override and go back to the shipped defaults."""
    db = get_db()
    db.execute("DELETE FROM settings")
    db.commit()
    flash("Settings reset to defaults.")
    return redirect(url_for("settings_page"))


# --------------------------------------------------------------------------
# Listings -- getting boards in front of buyers, and tracking what happened
# --------------------------------------------------------------------------


@app.route("/listings")
def listings_page() -> str:
    """The selling desk: what can go up, what is up, what needs renewing.

    Facebook Marketplace has no API for individual sellers, so "posting" is
    copy the pack, paste, pick photos -- about a minute on the phone. What the
    app owns is everything around that: the rule that green wood never gets
    listed, the pack itself, the link back, the renew nudge, and the sale
    record that feeds the Sold report.
    """
    db = get_db()
    ctx = piece_context(db)
    pieces = [decorate(r, ctx) for r in db.execute("SELECT * FROM pieces WHERE status != 'sold'")]

    counts = {
        row["piece_id"]: row["n"]
        for row in db.execute("SELECT piece_id, COUNT(*) n FROM photos GROUP BY piece_id")
    }

    listed, ready, blocked, drying = [], [], [], []
    for piece in pieces:
        piece["photo_count"] = counts.get(piece["id"], 0)
        piece["readiness"] = listing_readiness(piece, piece["photo_count"])
        piece["days_listed"] = _days_since(piece.get("listed_at"))
        piece["days_since_bump"] = _days_since(piece.get("bumped_at"))
        piece["stale"] = (piece["days_since_bump"] or 0) >= STALE_LISTING_DAYS

        if piece["status"] == "listed":
            listed.append(piece)
        elif piece["readiness"]["ok"]:
            ready.append(piece)
        elif piece.get("already_dry") or piece.get("dry_pct") == 100:
            blocked.append(piece)  # dry, but missing a photo or a price
        else:
            drying.append(piece)

    listed.sort(key=lambda p: -(p["days_since_bump"] or 0))
    drying.sort(key=lambda p: p.get("ready_date") or "9999")

    return render_template(
        "listings.html",
        listed=listed,
        ready=ready,
        blocked=blocked,
        drying=drying,
        platforms=LISTING_PLATFORMS,
        stale_days=STALE_LISTING_DAYS,
    )


@app.route("/piece/<int:piece_id>/listed", methods=["POST"])
def mark_listed(piece_id: int) -> Any:
    """Record that a piece went up somewhere, with the link back to it."""
    db = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    platform = request.form.get("listed_on") or "facebook"
    if platform not in LISTING_PLATFORMS:
        platform = "other"
    url = (request.form.get("listing_url") or "").strip()[:500] or None
    db.execute(
        "UPDATE pieces SET status = 'listed', listed_on = ?, listing_url = ?,"
        " listed_at = COALESCE(listed_at, ?), bumped_at = ?, updated_at = ? WHERE id = ?",
        (platform, url, now, now, now, piece_id),
    )
    db.commit()
    flash("Marked listed.")
    return redirect(request.referrer or url_for("listings_page"))


@app.route("/piece/<int:piece_id>/bump", methods=["POST"])
def bump_listing(piece_id: int) -> Any:
    """You renewed or re-posted the listing; reset the stale clock."""
    db = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    db.execute("UPDATE pieces SET bumped_at = ?, updated_at = ? WHERE id = ?", (now, now, piece_id))
    db.commit()
    return redirect(request.referrer or url_for("listings_page"))


@app.route("/piece/<int:piece_id>/unlist", methods=["POST"])
def unlist_piece(piece_id: int) -> Any:
    """Take it down but keep it: back to ready, listing fields cleared."""
    db = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    db.execute(
        "UPDATE pieces SET status = 'ready', listed_on = NULL, listing_url = NULL,"
        " listed_at = NULL, bumped_at = NULL, updated_at = ? WHERE id = ?",
        (now, piece_id),
    )
    db.commit()
    return redirect(request.referrer or url_for("listings_page"))


@app.route("/piece/<int:piece_id>/sold", methods=["POST"])
def mark_sold(piece_id: int) -> Any:
    """Close the sale with the number that matters: what it actually went for."""
    db = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    price = _float_or_none(request.form.get("price_sold"))
    buyer = (request.form.get("buyer") or "").strip()[:120] or None
    db.execute(
        "UPDATE pieces SET status = 'sold', price_sold = COALESCE(?, price_sold),"
        " buyer = COALESCE(?, buyer), sold_date = COALESCE(sold_date, ?), updated_at = ? WHERE id = ?",
        (price, buyer, date.today().isoformat(), now, piece_id),
    )
    db.commit()
    flash("Sold. It is on the Sold report now.")
    return redirect(request.referrer or url_for("listings_page"))


# --------------------------------------------------------------------------
# Logs -- the round things, before they become pieces
# --------------------------------------------------------------------------


def fetch_log_photos(db: sqlite3.Connection, log_id: int) -> list[sqlite3.Row]:
    """Photo rows for a log, oldest first."""
    return db.execute(
        "SELECT * FROM log_photos WHERE log_id = ? ORDER BY id", (log_id,)
    ).fetchall()


def _decorate_log(row: sqlite3.Row) -> dict[str, Any]:
    """A log row plus its parsed AI suggestions, ready for a template."""
    log = dict(row)
    log["ai"] = None
    if log.get("ai_analysis"):
        try:
            log["ai"] = json.loads(log["ai_analysis"])
        except (TypeError, ValueError):
            log["ai"] = None
    return log


@app.route("/logs")
def log_list() -> str:
    """Logs on deck, with scale totals so the backlog has a number on it."""
    db = get_db()
    rows = db.execute("SELECT * FROM logs ORDER BY (status != 'on_deck'), id DESC").fetchall()
    logs = [_decorate_log(r) for r in rows]

    thumbs: dict[int, str] = {}
    for log in logs:
        photo = db.execute(
            "SELECT filename FROM log_photos WHERE log_id = ? ORDER BY id LIMIT 1", (log["id"],)
        ).fetchone()
        if photo:
            thumbs[log["id"]] = photo["filename"]
        if log["small_diameter_in"] and log["length_ft"]:
            log["doyle"] = logscale.doyle_bf(log["small_diameter_in"], log["length_ft"])
            log["intl"] = logscale.international_bf(
                log["small_diameter_in"], log["length_ft"], log["large_diameter_in"]
            )

    on_deck = [l for l in logs if l["status"] == "on_deck"]
    totals = {
        "count": len(on_deck),
        "intl_bf": round(sum(l.get("intl") or 0 for l in on_deck), 1),
    }
    return render_template("log_list.html", logs=logs, thumbs=thumbs, totals=totals)


@app.route("/log/new", methods=["GET", "POST"])
def new_log() -> Any:
    """Create a log record."""
    if request.method == "POST":
        log_id = _save_log(None)
        return redirect(url_for("log_detail", log_id=log_id))
    return render_template("log_form.html", log=None, presets=SPECIES_PRESETS)


@app.route("/log/<int:log_id>/edit", methods=["GET", "POST"])
def edit_log(log_id: int) -> Any:
    """Edit a log record."""
    db = get_db()
    row = db.execute("SELECT * FROM logs WHERE id = ?", (log_id,)).fetchone()
    if row is None:
        return redirect(url_for("log_list"))
    if request.method == "POST":
        _save_log(log_id)
        return redirect(url_for("log_detail", log_id=log_id))
    return render_template("log_form.html", log=_decorate_log(row), presets=SPECIES_PRESETS)


def _save_log(log_id: int | None) -> int:
    """Insert or update a log from the submitted form. Returns its id."""
    db = get_db()
    form = request.form
    now = datetime.now().isoformat(timespec="seconds")

    fields = {
        "species": (form.get("species") or "").strip() or None,
        "small_diameter_in": _float_or_none(form.get("small_diameter_in")),
        "large_diameter_in": _float_or_none(form.get("large_diameter_in")),
        "length_ft": _float_or_none(form.get("length_ft")),
        "location": (form.get("location") or "").strip() or None,
        "notes": (form.get("notes") or "").strip() or None,
        "status": form.get("status") if form.get("status") in LOG_STATUSES else "on_deck",
        "updated_at": now,
    }
    if log_id is None:
        fields["created_at"] = now
        columns = ", ".join(fields)
        placeholders = ", ".join(["?"] * len(fields))
        cur = db.execute(
            f"INSERT INTO logs ({columns}) VALUES ({placeholders})", list(fields.values())
        )
        log_id = int(cur.lastrowid)
    else:
        assignments = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"UPDATE logs SET {assignments} WHERE id = ?", [*fields.values(), log_id])

    for upload in request.files.getlist("photos"):
        if upload and upload.filename:
            filename = _store_image(upload.stream, f"log{log_id}")
            if filename:
                db.execute(
                    "INSERT INTO log_photos (log_id, filename, created_at) VALUES (?, ?, ?)",
                    (log_id, filename, now),
                )
    db.commit()
    return log_id


@app.route("/log/<int:log_id>")
def log_detail(log_id: int) -> Any:
    """One log: photos, milling plan, scales, value, and AI suggestions.

    The plan recomputes from query parameters so thickness and kerf can be
    played with without saving anything -- planning a log IS trying numbers.
    """
    db = get_db()
    row = db.execute("SELECT * FROM logs WHERE id = ?", (log_id,)).fetchone()
    if row is None:
        return redirect(url_for("log_list"))

    log = _decorate_log(row)
    settings = get_settings(db)
    kiln_assumed = settings["assume_kiln"] >= 0.5
    kerf = _coerce_float(request.args.get("kerf")) or logscale.DEFAULT_KERF_IN
    choice = request.args.get("thickness") or "rec"

    plan = None
    svg = ""
    values = None
    comparison = None
    selected_label = None
    if log["small_diameter_in"] and log["length_ft"]:
        rate = species_rates(db).get(log["species"] or "")
        comparison = logscale.compare_options(
            log["small_diameter_in"], log["length_ft"], log["species"],
            kiln_assumed, rate, kerf, log["large_diameter_in"],
        )

        # "rec" (the default view) shows the recommended option; a numeric
        # choice shows that uniform thickness -- including odd ones like 5/4
        # that are not in the comparison table.
        if choice == "rec":
            option = comparison["options"][comparison["recommended"]]
            plan = option["plan"]
            selected_label = option["label"]
        else:
            thickness = _coerce_float(choice) or 1.0
            option = next(
                (o for o in comparison["options"] if o["center"] is None and o["thickness"] == thickness),
                None,
            )
            plan = option["plan"] if option else logscale.cut_plan(
                log["small_diameter_in"], log["length_ft"], thickness, kerf,
                large_diameter_in=log["large_diameter_in"],
            )
            selected_label = option["label"] if option else f'{_trim_number(thickness)}"'

        svg = logscale.plan_svg(plan, log["small_diameter_in"])
        if rate and plan.boards:
            values = logscale.plan_value(plan, rate)

    return render_template(
        "log_detail.html",
        log=log,
        photos=fetch_log_photos(db, log_id),
        plan=plan,
        plan_svg=svg,
        values=values,
        comparison=comparison,
        selected_label=selected_label,
        choice=choice,
        kiln_assumed=kiln_assumed,
        kerf=kerf,
        statuses=LOG_STATUSES,
    )


@app.route("/log/<int:log_id>/status/<new_status>", methods=["POST"])
def set_log_status(log_id: int, new_status: str) -> Any:
    """Quick status change from the list or detail view."""
    if new_status in LOG_STATUSES:
        db = get_db()
        db.execute(
            "UPDATE logs SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, datetime.now().isoformat(timespec="seconds"), log_id),
        )
        db.commit()
    return redirect(request.referrer or url_for("log_detail", log_id=log_id))


@app.route("/log/<int:log_id>/delete", methods=["POST"])
def delete_log(log_id: int) -> Any:
    """Delete a log and its photo files."""
    db = get_db()
    for photo in fetch_log_photos(db, log_id):
        (UPLOAD_DIR / photo["filename"]).unlink(missing_ok=True)
    db.execute("DELETE FROM log_photos WHERE log_id = ?", (log_id,))
    db.execute("DELETE FROM logs WHERE id = ?", (log_id,))
    db.commit()
    flash("Log deleted.")
    return redirect(url_for("log_list"))


@app.route("/log_photo/<int:photo_id>/delete", methods=["POST"])
def delete_log_photo(photo_id: int) -> Any:
    """Delete a single log photo."""
    db = get_db()
    row = db.execute("SELECT * FROM log_photos WHERE id = ?", (photo_id,)).fetchone()
    if row is None:
        return redirect(url_for("log_list"))
    (UPLOAD_DIR / row["filename"]).unlink(missing_ok=True)
    db.execute("DELETE FROM log_photos WHERE id = ?", (photo_id,))
    db.commit()
    return redirect(url_for("log_detail", log_id=row["log_id"]))


@app.route("/log/<int:log_id>/analyze", methods=["POST"])
def analyze_log(log_id: int) -> Any:
    """Run the log's newest photo through the vision backend.

    Runs at home, not at the mill -- the model lives on this machine. The
    result is stored as suggestions on the log; nothing is written into the
    species field without the user clicking it.
    """
    db = get_db()
    row = db.execute("SELECT * FROM logs WHERE id = ?", (log_id,)).fetchone()
    if row is None:
        return redirect(url_for("log_list"))

    photos = fetch_log_photos(db, log_id)
    if not photos:
        flash("No photo to analyze -- add one first.")
        return redirect(url_for("log_detail", log_id=log_id))

    photo_path = UPLOAD_DIR / photos[-1]["filename"]
    try:
        jpeg = photo_path.read_bytes()
    except OSError:
        flash("Photo file is missing on disk.")
        return redirect(url_for("log_detail", log_id=log_id))

    try:
        result = vision.analyze_log_photo(jpeg, list(SPECIES_PRESETS))
    except vision.VisionUnavailable as exc:
        flash(str(exc))
        return redirect(url_for("log_detail", log_id=log_id))

    now = datetime.now().isoformat(timespec="seconds")
    db.execute(
        "UPDATE logs SET ai_analysis = ?, ai_analyzed_at = ?, updated_at = ? WHERE id = ?",
        (json.dumps(result), now, now, log_id),
    )
    db.commit()
    flash(f"Analyzed via {result.get('_backend', 'vision backend')}.")
    return redirect(url_for("log_detail", log_id=log_id))


@app.route("/log/<int:log_id>/species", methods=["POST"])
def accept_species(log_id: int) -> Any:
    """Accept one AI species suggestion -- the click that makes it real."""
    name = (request.form.get("name") or "").strip()
    if name:
        db = get_db()
        db.execute(
            "UPDATE logs SET species = ?, updated_at = ? WHERE id = ?",
            (name, datetime.now().isoformat(timespec="seconds"), log_id),
        )
        db.commit()
    return redirect(url_for("log_detail", log_id=log_id))


# --------------------------------------------------------------------------
# Field sync API -- offline captures from the phone
# --------------------------------------------------------------------------


def _sync_photos(db: sqlite3.Connection, photos: Any, table: str, fk: str, row_id: int, prefix: str) -> int:
    """Store base64 photos from a sync payload; returns how many landed."""
    saved = 0
    now = datetime.now().isoformat(timespec="seconds")
    for item in (photos or [])[:10]:
        b64 = item.get("b64") if isinstance(item, dict) else item
        if not isinstance(b64, str):
            continue
        filename = save_photo_b64(prefix, b64)
        if filename:
            db.execute(
                f"INSERT INTO {table} ({fk}, filename, created_at) VALUES (?, ?, ?)",
                (row_id, filename, now),
            )
            saved += 1
    return saved


@app.post("/api/pieces")
def api_add_piece() -> Any:
    """Create a piece from the field app's offline queue.

    Idempotent on ``client_uuid``: a retry after a flaky sync returns the
    existing row instead of inserting a twin.
    """
    if not _authorized():
        return jsonify({"ok": False, "error": "bad or missing token"}), 401

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "expected a JSON object"}), 400

    db = get_db()
    client_uuid = str(payload.get("client_uuid") or "")[:64] or None
    if client_uuid:
        existing = db.execute(
            "SELECT id FROM pieces WHERE client_uuid = ?", (client_uuid,)
        ).fetchone()
        if existing:
            return jsonify({"ok": True, "id": existing["id"], "deduped": True}), 200

    now = datetime.now().isoformat(timespec="seconds")
    dry_method = payload.get("dry_method") if payload.get("dry_method") in DRY_METHODS else "air"
    status = payload.get("status") if payload.get("status") in STATUSES else "green"
    fields = {
        "client_uuid": client_uuid,
        "species": str(payload.get("species") or "Unknown")[:80],
        "thickness_in": _coerce_float(payload.get("thickness_in")) or 1.0,
        "width_in": _coerce_float(payload.get("width_in")) or 1.0,
        "width_max_in": _coerce_float(payload.get("width_max_in")),
        "length_in": _coerce_float(payload.get("length_in")) or 1.0,
        "mill_date": kiln.parse_timestamp(payload.get("mill_date")) or now,
        "moisture_pct": _coerce_float(payload.get("moisture_pct")),
        "status": status,
        "dry_method": dry_method,
        "location": str(payload.get("location") or "")[:200] or None,
        "notes": str(payload.get("notes") or "")[:2000] or None,
        "already_dry": 1 if payload.get("already_dry") else 0,
        "created_at": now,
        "updated_at": now,
    }
    fields["mill_date"] = fields["mill_date"][:10]  # date, not datetime
    columns = ", ".join(fields)
    placeholders = ", ".join(["?"] * len(fields))
    cur = db.execute(f"INSERT INTO pieces ({columns}) VALUES ({placeholders})", list(fields.values()))
    piece_id = int(cur.lastrowid)
    photos = _sync_photos(db, payload.get("photos"), "photos", "piece_id", piece_id, str(piece_id))
    db.commit()
    return jsonify({"ok": True, "id": piece_id, "photos": photos}), 201


@app.post("/api/logs")
def api_add_log() -> Any:
    """Create a log record from the field app's offline queue. Idempotent."""
    if not _authorized():
        return jsonify({"ok": False, "error": "bad or missing token"}), 401

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "expected a JSON object"}), 400

    db = get_db()
    client_uuid = str(payload.get("client_uuid") or "")[:64] or None
    if client_uuid:
        existing = db.execute("SELECT id FROM logs WHERE client_uuid = ?", (client_uuid,)).fetchone()
        if existing:
            return jsonify({"ok": True, "id": existing["id"], "deduped": True}), 200

    now = datetime.now().isoformat(timespec="seconds")
    fields = {
        "client_uuid": client_uuid,
        "species": str(payload.get("species") or "")[:80] or None,
        "small_diameter_in": _coerce_float(payload.get("small_diameter_in")),
        "large_diameter_in": _coerce_float(payload.get("large_diameter_in")),
        "length_ft": _coerce_float(payload.get("length_ft")),
        "location": str(payload.get("location") or "")[:200] or None,
        "notes": str(payload.get("notes") or "")[:2000] or None,
        "status": "on_deck",
        "created_at": now,
        "updated_at": now,
    }
    columns = ", ".join(fields)
    placeholders = ", ".join(["?"] * len(fields))
    cur = db.execute(f"INSERT INTO logs ({columns}) VALUES ({placeholders})", list(fields.values()))
    log_id = int(cur.lastrowid)
    photos = _sync_photos(db, payload.get("photos"), "log_photos", "log_id", log_id, f"log{log_id}")
    db.commit()
    return jsonify({"ok": True, "id": log_id, "photos": photos}), 201


# --------------------------------------------------------------------------
# Field app (PWA)
# --------------------------------------------------------------------------


@app.route("/field")
def field_page() -> str:
    """The offline-capable capture page the phone installs.

    Species presets and statuses are baked in at render time; the service
    worker caches the rendered page, so they travel to the mill with it.
    """
    db = get_db()
    return render_template(
        "field.html",
        presets=SPECIES_PRESETS,
        rates=species_rates(db),
        assume_kiln=get_settings(db)["assume_kiln"] >= 0.5,
        statuses=STATUSES,
        dry_methods=DRY_METHODS,
    )


@app.route("/sw.js")
def service_worker() -> Any:
    """Serve the service worker from the root so its scope covers the app."""
    response = send_from_directory(BASE_DIR / "static", "sw.js", mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/manifest.webmanifest")
def manifest() -> Any:
    return send_from_directory(
        BASE_DIR / "static", "manifest.webmanifest", mimetype="application/manifest+json"
    )


# --------------------------------------------------------------------------
# Sensor API -- for the IoT device
# --------------------------------------------------------------------------


def _authorized() -> bool:
    """Check the shared token when one is configured.

    Accepts either an ``X-Kiln-Token`` header or a ``token`` field in the body,
    because a microcontroller HTTP client may only manage one of the two.
    """
    if not KILN_TOKEN:
        return True
    supplied = request.headers.get("X-Kiln-Token", "")
    if not supplied and request.is_json:
        # The bulk endpoint accepts a bare array, so the body is not always a
        # dict. Only a mapping can carry a token field.
        body = request.get_json(silent=True)
        if isinstance(body, dict):
            supplied = str(body.get("token", ""))
    return secrets.compare_digest(supplied, KILN_TOKEN)


@app.post("/api/kiln/reading")
def api_add_reading() -> Any:
    """Ingest one sensor reading.

    POST JSON::

        {"temp_f": 118.4, "rh_pct": 42.1, "recorded_at": "2026-08-27T14:05:00",
         "wood_mc_pct": 14.2, "source": "kiln-esp32-1"}

    ``temp_f`` and ``rh_pct`` are required; everything else is optional and
    ``recorded_at`` defaults to now, so a device with no clock can just POST.
    """
    if not _authorized():
        return jsonify({"ok": False, "error": "bad or missing token"}), 401

    payload = request.get_json(silent=True) or {}
    temp = payload.get("temp_f")
    rh = payload.get("rh_pct")
    if temp is None or rh is None:
        return jsonify({"ok": False, "error": "temp_f and rh_pct are required"}), 400

    try:
        temp_f = float(temp)
        rh_pct = float(rh)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "temp_f and rh_pct must be numbers"}), 400

    db = get_db()
    reading_id = _insert_reading(
        db,
        temp_f=temp_f,
        rh_pct=rh_pct,
        wood_mc_pct=_coerce_float(payload.get("wood_mc_pct")),
        recorded_at=payload.get("recorded_at"),
        source=str(payload.get("source") or "device")[:64],
    )
    db.commit()

    emc = kiln.emc_from_conditions(temp_f, rh_pct)
    return jsonify({"ok": True, "id": reading_id, "emc_pct": emc}), 201


@app.post("/api/kiln/readings")
def api_add_readings_bulk() -> Any:
    """Ingest a batch of readings, for a device that buffers and flushes.

    POST JSON::

        {"source": "kiln-logger-1",
         "readings": [{"recorded_at": "2026-08-27T14:00:00", "temp_f": 118, "rh_pct": 42},
                      {"recorded_at": "2026-08-27T14:15:00", "temp_f": 120, "rh_pct": 41}]}

    A bare array is accepted too. Timestamps are required here -- unlike the
    single-reading endpoint, "now" is meaningless for a backlog, and defaulting
    would stamp a week of history to one instant and wreck the cycle average.

    Re-sending an overlapping batch is safe: duplicates are counted, not stored.
    """
    if not _authorized():
        return jsonify({"ok": False, "error": "bad or missing token"}), 401

    payload = request.get_json(silent=True)
    if isinstance(payload, list):
        payload = {"readings": payload}
    if not isinstance(payload, dict) or not isinstance(payload.get("readings"), list):
        return jsonify({"ok": False, "error": "expected a readings array"}), 400

    raw = payload["readings"][: kiln.MAX_IMPORT_ROWS]
    source = str(payload.get("source") or "device")[:64]

    readings: list[dict[str, Any]] = []
    rejected = 0
    for item in raw:
        if not isinstance(item, dict):
            rejected += 1
            continue
        temp = _coerce_float(item.get("temp_f"))
        if temp is None:
            celsius = _coerce_float(item.get("temp_c"))
            temp = celsius * 9.0 / 5.0 + 32.0 if celsius is not None else None
        rh = _coerce_float(item.get("rh_pct"))
        stamp = kiln.parse_timestamp(item.get("recorded_at"))
        if temp is None or rh is None or stamp is None:
            rejected += 1
            continue
        readings.append(
            {
                "recorded_at": stamp,
                "temp_f": temp,
                "rh_pct": rh,
                "wood_mc_pct": _coerce_float(item.get("wood_mc_pct")),
            }
        )

    db = get_db()
    result = bulk_insert_readings(db, readings, source)
    db.commit()
    return jsonify({"ok": True, "rejected": rejected, **result}), 201


@app.get("/api/kiln/readings")
def api_readings() -> Any:
    """Recent readings as JSON. ``?hours=48`` to widen the window."""
    hours = _coerce_float(request.args.get("hours")) or 24.0
    rows = fetch_readings(get_db(), hours=min(max(hours, 0.1), 24 * 90))
    for row in rows:
        if row.get("temp_f") is not None and row.get("rh_pct") is not None:
            row["emc_pct"] = kiln.emc_from_conditions(row["temp_f"], row["rh_pct"])
    return jsonify({"ok": True, "count": len(rows), "readings": rows})


@app.get("/api/kiln/status")
def api_status() -> Any:
    """Current conditions, EMC, and active alerts.

    Built for the device to poll: if it has a screen or an LED, this is the
    endpoint that tells it whether the kiln is healthy.
    """
    context = _kiln_context(get_db())
    return jsonify(
        {
            "ok": True,
            "charge": context["charge"]["name"] if context["charge"] else None,
            "target_mc_pct": context["target_mc_pct"],
            "conditions": context["summary"].as_dict(),
            "hours_since_last_reading": context["hours_since"],
            "alerts": context["alerts"],
            "sterilization": context["sterilization"],
            "pieces_drying": len([p for p in context["pieces"] if p["dry_pct"] < 100]),
        }
    )


def _coerce_float(value: Any) -> float | None:
    """Float or None, for values arriving from JSON or a query string."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@app.template_filter("money")
def money_filter(value: float | None) -> str:
    """Render a number as dollars, or an em dash when empty."""
    if value is None:
        return "—"
    return f"${value:,.2f}"


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def _lan_ip() -> str:
    """Best-effort LAN IP so the phone can reach the server."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
    except OSError:
        return "127.0.0.1"


def main(argv: Iterable[str] | None = None) -> None:
    """Parse arguments and start the development server."""
    parser = argparse.ArgumentParser(description="Lumber inventory tracker")
    parser.add_argument("--host", default="0.0.0.0", help="bind address")
    parser.add_argument("--port", type=int, default=5300, help="port")
    parser.add_argument("--debug", action="store_true", help="enable reloader")
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    init_db()

    logger.info("On this PC:  http://127.0.0.1:%d", args.port)
    logger.info("On your phone: http://%s:%d", _lan_ip(), args.port)
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
