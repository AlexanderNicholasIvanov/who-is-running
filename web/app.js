// Who is running? Static front end over data/manifest.json and data/<source>/<year>.json.
// Plain ES module, no build step.

const STATE_NAMES = {
  AL: "Alabama", AK: "Alaska", AS: "American Samoa", AZ: "Arizona", AR: "Arkansas",
  CA: "California", CO: "Colorado", CT: "Connecticut", DE: "Delaware",
  DC: "District of Columbia", FL: "Florida", GA: "Georgia", GU: "Guam", HI: "Hawaii",
  ID: "Idaho", IL: "Illinois", IN: "Indiana", IA: "Iowa", KS: "Kansas", KY: "Kentucky",
  LA: "Louisiana", ME: "Maine", MD: "Maryland", MA: "Massachusetts", MI: "Michigan",
  MN: "Minnesota", MS: "Mississippi", MO: "Missouri", MT: "Montana", NE: "Nebraska",
  NV: "Nevada", NH: "New Hampshire", NJ: "New Jersey", NM: "New Mexico", NY: "New York",
  NC: "North Carolina", ND: "North Dakota", MP: "Northern Mariana Islands", OH: "Ohio",
  OK: "Oklahoma", OR: "Oregon", PA: "Pennsylvania", PR: "Puerto Rico", RI: "Rhode Island",
  SC: "South Carolina", SD: "South Dakota", TN: "Tennessee", TX: "Texas", UT: "Utah",
  VT: "Vermont", VI: "US Virgin Islands", VA: "Virginia", WA: "Washington",
  WV: "West Virginia", WI: "Wisconsin", WY: "Wyoming",
};
const SOURCE_LABELS = { fec: "FEC registration", nc: "NC State Board of Elections", md: "MD State Board of Elections" };
const LEVELS = ["federal", "state", "local"];
const INACTIVE = ["withdrawn", "declined", "disqualified", "deceased", "failed"];
const PAGE_SIZE = 150; // races rendered per "page"

const $ = (id) => document.getElementById(id);
const els = {
  form: $("filters"), state: $("state"), year: $("year"), level: $("level"), type: $("type"),
  county: $("county"), q: $("q"), statutory: $("statutory"), preferBallot: $("prefer-ballot"),
  president: $("president"), coverage: $("coverage"), status: $("status"), results: $("results"),
  more: $("more"), generated: $("generated"),
};

const cache = new Map();
let manifest = null;
let loaded = []; // candidacy objects for the selected state + year
let groups = []; // filtered races, sorted
let shown = 0;
let loadToken = 0;

const collator = new Intl.Collator("en", { numeric: true, sensitivity: "base" });

