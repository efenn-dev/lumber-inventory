/* Fencerow Field: offline capture queue + sync.
 *
 * Everything captured at the mill lands in IndexedDB first and syncs to the
 * server whenever the phone can reach it -- on load, on regaining signal, or
 * on the Sync button. Sync targets are idempotent (client_uuid on records,
 * timestamp dedupe on readings), so a retry after a dropped connection can
 * never double-book anything.
 */
"use strict";

// ---------------------------------------------------------------- IndexedDB

const DB_NAME = "fencerow-field";
let dbPromise = null;

function idb() {
  if (!dbPromise) {
    dbPromise = new Promise((resolve, reject) => {
      const open = indexedDB.open(DB_NAME, 1);
      open.onupgradeneeded = () => {
        open.result.createObjectStore("queue", { keyPath: "id", autoIncrement: true });
      };
      open.onsuccess = () => resolve(open.result);
      open.onerror = () => reject(open.error);
    });
  }
  return dbPromise;
}

async function queueAdd(item) {
  const db = await idb();
  await new Promise((resolve, reject) => {
    const tx = db.transaction("queue", "readwrite");
    tx.objectStore("queue").add(item);
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
  await renderQueue();
}

async function queueAll() {
  const db = await idb();
  return new Promise((resolve, reject) => {
    const request = db.transaction("queue", "readonly").objectStore("queue").getAll();
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function queueDelete(id) {
  const db = await idb();
  await new Promise((resolve, reject) => {
    const tx = db.transaction("queue", "readwrite");
    tx.objectStore("queue").delete(id);
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
}

// ---------------------------------------------------------------- helpers

const $ = (id) => document.getElementById(id);
const num = (id) => {
  const v = parseFloat($(id).value);
  return Number.isFinite(v) ? v : null;
};

/* Downscale a photo on-device to what the server would do anyway (1600px
 * JPEG). A 4 MB camera frame becomes ~300 KB, which keeps the queue small and
 * a cellular sync fast. */
function shrinkPhoto(file) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      URL.revokeObjectURL(url);
      const max = 1600;
      const scale = Math.min(1, max / Math.max(img.width, img.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.width * scale);
      canvas.height = Math.round(img.height * scale);
      canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
      resolve(canvas.toDataURL("image/jpeg", 0.85));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("unreadable image")); };
    img.src = url;
  });
}

async function collectPhotos(inputId) {
  const files = Array.from($(inputId).files || []);
  const photos = [];
  for (const file of files.slice(0, 10)) {
    try { photos.push({ b64: await shrinkPhoto(file) }); } catch { /* skip junk */ }
  }
  return photos;
}

function token() { return localStorage.getItem("fencerow-token") || ""; }

function apiHeaders() {
  const headers = { "Content-Type": "application/json" };
  if (token()) headers["X-Kiln-Token"] = token();
  return headers;
}

function nowIso() { return new Date().toISOString().slice(0, 19); }

// ---------------------------------------------------------------- log tab

function renderPlan() {
  const d = num("log-small");
  const L = num("log-length");
  const out = $("plan-out");
  if (!d || !L) { out.innerHTML = ""; return; }

  const species = $("log-species").value.trim();
  const kerf = num("log-kerf") ?? logcalc.DEFAULT_KERF_IN;
  const large = num("log-large");
  const choice = $("log-thickness").value;
  const rec = logcalc.recommend(species || null, d, ASSUME_KILN);

  let plan, cutLabel, recHtml = "";
  if (choice === "rec") {
    plan = rec.mixed
      ? logcalc.cutPlan(d, L, rec.jacket, kerf, null, large, rec.center)
      : logcalc.cutPlan(d, L, rec.center, kerf, null, large);
    cutLabel = rec.mixed ? `${rec.center}" center / ${rec.jacket}" jackets` : `${rec.center}" throughout`;
    recHtml = `<p class="alert alert-info"><strong>Recommended for ${species || "this log"}: ${cutLabel}</strong>
      ${ASSUME_KILN ? "" : "(air drying only)"} — ${rec.why}</p>`;
  } else {
    const t = parseFloat(choice);
    plan = logcalc.cutPlan(d, L, t, kerf, null, large);
    cutLabel = `${t}" throughout`;
  }

  const rate = SPECIES_RATES[species];
  let valueHtml = "";
  let compareHtml = "";
  if (rate && plan.yield_bf) {
    const v = logcalc.planValue(plan, rate);
    valueHtml = `<p class="hint">Board value: <strong>$${v.green}</strong> green ·
      <strong>$${v.air_dried}</strong> air dry · <strong>$${v.kiln_dried}</strong> kiln dry
      — what the boards could list for, not what to pay for the log.</p>`;

    const cmp = logcalc.compareOptions(d, L, species || null, ASSUME_KILN, rate, kerf, large);
    compareHtml = `<p class="hint">` + cmp.options.map((o, i) =>
      `${i === cmp.recommended ? "<strong>" : ""}${o.label}: $${o.value_kiln}${i === cmp.recommended ? "</strong>" : ""}`
    ).join(" · ") + ` <small>(kiln-dried)</small></p>`;
  }

  out.innerHTML =
    recHtml +
    `<div class="plan-grid">` +
    `<div>${logcalc.planSvg(plan, d, 220)}</div>` +
    `<div class="totals">` +
    `<div><strong>${plan.yield_bf}</strong><span>plan yield bf</span></div>` +
    `<div><strong>${plan.doyle_bf}</strong><span>Doyle</span></div>` +
    `<div><strong>${plan.international_bf}</strong><span>Intl &frac14;"</span></div>` +
    `</div></div>` +
    `<p class="hint">${plan.boards.length} boards — ${cutLabel}` +
    (plan.boards.length ? `, widest ${plan.boards[0].width_in}"` : "") + `</p>` +
    compareHtml +
    valueHtml +
    plan.advice.map((a) => `<p class="alert alert-info">${a}</p>`).join("");
}

["log-small", "log-large", "log-length", "log-thickness", "log-kerf", "log-species"].forEach((id) =>
  $(id).addEventListener("input", renderPlan)
);

$("log-save").addEventListener("click", async () => {
  const payload = {
    client_uuid: crypto.randomUUID(),
    species: $("log-species").value.trim() || null,
    small_diameter_in: num("log-small"),
    large_diameter_in: num("log-large"),
    length_ft: num("log-length"),
    location: $("log-location").value.trim() || null,
    notes: $("log-notes").value.trim() || null,
    photos: await collectPhotos("log-photos"),
  };
  if (!payload.small_diameter_in || !payload.length_ft) {
    alert("Need at least a small-end diameter and a length."); return;
  }
  await queueAdd({ kind: "log", label: `Log ${payload.species || "?"} ${payload.small_diameter_in}"×${payload.length_ft}'`, payload, created: nowIso() });
  ["log-species", "log-small", "log-large", "log-length", "log-location", "log-notes", "log-photos"].forEach((id) => ($(id).value = ""));
  renderPlan();
  syncAll();
});

// ---------------------------------------------------------------- board tab

function renderBoardBf() {
  const t = num("board-thickness"), w = num("board-width"), L = num("board-length");
  $("board-bf").textContent = t && w && L ? ((t * w * L) / 144).toFixed(2) : "—";
}
["board-thickness", "board-width", "board-length"].forEach((id) =>
  $(id).addEventListener("input", renderBoardBf)
);

$("board-save").addEventListener("click", async () => {
  const payload = {
    client_uuid: crypto.randomUUID(),
    species: $("board-species").value.trim() || "Unknown",
    length_in: num("board-length"),
    width_in: num("board-width"),
    width_max_in: num("board-widthmax"),
    thickness_in: num("board-thickness"),
    status: $("board-status").value,
    dry_method: $("board-method").value,
    notes: $("board-notes").value.trim() || null,
    mill_date: nowIso(),
    photos: await collectPhotos("board-photos"),
  };
  if (!payload.length_in || !payload.width_in) { alert("Need a length and width."); return; }
  await queueAdd({ kind: "piece", label: `Board ${payload.species} ${payload.length_in}"L`, payload, created: nowIso() });
  ["board-species", "board-length", "board-width", "board-widthmax", "board-notes", "board-photos"].forEach((id) => ($(id).value = ""));
  renderBoardBf();
  syncAll();
});

// ---------------------------------------------------------------- kiln tab

$("k-save").addEventListener("click", async () => {
  const reading = { recorded_at: nowIso(), temp_f: num("k-temp"), rh_pct: num("k-rh"), wood_mc_pct: num("k-mc") };
  if (reading.temp_f == null || reading.rh_pct == null) { alert("Need temperature and humidity."); return; }
  await queueAdd({ kind: "reading", label: `Reading ${reading.temp_f}°F ${reading.rh_pct}%`, payload: reading, created: nowIso() });
  ["k-temp", "k-rh", "k-mc"].forEach((id) => ($(id).value = ""));
  syncAll();
});

$("k-import").addEventListener("click", async () => {
  const url = $("k-device-url").value.trim();
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(15000) });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const text = await response.text();
    await queueCsv(text, "device");
  } catch (err) {
    alert(`Could not reach the device: ${err.message}. Are you on its wifi?`);
  }
});

