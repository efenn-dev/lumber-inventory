"""Tests for log scaling and milling plans.

The scale anchors come from the published USFS Doyle and International 1/4"
tables -- if these drift, every value estimate in the field app is wrong.

Run:
    python -m pytest test_logscale.py -q
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import logscale


# --------------------------------------------------------------------------
# Scale anchors -- published table values
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "d, length, expected",
    [
        (16, 16, 144.0),  # the classic table entry
        (12, 12, 48.0),
        (20, 10, 160.0),
        (10, 8, 18.0),    # small log: Doyle's famous pessimism
        (4, 8, 0.0),      # at the slab allowance, nothing left
    ],
)
def test_doyle_matches_the_table(d: float, length: float, expected: float) -> None:
    assert logscale.doyle_bf(d, length) == expected


@pytest.mark.parametrize(
    "d, length, expected, tol",
    [
        (16, 16, 180, 4),  # USFS table: 180
        (12, 12, 70, 4),   # USFS table: 70
        (20, 16, 290, 8),  # USFS table: ~290
    ],
)
def test_international_matches_the_table(d: float, length: float, expected: float, tol: float) -> None:
    assert logscale.international_bf(d, length) == pytest.approx(expected, abs=tol)


def test_international_beats_doyle_on_small_logs() -> None:
    """The whole reason to show both numbers."""
    assert logscale.international_bf(12, 12) > logscale.doyle_bf(12, 12) * 1.3


def test_measured_taper_changes_international() -> None:
    """A butt log with real flare should scale higher than the standard assumption."""
    standard = logscale.international_bf(14, 12)
    flared = logscale.international_bf(14, 12, large_diameter_in=20)
    assert flared > standard


def test_scales_handle_zero_and_negative_input() -> None:
    assert logscale.doyle_bf(0, 10) == 0.0
    assert logscale.international_bf(-5, 10) == 0.0
    assert logscale.international_bf(10, 0) == 0.0


# --------------------------------------------------------------------------
# Cut plan
# --------------------------------------------------------------------------


def test_plan_has_exactly_one_pith_board_and_is_symmetric() -> None:
    plan = logscale.cut_plan(16, 10, thickness_in=1.0)
    pith_boards = [b for b in plan.boards if b.contains_pith]
    assert len(pith_boards) == 1
    # Everything else pairs off above/below the pith.
    assert (len(plan.boards) - 1) % 2 == 0


def test_no_board_is_wider_than_the_log() -> None:
    plan = logscale.cut_plan(14, 8)
    assert all(b.width_in <= 14 for b in plan.boards)


def test_widths_shrink_toward_the_edges() -> None:
    """Boards farther from the pith come off a narrower part of the circle."""
    plan = logscale.cut_plan(18, 10)
    pith = next(b for b in plan.boards if b.contains_pith)
    assert all(b.width_in <= pith.width_in for b in plan.boards)


def test_plan_yield_beats_doyle_on_a_small_log() -> None:
    """Encodes the economics: buy on Doyle, sell on actual measure."""
    plan = logscale.cut_plan(12, 10, thickness_in=1.0)
    assert plan.yield_bf > plan.doyle_bf


def test_thicker_stock_means_fewer_boards() -> None:
    four_quarter = logscale.cut_plan(16, 10, thickness_in=1.0)
    eight_quarter = logscale.cut_plan(16, 10, thickness_in=2.0)
    assert len(eight_quarter.boards) < len(four_quarter.boards)


def test_narrow_boards_are_dropped_as_edging_waste() -> None:
    plan = logscale.cut_plan(10, 8, min_width_in=3.0)
    assert all(b.width_in >= 3.0 for b in plan.boards)


def test_tiny_log_gets_the_firewood_advice() -> None:
    plan = logscale.cut_plan(6, 8)
    assert any("firewood" in a for a in plan.advice)


# --------------------------------------------------------------------------
# Thickness recommendation
# --------------------------------------------------------------------------


def test_pecan_with_kiln_recommends_eight_quarter_center() -> None:
    """The headline case: thick pecan is scarce, and the kiln makes it safe."""
    rec = logscale.recommend("Pecan", 16, kiln=True)
    assert rec["center"] == 2.0 and rec["jacket"] == 1.5 and rec["mixed"]
    assert "premium" in rec["why"]


def test_pecan_without_kiln_caps_at_six_quarter() -> None:
    """Same species, no kiln -- the honeycomb risk changes the answer."""
    rec = logscale.recommend("Pecan", 16, kiln=False)
    assert rec["center"] == 1.5
    assert "honeycomb" in rec["why"]


def test_red_oak_never_gets_eight_quarter_even_kilned() -> None:
    assert logscale.recommend("Red Oak", 20, kiln=True)["center"] == 1.5


def test_walnut_and_mesquite_are_thick_even_air_dried() -> None:
    assert logscale.recommend("Black Walnut", 16, kiln=False)["center"] == 2.0
    assert logscale.recommend("Mesquite", 14, kiln=False)["center"] == 2.0


def test_small_log_collapses_to_single_thickness() -> None:
    """Under 10 inches there is no slab-width center zone to be thick in."""
    rec = logscale.recommend("Pecan", 9, kiln=True)
    assert rec["center"] == rec["jacket"] and not rec["mixed"]


def test_unknown_species_gets_the_conservative_default() -> None:
    assert logscale.recommend("Mystery Wood", 16, kiln=True)["center"] == 1.5
    assert logscale.recommend(None, 16, kiln=False)["center"] == 1.0


# --------------------------------------------------------------------------
# Mixed cut plan
# --------------------------------------------------------------------------


def test_mixed_plan_is_thick_in_the_middle_and_thin_outside() -> None:
    plan = logscale.cut_plan(18, 10, thickness_in=1.0, center_thickness_in=2.0)
    thicknesses = [b.thickness_in for b in plan.boards]
    assert 2.0 in thicknesses and 1.0 in thicknesses
    # Every thick board must be wider than every thin board -- thickness
    # follows the slab-width zone, and width shrinks monotonically outward.
    thick_widths = [b.width_in for b in plan.boards if b.thickness_in == 2.0]
    thin_widths = [b.width_in for b in plan.boards if b.thickness_in == 1.0]
    assert min(thick_widths) >= max(thin_widths)


def test_mixed_plan_bands_do_not_overlap() -> None:
    """Varying thickness must not double-book any part of the log."""
    plan = logscale.cut_plan(18, 10, thickness_in=1.0, center_thickness_in=2.0)
    bands = sorted((b.offset_low, b.offset_high) for b in plan.boards)
    for (low1, high1), (low2, high2) in zip(bands, bands[1:]):
        assert high1 <= low2 + 1e-9


def test_mixed_plan_pith_board_is_center_thickness() -> None:
    plan = logscale.cut_plan(18, 10, thickness_in=1.0, center_thickness_in=2.0)
    pith = next(b for b in plan.boards if b.contains_pith)
    assert pith.thickness_in == 2.0


def test_thickness_barely_changes_yield() -> None:
    """Recovery is a wash across thickness: kerf savings at 8/4 are eaten by
    rim discretization (the last thick course not fitting). The case for thick
    cuts is value per foot, not board feet -- this pins that fact."""
    thin = logscale.cut_plan(16, 10, thickness_in=1.0)
    thick = logscale.cut_plan(16, 10, thickness_in=2.0)
    assert abs(thick.yield_bf - thin.yield_bf) / thin.yield_bf < 0.03


def test_mixed_plan_advice_names_the_split() -> None:
    plan = logscale.cut_plan(18, 10, thickness_in=1.0, center_thickness_in=2.0)
    assert any("Mixed cut" in a for a in plan.advice)


# --------------------------------------------------------------------------
# Thickness-aware value
# --------------------------------------------------------------------------


def test_thickness_multiplier_ladder() -> None:
    assert logscale.thickness_multiplier(1.0) == 1.0
    assert logscale.thickness_multiplier(1.5) == 1.15
    assert logscale.thickness_multiplier(2.0) == 1.35


def test_plan_value_pays_the_thickness_premium() -> None:
    """Same log, same rate: the 8/4 plan is worth more per bf AND in total."""
    thin = logscale.cut_plan(16, 10, thickness_in=1.0)
    thick = logscale.cut_plan(16, 10, thickness_in=2.0)
    v_thin = logscale.plan_value(thin, 12.0)["kiln_dried"]
    v_thick = logscale.plan_value(thick, 12.0)["kiln_dried"]
    assert v_thick > v_thin * 1.2


def test_compare_options_marks_the_pecan_mix_as_recommended() -> None:
    result = logscale.compare_options(16, 10, "Pecan", kiln=True, rate_per_bf=12.0)
    rec_option = result["options"][result["recommended"]]
    assert rec_option["center"] == 2.0 and rec_option["thickness"] == 1.5
    assert all(o["value_kiln"] is not None for o in result["options"])
    # Honesty contract: the recommendation must be within 5% of the best
    # modeled option and decisively above 4/4. It is deliberately NOT required
    # to be the maximum -- pure 8/4 models within noise of the mix, and the
    # mix wins on what the model cannot see (6/4 jackets finish at 1-1/4",
    # the best-selling shelf thickness; narrow 8/4 is slow-moving leg stock).
    best = max(o["value_kiln"] for o in result["options"])
    four_quarter = next(o for o in result["options"] if o["label"] == "4/4")
    assert rec_option["value_kiln"] >= best * 0.95
    assert rec_option["value_kiln"] > four_quarter["value_kiln"] * 1.2


def test_narrow_thick_boards_do_not_get_the_slab_premium() -> None:
    """The width gate on the premium: 7" of 8/4 is a table leg, not a slab."""
    assert logscale.board_multiplier(2.0, 15.0) == 1.35
    assert logscale.board_multiplier(2.0, 7.0) == pytest.approx(1.14)
    assert logscale.board_multiplier(1.0, 7.0) == 1.0


