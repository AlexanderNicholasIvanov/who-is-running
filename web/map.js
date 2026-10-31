// Results map: states shaded by who won a statewide race; pick a state to see its counties.
// Shapes are pre-drawn SVG paths (web/geo/, see geo.py); votes come from results/<year>/.

const MARGIN_STEPS = [5, 10, 20]; // percentage-point edges between the four shades
const STEP_LABELS = ["Under 5", "5–10", "10–20", "20+"];

const esc = (value) => String(value).replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[c]);

// MEDSL names are upper case ("DONALD J TRUMP", "ST. LOUIS CITY").
const titleCase = (s) => s.toLowerCase().replace(/(^|[\s\-/.'(])([a-z])/g, (m, p, c) => p + c.toUpperCase());
const fmt = new Intl.NumberFormat("en-US");
const pct = (n) => `${n.toFixed(1)}%`;
const raceName = (office) => titleCase(office).replace(/^Us /, "US ");

// "DEM/WORKING FAMILIES" and "REPUBLICAN/CONSERVATIVE" both count as the major party.
export function partyClass(party) {
  const parts = party.toUpperCase().split("/");
  if (parts.some((p) => p === "DEM" || p.startsWith("DEMOCRAT"))) return "dem";
  if (parts.some((p) => p === "REP" || p.startsWith("REPUBLICAN"))) return "rep";
  return "other";
}
const PARTY_NAMES = { dem: "Democrat", rep: "Republican", other: "Other party" };
const partyLabel = (party) => party || "no party";

// votes: one number per candidate, candidates: [[name, party, total], ...].
function tally(candidates, votes) {
  const total = votes.reduce((a, b) => a + b, 0);
  const order = votes.map((v, i) => i).sort((a, b) => votes[b] - votes[a]);
  if (!total) return { total, order, winner: null, margin: 0 };
  const [first, second] = order;
  const margin = ((votes[first] - (second === undefined ? 0 : votes[second])) / total) * 100;
  const tied = second !== undefined && votes[first] === votes[second];
  return { total, order, winner: tied ? null : candidates[first], margin, tied };
}

const step = (margin) => MARGIN_STEPS.filter((edge) => margin >= edge).length;

function fill(t) {
  if (!t.winner) return "url(#map-hatch-none)";
  const party = partyClass(t.winner[1]);
  return party === "other" ? "url(#map-hatch-other)" : `var(--map-${party}-${step(t.margin)})`;
}

function summary(t) {
  if (!t.total) return "No votes reported";
  if (t.tied) return "Tied";
  return `${titleCase(t.winner[0])} (${partyLabel(t.winner[1])}) by ${t.margin.toFixed(1)} pts`;
}

// Candidates are partisan if any has a party; the map is about which party leads.
const isPartisan = (race) => race.candidates.some(([, party]) => party);

// --- ranked-choice rounds (results/<year>/rcv.json) ------------------------
// A race: {state, office, district, election_type, candidates: [[name, party, [votes per round]]],
// exhausted: [per round]}, winner first. Tabulations say "Last, First"; show "First Last".
const rcvName = (name) => name.replace(/^([^,]+),\s*(.+)$/, "$2 $1");
const rounds = (race) => race.exhausted.length;
// The statewide general-election count of a map race, if it went to ranked-choice rounds.
const rcvRace = (rcv, st, office) => rcv.find((r) => r.state === st && r.office === office
  && !r.district && r.election_type === "general");
// Final-round tally in the map's shape. Without the winner's party it can't be shaded honestly.
function rcvTally(race) {
  const n = rounds(race) - 1;
  const t = tally(race.candidates.map(([name, party, v]) => [rcvName(name), party, v[n]]),
    race.candidates.map((c) => c[2][n]));
  return t.winner && t.winner[1] ? t : null;
}

export function rcvSummary(race) {
  const n = rounds(race) - 1;
  const [winner, second] = race.candidates;
  const total = race.candidates.reduce((sum, c) => sum + c[2][n], 0);
  const share = (c) => pct((c[2][n] / total) * 100);
  return `Ranked-choice: decided in round ${n + 1}. ${rcvName(winner[0])} won the final round, `
    + `${share(winner)}${second ? ` to ${share(second)} for ${rcvName(second[0])}` : ""}.`;
}

export function rcvTableHTML(race, caption) {
  const n = rounds(race);
  const cols = Array.from({ length: n }, (_, i) => i);
  const finalTotal = race.candidates.reduce((sum, c) => sum + c[2][n - 1], 0);
  const cell = (votes, i, everHad) => {
    if (!votes && everHad && i > 0) return '<td class="num rcv-out">–<span class="visually-hidden">eliminated</span></td>';
    const share = i === n - 1 && finalTotal ? ` <span class="rcv-pct">${esc(pct((votes / finalTotal) * 100))}</span>` : "";
    return `<td class="num">${esc(fmt.format(votes))}${share}</td>`;
  };
  const body = race.candidates.map(([name, party, votes], row) => `<tr${row === 0 ? ' class="rcv-winner"' : ""}>
    <th scope="row">${party ? `<span class="swatch swatch-${partyClass(party)}"></span>` : ""}${esc(rcvName(name))}${party ? ` <span class="rcv-party">${esc(party)}</span>` : ""}${row === 0 ? ' <span class="rcv-won">won</span>' : ""}</th>
    ${cols.map((i) => cell(votes[i], i, votes.some(Boolean))).join("")}</tr>`).join("");
  return `<div class="rcv-scroll"><table class="rcv-table">
    <caption>${esc(caption)}</caption>
    <thead><tr><th scope="col">Candidate</th>${cols.map((i) => `<th scope="col" class="num">Round ${i + 1}</th>`).join("")}</tr></thead>
    <tbody>${body}<tr class="rcv-exhausted"><th scope="row">Exhausted ballots</th>
    ${cols.map((i) => `<td class="num">${esc(fmt.format(race.exhausted[i]))}</td>`).join("")}</tr></tbody>
  </table></div>`;
}

const PRIMARY_NAMES = { DEM: "Democratic", REP: "Republican" };
const rcvTitle = (race) => {
  const office = race.district ? `${raceName(race.office)}, district ${race.district}` : raceName(race.office);
  const party = race.election_type === "primary" ? race.candidates[0]?.[1] || "" : "";
  return `${office}: ${party ? `${PRIMARY_NAMES[party] || party} ` : ""}${race.election_type}`;
};

const DEFS = `
  <defs>
    <pattern id="map-hatch-other" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <rect width="6" height="6" style="fill: var(--map-other)"></rect>
      <line x1="0" y1="0" x2="0" y2="6" style="stroke: var(--map-hatch); stroke-width: 2"></line>
    </pattern>
    <pattern id="map-hatch-none" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(135)">
      <rect width="6" height="6" style="fill: var(--map-none)"></rect>
      <line x1="0" y1="0" x2="0" y2="6" style="stroke: var(--map-none-line); stroke-width: 1"></line>
    </pattern>
  </defs>`;

export function initMap({ manifest, fetchJSON, stateName, onState, onCounty }) {
  const $ = (id) => document.getElementById(id);
  const els = {
    section: $("map-section"), year: $("map-year"), race: $("map-race"), back: $("map-back"),
    title: $("map-title"), svg: $("map"), tip: $("map-tip"), legend: $("map-legend"),
    note: $("map-note"), table: $("map-table"), rcv: $("map-rcv"),
  };
  const years = (manifest.results || []).map((r) => r.year).sort((a, b) => b - a);
  if (!years.length) return { showState() {} };
  els.section.hidden = false;
  els.year.replaceChildren(...years.map((y) => new Option(String(y), String(y))));
  els.legend.innerHTML = legendHTML();

  let view = { state: null, race: null }; // state null = the whole country
  let nationalChoice = null; // which national race the country view shows
  let tips = new Map(); // shape key -> () => tooltip HTML
  let token = 0; // drops renders that a newer one has overtaken

  const yearInfo = () => manifest.results.find((r) => r.year === Number(els.year.value));
  const rcvYears = new Set((manifest.rcv || []).map((r) => r.year));
  const loadRcv = (year) => (rcvYears.has(year)
    ? fetchJSON(`results/${year}/rcv.json`).then((d) => d.races) : Promise.resolve([]));
  const hasState = (st) => Boolean(yearInfo()?.states.includes(st));

  function setRaces(names, preferred) {
    els.race.replaceChildren(...names.map((n) => new Option(raceName(n), n)));
    if (names.includes(preferred)) els.race.value = preferred;
  }

  async function render() {
    const mine = ++token;
    hideTip();
    const year = Number(els.year.value);
    try {
      if (view.state && hasState(view.state)) await renderState(year, view.state, mine);
      else await renderNation(year, mine);
    } catch (err) {
      if (mine === token) els.note.textContent = `Couldn't load the map: ${err.message}`;
    }
  }

  async function renderNation(year, mine) {
    const [shapes, national, rcv] = await Promise.all([
      fetchJSON("geo/states.json"), fetchJSON(`results/${year}/national.json`), loadRcv(year),
    ]);
    if (mine !== token) return;
    setRaces(yearInfo().national, nationalChoice);
    nationalChoice = els.race.value;
    const byState = national.races[nationalChoice] || {};
    const name = raceName(nationalChoice);
    els.back.hidden = true;
    els.title.textContent = `${name}, ${year}`;
    const rows = [];
    const decidedByRounds = [];
    tips = new Map();
    const paths = Object.values(shapes.shapes).map((s) => {
      const race = byState[s.abbr];
      const totals = race ? race.candidates.map((c) => c[2]) : [];
      // A ranked-choice state is shaded by its final round, the one that decided it.
      const rr = race && rcvRace(rcv, s.abbr, race.office);
      const final = rr && rcvTally(rr);
      const t = race ? final || tally(race.candidates, totals) : null;
      if (race) rows.push([s.name, t]);
      if (final) decidedByRounds.push(s.name);
      tips.set(s.abbr, () => (race
        ? tipHTML(s.name, race.candidates, totals, race.office, rr)
        : `<strong>${esc(s.name)}</strong><div class="tip-sub">No ${esc(name)} race in ${year}</div>`));
      const label = `${s.name}: ${race ? summary(t) : `no ${name} race in ${year}`}`
        + (final ? `, in ranked-choice round ${rounds(rr)}` : "");
      return `<path d="${s.d}" style="fill: ${race ? fill(t) : "var(--map-empty)"}" data-state="${esc(s.abbr)}"
        tabindex="0" role="button" aria-label="${esc(label)}"></path>`;
    });
    draw(shapes.viewBox, paths.join(""), "");
    els.note.textContent = "Pick a state to see who won each of its counties. The stronger the shade, the wider the winning margin."
      + (decidedByRounds.length ? ` ${decidedByRounds.join(" and ")} counted ranked-choice rounds; `
        + "the shading and table use the final round's result." : "");
    els.rcv.innerHTML = "";
    renderTable(`${name} results by state, ${year}`, "State", rows);
  }

  async function renderState(year, st, mine) {
    const [shapes, results, national, allRcv] = await Promise.all([
      fetchJSON(`geo/counties/${st}.json`), fetchJSON(`results/${year}/${st}.json`),
      fetchJSON(`results/${year}/national.json`), loadRcv(year),
    ]);
    if (mine !== token) return;
    const races = results.races.filter(isPartisan);
    // Opening a state shows the same race the country view does, when the state had it.
    const choice = nationalChoice ?? yearInfo().national[0];
    setRaces(races.map((r) => r.office), view.race || national.races[choice]?.[st]?.office);
    const rcv = allRcv.filter((r) => r.state === st);
    for (const option of els.race.options) {
      if (rcvRace(rcv, st, option.value)) option.text += " (ranked-choice)";
    }
    const race = races.find((r) => r.office === els.race.value);
    const rr = race && rcvRace(rcv, st, race.office);
    renderRcv(st, year, rcv, rr);
    els.back.hidden = false;
    tips = new Map();
    if (!race) {
      els.title.textContent = `${stateName(st)}, ${year}`;
      draw(shapes.viewBox, "", `<path class="map-outline" d="${shapes.outline}"></path>`);
      els.note.textContent = `No partisan statewide races in ${stateName(st)} in ${year}.`;
      renderTable("", "County", []);
      return;
    }
    view.race = race.office;
    const name = raceName(race.office);
    els.title.textContent = `${stateName(st)}: ${name}, ${year}`;
    const rows = [];
    const paths = Object.entries(shapes.shapes).map(([fips, s]) => {
      const county = race.counties[fips];
      const countyName = county ? titleCase(county[0]) : s.name;
      const t = county ? tally(race.candidates, county[1]) : null;
      if (county) rows.push([countyName, t]);
      tips.set(fips, () => (county
        ? tipHTML(countyName, race.candidates, county[1])
        : `<strong>${esc(s.name)}</strong><div class="tip-sub">No results reported for this county</div>`));
      const label = `${countyName}: ${county ? summary(t) : "no results"}`;
      return `<path d="${s.d}" style="fill: ${county ? fill(t) : "url(#map-hatch-none)"}" data-county="${esc(fips)}"
        data-name="${esc(countyName)}" tabindex="0" role="button" aria-label="${esc(label)}"></path>`;
    });
    draw(shapes.viewBox, paths.join(""), `<path class="map-outline" d="${shapes.outline}"></path>`);

    const statewide = tally(race.candidates, race.candidates.map((c) => c[2]));
    const notes = rr
      ? [`Statewide first choices: ${summary(statewide)}.`, rcvSummary(rr),
        "County shading shows first choices only: the later rounds are counted statewide, not by county."]
      : [`Statewide: ${summary(statewide)}.`];
    const unassigned = race.counties[""];
    if (unassigned && statewide.total) {
      const share = (unassigned[1].reduce((a, b) => a + b, 0) / statewide.total) * 100;
      notes.push(`${pct(share)} of the votes weren't reported by county (often early and absentee ballots), so county shading leaves them out; the statewide result includes them.`);
    }
    const offMap = Object.keys(race.counties).filter((f) => f && !shapes.shapes[f]);
    if (offMap.length) {
      notes.push(`Not drawn: ${offMap.map((f) => titleCase(race.counties[f][0])).join(", ")}.`);
    }
    els.note.textContent = notes.join(" ");
    renderTable(`${name} results by county, ${stateName(st)} ${year}`, "County", rows);
  }

  function draw(viewBox, paths, overlay) {
    els.svg.setAttribute("viewBox", viewBox.join(" "));
    els.svg.innerHTML = `${DEFS}<g class="map-shapes">${paths}</g>${overlay}`;
  }

  function tipHTML(place, candidates, votes, office, rr) {
    const t = tally(candidates, votes);
    const lines = t.order.slice(0, 4).filter((i) => votes[i] > 0).map((i) => {
      const [name, party] = candidates[i];
      return `<tr><td><span class="swatch swatch-${partyClass(party)}"></span>${esc(titleCase(name))}</td>
        <td>${esc(partyLabel(party))}</td><td class="num">${esc(fmt.format(votes[i]))}</td>
        <td class="num">${esc(pct((votes[i] / t.total) * 100))}</td></tr>`;
    }).join("");
    const head = office ? `<div class="tip-sub">${esc(raceName(office))}</div>` : "";
    if (rr) {
      return `<strong>${esc(place)}</strong>${head}<div class="tip-rcv">${esc(rcvSummary(rr))}</div>
        <div class="tip-sub">First choices: ${esc(summary(t))}</div>${lines ? `<table>${lines}</table>` : ""}`;
    }
    return `<strong>${esc(place)}</strong>${head}<div class="tip-sub">${esc(summary(t))}</div>
      ${lines ? `<table>${lines}</table>` : ""}`;
  }

  // Under a state's map: the shown race's rounds, then the state's other ranked-choice counts.
  function renderRcv(st, year, rcv, shown) {
    const others = rcv.filter((r) => r !== shown);
    const parts = [];
    if (shown) parts.push(rcvTableHTML(shown, `${raceName(shown.office)}, ${stateName(st)} ${year}: votes in each ranked-choice round`));
    if (others.length) {
      parts.push(`<details class="rcv-more"><summary>${shown ? "Other ranked-choice" : "Ranked-choice"} counts in ${esc(stateName(st))}, ${year} (${others.length})</summary>
        ${others.map((r) => `<h3 class="rcv-title">${esc(rcvTitle(r))}</h3><p class="rcv-summary">${esc(rcvSummary(r))}</p>
          ${rcvTableHTML(r, `${rcvTitle(r)}: votes in each round`)}`).join("")}</details>`);
    }
    els.rcv.innerHTML = parts.join("");
  }

  function renderTable(caption, placeHeader, rows) {
    rows.sort((a, b) => a[0].localeCompare(b[0]));
    els.table.innerHTML = `<caption id="map-caption">${esc(caption)}</caption>
      <thead><tr><th scope="col">${esc(placeHeader)}</th><th scope="col">Winner</th><th scope="col">Margin</th></tr></thead>
      <tbody>${rows.map(([place, t]) => `<tr><th scope="row">${esc(place)}</th>
        <td>${t.winner ? `${esc(titleCase(t.winner[0]))} (${esc(partyLabel(t.winner[1]))})` : t.tied ? "Tied" : "No votes"}</td>
        <td class="num">${t.winner ? `${esc(t.margin.toFixed(1))} pts` : ""}</td></tr>`).join("")}</tbody>`;
  }

  // --- tooltip: follows the pointer on hover; a tap or Enter pins it -----
  let pinned = null;
  function showTip(target, x, y) {
    const key = target.dataset.state || target.dataset.county;
    const html = tips.get(key)?.();
    if (!html) return;
    const county = target.dataset.county;
    els.tip.innerHTML = html + (county && pinned === target
      ? `<button type="button" class="tip-link" data-county-name="${esc(target.dataset.name)}">Who's running in ${esc(target.dataset.name)}</button>`
      : !view.state && hasState(target.dataset.state) ? `<div class="tip-hint">Click for counties</div>` : "");
    els.tip.hidden = false;
    const box = els.svg.parentElement.getBoundingClientRect();
    const w = els.tip.offsetWidth, h = els.tip.offsetHeight;
    let left = x - box.left + 14, top = y - box.top + 14;
    if (left + w > box.width) left = Math.max(0, x - box.left - w - 14);
    if (top + h > box.height) top = Math.max(0, y - box.top - h - 14);
    els.tip.style.left = `${left}px`;
    els.tip.style.top = `${top}px`;
  }
  function hideTip() {
    pinned = null;
    els.tip.hidden = true;
    els.svg.querySelector(".is-active")?.classList.remove("is-active");
  }
  const shapeOf = (e) => e.target.closest?.("path[data-state], path[data-county]");

  els.svg.addEventListener("pointermove", (e) => {
    if (pinned) return;
    const target = shapeOf(e);
    if (target) showTip(target, e.clientX, e.clientY);
    else els.tip.hidden = true;
  });
  els.svg.addEventListener("pointerleave", () => { if (!pinned) els.tip.hidden = true; });

  function activate(target, x, y) {
    if (target.dataset.state) {
      if (!hasState(target.dataset.state)) return;
      onState(target.dataset.state); // the page calls showState back
      return;
    }
    els.svg.querySelector(".is-active")?.classList.remove("is-active");
    pinned = target;
    target.classList.add("is-active");
    showTip(target, x, y);
  }
  els.svg.addEventListener("click", (e) => {
    const target = shapeOf(e);
    if (target) activate(target, e.clientX, e.clientY);
    else hideTip();
  });
  els.svg.addEventListener("keydown", (e) => {
    const target = shapeOf(e);
    if (!target || (e.key !== "Enter" && e.key !== " ")) return;
    e.preventDefault();
    const r = target.getBoundingClientRect();
    activate(target, r.left + r.width / 2, r.top + r.height / 2);
  });
  els.svg.addEventListener("focusin", (e) => {
    const target = shapeOf(e);
    if (target && !pinned) {
      const r = target.getBoundingClientRect();
      showTip(target, r.left + r.width / 2, r.top + r.height / 2);
    }
  });
  els.tip.addEventListener("click", (e) => {
    const name = e.target.dataset?.countyName;
    if (name) onCounty(view.state, name);
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideTip(); });

  els.year.addEventListener("change", () => { view.race = null; render(); });
  els.race.addEventListener("change", () => {
    if (view.state) view.race = els.race.value;
    else nationalChoice = els.race.value;
    render();
  });
  els.back.addEventListener("click", () => {
    view = { state: null, race: null };
    render();
  });

  render();

  return {
    // Called when the page's state picker changes.
    showState(st) {
      if (view.state === st) return;
      view = { state: st || null, race: null };
      render();
    },
  };
}

function legendHTML() {
  const ramp = (party) => `<div class="legend-row"><span class="legend-name">${PARTY_NAMES[party]}</span>
    ${STEP_LABELS.map((label, i) => `<span class="legend-step"><span class="legend-swatch" style="background: var(--map-${party}-${i})"></span>${label}</span>`).join("")}</div>`;
  return `${ramp("dem")}${ramp("rep")}
    <div class="legend-row"><span class="legend-step"><span class="legend-swatch legend-other"></span>Other party or independent</span>
    <span class="legend-step"><span class="legend-swatch legend-none"></span>No results</span>
    <span class="legend-step"><span class="legend-swatch legend-empty"></span>No such race that year</span>
    <span class="legend-unit">Winning margin, percentage points</span></div>`;
}
