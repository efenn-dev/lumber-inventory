"""Tests for the kiln physics.

The EMC cases are checked against the USDA Forest Products Laboratory EMC table
in the Wood Handbook, which is the reference every published kiln schedule uses.
If these drift, the alerts and predictions built on top of them are wrong.

Run:
    python -m pytest test_kiln.py -q
"""

from __future__ import annotations

import pytest

import kiln


# --------------------------------------------------------------------------
# EMC -- anchored to published FPL values
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "temp_f, rh_pct, expected",
    [
        (70, 65, 12.0),   # classic indoor equilibrium
        (70, 30, 6.2),    # dry indoor winter air
        (70, 80, 16.5),   # humid summer day
        (80, 50, 9.0),    # shed in spring
        (120, 40, 6.8),   # a solar kiln working properly
        (140, 30, 5.0),   # a solar kiln on a hot Texas afternoon
    ],
)
def test_emc_matches_published_table(temp_f: float, rh_pct: float, expected: float) -> None:
    """EMC should land within half a point of the FPL table."""
    assert kiln.emc_from_conditions(temp_f, rh_pct) == pytest.approx(expected, abs=0.5)


def test_emc_rises_with_humidity() -> None:
    """Monotonic in humidity at fixed temperature."""
    values = [kiln.emc_from_conditions(100, rh) for rh in (20, 40, 60, 80)]
    assert values == sorted(values)


def test_emc_falls_with_temperature() -> None:
    """Monotonic in temperature at fixed humidity -- this is why kilns use heat."""
    values = [kiln.emc_from_conditions(t, 50) for t in (70, 100, 130, 160)]
    assert values == sorted(values, reverse=True)


def test_emc_handles_absurd_input() -> None:
    """A miswired sensor should not produce a NaN or an exception."""
    assert 0.0 <= kiln.emc_from_conditions(-500, 0) <= kiln.FIBER_SATURATION_PCT
    assert 0.0 <= kiln.emc_from_conditions(9999, 150) <= kiln.FIBER_SATURATION_PCT


# --------------------------------------------------------------------------
# Drying prediction
# --------------------------------------------------------------------------


def test_air_drying_reproduces_one_year_per_inch() -> None:
    """The trade rule of thumb should fall out of the model, not fight it."""
    days = kiln.days_to_target(
        current_mc_pct=70, target_mc_pct=15, emc_pct=12, thickness_in=1.0, method="air"
    )
    assert days == pytest.approx(365, rel=0.15)


def test_thicker_stock_takes_proportionally_longer() -> None:
    thin = kiln.days_to_target(70, 15, 12, thickness_in=1.0, method="air")
    thick = kiln.days_to_target(70, 15, 12, thickness_in=2.0, method="air")
    assert thick == pytest.approx(thin * 2, rel=0.01)


def test_solar_kiln_finishes_in_about_six_weeks() -> None:
    """Air-dried 4/4 to furniture-dry, under good kiln conditions."""
    days = kiln.days_to_target(20, 8, emc_pct=6.5, thickness_in=1.0, method="kiln")
    assert 30 <= days <= 60


def test_target_at_or_below_emc_is_unreachable() -> None:
    """The single most useful thing this module knows: it reports None."""
    assert kiln.days_to_target(20, 8, emc_pct=9.0, thickness_in=1.0, method="kiln") is None
    assert kiln.days_to_target(20, 8, emc_pct=8.0, thickness_in=1.0, method="kiln") is None


def test_already_at_target_is_zero_days() -> None:
    assert kiln.days_to_target(7, 8, emc_pct=6.0, thickness_in=1.0, method="kiln") == 0.0


def test_project_mc_decays_toward_emc_and_never_past_it() -> None:
    early = kiln.project_mc(70, 12, days_elapsed=30, thickness_in=1.0, method="air")
    late = kiln.project_mc(70, 12, days_elapsed=300, thickness_in=1.0, method="air")
    assert 12 < late < early < 70
    assert kiln.project_mc(70, 12, 100_000, 1.0, "air") == pytest.approx(12, abs=0.1)


# --------------------------------------------------------------------------
# Summaries
# --------------------------------------------------------------------------


def _reading(at: str, temp: float, rh: float) -> dict[str, object]:
    return {"recorded_at": at, "temp_f": temp, "rh_pct": rh}


