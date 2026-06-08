// Real Estate Analyzer dashboard frontend
// SSE consumer + history/favorites/quota handling

const api = {
  async me() { return fetch("/api/me", {credentials: "include"}).then(r => r.json()); },
  async logout() { return fetch("/api/logout", {method: "POST", credentials: "include"}); },
  async history() { return fetch("/api/history", {credentials: "include"}).then(r => r.json()); },
  async historyOne(slug) { return fetch(`/api/history/${slug}`, {credentials: "include"}).then(r => r.json()); },
  async favorites() { return fetch("/api/favorites", {credentials: "include"}).then(r => r.json()); },
  async favProp(zpid, on, citySlug = null) {
    const qs = citySlug ? `?city_slug=${encodeURIComponent(citySlug)}` : "";
    return fetch(`/api/favorites/properties/${zpid}${qs}`, {
      method: on ? "POST" : "DELETE", credentials: "include",
    }).then(r => r.json());
  },
  async favCity(slug, on) {
    return fetch(`/api/favorites/cities/${slug}`, {
      method: on ? "POST" : "DELETE", credentials: "include",
    }).then(r => r.json());
  },
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// in-memory: current view + favorites set
let favorites = {properties: new Set(), cities: new Set()};
let currentCity = null;
let currentSlug = null;

// --- auth gate ---
async function ensureAuth() {
  const r = await api.me();
  if (!r.authenticated) { window.location = "/login"; return false; }
  renderUserChip(r);
  return true;
}

// Render quota chip + admin link in the header.
// `me` shape: { authenticated, email, is_admin, daily_cap, runs_today, remaining }
// remaining === null means unlimited (admin).
function renderUserChip(me) {
  const chip = document.getElementById("quota-chip");
  const adminLink = document.getElementById("admin-link");
  if (!chip) return;

  if (me.is_admin) {
    adminLink && (adminLink.hidden = false);
  } else {
    adminLink && (adminLink.hidden = true);
  }

  if (me.remaining === null) {
    // admin — show identity but no cap
    chip.classList.remove("warn", "empty");
    chip.innerHTML = `∞ <span class="quota-email">${me.email}</span>`;
  } else {
    const left = me.remaining;
    chip.classList.remove("warn", "empty");
    if (left === 0) chip.classList.add("empty");
    else if (left <= 1) chip.classList.add("warn");
    chip.innerHTML =
      `${left}/${me.daily_cap} runs left today ` +
      `<span class="quota-email">· ${me.email}</span>`;
  }
  chip.hidden = false;
}

async function refreshQuotaChip() {
  try {
    const r = await api.me();
    if (r.authenticated) renderUserChip(r);
  } catch (_) { /* non-fatal */ }
}

// --- helpers ---
function fmtMoney(n) {
  if (n == null) return "—";
  const sign = n < 0 ? "-$" : "$";
  return sign + Math.abs(Math.round(n)).toLocaleString();
}
function fmtNum(n) { return n == null ? "—" : Math.round(n).toLocaleString(); }
function fmtPct(n) { return n == null ? "—" : `${n}%`; }
function timeAgo(iso) {
  if (!iso) return "";
  let s = iso;
  // Normalize common SQLite format 'YYYY-MM-DD HH:MM:SS' to ISO UTC
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(s)) {
    s = s.replace(' ', 'T') + 'Z';
  }
  const t = new Date(s);
  const dt = (Date.now() - t.getTime()) / 1000;
  if (isNaN(dt)) return "";
  if (dt < 0) return `0s ago`;
  if (dt < 60) return `${Math.floor(dt)}s ago`;
  if (dt < 3600) return `${Math.floor(dt/60)}m ago`;
  if (dt < 86400) return `${Math.floor(dt/3600)}h ago`;
  return `${Math.floor(dt/86400)}d ago`;
}
function setStatus(msg) { $("#status-line").textContent = msg || ""; }
function showQuota(text) {
  const el = $("#quota-banner-text");
  // linkify brightdata.com/cp so the admin can click straight through
  el.innerHTML = text.replace(
    /(brightdata\.com\/cp)/g,
    '<a href="https://$1" target="_blank" rel="noopener" style="color:inherit;text-decoration:underline;">$1</a>'
  );
  $("#quota-banner").hidden = false;
}
function hideQuota() { $("#quota-banner").hidden = true; }

// --- carousel ---
// Each card's photo div stores _photos (array) and _idx (current index) directly on the element.

