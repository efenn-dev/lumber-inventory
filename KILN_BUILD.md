# Solar kiln — build list, two sizes

One Virginia Tech-style drawing at two lengths:

- **8 ft backyard box** — 4 × 8 ft, 89 in inside, boards to 7 ft 4 in, ~330 bf of 6/4 per charge. Fits a 5×8 trailer. **≈ $1,350 all-new, ≈ $1,050 with the swaps.**
- **12 ft mill kiln** — 4 × 12 ft, 137 in inside, takes any slab the HM122 can cut (it tops out at 10 ft 3 in), ~515 bf of 6/4 per charge. Fits a 6×12 trailer. **≈ $1,900 all-new, ≈ $1,500 with the swaps.**

Everything north–south is identical: 4 ft deep, 36 in south wall, 72 in north wall,
37° roof, the same end walls, the same door, the same fans and power. Only the
east–west pieces change — plates, rims, purlins, how many rafters, joists and doors,
and the sheet count. Build the 8 ft one first; the 12 ft one is the same job with more
of the same cuts. Companion to `HARDWARE.md` (the sensor) and the `/kiln` page in the
app (the drying model). Written 2026-09-11.

---

## Why these dimensions

| Choice | 8 ft | 12 ft | Reason |
|---|---|---|---|
| Outside | 96 × 48 in | 144 × 48 in | Both lengths land on 4×8 sheet goods with no strips: full sheets at 8 ft, full and half sheets at 12 ft. |
| Inside | 89 × 41 in | 137 × 41 in | 8 ft: one 4-ft board plus a piece up to 40 in behind it per row, boards to 7 ft 4 in. 12 ft: a 10-ft slab with 15 in to spare, or rows of 8 + 3 or 4 + 4 + 3 ft. 12 ft was chosen over the 11-ft minimum because plates, rims and skids come as 12-ft stock and the third door is the same door — the last foot costs about $100. |
| Depth, inside | 41 in | 41 in | 8 in fan plenum (north) + 30 in stack + 3 in return gap (south). Air crosses the stack the short way, so north and south boards dry alike. |
| South / north wall | 36 / 72 in | 36 / 72 in | Roof rises 36 over a 48 run: a **3-4-5 triangle**. Rafters are exactly 60 in plus overhang, pitch is 37°, right for 33° N. **Set the speed square to 37 for every angled cut in the build.** |
| Stack, max | 30 × 89 × 27 in | 30 × 137 × 27 in | 8 ft: ≈ 330 bf of 6/4 · 370 of 8/4 · 280 of 4/4. 12 ft: ≈ 515 · 570 · 430. |
| Collector over the box | ~40 sq ft | ~60 sq ft | Near VT's rule of 1 sq ft per 10 bf. |
| Doors | 2 | 3 | Same 45-1/4 × 58 door in both. |
| Fans | 6 | 8 | 120 mm industrial, solar-direct. |
| Weight, whole | ~520 lb | ~900 lb | Both come apart into panels. The 12 ft deck is two bolted halves so no single piece is over ~200 lb. |
| Trailer | 5×8, 5×9, 6×12 | 6×12 | Roof off. The 12 ft box is exactly 12 ft. |

## How it works

Sun comes through the sloped polycarbonate and heats the **black lid** on the stack
and the **fan shelf** beside it. The 12 V fans set in that shelf pull the hot air and
push it **down the 8 in plenum** behind the stack, **through the sticker gaps** toward
the south, **up the 3 in south gap**, and back across under the glazing. Foam strips
at the ends of the stack block the bypass. Fresh air enters through two **intake
vents in the north band** (fan suction side); wet air leaves through an **exhaust
vent low in each door** (fan pressure side). Fans run **solar-direct** — sun up,
fans on; no battery, no timer. That is exactly why the sensor gets its own battery
(`HARDWARE.md`).

```
   SOUTH (sun side)                                          NORTH (doors)
                                                    ___ intake vent (band)
                                             ___---   |
                                      ___---  glazing  |  72 in
                               ___---                  |
   36 in |__---     collector zone  (hot air ->)   [fans] shelf
         |  ^  ________________________________   |  |
         |  |  |  black lid on stack            |  |  v   8 in plenum
   3 in  |  |  | boards  <---- air ----  stickers|  |  |
   gap   |  |  | boards  <---- air ----          |  |  |   exhaust vent
         |  s  | boards  <---- air ----          |  |  |   (low in door)
         |_____|__bolsters 4x4_________________ |__|__|
      s = sensor lead, mid-stack, in the south gap, shaded
```