def test_summarize_reports_median_temperature() -> None:
    """One sensor spike in direct sun should not move the headline temperature."""
    readings = [
        _reading("2026-08-26T10:00:00", 110, 40),
        _reading("2026-08-26T11:00:00", 112, 41),
        _reading("2026-08-26T12:00:00", 400, 42),  # sensor in the sun
    ]
    summary = kiln.summarize(readings)
    assert summary.temp_f == 112
    assert summary.temp_max == 400  # still reported, so the alert can fire


def test_emc_is_averaged_per_reading_not_from_averaged_conditions() -> None:
    """The sorption curve is nonlinear, so the order of operations matters."""
    readings = [
        _reading("2026-08-26T03:00:00", 72, 78),   # humid night
        _reading("2026-08-26T15:00:00", 140, 32),  # hot dry afternoon
    ]
    summary = kiln.summarize(readings)
    naive = kiln.emc_from_conditions(106, 55)  # EMC of the averaged conditions
    honest = (kiln.emc_from_conditions(72, 78) + kiln.emc_from_conditions(140, 32)) / 2
    assert summary.emc_pct == pytest.approx(honest, abs=0.05)
    assert summary.emc_pct != pytest.approx(naive, abs=0.05)


def test_solar_cycle_does_not_false_alarm_on_the_night_half() -> None:
    """A full day sampled uniformly is mostly dark. Wood integrates; the alert must too.

    This is the regression that a real 48 h simulation caught: a median over the
    cycle sat at the humid night value and fired a danger alert every evening on
    a kiln that was working fine during the day.
    """
    readings = []
    for hour in range(24):
        day = 6 <= hour <= 18
        readings.append(_reading(f"2026-08-26T{hour:02d}:00:00", 135 if day else 74, 33 if day else 76))
    summary = kiln.summarize(readings)
    assert summary.emc_min is not None and summary.emc_min < summary.emc_pct < summary.emc_max
    # A kiln this good should not be reported as unable to reach 12%.
    codes = {a.code for a in kiln.evaluate_alerts(summary, target_mc_pct=12.0, hours_since_last=0.5)}
    assert "emc_above_target" not in codes


def test_summarize_empty_is_safe() -> None:
    summary = kiln.summarize([])
    assert summary.count == 0
    assert summary.emc_pct is None


def test_sterilization_detects_a_sustained_run() -> None:
    readings = [
        _reading("2026-08-26T12:00:00", 130, 30),
        _reading("2026-08-26T12:30:00", 135, 28),
        _reading("2026-08-26T13:00:00", 138, 27),
        _reading("2026-08-26T13:30:00", 136, 27),
    ]
    result = kiln.sterilization_window(readings)
    assert result["sterilized"] is True
    assert result["longest_minutes"] >= 30


def test_sterilization_resets_when_temperature_dips() -> None:
    """A dip below threshold breaks the run -- larvae do not care about averages."""
    readings = [
        _reading("2026-08-26T12:00:00", 135, 30),
        _reading("2026-08-26T12:20:00", 120, 30),  # dipped
        _reading("2026-08-26T12:40:00", 135, 30),
        _reading("2026-08-26T13:00:00", 136, 30),
    ]
    result = kiln.sterilization_window(readings)
    assert result["sterilized"] is False
    assert result["longest_minutes"] == 20


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------


def _codes(alerts: list[kiln.Alert]) -> set[str]:
    return {a.code for a in alerts}


def test_good_conditions_produce_no_alerts() -> None:
    readings = [_reading("2026-08-26T12:00:00", 120, 40)]
    alerts = kiln.evaluate_alerts(kiln.summarize(readings), target_mc_pct=8.0, hours_since_last=0.5)
    assert alerts == []


def test_humid_kiln_warns_that_target_is_unreachable() -> None:
    readings = [_reading("2026-08-26T12:00:00", 100, 85)]
    alerts = kiln.evaluate_alerts(kiln.summarize(readings), target_mc_pct=8.0, hours_since_last=0.5)
    assert "emc_above_target" in _codes(alerts)
    assert any(a.level == "danger" for a in alerts)


