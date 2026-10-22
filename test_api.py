"""Tests for the kiln sensor API.

This is the contract a microcontroller in the kiln will code against, so it is
worth pinning down: what is required, what defaults, and what happens when the
device sends junk or the wrong token.

Each test gets its own temporary database so nothing here touches lumber.db.

Run:
    python -m pytest test_api.py -q
"""

from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path
from typing import Any, Iterator

import pytest


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """A Flask test client backed by a throwaway database."""
    import app as app_module

    importlib.reload(app_module)
    monkeypatch.setattr(app_module, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(app_module, "KILN_TOKEN", "")
    app_module.init_db()
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as test_client:
        test_client.module = app_module  # type: ignore[attr-defined]
        yield test_client


def _rows(client: Any) -> list[sqlite3.Row]:
    conn = sqlite3.connect(client.module.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM kiln_readings ORDER BY id").fetchall()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Ingest
# --------------------------------------------------------------------------


def test_minimal_payload_is_accepted(client: Any) -> None:
    """A board with no clock and no config should be able to just POST."""
    response = client.post("/api/kiln/reading", json={"temp_f": 118.4, "rh_pct": 42.1})
    assert response.status_code == 201
    body = response.get_json()
    assert body["ok"] is True
    assert body["emc_pct"] == pytest.approx(6.5, abs=1.0)

    rows = _rows(client)
    assert len(rows) == 1
    assert rows[0]["recorded_at"]  # defaulted to now
    assert rows[0]["source"] == "device"


def test_missing_fields_are_rejected(client: Any) -> None:
    assert client.post("/api/kiln/reading", json={"temp_f": 100}).status_code == 400
    assert client.post("/api/kiln/reading", json={"rh_pct": 50}).status_code == 400
    assert client.post("/api/kiln/reading", json={}).status_code == 400


def test_non_numeric_values_are_rejected_not_stored(client: Any) -> None:
    """A flaky sensor sending 'nan' as a string should not poison the series."""
    response = client.post("/api/kiln/reading", json={"temp_f": "hot", "rh_pct": 40})
    assert response.status_code == 400
    assert _rows(client) == []


def test_supplied_timestamp_is_used_and_z_suffix_tolerated(client: Any) -> None:
    client.post(
        "/api/kiln/reading",
        json={"temp_f": 120, "rh_pct": 40, "recorded_at": "2026-08-20T10:30:00Z"},
    )
    assert _rows(client)[0]["recorded_at"] == "2026-08-20T10:30:00"


def test_garbage_timestamp_falls_back_to_now(client: Any) -> None:
    """Better a reading with the wrong time than a 500 and a lost sample."""
    response = client.post(
        "/api/kiln/reading", json={"temp_f": 120, "rh_pct": 40, "recorded_at": "yesterday-ish"}
    )
    assert response.status_code == 201
    assert _rows(client)[0]["recorded_at"]


def test_reading_attaches_to_the_open_charge(client: Any) -> None:
    client.post("/kiln/charge", data={"name": "Pecan #1", "target_mc_pct": "8"})
    client.post("/api/kiln/reading", json={"temp_f": 120, "rh_pct": 40})
    assert _rows(client)[0]["charge_id"] == 1


def test_source_is_truncated_not_unbounded(client: Any) -> None:
    client.post("/api/kiln/reading", json={"temp_f": 120, "rh_pct": 40, "source": "x" * 500})
    assert len(_rows(client)[0]["source"]) == 64


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------


def test_token_is_required_when_configured(client: Any) -> None:
    client.module.KILN_TOKEN = "s3cret"
    assert client.post("/api/kiln/reading", json={"temp_f": 120, "rh_pct": 40}).status_code == 401
    assert _rows(client) == []


def test_token_accepted_in_header(client: Any) -> None:
    client.module.KILN_TOKEN = "s3cret"
    response = client.post(
        "/api/kiln/reading", json={"temp_f": 120, "rh_pct": 40}, headers={"X-Kiln-Token": "s3cret"}
    )
    assert response.status_code == 201


def test_token_accepted_in_body_for_limited_http_clients(client: Any) -> None:
    client.module.KILN_TOKEN = "s3cret"
    response = client.post(
        "/api/kiln/reading", json={"temp_f": 120, "rh_pct": 40, "token": "s3cret"}
    )
    assert response.status_code == 201


def test_wrong_token_is_rejected(client: Any) -> None:
    client.module.KILN_TOKEN = "s3cret"
    response = client.post(
        "/api/kiln/reading", json={"temp_f": 120, "rh_pct": 40}, headers={"X-Kiln-Token": "nope"}
    )
    assert response.status_code == 401


def test_reads_stay_open_when_writes_are_locked(client: Any) -> None:
    """The token guards writes. A dashboard or display can still poll status."""
    client.module.KILN_TOKEN = "s3cret"
    assert client.get("/api/kiln/status").status_code == 200
    assert client.get("/api/kiln/readings").status_code == 200


# --------------------------------------------------------------------------
# Read-back
# --------------------------------------------------------------------------


def test_status_reports_alerts_and_survives_an_empty_kiln(client: Any) -> None:
    body = client.get("/api/kiln/status").get_json()
    assert body["ok"] is True
    assert {a["code"] for a in body["alerts"]} == {"no_readings"}


def test_readings_endpoint_annotates_each_sample_with_emc(client: Any) -> None:
    client.post("/api/kiln/reading", json={"temp_f": 120, "rh_pct": 40})
    body = client.get("/api/kiln/readings?hours=48").get_json()
    assert body["count"] == 1
    assert body["readings"][0]["emc_pct"] == pytest.approx(6.8, abs=0.5)


def test_absurd_hours_window_is_clamped_not_fatal(client: Any) -> None:
    assert client.get("/api/kiln/readings?hours=999999").status_code == 200
    assert client.get("/api/kiln/readings?hours=-5").status_code == 200
    assert client.get("/api/kiln/readings?hours=banana").status_code == 200


# --------------------------------------------------------------------------
# Migration
# --------------------------------------------------------------------------


def test_migration_adds_columns_to_a_pre_kiln_database(tmp_path: Path) -> None:
    """An existing lumber.db from before the kiln work must survive the upgrade."""
    import app as app_module

    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE pieces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            species TEXT NOT NULL, thickness_in REAL NOT NULL,
            width_in REAL NOT NULL, width_max_in REAL, length_in REAL NOT NULL,
            mill_date TEXT, moisture_pct REAL, status TEXT NOT NULL DEFAULT 'green',
            location TEXT, price_asking REAL, price_sold REAL, sold_date TEXT,
            buyer TEXT, notes TEXT, already_dry INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        INSERT INTO pieces (species, thickness_in, width_in, length_in, created_at, updated_at)
        VALUES ('Pecan', 1.5, 9, 87, '2026-08-01', '2026-08-01');
        """
    )
    conn.commit()
    conn.close()

    conn = sqlite3.connect(db_path)
    app_module._migrate_pieces(conn)
    conn.commit()
    columns = {row[1] for row in conn.execute("PRAGMA table_info(pieces)")}
    row = conn.execute("SELECT * FROM pieces").fetchone()
    conn.close()

    assert {"dry_method", "kiln_charge_id", "target_mc_pct"} <= columns
    assert row is not None  # the existing piece is still there


def test_migration_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """init_db runs on every start, so running it twice must be a no-op."""
    import app as app_module

    monkeypatch.setattr(app_module, "DB_PATH", tmp_path / "twice.db")
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path / "uploads")
    app_module.init_db()
    app_module.init_db()  # would raise "duplicate column name" if not guarded


# --------------------------------------------------------------------------
# Batch import
# --------------------------------------------------------------------------


def _batch(n: int, start_hour: int = 0) -> list[dict[str, Any]]:
    return [
        {
            "recorded_at": f"2026-08-20T{start_hour + i:02d}:00:00",
            "temp_f": 110 + i,
            "rh_pct": 45 - i,
        }
        for i in range(n)
    ]


def test_bulk_post_imports_a_batch(client: Any) -> None:
    response = client.post(
        "/api/kiln/readings", json={"source": "kiln-logger-1", "readings": _batch(5)}
    )
    assert response.status_code == 201
    body = response.get_json()
    assert body["imported"] == 5 and body["duplicates"] == 0
    assert len(_rows(client)) == 5


def test_bulk_post_accepts_a_bare_array(client: Any) -> None:
    assert client.post("/api/kiln/readings", json=_batch(3)).status_code == 201
    assert len(_rows(client)) == 3


def test_reimporting_the_same_batch_stores_nothing_twice(client: Any) -> None:
    """Re-plugging the same SD card must not skew the cycle-average EMC."""
    client.post("/api/kiln/readings", json={"source": "sd", "readings": _batch(5)})
    body = client.post("/api/kiln/readings", json={"source": "sd", "readings": _batch(5)}).get_json()
    assert body["imported"] == 0 and body["duplicates"] == 5
    assert len(_rows(client)) == 5


def test_overlapping_batches_import_only_the_new_part(client: Any) -> None:
    client.post("/api/kiln/readings", json={"source": "sd", "readings": _batch(5, 0)})
    body = client.post("/api/kiln/readings", json={"source": "sd", "readings": _batch(5, 3)}).get_json()
    assert body["imported"] == 3 and body["duplicates"] == 2
    assert len(_rows(client)) == 8


def test_duplicates_within_one_batch_are_collapsed(client: Any) -> None:
    dupes = _batch(2) + _batch(2)
    body = client.post("/api/kiln/readings", json={"source": "sd", "readings": dupes}).get_json()
    assert body["imported"] == 2 and body["duplicates"] == 2


def test_different_sources_are_not_treated_as_duplicates(client: Any) -> None:
    """Two sensors in the same box legitimately report the same instant."""
    client.post("/api/kiln/readings", json={"source": "top", "readings": _batch(3)})
    body = client.post("/api/kiln/readings", json={"source": "bottom", "readings": _batch(3)}).get_json()
    assert body["imported"] == 3
    assert len(_rows(client)) == 6


def test_bulk_post_requires_timestamps(client: Any) -> None:
    """'Now' is meaningless for a backlog -- it would stamp history to one instant."""
    body = client.post(
        "/api/kiln/readings", json={"readings": [{"temp_f": 110, "rh_pct": 40}]}
    ).get_json()
    assert body["imported"] == 0 and body["rejected"] == 1


def test_bulk_post_rejects_junk_rows_but_keeps_the_good_ones(client: Any) -> None:
    mixed = [
        {"recorded_at": "2026-08-20T01:00:00", "temp_f": 110, "rh_pct": 40},
        {"recorded_at": "2026-08-20T02:00:00", "temp_f": "hot", "rh_pct": 40},
        "not even a dict",
        {"recorded_at": "2026-08-20T03:00:00", "temp_c": 45, "rh_pct": 38},
    ]
    body = client.post("/api/kiln/readings", json={"readings": mixed}).get_json()
    assert body["imported"] == 2 and body["rejected"] == 2
    assert _rows(client)[1]["temp_f"] == pytest.approx(113.0)


def test_bulk_post_rejects_a_malformed_body(client: Any) -> None:
    assert client.post("/api/kiln/readings", json={"nope": 1}).status_code == 400


def test_bulk_post_honours_the_token(client: Any) -> None:
    client.module.KILN_TOKEN = "s3cret"
    assert client.post("/api/kiln/readings", json=_batch(2)).status_code == 401


def test_imported_readings_attach_to_the_charge_that_was_open_then(client: Any) -> None:
    """A month off an SD card belongs to the charge that was actually in the box."""
    conn = sqlite3.connect(client.module.DB_PATH)
    conn.execute(
        "INSERT INTO kiln_charges (id, name, started_at, ended_at, target_mc_pct, created_at, updated_at)"
        " VALUES (1, 'Old', '2026-08-01T00:00:00', '2026-08-10T00:00:00', 8, 'x', 'x'),"
        "        (2, 'Current', '2026-08-19T00:00:00', NULL, 8, 'x', 'x')",
    )
    conn.commit()
    conn.close()

    client.post("/api/kiln/readings", json={"readings": [
        {"recorded_at": "2026-08-05T12:00:00", "temp_f": 110, "rh_pct": 40},  # during Old
        {"recorded_at": "2026-08-20T12:00:00", "temp_f": 110, "rh_pct": 40},  # during Current
        {"recorded_at": "2026-08-15T12:00:00", "temp_f": 110, "rh_pct": 40},  # between charges
    ]})
    charges = [r["charge_id"] for r in _rows(client)]
    assert charges == [1, 2, None]


# --------------------------------------------------------------------------
# CSV upload
# --------------------------------------------------------------------------


def test_csv_upload_imports_and_reports(client: Any) -> None:
    import io

    csv = b"timestamp,temp_c,humidity\n2026-08-20T10:00:00,45,40\n2026-08-20T10:15:00,46,39\n"
    response = client.post(
        "/kiln/import",
        data={"csv": (io.BytesIO(csv), "KILNLOG.CSV"), "source": "sd-card"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Imported 2 readings" in response.data
    assert _rows(client)[0]["temp_f"] == pytest.approx(113.0)


def test_csv_upload_with_no_file_is_handled(client: Any) -> None:
    response = client.post(
        "/kiln/import", data={}, content_type="multipart/form-data", follow_redirects=True
    )
    assert response.status_code == 200
    assert b"No file chosen" in response.data


def test_csv_upload_tolerates_a_bom_from_windows(client: Any) -> None:
    import io

    csv = "﻿timestamp,temp_f,rh_pct\n2026-08-20T10:00:00,110,40\n".encode("utf-8")
    client.post(
        "/kiln/import",
        data={"csv": (io.BytesIO(csv), "log.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert len(_rows(client)) == 1


def test_bare_array_with_token_auth_does_not_crash(client: Any) -> None:
    """Regression: _authorized() assumed a dict body and 500'd on a bare array."""
    client.module.KILN_TOKEN = "s3cret"
    assert client.post("/api/kiln/readings", json=_batch(2)).status_code == 401
    ok = client.post("/api/kiln/readings", json=_batch(2), headers={"X-Kiln-Token": "s3cret"})
    assert ok.status_code == 201


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


def test_settings_page_renders(client: Any) -> None:
    assert client.get("/settings").status_code == 200


def test_saving_a_setting_changes_the_prediction(client: Any) -> None:
    """The whole point: tune the model without editing Python."""
    client.post("/piece/new", data={
        "species": "Pecan", "length_in": "87", "width_in": "9", "thickness_in": "1.5",
        "mill_date": "2026-01-01", "moisture_pct": "20", "dry_method": "kiln",
        "target_mc_pct": "8", "status": "kiln",
    })
    client.post("/api/kiln/reading", json={"temp_f": 125, "rh_pct": 35})

    def days() -> str:
        return client.get("/piece/1").get_data(as_text=True)

    before = days()
    client.post("/settings", data={"tau_kiln": "60"})
    assert days() != before


def test_blank_setting_falls_back_to_the_shipped_default(client: Any) -> None:
    client.post("/settings", data={"tau_kiln": "60"})
    assert client.module.get_settings(_conn(client))["tau_kiln"] == 60.0
    client.post("/settings", data={"tau_kiln": ""})
    assert client.module.get_settings(_conn(client))["tau_kiln"] == client.module.SETTING_DEFAULTS["tau_kiln"]


def test_junk_setting_value_is_ignored_not_stored_as_zero(client: Any) -> None:
    """A zeroed time constant would divide by nothing and wreck every prediction."""
    client.post("/settings", data={"tau_kiln": "not a number"})
    assert client.module.get_settings(_conn(client))["tau_kiln"] == client.module.SETTING_DEFAULTS["tau_kiln"]


def test_species_rate_override_changes_the_suggested_price(client: Any) -> None:
    client.post("/settings", data={"rate:Pecan": "20"})
    assert client.module.species_rates(_conn(client))["Pecan"] == 20.0


def test_reset_clears_every_override(client: Any) -> None:
    client.post("/settings", data={"tau_kiln": "60", "rate:Pecan": "20"})
    client.post("/settings/reset")
    settings = client.module.get_settings(_conn(client))
    assert settings["tau_kiln"] == client.module.SETTING_DEFAULTS["tau_kiln"]
    assert client.module.species_rates(_conn(client))["Pecan"] == 12.0


def _conn(client: Any) -> sqlite3.Connection:
    conn = sqlite3.connect(client.module.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# --------------------------------------------------------------------------
# Editing readings
# --------------------------------------------------------------------------


def test_reading_can_be_corrected(client: Any) -> None:
    client.post("/api/kiln/reading", json={"temp_f": 999, "rh_pct": 40,
                                           "recorded_at": "2026-08-20T10:00:00"})
    client.post("/kiln/reading/1/edit", data={
        "recorded_at": "2026-08-20T10:00", "temp_f": "120", "rh_pct": "40", "wood_mc_pct": "18",
    })
    row = _rows(client)[0]
    assert row["temp_f"] == 120 and row["wood_mc_pct"] == 18


def test_editing_a_timestamp_reattaches_to_the_right_charge(client: Any) -> None:
    conn = _conn(client)
    conn.execute(
        "INSERT INTO kiln_charges (id, name, started_at, ended_at, target_mc_pct, created_at, updated_at)"
        " VALUES (1, 'Old', '2026-08-01T00:00:00', '2026-08-10T00:00:00', 8, 'x', 'x')")
    conn.commit(); conn.close()

    client.post("/api/kiln/reading", json={"temp_f": 120, "rh_pct": 40,
                                           "recorded_at": "2026-08-20T10:00:00"})
    assert _rows(client)[0]["charge_id"] is None
    client.post("/kiln/reading/1/edit", data={
        "recorded_at": "2026-08-05T10:00", "temp_f": "120", "rh_pct": "40"})
    assert _rows(client)[0]["charge_id"] == 1


def test_editing_a_reading_rejects_incomplete_input(client: Any) -> None:
    client.post("/api/kiln/reading", json={"temp_f": 120, "rh_pct": 40})
    client.post("/kiln/reading/1/edit", data={"recorded_at": "", "temp_f": "", "rh_pct": ""})
    assert _rows(client)[0]["temp_f"] == 120  # unchanged


def test_reading_can_be_deleted(client: Any) -> None:
    client.post("/api/kiln/reading", json={"temp_f": 120, "rh_pct": 40})
    client.post("/kiln/reading/1/delete")
    assert _rows(client) == []


def test_purge_by_source_clears_test_data(client: Any) -> None:
    client.post("/api/kiln/readings", json={"source": "sim", "readings": _batch(5)})
    client.post("/api/kiln/readings", json={"source": "real", "readings": _batch(3, 12)})
    client.post("/kiln/readings/purge", data={"source": "sim"})
    assert {r["source"] for r in _rows(client)} == {"real"}


def test_readings_page_renders(client: Any) -> None:
    client.post("/api/kiln/reading", json={"temp_f": 120, "rh_pct": 40})
    assert client.get("/kiln/readings").status_code == 200
    assert client.get("/kiln/readings?source=device&limit=10").status_code == 200


# --------------------------------------------------------------------------
# Editing charges
# --------------------------------------------------------------------------


def test_charge_can_be_renamed_and_retargeted(client: Any) -> None:
    client.post("/kiln/charge", data={"name": "Pecan #1", "target_mc_pct": "8"})
    client.post("/kiln/charge/1/edit", data={"name": "Pecan batch one", "target_mc_pct": "6.5"})
    conn = _conn(client)
    row = conn.execute("SELECT * FROM kiln_charges WHERE id = 1").fetchone()
    conn.close()
    assert row["name"] == "Pecan batch one" and row["target_mc_pct"] == 6.5


def test_blank_charge_name_keeps_the_existing_one(client: Any) -> None:
    client.post("/kiln/charge", data={"name": "Pecan #1", "target_mc_pct": "8"})
    client.post("/kiln/charge/1/edit", data={"name": "", "target_mc_pct": ""})
    conn = _conn(client)
    row = conn.execute("SELECT * FROM kiln_charges WHERE id = 1").fetchone()
    conn.close()
    assert row["name"] == "Pecan #1" and row["target_mc_pct"] == 8.0


def _open_charge_since(client: Any, started_at: str) -> None:
    """A charge that was already running before the readings were logged."""
    conn = _conn(client)
    conn.execute(
        "INSERT INTO kiln_charges (id, name, started_at, ended_at, target_mc_pct, created_at, updated_at)"
        " VALUES (1, 'Pecan #1', ?, NULL, 8, 'x', 'x')", (started_at,))
    conn.commit(); conn.close()


def test_calibration_appears_once_two_moisture_readings_exist(client: Any) -> None:
    """End to end: the kiln page fits tau from wood MC readings in the charge."""
    _open_charge_since(client, "2026-07-31T00:00:00")
    client.post("/piece/new", data={
        "species": "Pecan", "length_in": "87", "width_in": "9", "thickness_in": "1.5",
        "mill_date": "2026-08-01", "dry_method": "kiln", "status": "kiln"})
    for day, mc in ((1, 22.0), (8, 17.0), (15, 13.5), (22, 11.0)):
        client.post("/api/kiln/reading", json={
            "temp_f": 125, "rh_pct": 35, "wood_mc_pct": mc,
            "recorded_at": f"2026-08-{day:02d}T12:00:00"})
    body = client.get("/kiln").get_data(as_text=True)
    assert "measured time constant" in body


def test_readings_before_a_charge_started_do_not_join_it(client: Any) -> None:
    """Guards the above: attachment is by timestamp, not by 'whatever is open'."""
    _open_charge_since(client, "2026-08-10T00:00:00")
    client.post("/api/kiln/reading", json={
        "temp_f": 120, "rh_pct": 40, "recorded_at": "2026-08-01T12:00:00"})
    assert _rows(client)[0]["charge_id"] is None


def test_calibration_works_on_a_stale_batch_from_an_sd_card(client: Any) -> None:
    """Regression: it keyed off the last 24 h of EMC, so a weekly card upload
    would never produce a calibration even with plenty of moisture readings."""
    _open_charge_since(client, "2020-02-01T00:00:00")
    old = [
        {"recorded_at": f"2020-03-{day:02d}T12:00:00", "temp_f": 125, "rh_pct": 35,
         "wood_mc_pct": mc}
        for day, mc in ((1, 22.0), (8, 17.0), (15, 13.5), (22, 11.0))
    ]
    client.post("/api/kiln/readings", json={"source": "sd-card", "readings": old})
    assert "measured time constant" in client.get("/kiln").get_data(as_text=True)


# --------------------------------------------------------------------------
# Field sync: logs and pieces
# --------------------------------------------------------------------------

TINY_JPEG_B64 = (
    "data:image/jpeg;base64,"
    "/9j/4AAQSkZJRgABAQEAAAAAAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB"
    "AAAAAAAAAAAAAAAAAAAAAv/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AVN//2Q=="
)


def test_field_page_and_pwa_assets_are_served(client: Any) -> None:
    assert client.get("/field").status_code == 200
    assert client.get("/sw.js").status_code == 200
    assert client.get("/manifest.webmanifest").status_code == 200
    assert b"Fencerow" in client.get("/manifest.webmanifest").data


def test_sync_log_creates_a_record_with_photo(client: Any) -> None:
    response = client.post("/api/logs", json={
        "client_uuid": "abc-123", "species": "Pecan",
        "small_diameter_in": 16, "length_ft": 10,
        "photos": [{"b64": TINY_JPEG_B64}],
    })
    assert response.status_code == 201
    body = response.get_json()
    assert body["ok"] and body["photos"] == 1

    conn = _conn(client)
    row = conn.execute("SELECT * FROM logs").fetchone()
    photo = conn.execute("SELECT * FROM log_photos").fetchone()
    conn.close()
    assert row["species"] == "Pecan" and row["client_uuid"] == "abc-123"
    assert photo is not None
    assert (client.module.UPLOAD_DIR / photo["filename"]).exists()


def test_sync_log_is_idempotent_on_client_uuid(client: Any) -> None:
    """A retry after a dropped cell connection must not create a twin."""
    payload = {"client_uuid": "abc-123", "species": "Pecan", "small_diameter_in": 16, "length_ft": 10}
    first = client.post("/api/logs", json=payload)
    second = client.post("/api/logs", json=payload)
    assert first.status_code == 201
    assert second.status_code == 200 and second.get_json()["deduped"] is True
    conn = _conn(client)
    assert conn.execute("SELECT COUNT(*) c FROM logs").fetchone()["c"] == 1
    conn.close()


def test_sync_piece_is_idempotent_and_validated(client: Any) -> None:
    payload = {"client_uuid": "p-1", "species": "Pecan", "thickness_in": 1.5,
               "width_in": 9, "length_in": 87, "status": "green", "dry_method": "kiln"}
    assert client.post("/api/pieces", json=payload).status_code == 201
    assert client.post("/api/pieces", json=payload).get_json()["deduped"] is True

    # Junk status and method fall back to safe defaults rather than erroring.
    bad = {"client_uuid": "p-2", "species": "Pecan", "thickness_in": 1,
           "width_in": 5, "length_in": 40, "status": "stolen", "dry_method": "microwave"}
    client.post("/api/pieces", json=bad)
    conn = _conn(client)
    row = conn.execute("SELECT * FROM pieces WHERE client_uuid = 'p-2'").fetchone()
    conn.close()
    assert row["status"] == "green" and row["dry_method"] == "air"


def test_sync_endpoints_honor_the_token(client: Any) -> None:
    client.module.KILN_TOKEN = "s3cret"
    assert client.post("/api/logs", json={"species": "Pecan"}).status_code == 401
    assert client.post("/api/pieces", json={"species": "Pecan"}).status_code == 401
    ok = client.post("/api/logs", json={"species": "Pecan"}, headers={"X-Kiln-Token": "s3cret"})
    assert ok.status_code == 201


def test_sync_rejects_garbage_photo_without_losing_the_record(client: Any) -> None:
    response = client.post("/api/logs", json={
        "species": "Pecan", "small_diameter_in": 12, "length_ft": 8,
        "photos": [{"b64": "data:image/jpeg;base64,not-actual-base64!!!"}, "not a dict either"],
    })
    assert response.status_code == 201
    assert response.get_json()["photos"] == 0  # record saved, junk photos dropped


# --------------------------------------------------------------------------
# Log pages + analyze
# --------------------------------------------------------------------------


def _make_log(client: Any, with_photo: bool = False) -> int:
    data = {"species": "Pecan", "small_diameter_in": "16", "length_ft": "10"}
    client.post("/log/new", data=data)
    conn = _conn(client)
    log_id = conn.execute("SELECT id FROM logs ORDER BY id DESC").fetchone()["id"]
    if with_photo:
        import base64 as b64mod
        filename = client.module.save_photo_b64(f"log{log_id}", TINY_JPEG_B64)
        conn.execute("INSERT INTO log_photos (log_id, filename, created_at) VALUES (?, ?, 'x')",
                     (log_id, filename))
        conn.commit()
    conn.close()
    return int(log_id)


def test_log_detail_renders_plan_and_scales(client: Any) -> None:
    log_id = _make_log(client)
    body = client.get(f"/log/{log_id}").get_data(as_text=True)
    assert "Milling plan" in body and "Doyle" in body and "box the heart" in body


def test_log_detail_thickness_param_changes_the_plan(client: Any) -> None:
    log_id = _make_log(client)
    plan_44 = client.get(f"/log/{log_id}?thickness=1.0").get_data(as_text=True)
    plan_84 = client.get(f"/log/{log_id}?thickness=2.0").get_data(as_text=True)
    assert plan_44 != plan_84


def test_analyze_stores_suggestions_without_touching_species(client: Any, monkeypatch: Any) -> None:
    """The model proposes; only the user's click on 'use this' disposes."""
    log_id = _make_log(client, with_photo=True)

    monkeypatch.setattr(client.module.vision, "analyze_log_photo", lambda jpeg, hints: {
        "species_candidates": [{"name": "Black Walnut", "confidence": 0.8, "why": "dark heart"}],
        "defects": [], "milling_advice": "", "caution": "", "_backend": "local (test)",
    })
    client.post(f"/log/{log_id}/analyze")

    conn = _conn(client)
    row = conn.execute("SELECT * FROM logs WHERE id = ?", (log_id,)).fetchone()
    conn.close()
    assert row["species"] == "Pecan"  # unchanged: suggestion only
    assert "Black Walnut" in row["ai_analysis"]

    # Accepting the suggestion is what writes it.
    client.post(f"/log/{log_id}/species", data={"name": "Black Walnut"})
    conn = _conn(client)
    assert conn.execute("SELECT species FROM logs WHERE id = ?", (log_id,)).fetchone()[0] == "Black Walnut"
    conn.close()


def test_analyze_with_no_backend_fails_gracefully(client: Any, monkeypatch: Any) -> None:
    log_id = _make_log(client, with_photo=True)

    def boom(jpeg: bytes, hints: list) -> dict:
        raise client.module.vision.VisionUnavailable("no backend answered")

    monkeypatch.setattr(client.module.vision, "analyze_log_photo", boom)
    response = client.post(f"/log/{log_id}/analyze", follow_redirects=True)
    assert response.status_code == 200
    assert b"no backend answered" in response.data


def test_analyze_without_a_photo_says_so(client: Any) -> None:
    log_id = _make_log(client)
    response = client.post(f"/log/{log_id}/analyze", follow_redirects=True)
    assert b"No photo to analyze" in response.data


# --------------------------------------------------------------------------
# Thickness recommendation on the log page
# --------------------------------------------------------------------------


def test_log_detail_defaults_to_the_recommended_mixed_cut(client: Any) -> None:
    """assume_kiln defaults on, so a 16-inch pecan opens on the 8/4-center mix."""
    log_id = _make_log(client)  # Pecan, 16" x 10'
    body = client.get(f"/log/{log_id}").get_data(as_text=True)
    assert "Recommended: mix 2&#34;/1.5&#34;" in body or 'Recommended: mix 2"/1.5"' in body
    assert "Mixed cut" in body
    assert "premium" in body  # the why-line reached the page


def test_log_detail_recommendation_respects_the_kiln_setting(client: Any) -> None:
    log_id = _make_log(client)
    client.post("/settings", data={"assume_kiln": "0"})
    body = client.get(f"/log/{log_id}").get_data(as_text=True)
    assert "honeycomb" in body  # pecan's air-drying why-line
    assert "air drying only" in body


def test_log_detail_comparison_table_lists_all_options(client: Any) -> None:
    log_id = _make_log(client)
    body = client.get(f"/log/{log_id}").get_data(as_text=True)
    for label in ("4/4", "6/4", "8/4"):
        assert label in body


def test_log_detail_explicit_thickness_still_works(client: Any) -> None:
    log_id = _make_log(client)
    body = client.get(f"/log/{log_id}?thickness=1.25").get_data(as_text=True)
    assert "1.25" in body  # the odd size renders even though it is not in the table


# --------------------------------------------------------------------------
# Listing workflow
# --------------------------------------------------------------------------


def test_species_defaults_reflect_the_market_survey(client: Any) -> None:
    rates = client.module.species_rates(_conn(client))
    assert rates["Eastern Redcedar"] == 6.0
    assert rates["Black Walnut"] == 15.0
    assert rates["Mesquite"] == 22.0
    assert rates["Pecan"] == 12.0  # unchanged: matched Celina at $14


def _piece_dict(**overrides: Any) -> dict[str, Any]:
    base = {
        "species": "Pecan", "length_in": 87.0, "width_in": 9.0, "width_max_in": 10.0,
        "thickness_in": 1.5, "board_feet": 8.16, "dry_method": "kiln", "dry_pct": 100,
        "already_dry": 0, "moisture_pct": 7.0, "target_mc_pct": 8.0, "notes": None,
        "price_asking": 120.0, "price_per_bf": 14.71, "status": "ready",
        "suggested_price": 98.0, "dry_label": "Estimated dry",
    }
    base.update(overrides)
    return base


def test_listing_pack_is_keyword_first_and_tags_figure(client: Any) -> None:
    text = client.module.generate_listing(_piece_dict(
        notes="Ambrosia bug holes + mineral streak. Tim-bor while green.", sterilized=True))
    title = text.splitlines()[0]
    assert title.startswith("Kiln Dried Live Edge Pecan Ambrosia Slab 87")
    assert "Kiln dried to 7%" in text
    assert "Heat sterilized at 133F" in text and "borate treated" in text
    assert "Ambrosia figure" in text and "Mineral streaking" in text
    assert "7' 3\"" in text  # feet for long stock
    assert "$120 (about $14.71/bd ft)" in text
    assert "bug holes" not in title.lower()


def test_listing_pack_never_claims_sterilized_without_data(client: Any) -> None:
    text = client.module.generate_listing(_piece_dict(notes="Tim-bor treated"))
    assert "Heat sterilized" not in text
    assert "Borate treated" in text


def test_readiness_blocks_green_wood_no_photo_no_price(client: Any) -> None:
    r = client.module.listing_readiness(
        _piece_dict(dry_pct=40, dry_label="~5 mo left", price_asking=None), photo_count=0)
    assert r["ok"] is False
    assert any("Still drying" in b for b in r["blockers"])
    assert any("No photo" in b for b in r["blockers"])
    assert any("No asking price" in b and "$98" in b for b in r["blockers"])


def test_readiness_ok_with_warnings(client: Any) -> None:
    r = client.module.listing_readiness(_piece_dict(moisture_pct=None), photo_count=1)
    assert r["ok"] is True
    assert any("meter reading" in w for w in r["warnings"])
    assert any("Only one photo" in w for w in r["warnings"])


def _dry_piece(client: Any) -> int:
    client.post("/piece/new", data={
        "species": "Pecan", "length_in": "87", "width_in": "9", "thickness_in": "1.5",
        "mill_date": "2020-01-01", "already_dry": "on", "status": "ready",
        "price_asking": "120", "dry_method": "air",
    })
    conn = _conn(client)
    pid = conn.execute("SELECT id FROM pieces ORDER BY id DESC").fetchone()["id"]
    conn.execute("INSERT INTO photos (piece_id, filename, created_at) VALUES (?, 'x.jpg', 'x')", (pid,))
    conn.commit(); conn.close()
    return int(pid)


def test_mark_listed_records_platform_link_and_clock(client: Any) -> None:
    pid = _dry_piece(client)
    client.post(f"/piece/{pid}/listed", data={
        "listed_on": "facebook", "listing_url": "https://www.facebook.com/marketplace/item/123/"})
    conn = _conn(client)
    row = conn.execute("SELECT * FROM pieces WHERE id = ?", (pid,)).fetchone()
    conn.close()
    assert row["status"] == "listed" and row["listed_on"] == "facebook"
    assert row["listing_url"].endswith("/123/")
    assert row["listed_at"] and row["bumped_at"]


def test_bump_resets_the_stale_clock_and_stale_is_flagged(client: Any) -> None:
    pid = _dry_piece(client)
    client.post(f"/piece/{pid}/listed", data={"listed_on": "facebook"})
    conn = _conn(client)
    conn.execute("UPDATE pieces SET bumped_at = '2026-01-01T00:00:00' WHERE id = ?", (pid,))
    conn.commit(); conn.close()
    assert b"bump it" in client.get("/listings").data
    client.post(f"/piece/{pid}/bump")
    assert b"bump it" not in client.get("/listings").data


def test_mark_sold_from_the_listings_page_feeds_the_sold_report(client: Any) -> None:
    pid = _dry_piece(client)
    client.post(f"/piece/{pid}/listed", data={"listed_on": "facebook"})
    client.post(f"/piece/{pid}/sold", data={"price_sold": "110", "buyer": "Darrell"})
    conn = _conn(client)
    row = conn.execute("SELECT * FROM pieces WHERE id = ?", (pid,)).fetchone()
    conn.close()
    assert row["status"] == "sold" and row["price_sold"] == 110 and row["buyer"] == "Darrell"
    assert row["sold_date"]
    assert b"Darrell" in client.get("/stats").data


def test_unlist_returns_to_ready_and_clears_listing_fields(client: Any) -> None:
    pid = _dry_piece(client)
    client.post(f"/piece/{pid}/listed", data={"listed_on": "website", "listing_url": "https://x"})
    client.post(f"/piece/{pid}/unlist")
    conn = _conn(client)
    row = conn.execute("SELECT * FROM pieces WHERE id = ?", (pid,)).fetchone()
    conn.close()
    assert row["status"] == "ready" and row["listing_url"] is None and row["listed_on"] is None


def test_listings_page_sorts_green_wood_into_drying_not_ready(client: Any) -> None:
    client.post("/piece/new", data={
        "species": "Pecan", "length_in": "87", "width_in": "9", "thickness_in": "1.5",
        "mill_date": "2026-08-20", "status": "drying", "price_asking": "120", "dry_method": "air",
    })
    body = client.get("/listings").get_data(as_text=True)
    assert "Still drying" in body
    assert "mark listed" not in body  # no form offered for green wood


def test_piece_page_shows_blockers_instead_of_the_listed_form(client: Any) -> None:
    client.post("/piece/new", data={
        "species": "Pecan", "length_in": "87", "width_in": "9", "thickness_in": "1.5",
        "mill_date": "2026-08-20", "status": "drying", "dry_method": "air",
    })
    conn = _conn(client)
    pid = conn.execute("SELECT id FROM pieces ORDER BY id DESC").fetchone()["id"]
    conn.close()
    body = client.get(f"/piece/{pid}").get_data(as_text=True)
    assert "Green wood moves after it is sold" in body
    assert "Mark listed" not in body