---

## Shopping list

Big-box ballparks, September 2026, ±20 %. Check the shed at the property first —
a table saw and old supplies are there, unverified. A dash means that size does not
use the item.

### Framing lumber — ≈ $315 (8 ft) · ≈ $470 (12 ft)

| 8 ft | 12 ft | Item | Use | ≈ $ (8 / 12) |
|---:|---:|---|---|---:|
| 33 | 39 | 2×4 × 8 ft SPF stud | studs, rafters, doors, end walls; 2 spare for mis-cuts | 130 / 155 |
| 4 | — | 2×4 × 10 ft SPF | purlins, 100 in | 22 / — |
| — | 4 | 2×4 × 12 ft SPF | wall plates, 144 in | — / 28 |
| — | 4 | 2×4 × 14 ft SPF | purlins, 148 in | — / 36 |
| 6 | 6 | 2×4 × 8 ft **pressure-treated** | 8 ft: 2 rims + 7 joists · 12 ft: 12 joists | 33 / 33 |
| — | 2 | 2×4 × 12 ft pressure-treated | four 72 in rims | — / 16 |
| 5 | 3 | 4×4 × 8 ft pressure-treated | 8 ft: 3 skids + 2 for bolsters · 12 ft: nine 30 in bolsters | 70 / 42 |
| — | 3 | 4×4 × 12 ft pressure-treated | skids | — / 75 |
| 3 | 4 | 1×4 × 8 ft | two rake trims, one astragal per door | 15 / 20 |
| 23 | 34 | 1×2 × 8 ft **kiln-dried** furring | stickers (30 in) + door stops. Dry, all the same thickness — green furring stains pecan. | 45 / 65 |

### Sheet goods — ≈ $360 · ≈ $550

| 8 ft | 12 ft | Item | Use | ≈ $ (8 / 12) |
|---:|---:|---|---|---:|
| 7 | 10 | 7/16 in OSB 4×8 | sheathing, door skins, floor bottom, plus one spare that becomes the fan shelf and vent sliders | 110 / 160 |
| 1 | 2 | 3/4 in plywood 4×8 (BCX) | floor: one sheet at 8 ft, two 72×48 halves at 12 ft | 50 / 100 |
| 1 | 2 | 1/4 in plywood 4×8 | stack lid, vent covers | 22 / 44 |
| 5 | 7 | **Rmax Pro Select R-Matte Plus-3** 4×8, foil-faced polyiso — 1 in R-6 (model 637900, ~$34, stocked at the Anna Home Depot); 1.5 or 2 in if the store can get it | walls, doors, floor. Foil faces in and stays exposed — it is the vapor barrier and the radiant barrier. The facer is what matters, not the R: anything from 1 to 2 in fits the bay. **Not** the Henry "Single-Ply Roofing" polyiso (HE1622436): Home Depot lists it as unfaced, it is the paper-faced sheet that goes under a rubber roof and it wicks. Not XPS (pink/blue, no foil) or EPS either — both soften at 165 °F. | 175 / 245 |

### Roof — ≈ $145 · ≈ $225

| 8 ft | 12 ft | Item | Use | ≈ $ (8 / 12) |
|---:|---:|---|---|---:|
| 2 | 3 | **Clear polycarbonate** corrugated panel 26 in × 12 ft (Suntuf / Tuftex PolyCarb) | each cut in half → 72 in pieces. **Not PVC** (Palruf softens at kiln temps), **not fiberglass** (yellows in 3 years). | 100 / 150 |
| 3 pk | 4 pk | foam closure strips, 24 in, matching profile | under the panels at the top and bottom purlins | 20 / 27 |
| 1 bag | 2 bags | polycarbonate roofing screws w/ EPDM washers, 100 ct | | 14 / 28 |
| 1 | 2 | aluminum drip edge 10 ft | caps the top (north) edge of the glazing | 10 / 20 |

### Doors, vents, sealing, paint — ≈ $240 · ≈ $350