function photoUrl(p) {
  // photos array may hold {url, width} objects or plain url strings
  return (p && typeof p === 'object') ? (p.url || '') : (p || '');
}

function setCarouselFrame(photoDiv) {
  const photos = photoDiv._photos || [];
  if (!photos.length) return;
  const idx = photoDiv._idx || 0;
  const url = photoUrl(photos[idx]);
  if (url) photoDiv.style.backgroundImage = `url('${url}')`;
  photoDiv.classList.remove("placeholder");

  const prev = photoDiv.querySelector(".carousel-prev");
  const next = photoDiv.querySelector(".carousel-next");
  const counter = photoDiv.querySelector(".carousel-counter");
  if (photos.length > 1) {
    if (prev) prev.hidden = false;
    if (next) next.hidden = false;
    if (counter) { counter.hidden = false; counter.textContent = `${idx + 1} / ${photos.length}`; }
  } else {
    if (prev) prev.hidden = true;
    if (next) next.hidden = true;
    if (counter) counter.hidden = true;
  }
}

// Global delegated handler for carousel arrows (attached once at bottom of file)
function _handleCarouselClick(e) {
  const btn = e.target.closest(".carousel-btn");
  if (!btn) return;
  e.stopPropagation();
  e.preventDefault();
  const photoDiv = btn.closest(".card-photo");
  if (!photoDiv || !photoDiv._photos) return;
  const dir = parseInt(btn.dataset.dir, 10);
  const len = photoDiv._photos.length;
  photoDiv._idx = ((photoDiv._idx || 0) + dir + len) % len;
  setCarouselFrame(photoDiv);
}

// --- rendering ---
function cardSkeleton(card, idx) {
  const isPlaceholder = card.photo ? '' : ' placeholder';
  const dataPhotoAttr = card.photo ? `data-photo="${card.photo}"` : '';
  return `
    <article class="card" data-zpid="${card.zpid}" id="card-${card.zpid}">
      <div class="card-photo${isPlaceholder}" ${dataPhotoAttr}>
        <span class="card-rank">#${idx + 1}</span>
        <button class="card-favorite" data-fav title="Favorite">${favorites.properties.has(card.zpid) ? '★' : '☆'}</button>
        <button class="carousel-btn carousel-prev" data-dir="-1" hidden title="Previous photo">&#8249;</button>
        <button class="carousel-btn carousel-next" data-dir="1" hidden title="Next photo">&#8250;</button>
        <span class="carousel-counter" hidden></span>
      </div>
      <div class="card-body">
        <div class="card-title"><a href="${card.link}" target="_blank" rel="noopener">${card.address}, ${card.city}, ${card.state}</a></div>
        <div class="card-meta">$${(card.price || 0).toLocaleString()} · ${card.bedrooms || '?'}bd / ${card.bathrooms || '?'}ba · ${card.sqft || '?'} sqft · ${card.home_type || ''}</div>
        <div class="verdict-row">
          <span class="verdict PENDING">analyzing…</span>
        </div>
        <div class="breakdown" data-breakdown></div>
      </div>
    </article>`;
}

