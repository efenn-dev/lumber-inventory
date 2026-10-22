"""Export a public, shareable inventory page from lumber.db.

Produces a self-contained static site (one HTML file plus a photos folder)
showing only pieces that are actually for sale. Internal fields — location,
buyer, sold price, private notes — are never rendered.

    python export_site.py --contact "Emmitt - 555-0100"
    python export_site.py --out dist --status ready listed
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from jinja2 import Template

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "lumber.db"
UPLOAD_DIR = BASE_DIR / "static" / "uploads"

logger = logging.getLogger(__name__)

PUBLIC_STATUSES = ("ready", "listed")

TEMPLATE = Template("""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ brand }} - Available Lumber</title>
<meta name="description" content="{{ tagline }}">
<style>
  :root{--ink:#2B1D12;--muted:#7a6a58;--paper:#F4EFE7;--card:#fff;--line:#ddd2c2}
  *{box-sizing:border-box}
  body{margin:0;background:var(--paper);color:var(--ink);
       font:16px/1.65 Georgia,'Times New Roman',serif}
  header{padding:44px 20px 28px;text-align:center;border-bottom:1px solid var(--line)}
  h1{margin:0;font-size:38px;letter-spacing:7px;text-transform:uppercase}
  .place{margin:6px 0 0;font-size:13px;letter-spacing:5px;text-transform:uppercase;color:var(--muted)}
  .tagline{max-width:60ch;margin:20px auto 0;color:#5c4d3e}
  main{max-width:1040px;margin:0 auto;padding:28px 20px 60px}
  .count{font-size:13px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);margin:0 0 18px}
  .grid{display:grid;gap:22px;grid-template-columns:repeat(auto-fill,minmax(280px,1fr))}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;
        display:flex;flex-direction:column}
  .photo{aspect-ratio:4/3;background:#eee3d5;overflow:hidden}
  .photo img{width:100%;height:100%;object-fit:cover;display:block}
  .photo.empty{display:grid;place-items:center;color:var(--muted);font-size:13px}
  .body{padding:14px 16px 16px}
  .species{margin:0;font-size:19px}
  .dims{margin:3px 0 10px;color:var(--muted);font-size:15px}
  dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:2px 12px;font-size:14px}
  dt{color:var(--muted)}
  dd{margin:0;text-align:right}
  .price{margin:14px 0 0;padding-top:12px;border-top:1px solid var(--line);
         font-size:21px;font-weight:bold}
  .notes{margin:10px 0 0;font-size:14px;color:#5c4d3e;font-style:italic}
  footer{border-top:1px solid var(--line);padding:30px 20px 60px;text-align:center}
  .contact{font-size:19px;margin:0 0 8px}
  .terms{color:var(--muted);font-size:14px;margin:0}
  .empty-state{text-align:center;color:var(--muted);padding:50px 0}
  @media(prefers-color-scheme:dark){
    :root{--ink:#F0E7DC;--muted:#a8998a;--paper:#14110e;--card:#1e1a16;--line:#3a3128}
    .photo{background:#272119}
  }
</style></head><body>
<header>
  <h1>{{ brand }}</h1>
  <p class="place">{{ place }}</p>
  <p class="tagline">{{ tagline }}</p>
</header>
<main>
{% if pieces %}
  <p class="count">{{ pieces|length }} piece{{ plural }}
     &middot; {{ total_bf }} board feet &middot; updated {{ updated }}</p>
  <div class="grid">
  {% for p in pieces %}
    <article class="card">
      {% if p.photo %}
        <div class="photo"><img src="photos/{{ p.photo }}" alt="{{ p.species }} board" loading="lazy"></div>
      {% else %}
        <div class="photo empty">photo coming</div>
      {% endif %}
      <div class="body">
        <h2 class="species">{{ p.species }}</h2>
        <p class="dims">{{ p.dimensions }}</p>
        <dl>
          <dt>Board feet</dt><dd>{{ p.board_feet }}</dd>
          <dt>Thickness</dt><dd>{{ p.thickness_label }}</dd>
          <dt>Condition</dt><dd>{{ p.condition }}</dd>
        </dl>
        {% if p.notes %}<p class="notes">{{ p.notes }}</p>{% endif %}
        {% if p.price %}<p class="price">${{ p.price }}</p>{% endif %}
      </div>
    </article>
  {% endfor %}
  </div>
{% else %}
  <p class="empty-state">Nothing listed right now - check back, or get in touch.</p>
{% endif %}
</main>
<footer>
  <p class="contact">{{ contact }}</p>
  <p class="terms">Prices are per piece. Cash, local pickup. Custom dimensions available.</p>
</footer>
</body></html>""")


def board_feet(thickness_in: float, width_in: float, length_in: float) -> float:
    """Return board feet. One board foot is 144 cubic inches."""
    return (thickness_in * width_in * length_in) / 144.0


def _trim(value: float) -> str:
    """Render 48.0 as '48' and 1.5 as '1.5'."""
    return f"{value:g}"


def _thickness_label(thickness_in: float) -> str:
    """Render thickness in both quarter and decimal notation."""
    quarters = {1.0: "4/4", 1.25: "5/4", 1.5: "6/4", 2.0: "8/4", 3.0: "12/4"}
    label = quarters.get(round(thickness_in, 2))
    return f'{label} ({_trim(thickness_in)} in)' if label else f'{_trim(thickness_in)} in'


def _condition(row: dict[str, Any]) -> str:
    """Public-facing dryness description. Never overstates."""
    moisture = row.get("moisture_pct")
    if moisture is not None:
        return f"Air dried, ~{_trim(moisture)}% MC"
    if row.get("already_dry"):
        return "Dry, ready to work"
    return "Air drying"


def _dimensions(row: dict[str, Any]) -> str:
    """Human-readable dimensions, showing a width range for live edge."""
    if row.get("width_max_in") and row["width_max_in"] > row["width_in"]:
        width = f'{_trim(row["width_in"])}-{_trim(row["width_max_in"])}'
    else:
        width = _trim(row["width_in"])
    return f'{_trim(row["length_in"])} L x {width} W x {_trim(row["thickness_in"])} T (inches)'


def collect(db_path: Path, statuses: Iterable[str]) -> list[dict[str, Any]]:
    """Read sellable pieces and their lead photo, dropping internal fields."""
    statuses = tuple(statuses)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(statuses))
        rows = conn.execute(
            f"SELECT * FROM pieces WHERE status IN ({placeholders}) "
            "ORDER BY species, length_in DESC",
            statuses,
        ).fetchall()

        pieces: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            photo = conn.execute(
                "SELECT filename FROM photos WHERE piece_id = ? ORDER BY id LIMIT 1",
                (data["id"],),
            ).fetchone()
            pieces.append(
                {
                    "species": data["species"],
                    "dimensions": _dimensions(data),
                    "board_feet": round(
                        board_feet(data["thickness_in"], data["width_in"], data["length_in"]), 2
                    ),
                    "thickness_label": _thickness_label(data["thickness_in"]),
                    "condition": _condition(data),
                    "notes": data.get("notes"),
                    "price": _trim(data["price_asking"]) if data.get("price_asking") else None,
                    "photo": photo["filename"] if photo else None,
                }
            )
    return pieces


def export(out_dir: Path, brand: str, place: str, tagline: str, contact: str,
           statuses: Iterable[str]) -> Path:
    """Write the static site and return the path to index.html."""
    pieces = collect(DB_PATH, statuses)

    photos_dir = out_dir / "photos"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    photos_dir.mkdir(parents=True)

    for piece in pieces:
        if piece["photo"]:
            source = UPLOAD_DIR / piece["photo"]
            if source.exists():
                shutil.copy2(source, photos_dir / piece["photo"])
            else:
                logger.warning("Missing photo file: %s", source)
                piece["photo"] = None

    html = TEMPLATE.render(
        brand=brand,
        place=place,
        tagline=tagline,
        contact=contact,
        pieces=pieces,
        plural="" if len(pieces) == 1 else "s",
        total_bf=round(sum(p["board_feet"] for p in pieces), 1),
        updated=date.today().strftime("%B %d, %Y").replace(" 0", " "),
    )
    index = out_dir / "index.html"
    index.write_text(html, encoding="utf-8")
    logger.info("Exported %d piece(s) to %s", len(pieces), index)
    return index


def main(argv: Iterable[str] | None = None) -> None:
    """Parse arguments and export the public inventory page."""
    parser = argparse.ArgumentParser(description="Export a public inventory page")
    parser.add_argument("--out", type=Path, default=BASE_DIR / "dist", help="output directory")
    parser.add_argument("--brand", default="Fencerow", help="business name")
    parser.add_argument("--place", default="Melissa, Texas", help="location line")
    parser.add_argument(
        "--tagline",
        default="Live edge cedar and pecan, milled by hand from standing-dead timber "
                "on a ranch in Collin County.",
    )
    parser.add_argument("--contact", default="Get in touch for pricing and pickup.")
    parser.add_argument("--status", nargs="+", default=list(PUBLIC_STATUSES),
                        help="which statuses to publish")
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    index = export(args.out, args.brand, args.place, args.tagline, args.contact, args.status)
    print(f"\nOpen: {index}")
    print(f"Upload the whole '{args.out.name}' folder to your web root.")


if __name__ == "__main__":
    main()