| 8 ft | 12 ft | Item | Use | ≈ $ (8 / 12) |
|---:|---:|---|---|---:|
| 6 | 9 | heavy T-hinge 10 in (or gate strap hinge) | 3 per door; each door is ~50 lb | 48 / 72 |
| 2 + 2 | 3 + 3 | hasp + gate handle | one per door | 18 / 27 |
| 2 | 2 | 3/8 × 6 in eye bolt w/ nut + washers | through the skids, for dragging | 6 / 6 |
| — | 4 | 3/8 × 4 in carriage bolt w/ nut + washers | joins the two deck halves | — / 8 |
| 2 rolls | 3 rolls | D-profile EPDM weatherstrip, self-adhesive | door stops | 24 / 36 |
| 1 roll | 1 roll | 1/4 in hardware cloth 24 in × 5 ft | over the vent openings (wasps) | 12 / 12 |
| 1 | 1 | 1/2 in cable gland | sensor lead through the south wall | 3 / 3 |
| 4 | 6 | exterior polyurethane caulk | every seam | 25 / 38 |
| 2 cans | 3 cans | Great Stuff spray foam | eave wedge gaps, corners | 12 / 18 |
| 1 roll | 1 roll | foil tape 2 in | polyiso seams | 12 / 12 |
| 1 gal | 1 gal | exterior **flat black** latex | lid, fan shelf, exposed studs in the collector zone | 40 / 40 |
| 1 gal | 2 gal | exterior paint + primer (any light color) | outside of the OSB, 2 coats. Bare OSB is dead in two years. | 40 / 80 |

### Fasteners — ≈ $70 either size

| Qty | Item | ≈ $ |
|---:|---|---:|
| 5 lb | #9 × 3 in exterior construction screws (GRK / Deckmate) — every joint, so it comes apart. About 350 in the box; the 12 ft build uses nearly all of them. | 35 |
| 5 lb | #8 × 1-5/8 in exterior screws — sheathing, lid, shelf | 30 |
| 1 pk | M4 × 40 mm machine screws + nuts (fans) | 6 |

### Fans and power, solar-direct — ≈ $215 · ≈ $250

| 8 ft | 12 ft | Item | Notes | ≈ $ (8 / 12) |
|---:|---:|---|---|---:|
| 6 | 8 | **120 × 38 mm 12 V DC industrial fan, ball bearing, rated 70 °C** (Delta / Sunon / NMB) | Check the datasheet for the 70 °C rating. Computer case fans (Arctic, Noctua) are rated 40 °C ambient and die in a season. ~100 cfm each. | 80 / 105 |
| 1 | 1 | 100 W 12 V mono solar panel | on a 2×4 A-frame beside the south wall, tilted ~30°, faces south; separate from the box so it moves easily | 70 / 70 |
| 1 | 1 | DC-DC buck converter, 8–40 V in → 12 V out, 10 A, adjustable | **Required.** A bare panel puts out 18–21 V in light load and cooks 12 V fans. Set to 12.0 V. Lives in a vented box in the shade under the north eave, never inside the kiln. | 12 / 12 |
| 1 | 1 | inline blade fuse holder + 10 A fuses | on the panel positive | 6 / 6 |
| 25 ft | 50 ft | 16 AWG red/black zip wire, silicone-jacketed for the run inside the box | | 12 / 20 |
| 1 | 1 | MC4 pigtail pair | | 8 / 8 |
| 1 | 1 | small weatherproof junction box + Wago connectors | | 16 / 16 |
| 1 | 1 | $12 min/max thermo-hygrometer with a wired probe | **day-one monitor** until the ESP32 logger exists; type the readings into the kiln page by hand | 12 / 12 |

Pin moisture meter and the ESP32 logger are on `HARDWARE.md`, not repeated here.

### Tools you need beyond the circular saw and sander

Drill/driver (impact ideal — same DeWalt batteries), speed square, 25 ft tape,
4 ft level, chalk line, two 24 in bar clamps, caulk gun, utility knife, tin snips,
**keyhole saw or jigsaw** (the fan holes in the shelf), a **40–60 tooth 7-1/4 in
blade** (cuts polycarbonate and plywood clean), two sawhorses, safety glasses and
ear protection; a come-along for moving the 12 ft box. Roughly $150–180 if you own
none of it.