function breakdownHtml(r) {
  const compRange = (r.comp_psf_range && r.comp_psf_range[0] != null)
    ? `$${Math.round(r.comp_psf_range[0])}–$${Math.round(r.comp_psf_range[1])}/sqft`
    : "n/a";
  const passEmoji = r.passes_70_rule ? "✅" : "❌";
  const compsHtml = (r.comps_summary || []).map(c => `<li>${c}</li>`).join("") || "<li>(no comps)</li>";
  const risksHtml = (r.risk_flags || []).map(f => `<li>${f}</li>`).join("") || "<li>none flagged</li>";

  return `
    <h4>Flip: <strong>${r.verdict.replace(/_/g, ' ')}</strong> · Score ${r.flip_score}/100</h4>
    <p>${r.verdict_reason}</p>

    <h4>Rent: <strong>${(r.rental_verdict || 'NO_RENT_DATA').replace(/_/g, ' ')}</strong> · Score ${r.rental_score || 0}/100</h4>
    <p>${r.rental_verdict_reason || ''}</p>

    <h4>Flip math</h4>
    <div class="row"><span>ARV</span><span class="v">${fmtMoney(r.arv)} <span class="muted">(${r.arv_source}, ${r.arv_confidence}, ${r.comp_count} comps ${compRange})</span></span></div>
    <div class="row"><span>Rehab</span><span class="v">${fmtMoney(r.rehab_estimate)} <span class="muted">($${r.rehab_psf}/sqft, ${r.rehab_signal})</span></span></div>
    <div class="row"><span>Hold 6mo / Financing / Sell</span><span class="v">${fmtMoney(r.holding_cost_6mo)} / ${fmtMoney(r.financing_cost)} / ${fmtMoney(r.selling_cost)}</span></div>
    <div class="row"><span>All-in cost</span><span class="v">${fmtMoney(r.all_in_cost)}</span></div>
    <div class="row"><span>Net resale</span><span class="v">${fmtMoney(r.net_resale)}</span></div>
    <div class="row"><span>Projected profit</span><span class="v">${fmtMoney(r.projected_profit)} (${fmtPct(r.profit_margin_pct)})</span></div>
    <div class="row"><span>70% rule MAO</span><span class="v">${fmtMoney(r.mao_70_rule)} ${passEmoji}</span></div>

    <h4>Rental math (BRRRR)</h4>
    ${r.monthly_rent_est ? `
      <div class="row"><span>Rent est</span><span class="v">${fmtMoney(r.monthly_rent_est)}/mo</span></div>
      <div class="row"><span>Cap rate</span><span class="v">${fmtPct(r.cap_rate_pct)}</span></div>
      <div class="row"><span>Cash flow</span><span class="v">${fmtMoney(r.monthly_cash_flow)}/mo</span></div>
      <div class="row"><span>BRRRR refi proceeds</span><span class="v">${fmtMoney(r.brrrr_refi_proceeds)}</span></div>
    ` : `<p class="muted">No rent comps available.</p>`}

    <details><summary>Risks (${(r.risk_flags || []).length})</summary><ul>${risksHtml}</ul></details>
    <details><summary>Comps used (${(r.comps_summary || []).length})</summary><ul>${compsHtml}</ul></details>
  `;
}

function renderResults(city, baseCards) {
  currentCity = city;
  $("#results-grid").innerHTML = baseCards.map((c, i) => cardSkeleton(c, i)).join("");
  // Initialize carousels for cards that already have a photos array (history loads)
  baseCards.forEach(c => {
    if (c.photos && c.photos.length > 1) {
      const photoDiv = $(`#card-${c.zpid} .card-photo`);
      if (photoDiv) {
        photoDiv._photos = c.photos;
        photoDiv._idx = 0;
        setCarouselFrame(photoDiv);
      }
    }
  });
}

let currentIntent = "both";  // updated on each search

function _primaryScore(r, intent) {
  if (intent === "rent") return r.rental_score || 0;
  if (intent === "both") return Math.max(r.flip_score || 0, r.rental_score || 0);
  return r.flip_score || 0;
}

function upgradeCard(report) {
  const card = $(`#card-${report.zpid}`);
  if (!card) {
    // Property wasn't in the base discovery list (rare); append.
    const idx = $$("#results-grid .card").length;
    $("#results-grid").insertAdjacentHTML("beforeend", cardSkeleton(report, idx));
  }
  const target = $(`#card-${report.zpid}`);
  const photoDiv = target.querySelector(".card-photo");
  if (report.photos && report.photos.length) {
    photoDiv._photos = report.photos;
    photoDiv._idx = 0;
    photoDiv.dataset.photos = JSON.stringify(report.photos);
    photoDiv.dataset.photo = report.photos[0] || report.photo || '';
    setCarouselFrame(photoDiv);
    photoDiv.classList.remove("placeholder");
  } else if (report.photo) {
    photoDiv.dataset.photo = report.photo;
    photoDiv.classList.remove("placeholder");
    // If already visible, set immediately
    if (photoDiv._visible) photoDiv.style.backgroundImage = `url('${report.photo}')`;
  }
  const row = target.querySelector(".verdict-row");
  const score = _primaryScore(report, currentIntent);
  target.dataset.score = score;
  row.innerHTML = `
    <span class="verdict ${report.verdict}" title="Flip verdict">Flip: ${report.verdict.replace(/_/g, ' ')} (${report.flip_score})</span>
    <span class="verdict ${report.rental_verdict || 'NO_RENT_DATA'}" title="Rental verdict">Rent: ${(report.rental_verdict || 'NO_RENT_DATA').replace(/_/g, ' ')} (${report.rental_score || 0})</span>
  `;
  target.querySelector("[data-breakdown]").innerHTML = breakdownHtml(report);
  reorderByScore();
}

