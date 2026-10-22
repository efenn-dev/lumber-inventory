"""Log scaling, milling plans, thickness recommendations, and value estimates.

Pure math over plain numbers, same policy as ``kiln.py``: no Flask, no SQLite,
so every formula here is testable on its own. The field page carries a
JavaScript port of this module for offline use at the mill;
``test_logscale.py`` cross-checks the two so they cannot drift apart silently.

Three different numbers get called "board feet" around a log, and conflating
them is how people get cheated in both directions:

* **Doyle scale** -- what a log *buyer* quotes. It famously under-scales small
  logs (the 4-inch slab allowance is brutal under 16" diameter), which is most
  of what a manual mill eats.
* **International 1/4"** -- the most accurate published scale; a fair estimate
  of what a competent mill recovers.
* **Plan yield** -- what the cut list below actually produces. For small logs
  milled through-and-through this usually *beats* Doyle, which is exactly why
  buying logs on Doyle and selling boards by actual measure is a real margin.

On thickness: the answer is rarely one number. The wide center cuts of a good
log are slab, bench, and table stock -- thickness IS the product there -- while
the narrow outer jackets only ever become shelves and glue-up stock. So the
recommender speaks in pairs (center thickness, jacket thickness), and the cut
plan can lay out a mixed cut: thick through the middle, thinner outside, heart
still boxed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

#: Default kerf for a band mill. The HM122 blade cuts about 0.045"; 1/16" is a
#: hair conservative, which is the right direction for a yield promise.
DEFAULT_KERF_IN = 0.0625

#: Boards narrower than this are edging waste, not product.
MIN_BOARD_WIDTH_IN = 3.0

#: Widths are quoted rounded DOWN to this increment. Green boards shrink; a
#: promised width should survive drying.
WIDTH_ROUND_IN = 0.25

#: Standard taper assumption when only the small end was measured: the log
#: gains half an inch of diameter every four feet. Used by International
#: scaling and by nothing else.
TAPER_IN_PER_4FT = 0.5

#: Value multipliers by condition, mirroring the ones ``suggested_price`` uses
#: in app.py: buyers price drying risk into green stock hard.
CONDITION_MULTIPLIERS = {"green": 0.45, "air_dried": 0.75, "kiln_dried": 1.0}

#: In a mixed cut, a board qualifies for the thick center zone only while the
#: log is still wide enough there for slab-grade product.
CENTER_MIN_WIDTH_FRACTION = 0.6
CENTER_MIN_WIDTH_FLOOR_IN = 6.0


# --------------------------------------------------------------------------
# Species milling knowledge
# --------------------------------------------------------------------------

#: Per-species thickness recommendations as (center, jacket) pairs, one set
#: for kiln-equipped drying and one for air-only. Opinionated defaults, built
#: from three facts per species: what its thick stock sells for, how badly it
#: degrades when dried thick, and what products its narrow boards become.
#:
#: The pecan entry is the one that matters most here, and the reasoning is
#: worth recording: commercial pecan/hickory overwhelmingly ships as 4/4
#: flooring stock, so 8/4 live-edge pecan is scarce and commands a premium --
#: but pecan is also on every "difficult to dry" list, and 8/4 pecan
#: air-drying unmonitored is a honeycomb factory. A kiln (especially a solar
#: kiln, whose nightly humidity rebound acts as a daily conditioning cycle)
#: is what makes the thick cut safe. Hence: with a kiln, 8/4 center; without
#: one, cap at 6/4.
SPECIES_MILLING: dict[str, dict[str, Any]] = {
    "Pecan": {
        "kiln": (2.0, 1.5),
        "air": (1.5, 1.0),
        "why_kiln": "Thick pecan is scarce -- the trade saws it 4/4 for flooring -- so 8/4 "
        "slab stock sells at a premium, and the kiln makes drying it safe.",
        "why_air": "8/4 pecan honeycombs when air-dried unmonitored; cap the center at 6/4 "
        "until the kiln exists.",
    },
    "Black Walnut": {
        "kiln": (2.0, 1.0),
        "air": (2.0, 1.0),
        "why_kiln": "8/4 walnut is the most valuable board on the property, and walnut "
        "dries forgivingly at thickness.",
        "why_air": "Walnut air-dries at 8/4 with little drama -- one of the few species "
        "where thick and patient works without a kiln.",
    },
    "Mesquite": {
        "kiln": (2.0, 1.5),
        "air": (2.0, 1.5),
        "why_kiln": "Mesquite is dimensionally the most stable wood in Texas -- thickness "
        "is free value, kiln or not.",
        "why_air": "Mesquite barely moves as it dries; cut it thick without worry.",
    },
    "Eastern Redcedar": {
        "kiln": (1.5, 1.0),
        "air": (1.5, 1.0),
        "why_kiln": "Cedar sells on aroma and rot resistance, not mass -- 4/4 closet "
        "lining and box stock move fastest; keep 6/4 only for mantel and bench blanks.",
        "why_air": "Cedar dries easily at any thickness; the market, not the kiln, is why "
        "most of it should be 4/4.",
    },
    "Red Oak": {
        "kiln": (1.5, 1.0),
        "air": (1.0, 1.0),
        "why_kiln": "Red oak honeycombs at 8/4 even in a kiln -- 6/4 center is the safe "
        "ceiling for slab stock.",
        "why_air": "Air-dried red oak should stay at 4/4; thicker checks and honeycombs.",
    },
    "White Oak": {
        "kiln": (1.5, 1.0),
        "air": (1.0, 1.0),
        "why_kiln": "White oak dries slowly and checks readily at thickness; 6/4 is the "
        "prudent center even kilned.",
        "why_air": "Air-dried white oak at 8/4 is a multi-year gamble; keep it 4/4.",
    },
    "Post Oak": {
        "kiln": (1.5, 1.0),
        "air": (1.0, 1.0),
        "why_kiln": "Treat post oak like white oak: 6/4 ceiling, patience required.",
        "why_air": "Air-dried post oak stays 4/4.",
    },
    "Elm": {
        "kiln": (2.0, 1.0),
        "air": (1.5, 1.0),
        "why_kiln": "Elm's interlocked grain twists thin boards; thick slabs hold flat "
        "and the figure sells.",
        "why_air": "Elm moves hard while drying -- 6/4 center air-only, and expect some loss.",
    },
    "Hackberry": {
        "kiln": (1.0, 1.0),
        "air": (1.0, 1.0),
        "why_kiln": "Hackberry stains and spalts fast; get it thin, dry it quick, sell "
        "the spalt as figure.",
        "why_air": "Same story without the kiln, only more urgent: 4/4 and hurry.",
    },
}

DEFAULT_MILLING: dict[str, Any] = {
    "kiln": (1.5, 1.0),
    "air": (1.0, 1.0),
    "why_kiln": "Unknown species: 6/4 center is a safe default with a kiln.",
    "why_air": "Unknown species air-drying: 4/4 is the conservative cut.",
}

#: Slab product needs width as well as thickness -- a 7"-wide 8/4 board is
#: bench-leg stock, not a slab, and does not command the slab premium. This is
#: the economic fact that justifies cutting thinner jackets outside the
#: center zone.
SLAB_WIDTH_MIN_IN = 10.0

#: Fraction of the thickness premium a narrow (sub-slab-width) board keeps.
NARROW_PREMIUM_FRACTION = 0.4


def thickness_multiplier(thickness_in: float) -> float:
    """Per-board-foot value premium for thickness, at full slab width.

    Thick stock sells for more per board foot, not just more board feet --
    scarcity plus product fit (slab, bench, and table stock has no thin
    substitute).
    """
    if thickness_in >= 2.0:
        return 1.35
    if thickness_in >= 1.5:
        return 1.15
    if thickness_in >= 1.25:
        return 1.05
    return 1.0


def board_multiplier(thickness_in: float, width_in: float) -> float:
    """Value multiplier for one board: the thickness premium, width-gated.

    Boards at slab width carry the full premium; narrow boards keep only
    :data:`NARROW_PREMIUM_FRACTION` of it, because thick-and-narrow is leg and
    lamination stock, which is worth something extra but nothing like a slab.
    """
    full = thickness_multiplier(thickness_in)
    if width_in >= SLAB_WIDTH_MIN_IN:
        return full
    return round(1.0 + (full - 1.0) * NARROW_PREMIUM_FRACTION, 4)


def recommend(species: str | None, small_diameter_in: float, kiln: bool) -> dict[str, Any]:
    """Recommended (center, jacket) thickness for one log.

    ``kiln`` is whether kiln drying is available -- it changes the answer for
    every degrade-prone species, pecan first among them. Small logs collapse
    to a single thickness: under 10" there is no center zone wide enough for
    slab product, so the "thick middle" concept has nothing to attach to.
    """
    spec = SPECIES_MILLING.get(species or "", DEFAULT_MILLING)
    mode = "kiln" if kiln else "air"
    center, jacket = spec[mode]
    why = spec[f"why_{mode}"]

    if small_diameter_in < 10:
        center = jacket
        why = (
            f'Under 10" there is no slab-width center zone -- single thickness. ' + why
        )

    return {"center": center, "jacket": jacket, "mixed": center != jacket, "why": why}


# --------------------------------------------------------------------------
# Log scales
# --------------------------------------------------------------------------


def doyle_bf(small_diameter_in: float, length_ft: float) -> float:
    """Doyle scale: ``((D - 4) / 4)^2 * L``.

    The scale log buyers in Texas and the South actually quote. The flat
    4-inch slab allowance makes it pessimistic on small logs and roughly
    honest above ~28".
    """
    usable = max(0.0, float(small_diameter_in) - 4.0)
    return round((usable / 4.0) ** 2 * max(0.0, float(length_ft)), 1)


def international_bf(
    small_diameter_in: float,
    length_ft: float,
    large_diameter_in: float | None = None,
) -> float:
    """International 1/4" scale, computed segment-wise like the tables are.

    Each 4-foot segment scales at ``0.905 * (0.22 D^2 - 0.71 D)`` with the
    diameter growing by taper toward the butt. When the large end was actually
    measured, the real taper replaces the standard half-inch-per-4-ft
    assumption. A partial trailing segment is pro-rated -- the published
    tables round instead, so expect table agreement within a couple of board
    feet, which is what the tests pin.
    """
    d = float(small_diameter_in)
    length = max(0.0, float(length_ft))
    if d <= 0 or length <= 0:
        return 0.0

    segments = int(length // 4)
    remainder = length - segments * 4

    if large_diameter_in is not None and large_diameter_in > d and length > 0:
        taper_per_seg = (float(large_diameter_in) - d) / (length / 4.0)
    else:
        taper_per_seg = TAPER_IN_PER_4FT

    def segment_bf(diameter: float) -> float:
        return 0.905 * (0.22 * diameter * diameter - 0.71 * diameter)

    total = sum(segment_bf(d + taper_per_seg * i) for i in range(segments))
    if remainder > 1e-9:
        total += segment_bf(d + taper_per_seg * segments) * (remainder / 4.0)
    return round(max(0.0, total), 1)


# --------------------------------------------------------------------------
# Milling plan
# --------------------------------------------------------------------------


@dataclass
class PlannedBoard:
    """One board out of a through-and-through cut plan.

    ``offset_low``/``offset_high`` are the board's vertical band relative to
    the pith, recorded so the SVG can draw exactly what was planned -- mixed
    cuts made reconstructing bands from a single thickness impossible.
    """

    thickness_in: float
    width_in: float
    length_ft: float
    board_feet: float
    contains_pith: bool = False
    offset_low: float = 0.0
    offset_high: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class MillingPlan:
    """A full plan for one log."""

    boards: list[PlannedBoard] = field(default_factory=list)
    yield_bf: float = 0.0
    doyle_bf: float = 0.0
    international_bf: float = 0.0
    advice: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "boards": [b.as_dict() for b in self.boards],
            "yield_bf": self.yield_bf,
            "doyle_bf": self.doyle_bf,
            "international_bf": self.international_bf,
            "advice": list(self.advice),
        }


def _round_width_down(width_in: float) -> float:
    return math.floor(width_in / WIDTH_ROUND_IN) * WIDTH_ROUND_IN


def _chord(radius: float, offset: float) -> float:
    """Width of the log's circular cross-section at ``offset`` from center."""
    if abs(offset) >= radius:
        return 0.0
    return 2.0 * math.sqrt(radius * radius - offset * offset)