---

## Cut list

All 2×4 unless noted. **Frame first, then cut sheathing to what you actually
built** — the sheet sizes below are nominal.

### Floor deck, pressure-treated

At 8 ft the deck is one piece, 96 × 48. At 12 ft it is two identical 72 × 48 halves,
each with its own rim all round, bolted together through the meeting rims — each
half is about 190 lb, which two people can carry; the whole deck is not.

| Piece | 8 ft | 12 ft |
|---|---|---|
| rim (E–W) | 2 × 96 | 4 × 72 |
| joist (N–S), 16 in on center | 7 × 45 | 12 × 45 (6 per half, last bay 8 in) |
| 4×4 skid, under both rims and the center, ends beveled 45° | 3 × 96 | 3 × 144 |
| bottom OSB | 1 × 96×48 | 2 × 72×48 |
| polyiso between joists | 6 × 45 × 14-1/2 | 8 × 45 × 14-1/2 + 2 × 45 × 6-1/2 |
| 3/4 in plywood floor | 1 × 96×48 | 2 × 72×48 |
| 3/8 × 4 in carriage bolts through the meeting rims | — | 4 |

### South wall, 36 tall

| Piece | 8 ft | 12 ft |
|---|---|---|
| plate | 2 × 96 | 2 × 144 (12 ft stock) |
| stud, 24 in on center | 5 × 33 | 7 × 33 |
| OSB, laps 4-1/2 in down over the deck edge | 96 × 46-3/4 | 144 × 46-3/4 (96 + 48, seam on the stud at 96) |

### North wall, 72 tall

8 ft: end stud · opening 45-3/4 · center stud · opening 45-3/4 · end stud.
12 ft: end stud · opening 46 · stud · opening 46 · stud · opening 46 · end stud.

| Piece | 8 ft | 12 ft |
|---|---|---|
| plate | 2 × 96 | 2 × 144 (12 ft stock) |
| stud | 3 × 69 | 4 × 69 |
| header, 2×4 flat, bottom edge 60 in above the deck | 2 × 45-3/4 | 3 × 46 |
| OSB band, 60 in up to the glazing; seams land on studs | 96 × 18-1/4 (48 + 48) | 144 × 18-1/4 (48 × 3) |

### Doors — 45-1/4 wide × 58 tall, 2×4 on edge, same door in both sizes

Two doors at 8 ft, three at 12 ft. Per door:

| Qty | Piece | Length |
|---:|---|---|
| 2 | rail | 45-1/4 |
| 2 | stile | 55 |
| 1 | mid rail | 42-1/4 |
| 1 | OSB skin, outside only | 45-1/4 × 58 |
| 2 | polyiso, foil facing in | 42-1/4 × 26-3/4 |
| — | 1×2 door stops on the inside edges of the opening, weatherstrip on the stops | to fit |

Foam sits against the outer skin; the inside face is bare foil. Cut a 4 × 8 in
exhaust vent through each door 12 in above the bottom, frame it in 1×2, sliding
1/4 in ply cover, hardware cloth.

### End walls — two, fit between the north and south walls (41 in), sloped top, identical in both sizes

Angled cuts are all 37°. Stud lengths are to the **long point**; the short point is
2-5/8 in less.

| Qty | Piece | Length |
|---:|---|---|
| 2 | bottom plate | 41 |
| 2 | south stud, top cut 37° | 37-7/8 |
| 2 | middle stud (centered), top cut 37° | 51-15/16 |
| 2 | north stud, top cut 37° | 66 |
| 2 | sloped top plate, both ends cut 37° so they stand plumb | 51-5/16 |
| 2 | OSB trapezoid, 48 wide; runs up past the rafter to touch the glazing and 4-1/2 in down over the deck | 46-3/4 at the south edge → 82-3/4 at the north edge |

### Roof — one ladder, built flat, lifted on