function reorderByScore() {
  const grid = $("#results-grid");
  const cards = Array.from(grid.children);
  // Sort scored cards first (desc by score), then unscored skeletons at the end
  cards.sort((a, b) => {
    const sa = a.dataset.score != null ? parseFloat(a.dataset.score) : -Infinity;
    const sb = b.dataset.score != null ? parseFloat(b.dataset.score) : -Infinity;
    return sb - sa;
  });
  cards.forEach((c, i) => {
    const rankEl = c.querySelector(".card-rank");
    if (rankEl) rankEl.textContent = `#${i + 1}`;
    grid.appendChild(c);
  });
}

function trimGrid(keepZpids) {
  const keep = new Set(keepZpids);
  $$("#results-grid .card").forEach(card => {
    if (!keep.has(card.dataset.zpid)) {
      card.style.transition = "opacity 0.3s";
      card.style.opacity = "0";
      setTimeout(() => card.remove(), 350);
    }
  });
  setTimeout(reorderByScore, 400);
}

// --- favorites ---
async function refreshFavorites() {
  const f = await api.favorites();
  favorites.properties = new Set(f.properties.map(p => p.zpid));
  favorites.cities = new Set(f.cities);
  $$(".card-favorite").forEach(btn => {
    const zpid = btn.closest("[data-zpid]")?.dataset.zpid;
    if (favorites.properties.has(zpid)) { btn.textContent = "★"; btn.classList.add("on"); }
    else { btn.textContent = "☆"; btn.classList.remove("on"); }
  });
  // Save city button reflects current city favorite state
  const btn = $("#save-city-btn");
  if (currentSlug && btn) {
    btn.hidden = false;
    if (favorites.cities.has(currentSlug)) {
      btn.textContent = "★ Saved (city)";
      btn.classList.add("on");
    } else {
      btn.textContent = "☆ Save this city";
      btn.classList.remove("on");
    }
  } else if (btn) {
    btn.hidden = true;
  }
}

document.addEventListener("click", _handleCarouselClick);

document.addEventListener("click", async (e) => {
  const fav = e.target.closest("[data-fav]");
  if (fav) {
    const card = fav.closest("[data-zpid]");
    const zpid = card.dataset.zpid;
    const on = !favorites.properties.has(zpid);
    await api.favProp(zpid, on, currentSlug);
    await refreshFavorites();
  }
  if (e.target.closest("[data-close]")) {
    e.target.closest(".modal").hidden = true;
  }
});

// --- search ---
async function startSearch(city, count, intent) {
  hideQuota();
  currentIntent = intent;

  // Preflight: refresh quota chip and short-circuit cleanly if already capped.
  try {
    const me = await api.me();
    if (me.authenticated) {
      renderUserChip(me);
      if (me.remaining === 0) {
        setStatus(`⚠️ Daily run limit reached (${me.daily_cap}/${me.daily_cap}). Try again tomorrow or ask your admin to raise your cap.`);
        return;
      }
    }
  } catch (_) { /* non-fatal — let the server enforce */ }

  $("#search-btn").disabled = true;
  setStatus(`Searching ${city} (${intent})…`);
  const url = `/api/search/stream?city=${encodeURIComponent(city)}&count=${count}&intent=${encodeURIComponent(intent)}`;
  const es = new EventSource(url, {withCredentials: true});

  es.addEventListener("status", (e) => {
    const d = JSON.parse(e.data); setStatus(d.message);
  });
  es.addEventListener("discovery", (e) => {
    const d = JSON.parse(e.data);
    renderResults(d.city, d.properties);
    setStatus(`Found ${d.count} properties — enriching…`);
  });
  es.addEventListener("enrich_tick", (e) => {
    const d = JSON.parse(e.data);
    setStatus(`Still enriching… ${d.elapsed}s elapsed (${d.requested} URLs)`);
  });
  es.addEventListener("property", (e) => {
    const r = JSON.parse(e.data);
    upgradeCard(r);
  });
  es.addEventListener("trim", (e) => {
    const d = JSON.parse(e.data);
    trimGrid(d.keep);
  });
  es.addEventListener("quota", (e) => {
    const d = JSON.parse(e.data);
    showQuota(d.message);
  });
  es.addEventListener("error", (e) => {
    try {
      const d = JSON.parse(e.data);
      setStatus(`⚠️ ${d.message}`);
    } catch {
      setStatus("⚠️ Connection error");
    }
    es.close();
    $("#search-btn").disabled = false;
  });
  es.addEventListener("complete", (e) => {
    const d = JSON.parse(e.data);
    currentSlug = d.slug;
    const s = d.summary || {};
    const cacheNote = (s.from_cache != null && s.fresh != null)
      ? ` (${s.from_cache} cached, ${s.fresh} fresh, $${(s.cost_usd || 0).toFixed(4)})`
      : "";
    setStatus(`✅ Done — ${d.total} ranked. Enriched ${s.enriched}/${s.requested}${cacheNote}.`);
    es.close();
    $("#search-btn").disabled = false;
    refreshHistory();
    refreshFavorites();
    refreshQuotaChip();
  });
}