def cut_plan(
    small_diameter_in: float,
    length_ft: float,
    thickness_in: float = 1.0,
    kerf_in: float = DEFAULT_KERF_IN,
    min_width_in: float = MIN_BOARD_WIDTH_IN,
    large_diameter_in: float | None = None,
    center_thickness_in: float | None = None,
) -> MillingPlan:
    """Through-and-through cut plan for one log.

    With only ``thickness_in``, every board is that thickness. Pass
    ``center_thickness_in`` for a mixed cut: boards stay at the center
    thickness while the log is still slab-width there (at least
    ``CENTER_MIN_WIDTH_FRACTION`` of the diameter, floored at
    ``CENTER_MIN_WIDTH_FLOOR_IN``), then drop to ``thickness_in`` jackets for
    the narrower outer cuts. That mirrors what a sawyer actually does with a
    good log: thick money boards through the middle, shelf stock outside.

    The layout centers one board on the pith either way, per the sawing rule
    this whole project lives by: the pith board splits no matter what, so
    isolate it in a single sacrificial board instead of ruining two good ones.

    Board width is governed by the *narrow* face -- the chord at whichever
    edge of the board sits farther from center -- and the small end of the log
    governs everything, so every promised width exists along the full length.
    Widths round down to :data:`WIDTH_ROUND_IN`; anything under
    ``min_width_in`` is edging waste and is dropped.
    """
    plan = MillingPlan()
    d = float(small_diameter_in)
    length = float(length_ft)
    jacket_t = float(thickness_in)
    center_t = float(center_thickness_in) if center_thickness_in else jacket_t
    kerf = max(0.0, float(kerf_in))

    plan.doyle_bf = doyle_bf(d, length)
    plan.international_bf = international_bf(d, length, large_diameter_in)

    if d <= 0 or length <= 0 or jacket_t <= 0 or center_t <= 0:
        plan.advice.append("Need a positive diameter, length, and thickness.")
        return plan

    radius = d / 2.0
    mixed = center_thickness_in is not None and center_t != jacket_t
    center_width_min = max(CENTER_MIN_WIDTH_FLOOR_IN, CENTER_MIN_WIDTH_FRACTION * d)

    def board_at(low: float, high: float, pith: bool) -> PlannedBoard | None:
        width = _round_width_down(_chord(radius, max(abs(low), abs(high))))
        if width < min_width_in:
            return None
        t = high - low
        bf = t * width * (length * 12.0) / 144.0
        return PlannedBoard(round(t, 4), width, length, round(bf, 2), pith, round(low, 4), round(high, 4))

    # The pith board takes the center thickness -- it is sacrificial stock
    # either way, and keeping the center zone's rhythm intact matters more
    # than what the sticker board measures.
    pith_board = board_at(-center_t / 2.0, center_t / 2.0, True)
    if pith_board:
        plan.boards.append(pith_board)

    offset = center_t / 2.0 + kerf
    while True:
        # Choose this course's thickness: center stock while the far edge of a
        # center-thickness board would still be slab-width, jackets after.
        t = center_t
        if mixed and _chord(radius, offset + center_t) < center_width_min:
            t = jacket_t
        if offset + t >= radius:
            break
        above = board_at(offset, offset + t, False)
        if above is None:
            break  # boards only get narrower from here
        plan.boards.append(above)
        below = board_at(-offset - t, -offset, False)
        if below:
            plan.boards.append(below)
        offset += t + kerf

    plan.yield_bf = round(sum(b.board_feet for b in plan.boards), 1)

    if pith_board:
        plan.advice.append(
            "The centered board carries the pith and WILL split -- box the heart: "
            "use it for stickers, blocking, or epoxy stock, and sell the rest."
        )
    if mixed and plan.boards:
        thick = sum(1 for b in plan.boards if b.thickness_in >= center_t)
        thin = len(plan.boards) - thick
        if thick and thin:
            plan.advice.append(
                f"Mixed cut: {thick} boards at {_trim(center_t)}\" through the middle, "
                f"{thin} jackets at {_trim(jacket_t)}\" outside."
            )
    if d < 8:
        plan.advice.append(
            "Under 8\" recovery is mostly edging waste; this log may be worth more "
            "as stickers or firewood than as mill time."
        )
    if plan.yield_bf > plan.doyle_bf > 0:
        plan.advice.append(
            f"Plan yield ({plan.yield_bf} bf) beats Doyle scale ({plan.doyle_bf} bf) -- "
            "normal for small logs. Buy on Doyle, sell on actual measure."
        )
    return plan


