# Lumber Inventory

Track milled lumber from log to sale: dimensions, board feet, drying progress,
photos, asking prices, and what actually sold. Built to be used from a phone
while standing at the stack.

## Setup

```bash
cd C:\projects\lumber-inventory
pip install -r requirements.txt
python app.py
```

The database (`lumber.db`) and the photo folder are created on first run.

## Using it from your phone

`app.py` binds to `0.0.0.0`, so on startup it prints two URLs:

```
On this PC:    http://127.0.0.1:5300
On your phone: http://192.168.1.x:5300
```

Use the second one from any device on the same wifi. The photo field uses
`capture="environment"`, so on a phone it opens the rear camera directly.

If the phone can't reach it, Windows Firewall is the usual culprit — allow
Python on private networks, or run:

```powershell
New-NetFirewallRule -DisplayName "Lumber Inventory" -Direction Inbound -LocalPort 5300 -Protocol TCP -Action Allow -Profile Private
```

## What it does

**Board feet** are calculated for you from thickness x width x length
(1 bd ft = 144 cubic inches). Enter 4/4 as `1`, 5/4 as `1.25`, 8/4 as `2`.

**Drying** works one of two ways, set per piece by the Method field:

- **air** — the rule of thumb of roughly one year per inch of thickness.
- **kiln** — predicted from live sensor readings (see below), which is both
  faster and honest about when it will not work.

For standing-dead stock that was already dry when milled, tick "Already dry"
and it skips the clock.

**Status** moves green → drying → kiln → ready → listed → sold. Tap the chips
on the detail page to change it; marking a piece sold stamps today's date.

**Listing text** is generated on the detail page — dimensions, board feet,
species blurb, condition, your notes, and price, formatted for Marketplace.
One button copies it to the clipboard.

**Sold** (the `/stats` page) is the one that earns its keep over time. After a
few months it tells you which species and price points actually move in your
market, including realized dollars per board foot — which is worth more than
any guess about what lumber "should" sell for.

## The kiln page

`/kiln` is the solar kiln dashboard: current conditions, what is in the box,
how far it has to go, and what is going wrong.

The number it is built around is **equilibrium moisture content (EMC)** — the
moisture content wood will settle at in given air. A thermometer alone cannot
tell you how wet the wood is, but temperature plus humidity tells you where it
is *heading*, and that is enough to predict and to alarm. `kiln.py` computes it
with the Hailwood-Horrobin model from the USDA Forest Products Laboratory *Wood
Handbook*, the same one published kiln schedules are built on.

The consequence worth knowing: **if EMC is above your target, the wood will
never reach target, however long you run the kiln.** The dashboard says so in
those words rather than showing a countdown that will never finish.

EMC is averaged across the whole daily cycle, not sampled at a moment. A solar
kiln swings hard — 140°F at 32% RH in the afternoon, 72°F at 78% overnight —
and a 4/4 board has a time constant of weeks, so it integrates the day rather
than tracking it. A kiln that hits 5% at noon and gives it all back by dawn
settles near the average, not the best hour.

It also tracks **sterilization**: 133°F held for 30 continuous minutes is the
standard heat-treatment spec for killing powderpost beetle larvae. Pecan is
high-starch and a favourite target, so this matters before anything ships.

Readings can be typed in by hand on the kiln page — useful before any hardware
exists.

## Sensor API

For an IoT device in the kiln. `temp_f` and `rh_pct` are the only required
fields and `recorded_at` defaults to now, so a board with no clock can just
POST and go.

```
POST /api/kiln/reading
Content-Type: application/json

{"temp_f": 118.4, "rh_pct": 42.1, "source": "kiln-esp32-1"}
```

Optional: `recorded_at` (ISO 8601, `Z` suffix tolerated), `wood_mc_pct` for an
in-kiln moisture probe, `source` for a device name. Readings automatically
attach to whichever charge is open. Returns `201` with the computed EMC, so the
device can display it without a second call.

```
POST /api/kiln/readings       batch, for a device that buffers and flushes
GET  /api/kiln/status         current conditions, EMC, alerts, sterilization
GET  /api/kiln/readings?hours=48
```

The batch endpoint takes `{"source": "...", "readings": [...]}` or a bare array.
**Timestamps are required there** — unlike the single-reading endpoint, "now" is
meaningless for a backlog and would stamp a week of history to one instant.

## Importing from an offline logger

No wifi or mains power at the kiln is the normal case. The cheap answer is a
logger that writes a CSV to an SD card on the fan array's solar supply; you pull
the card when you drive out and upload it on the kiln page.

Two things the firmware must get right:

- **Fit a real-time clock** (a DS3231 is about $5). Timestamps drive the
  cycle-average EMC, and readings without them are close to useless.
- **Give the sensor its own small battery.** If the fans are solar-*direct*
  there is no power after sundown, and the overnight readings are precisely the
  ones that decide whether the kiln can reach target at all.