async function loadFromHistory(slug) {
  hideQuota();
  setStatus("Loading cached results…");
  const data = await api.historyOne(slug);
  currentCity = data.city;
  currentSlug = slug;
  const cards = data.results.map(r => ({
    zpid: r.zpid, address: r.address, city: r.city, state: r.state, price: r.purchase_price,
    bedrooms: null, bathrooms: null, sqft: r.sqft, home_type: r.home_type,
    photo: r.photo, photos: r.photos, link: r.link,
  }));
  renderResults(data.city, cards);
  data.results.forEach(r => upgradeCard(r));
  setStatus(`📂 Cached: ${data.city} (queried ${timeAgo(data.queried_at)})`);
  refreshFavorites();
}

async function refreshHistory() {
  const data = await api.history();
  const ul = $("#history-list");
  ul.innerHTML = data.items.map(it => `
    <li data-slug="${it.slug}">
      <span>${it.city}</span>
      <span class="ago">${timeAgo(it.queried_at)}</span>
    </li>`).join("") || `<li class="muted" style="padding:0.5rem">No searches yet.</li>`;
  ul.querySelectorAll("li[data-slug]").forEach(li => {
    li.addEventListener("click", () => loadFromHistory(li.dataset.slug));
  });
}

// --- archive: redirect to sidebar (one source of truth) ---
$("#nav-archive").addEventListener("click", () => {
  const heading = $("#archive-heading") || $("#history-list");
  if (heading) {
    heading.scrollIntoView({behavior: "smooth", block: "start"});
    const list = $("#history-list");
    list.classList.add("highlight-section");
    setTimeout(() => list.classList.remove("highlight-section"), 1500);
  }
});

// --- save city button ---
$("#save-city-btn").addEventListener("click", async () => {
  if (!currentSlug) return;
  const on = !favorites.cities.has(currentSlug);
  await api.favCity(currentSlug, on);
  await refreshFavorites();
});

