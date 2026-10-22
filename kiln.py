"""Kiln physics: equilibrium moisture content, drying prediction, alerts, and
parsing of logged readings.

Deliberately free of Flask and SQLite so the math can be tested on its own.
Everything here is a pure function over plain numbers, dicts, and strings.

The core idea: a temperature/humidity sensor does not directly tell you how wet
the wood is, but it does tell you the moisture content the wood is *heading
toward*. That number is the equilibrium moisture content (EMC), and it is what
turns a pair of sensor readings into a prediction and an alarm.

If the EMC implied by kiln conditions is above your target, the wood will never
reach target no matter how long you run the kiln. That is the single most useful
thing this module knows.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

#: Fiber saturation point. Above this, water is free in the cell cavities and
#: the wood does not shrink; below it, the wood shrinks and can check.
FIBER_SATURATION_PCT = 30.0

#: Nominal green moisture content when nothing has been measured. Pecan comes
#: off the mill wetter than this, but it is a safe planning default.
DEFAULT_GREEN_MC_PCT = 70.0

#: Typical finished targets.
TARGET_MC_AIR = 15.0     # air-dried equilibrium outdoors in North Texas
TARGET_MC_KILN = 8.0     # furniture / epoxy-ready

#: Drying time constants, in days per inch of thickness, for the exponential
#: approach to equilibrium below. Calibrated against the two rules of thumb the
#: trade actually uses:
#:
#:   air  - "one year per inch" takes 70% MC to ~15% in 365 days at EMC 12
#:   kiln - a solar kiln takes air-dried 20% stock to ~8% in about six weeks
#:
#: Both are linear in thickness. Real diffusion is closer to quadratic, but the
#: published rules of thumb are linear and this stays consistent with them
#: rather than inventing precision the inputs do not support.
TAU_DAYS_PER_INCH = {"air": 120.0, "kiln": 18.0}

#: Sterilization: holding this core temperature for this long kills powderpost
#: beetle larvae and their eggs. The standard heat-treatment spec.
STERILIZE_TEMP_F = 133.0
STERILIZE_MINUTES = 30

#: A solar kiln is a finisher, not a green-wood dryer. Loading green stock
#: straight in case-hardens it.
KILN_ENTRY_MAX_MC_PCT = 25.0

#: Alert thresholds.
MAX_SAFE_TEMP_F = 160.0
HIGH_RH_PCT = 85.0
STALE_READING_HOURS = 6.0


# --------------------------------------------------------------------------
# Equilibrium moisture content
# --------------------------------------------------------------------------


def emc_from_conditions(temp_f: float, rh_pct: float) -> float:
    """Equilibrium moisture content (%) for air at ``temp_f`` and ``rh_pct``.

    Implements the Hailwood-Horrobin sorption model as published in the USDA
    Forest Products Laboratory *Wood Handbook*, which is the standard reference
    every kiln schedule is built on.

    Sanity anchors: 70 F / 65% RH gives about 12.0%, the classic indoor
    equilibrium. 120 F / 40% RH gives about 6.8%, a good solar-kiln condition.

    Args:
        temp_f: Dry-bulb temperature in degrees Fahrenheit.
        rh_pct: Relative humidity as a percentage, 0-100.

    Returns:
        Equilibrium moisture content as a percentage of oven-dry weight.
    """
    h = max(0.0, min(100.0, float(rh_pct))) / 100.0
    t = float(temp_f)

    # Clamp to the range the polynomials were fitted over. Outside it they
    # diverge, and a sensor reporting -40 F should not produce a confident number.
    t = max(-20.0, min(250.0, t))

    w = 330.0 + 0.452 * t + 0.00415 * t**2
    k = 0.791 + 4.63e-4 * t - 8.44e-7 * t**2
    k1 = 6.34 + 7.75e-4 * t - 9.35e-5 * t**2
    k2 = 1.09 + 2.84e-2 * t - 9.04e-5 * t**2

    kh = k * h
    if kh >= 1.0:  # saturated; the first term blows up
        return FIBER_SATURATION_PCT

    first = kh / (1.0 - kh)
    numerator = k1 * kh + 2.0 * k1 * k2 * kh * kh
    denominator = 1.0 + k1 * kh + k1 * k2 * kh * kh
    second = numerator / denominator if denominator else 0.0

    emc = (1800.0 / w) * (first + second)
    return round(max(0.0, min(FIBER_SATURATION_PCT, emc)), 2)


# --------------------------------------------------------------------------
# Drying prediction
# --------------------------------------------------------------------------


def days_to_target(
    current_mc_pct: float,
    target_mc_pct: float,
    emc_pct: float,
    thickness_in: float,
    method: str = "air",
    tau_days_per_inch: float | None = None,
) -> float | None:
    """Days for wood to fall from ``current_mc_pct`` to ``target_mc_pct``.

    Models drying as first-order exponential approach to equilibrium::

        MC(t) = EMC + (MC0 - EMC) * exp(-t / tau)

    solved for the time at which ``MC(t) == target``. ``tau`` scales with
    thickness (see :data:`TAU_DAYS_PER_INCH`).

    Returns None when the target is unreachable under these conditions -- that
    is, when the target is at or below the equilibrium the air can deliver. That
    is not an error case; it is the most actionable answer this module produces,
    and callers should surface it rather than swallow it.
    """
    tau_per_inch = tau_days_per_inch or TAU_DAYS_PER_INCH.get(method, TAU_DAYS_PER_INCH["air"])
    tau = tau_per_inch * max(0.25, float(thickness_in))

    if current_mc_pct <= target_mc_pct:
        return 0.0

    # Asymptotic approach: you never *reach* EMC, so a target at or below it is
    # unreachable. Require a little headroom, otherwise the log explodes and
    # reports a plausible-looking but meaningless number of days.
    headroom = target_mc_pct - emc_pct
    if headroom < 0.5:
        return None

    ratio = (current_mc_pct - emc_pct) / headroom
    if ratio <= 1.0:
        return 0.0

    return round(tau * math.log(ratio), 1)


def project_mc(
    start_mc_pct: float,
    emc_pct: float,
    days_elapsed: float,
    thickness_in: float,
    method: str = "air",
    tau_days_per_inch: float | None = None,
) -> float:
    """Estimated moisture content after ``days_elapsed`` under given conditions."""
    tau_per_inch = tau_days_per_inch or TAU_DAYS_PER_INCH.get(method, TAU_DAYS_PER_INCH["air"])
    tau = tau_per_inch * max(0.25, float(thickness_in))
    decayed = (start_mc_pct - emc_pct) * math.exp(-max(0.0, days_elapsed) / tau)
    return round(emc_pct + decayed, 1)


def default_target_mc(method: str) -> float:
    """The moisture target a given drying method is actually trying to hit."""
    return TARGET_MC_KILN if method == "kiln" else TARGET_MC_AIR


def calibrate_tau(
    observations: Sequence[tuple[float, float]],
    thickness_in: float,
    emc_pct: float,
) -> dict[str, Any] | None:
    """Solve for the drying time constant your kiln actually has.

    The shipped :data:`TAU_DAYS_PER_INCH` numbers are back-calculated from trade
    rules of thumb, not from *this* kiln with *this* stacking and *this* fan. Two
    or more measured moisture readings during one charge are enough to replace
    the guess with the real number.

    Taking logs of the drying model::

        MC(t) = EMC + (MC0 - EMC) * exp(-t / tau)
        ln(MC(t) - EMC) = ln(MC0 - EMC) - t / tau

    turns it into a straight line whose slope is ``-1 / tau``, so this is an
    ordinary least-squares fit on the log residuals.

    Args:
        observations: ``(days_elapsed, measured_mc_pct)`` pairs, at least two,
            all above ``emc_pct``.
        thickness_in: Thickness of the stock the readings came from.
        emc_pct: The equilibrium the wood was drying toward over that period.

    Returns:
        A dict with the fitted ``tau_days_per_inch``, the ``r_squared`` of the
        fit, and the point count -- or None when there is not enough usable
        signal to fit. A low r-squared is worth surfacing rather than hiding:
        it usually means conditions changed mid-charge, and the single number
        is then a poor description of what happened.
    """
    usable = [
        (float(days), math.log(float(mc) - emc_pct))
        for days, mc in observations
        if float(mc) - emc_pct > 0.1
    ]
    if len(usable) < 2:
        return None

    xs = [d for d, _ in usable]
    ys = [y for _, y in usable]
    if max(xs) - min(xs) < 0.5:  # all measured at effectively one moment
        return None

    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in usable)
    variance = sum((x - mean_x) ** 2 for x in xs)
    if variance == 0:
        return None

    slope = covariance / variance
    if slope >= 0:  # wood got wetter; not a drying curve
        return None

    tau = -1.0 / slope
    predicted = [mean_y + slope * (x - mean_x) for x in xs]
    ss_residual = sum((y - p) ** 2 for y, p in zip(ys, predicted))
    ss_total = sum((y - mean_y) ** 2 for y in ys)
    r_squared = 1.0 - (ss_residual / ss_total) if ss_total > 0 else 1.0

    return {
        "tau_days_per_inch": round(tau / max(0.25, float(thickness_in)), 1),
        "r_squared": round(max(0.0, min(1.0, r_squared)), 3),
        "points": len(usable),
        "span_days": round(max(xs) - min(xs), 1),
    }


# --------------------------------------------------------------------------
# Reading summaries
# --------------------------------------------------------------------------


@dataclass
class ConditionSummary:
    """Aggregate of a window of sensor readings."""

    count: int
    temp_f: float | None
    rh_pct: float | None
    emc_pct: float | None
    emc_min: float | None
    emc_max: float | None
    temp_min: float | None
    temp_max: float | None
    rh_min: float | None
    rh_max: float | None
    latest_at: str | None

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def summarize(readings: Sequence[dict[str, Any]]) -> ConditionSummary:
    """Aggregate a window of readings into the numbers alerts and models use.

    Two deliberate choices, both learned from running a simulated solar-kiln
    cycle through this:

    **EMC is averaged per-reading, not computed from averaged conditions.** The
    sorption curve is strongly nonlinear, so EMC(mean temp, mean RH) is not the
    mean EMC. Averaging the conditions first understates the humid end.

    **The headline EMC is a mean, not a median.** A solar kiln swings hard --
    140 F at 32% RH in the afternoon, 72 F at 78% overnight. Wood does not track
    that: a 4/4 board has a time constant of weeks, so it integrates the cycle
    and settles toward the *average* equilibrium. Taking a median over a
    uniformly sampled day is dominated by the long cool night and fires a false
    alarm every evening.

    Temperature and humidity are still reported as medians, because those are
    for a human reading the dashboard and one sensor baking in direct sun should
    not move them. The min/max carry the swing.
    """
    temps = [r["temp_f"] for r in readings if r.get("temp_f") is not None]
    rhs = [r["rh_pct"] for r in readings if r.get("rh_pct") is not None]

    if not temps or not rhs:
        return ConditionSummary(
            len(readings), None, None, None, None, None, None, None, None, None, None
        )

    emcs = [
        emc_from_conditions(r["temp_f"], r["rh_pct"])
        for r in readings
        if r.get("temp_f") is not None and r.get("rh_pct") is not None
    ]
    latest = max((r.get("recorded_at") or "") for r in readings) or None

    return ConditionSummary(
        count=len(readings),
        temp_f=round(statistics.median(temps), 1),
        rh_pct=round(statistics.median(rhs), 1),
        emc_pct=round(statistics.fmean(emcs), 2) if emcs else None,
        emc_min=round(min(emcs), 2) if emcs else None,
        emc_max=round(max(emcs), 2) if emcs else None,
        temp_min=round(min(temps), 1),
        temp_max=round(max(temps), 1),
        rh_min=round(min(rhs), 1),
        rh_max=round(max(rhs), 1),
        latest_at=latest,
    )


def sterilization_window(
    readings: Sequence[dict[str, Any]],
    temp_f: float = STERILIZE_TEMP_F,
    minutes: int = STERILIZE_MINUTES,
) -> dict[str, Any]:
    """Whether readings show a sustained run at or above sterilizing temperature.

    Powderpost beetles are the reason this matters: pecan is high-starch and a
    favourite target, and a charge that never got hot enough can ship live larvae
    into a customer's shop.

    Readings must be ordered oldest first. Gaps are treated as breaks in the run,
    because a sensor that went quiet is not evidence of sustained heat.
    """
    best_minutes = 0.0
    run_start: str | None = None
    best_start: str | None = None
    prev_at: str | None = None

    for reading in readings:
        at = reading.get("recorded_at")
        hot = (reading.get("temp_f") or -999) >= temp_f
        if not hot or at is None:
            run_start = None
            prev_at = at
            continue

        if run_start is None:
            run_start = at
        prev_at = at

        elapsed = _minutes_between(run_start, prev_at)
        if elapsed > best_minutes:
            best_minutes = elapsed
            best_start = run_start

    return {
        "sterilized": best_minutes >= minutes,
        "longest_minutes": round(best_minutes),
        "required_minutes": minutes,
        "threshold_f": temp_f,
        "started_at": best_start,
    }


def _minutes_between(a: str | None, b: str | None) -> float:
    """Minutes between two ISO timestamps, 0 when either is missing or malformed."""
    from datetime import datetime

    if not a or not b:
        return 0.0
    try:
        return max(0.0, (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 60.0)
    except ValueError:
        return 0.0


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------


@dataclass
class Alert:
    """Something worth telling the operator about."""

    level: str  # "danger" | "warn" | "info"
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"level": self.level, "code": self.code, "message": self.message}


#: Alert thresholds that can be overridden from the settings page. Kilns differ
#: and so do climates; these are starting points, not laws.
DEFAULT_THRESHOLDS: dict[str, float] = {
    "max_safe_temp_f": MAX_SAFE_TEMP_F,
    "high_rh_pct": HIGH_RH_PCT,
    "stale_reading_hours": STALE_READING_HOURS,
    "cool_kiln_temp_f": 90.0,
    "aggressive_emc_pct": 8.0,
}


def evaluate_alerts(
    summary: ConditionSummary,
    *,
    target_mc_pct: float = TARGET_MC_KILN,
    hours_since_last: float | None = None,
    wettest_piece_mc: float | None = None,
    thresholds: dict[str, float] | None = None,
) -> list[Alert]:
    """Turn a condition summary into operator-facing warnings.

    Ordered most urgent first. An empty list means the kiln looks fine, which is
    a claim worth being able to make.

    ``thresholds`` overrides :data:`DEFAULT_THRESHOLDS` key by key, so a setting
    left blank keeps the shipped default rather than becoming zero.
    """
    limits = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    alerts: list[Alert] = []

    if summary.count == 0:
        alerts.append(Alert("warn", "no_readings", "No sensor readings yet. Add one manually or point a device at /api/kiln/reading."))
        return alerts

    if hours_since_last is not None and hours_since_last > limits["stale_reading_hours"]:
        alerts.append(
            Alert(
                "danger",
                "sensor_stale",
                f"No reading in {hours_since_last:.1f} h. The sensor or its power may be down.",
            )
        )

    if summary.temp_max is not None and summary.temp_max > limits["max_safe_temp_f"]:
        alerts.append(
            Alert(
                "danger",
                "overheating",
                f"Peaked at {summary.temp_max:.0f} F. Above {limits['max_safe_temp_f']:.0f} F risks "
                "surface checking and glue-line damage. Open a vent or shade the collector.",
            )
        )

    if summary.emc_pct is not None and summary.emc_pct >= target_mc_pct:
        detail = ""
        if summary.emc_min is not None and summary.emc_min < target_mc_pct:
            # The kiln does get there during the day and loses it again at night,
            # which points at heat retention rather than at the collector.
            detail = (
                f" It does reach {summary.emc_min:.1f}% at the best part of the day and gives it "
                "back overnight, so the problem is holding heat after dark, not gathering it: "
                "insulate harder and close the vents at sundown."
            )
        alerts.append(
            Alert(
                "danger",
                "emc_above_target",
                f"Averaged over the cycle, conditions equilibrate at {summary.emc_pct:.1f}% -- at or "
                f"above your {target_mc_pct:.0f}% target. Wood integrates the whole day, so it will "
                f"settle near {summary.emc_pct:.1f}% and stop.{detail}",
            )
        )

    if summary.rh_max is not None and summary.rh_max > limits["high_rh_pct"]:
        alerts.append(
            Alert(
                "warn",
                "high_humidity",
                f"Humidity peaked at {summary.rh_max:.0f}%. Moist air is not leaving the box; "
                "suspect a closed vent or a dead fan.",
            )
        )

    if (
        wettest_piece_mc is not None
        and wettest_piece_mc > FIBER_SATURATION_PCT
        and summary.emc_pct is not None
        and summary.emc_pct < limits["aggressive_emc_pct"]
    ):
        alerts.append(
            Alert(
                "warn",
                "drying_too_fast",
                f"Wettest piece is around {wettest_piece_mc:.0f}% MC while conditions pull "
                f"toward {summary.emc_pct:.1f}%. That gradient surface-checks pecan and oak. "
                "Air-dry below 25% before loading the kiln.",
            )
        )

    if summary.temp_f is not None and summary.temp_f < limits["cool_kiln_temp_f"] and summary.rh_pct is not None:
        alerts.append(
            Alert(
                "info",
                "cool_kiln",
                f"Running at {summary.temp_f:.0f} F. A solar kiln wants sun on the collector; "
                "expect slow progress until it warms.",
            )
        )

    return alerts


# --------------------------------------------------------------------------
# Parsing logged readings
# --------------------------------------------------------------------------

#: Column names accepted for each field. An offline logger is firmware someone
#: writes once at a workbench, so this reads whatever it plausibly emitted
#: rather than making them go back and reflash it to match a spec.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "recorded_at": ("recorded_at", "timestamp", "time", "datetime", "date", "ts", "when"),
    "temp_f": ("temp_f", "temperature_f", "tempf", "temp", "temperature", "t"),
    "temp_c": ("temp_c", "temperature_c", "tempc", "celsius"),
    "rh_pct": ("rh_pct", "humidity", "rh", "relative_humidity", "humidity_pct", "h"),
    "wood_mc_pct": ("wood_mc_pct", "wood_mc", "mc", "moisture", "moisture_pct"),
}

#: Refuse to chew through an unbounded file. At a 15 minute cadence this is
#: about two and a half years of logging, well past any card he will hand it.
MAX_IMPORT_ROWS = 100_000


def parse_timestamp(value: Any) -> str | None:
    """Normalise a logged timestamp to ISO 8601, or None if unreadable.

    Accepts ISO (with or without ``T`` and a ``Z`` suffix), ``YYYY-MM-DD
    HH:MM:SS``, and Unix epoch in seconds or milliseconds -- a microcontroller
    with an RTC will hand you one of those and it is not worth caring which.
    """
    from datetime import datetime

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    # Epoch, seconds or milliseconds.
    if text.lstrip("-").isdigit():
        number = int(text)
        if number > 10**11:  # milliseconds
            number //= 1000
        if 10**8 < number < 10**10:  # plausible seconds since 1973..2286
            try:
                return datetime.fromtimestamp(number).isoformat(timespec="seconds")
            except (OverflowError, OSError, ValueError):
                return None
        return None

    cleaned = text.replace("Z", "").replace("z", "").strip()
    for candidate in (cleaned, cleaned.replace(" ", "T")):
        try:
            return datetime.fromisoformat(candidate).isoformat(timespec="seconds")
        except ValueError:
            continue

    for fmt in ("%Y/%m/%d %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(cleaned, fmt).isoformat(timespec="seconds")
        except ValueError:
            continue
    return None


def parse_readings_csv(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse a CSV of logged readings into reading dicts.

    Returns ``(readings, problems)``. Bad rows are skipped and described rather
    than aborting the import: a card pulled out of a hot box will sometimes have
    one truncated line, and losing a month of data over it would be absurd.

    Understands the column aliases in :data:`COLUMN_ALIASES`, and converts a
    ``temp_c`` column to Fahrenheit -- most cheap sensors report Celsius, and
    silently importing 22 as 22 F would poison every number downstream.
    """
    import csv
    import io

    problems: list[str] = []
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return [], ["File is empty."]

    header = [cell.strip().lower().replace(" ", "_") for cell in rows[0]]
    index: dict[str, int] = {}
    for field, aliases in COLUMN_ALIASES.items():
        for position, name in enumerate(header):
            if name in aliases:
                index[field] = position
                break

    if "temp_f" not in index and "temp_c" not in index:
        return [], [f"No temperature column found. Header was: {', '.join(header) or '(blank)'}"]
    if "rh_pct" not in index:
        return [], [f"No humidity column found. Header was: {', '.join(header) or '(blank)'}"]

    readings: list[dict[str, Any]] = []
    for line_number, row in enumerate(rows[1:], start=2):
        if len(readings) >= MAX_IMPORT_ROWS:
            problems.append(f"Stopped at {MAX_IMPORT_ROWS} rows; the rest of the file was ignored.")
            break
        if not any(cell.strip() for cell in row):
            continue

        def cell(field: str) -> str | None:
            position = index.get(field)
            if position is None or position >= len(row):
                return None
            return row[position].strip() or None

        temp_f = _to_float(cell("temp_f"))
        if temp_f is None:
            celsius = _to_float(cell("temp_c"))
            temp_f = celsius * 9.0 / 5.0 + 32.0 if celsius is not None else None
        rh_pct = _to_float(cell("rh_pct"))

        if temp_f is None or rh_pct is None:
            problems.append(f"Line {line_number}: missing temperature or humidity.")
            continue

        stamp = parse_timestamp(cell("recorded_at"))
        if stamp is None:
            problems.append(f"Line {line_number}: unreadable timestamp {cell('recorded_at')!r}.")
            continue

        readings.append(
            {
                "recorded_at": stamp,
                "temp_f": round(temp_f, 2),
                "rh_pct": round(rh_pct, 2),
                "wood_mc_pct": _to_float(cell("wood_mc_pct")),
            }
        )

    if not readings and not problems:
        problems.append("No data rows found below the header.")
    return readings, problems


def _to_float(value: Any) -> float | None:
    """Float or None, tolerating blanks and the junk a flaky sensor emits."""
    if value is None:
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def kiln_entry_check(mc_pct: float | None) -> tuple[bool, str]:
    """Whether a piece is dry enough to load into the kiln.

    A solar kiln is a finisher. Green stock loaded straight in case-hardens:
    the shell dries and seals while the core stays wet, and it honeycombs later
    when it is machined.
    """
    if mc_pct is None:
        return True, "No moisture reading. Measure before loading -- aim for 20-25% or below."
    if mc_pct > KILN_ENTRY_MAX_MC_PCT:
        return False, (
            f"{mc_pct:.0f}% MC is too wet to load. A solar kiln is a finisher, not a green-wood "
            f"dryer. Air-dry to {KILN_ENTRY_MAX_MC_PCT:.0f}% or below first, or it will case-harden."
        )
    return True, f"{mc_pct:.0f}% MC -- ready for the kiln."