The importer is deliberately forgiving about format, because firmware gets
written once at a workbench and should not have to be reflashed to match a spec:

| Field | Accepted column names |
|---|---|
| time | `recorded_at`, `timestamp`, `time`, `datetime`, `date`, `ts`, `when` |
| temperature | `temp_f`, `temperature`, `temp`, `t` — or `temp_c`/`celsius`, converted |
| humidity | `rh_pct`, `humidity`, `rh`, `relative_humidity`, `h` |
| wood moisture | `wood_mc_pct`, `mc`, `moisture` (optional) |

Timestamps may be ISO 8601 (with or without `T`/`Z`), `YYYY-MM-DD HH:MM:SS`,
`MM/DD/YYYY HH:MM`, or Unix epoch in seconds or milliseconds.

Bad rows are skipped and reported rather than aborting the import — a card
pulled out of a hot box often has one truncated final line, and losing a month
of data over it would be absurd.

**Re-importing is safe.** Readings are deduplicated on `(recorded_at, source)`,
so plugging the same card in twice, or uploading overlapping windows, counts
duplicates instead of storing them. This matters more than it sounds: double
counted samples would quietly skew the cycle-average EMC that every prediction
and alert is built on. Two different sensors reporting the same instant are not
duplicates, since they carry different `source` labels.

Imported readings attach to whichever charge was open *at the reading's
timestamp*, not to whatever is open when the card is plugged in.

`/api/kiln/status` is the one to poll if the device has a screen or a warning
LED — it returns the same alerts the dashboard shows.

**Auth.** Set `LUMBER_KILN_TOKEN` in the environment and writes require it, as
either an `X-Kiln-Token` header or a `token` field in the body (microcontroller
HTTP clients often manage only one of the two). Reads stay open either way, so
a display can poll without holding a secret. With no token set, anything on
your wifi can write readings — fine at home, and the reason the option exists.

## The phone app (Fencerow Field)

`/field` is an installable app for the phone: capture logs, boards, and kiln
readings **standing at the mill with zero signal**. Everything queues in the
phone's own storage and syncs automatically the moment the server is reachable
-- on load, on regaining signal, or on the Sync button. Every sync target is
idempotent (records carry a client UUID, readings dedupe on timestamp), so a
retry after a dropped connection can never double-book anything.

The Log tab is the field calculator: species, small-end diameter, and length
give you Doyle scale, International 1/4" scale, a full through-and-through cut
list with the pith board flagged, an end-view diagram, and a value range at
your species rates -- all computed on the phone, no server involved. The math
is a JavaScript port of `logscale.py`, and the test suite cross-checks the two
so they cannot drift apart.

### Reaching it away from home

The mill has no wifi, and this app lives on your home LAN -- so the phone needs
a road to it. Tailscale is already on this machine and the phone is already on
the tailnet; two steps make it reachable from anywhere over cellular:

1. On this PC: `tailscale serve --bg 5300` -- **already done 2026-08-27**; the app is live at `https://<your-machine>.<tailnet>.ts.net`. To undo: `tailscale serve --https=443 off`.
   -- that publishes the app inside your tailnet (and only your tailnet) at
   `https://<this-machine>.<tailnet>.ts.net`, with a real HTTPS certificate.
2. On the phone: open the Tailscale app and turn it on, then visit that URL.

Install from **that** HTTPS address (Chrome menu -> "Add to Home screen"), not
from the LAN IP: the page syncs to wherever it was installed from, and the
Tailscale address is the one that works from the truck. HTTPS also unlocks the
service worker, which is what makes the page load with no signal at all.

### At the kiln

The Kiln tab logs spot readings by hand, imports the logger's CSV straight off
an SD card, or -- once the firmware exists -- pulls the CSV wirelessly from the
sensor's own wifi hotspot (join `KILN-SENSOR`, tap "Pull CSV from device").
Either way it queues and syncs home like everything else.

## Logs and the milling calculator

`/logs` tracks the round things before they become pieces. Each log gets
photos, dimensions, and a live milling plan (`/log/<id>`): pick a thickness and
kerf and it recomputes the cut list, scales, and value on the spot. The three
numbers it shows are deliberately different -- Doyle is what a log *buyer*
quotes (famously stingy on small logs), International 1/4" is a fair recovery
estimate, and plan yield is what the cut list actually produces. Buy on Doyle,
sell on actual measure; the gap between those numbers is the milling margin.

Value estimates are the *boards'* asking-price potential at your species rates,
discounted by condition -- not what to pay for the log.

### Thickness recommendation

Log pages open on a **recommended cut**, not a default 4/4. The recommendation
is species- and kiln-aware (the "Assume kiln drying" setting drives it): pecan
with a kiln gets an 8/4 center with 6/4 jackets, because commercial pecan ships
as 4/4 flooring stock and thick slab material is scarce -- while red oak caps
at 6/4 even kilned (honeycomb), walnut and mesquite go thick even air-dried,
and anything under 10" collapses to a single thickness because there is no
slab-width center zone.