async function openFavoritesModal() {
  const f = await api.favorites();

  // --- Saved Listings tab: resolve each {zpid, city_slug} to the actual property data ---
  if (!f.properties.length) {
    $("#fav-props").innerHTML = `<div class="fav-empty">No favorited listings yet. Click ☆ on any property card.</div>`;
  } else {
    // Group by city to minimize history fetches
    const byCity = {};
    for (const p of f.properties) {
      (byCity[p.city_slug] ||= []).push(p.zpid);
    }
    const cityData = {};
    for (const slug of Object.keys(byCity)) {
      if (!slug) continue;
      try { cityData[slug] = await api.historyOne(slug); }
      catch { cityData[slug] = null; }
    }
    const cards = [];
    for (const p of f.properties) {
      const hist = cityData[p.city_slug];
      const rec = hist?.results?.find(r => r.zpid === p.zpid);
      if (!rec) {
        cards.push(`
          <div class="fav-card" data-orphan>
            <div class="fav-info">
              <div class="fav-addr">Property ${p.zpid}</div>
              <div class="fav-city muted">${p.city_slug || "city unknown"} — not in archive (re-run that city to see it)</div>
            </div>
            <button class="fav-remove" data-fav-remove="${p.zpid}" title="Remove from favorites">×</button>
          </div>`);
        continue;
      }
      cards.push(`
        <div class="fav-card" data-load-prop="${p.zpid}" data-city-slug="${p.city_slug}">
          <div class="fav-thumb" style="${rec.photo ? `background-image:url('${rec.photo}')` : ''}"></div>
          <div class="fav-info">
            <div class="fav-addr">${rec.address}</div>
            <div class="fav-city">${rec.city}, ${rec.state} · $${(rec.purchase_price || 0).toLocaleString()}</div>
            <div class="fav-meta">
              <span class="verdict ${rec.verdict}">Flip: ${rec.verdict.replace(/_/g, ' ')}</span>
              <span class="verdict ${rec.rental_verdict || 'NO_RENT_DATA'}">Rent: ${(rec.rental_verdict || 'NO_RENT_DATA').replace(/_/g, ' ')}</span>
            </div>
          </div>
          <button class="fav-remove" data-fav-remove="${p.zpid}" title="Remove from favorites">×</button>
        </div>`);
    }
    $("#fav-props").innerHTML = cards.join("");
  }

  // --- Watched Cities tab ---
  if (!f.cities.length) {
    $("#fav-cities").innerHTML = `<div class="fav-empty">No saved cities yet. Click "Save this city" after a search.</div>`;
  } else {
    const cards = [];
    for (const slug of f.cities) {
      try {
        const hist = await api.historyOne(slug);
        cards.push(`
          <div class="fav-card" data-load-city="${slug}">
            <div class="fav-info">
              <div class="fav-addr">${hist.city}</div>
              <div class="fav-city">${hist.results.length} listings · queried ${timeAgo(hist.queried_at)}</div>
            </div>
            <button class="fav-remove" data-fav-city-remove="${slug}" title="Remove from favorites">×</button>
          </div>`);
      } catch {
        cards.push(`
          <div class="fav-card">
            <div class="fav-info">
              <div class="fav-addr">${slug}</div>
              <div class="fav-city muted">(no cached data — re-run to see)</div>
            </div>
            <button class="fav-remove" data-fav-city-remove="${slug}" title="Remove from favorites">×</button>
          </div>`);
      }
    }
    $("#fav-cities").innerHTML = cards.join("");
  }

  // --- Click handlers ---
  $$("#fav-props [data-load-prop]").forEach(el => {
    el.addEventListener("click", async () => {
      const zpid = el.dataset.loadProp;
      const slug = el.dataset.citySlug;
      $("#modal-favorites").hidden = true;
      await loadFromHistory(slug);
      setTimeout(() => scrollToCard(zpid), 100);
    });
  });
  $$("#fav-cities [data-load-city]").forEach(el => {
    el.addEventListener("click", () => {
      const slug = el.dataset.loadCity;
      $("#modal-favorites").hidden = true;
      loadFromHistory(slug);
    });
  });
  $$("[data-fav-remove]").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      await api.favProp(btn.dataset.favRemove, false);
      await refreshFavorites();
      openFavoritesModal();
    });
  });
  $$("[data-fav-city-remove]").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      await api.favCity(btn.dataset.favCityRemove, false);
      await refreshFavorites();
      openFavoritesModal();
    });
  });

  $("#modal-favorites").hidden = false;
}

function scrollToCard(zpid) {
  const card = $(`#card-${zpid}`);
  if (!card) return;
  card.scrollIntoView({behavior: "smooth", block: "center"});
  card.classList.add("highlight");
  setTimeout(() => card.classList.remove("highlight"), 3000);
}

$("#nav-favorites").addEventListener("click", openFavoritesModal);

$$(".tab").forEach(t => {
  t.addEventListener("click", () => {
    $$(".tab").forEach(x => x.classList.toggle("active", x === t));
    const which = t.dataset.tab;
    $("#fav-props").hidden = which !== "props";
    $("#fav-cities").hidden = which !== "cities";
    $("#fav-props").classList.toggle("active", which === "props");
    $("#fav-cities").classList.toggle("active", which === "cities");
  });
});

// --- form ---
$("#search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const city = $("#city").value.trim();
  const count = parseInt($("#count").value, 10) || 10;
  const intent = $("#intent").value || "both";
  if (!city) return;
  startSearch(city, count, intent);
});

$("#logout-btn").addEventListener("click", async () => {
  await api.logout();
  window.location = "/login";
});

$("#quota-banner-close").addEventListener("click", hideQuota);

// --- active runs panel ---
let _activeSources = {};

async function fetchActiveRuns() {
  try {
    const r = await fetch('/api/runs', {credentials: 'include'});
    if (!r.ok) return;
    const data = await r.json();
    renderActiveRuns(data.runs || []);
  } catch (e) { /* ignore */ }
}