def _trim(value: float) -> str:
    """Render 2.0 as '2' and 1.5 as '1.5'."""
    return f"{value:g}"


# --------------------------------------------------------------------------
# Value
# --------------------------------------------------------------------------


def value_estimate(yield_bf: float, rate_per_bf: float) -> dict[str, float]:
    """Flat asking-price potential of ``yield_bf`` at one rate, by condition.

    Thickness-blind -- kept for simple totals. :func:`plan_value` is the
    thickness-aware version and is what plan comparisons should use.
    """
    return {
        condition: round(yield_bf * rate_per_bf * multiplier, 2)
        for condition, multiplier in CONDITION_MULTIPLIERS.items()
    }


def plan_value(plan: MillingPlan, rate_per_bf: float) -> dict[str, float]:
    """Asking-price potential of a plan's boards, thickness premium included.

    Per board: ``bf x rate x thickness multiplier x condition multiplier``.
    This is what the *boards* could list for after milling and drying -- NOT
    what to pay for the log. Log purchases price on Doyle at a fraction of
    retail; the gap is the milling and drying margin.
    """
    base = sum(
        b.board_feet * rate_per_bf * board_multiplier(b.thickness_in, b.width_in)
        for b in plan.boards
    )
    return {
        condition: round(base * multiplier, 2)
        for condition, multiplier in CONDITION_MULTIPLIERS.items()
    }