def test_dead_sensor_is_a_danger() -> None:
    readings = [_reading("2026-08-26T12:00:00", 120, 40)]
    alerts = kiln.evaluate_alerts(kiln.summarize(readings), target_mc_pct=8.0, hours_since_last=24)
    assert "sensor_stale" in _codes(alerts)


def test_overheating_is_flagged() -> None:
    readings = [_reading("2026-08-26T12:00:00", 175, 20)]
    alerts = kiln.evaluate_alerts(kiln.summarize(readings), target_mc_pct=8.0, hours_since_last=0.5)
    assert "overheating" in _codes(alerts)


def test_green_wood_in_a_hot_dry_kiln_warns_about_checking() -> None:
    readings = [_reading("2026-08-26T12:00:00", 130, 25)]
    alerts = kiln.evaluate_alerts(
        kiln.summarize(readings), target_mc_pct=8.0, hours_since_last=0.5, wettest_piece_mc=60
    )
    assert "drying_too_fast" in _codes(alerts)


def test_no_readings_says_so_and_stops() -> None:
    alerts = kiln.evaluate_alerts(kiln.summarize([]), target_mc_pct=8.0)
    assert _codes(alerts) == {"no_readings"}


# --------------------------------------------------------------------------
# Kiln entry gate
# --------------------------------------------------------------------------


def test_green_stock_is_refused_entry() -> None:
    ok, message = kiln.kiln_entry_check(45.0)
    assert ok is False
    assert "case-harden" in message


def test_air_dried_stock_is_allowed_in() -> None:
    ok, _ = kiln.kiln_entry_check(18.0)
    assert ok is True


def test_unmeasured_stock_is_allowed_but_nagged() -> None:
    ok, message = kiln.kiln_entry_check(None)
    assert ok is True
    assert "Measure" in message


# --------------------------------------------------------------------------
# Timestamp parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-08-27T14:05:00", "2026-08-27T14:05:00"),
        ("2026-08-27T14:05:00Z", "2026-08-27T14:05:00"),
        ("2026-08-27 14:05:00", "2026-08-27T14:05:00"),
        ("2026-08-27 14:05", "2026-08-27T14:05:00"),
        ("08/27/2026 14:05:00", "2026-08-27T14:05:00"),
    ],
)
def test_parse_timestamp_accepts_what_loggers_emit(raw: str, expected: str) -> None:
    assert kiln.parse_timestamp(raw) == expected


def test_parse_timestamp_handles_epoch_seconds_and_millis() -> None:
    """A microcontroller RTC usually hands you epoch, in one unit or the other."""
    seconds = kiln.parse_timestamp("1787000000")
    millis = kiln.parse_timestamp("1787000000000")
    assert seconds is not None and seconds == millis


def test_parse_timestamp_rejects_junk() -> None:
    for bad in ("", "   ", "yesterday", "not-a-date", None, "42"):
        assert kiln.parse_timestamp(bad) is None


# --------------------------------------------------------------------------
# CSV import
# --------------------------------------------------------------------------


def test_csv_import_reads_a_plain_log() -> None:
    text = (
        "timestamp,temp_f,rh_pct\n"
        "2026-08-27T10:00:00,112.5,44\n"
        "2026-08-27T10:15:00,118.0,41\n"
    )
    readings, problems = kiln.parse_readings_csv(text)
    assert problems == []
    assert [r["temp_f"] for r in readings] == [112.5, 118.0]
    assert readings[0]["recorded_at"] == "2026-08-27T10:00:00"


def test_csv_import_converts_celsius() -> None:
    """Most cheap sensors report C. Importing 45 as 45 F would poison everything."""
    readings, problems = kiln.parse_readings_csv("time,temp_c,humidity\n2026-08-27T10:00:00,45,40\n")
    assert problems == []
    assert readings[0]["temp_f"] == pytest.approx(113.0)


def test_csv_import_accepts_alternate_column_names() -> None:
    readings, _ = kiln.parse_readings_csv("ts,temperature,rh\n1787000000,110,45\n")
    assert len(readings) == 1
    assert readings[0]["rh_pct"] == 45