function renderActiveRuns(runs) {
  const ul = $('#active-runs');
  if (!ul) return;
  // Only show truly active runs (pending/running). Keep UI small.
  const active = runs.filter(r => (r.status || 'pending') === 'pending' || (r.status || '') === 'running');
  if (!active.length) { ul.innerHTML = '<li class="muted">No active runs</li>'; return; }
  ul.innerHTML = active.map(run => {
    const id = run.id;
    const started = timeAgo(run.started_at);
    const status = run.status || 'pending';
    return `<li data-run-id="${id}" tabindex="0" role="button">
      <div class="run-city">${run.city}</div>
      <div class="run-meta">${status} · ${started}</div>
      <div class="run-progress" id="run-progress-${id}"></div>
    </li>`;
  }).join('');

  // Click handlers: open run details in right pane
  ul.querySelectorAll('li[data-run-id]').forEach(li => {
    li.addEventListener('click', (e) => {
      const id = parseInt(li.dataset.runId, 10);
      $$('#active-runs li').forEach(x => x.classList.toggle('selected', x === li));
      viewRun(id);
    });
    li.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); li.click(); } });
  });

  // attach SSE to running ones for compact progress in list
  active.forEach(run => {
    if ((run.status === 'pending' || run.status === 'running') && !_activeSources[run.id]) {
      const es = new EventSource(`/api/runs/${run.id}/events`, {withCredentials: true});
      _activeSources[run.id] = es;
      es.addEventListener('enrich_tick', (e) => {
        try {
          const d = JSON.parse(e.data);
          const el = $(`#run-progress-${run.id}`);
          if (el) el.textContent = `Enriching — ${d.elapsed}s elapsed (${d.requested} requested)`;
        } catch(_){}
      });
      es.addEventListener('property', (e) => {
        try {
          const d = JSON.parse(e.data);
          const el = $(`#run-progress-${run.id}`);
          if (el) {
            el.textContent = `Got property ${d.zpid} — ${d.verdict || ''}`;
          }
        } catch(_){}
      });
      es.addEventListener('status', (e) => {
        try { const d = JSON.parse(e.data); const el = $(`#run-progress-${run.id}`); if (el) el.textContent = d.message; } catch(_){}
      });
      es.addEventListener('complete', (e) => {
        try { const d = JSON.parse(e.data); const el = $(`#run-progress-${run.id}`); if (el) el.textContent = `Complete — ${d.total} items`; } catch(_){}
        // close source and refresh archive
        setTimeout(() => { es.close(); delete _activeSources[run.id]; fetchActiveRuns(); refreshHistory(); }, 1500);
      });
      es.addEventListener('error', (e) => {
        try { const d = JSON.parse(e.data); const el = $(`#run-progress-${run.id}`); if (el) el.textContent = `Error: ${d.message}`; } catch(_){}
        es.close(); delete _activeSources[run.id]; fetchActiveRuns();
      });
    }
  });
}

// View a run's live output in the right panel
let _viewSource = null;
function viewRun(runId) {
  // close previous view source
  if (_viewSource) { try { _viewSource.close(); } catch(_){} _viewSource = null; }
  // clear results grid
  $("#results-grid").innerHTML = '';
  setStatus('Loading run output...');
  const es = new EventSource(`/api/runs/${runId}/events`, {withCredentials: true});
  _viewSource = es;
  es.addEventListener('status', (e) => { try { const d = JSON.parse(e.data); setStatus(d.message); } catch(_){} });
  es.addEventListener('discovery', (e) => {
    try {
      const d = JSON.parse(e.data);
      renderResults(d.city, d.properties);
      setStatus(`Found ${d.count} properties — enriching…`);
    } catch(_){}
  });
  es.addEventListener('enrich_tick', (e) => { try { const d = JSON.parse(e.data); setStatus(`Enriching — ${d.elapsed}s elapsed (${d.requested} requested)`); } catch(_){} });
  es.addEventListener('property', (e) => { try { const r = JSON.parse(e.data); upgradeCard(r); } catch(_){} });
  es.addEventListener('trim', (e) => { try { const d = JSON.parse(e.data); trimGrid(d.keep); } catch(_){} });
  es.addEventListener('complete', (e) => {
    try { const d = JSON.parse(e.data); setStatus(`Run complete — ${d.total} items`); } catch(_){}
    setTimeout(() => { try { es.close(); } catch(_){} _viewSource = null; refreshHistory(); fetchActiveRuns(); }, 1000);
  });
  es.addEventListener('error', (e) => { try { const d = JSON.parse(e.data); setStatus(`Error: ${d.message}`); } catch(_){} try { es.close(); } catch(_){} _viewSource = null; fetchActiveRuns(); });
}

// poll active runs periodically
setInterval(fetchActiveRuns, 5000);

// --- init ---
(async () => {
  if (!(await ensureAuth())) return;
  refreshHistory();
  refreshFavorites();
})();