| Piece | 8 ft | 12 ft |
|---|---|---|
| rafter, 24 in on center, square ends (6 in south overhang, 3 in north) | 5 × 69 | 7 × 69 |
| eave block between rafters, over the north and south plates, flush with the outside of the wall | 8 × 22-1/2 | 12 × 22-1/2 |
| purlin, 2×4 **flat** on the rafters at 1, 24, 47 and 68 in from the north end; 2 in overhang each side | 4 × 100 (10 ft stock) | 4 × 148 (14 ft stock) |
| polycarbonate | 4 × 26 × 72 | 6 × 26 × 72 |
| 1×4 rake trim over the east and west glazing edges | 2 × 72 | 2 × 72 |

Everything is square except the end-wall pieces. No birdsmouths: the end rafters
sit flat on the sloped end-wall plates, and the wedge-shaped gap under the eave
blocks along the north and south walls is filled with spray foam and hidden by the
sheathing. That makes one continuous seal along each eave instead of a row of
fitted blocks.

### Inside

| Piece | 8 ft | 12 ft |
|---|---|---|
| stack lid, 1/4 in ply, flat black both sides | 30 × 89 | 30 × 137 (96 + 41) |
| fan shelf, OSB from the spare sheet, flat black, 4-3/4 in square holes cut with the fans in hand | 9 × 89 (48 + 41), 6 holes 14 in apart | 9 × 137 (96 + 41), 8 holes 17 in apart |
| bolsters, 4×4 × 30, one spare | 6 (five at 0-22-44-66-88) | 8 (seven at 0-23-46-68-91-114-137) |
| stickers, 1×2 × 30, directly above the bolsters | 60 (5 per layer) | 84 (7 per layer) |
| end baffles: polyiso offcuts wedged between the stack ends and the end walls | to fit | to fit |

---

## Build order

Three weekends solo for the 8 ft box, four for the 12 ft. Get a second person for
steps 4 through 6.

1. **Site.** A spot with no shade on the south side from 9 to 4, all year — in December the noon sun here is only 33° up, so a 6 ft fence 10 ft south of the box shades the glazing. Concrete pavers, leveled, under the three skids (six at 8 ft, nine at 12 ft). Doors face north, where you will stand, with room to swing them.
2. **Deck.** Screw the rims to the joists, flip, screw on the bottom OSB, flip back, drop in the foam, screw down the 3/4 in floor. At 12 ft build both halves the same, set them end to end on the skids and bolt the meeting rims together with the four carriage bolts. Screw the deck down to the skids. Eye bolts through one end of the outer skids.
3. **South wall.** Build it flat on the deck, sheathe it (OSB hangs 4-1/2 in below the plate), stand it on the south edge, screw through the plate into the rims every 16 in, brace it plumb with a scrap 2×4.
4. **North wall.** Frame it (studs, headers), stand it, screw it down. Sheathe only the band. Leave the openings empty for now.
5. **End walls.** Frame both with the 37° cuts, stand them between the long walls, screw through the long walls' end studs into the end-wall studs (6 per corner) and down through the bottom plate. Sheathe with the trapezoids — or wait until the roof is on and scribe the sheet against the rafter line, which is safer for a first build.
6. **Roof ladder.** On sawhorses: rafters, eave blocks, purlins. Two people lift it on; walk it up from the low south side. Toe-screw the end rafters into the sloped plates (four each), every rafter and eave block into the north and south plates (two each). Fill the wedge gaps with spray foam from inside.
7. **Glazing.** Fine-tooth blade, cut each 12 ft panel in half. Foam closures on the top and bottom purlins. Start at the west end, overlap each panel one rib over the previous. Pre-drill 1/4 in holes; screws at every second rib on the top and bottom purlins, every third on the middle two, never overtightened. Drip edge over the top edge, rake trim over the sides, caulk everything.
8. **Insulate.** Polyiso pressed into every bay against the OSB, foil facing in, seams and the stud lines taped. Floor is already done.
9. **Paint.** Outside: two coats, including the deck edge. Inside: flat black on the lid, the shelf, and the stud faces in the collector zone (above 31 in on the north and end walls).
10. **Doors.** Frame, skin, foam. Stops around the openings, weatherstrip on the stops, hang on three hinges each, a hasp per door to its 1×4 astragal. Cut the exhaust vents in the doors and the two 4 × 8 in intake vents in the north band, sliding covers, hardware cloth over every vent.
11. **Fans and power.** Fans in the shelf blowing **down**. Panel → fuse → buck (set 12.0 V with a meter before connecting fans) → fans in parallel, silicone wire through a gland. A-frame for the panel. Test at noon: every fan spinning, air moving out of the exhaust vents.
12. **Load.** Bolsters at 22 in centers (23 at 12 ft), first row of boards 1 in apart, stickers straight above the bolsters, repeat. Lid on top, shelf against the north edge of the lid, foam baffles at the ends. Cable gland and the sensor lead into the south gap at mid-stack height, shaded. Close the doors. Open the vents a quarter.