$("k-csv-file").addEventListener("change", async () => {
  const file = $("k-csv-file").files[0];
  if (file) { await queueCsv(await file.text(), "sd-card"); $("k-csv-file").value = ""; }
});

async function queueCsv(text, source) {
  const lines = text.trim().split("\n").length - 1;
  await queueAdd({ kind: "csv", label: `Logger CSV — ~${lines} readings (${source})`, payload: { text, source }, created: nowIso() });
  syncAll();
}

// ---------------------------------------------------------------- sync

let syncing = false;

async function syncAll() {
  if (syncing || !navigator.onLine) { renderQueue(); return; }
  syncing = true;
  const status = $("sync-status");
  try {
    const items = await queueAll();
    if (!items.length) { status.textContent = ""; return; }
    status.textContent = "Syncing…";

    // Readings batch into one idempotent call; everything else goes item by item.
    const readings = items.filter((i) => i.kind === "reading");
    if (readings.length) {
      const response = await fetch("/api/kiln/readings", {
        method: "POST", headers: apiHeaders(),
        body: JSON.stringify({ source: "field-app", readings: readings.map((r) => r.payload) }),
      });
      if (response.ok) for (const r of readings) await queueDelete(r.id);
    }

    for (const item of items.filter((i) => i.kind !== "reading")) {
      let ok = false;
      if (item.kind === "csv") {
        const form = new FormData();
        form.append("csv", new Blob([item.payload.text], { type: "text/csv" }), "logger.csv");
        form.append("source", item.payload.source);
        const response = await fetch("/kiln/import", { method: "POST", body: form });
        ok = response.ok;
      } else {
        const endpoint = item.kind === "log" ? "/api/logs" : "/api/pieces";
        const response = await fetch(endpoint, {
          method: "POST", headers: apiHeaders(), body: JSON.stringify(item.payload),
        });
        ok = response.ok;
      }
      if (ok) await queueDelete(item.id);
    }

    const left = (await queueAll()).length;
    status.textContent = left ? `${left} item(s) could not sync — will retry.` : "All synced.";
  } catch (err) {
    status.textContent = `Sync failed (${err.message}) — items are safe in the queue.`;
  } finally {
    syncing = false;
    renderQueue();
  }
}