def compare_options(
    small_diameter_in: float,
    length_ft: float,
    species: str | None,
    kiln: bool,
    rate_per_bf: float | None,
    kerf_in: float = DEFAULT_KERF_IN,
    large_diameter_in: float | None = None,
) -> dict[str, Any]:
    """Every sensible way to open this log, with the recommended one marked.

    Returns uniform 4/4, 6/4, and 8/4 plans plus the species-aware
    recommendation (mixed when the species and diameter justify it), each with
    yield and kiln-dried value so the recommendation is an argument, not a
    decree.
    """
    rec = recommend(species, small_diameter_in, kiln)

    options: list[dict[str, Any]] = []
    for label, thickness in (("4/4", 1.0), ("6/4", 1.5), ("8/4", 2.0)):
        plan = cut_plan(
            small_diameter_in, length_ft, thickness, kerf_in,
            large_diameter_in=large_diameter_in,
        )
        options.append({"label": label, "thickness": thickness, "center": None, "plan": plan})

    if rec["mixed"]:
        plan = cut_plan(
            small_diameter_in, length_ft, rec["jacket"], kerf_in,
            large_diameter_in=large_diameter_in, center_thickness_in=rec["center"],
        )
        options.append(
            {
                "label": f"mix {_trim(rec['center'])}\"/{_trim(rec['jacket'])}\"",
                "thickness": rec["jacket"],
                "center": rec["center"],
                "plan": plan,
            }
        )
        recommended = len(options) - 1
    else:
        recommended = min(
            range(3), key=lambda i: abs(options[i]["thickness"] - rec["center"])
        )

    for option in options:
        option["value_kiln"] = (
            plan_value(option["plan"], rate_per_bf)["kiln_dried"] if rate_per_bf else None
        )

    return {"options": options, "recommended": recommended, "why": rec["why"], "rec": rec}