// --- mobile helpers: sidebar toggle + FAB + lazy-load photos ---
(function(){
  const menuBtn = document.getElementById('menu-toggle');
  const topbar = document.querySelector('.topbar');
  const sidebar = document.querySelector('.sidebar');
  const fab = document.getElementById('fab-run');
  const collapseBtn = document.getElementById('sidebar-collapse');

  function positionSidebarTopAndHandle() {
    const topH = topbar ? Math.round(topbar.getBoundingClientRect().height) : 0;
    if (sidebar) {
      sidebar.style.top = topH + 'px';
      sidebar.style.height = `calc(100vh - ${topH}px)`;
    }
    if (collapseBtn && sidebar) {
      const sRect = sidebar.getBoundingClientRect();
      const hW = collapseBtn.offsetWidth || 36;
      if (sidebar.classList.contains('open')) {
        const left = Math.max(8, Math.round(sRect.right - (hW / 2)));
        collapseBtn.style.left = left + 'px';
        collapseBtn.textContent = '‹';
      } else {
        collapseBtn.style.left = '0px';
        collapseBtn.textContent = '›';
      }
      collapseBtn.style.opacity = '1';
    }
  }

  // Re-run positionSidebarTopAndHandle every rAF for ~300ms so the handle
  // smoothly tracks the sidebar during its 240ms CSS transition.
  function trackHandleTransition() {
    const deadline = Date.now() + 300;
    (function frame() {
      positionSidebarTopAndHandle();
      if (Date.now() < deadline) requestAnimationFrame(frame);
    })();
  }

  // Start sidebar open on mobile viewports
  if (sidebar && window.innerWidth <= 768) sidebar.classList.add('open');
  // initial positioning after layout
  setTimeout(trackHandleTransition, 60);
  window.addEventListener('resize', positionSidebarTopAndHandle);

  if (menuBtn && sidebar) {
    menuBtn.addEventListener('click', (e) => { e.stopPropagation(); sidebar.classList.toggle('open'); trackHandleTransition(); });
    document.addEventListener('click', (e) => {
      if (!sidebar.classList.contains('open')) return;
      if (e.target.closest('.sidebar') || e.target.closest('#menu-toggle')) return;
      sidebar.classList.remove('open'); trackHandleTransition();
    });
  }

  if (fab) {
    fab.addEventListener('click', (e) => {
      e.preventDefault();
      if (sidebar) { sidebar.classList.add('open'); trackHandleTransition(); }
      const cityInput = document.getElementById('city');
      if (cityInput) { cityInput.focus(); }
    });
  }

  // Sidebar collapse button on the right edge of the left panel
  if (collapseBtn && sidebar) {
    collapseBtn.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); sidebar.classList.toggle('open'); trackHandleTransition(); });
  }

  // Lazy-load card photos using IntersectionObserver
  const io = new IntersectionObserver((entries) => {
    for (const ent of entries) {
      const el = ent.target;
      if (ent.isIntersecting) {
        // mark visible
        el._visible = true;
        const photosJson = el.dataset.photos;
        let url = el.dataset.photo || null;
        try {
          if (photosJson) {
            const arr = JSON.parse(photosJson);
            if (arr.length && typeof arr[0] === 'object' && arr[0].url) {
              // array of objects {url, width}
              const dpr = window.devicePixelRatio || 1;
              const targetPx = Math.min(window.innerWidth * dpr, 2048);
              // pick smallest width >= targetPx, otherwise largest available
              const withWidth = arr.filter(p => p.width).sort((a,b) => a.width - b.width);
              let candidate = null;
              if (withWidth.length) {
                candidate = withWidth.find(p => p.width >= targetPx) || withWidth[withWidth.length-1];
              }
              if (!candidate) candidate = arr[0];
              url = candidate.url || (arr[0] && arr[0].url);
            } else {
              // legacy array of urls
              url = arr[0] || url;
            }
          }
        } catch(_){ }
        if (url) {
          el.style.backgroundImage = `url('${url}')`;
          el.classList.remove('placeholder');
        }
        io.unobserve(el);
      }
    }
  }, {rootMargin: '300px 0px', threshold: 0.01});

  // Observe existing photo divs and future ones
  function observePhotos() {
    document.querySelectorAll('.card-photo').forEach(p => {
      if (!p._observed) { io.observe(p); p._observed = true; }
    });
  }
  // run on init and after discovery renders
  observePhotos();
  // Re-run periodically to catch newly added cards
  const obsTimer = setInterval(observePhotos, 1500);

})();


