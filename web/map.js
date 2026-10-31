// Results map: states shaded by who won a statewide race; pick a state to see its counties.
// Shapes are pre-drawn SVG paths (web/geo/, see geo.py); votes come from results/<year>/.

const MARGIN_STEPS = [5, 10, 20]; // percentage-point edges between the four shades
const STEP_LABELS = ["Under 5", "5–10", "10–20", "20+"];

const esc = (value) => String(value).replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[c]);

// MEDSL names are upper case ("DONALD J TRUMP", "ST. LOUIS CITY"); live results' aren't.
const titleCase = (s) => (s !== s.toUpperCase() ? s
  : s.toLowerCase().replace(/(^|[\s\-/.'(])([a-z])/g, (m, p, c) => p + c.toUpperCase()));
const fmt = new Intl.NumberFormat("en-US");
const pct = (n) => `${n.toFixed(1)}%`;
const raceName = (office) => titleCase(office).replace(/^Us /, "US ");

// Live, unofficial results (sources/enr.py) carry {updated, precincts_reported, precincts_total}.
const timeFmt = new Intl.DateTimeFormat("en-US", {
  month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short",
});
function liveText(live) {
  const share = live.precincts_total
    ? `${Math.floor((live.precincts_reported / live.precincts_total) * 100)}% of precincts reporting`
    : "precincts reporting unknown";
  const when = new Date(live.updated);
  return `Unofficial — ${share}${live.updated && !Number.isNaN(when.getTime()) ? `, updated ${timeFmt.format(when)}` : ""}`;
}

// "DEM/WORKING FAMILIES" and "REPUBLICAN/CONSERVATIVE" both count as the major party.
export function partyClass(party) {
  const parts = party.toUpperCase().split("/");
  if (parts.some((p) => p === "DEM" || p === "DFL" || p.startsWith("DEMOCRAT"))) return "dem"; // DFL: Minnesota
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

// Runoffs and primaries are separate contests from the general election.
const STAGE_LABELS = { general: "General", runoff: "Runoff", primary: "Primary", "primary runoff": "Primary runoff" };
const longDate = (iso) => (iso
  ? new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })
  : "");
// A primary (or a runoff between candidates of one party) can't be told apart
// by party color, so its counties are colored by winning candidate.
const byCandidate = (race) => race.stage !== "general" && new Set(race.candidates.map(([, p]) => p)).size <= 1;
const CANDIDATE_COLORS = 3; // the leading three statewide; checked all-pairs for color-vision deficiency
const candidateSlot = (race, candidate) => {
  const i = race.candidates.indexOf(candidate);
  return i >= 0 && i < CANDIDATE_COLORS ? String(i) : "other";
};
const raceId = (race) => (race.stage === "general" ? race.office : [race.stage, race.office, race.party, race.date].join("|"));
const PARTY_LONG = { DEM: "Democratic", REP: "Republican", LIB: "Libertarian", GRE: "Green" };
const partyLong = (party) => (party ? PARTY_LONG[party] || titleCase(party) : "nonpartisan");
// The race to show for a wanted race id or office: the same one, else the same
// office (and party) in another stage, so Georgia's Senate general leads to its runoff.
function pickRace(races, wanted) {
  const [office, party] = wanted.includes("|") ? wanted.split("|").slice(1, 3) : [wanted, undefined];
  return races.find((r) => raceId(r) === wanted)
    || races.find((r) => r.office === office && r.party === party)
    || races.find((r) => r.office === office);
}
const raceLabel = (race) => (race.stage.startsWith("primary") ? `${raceName(race.office)} (${partyLong(race.party)})` : raceName(race.office));

function fill(t, race) {
  if (!t.winner) return "url(#map-hatch-none)";
  if (race && byCandidate(race)) {
    const slot = candidateSlot(race, t.winner);
    return slot === "other" ? "url(#map-hatch-candidate)" : `var(--map-candidate-${slot})`;
  }
  const party = partyClass(t.winner[1]);
  return party === "other" ? "url(#map-hatch-other)" : `var(--map-${party}-${step(t.margin)})`;
}

function summary(t) {
  if (!t.total) return "No votes reported";
  if (t.tied) return "Tied";
  return `${titleCase(t.winner[0])} (${partyLabel(t.winner[1])}) by ${t.margin.toFixed(1)} pts`;
}

// Counties with no results of their own because another county counts their votes.
const COUNTED_WITH = { "15005": "Maui" }; // Kalawao has no polling place; it's a Maui precinct

// Candidates are partisan if any has a party; the map is about which party leads
// (or, in a one-party contest, which candidate).
const isPartisan = (race) => race.candidates.some(([, party]) => party);
const isMapped = (race) => isPartisan(race) || byCandidate(race);

// US territories aren't in the map's shapes, so the country view adds a row of tiles below.
export const TERRITORIES = ["PR", "GU", "VI", "AS", "MP"];
const TILE_LABELS = { PR: "Puerto Rico", GU: "Guam", VI: "US Virgin Islands", AS: "American Samoa", MP: "N. Mariana Islands" };
// Races territories never hold, so an empty tile means "no such race" rather than missing results.
const NO_TERRITORY_RACE = {
  "US President": "Territories don't vote in the general election for president",
  "US Senate": "Territories don't elect US senators",
};

// Ballot measures: choices are always [Yes, No] (see build_site.py), shaded by which leads.
function measureTally([yes, no]) {
  const total = yes + no;
  const side = yes > no ? "yes" : no > yes ? "no" : null;
  return { total, yes, no, side, margin: total ? (Math.abs(yes - no) / total) * 100 : 0 };
}
const measureFill = (m) => (m.side ? `var(--map-${m.side}-${step(m.margin)})` : "url(#map-hatch-none)");
const split = (m) => (m.total
  ? `Yes ${pct((m.yes / m.total) * 100)} · No ${pct((m.no / m.total) * 100)}`
  : "No votes reported");
const measureCount = (n) => `${n} statewide ballot measure${n === 1 ? "" : "s"}`;

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
    <pattern id="map-hatch-candidate" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <rect width="6" height="6" style="fill: var(--map-candidate-other)"></rect>
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
    stage: $("map-stage"), stageField: $("map-stage-field"),
    title: $("map-title"), svg: $("map"), tip: $("map-tip"), legend: $("map-legend"),
    note: $("map-note"), table: $("map-table"), rcv: $("map-rcv"),
  };
  const years = (manifest.results || []).map((r) => r.year).sort((a, b) => b - a);
  if (!years.length) return { showState() {} };
  els.section.hidden = false;
  els.year.replaceChildren(...years.map((y) => {
    const info = manifest.results.find((r) => r.year === y);
    const live = Object.keys(info.live || {}).length;
    const label = !live ? `${y}` : `${y} (${live < info.states.length ? "partly " : ""}unofficial)`;
    return new Option(label, String(y));
  }));
  els.legend.innerHTML = legendHTML();

  let view = { state: null, race: null, stage: "general" }; // state null = the whole country
  let nationalChoice = null; // which national race the country view shows
  let tips = new Map(); // shape key -> () => tooltip HTML
  let token = 0; // drops renders that a newer one has overtaken

  const yearInfo = () => manifest.results.find((r) => r.year === Number(els.year.value));
  const rcvYears = new Set((manifest.rcv || []).map((r) => r.year));
  const loadRcv = (year) => (rcvYears.has(year)
    ? fetchJSON(`results/${year}/rcv.json`).then((d) => d.races) : Promise.resolve([]));
  const hasState = (st) => Boolean(yearInfo()?.states.includes(st));

  // measures: ballot measure ids, listed in their own group after the races.
  function setRaces(names, preferred, labels = names.map(raceName), measures = [], measureLabels = measures.map(raceName)) {
    const options = names.map((n, i) => new Option(labels[i], n));
    if (measures.length) {
      const group = document.createElement("optgroup");
      group.label = "Ballot measures";
      group.append(...measures.map((n, i) => new Option(measureLabels[i], n)));
      options.push(group);
    }
    els.race.replaceChildren(...options);
    if (names.includes(preferred) || measures.includes(preferred)) els.race.value = preferred;
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
    const live = yearInfo().live || {};
    const liveStates = Object.keys(live);
    els.back.hidden = true;
    els.stageField.hidden = true;
    els.legend.innerHTML = legendHTML();
    els.title.textContent = `${name}, ${year}${liveStates.length ? " (unofficial)" : ""}`;
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
      // A state missing from the year altogether had races; its results just aren't published.
      const none = race || hasState(s.abbr) || liveStates.length ? "" : "No results reported for this state";
      const missing = liveStates.length ? `no live ${name} results` : `no ${name} race in ${year}`;
      const measures = national.measures?.[s.abbr]?.length;
      tips.set(s.abbr, () => (race
        ? tipHTML(s.name, race.candidates, totals, race.office, live[s.abbr] ? liveText(live[s.abbr]) : "", undefined, rr)
        : `<strong>${esc(s.name)}</strong><div class="tip-sub">${esc(none || missing[0].toUpperCase() + missing.slice(1))}</div>`)
        + (measures ? `<div class="tip-sub">${esc(measureCount(measures))}</div>` : ""));
      const label = `${s.name}: ${race ? summary(t) : none ? "no results" : missing}${final ? `, in ranked-choice round ${rounds(rr)}` : ""}${live[s.abbr] ? ` (${liveText(live[s.abbr])})` : ""}`;
      const empty = none ? "url(#map-hatch-none)" : "var(--map-empty)";
      return `<path d="${s.d}" style="fill: ${race ? fill(t) : empty}" data-state="${esc(s.abbr)}"
        tabindex="0" role="button" aria-label="${esc(label)}"></path>`;
    });
    const inset = territoryTiles(shapes.viewBox, byState, name, year, rows);
    draw(inset.viewBox, paths.join("") + inset.paths, inset.overlay);
    els.note.textContent = "Pick a state to see who won each of its counties. The stronger the shade, the wider the winning margin."
      + (decidedByRounds.length ? ` ${decidedByRounds.join(" and ")} counted ranked-choice rounds; `
        + "the shading and table use the final round's result." : "");
    if (liveStates.length) {
      const states = new Intl.ListFormat("en-US").format(liveStates.map(stateName));
      els.note.textContent = `Unofficial election-night results from the state results sites of ${states},`
        + ` still being counted: hover a state for how far along it is. `
        + `Other states have no live results here. ${els.note.textContent}`;
    }
    els.rcv.innerHTML = "";
    renderTable(`${name} results by state, ${year}${liveStates.length ? " (unofficial)" : ""}`, "State", rows);
  }

  // A row of tiles under the country, one per territory, that behave like the states.
  function territoryTiles([x, y, w, h], byState, name, year, rows) {
    const top = y + h + 16, height = 48, gap = 10, left = x + 160;
    const width = (w - (left - x) - gap * (TERRITORIES.length - 1)) / TERRITORIES.length;
    const noRace = NO_TERRITORY_RACE[nationalChoice];
    let paths = "";
    let labels = `<line x1="${x}" y1="${y + h + 6}" x2="${x + w}" y2="${y + h + 6}"></line>
      <text class="inset-caption" x="${x + 8}" y="${top + height / 2}">Territories</text>`;
    TERRITORIES.forEach((abbr, i) => {
      const place = stateName(abbr);
      const race = byState[abbr];
      const totals = race ? race.candidates.map((c) => c[2]) : [];
      const t = race ? tally(race.candidates, totals) : null;
      if (race || !noRace) rows.push([place, t]);
      const why = noRace || "No results for territories";
      tips.set(abbr, () => (race
        ? tipHTML(place, race.candidates, totals, race.office)
        : `<strong>${esc(place)}</strong><div class="tip-sub">${esc(why)}</div>`));
      const label = `${place}: ${race ? summary(t) : why.toLowerCase()}`;
      const fillStyle = race ? fill(t) : noRace ? "var(--map-empty)" : "url(#map-hatch-none)";
      const tx = left + i * (width + gap);
      paths += `<path d="M${tx} ${top}h${width}v${height}h${-width}z" style="fill: ${fillStyle}" data-state="${esc(abbr)}"
        tabindex="0" role="button" aria-label="${esc(label)}"></path>`;
      labels += `<text x="${tx + width / 2}" y="${top + height / 2}" text-anchor="middle">
        <tspan class="inset-long">${esc(TILE_LABELS[abbr])}</tspan><tspan class="inset-short">${esc(abbr)}</tspan></text>`;
    });
    return { viewBox: [x, y, w, h + 16 + height + 4], paths, overlay: `<g class="map-inset">${labels}</g>` };
  }

  async function renderState(year, st, mine) {
    const [shapes, results, national, allRcv] = await Promise.all([
      fetchJSON(`geo/counties/${st}.json`), fetchJSON(`results/${year}/${st}.json`),
      fetchJSON(`results/${year}/national.json`), loadRcv(year),
    ]);
    if (mine !== token) return;
    const mapped = results.races.filter((r) => r.measure || isMapped(r));
    // General, runoff, primary: offered only when the state had that contest.
    const stages = Object.keys(STAGE_LABELS).filter((s) => mapped.some((r) => r.stage === s));
    if (!stages.includes(view.stage)) view.stage = stages[0] || "general";
    els.stage.replaceChildren(...stages.map((s) => {
      const dates = [...new Set(mapped.filter((r) => r.stage === s).map((r) => r.date))];
      const when = s !== "general" && dates.length === 1 && dates[0] ? `, ${longDate(dates[0])}` : "";
      return new Option(`${STAGE_LABELS[s]}${when}`, s);
    }));
    els.stage.value = view.stage;
    els.stageField.hidden = !stages.some((s) => s !== "general");
    const races = mapped.filter((r) => r.stage === view.stage);
    // Opening a state shows the same race the country view does, when the state had it.
    const choice = nationalChoice ?? yearInfo().national[0];
    const wanted = pickRace(races, view.race || national.races[choice]?.[st]?.office || "");
    const contests = races.filter((r) => !r.measure);
    const measures = races.filter((r) => r.measure);
    setRaces(contests.map(raceId), wanted && raceId(wanted), contests.map(raceLabel),
      measures.map(raceId), measures.map(raceLabel));
    const rcv = allRcv.filter((r) => r.state === st);
    for (const option of els.race.options) {
      if (rcvRace(rcv, st, option.value)) option.text += " (ranked-choice)";
    }
    const race = races.find((r) => raceId(r) === els.race.value);
    // Only general elections go to ranked-choice rounds on the map.
    const rr = race?.stage === "general" ? rcvRace(rcv, st, race.office) : undefined;
    renderRcv(st, year, rcv, rr);
    els.back.hidden = false;
    tips = new Map();
    els.legend.innerHTML = race?.measure ? measureLegendHTML() : legendHTML();
    if (!race) {
      els.title.textContent = `${stateName(st)}, ${year}`;
      els.legend.innerHTML = legendHTML();
      draw(shapes.viewBox, "", `<path class="map-outline" d="${shapes.outline}"></path>`);
      els.note.textContent = `No partisan statewide races or ballot measures in ${stateName(st)} in ${year}.`;
      renderTable("", "County", []);
      return;
    }
    view.race = raceId(race);
    if (race.measure) {
      renderMeasure(year, st, race, shapes);
      return;
    }
    const name = raceName(race.office);
    const general = race.stage === "general";
    // "US Senate runoff, Dec 6, 2022"; "Governor, Republican primary, Aug 28, 2018"
    const contest = general ? name : `${name}${race.stage.startsWith("primary") ? `, ${partyLong(race.party)}` : ""} ${STAGE_LABELS[race.stage].toLowerCase()}`;
    const when = general ? String(year) : longDate(race.date) || String(year);
    const live = results.live;
    els.title.textContent = `${stateName(st)}: ${contest}, ${when}${live ? " (unofficial)" : ""}`;
    els.legend.innerHTML = byCandidate(race) ? candidateLegendHTML(race) : legendHTML();
    const rows = [];
    const paths = Object.entries(shapes.shapes).map(([fips, s]) => {
      const county = race.counties[fips];
      const countyName = county ? titleCase(county[0]) : s.name;
      const t = county ? tally(race.candidates, county[1]) : null;
      if (county) rows.push([countyName, t]);
      const precincts = live?.counties?.[fips];
      const progress = precincts ? `${precincts[0]} of ${precincts[1]} precincts reporting` : "";
      const missing = COUNTED_WITH[fips] ? `Votes counted with ${COUNTED_WITH[fips]} County` : "No results reported for this county";
      tips.set(fips, () => (county
        ? tipHTML(countyName, race.candidates, county[1], "", progress, race)
        : `<strong>${esc(s.name)}</strong><div class="tip-sub">${esc(missing)}</div>`));
      const label = `${countyName}: ${county ? summary(t) : missing}`;
      return `<path d="${s.d}" style="fill: ${county ? fill(t, race) : "url(#map-hatch-none)"}" data-county="${esc(fips)}"
        data-name="${esc(countyName)}" tabindex="0" role="button" aria-label="${esc(label)}"></path>`;
    });
    draw(shapes.viewBox, paths.join(""), `<path class="map-outline" d="${shapes.outline}"></path>`);

    const statewide = tally(race.candidates, race.candidates.map((c) => c[2]));
    const notes = rr
      ? [`Statewide first choices: ${summary(statewide)}.`, rcvSummary(rr),
        "County shading shows first choices only: the later rounds are counted statewide, not by county."]
      : [`Statewide: ${summary(statewide)}.`];
    if (live) notes.unshift(`${liveText(live)}. Counts change until results are certified.`);
    const unassigned = race.counties[""];
    if (unassigned && statewide.total) {
      const share = (unassigned[1].reduce((a, b) => a + b, 0) / statewide.total) * 100;
      notes.push(`${pct(share)} of the votes weren't reported by county (often early and absentee ballots), so county shading leaves them out; the statewide result includes them.`);
    }
    if (byCandidate(race)) {
      notes.push(race.candidates.length > CANDIDATE_COLORS
        ? "Each county is colored by its winner: the three candidates with the most votes statewide have their own colors, and counties won by anyone else share a gray hatch."
        : "Each county is colored by its winning candidate.");
    }
    const runoff = general && mapped.find((r) => r.stage === "runoff" && r.office === race.office);
    if (runoff) notes.push(`This race went to a runoff${runoff.date ? ` on ${longDate(runoff.date)}` : ""}: pick Runoff under Election to see it.`);
    const offMap = Object.keys(race.counties).filter((f) => f && !shapes.shapes[f]);
    if (offMap.length) {
      notes.push(`Not drawn: ${offMap.map((f) => titleCase(race.counties[f][0])).join(", ")}.`);
    }
    els.note.textContent = notes.join(" ");
    renderTable(`${contest} results by county, ${stateName(st)}, ${when}${live ? " (unofficial)" : ""}`, "County", rows);
  }

  function renderMeasure(year, st, race, shapes) {
    const name = raceName(race.office);
    // A measure on a primary ballot goes by its date, like the primary.
    const when = race.stage === "general" ? String(year) : longDate(race.date) || String(year);
    els.title.textContent = `${stateName(st)}: ${name}, ${when}`;
    const rows = [];
    const paths = Object.entries(shapes.shapes).map(([fips, s]) => {
      const county = race.counties[fips];
      const countyName = county ? titleCase(county[0]) : s.name;
      const m = county ? measureTally(county[1]) : null;
      if (county) rows.push([countyName, m]);
      const missing = COUNTED_WITH[fips] ? `Votes counted with ${COUNTED_WITH[fips]} County` : "No results reported for this county";
      tips.set(fips, () => (county
        ? measureTipHTML(countyName, m)
        : `<strong>${esc(s.name)}</strong><div class="tip-sub">${esc(missing)}</div>`));
      const label = `${countyName}: ${county ? split(m) : missing}`;
      return `<path d="${s.d}" style="fill: ${county ? measureFill(m) : "url(#map-hatch-none)"}" data-county="${esc(fips)}"
        data-name="${esc(countyName)}" tabindex="0" role="button" aria-label="${esc(label)}"></path>`;
    });
    draw(shapes.viewBox, paths.join(""), `<path class="map-outline" d="${shapes.outline}"></path>`);

    const statewide = measureTally(race.candidates.map((c) => c[2]));
    const notes = [`Statewide: ${split(statewide)}.`];
    if (race.outcome) {
      notes.push(`${race.outcome === "passed" ? "Passed" : "Failed"}: it needed ${race.needs} of the Yes and No votes.`);
    } else if (statewide.total) {
      notes.push(`Not marked passed or failed: some ${stateName(st)} measures need a supermajority or count against all ballots cast, which this data can't settle.`);
    }
    const unassigned = race.counties[""];
    if (unassigned && statewide.total) {
      const share = (unassigned[1].reduce((a, b) => a + b, 0) / statewide.total) * 100;
      notes.push(`${pct(share)} of the votes weren't reported by county, so county shading leaves them out; the statewide split includes them.`);
    }
    const offMap = Object.keys(race.counties).filter((f) => f && !shapes.shapes[f]);
    if (offMap.length) {
      notes.push(`Not drawn: ${offMap.map((f) => titleCase(race.counties[f][0])).join(", ")}.`);
    }
    els.note.textContent = notes.join(" ");
    rows.sort((a, b) => a[0].localeCompare(b[0]));
    const share = (m, votes) => (m.total ? esc(pct((votes / m.total) * 100)) : "");
    els.table.innerHTML = `<caption id="map-caption">${esc(`${name} results by county, ${stateName(st)}, ${when}`)}</caption>
      <thead><tr><th scope="col">County</th><th scope="col">Yes</th><th scope="col">No</th><th scope="col">Margin</th></tr></thead>
      <tbody>${rows.map(([place, m]) => `<tr><th scope="row">${esc(place)}</th>
        <td class="num">${share(m, m.yes)}</td><td class="num">${share(m, m.no)}</td>
        <td class="num">${m.side ? `${m.side === "yes" ? "Yes" : "No"} by ${esc(m.margin.toFixed(1))} pts` : m.total ? "Tied" : "No votes"}</td></tr>`).join("")}</tbody>`;
  }

  function measureTipHTML(place, m) {
    const line = (label, cls, votes) => `<tr><td><span class="swatch swatch-${cls}"></span>${label}</td>
      <td class="num">${esc(fmt.format(votes))}</td><td class="num">${esc(pct((votes / m.total) * 100))}</td></tr>`;
    return `<strong>${esc(place)}</strong><div class="tip-sub">${esc(split(m))}</div>
      ${m.total ? `<table>${line("Yes", "yes", m.yes)}${line("No", "no", m.no)}</table>` : ""}`;
  }

  function draw(viewBox, paths, overlay) {
    els.svg.setAttribute("viewBox", viewBox.join(" "));
    els.svg.innerHTML = `${DEFS}<g class="map-shapes">${paths}</g>${overlay}`;
  }

  // progress: for live results, e.g. "12 of 40 precincts reporting".
  // rr: the race's ranked-choice rounds, when it went to them.
  function tipHTML(place, candidates, votes, office, progress, race, rr) {
    const t = tally(candidates, votes);
    const swatch = (i) => (race && byCandidate(race) ? `candidate-${candidateSlot(race, candidates[i])}` : partyClass(candidates[i][1]));
    const lines = t.order.slice(0, 4).filter((i) => votes[i] > 0).map((i) => {
      const [name, party] = candidates[i];
      return `<tr><td><span class="swatch swatch-${swatch(i)}"></span>${esc(titleCase(name))}</td>
        <td>${esc(partyLabel(party))}</td><td class="num">${esc(fmt.format(votes[i]))}</td>
        <td class="num">${esc(pct((votes[i] / t.total) * 100))}</td></tr>`;
    }).join("");
    const head = office ? `<div class="tip-sub">${esc(raceName(office))}</div>` : "";
    if (rr) {
      return `<strong>${esc(place)}</strong>${head}<div class="tip-rcv">${esc(rcvSummary(rr))}</div>
        <div class="tip-sub">First choices: ${esc(summary(t))}</div>${lines ? `<table>${lines}</table>` : ""}`;
    }
    return `<strong>${esc(place)}</strong>${head}<div class="tip-sub">${esc(summary(t))}</div>
      ${progress ? `<div class="tip-sub">${esc(progress)}</div>` : ""}
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
        <td>${!t ? "No results" : t.winner ? `${esc(titleCase(t.winner[0]))} (${esc(partyLabel(t.winner[1]))})` : t.tied ? "Tied" : "No votes"}</td>
        <td class="num">${t?.winner ? `${esc(t.margin.toFixed(1))} pts` : ""}</td></tr>`).join("")}</tbody>`;
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

  els.year.addEventListener("change", () => { view.race = null; view.stage = "general"; render(); });
  els.stage.addEventListener("change", () => {
    view.race = els.race.value; // pickRace finds the same office in the new stage
    view.stage = els.stage.value;
    render();
  });
  els.race.addEventListener("change", () => {
    if (view.state) view.race = els.race.value;
    else nationalChoice = els.race.value;
    render();
  });
  els.back.addEventListener("click", () => {
    view = { state: null, race: null, stage: "general" };
    render();
  });

  render();

  return {
    // Called when the page's state picker changes.
    showState(st) {
      if (view.state === st) return;
      view = { state: st || null, race: null, stage: "general" };
      render();
    },
  };
}

// One swatch per leading candidate, named, so color is never the only cue.
function candidateLegendHTML(race) {
  const shown = race.candidates.slice(0, CANDIDATE_COLORS);
  const others = race.candidates.length > CANDIDATE_COLORS
    ? `<span class="legend-step"><span class="legend-swatch legend-candidate-other"></span>Another candidate</span>` : "";
  return `<div class="legend-row"><span class="legend-name">County winner</span>
    ${shown.map(([name], i) => `<span class="legend-step"><span class="legend-swatch" style="background: var(--map-candidate-${i})"></span>${esc(titleCase(name))}</span>`).join("")}
    ${others}<span class="legend-step"><span class="legend-swatch legend-none"></span>No results</span></div>`;
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

function measureLegendHTML() {
  const ramp = (side, label) => `<div class="legend-row"><span class="legend-name">${label}</span>
    ${STEP_LABELS.map((step, i) => `<span class="legend-step"><span class="legend-swatch" style="background: var(--map-${side}-${i})"></span>${step}</span>`).join("")}</div>`;
  return `${ramp("yes", "Yes leads")}${ramp("no", "No leads")}
    <div class="legend-row"><span class="legend-step"><span class="legend-swatch legend-none"></span>No results or tied</span>
    <span class="legend-unit">Margin between Yes and No, percentage points</span></div>`;
}