## First charge

Run the **dry cedar first** (it is already ~10 %). Nothing can go wrong with it,
it proves the box against the kiln page, it hits the 133 °F / 30 min sterilization
hold the listing generator needs before it will write "heat sterilized," and it
sells as kiln-dried. Pecan goes in only when the pin meter reads 25 % or below;
green stock case-hardens in a solar kiln.

## Moving it to the mill

Every joint is a 3 in screw; nothing is glued or nailed. Roof off (two people;
~110 lb glazed at 8 ft, ~140 lb at 12 ft — pull the glazing first and it is 90 lb),
doors off (hinge screws), shelf and lid out, then either

- **whole** — ~400 lb at 8 ft, ~600 lb at 12 ft without roof and doors — winched up the trailer ramp by the eye bolts with a come-along, or
- **in panels** — back out the corner and plate screws (and the four carriage bolts at 12 ft) and it is a deck (one piece ~200 lb, or two halves ~190 lb each), a north wall frame (~75 lb without its doors), a south wall (~45–70), two ends (~45 each), the doors (~50 each), skids and the roof. Two people and a pickup; two trips for the 12 ft.

Re-level the skids on pavers at the mill, re-bolt the deck, re-screw, re-foam the
eaves, re-caulk.

## Cheap-path swaps

| Swap | Saves (8 / 12) | Cost |
|---|---:|---|
| 1 in polyiso (R-6.5) instead of 1.5 in | $65 / $90 | slower nights in winter |
| fewer fans + 50 W panel | $50 / $50 | slower; add fans later if a charge stalls |
| 23/32 in OSB subfloor instead of 3/4 in plywood | $20 / $45 | swells at the edges if it stays wet |
| 6 in T-hinges instead of 10 in | $25 / $35 | fine if you do not slam them |
| hinges, paint, hardware from Habitat ReStore | $50–100 | a trip |
| used polycarbonate panels off Marketplace (greenhouse teardowns) | $60+ / $100+ | check they are polycarbonate, not PVC |

## From the property instead of the store

The frame can come out of the mill. Only **dry** wood goes into it — standing-dead
cedar is fine as it is; anything from a living tree needs a summer stickered under
cover first, or it shrinks after assembly and opens every seam.

| Still bought at the store | Can be milled from dry cedar or found on the property |
|---|---|
| Polycarbonate glazing, closures, roofing screws, drip edge — unless tempered sliding-door panels turn up: free glazing, same R-1, lay the purlins out to the panel size; hail is the only reason polycarbonate is on the list | Skids (4×4 or 6×6 heart cedar), bolsters (4×4 cedar), **stickers** (3/4 × 1-1/2 ripped from dry cedar on the shed table saw) |
| Foil-faced polyiso | Studs, plates, rafters, purlins, eave blocks, door frames, 1×4 trim — cedar 2×4 if it is dry |
| Fans, panel, buck converter, fuse, wire | Sheathing, floor plywood, paint, screws, caulk — whatever leftovers are in the shed |
| Hinges, hasps, handles, weatherstrip, hardware cloth, caulk, spray foam, foil tape | |

What that does to the number: framing, skids and stickers are about $470 of the 12 ft
list and $315 of the 8 ft. Must-buy with nothing on hand is about **$1,000 for the
12 ft, $770 for the 8 ft**; sheathing and paint add $400 / $260 if the shed has none.

## Upgrades that can wait

- **Double glazing.** Staple 6-mil UV greenhouse film to the underside of the rafters (~$20–30). Do it only if the kiln page shows the nights giving back what the days gain.
- **Plywood lining** over the foil in the drying zone, 1/4 in painted black (~$120–180). Only if the foil gets torn up by loading.
- **The ESP32 logger** from `HARDWARE.md`, then cellular.