A comparison table shows 4/4, 6/4, 8/4, and the recommended mix side by side.
Two honest facts it encodes: **yield barely moves with thickness** (kerf
savings are eaten by rim discretization -- the case for thick cuts is value
per foot, not recovery), and **the thickness premium is width-gated** (a
7"-wide 8/4 board is leg stock, not a slab, and is priced like it -- which is
exactly why the jackets are cut thinner). The recommended mix models within a
couple percent of pure 8/4; its real edge is that 6/4 jackets finish at 1-1/4",
the best-selling shelf thickness.

The species table lives in `SPECIES_MILLING` in `logscale.py` (mirrored in
`logcalc.js`) -- edit both together; the cross-check test enforces agreement.

## Photo analysis (species + defects)

On a log's page, "Analyze newest photo" sends it through a vision model and
returns species candidates (constrained to the species that actually grow on
the property), visible defects, and milling advice. Everything is a suggestion
-- nothing touches the species field until you click "use this".

Backends, tried in order:

1. **Local** -- the Qwen3-VL llama-server on `:18080`. Start it with
   `powershell -File C:/projects/app-manager-mcp/scripts/start-visual-debug-llama.ps1`.
   First call after the model loads can take 2-3 minutes; later calls are fast.
   Override with `LUMBER_VISION_URL`.
2. **Fallback** -- any OpenAI-compatible hosted API, via
   `LUMBER_VISION_FALLBACK_URL`, `LUMBER_VISION_FALLBACK_KEY`, and
   `LUMBER_VISION_FALLBACK_MODEL`.

Expectation-setting: a field-scale vision model is reliable at telling pecan
from cedar from oak, and shaky at fine species calls. Photograph the **end
grain** as well as the bark -- that is where the signal is. The analysis runs
on this machine, so it happens at home after sync, not at the mill; the
deterministic calculator is the part that works in the field.

## Listing workflow

`/listings` is the selling desk. Facebook Marketplace has no API for
individual sellers, so the post itself is copy-paste on the phone; the app
owns everything around it -- the rule that **green wood is never listable**,
the keyword-first listing pack (with the kiln-dried and heat-sterilized lines
when the kiln data backs them up, and figure tags like Ambrosia or Spalted
pulled from the notes), the link back to the live listing, a renew nudge after
seven days, and the one-tap sale record that feeds the Sold report.

The playbook -- photos, title formula, pricing ladder, reply templates, pickup
rules, and the website step -- is in [LISTING.md](LISTING.md).

## Settings

`/settings` tunes the drying model, the alert thresholds, and the per-species
rates. The shipped numbers are back-calculated from trade rules of thumb, not
measured on this kiln, and they are meant to be replaced once a real charge has
been logged. A blank field falls back to the shipped default rather than
becoming zero.

**Calibration.** Log two or more wood moisture readings during one charge -- a
$35 handheld meter and the manual reading form are enough -- and the kiln page
fits the drying time constant your kiln actually achieves, with the r-squared of
the fit so you can see whether one number really describes what happened. A low
r-squared usually means conditions changed mid-charge.

The equilibrium used for that fit is averaged over the period the moisture
readings span, not over recent conditions, so it works on a batch imported from
an SD card weeks after the fact.

**If you are on the SD-card workflow, raise "Sensor considered dead after" to
around 200 hours.** At the default 6 the dashboard shows a stale-sensor alarm
permanently between card pulls, and a permanent alarm is one you stop reading.

## Editing logged readings

`/kiln/readings` is an editable table of every reading. Sensors drift and
fingers slip, and one bad sample skews the cycle-average EMC that every
prediction leans on. Editing a timestamp re-attaches the reading to whichever
charge covers the new time; a per-source "delete all" clears out test data
without hand-writing SQL.

## Hardware

See [HARDWARE.md](HARDWARE.md) for the sensor node parts list, why each part is
the one it is, and what the firmware has to do.

## Species presets

`price_per_bf` also drives a suggested asking price on the detail page: board
feet times the species rate, discounted for condition (green stock is marked
down hard, since buyers price drying risk into it).



`SPECIES_PRESETS` in `app.py` holds starting prices and the blurb that goes
into listing text. The numbers are rough North Texas starting points — edit
them freely, and let the Sold report correct you.

## Notes

- Photos are auto-rotated (phones lie about orientation) and downscaled to
  1600px, so the folder stays small.
- This runs on your LAN with no authentication. Don't expose it to the
  internet as-is.
- Back up `lumber.db` and `static/uploads/` — that's your whole dataset.

## License

MIT. See [LICENSE](LICENSE). Copyright (c) 2026 Emmitt Fennessey.
