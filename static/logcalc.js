/* Log scaling, milling plans, thickness recommendations — JS port of logscale.py.
 *
 * This runs OFFLINE on the field page at the mill, so it cannot lean on the
 * server. test_logscale.py cross-checks it against the Python module on every
 * test run; if you change a formula, change both files or the suite fails.
 *
 * Works as a browser global (window.logcalc) and as a CommonJS module so the
 * pytest cross-check can require() it under node.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.logcalc = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const DEFAULT_KERF_IN = 0.0625;
  const MIN_BOARD_WIDTH_IN = 3.0;
  const WIDTH_ROUND_IN = 0.25;
  const TAPER_IN_PER_4FT = 0.5;
  const CONDITION_MULTIPLIERS = { green: 0.45, air_dried: 0.75, kiln_dried: 1.0 };
  const CENTER_MIN_WIDTH_FRACTION = 0.6;
  const CENTER_MIN_WIDTH_FLOOR_IN = 6.0;
  const SLAB_WIDTH_MIN_IN = 10.0;
  const NARROW_PREMIUM_FRACTION = 0.4;

  /* Mirrors logscale.SPECIES_MILLING — (center, jacket) by drying mode. */
  const SPECIES_MILLING = {
    "Pecan": {
      kiln: [2.0, 1.5], air: [1.5, 1.0],
      why_kiln: "Thick pecan is scarce -- the trade saws it 4/4 for flooring -- so 8/4 slab stock sells at a premium, and the kiln makes drying it safe.",
      why_air: "8/4 pecan honeycombs when air-dried unmonitored; cap the center at 6/4 until the kiln exists.",
    },
    "Black Walnut": {
      kiln: [2.0, 1.0], air: [2.0, 1.0],
      why_kiln: "8/4 walnut is the most valuable board on the property, and walnut dries forgivingly at thickness.",
      why_air: "Walnut air-dries at 8/4 with little drama -- one of the few species where thick and patient works without a kiln.",
    },
    "Mesquite": {
      kiln: [2.0, 1.5], air: [2.0, 1.5],
      why_kiln: "Mesquite is dimensionally the most stable wood in Texas -- thickness is free value, kiln or not.",
      why_air: "Mesquite barely moves as it dries; cut it thick without worry.",
    },
    "Eastern Redcedar": {
      kiln: [1.5, 1.0], air: [1.5, 1.0],
      why_kiln: "Cedar sells on aroma and rot resistance, not mass -- 4/4 closet lining and box stock move fastest; keep 6/4 only for mantel and bench blanks.",
      why_air: "Cedar dries easily at any thickness; the market, not the kiln, is why most of it should be 4/4.",
    },
    "Red Oak": {
      kiln: [1.5, 1.0], air: [1.0, 1.0],
      why_kiln: "Red oak honeycombs at 8/4 even in a kiln -- 6/4 center is the safe ceiling for slab stock.",
      why_air: "Air-dried red oak should stay at 4/4; thicker checks and honeycombs.",
    },
    "White Oak": {
      kiln: [1.5, 1.0], air: [1.0, 1.0],
      why_kiln: "White oak dries slowly and checks readily at thickness; 6/4 is the prudent center even kilned.",
      why_air: "Air-dried white oak at 8/4 is a multi-year gamble; keep it 4/4.",
    },
    "Post Oak": {
      kiln: [1.5, 1.0], air: [1.0, 1.0],
      why_kiln: "Treat post oak like white oak: 6/4 ceiling, patience required.",
      why_air: "Air-dried post oak stays 4/4.",
    },
    "Elm": {
      kiln: [2.0, 1.0], air: [1.5, 1.0],
      why_kiln: "Elm's interlocked grain twists thin boards; thick slabs hold flat and the figure sells.",
      why_air: "Elm moves hard while drying -- 6/4 center air-only, and expect some loss.",
    },
    "Hackberry": {
      kiln: [1.0, 1.0], air: [1.0, 1.0],
      why_kiln: "Hackberry stains and spalts fast; get it thin, dry it quick, sell the spalt as figure.",
      why_air: "Same story without the kiln, only more urgent: 4/4 and hurry.",
    },
  };
  const DEFAULT_MILLING = {
    kiln: [1.5, 1.0], air: [1.0, 1.0],
    why_kiln: "Unknown species: 6/4 center is a safe default with a kiln.",
    why_air: "Unknown species air-drying: 4/4 is the conservative cut.",
  };

  /* Round half-to-even, matching Python's round() -- the cross-check test
   * compares yields to the decimal, and JS's half-up drifts on exact halves
   * (89.25 -> 89.3 while Python says 89.2). */
  function roundEven(x, digits) {
    const factor = 10 ** digits;
    const scaled = x * factor;
    const floor = Math.floor(scaled);
    const diff = scaled - floor;
    if (Math.abs(diff - 0.5) < 1e-9) return (floor % 2 === 0 ? floor : floor + 1) / factor;
    return Math.round(scaled) / factor;
  }
  const round1 = (x) => roundEven(x, 1);
  const round2 = (x) => roundEven(x, 2);
  const round4 = (x) => roundEven(x, 4);
  const trim = (x) => String(Number(x));

  function thicknessMultiplier(t) {
    if (t >= 2.0) return 1.35;
    if (t >= 1.5) return 1.15;
    if (t >= 1.25) return 1.05;
    return 1.0;
  }

  function boardMultiplier(t, width) {
    const full = thicknessMultiplier(t);
    if (width >= SLAB_WIDTH_MIN_IN) return full;
    return round4(1.0 + (full - 1.0) * NARROW_PREMIUM_FRACTION);
  }

  function recommend(species, smallDiameterIn, kiln) {
    const spec = SPECIES_MILLING[species || ""] || DEFAULT_MILLING;
    const mode = kiln ? "kiln" : "air";
    let [center, jacket] = spec[mode];
    let why = spec["why_" + mode];
    if (smallDiameterIn < 10) {
      center = jacket;
      why = 'Under 10" there is no slab-width center zone -- single thickness. ' + why;
    }
    return { center, jacket, mixed: center !== jacket, why };
  }

  function doyleBf(smallDiameterIn, lengthFt) {
    const usable = Math.max(0, smallDiameterIn - 4);
    return round1((usable / 4) ** 2 * Math.max(0, lengthFt));
  }

  function internationalBf(smallDiameterIn, lengthFt, largeDiameterIn) {
    const d = Number(smallDiameterIn);
    const length = Math.max(0, Number(lengthFt));
    if (!(d > 0) || !(length > 0)) return 0.0;

    const segments = Math.floor(length / 4);
    const remainder = length - segments * 4;
    const taperPerSeg =
      largeDiameterIn && largeDiameterIn > d && length > 0
        ? (largeDiameterIn - d) / (length / 4)
        : TAPER_IN_PER_4FT;

    const segmentBf = (dd) => 0.905 * (0.22 * dd * dd - 0.71 * dd);

    let total = 0;
    for (let i = 0; i < segments; i++) total += segmentBf(d + taperPerSeg * i);
    if (remainder > 1e-9) total += segmentBf(d + taperPerSeg * segments) * (remainder / 4);
    return round1(Math.max(0, total));
  }

  const roundWidthDown = (w) => Math.floor(w / WIDTH_ROUND_IN) * WIDTH_ROUND_IN;

  function chord(radius, offset) {
    if (Math.abs(offset) >= radius) return 0;
    return 2 * Math.sqrt(radius * radius - offset * offset);
  }

  function cutPlan(smallDiameterIn, lengthFt, thicknessIn, kerfIn, minWidthIn, largeDiameterIn, centerThicknessIn) {
    const d = Number(smallDiameterIn);
    const length = Number(lengthFt);
    const jacketT = Number(thicknessIn || 1.0);
    const centerT = centerThicknessIn ? Number(centerThicknessIn) : jacketT;
    const kerf = Math.max(0, kerfIn == null ? DEFAULT_KERF_IN : Number(kerfIn));
    const minWidth = minWidthIn == null ? MIN_BOARD_WIDTH_IN : Number(minWidthIn);

    const plan = {
      boards: [],
      yield_bf: 0,
      doyle_bf: doyleBf(d, length),
      international_bf: internationalBf(d, length, largeDiameterIn),
      advice: [],
    };
    if (!(d > 0) || !(length > 0) || !(jacketT > 0) || !(centerT > 0)) {
      plan.advice.push("Need a positive diameter, length, and thickness.");
      return plan;
    }

    const radius = d / 2;
    const mixed = centerThicknessIn != null && centerT !== jacketT;
    const centerWidthMin = Math.max(CENTER_MIN_WIDTH_FLOOR_IN, CENTER_MIN_WIDTH_FRACTION * d);

    const boardAt = (low, high, pith) => {
      const width = roundWidthDown(chord(radius, Math.max(Math.abs(low), Math.abs(high))));
      if (width < minWidth) return null;
      const t = high - low;
      const bf = (t * width * (length * 12)) / 144;
      return {
        thickness_in: round4(t), width_in: width, length_ft: length,
        board_feet: round2(bf), contains_pith: pith,
        offset_low: round4(low), offset_high: round4(high),
      };
    };

    const pithBoard = boardAt(-centerT / 2, centerT / 2, true);
    if (pithBoard) plan.boards.push(pithBoard);

    let offset = centerT / 2 + kerf;
    for (;;) {
      let t = centerT;
      if (mixed && chord(radius, offset + centerT) < centerWidthMin) t = jacketT;
      if (offset + t >= radius) break;
      const above = boardAt(offset, offset + t, false);
      if (above === null) break;
      plan.boards.push(above);
      const below = boardAt(-offset - t, -offset, false);
      if (below) plan.boards.push(below);
      offset += t + kerf;
    }

    plan.yield_bf = round1(plan.boards.reduce((sum, b) => sum + b.board_feet, 0));

    if (pithBoard) {
      plan.advice.push(
        "The centered board carries the pith and WILL split -- box the heart: " +
          "use it for stickers, blocking, or epoxy stock, and sell the rest."
      );
    }
    if (mixed && plan.boards.length) {
      const thick = plan.boards.filter((b) => b.thickness_in >= centerT).length;
      const thin = plan.boards.length - thick;
      if (thick && thin) {
        plan.advice.push(
          `Mixed cut: ${thick} boards at ${trim(centerT)}" through the middle, ${thin} jackets at ${trim(jacketT)}" outside.`
        );
      }
    }
    if (d < 8) {
      plan.advice.push(
        'Under 8" recovery is mostly edging waste; this log may be worth more as stickers or firewood than as mill time.'
      );
    }
    if (plan.yield_bf > plan.doyle_bf && plan.doyle_bf > 0) {
      plan.advice.push(
        `Plan yield (${plan.yield_bf} bf) beats Doyle scale (${plan.doyle_bf} bf) -- ` +
          "normal for small logs. Buy on Doyle, sell on actual measure."
      );
    }
    return plan;
  }

  function valueEstimate(yieldBf, ratePerBf) {
    const out = {};
    for (const [condition, mult] of Object.entries(CONDITION_MULTIPLIERS)) {
      out[condition] = round2(yieldBf * ratePerBf * mult);
    }
    return out;
  }

  function planValue(plan, ratePerBf) {
    const base = plan.boards.reduce(
      (sum, b) => sum + b.board_feet * ratePerBf * boardMultiplier(b.thickness_in, b.width_in), 0
    );
    const out = {};
    for (const [condition, mult] of Object.entries(CONDITION_MULTIPLIERS)) {
      out[condition] = round2(base * mult);
    }
    return out;
  }

  function compareOptions(d, L, species, kiln, ratePerBf, kerfIn, largeDiameterIn) {
    const rec = recommend(species, d, kiln);
    const options = [
      ["4/4", 1.0], ["6/4", 1.5], ["8/4", 2.0],
    ].map(([label, t]) => ({
      label, thickness: t, center: null,
      plan: cutPlan(d, L, t, kerfIn, null, largeDiameterIn),
    }));

    let recommended;
    if (rec.mixed) {
      options.push({
        label: `mix ${trim(rec.center)}"/${trim(rec.jacket)}"`,
        thickness: rec.jacket, center: rec.center,
        plan: cutPlan(d, L, rec.jacket, kerfIn, null, largeDiameterIn, rec.center),
      });
      recommended = options.length - 1;
    } else {
      recommended = [0, 1, 2].reduce((best, i) =>
        Math.abs(options[i].thickness - rec.center) < Math.abs(options[best].thickness - rec.center) ? i : best, 0);
    }

    for (const option of options) {
      option.value_kiln = ratePerBf ? planValue(option.plan, ratePerBf).kiln_dried : null;
    }
    return { options, recommended, why: rec.why, rec };
  }

  /* End-view SVG drawn from each board's stored band, so mixed plans render
   * exactly as laid out. Mirrors logscale.plan_svg. */
  function planSvg(plan, smallDiameterIn, sizePx) {
    const d = Number(smallDiameterIn);
    const size = sizePx || 260;
    if (!(d > 0) || !plan.boards.length) return "";

    const scale = (size * 0.9) / d;
    const c = size / 2;
    const r = (d / 2) * scale;

    let svg =
      `<svg viewBox="0 0 ${size} ${size}" class="log-plan" role="img" aria-label="End view of the log with planned cuts">` +
      `<circle cx="${c}" cy="${c}" r="${r.toFixed(1)}" fill="var(--panel-2)" stroke="var(--line)" stroke-width="2"/>`;

    for (const board of plan.boards) {
      const width = chord(d / 2, Math.max(Math.abs(board.offset_low), Math.abs(board.offset_high)));
      if (width <= 0) continue;
      const t = board.offset_high - board.offset_low;
      const x = c - (width / 2) * scale;
      const y = c - board.offset_high * scale;
      const fill = board.contains_pith ? "var(--danger)" : "var(--accent)";
      svg +=
        `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${(width * scale).toFixed(1)}" ` +
        `height="${(t * scale).toFixed(1)}" fill="${fill}" fill-opacity="0.35" stroke="${fill}" stroke-width="1"/>`;
    }
    return svg + "</svg>";
  }

  return {
    doyleBf, internationalBf, cutPlan, valueEstimate, planValue, planSvg,
    recommend, compareOptions, thicknessMultiplier, boardMultiplier,
    DEFAULT_KERF_IN, SPECIES_MILLING,
  };
});