def test_compare_options_single_thickness_recommendation_for_small_logs() -> None:
    result = logscale.compare_options(9, 8, "Pecan", kiln=True, rate_per_bf=12.0)
    assert result["options"][result["recommended"]]["center"] is None


def test_degenerate_input_does_not_crash() -> None:
    plan = logscale.cut_plan(0, 0, thickness_in=0)
    assert plan.boards == [] and plan.yield_bf == 0.0


# --------------------------------------------------------------------------
# Value
# --------------------------------------------------------------------------


def test_value_estimate_orders_conditions_correctly() -> None:
    values = logscale.value_estimate(50, 12.0)
    assert values["green"] < values["air_dried"] < values["kiln_dried"]
    assert values["kiln_dried"] == 600.0


# --------------------------------------------------------------------------
# SVG
# --------------------------------------------------------------------------


def test_svg_renders_and_marks_the_pith_board() -> None:
    plan = logscale.cut_plan(16, 10)
    svg = logscale.plan_svg(plan, 16)
    assert svg.startswith("<svg") and "var(--danger)" in svg


def test_svg_is_empty_for_an_empty_plan() -> None:
    assert logscale.plan_svg(logscale.MillingPlan(), 16) == ""


# --------------------------------------------------------------------------
# JS port cross-check
# --------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_javascript_port_agrees_with_python() -> None:
    """The field page computes offline with a JS port of this module.

    If the two implementations drift, the phone quotes one number and the
    desktop another -- this pins them together on scales, plan yield, board
    count, and value.
    """
    js_path = Path(__file__).parent / "static" / "logcalc.js"
    if not js_path.exists():
        pytest.skip("static/logcalc.js not built yet")

    cases = [(16, 16, 1.0, None), (12, 12, 1.0, None), (18, 10, 2.0, None),
             (10, 8, 1.5, None), (16, 10, 1.5, 2.0), (18, 12, 1.0, 2.0)]
    script = (
        "const lc = require(process.argv[1]);"
        "const cases = JSON.parse(process.argv[2]);"
        "console.log(JSON.stringify(cases.map(([d, L, t, ct]) => {"
        "  const p = lc.cutPlan(d, L, t, null, null, null, ct);"
        "  const rec = lc.recommend('Pecan', d, true);"
        "  return [lc.doyleBf(d, L), lc.internationalBf(d, L, null),"
        "          p.yield_bf, p.boards.length,"
        "          lc.planValue(p, 12.0).kiln_dried,"
        "          rec.center, rec.jacket];"
        "})));"
    )
    out = subprocess.run(
        ["node", "-e", script, str(js_path), json.dumps(cases)],
        capture_output=True, text=True, timeout=30, check=True,
    )
    js_results = json.loads(out.stdout)

    for (d, length, t, center), js_row in zip(cases, js_results):
        plan = logscale.cut_plan(d, length, thickness_in=t, center_thickness_in=center)
        rec = logscale.recommend("Pecan", d, kiln=True)
        expected = [
            logscale.doyle_bf(d, length),
            logscale.international_bf(d, length),
            plan.yield_bf,
            len(plan.boards),
            logscale.plan_value(plan, 12.0)["kiln_dried"],
            rec["center"],
            rec["jacket"],
        ]
        assert js_row == pytest.approx(expected), f"JS drifted from Python for {d}x{length}@{t}/{center}"