async function renderQueue() {
  const items = await queueAll();
  $("queue-count").textContent = items.length ? `(${items.length})` : "";
  $("queue-list").innerHTML = items.length
    ? items.map((i) => `<li>${i.label} <span class="hint">${i.created.replace("T", " ")}${(i.payload.photos || []).length ? ` · ${i.payload.photos.length} photo(s)` : ""}</span></li>`).join("")
    : '<li class="hint">Queue is empty — everything is on the server.</li>';
}

// ---------------------------------------------------------------- chrome

document.querySelectorAll(".field-tabs button").forEach((button) =>
  button.addEventListener("click", () => {
    document.querySelectorAll(".field-tabs button, .tab").forEach((el) => el.classList.remove("on"));
    button.classList.add("on");
    $("tab-" + button.dataset.tab).classList.add("on");
  })
);

function renderNet() {
  const pill = $("net-pill");
  pill.textContent = navigator.onLine ? "online" : "offline — queueing";
  pill.className = "pill " + (navigator.onLine ? "pill-ok" : "pill-off");
}
window.addEventListener("online", () => { renderNet(); syncAll(); });
window.addEventListener("offline", renderNet);

$("sync-now").addEventListener("click", syncAll);
$("set-token").value = token();
$("set-token").addEventListener("change", () => localStorage.setItem("fencerow-token", $("set-token").value.trim()));
$("server-origin").textContent = location.origin;

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch((err) => console.warn("SW registration failed:", err));
}

renderNet();
renderQueue();
syncAll();