function esc(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

async function fetchJSON(path) {
  if (!cache.has(path)) {
    cache.set(path, fetch(path).then((r) => {
      if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
      return r.json();
    }));
  }
  return cache.get(path);
}

const stateName = (code) => STATE_NAMES[code] || code;

function statesWithBallotLists() {
  return new Set(manifest.files.filter((f) => f.source !== "fec").flatMap((f) => Object.keys(f.coverage)));
}

function populateStates() {
  const full = statesWithBallotLists();
  const states = new Set(manifest.files.flatMap((f) => Object.keys(f.coverage)));
  states.delete("US");
  const sorted = [...states].sort((a, b) => collator.compare(stateName(a), stateName(b)));
  for (const code of sorted) {
    const label = full.has(code) ? `${stateName(code)} (all levels)` : stateName(code);
    els.state.add(new Option(label, code));
  }
}

function yearsFor(state) {
  const years = new Set();
  for (const f of manifest.files) {
    for (const y of Object.keys(f.coverage[state] || {})) years.add(Number(y));
  }
  return [...years].sort((a, b) => b - a);
}

function populateYears(preferred) {
  const years = yearsFor(els.state.value);
  els.year.replaceChildren(...years.map((y) => new Option(String(y), String(y))));
  els.year.disabled = years.length === 0;
  const thisYear = new Date().getFullYear();
  const pick = years.includes(preferred) ? preferred : (years.find((y) => y <= thisYear) ?? years[0]);
  if (pick !== undefined) els.year.value = String(pick);
}

function filesFor(state, year) {
  return manifest.files.filter((f) => f.coverage[state]?.[year] || f.coverage.US?.[year]);
}

async function load() {
  const state = els.state.value;
  const year = els.year.value;
  const token = ++loadToken;
  loaded = [];
  if (!state || !year) {
    render();
    return;
  }
  els.status.textContent = "Loading…";
  try {
    const datasets = await Promise.all(
      filesFor(state, year).map((f) => fetchJSON(`data/${f.source}/${f.year}.json`)),
    );
    if (token !== loadToken) return; // a newer selection superseded this one
    for (const { columns, rows } of datasets) {
      const idx = Object.fromEntries(columns.map((c, i) => [c, i]));
      for (const row of rows) {
        const st = row[idx.state];
        if ((st !== state && st !== "US") || row[idx.election_year] !== year) continue;
        const obj = {};
        for (const c of columns) obj[c] = row[idx[c]];
        loaded.push(obj);
      }
    }
  } catch (err) {
    els.status.textContent = `Couldn't load data: ${err.message}`;
    return;
  }
  populateCounties();
  render();
}

function populateCounties() {
  const current = els.county.value;
  const counties = [...new Set(loaded.map((r) => r.jurisdiction).filter(Boolean))].sort(collator.compare);
  els.county.replaceChildren(new Option("All counties", ""), ...counties.map((c) => new Option(c, c)));
  els.county.disabled = counties.length === 0;
  if (counties.includes(current)) els.county.value = current;
}

function visibleRows() {
  const state = els.state.value;
  const hasBallotFederal = loaded.some((r) => r.source !== "fec" && r.level === "federal");
  const q = els.q.value.trim().toLowerCase();
  return loaded.filter((r) => {
    if (r.source === "fec") {
      if (els.preferBallot.checked && hasBallotFederal) return false;
      if (els.statutory.checked && r.candidate_status === "not_yet_statutory_candidate") return false;
    }
    if (r.state === "US" && (!els.president.checked || state === "US")) return false;
    if (els.level.value && r.level !== els.level.value) return false;
    if (els.type.value && r.election_type !== els.type.value) return false;
    if (els.county.value && r.jurisdiction !== els.county.value) return false;
    if (q) {
      const hay = `${r.candidate_name} ${r.office} ${r.district} ${r.jurisdiction}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function groupRaces(rows) {
  const byKey = new Map();
  for (const r of rows) {
    const key = [r.level, r.office, r.district, r.jurisdiction, r.election_date, r.election_type, r.source].join("|");
    if (!byKey.has(key)) byKey.set(key, { ...r, candidates: [] });
    byKey.get(key).candidates.push(r);
  }
  const out = [...byKey.values()];
  for (const g of out) g.candidates.sort((a, b) => collator.compare(a.candidate_name, b.candidate_name));
  out.sort((a, b) =>
    LEVELS.indexOf(a.level) - LEVELS.indexOf(b.level)
    || collator.compare(a.office, b.office)
    || collator.compare(a.district, b.district)
    || collator.compare(a.jurisdiction, b.jurisdiction)
    || collator.compare(b.election_date, a.election_date), // most recent election first
  );
  return out;
}

function formatDate(iso) {
  if (!iso) return "";
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", {
    timeZone: "UTC", month: "short", day: "numeric", year: "numeric",
  });
}

function humanize(status) {
  return status.replace(/_/g, " ");
}

function candidateHTML(c) {
  const status = c.candidate_status;
  const inactive = INACTIVE.some((s) => status.startsWith(s));
  const badges = [];
  if (c.party) badges.push(`<span class="badge party-${esc(c.party.replace(/[^A-Za-z]/g, ""))}">${esc(c.party)}</span>`);
  if (c.incumbent_status === "incumbent") badges.push('<span class="badge">Incumbent</span>');
  if (status && !["active", "statutory_candidate"].includes(status)) {
    badges.push(`<span class="badge note">${esc(humanize(status))}</span>`);
  }
  return `<li class="candidate${inactive ? " inactive" : ""}"><span class="name">${esc(c.candidate_name)}</span>${badges.join("")}</li>`;
}

function raceHTML(g) {
  const meta = [];
  if (g.district && !/\b(DISTRICT|CIRCUIT)\b/i.test(g.office)) meta.push(`District ${g.district}`);
  if (g.state === "US") meta.push("Nationwide");
  if (g.jurisdiction) meta.push(g.jurisdiction);
  const when = [g.election_type && g.election_type[0].toUpperCase() + g.election_type.slice(1), formatDate(g.election_date)]
    .filter(Boolean).join(" ");
  if (when) meta.push(when);
  meta.push(SOURCE_LABELS[g.source] || g.source);
  return `<article class="race">
    <h3>${esc(g.office)}</h3>
    <p class="race-meta">${meta.map((m) => `<span>${esc(m)}</span>`).join("")}</p>
    <ul class="candidates">${g.candidates.map(candidateHTML).join("")}</ul>
  </article>`;
}

function renderMore() {
  const next = groups.slice(shown, shown + PAGE_SIZE);
  let html = "";
  let lastLevel = shown > 0 ? groups[shown - 1].level : null;
  for (const g of next) {
    if (g.level !== lastLevel) {
      html += `<h2 class="level-heading">${esc(g.level)}</h2>`;
      lastLevel = g.level;
    }
    html += raceHTML(g);
  }
  els.results.insertAdjacentHTML("beforeend", html);
  shown += next.length;
  els.more.hidden = shown >= groups.length;
}

function renderCoverage() {
  const state = els.state.value;
  if (!state || statesWithBallotLists().has(state)) {
    els.coverage.textContent = "";
    return;
  }
  const full = [...statesWithBallotLists()].map(stateName).sort().join(" and ");
  els.coverage.textContent = `For ${stateName(state)} we only have federal candidates, from FEC registrations. `
    + `State and local races are currently covered for ${full}.`;
}

function render() {
  renderCoverage();
  writeHash();
  els.results.replaceChildren();
  shown = 0;
  if (!els.state.value) {
    groups = [];
    els.status.textContent = "Choose a state to see who is running.";
    els.more.hidden = true;
    return;
  }
  const rows = visibleRows();
  groups = groupRaces(rows);
  const people = rows.length.toLocaleString("en-US");
  const races = groups.length.toLocaleString("en-US");
  els.status.textContent = rows.length
    ? `${people} candidacies in ${races} races, ${stateName(els.state.value)} ${els.year.value}.`
    : "No candidates match these filters.";
  if (els.county.value) {
    els.status.textContent += ` Showing races held only in ${els.county.value}; statewide and multi-county races are hidden.`;
  }
  renderMore();
}

// --- URL hash: #state=NC&year=2026&level=local&q=mayor -------------------

const DEFAULT_FLAGS = { statutory: true, preferBallot: true, president: true };

function writeHash() {
  const p = new URLSearchParams();
  for (const k of ["state", "year", "level", "type", "county", "q"]) {
    if (els[k].value) p.set(k, els[k].value);
  }
  for (const [k, def] of Object.entries(DEFAULT_FLAGS)) {
    if (els[k].checked !== def) p.set(k, els[k].checked ? "1" : "0");
  }
  const hash = p.toString();
  history.replaceState(null, "", hash ? `#${hash}` : location.pathname + location.search);
}

function readHash() {
  return new URLSearchParams(location.hash.slice(1));
}

async function applyHash() {
  const p = readHash();
  els.state.value = p.get("state") || "";
  for (const k of ["level", "type", "q"]) els[k].value = p.get(k) || "";
  for (const [k, def] of Object.entries(DEFAULT_FLAGS)) {
    els[k].checked = p.has(k) ? p.get(k) === "1" : def;
  }
  populateYears(Number(p.get("year")));
  await load();
  if (p.get("county")) {
    els.county.value = p.get("county");
    render();
  }
}

// --- wiring -------------------------------------------------------------

async function init() {
  try {
    manifest = await fetchJSON("data/manifest.json");
  } catch (err) {
    els.status.textContent = `Couldn't load the data index: ${err.message}`;
    return;
  }
  els.generated.textContent = `Last updated ${manifest.generated.replace("T", " ").replace("Z", " UTC")}.`;
  populateStates();
  await applyHash();

  els.form.addEventListener("submit", (e) => e.preventDefault());
  els.state.addEventListener("change", async () => {
    els.county.value = "";
    populateYears(Number(els.year.value));
    await load();
  });
  els.year.addEventListener("change", load);
  for (const el of [els.level, els.type, els.county, els.statutory, els.preferBallot, els.president]) {
    el.addEventListener("change", render);
  }
  let debounce;
  els.q.addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(render, 150);
  });
  els.more.addEventListener("click", renderMore);
  // Back/forward and pasted links. writeHash uses replaceState, which doesn't fire this.
  window.addEventListener("hashchange", applyHash);
}

init();