def test_csv_import_skips_bad_rows_without_losing_the_file() -> None:
    """A card pulled from a hot box often has one truncated final line."""
    text = (
        "timestamp,temp_f,rh_pct\n"
        "2026-08-27T10:00:00,112,44\n"
        "2026-08-27T10:15:00,,41\n"        # missing temperature
        "not-a-date,115,40\n"              # unreadable timestamp
        "2026-08-27T10:30:00,118,4\n"
    )
    readings, problems = kiln.parse_readings_csv(text)
    assert len(readings) == 2
    assert len(problems) == 2


def test_csv_import_reports_a_missing_column_rather_than_guessing() -> None:
    readings, problems = kiln.parse_readings_csv("timestamp,temp_f\n2026-08-27T10:00:00,112\n")
    assert readings == []
    assert "humidity" in problems[0].lower()


def test_csv_import_handles_empty_and_headerless_input() -> None:
    assert kiln.parse_readings_csv("")[0] == []
    assert kiln.parse_readings_csv("timestamp,temp_f,rh_pct\n")[1] != []


def test_csv_import_rejects_nan_from_a_flaky_sensor() -> None:
    readings, problems = kiln.parse_readings_csv("timestamp,temp_f,rh_pct\n2026-08-27T10:00:00,nan,44\n")
    assert readings == []
    assert problems


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------


def test_calibration_recovers_a_known_time_constant() -> None:
    """Generate a curve from a known tau, then check the fit finds it again."""
    import math

    emc, tau_per_inch, thickness, start = 7.0, 22.0, 1.5, 24.0
    tau = tau_per_inch * thickness
    observations = [
        (day, emc + (start - emc) * math.exp(-day / tau)) for day in (0, 7, 14, 21, 28)
    ]
    fit = kiln.calibrate_tau(observations, thickness, emc)
    assert fit is not None
    assert fit["tau_days_per_inch"] == pytest.approx(tau_per_inch, rel=0.02)
    assert fit["r_squared"] > 0.99


def test_calibration_flags_a_poor_fit_rather_than_hiding_it() -> None:
    """Conditions changing mid-charge should show up as a low r-squared."""
    noisy = [(0, 24.0), (7, 12.0), (14, 19.0), (21, 9.0), (28, 15.0)]
    fit = kiln.calibrate_tau(noisy, 1.5, 7.0)
    assert fit is not None
    assert fit["r_squared"] < 0.8


def test_calibration_needs_two_usable_points() -> None:
    assert kiln.calibrate_tau([(0, 20.0)], 1.0, 7.0) is None
    assert kiln.calibrate_tau([], 1.0, 7.0) is None


def test_calibration_ignores_points_at_or_below_emc() -> None:
    """Below equilibrium the log is undefined; those points carry no signal."""
    assert kiln.calibrate_tau([(0, 6.0), (10, 5.0)], 1.0, 7.0) is None


def test_calibration_refuses_a_wetting_curve() -> None:
    """Wood picking up moisture is not a drying constant."""
    assert kiln.calibrate_tau([(0, 10.0), (10, 20.0)], 1.0, 7.0) is None


def test_calibration_refuses_points_from_one_moment() -> None:
    assert kiln.calibrate_tau([(5.0, 20.0), (5.1, 15.0)], 1.0, 7.0) is None


# --------------------------------------------------------------------------
# Tunable settings
# --------------------------------------------------------------------------


def test_tau_override_changes_the_prediction() -> None:
    slow = kiln.days_to_target(20, 8, 6.5, 1.0, "kiln", tau_days_per_inch=40.0)
    fast = kiln.days_to_target(20, 8, 6.5, 1.0, "kiln", tau_days_per_inch=10.0)
    assert slow is not None and fast is not None and slow > fast * 3


def test_threshold_overrides_are_merged_not_replaced() -> None:
    """A partial override must not zero out the thresholds it does not mention."""
    readings = [_reading("2026-08-26T12:00:00", 150, 30)]
    summary = kiln.summarize(readings)
    assert "overheating" not in _codes(kiln.evaluate_alerts(summary, target_mc_pct=8.0, hours_since_last=0.5))
    tightened = kiln.evaluate_alerts(
        summary, target_mc_pct=8.0, hours_since_last=0.5, thresholds={"max_safe_temp_f": 140.0}
    )
    assert "overheating" in _codes(tightened)
    # stale_reading_hours was not overridden, so it still has its default
    assert "sensor_stale" not in _codes(tightened)