# --------------------------------------------------------------------------
# Cross-section SVG
# --------------------------------------------------------------------------


def plan_svg(plan: MillingPlan, small_diameter_in: float, size_px: int = 260) -> str:
    """End-view SVG of the log with the planned cuts drawn on it.

    Draws each board from its stored band offsets, so mixed-thickness plans
    render exactly as laid out. Colors ride on the app's CSS variables so the
    diagram follows the theme.
    """
    d = float(small_diameter_in)
    if d <= 0 or not plan.boards:
        return ""

    scale = (size_px * 0.9) / d
    cx = cy = size_px / 2.0
    r = (d / 2.0) * scale

    parts = [
        f'<svg viewBox="0 0 {size_px} {size_px}" class="log-plan" role="img" '
        f'aria-label="End view of the log with planned cuts">',
        f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" fill="var(--panel-2)" '
        f'stroke="var(--line)" stroke-width="2"/>',
    ]

    for board in plan.boards:
        width = _chord(d / 2.0, max(abs(board.offset_low), abs(board.offset_high)))
        if width <= 0:
            continue
        t = board.offset_high - board.offset_low
        x = cx - (width / 2.0) * scale
        y = cy - board.offset_high * scale
        fill = "var(--danger)" if board.contains_pith else "var(--accent)"
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{width * scale:.1f}" '
            f'height="{t * scale:.1f}" fill="{fill}" fill-opacity="0.35" '
            f'stroke="{fill}" stroke-width="1"/>'
        )

    parts.append("</svg>")
    return "".join(parts)
