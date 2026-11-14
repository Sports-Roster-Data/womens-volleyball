/* Volleyball Roster Atlas — static site over data/derived/atlas exports. */
(() => {
  "use strict";
  const app = document.getElementById("app");
  const cache = new Map();
  const DIVS = ["I", "II", "III"];
  const DIV_LABEL = { I: "Division I", II: "Division II", III: "Division III" };

  // ---------- helpers -------------------------------------------------------
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const divColor = (d) => css({ I: "--d1", II: "--d2", III: "--d3" }[d] || "--text-muted");
  const pct = (v, digits = 0) => (v == null ? "—" : `${(v * 100).toFixed(digits)}%`);
  const num = (v) => (v == null ? "—" : d3.format(",")(Math.round(v)));
  const miles = (v) => (v == null ? "—" : `${d3.format(",")(Math.round(v))} mi`);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const short = (season) => season.slice(2, 4) + "–" + season.slice(5);

  async function get(path) {
    if (!cache.has(path)) {
      cache.set(path, fetch(path).then((r) => {
        if (!r.ok) throw new Error(`${path}: ${r.status}`);
        return r.json();
      }));
    }
    return cache.get(path);
  }

  function el(html) {
    const t = document.createElement("template");
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  }

  function mount(container, node) {
    container.replaceChildren(node);
  }

  function baseStyle() {
    return {
      background: "transparent",
      color: css("--text-secondary"),
      fontSize: "12px",
      overflow: "visible",
    };
  }

  function widthOf(node, fallback = 520) {
    return Math.max(280, Math.floor(node.getBoundingClientRect().width) || fallback);
  }

  function legend(items) {
    return `<div class="legend">${items.map(([label, color]) =>
      `<span style="--c:${color}">${esc(label)}</span>`).join("")}</div>`;
  }

  function divLegend() {
    return legend(DIVS.map((d) => [DIV_LABEL[d], divColor(d)]));
  }

  // line chart by division with crosshair tip
  function divisionLines(container, rows, y, { yLabel, format, domain } = {}) {
    const w = widthOf(container);
    const data = rows.filter((r) => DIVS.includes(r.division) && r[y] != null);
    const seasons = [...new Set(data.map((r) => r.season))].sort();
    const chart = Plot.plot({
      width: w, height: 260, marginLeft: 52, marginRight: 16, marginBottom: 34,
      style: baseStyle(),
      x: { type: "point", domain: seasons, label: null, tickFormat: short,
           ticks: w < 520 ? seasons.filter((_, i) => i % 3 === 0) : seasons },
      y: { grid: true, label: yLabel, tickFormat: format, domain, nice: true },
      color: { domain: DIVS, range: DIVS.map(divColor) },
      marks: [
        Plot.ruleY(domain ? [domain[0]] : [0], { stroke: css("--border") }),
        Plot.line(data, { x: "season", y, stroke: "division", strokeWidth: 2 }),
        Plot.dot(data, Plot.pointerX({ x: "season", y, fill: "division", r: 4,
          stroke: css("--surface-1"), strokeWidth: 2 })),
        Plot.ruleX(data, Plot.pointerX({ x: "season", stroke: css("--border") })),
        Plot.tip(data, Plot.pointerX({ x: "season", y, channels: { Division: "division" },
          format: { x: true, y: (v) => (format ? format(v) : v), stroke: false, fill: false } })),
        Plot.text(data.filter((r) => r.season === seasons[seasons.length - 1]),
          { x: "season", y, text: (r) => `D-${r.division}`, dx: 6, textAnchor: "start",
            fill: css("--text-secondary"), fontSize: 11 }),
      ],
      marginRight: 40,
    });
    mount(container, chart);
  }

  function table(columns, rows, { empty = "None in the data." } = {}) {
    if (!rows.length) return `<p class="muted small">${empty}</p>`;
    const head = columns.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("");
    const body = rows.map((r) => `<tr>${columns.map((c) =>
      `<td class="${c.num ? "num" : ""}">${c.html ? c.html(r) : esc(c.get(r))}</td>`).join("")}</tr>`).join("");
    return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  const progLink = (id, name) => (id ? `<a href="#/program/${esc(id)}">${esc(name)}</a>` : esc(name));

  // ---------- map layers ----------------------------------------------------
  async function usStates() {
    const topo = await get("vendor/states-10m.json");
    return topojson.feature(topo, topo.objects.states);
  }
  async function world() {
    const topo = await get("vendor/countries-110m.json");
    return topojson.feature(topo, topo.objects.land);
  }

  // ---------- overview ------------------------------------------------------
  async function overview() {
    const o = await get("data/overview.json");
    const first = o.seasons[0], last = o.seasons[o.seasons.length - 1];
    const d = (div, s) => o.division.find((r) => r.division === div && r.season === s) || {};
    const d1a = d("I", first), d1b = d("I", last);
    const tByYear = d3.rollups(o.transfers.filter((t) => DIVS.includes(t.to_division)),
      (v) => d3.sum(v, (x) => x.n), (x) => x.to_first_season);
    const tFirst = tByYear.find(([s]) => s === o.seasons[1])?.[1];
    const tLast = tByYear.find(([s]) => s === last)?.[1];

    const page = el(`<section>
      <h1>How far do college volleyball players travel?</h1>
      <p class="lede">${num(o.totals.player_seasons)} roster spots across ${num(o.totals.programs)} NCAA women's volleyball programs, ${short(first)} to ${short(last)}, traced back to each player's hometown and linked across seasons to follow ${num(o.totals.people)} individual players.</p>
      <div class="tiles">
        <div class="tile"><div class="k">D-I median distance from home</div><div class="v">${miles(d1b.median_miles)}</div><div class="d">up from ${miles(d1a.median_miles)} in ${first}</div></div>
        <div class="tile"><div class="k">D-I players from the campus's state</div><div class="v">${pct(d1b.in_state_share)}</div><div class="d">down from ${pct(d1a.in_state_share)} in ${first}</div></div>
        <div class="tile"><div class="k">D-I players from outside the U.S.</div><div class="v">${pct(d1b.international_share, 1)}</div><div class="d">up from ${pct(d1a.international_share, 1)} in ${first}</div></div>
        <div class="tile"><div class="k">Linked transfers arriving per season</div><div class="v">${num(tLast)}</div><div class="d">vs ${num(tFirst)} in ${o.seasons[1]}</div></div>
      </div>
      <div class="grid2">
        <div class="card"><h2>Median miles from hometown to campus</h2><p class="sub">Half of each division's players come from closer than this.</p>${divLegend()}<div class="chart" id="c-miles"></div></div>
        <div class="card"><h2>Share of players from the campus's own state</h2><p class="sub">U.S. players with a known home state.</p>${divLegend()}<div class="chart" id="c-instate"></div></div>
        <div class="card"><h2>Share of players from outside the U.S.</h2><p class="sub">Players with a known home country.</p>${divLegend()}<div class="chart" id="c-intl"></div></div>
        <div class="card"><h2>Linked transfers by destination division</h2><p class="sub">Players found on a different NCAA roster after leaving one, counted in their first season at the new school.</p>${divLegend()}<div class="chart" id="c-transfers"></div><p class="note">Counts only confirmed and probable links. Transfers from junior colleges, NAIA schools or before 2015-16 are not included here.</p></div>
        <div class="card"><h2>What happened to each freshman class</h2><p class="sub">Division I freshmen by entering season. Later classes are still playing, so their outcomes are incomplete.</p><div class="chart" id="c-cohort"></div></div>
        <div class="card"><h2>Share of freshmen who later transferred</h2><p class="sub">Classes that entered by 2022-23, the last with at least four seasons observable.</p>${divLegend()}<div class="chart" id="c-cohort-rate"></div></div>
      </div>
    </section>`);
    mount(app, page);

    divisionLines(page.querySelector("#c-miles"), o.division, "median_miles", { yLabel: "miles", format: d3.format(",") });
    divisionLines(page.querySelector("#c-instate"), o.division, "in_state_share", { yLabel: "share", format: d3.format(".0%") });
    divisionLines(page.querySelector("#c-intl"), o.division, "international_share", { yLabel: "share", format: d3.format(".0%") });

    const tr = d3.rollups(o.transfers.filter((t) => DIVS.includes(t.to_division)),
      (v) => d3.sum(v, (x) => x.n), (x) => x.to_first_season, (x) => x.to_division)
      .flatMap(([season, ds]) => ds.map(([division, n]) => ({ season, division, n })));
    divisionLines(page.querySelector("#c-transfers"), tr, "n", { yLabel: "players", format: d3.format(",") });

    // stacked outcomes for D-I freshmen
    const OUT = [["completed", "Played 4+ seasons", css("--d1")], ["transferred", "Transferred", css("--d2")],
                 ["left", "Left the data", css("--text-muted")], ["still_on_roster", "Still on roster", css("--neutral")]];
    const coh = o.cohorts.filter((c) => c.division === "I").flatMap((c) =>
      OUT.map(([k, label]) => ({ cohort: c.cohort, outcome: label, n: c[k], share: c[k] / c.freshmen })));
    const cc = page.querySelector("#c-cohort");
    const w = widthOf(cc);
    cc.insertAdjacentHTML("beforebegin", legend(OUT.map(([, l, c]) => [l, c])));
    mount(cc, Plot.plot({
      width: w, height: 260, marginLeft: 44, marginBottom: 34, style: baseStyle(),
      x: { label: null, tickFormat: (d, i) => (w < 700 && i % 2 ? "" : short(d)), type: "band", padding: 0.18 },
      y: { label: "share of class", tickFormat: d3.format(".0%"), grid: true },
      color: { domain: OUT.map((x) => x[1]), range: OUT.map((x) => x[2]) },
      marks: [
        Plot.barY(coh, { x: "cohort", y: "share", fill: "outcome", order: OUT.map((x) => x[1]),
          stroke: css("--surface-1"), strokeWidth: 1, tip: { format: { y: d3.format(".0%"), x: true } },
          channels: { Players: "n" } }),
        Plot.ruleY([0], { stroke: css("--border") }),
      ],
    }));
    const rate = o.cohorts.filter((c) => c.seasons_observable >= 5)
      .map((c) => ({ season: c.cohort, division: c.division, rate: c.transferred / c.freshmen }));
    divisionLines(page.querySelector("#c-cohort-rate"), rate, "rate", { yLabel: "share", format: d3.format(".0%") });
  }

  // ---------- programs ------------------------------------------------------
  async function programs() {
    const list = await get("data/programs.json");
    const page = el(`<section>
      <h1>Programs</h1>
      <p class="lede">Pick a program to see where its players come from, how its recruiting radius has changed, and who transferred in and out.</p>
      <div class="controls">
        <label>Search<input type="search" id="q" placeholder="Team name" autocomplete="off"></label>
        <label>Division<select id="dv"><option value="">All</option>${DIVS.map((d) => `<option>${d}</option>`).join("")}</select></label>
        <label>State<select id="st"><option value="">All</option>${[...new Set(list.map((p) => p.campus_state).filter(Boolean))].sort().map((s) => `<option>${s}</option>`).join("")}</select></label>
      </div>
      <div class="card"><div class="chart" id="pmap"></div><p class="note">Each dot is a campus; size shows the program's median distance from home in its latest season. Select one to open it.</p></div>
      <div class="prog-list" id="plist"></div>
    </section>`);
    mount(app, page);
    const states = await usStates();
    const draw = () => {
      const q = page.querySelector("#q").value.trim().toLowerCase();
      const dv = page.querySelector("#dv").value, st = page.querySelector("#st").value;
      const rows = list.filter((p) => (!q || p.team.toLowerCase().includes(q))
        && (!dv || p.division === dv) && (!st || p.campus_state === st))
        .sort((a, b) => a.team.localeCompare(b.team));
      page.querySelector("#plist").innerHTML = rows.map((p) =>
        `<a href="#/program/${p.ncaa_id}">${esc(p.team)} <span class="div">D-${esc(p.division)} · ${esc(p.campus_state)}</span></a>`).join("")
        || `<p class="muted">No programs match.</p>`;
      const m = page.querySelector("#pmap");
      const w = widthOf(m, 900);
      const pts = rows.filter((p) => p.lat != null && p.division && DIVS.includes(p.division));
      const chart = Plot.plot({
        width: w, height: Math.round(w * 0.58), projection: "albers-usa", style: baseStyle(),
        color: { domain: DIVS, range: DIVS.map(divColor) },
        r: { range: [2, 9] },
        marks: [
          Plot.geo(states, { fill: css("--land"), stroke: css("--land-line"), strokeWidth: 1 }),
          Plot.dot(pts, { x: "lon", y: "lat", r: "median_miles", fill: "division", fillOpacity: 0.75,
            stroke: css("--surface-1"), strokeWidth: 1,
            channels: { Program: "team", Division: "division", "Median miles": "median_miles",
                        "In-state": (p) => pct(p.in_state_share), "International": (p) => pct(p.international_share, 1) },
            tip: { format: { x: false, y: false, r: false, fill: false } }, href: (p) => `#/program/${p.ncaa_id}` }),
        ],
      });
      chart.querySelectorAll("a").forEach((a) => a.setAttribute("target", "_self"));
      mount(m, chart);
    };
    page.querySelector("#q").addEventListener("input", draw);
    page.querySelector("#dv").addEventListener("change", draw);
    page.querySelector("#st").addEventListener("change", draw);
    draw();
  }

  async function program(id) {
    const [p, list] = await Promise.all([get(`data/program/${id}.json`), get("data/programs.json")]);
    const byId = new Map(list.map((x) => [x.ncaa_id, x]));
    const seasons = p.seasons.map((s) => s.season);
    const last = p.seasons[p.seasons.length - 1], first = p.seasons[0];
    const page = el(`<section>
      <p class="small"><a href="#/programs">← All programs</a></p>
      <h1>${esc(p.team)}</h1>
      <p class="lede">Division ${esc(p.division)} · ${esc(p.state || "outside the U.S.")}${p.conference ? ` · ${esc(p.conference)} (current conference)` : ""} · rosters ${short(first.season)} to ${short(last.season)}</p>
      <div class="tiles">
        <div class="tile"><div class="k">Median miles from home, ${last.season}</div><div class="v">${miles(last.median_miles)}</div><div class="d">${first.season}: ${miles(first.median_miles)}</div></div>
        <div class="tile"><div class="k">Players from ${esc(p.state || "home state")}, ${last.season}</div><div class="v">${pct(last.in_state_share)}</div><div class="d">${first.season}: ${pct(first.in_state_share)}</div></div>
        <div class="tile"><div class="k">International, ${last.season}</div><div class="v">${pct(last.international_share)}</div><div class="d">${last.home_countries} countries incl. U.S.</div></div>
        <div class="tile"><div class="k">Transfers in / out</div><div class="v">${p.transfers_in.length} / ${p.transfers_out.length}</div><div class="d">linked NCAA moves, all seasons</div></div>
      </div>
      <div class="card">
        <div class="controls"><label>Season<select id="ps"><option value="all">All seasons</option>${seasons.slice().reverse().map((s) => `<option>${s}</option>`).join("")}</select></label></div>
        <h2>Where the roster comes from</h2><p class="sub">Dots are hometowns, sized by number of players; the ring marks campus. Players from outside the U.S. are listed below the map.</p>
        <div class="chart" id="cmap"></div><div id="outside" class="small muted"></div>
      </div>
      <div class="grid2">
        <div class="card"><h2>Median miles from home</h2><div class="chart" id="cm"></div></div>
        <div class="card"><h2>In-state and international share</h2>${legend([["In-state", css("--d1")], ["International", css("--d2")]])}<div class="chart" id="cs"></div></div>
      </div>
      <div class="grid2">
        <div class="card"><h2>Transferred in</h2>${table([
          { label: "Season", get: (r) => r[0] },
          { label: "Player", get: (r) => r[4] },
          { label: "From", html: (r) => progLink(r[1], r[2]) + ` <span class="pill">D-${esc(r[3])}</span>` },
          { label: "Link", html: (r) => `<span class="pill">${esc(r[5])}</span>` },
        ], p.transfers_in.slice().reverse())}
        ${p.reported_in.length ? `<p class="note">Rosters also name ${p.reported_in.length} previous school(s) the linker couldn't match to an NCAA roster in the data, e.g. ${esc([...new Set(p.reported_in.map((r) => r[2]))].slice(0, 4).join(", "))}.</p>` : ""}</div>
        <div class="card"><h2>Transferred out</h2>${table([
          { label: "Season", get: (r) => r[0] },
          { label: "Player", get: (r) => r[4] },
          { label: "To", html: (r) => progLink(r[1], r[2]) + ` <span class="pill">D-${esc(r[3])}</span>` },
          { label: "Link", html: (r) => `<span class="pill">${esc(r[5])}</span>` },
        ], p.transfers_out.slice().reverse())}</div>
      </div>
      <div class="grid2">
        <div class="card"><h2>Freshman classes</h2>${table([
          { label: "Entered", get: (r) => r.cohort },
          { label: "Freshmen", get: (r) => r.freshmen, num: true },
          { label: "Back for 2nd season", get: (r) => pct(r.retained_2nd_season), num: true },
          { label: "Reached 4th", get: (r) => pct(r.retained_4th_season), num: true },
          { label: "Transferred", get: (r) => r.transferred, num: true },
        ], p.cohorts.slice().reverse())}</div>
        <div class="card"><h2>Recruits like ${esc(p.team)}</h2><p class="sub">Programs whose last three seasons drew from the most similar mix of states and countries.</p>${table([
          { label: "Program", html: (r) => progLink(r[0], byId.get(r[0])?.team || r[0]) },
          { label: "Division", get: (r) => byId.get(r[0])?.division || "" },
          { label: "Similarity", get: (r) => r[1].toFixed(2), num: true },
        ], p.neighbours)}
        <h2 style="margin-top:16px">International players</h2>${table([
          { label: "Country", get: (r) => r.country },
          { label: "Player-seasons", get: (r) => r.n, num: true },
          { label: "Seasons", get: (r) => r.seasons },
        ], d3.rollups(p.international, (v) => ({ n: d3.sum(v, (x) => x.players),
          seasons: `${short(d3.min(v, (x) => x.season))} to ${short(d3.max(v, (x) => x.season))}` }), (x) => x.country_name)
          .map(([country, v]) => ({ country, ...v })).sort((a, b) => b.n - a.n), { empty: "No international players in the data." })}</div>
      </div>
    </section>`);
    mount(app, page);

    const states = await usStates();
    const drawMap = () => {
      const s = page.querySelector("#ps").value;
      const pts = d3.rollups(p.points.filter((x) => s === "all" || x[0] === s),
        (v) => ({ lat: v[0][1], lon: v[0][2], origin: v[0][3], n: d3.sum(v, (x) => x[4]) }),
        (x) => `${x[1]},${x[2]}`).map(([, v]) => v);
      const us = pts.filter((x) => /^[A-Z]{2}$/.test(x.origin));
      const outside = d3.rollups(pts.filter((x) => !/^[A-Z]{2}$/.test(x.origin)), (v) => d3.sum(v, (x) => x.n), (x) => x.origin)
        .sort((a, b) => b[1] - a[1]);
      page.querySelector("#outside").textContent = outside.length
        ? `Outside the U.S.: ${outside.map(([c, n]) => `${c} (${n})`).join(", ")}` : "";
      const m = page.querySelector("#cmap");
      const w = widthOf(m, 900);
      const chart = Plot.plot({
        width: w, height: Math.round(w * 0.58), projection: "albers-usa", style: baseStyle(),
        r: { range: [2.5, 14] },
        marks: [
          Plot.geo(states, { fill: css("--land"), stroke: css("--land-line"), strokeWidth: 1 }),
          Plot.dot(us, { x: "lon", y: "lat", r: "n", fill: css("--d1"), fillOpacity: 0.7,
            stroke: css("--surface-1"), strokeWidth: 1, channels: { From: "origin", Players: "n" },
            tip: { format: { x: false, y: false, r: false } } }),
          p.lat != null ? Plot.dot([p], { x: "lon", y: "lat", r: 8, stroke: css("--campus"), strokeWidth: 2.5, fill: "none" }) : null,
        ],
      });
      mount(m, chart);
    };
    page.querySelector("#ps").addEventListener("change", drawMap);
    drawMap();

    const single = (container, rows, y, color, fmt) => {
      const w = widthOf(container);
      mount(container, Plot.plot({
        width: w, height: 220, marginLeft: 52, marginBottom: 34, style: baseStyle(),
        x: { type: "point", label: null, tickFormat: short, ticks: w < 520 ? seasons.filter((_, i) => i % 3 === 0) : seasons },
        y: { grid: true, label: null, tickFormat: fmt, nice: true, zero: true },
        marks: [
          Plot.ruleY([0], { stroke: css("--border") }),
          ...y.map((k, i) => Plot.line(rows, { x: "season", y: k, stroke: color[i], strokeWidth: 2 })),
          ...y.map((k, i) => Plot.dot(rows, { x: "season", y: k, fill: color[i], r: 3 })),
          Plot.tip(rows, Plot.pointerX({ x: "season", y: y[0],
            channels: Object.fromEntries(y.map((k) => [k.replace(/_/g, " "), (r) => fmt(r[k])])),
            format: { y: false } })),
        ],
      }));
    };
    single(page.querySelector("#cm"), p.seasons, ["median_miles"], [css("--d1")], d3.format(","));
    single(page.querySelector("#cs"), p.seasons, ["in_state_share", "international_share"], [css("--d1"), css("--d2")], d3.format(".0%"));
  }

  // ---------- states --------------------------------------------------------
  async function states(code) {
    const s = await get("data/states.json");
    const allStates = Object.keys(s.names).filter((k) => s.balance.some((b) => b.state === k)).sort();
    code = code && allStates.includes(code) ? code : "TX";
    const seasons = [...new Set(s.balance.map((b) => b.season))].sort();
    const name = s.names[code] || code;
    const page = el(`<section>
      <h1>${esc(name)}: who stays, who leaves</h1>
      <p class="lede">Players a state produces, compared with the roster spots its own programs fill, and where its players end up.</p>
      <div class="controls">
        <label>State<select id="sc">${allStates.map((k) => `<option value="${k}" ${k === code ? "selected" : ""}>${esc(s.names[k] || k)}</option>`).join("")}</select></label>
        <label>Season<select id="ss">${seasons.slice().reverse().map((x) => `<option>${x}</option>`).join("")}</select></label>
        <label>Division<select id="sd"><option value="">All</option>${DIVS.map((d) => `<option>${d}</option>`).join("")}</select></label>
      </div>
      <div class="tiles" id="stiles"></div>
      <div class="grid2">
        <div class="card"><h2>Where ${esc(name)} players go</h2><p class="sub">Campus states of players from ${esc(name)}, shaded by count.</p><div class="chart" id="smap-out"></div></div>
        <div class="card"><h2>Where ${esc(name)} programs recruit</h2><p class="sub">Home states of players on ${esc(name)} rosters.</p><div class="chart" id="smap-in"></div></div>
      </div>
      <div class="card"><h2>Players produced vs. roster spots filled</h2>${legend([["Players from the state", css("--d1")], ["Roster spots at its programs", css("--d2")]])}<div class="chart" id="sbal"></div><p class="note">A state above the orange line exports players; below it, its programs import them.</p></div>
      <div class="card"><h2>All states, ${esc(seasons[seasons.length - 1])}</h2><div id="stable"></div></div>
    </section>`);
    mount(app, page);
    page.querySelector("#sc").addEventListener("change", (e) => { location.hash = `#/states/${e.target.value}`; });
    const topo = await usStates();
    const nameToCode = Object.fromEntries(Object.entries(s.names).map(([k, v]) => [v, k]));

    const draw = () => {
      const season = page.querySelector("#ss").value, dv = page.querySelector("#sd").value;
      const f = s.flows.filter((x) => x.season === season && (!dv || x.division === dv));
      const out = d3.rollup(f.filter((x) => x.home_state === code), (v) => d3.sum(v, (x) => x.players), (x) => x.campus_state);
      const inn = d3.rollup(f.filter((x) => x.campus_state === code), (v) => d3.sum(v, (x) => x.players), (x) => x.home_state);
      const produced = d3.sum(out.values()), hosted = d3.sum(inn.values()), stayed = out.get(code) || 0;
      page.querySelector("#stiles").innerHTML = `
        <div class="tile"><div class="k">Players from ${esc(name)}</div><div class="v">${num(produced)}</div><div class="d">on NCAA rosters, ${season}${dv ? `, D-${dv}` : ""}</div></div>
        <div class="tile"><div class="k">Stayed in ${esc(name)}</div><div class="v">${pct(produced ? stayed / produced : null)}</div><div class="d">${num(stayed)} players</div></div>
        <div class="tile"><div class="k">Roster spots at ${esc(name)} programs</div><div class="v">${num(hosted)}</div><div class="d">${pct(hosted ? stayed / hosted : null)} filled by in-state players</div></div>
        <div class="tile"><div class="k">Net</div><div class="v">${produced - hosted >= 0 ? "+" : ""}${num(produced - hosted)}</div><div class="d">${produced >= hosted ? "net exporter" : "net importer"}</div></div>`;
      const choro = (container, counts) => {
        const w = widthOf(container);
        const max = d3.max(counts.values()) || 1;
        mount(container, Plot.plot({
          width: w, height: Math.round(w * 0.62), projection: "albers-usa", style: baseStyle(),
          color: { type: "sqrt", domain: [0, max], range: [css("--seq-0"), css("--seq-1")], legend: true, label: "players" },
          marks: [
            Plot.geo(topo, { fill: css("--land"), stroke: css("--land-line") }),
            Plot.geo(topo.features.filter((ft) => counts.get(nameToCode[ft.properties.name])), {
              fill: (ft) => counts.get(nameToCode[ft.properties.name]), stroke: css("--land-line"),
              channels: { State: (ft) => ft.properties.name, Players: (ft) => counts.get(nameToCode[ft.properties.name]) },
              tip: { format: { fill: false } } }),
            Plot.geo(topo.features.filter((ft) => nameToCode[ft.properties.name] === code), { fill: "none", stroke: css("--campus"), strokeWidth: 2 }),
          ],
        }));
      };
      choro(page.querySelector("#smap-out"), out);
      choro(page.querySelector("#smap-in"), inn);
    };
    page.querySelector("#ss").addEventListener("change", draw);
    page.querySelector("#sd").addEventListener("change", draw);
    draw();

    const bal = s.balance.filter((b) => b.state === code);
    const bc = page.querySelector("#sbal"), w = widthOf(bc);
    mount(bc, Plot.plot({
      width: w, height: 240, marginLeft: 52, marginBottom: 34, style: baseStyle(),
      x: { type: "point", label: null, tickFormat: short, ticks: w < 520 ? seasons.filter((_, i) => i % 3 === 0) : seasons },
      y: { grid: true, label: "players", zero: true, nice: true },
      marks: [
        Plot.ruleY([0], { stroke: css("--border") }),
        Plot.line(bal, { x: "season", y: "produced", stroke: css("--d1"), strokeWidth: 2 }),
        Plot.line(bal, { x: "season", y: "roster_spots", stroke: css("--d2"), strokeWidth: 2 }),
        Plot.tip(bal, Plot.pointerX({ x: "season", y: "produced",
          channels: { Produced: "produced", "Roster spots": "roster_spots", "Stayed home": "stayed_home" }, format: { y: false } })),
      ],
    }));
    const lastS = seasons[seasons.length - 1];
    const rows = s.balance.filter((b) => b.season === lastS && s.names[b.state]).sort((a, b) => b.net_export - a.net_export);
    page.querySelector("#stable").innerHTML = table([
      { label: "State", html: (r) => `<a href="#/states/${r.state}">${esc(s.names[r.state])}</a>` },
      { label: "Players produced", get: (r) => num(r.produced), num: true },
      { label: "Roster spots", get: (r) => num(r.roster_spots), num: true },
      { label: "Net", get: (r) => (r.net_export > 0 ? "+" : "") + num(r.net_export), num: true },
      { label: "Stay home", get: (r) => pct(r.stay_home_share), num: true },
    ], rows);
  }

  // ---------- transfers -----------------------------------------------------
  async function transfers() {
    const rows = await get("data/transfers.json");
    const seasons = [...new Set(rows.map((r) => r[0]))].sort();
    const page = el(`<section>
      <h1>Transfers</h1>
      <p class="lede">${num(rows.length)} moves between NCAA programs, found by matching players across rosters by name plus hometown, high school, class and height. <span class="pill">confirmed</span> links are backed by the roster's own previous-school note or a matching high school and hometown; <span class="pill">probable</span> links match on one of those.</p>
      <div class="card"><h2>Moves between divisions</h2><p class="sub">Each cell counts transfers from one division to another, for the selected season or all seasons.</p>
        <div class="controls"><label>Season<select id="ts"><option value="">All seasons</option>${seasons.slice().reverse().map((s) => `<option>${s}</option>`).join("")}</select></label></div>
        <div class="chart" id="tmx"></div></div>
      <div class="card">
        <div class="controls">
          <label>Search player or school<input type="search" id="tq" placeholder="e.g. Nebraska" autocomplete="off"></label>
          <label>Tier<select id="tt"><option value="">All</option><option>confirmed</option><option>probable</option></select></label>
        </div>
        <div id="ttab"></div>
        <div class="pager"><button id="prev" type="button">Previous</button><span id="pinfo"></span><button id="next" type="button">Next</button></div>
      </div>
    </section>`);
    mount(app, page);
    const drawMatrix = () => {
      const s = page.querySelector("#ts").value;
      const f = rows.filter((r) => (!s || r[0] === s) && DIVS.includes(r[4]) && DIVS.includes(r[8]));
      const cells = d3.rollups(f, (v) => v.length, (r) => r[4], (r) => r[8])
        .flatMap(([from, tos]) => tos.map(([to, n]) => ({ from: `From D-${from}`, to: `To D-${to}`, n })));
      const c = page.querySelector("#tmx");
      const w = Math.min(widthOf(c), 520);
      mount(c, Plot.plot({
        width: w, height: 230, marginLeft: 80, marginTop: 30, style: baseStyle(),
        x: { axis: "top", label: null, domain: DIVS.map((d) => `To D-${d}`) },
        y: { label: null, domain: DIVS.map((d) => `From D-${d}`) },
        color: { type: "sqrt", range: [css("--seq-0"), css("--seq-1")] },
        marks: [
          Plot.cell(cells, { x: "to", y: "from", fill: "n", inset: 1, rx: 4, tip: true }),
          Plot.text(cells, { x: "to", y: "from", text: (d) => d3.format(",")(d.n),
            fill: (d) => (d.n > d3.max(cells, (x) => x.n) * 0.35 ? css("--surface-1") : css("--text-primary")) }),
        ],
      }));
    };
    page.querySelector("#ts").addEventListener("change", drawMatrix);
    drawMatrix();

    let pageNo = 0;
    const PER = 50;
    const drawTable = () => {
      const q = page.querySelector("#tq").value.trim().toLowerCase();
      const tier = page.querySelector("#tt").value;
      const f = rows.filter((r) => (!tier || r[10] === tier)
        && (!q || `${r[1]} ${r[3]} ${r[7]}`.toLowerCase().includes(q))).slice().reverse();
      const pages = Math.max(1, Math.ceil(f.length / PER));
      pageNo = Math.min(pageNo, pages - 1);
      page.querySelector("#ttab").innerHTML = table([
        { label: "Season", get: (r) => r[0] },
        { label: "Player", get: (r) => r[1] },
        { label: "From", html: (r) => progLink(r[2], r[3]) + ` <span class="pill">D-${esc(r[4])}</span>` },
        { label: "To", html: (r) => progLink(r[6], r[7]) + ` <span class="pill">D-${esc(r[8])}</span>` },
        { label: "Class at new school", get: (r) => r[9] || "" },
        { label: "Tier", html: (r) => `<span class="pill" title="${esc(r[11])}">${esc(r[10])}</span>` },
      ], f.slice(pageNo * PER, pageNo * PER + PER), { empty: "No transfers match." });
      page.querySelector("#pinfo").textContent = `${num(f.length)} transfers · page ${pageNo + 1} of ${pages}`;
      page.querySelector("#prev").disabled = pageNo === 0;
      page.querySelector("#next").disabled = pageNo >= pages - 1;
    };
    page.querySelector("#tq").addEventListener("input", () => { pageNo = 0; drawTable(); });
    page.querySelector("#tt").addEventListener("change", () => { pageNo = 0; drawTable(); });
    page.querySelector("#prev").addEventListener("click", () => { pageNo--; drawTable(); });
    page.querySelector("#next").addEventListener("click", () => { pageNo++; drawTable(); });
    drawTable();
  }

  // ---------- international -------------------------------------------------
  async function international(sel) {
    const [x, list] = await Promise.all([get("data/international.json"), get("data/programs.json")]);
    const byId = new Map(list.map((p) => [p.ncaa_id, p]));
    const seasons = [...new Set(x.by_country.map((r) => r.season))].sort();
    const cname = new Map(x.by_country.map((r) => [r.home_country, r.country_name]));
    const totals = d3.rollups(x.by_country.filter((r) => x.recent.includes(r.season)),
      (v) => d3.sum(v, (r) => r.players), (r) => r.home_country).sort((a, b) => b[1] - a[1]);
    sel = sel && cname.has(sel) ? sel : totals[0][0];
    const top = totals.slice(0, 6).map((t) => t[0]);
    const page = el(`<section>
      <h1>The international pipeline</h1>
      <p class="lede">Where non-U.S. players come from, which programs they join, and how they get there. Totals cover the last three seasons (${x.recent.join(", ")}) unless noted.</p>
      <div class="card"><h2>Home countries</h2><p class="sub">Circle size is player-seasons over the last three seasons. Select a country to draw its links to programs.</p>
        <div class="controls"><label>Country<select id="ic">${totals.map(([c, n]) => `<option value="${c}" ${c === sel ? "selected" : ""}>${esc(cname.get(c))} (${n})</option>`).join("")}</select></label></div>
        <div class="chart" id="imap"></div></div>
      <div class="grid2">
        <div class="card"><h2>Top countries over time</h2><p class="sub">Player-seasons, all divisions.</p><div id="ileg"></div><div class="chart" id="itrend"></div></div>
        <div class="card"><h2>How international players arrive</h2><p class="sub">Route into the first NCAA roster we see them on, all divisions.</p><div id="rleg"></div><div class="chart" id="iroute"></div><p class="note">${short(seasons[0])} counts everyone already on a roster in the first season, not just newcomers.</p></div>
      </div>
      <div class="card"><h2>Programs drawing from ${esc(cname.get(sel))}</h2><div id="iprog"></div></div>
    </section>`);
    mount(app, page);
    page.querySelector("#ic").addEventListener("change", (e) => { location.hash = `#/international/${e.target.value}`; });
    const land = await world();
    const cent = new Map(x.centroids.map((c) => [c.home_country, c]));
    const dots = totals.filter(([c]) => cent.has(c)).map(([c, n]) => ({ c, n, name: cname.get(c), ...cent.get(c) }));
    const arcs = x.arcs.filter((a) => a.home_country === sel && byId.get(a.ncaa_id)?.lat != null && cent.has(sel))
      .map((a) => ({ ...a, x1: cent.get(sel).home_lon, y1: cent.get(sel).home_lat, x2: byId.get(a.ncaa_id).lon, y2: byId.get(a.ncaa_id).lat }));
    const m = page.querySelector("#imap"), w = widthOf(m, 900);
    const chart = Plot.plot({
      width: w, height: Math.round(w * 0.5), projection: { type: "equal-earth" }, style: baseStyle(),
      r: { range: [2, 18] },
      marks: [
        Plot.geo(land, { fill: css("--land"), stroke: css("--land-line") }),
        Plot.link(arcs, { x1: "x1", y1: "y1", x2: "x2", y2: "y2", stroke: css("--d2"), strokeOpacity: 0.45, strokeWidth: (a) => Math.min(3, 0.6 + a.players / 4) }),
        Plot.dot(dots, { x: "home_lon", y: "home_lat", r: "n", fill: (d) => (d.c === sel ? css("--d2") : css("--d1")), fillOpacity: 0.7,
          stroke: css("--surface-1"), strokeWidth: 1, channels: { Country: "name", "Player-seasons": "n" },
          tip: { format: { x: false, y: false, r: false, fill: false } }, href: (d) => `#/international/${d.c}` }),
      ],
    });
    chart.querySelectorAll("a").forEach((a) => a.setAttribute("target", "_self"));
    mount(m, chart);

    const palette = ["--d1", "--d2", "--d3", "--series-4", "--series-5", "--series-6"];
    const fallback = ["#eda100", "#e87ba4", "#008300"];
    const colors = top.map((_, i) => css(palette[i]) || fallback[i - 3]);
    page.querySelector("#ileg").innerHTML = legend(top.map((c, i) => [cname.get(c), colors[i]]));
    const trend = d3.rollups(x.by_country.filter((r) => top.includes(r.home_country)),
      (v) => d3.sum(v, (r) => r.players), (r) => r.home_country, (r) => r.season)
      .flatMap(([c, ss]) => ss.map(([season, n]) => ({ c: cname.get(c), season, n })));
    const tc = page.querySelector("#itrend"), tw = widthOf(tc);
    mount(tc, Plot.plot({
      width: tw, height: 260, marginLeft: 44, marginBottom: 34, style: baseStyle(),
      x: { type: "point", domain: seasons, label: null, tickFormat: short, ticks: tw < 520 ? seasons.filter((_, i) => i % 3 === 0) : seasons },
      y: { grid: true, label: "players", zero: true },
      color: { domain: top.map((c) => cname.get(c)), range: colors },
      marks: [
        Plot.ruleY([0], { stroke: css("--border") }),
        Plot.line(trend, { x: "season", y: "n", stroke: "c", strokeWidth: 2 }),
        Plot.tip(trend, Plot.pointer({ x: "season", y: "n", channels: { Country: "c" }, format: { stroke: false } })),
      ],
    }));
    const RT = [["direct", "Direct", css("--d1")], ["via_junior_college", "Via junior college", css("--d2")], ["via_ncaa_transfer", "Transfer from another NCAA school", css("--d3")]];
    page.querySelector("#rleg").innerHTML = legend(RT.map(([, l, c]) => [l, c]));
    const rr = d3.rollups(x.routes, (v) => d3.sum(v, (r) => r.n), (r) => r.first_season, (r) => r.route)
      .flatMap(([season, rs]) => rs.map(([route, n]) => ({ season, route: RT.find((t) => t[0] === route)[1], n })));
    const rc = page.querySelector("#iroute"), rw = widthOf(rc);
    mount(rc, Plot.plot({
      width: rw, height: 260, marginLeft: 44, marginBottom: 34, style: baseStyle(),
      x: { type: "band", label: null, tickFormat: (d, i) => (rw < 700 && i % 2 ? "" : short(d)), padding: 0.18 },
      y: { grid: true, label: "new players" },
      color: { domain: RT.map((t) => t[1]), range: RT.map((t) => t[2]) },
      marks: [Plot.barY(rr, { x: "season", y: "n", fill: "route", order: RT.map((t) => t[1]), stroke: css("--surface-1"), strokeWidth: 1, tip: true }),
              Plot.ruleY([0], { stroke: css("--border") })],
    }));
    const progs = x.arcs.filter((a) => a.home_country === sel).sort((a, b) => b.players - a.players);
    page.querySelector("#iprog").innerHTML = table([
      { label: "Program", html: (r) => progLink(r.ncaa_id, r.team) },
      { label: "Division", get: (r) => byId.get(r.ncaa_id)?.division || "" },
      { label: "Player-seasons (last 3)", get: (r) => r.players, num: true },
    ], progs);
  }

  // ---------- router --------------------------------------------------------
  async function route() {
    const [, name = "", arg] = location.hash.replace(/^#/, "").split("/");
    const key = name || "overview";
    document.querySelectorAll("nav a").forEach((a) => {
      const active = a.dataset.route === (key === "program" ? "programs" : key);
      if (active) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    try {
      if (key === "program" && arg) await program(arg);
      else if (key === "programs") await programs();
      else if (key === "states") await states(arg);
      else if (key === "transfers") await transfers();
      else if (key === "international") await international(arg);
      else await overview();
      window.scrollTo(0, 0);
    } catch (err) {
      console.error(err);
      app.innerHTML = `<h1>Something went wrong</h1><p class="muted">${esc(err.message)}</p>`;
    }
  }

  // theme toggle; charts read CSS variables, so re-render after a switch
  const themeBtn = document.getElementById("theme");
  try {
    const saved = localStorage.getItem("atlas-theme");
    if (saved) document.documentElement.dataset.theme = saved;
  } catch (_) { /* storage unavailable */ }
  themeBtn.addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("atlas-theme", next); } catch (_) { /* ignore */ }
    route();
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", route);
  let rt;
  window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(route, 250); });
  window.addEventListener("hashchange", route);
  route();
})();
