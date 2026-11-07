/* ======================================================================
   Gemini adapter and focused view for the review design (review.html).

   Implements the Engine.interpret() slot the page was built with. A question goes to /api/ask, where Gemini answers
   it through the Python data tools on the whole workbook; the answer leads the focused view. Figures that cannot be
   traced to the data are marked. Without the server or a key the page keeps working on its offline rules.
   ====================================================================== */
(function () {
"use strict";
const AI = {on: false, model: "gemini-2.5-flash", reason: "checking"};
const NAVY = "#00205b", ORANGE = "#d9701c", GREY = "#cbd5e1";
const NO_SCAN = "Allocated garments with no scan activity";
const WHATIF = {scale: 1, rate: 1, pct: 0};
const F = {open: false, kind: "", question: "", pct: 5, contract: null, answer: null, thinking: false, dynamic: false, error: "", key: ""};
const HISTORY = {};
const PARTS = ["#2f5fb3", ORANGE, "#0e8f7e", "#8250c4", GREY];  // validated for colour blindness; legend names each part
let REQUEST = 0;
const ASK_LIMIT_MS = 150000;
const tag = s => STATUS[s] ? badge(s) : "";
const plain = h => String(h).replace(/<[^>]+>/g, "");
const eur = v => "€" + fmt(v);
const signedEur = v => (v >= 0 ? "+€" : "-€") + fmt(Math.abs(v));

/* ---------------------------------------------------------------- reading the question */
const TOPICS = [
  ["pricing", /\bpric\w*|\btariffs?\b|\bfees?\b|\bcosts?\b.*\d\s*%|\d\s*%.*\bcosts?\b|\bцен\w*|тариф/],
  ["whatif", /\bwhat.?if\b|\bscenarios?\b|sensitiv\w*|\bbaselines?\b|\bcompare\b.*\b(home|in.?house|laundry|washing)\b/],
  ["hygiene", /\bhygien\w*|food.safety|haccp|contaminat\w*|\binfection\b|гигиен|пищев\w* безопас/],
  ["repair", /\brepairs?\b|\brepaired\b|\breplacements?\b|ремонт/],
  ["fleet", /\ballocat\w*|\bno.scan|\bunused\b|\bfleet\b|\binventory\b|\butili[sz]ation\b|\bin use\b|инвентар/],
  ["service", /\bon.time\b|\bdeliver\w*|\blate\b|\btrips?\b|service (request|performance)|доставк/],
  ["sustainability", /\bwater\b|\benergy\b|\bkwh\b|\bcarbon\b|\bco2\w*|\bemissions?\b|\bcircular\w*|\brecycl\w*|sustainab\w*|\besg\b|\bbaseline\b|\bвод[аыуе]\b|углерод|выброс/],
];
const classify = text => { const t = String(text).toLowerCase(); const hit = TOPICS.find(([, re]) => re.test(t)); return hit ? hit[0] : "general"; };
function priceChange(text) {
  const t = String(text).toLowerCase(), m = t.match(/([+-]?\d+(?:[.,]\d+)?)\s*(?:%|percent|per cent|процент)/);
  let pct = m ? parseFloat(m[1].replace(",", ".")) : 5;
  if (/\b(decreas\w*|reduc\w*|lower\w*|drops?|cuts?|falls?|discount\w*)\b|сниж|уменьш|скидк/.test(t)) pct = -Math.abs(pct);
  return Math.max(-50, Math.min(100, pct));
}

/* ---------------------------------------------------------------- small visuals */
function donut(parts, center) {
  const total = parts.reduce((s, p) => s + p.v, 0) || 1, r = 62, c = 2 * Math.PI * r; let run = 0;
  const arcs = parts.map(p => { const len = p.v / total * c, gap = parts.length > 1 ? Math.min(2, len / 2) : 0;
    const out = `<circle cx="80" cy="80" r="${r}" fill="none" stroke="${p.c}" stroke-width="20" stroke-dasharray="${Math.max(len - gap, 0)} ${c}" stroke-dashoffset="${-run}" transform="rotate(-90 80 80)"><title>${esc(p.l)}</title></circle>`;
    run += len; return out; }).join("");
  return `<div class="dn"><svg width="170" height="170" viewBox="0 0 160 160" role="img" aria-label="${esc(parts.map(p => p.l).join(", "))}">${arcs}<text x="80" y="87" text-anchor="middle" font-size="24" font-weight="700" fill="#0a1b3d">${esc(center)}</text></svg>
    <div class="leg">${parts.map(p => `<div><i style="background:${p.c}"></i>${esc(p.l)}<b>${esc(p.t || fmt(p.v))}</b></div>`).join("")}</div></div>`;
}
const bars = rows => { const top = Math.max(...rows.map(r => r.v), 1e-9);
  return rows.map(r => `<div class="br"><div>${esc(r.l)}</div><div class="t"><i class="${r.w ? "w" : ""}" style="width:${Math.max(r.v / top * 100, 2)}%"></i></div><b>${esc(r.t)}</b></div>`).join(""); };
const ind = (l, v, tags, lvl, n) => ({l, v, tags: tags || [], lvl: lvl || "", n: n || ""});
function direction(key, spec, worseUp = true) {
  const s = monthSeries(key, spec).slice(spec.from, spec.to + 1).filter(v => v != null);
  if (s.length < 6) return ind("Recent direction", "n/a", ["CALCULATED"], "", "Needs at least six months in view.");
  const mean = a => a.reduce((x, y) => x + y, 0) / a.length, now = mean(s.slice(-3)), was = mean(s.slice(-6, -3)), ch = was ? (now / was - 1) * 100 : 0;
  const text = Math.abs(ch) < 2 ? "Stable" : ch > 0 ? "Rising" : "Falling", bad = Math.abs(ch) >= 2 && (ch > 0) === worseUp;
  return ind("Recent direction", `${text} (${ch >= 0 ? "+" : ""}${fmt(ch, 1)}%)`, ["CALCULATED"], bad ? "watch" : "ok", "Last 3 months against the 3 before. A direction to discuss, not a forecast.");
}

/* ---------------------------------------------------------------- one builder per topic, on compute() only */
const BUILD = {
  whatif(a, spec) { // team what-if: every result recalculates from compute(); nothing here is a measured figure
    const W = WHATIF, used = a.flows, saved = k => a.base[k] * W.scale - used[k], factor = 1 + W.pct / 100;
    const avoided = used.rep_eur * W.rate * factor, co2Rep = used.rep_kg * W.rate, spend = F.contract != null ? F.contract : (evidence().spend || {fields: {}}).fields.eur;
    const now = (v, base) => `Now ${base}.`, others = Object.entries(D.baselines).map(([id, b]) => ({l: b.name, v: Math.max(compute(S.cid, spec.from, spec.to, spec.sites, id).base.co2 * W.scale - used.co2, 0), id}));
    const water = a.base.water * W.scale, share = water > used.water ? 100 * (water - used.water) / water : null;
    const list = [ind("Water saved", big(saved("water"), "L"), ["ESTIMATED"], "", now(0, big(a.save.water.base, "L"))), ind("Energy saved", big(saved("energy"), "kWh"), ["ESTIMATED"], "", now(0, big(a.save.energy.base, "kWh"))),
      ind("CO2e saved", tonnes(saved("co2")), ["ESTIMATED"], "", now(0, tonnes(a.save.co2.base))), ind("Purchases avoided by repairs", eur(avoided), ["ESTIMATED"], "", `${fmt(W.rate, 2)} replacements per repair, prices ${W.pct >= 0 ? "+" : ""}${fmt(W.pct, 1)}%.`),
      ind("Product CO2e avoided by repairs", tonnes(co2Rep), ["ESTIMATED"], "", "From synthetic product footprints.")];
    if (spend) list.push(ind("Annual cost change", signedEur(spend * W.pct / 100), ["ESTIMATED"], W.pct > 0 ? "watch" : "ok", `From reported spend of ${eur(spend)} a year.`));
    return {title: "What-if", lead: `Against ${D.baselines[spec.baseline].name.toLowerCase()}, ${a.label}. Change the assumptions and every figure recalculates.`,
      note: "Team assumptions for discussion, not measured results. The data pack has no contract or service prices, so the price change applies to synthetic garment purchase prices and to the annual spend if you enter it.",
      controls: `<div class="card fctl" id="wControls">
        <label>Comparison baseline<select id="wBase">${Object.entries(D.baselines).map(([id, b]) => `<option value="${esc(id)}" ${id === spec.baseline ? "selected" : ""}>${esc(b.name)}</option>`).join("")}</select></label>
        <label>Baseline factor scale: <output>${fmt(W.scale, 2)}×</output><input id="wScale" type="range" min="0.85" max="1.15" step="0.05" value="${W.scale}"></label>
        <label>Replacements postponed per repair: <output>${fmt(W.rate, 2)}</output><input id="wRate" type="range" min="0.5" max="1.2" step="0.1" value="${W.rate}"></label>
        <label>Price change: <output>${W.pct >= 0 ? "+" : ""}${fmt(W.pct, 1)}%</output><input id="wPct" type="range" min="-20" max="20" step="0.5" value="${W.pct}"></label>
        <label>Annual spend, EUR (client-reported)<input id="fSpend" type="number" step="10000" min="0" value="${spend == null ? "" : spend}" placeholder="Not in the data pack"></label>
        <button class="btn pill ghost" id="wReset" style="align-self:end;height:40px">Reset assumptions</button></div>`,
      donutTitle: "Water per kg against the baseline", donut: share == null ? "" : donut([{l: "Lindström water", v: used.water, c: NAVY, t: big(used.water, "L")}, {l: "Saved against the baseline", v: water - used.water, c: GREY, t: big(water - used.water, "L")}], fmt(share, 0) + "%"),
      inds: list, rules: W.scale === 1 && W.rate === 1 && W.pct === 0 ? ["These are today's assumptions. Move a control above to see what changes."] : [`CO2e saved would be ${tonnes(saved("co2"))} instead of ${tonnes(a.save.co2.base)}.`, `Water saved would be ${big(saved("water"), "L")} instead of ${big(a.save.water.base, "L")}.`, `Repairs would avoid ${eur(avoided)} of purchases at ${fmt(W.rate, 2)} replacements per repair.`],
      barsTitle: "CO2e saved against each baseline, at these assumptions", bars: bars(others.map(o => ({l: o.l, v: o.v, t: tonnes(o.v), w: o.id === spec.baseline})))};
  },
  hygiene(a, spec) {
    const rows = a.siteRows.map(r => ({...r, h: EXTRA.sites[r.id]})).filter(r => r.h && r.h.traced);
    if (!rows.length) return {title: "Hygiene and food safety risk", lead: "This view has no traced Food Industry or Healthcare garments, so the hygiene screen does not apply.", inds: [ind(M.hygiene.lab, M.hygiene.big(a), ["CALCULATED"], "", M.hygiene.sub(a))], rules: ["No traced hygiene-critical garments in this view, so there is no scan screen to report."]};
    const t = rows.reduce((s, r) => ({traced: s.traced + r.h.traced, flagged: s.flagged + r.h.flagged, allocated: s.allocated + r.h.allocated}), {traced: 0, flagged: 0, allocated: 0});
    const share = 100 * t.flagged / t.traced, cover = t.allocated ? 100 * t.traced / t.allocated : 0;
    const ranked = rows.map(r => ({n: r.name, s: 100 * r.h.flagged / r.h.traced})).sort((x, y) => y.s - x.s), review = ranked.filter(r => r.s >= 30).length;
    return {title: "Hygiene and food safety risk", lead: `${fmt(share, 1)}% of traced hygiene-critical garments have no recent scan.`,
      note: `Low-confidence screen. The cycle log traces ${fmt(t.traced)} of ${fmt(t.allocated)} allocated hygiene garments (${fmt(cover, 1)}%). Every flag comes from the log ending, so confirm against full RFID records before raising it as a hygiene concern.`,
      donutTitle: "Traced garments by last scan", donut: donut([{l: `No scan for over ${EXTRA.days} days`, v: t.flagged, c: ORANGE}, {l: "Scanned more recently", v: t.traced - t.flagged, c: NAVY}], fmt(share, 1) + "%"),
      inds: [ind(`No scan for over ${EXTRA.days} days`, fmt(t.flagged) + " garments", ["CALCULATED"], t.flagged ? "watch" : "ok", "Low-confidence screen."),
        ind("Fleet the log can see", fmt(cover, 1) + "%", ["CALCULATED"], "watch", "Traced garments / allocated hygiene garments."),
        ind("Sites in a review band", `${review} of ${rows.length}`, ["CALCULATED"], review ? "watch" : "ok", "Watch from 30% of traced garments."),
        ind(M.hygiene.lab, M.hygiene.big(a) + " /wk", ["CALCULATED"], "", M.hygiene.sub(a))],
      rules: [`${fmt(t.flagged)} of ${fmt(t.traced)} traced garments (${fmt(share, 1)}%) have no scan for over ${EXTRA.days} days. This is a low-confidence screen, not a finding.`,
        `The log traces only ${fmt(cover, 1)}% of this fleet, so pull full RFID records before raising it.`,
        `${ranked[0].n} has the highest flagged share at ${fmt(ranked[0].s, 1)}%. Start the check there.`],
      barsTitle: "Flagged share of traced garments by site", bars: bars(ranked.slice(0, 10).map(r => ({l: r.n, v: r.s, t: fmt(r.s, 1) + "%", w: r.s >= 30})))};
  },
  pricing(a, spec) {
    const ev = evidence(), pct = F.pct, factor = 1 + pct / 100, spend = F.contract != null ? F.contract : ev.spend ? ev.spend.fields.eur : null, words = pct >= 0 ? "increase" : "decrease";
    const list = [], rules = [], full = compute(S.cid, 0, NM - 1);
    if (spend) { const change = spend * pct / 100, cpw = spend / full.wearerMonths;
      list.push(ind("Annual cost change", signedEur(change), ["ESTIMATED"], pct > 0 ? "watch" : "ok", `From reported spend of ${eur(spend)} a year. Client-reported, not verified.`));
      list.push(ind("Cost per wearer per month", `€${fmt(cpw * factor, 2)}`, ["ESTIMATED"], "", `Now €${fmt(cpw, 2)}.`));
      rules.push(`A ${fmt(Math.abs(pct), 1)}% ${words} changes annual cost by ${signedEur(change)}, from the reported spend.`); }
    else { list.push(ind("Annual cost change", "Needs spend", ["ESTIMATED"], "", "The data pack has no contract or service prices.")); rules.push("The data pack has no contract or service prices, so the service cost impact needs the annual spend entered above."); }
    list.push(ind("Purchases avoided by repairs", `€${big(a.avoidedEur.low * factor)}–${big(a.avoidedEur.high * factor)}`, ["ESTIMATED"], "ok", `Now €${big(a.avoidedEur.low)}–${big(a.avoidedEur.high)}, if garment prices move the same way.`));
    list.push(ind("Right-sizing value", eur(a.rs.base.eur * factor), ["ESTIMATED"], "", `Now ${eur(a.rs.base.eur)}. Scenario at synthetic purchase prices, not a saving.`));
    rules.push(`If garment prices move ${pct >= 0 ? "+" : ""}${fmt(pct, 1)}%, repairs avoid €${big(a.avoidedEur.low * factor)}–${big(a.avoidedEur.high * factor)} instead of €${big(a.avoidedEur.low)}–${big(a.avoidedEur.high)}.`);
    const parts = pct >= 0 ? [{l: "Current price", v: 100, c: NAVY, t: "100"}, {l: "Increase", v: pct, c: ORANGE, t: fmt(pct, 1)}] : [{l: "New price", v: 100 + pct, c: NAVY, t: fmt(100 + pct, 1)}, {l: "Reduction", v: -pct, c: ORANGE, t: fmt(-pct, 1)}];
    return {title: `What-if: pricing ${pct >= 0 ? "+" : ""}${fmt(pct, 1)}%`, lead: `A ${fmt(Math.abs(pct), 1)}% ${words} on garment purchase prices${spend ? " and the reported spend." : ". Add the annual spend to see the service cost impact."}`,
      note: "Full TCO cannot be stated: the data pack has no contract or service pricing. Purchase prices are synthetic, and this applies one percentage to all of them.",
      controls: `<div class="card fctl"><label>Price change, %<input id="fPct" type="number" step="0.5" min="-50" max="100" value="${pct}"></label><label>Annual spend, EUR (client-reported)<input id="fSpend" type="number" step="10000" min="0" value="${spend == null ? "" : spend}" placeholder="Not in the data pack"></label></div>`,
      donutTitle: "Price change against the current price", donut: donut(parts, `${pct >= 0 ? "+" : ""}${fmt(pct, 1)}%`), inds: list, rules,
      barsTitle: "Purchases avoided by repairs, high case", bars: bars([{l: "Today", v: a.avoidedEur.high, t: eur(a.avoidedEur.high)}, {l: "What-if", v: a.avoidedEur.high * factor, t: eur(a.avoidedEur.high * factor), w: true}])};
  },
  fleet(a, spec) {
    const rows = a.siteRows.filter(r => r.util != null).sort((x, y) => x.util - y.util), share = a.alloc ? 100 * a.unused / a.alloc : 0, low = rows[0];
    return {title: "Fleet allocation signal", lead: `${NO_SCAN}: ${fmt(a.unused)}, ${fmt(share, 1)}% of allocated. An optimization signal for investigation.`,
      donutTitle: `Allocated garments, end of ${MLAB[a.t]}`, donut: donut([{l: NO_SCAN, v: a.unused, c: ORANGE}, {l: "Scanned", v: a.scanned, c: NAVY}], fmt(share, 1) + "%"),
      inds: [ind(NO_SCAN, fmt(a.unused), ["CALCULATED"], "watch", "May reflect low wash frequency, delayed pickups or uncaptured RFID scans."), ind("Garments in use", fmt(a.util, 1) + "%", ["CALCULATED"]),
        ind("Right-sizing scenario", eur(a.rs.base.eur), ["ESTIMATED"], "", `${fmt(a.rs.base.pieces)} pieces. Not a saving or a write-off.`), direction("utilisation", spec, false)],
      rules: [`${NO_SCAN}: ${fmt(a.unused)} of ${fmt(a.alloc)} allocated. It may reflect low wash frequency, delayed pickups or uncaptured RFID scans.`, low ? `${low.name} has the lowest in-use share at ${fmt(low.util, 1)}%.` : "", "Ask the site to confirm the cause before discussing any allocation change."].filter(Boolean),
      barsTitle: "Garments in use by site, lowest first", bars: bars(rows.slice(0, 10).map(r => ({l: r.name, v: r.util, t: fmt(r.util, 1) + "%", w: r === low})))};
  },
  repair(a, spec) {
    const rows = a.siteRows.slice().sort((x, y) => y.repairs - x.repairs);
    return {title: "Repair and replacement signal", lead: `${fmt(a.repairs)} repairs completed, avoiding an estimated €${big(a.avoidedEur.low)}–${big(a.avoidedEur.high)} of purchases.`,
      donutTitle: "Replacements postponed per repair", donut: donut([{l: "Low case", v: a.postponed.low, c: "#2f5fb3"}, {l: "Added in the high case", v: a.postponed.high - a.postponed.low, c: GREY}], fmt(a.repairs)),
      inds: [ind("Repairs completed", fmt(a.repairs), ["SYNTHETIC"]), ind("Purchases avoided", `€${big(a.avoidedEur.low)}–${big(a.avoidedEur.high)}`, ["ESTIMATED"], "ok", "0.5 to 1 replacement postponed per repair."), ind("Replacements postponed", `${fmt(a.postponed.low)}–${fmt(a.postponed.high)}`, ["ESTIMATED"]), direction("repairs", spec)],
      rules: [`${fmt(a.repairs)} repairs avoided an estimated €${big(a.avoidedEur.low)}–${big(a.avoidedEur.high)} of purchases.`, rows[0] ? `${rows[0].name} has the most repairs, ${fmt(rows[0].repairs)}.` : "", "Would a garment upgrade cost less than continued repair where the rate stays high?"].filter(Boolean),
      barsTitle: "Repairs by site", bars: bars(rows.slice(0, 10).map(r => ({l: r.name, v: r.repairs, t: fmt(r.repairs)})))};
  },
  service(a, spec) {
    const rows = a.siteRows.filter(r => r.on_time != null).sort((x, y) => x.on_time - y.on_time), low = rows[0], on = a.onTime == null ? 0 : a.onTime;
    return {title: "Service reliability", lead: `${fmt(a.onTime, 1)}% of tracked trips on time.`,
      donutTitle: "Tracked trips", donut: donut([{l: "On time", v: a.cycles - a.late, c: NAVY}, {l: "Late", v: a.late, c: ORANGE}], fmt(on, 1) + "%"),
      inds: [ind("Trips on time", fmt(a.onTime, 1) + "%", ["CALCULATED"], "", M.on_time.sub(a)), ind("Late trips", fmt(a.late), ["CALCULATED"], a.late ? "watch" : "ok", `of ${fmt(a.cycles)} tracked.`), ind("Service requests", fmt(a.reqPer1000, 1), ["CALCULATED"], "", M.requests.sub(a)), direction("on_time", spec, false)],
      rules: [`${fmt(a.onTime, 1)}% of ${fmt(a.cycles)} tracked trips were on time.`, low ? `${low.name} has the lowest on-time rate at ${fmt(low.on_time, 1)}%.` : "", "Which stage of the delivery cycle matters most to site operations next year?"].filter(Boolean),
      barsTitle: "Trips on time by site, lowest first", bars: bars(rows.slice(0, 10).map(r => ({l: r.name, v: r.on_time, t: fmt(r.on_time, 1) + "%", w: r === low})))};
  },
  sustainability(a, spec) {
    const bl = shortBl(a.bl).toLowerCase(), re = a.recycledPct, rows = a.siteRows.slice().sort((x, y) => y.water_saved - x.water_saved);
    return {title: "Sustainability value", lead: `${big(a.save.water.base, "L")} water, ${big(a.save.energy.base, "kWh")} energy and ${tonnes(a.save.co2.base)} CO2e saved vs ${bl}.`,
      donutTitle: "Allocated textile waste by route", donut: re == null ? "" : donut([{l: "Recycled or reused", v: re, c: NAVY, t: fmt(re, 0) + "%"}, {l: "Other routes", v: 100 - re, c: GREY, t: fmt(100 - re, 0) + "%"}], fmt(re, 0) + "%"),
      inds: [ind("Water saved", big(a.save.water.base, "L"), ["ALLOCATED"], "ok", M.water_saving.sub(a)), ind("Energy saved", big(a.save.energy.base, "kWh"), ["ALLOCATED"], "ok", M.energy_saving.sub(a)), ind("CO2e saved", tonnes(a.save.co2.base), ["ALLOCATED"], "ok", M.co2_saving.sub(a)), direction("co2_saving", spec, false)],
      rules: [`Against ${bl}: ${big(a.save.water.base, "L")} water and ${tonnes(a.save.co2.base)} CO2e saved.`, `The service footprint is ${tonnes(a.flows.co2)} CO2e, excluding garment production.`, re == null ? "" : `${fmt(re, 0)}% of allocated textile waste goes to recycling or reuse.`].filter(Boolean),
      barsTitle: "Water saved by site", bars: bars(rows.slice(0, 10).map(r => ({l: r.name, v: Math.max(r.water_saved, 0), t: big(r.water_saved, "L")})))};
  },
};
const build = (kind, spec) => (BUILD[kind] || BUILD.general)(AGG(spec), spec);

/* ---------------------------------------------------------------- the canvas */
function mount() { let el = $("#focusView"); if (!el) { el = document.createElement("div"); el.id = "focusView"; el.className = "hidden"; $("#dash").appendChild(el); } return el; }
const md = text => { let html = "", list = false;
  String(text).split(/\r?\n/).forEach(raw => { const t = esc(raw).replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>"), li = t.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
    if (li) { if (!list) { html += "<ul>"; list = true; } html += `<li>${li[1]}</li>`; } else { if (list) { html += "</ul>"; list = false; } if (t.trim()) html += `<p>${t}</p>`; } });
  return html + (list ? "</ul>" : ""); };
function answerCard(A) {
  return `<div class="card fanswer" id="fAnswer"><h3>AI insights</h3>${md(A.text)}${A.untraced.length ? `<div class="fwarn">⚠ Check before quoting. Not found in the data: ${esc(A.untraced.join(", "))}</div>` : ""}
    <div class="fsrc">Source: ${esc(A.source)}</div>${A.queries.length ? `<details class="fq"><summary>Data queried (${A.queries.length})</summary>${A.queries.map(q => `<div><code>${esc(q.tool)}(${esc(Object.entries(q.args || {}).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", "))})</code></div>`).join("")}</details>` : ""}</div>`;
}
function canvasView(A) { // built only from what answered this question
  const c = (A && A.canvas) || {}, chart = c.chart || null, items = chart ? chart.items : [];
  return {title: c.title || "Your question", lead: "", rules: [], inds: (c.metrics || []).map(m => ind(m.label, m.value, [], "", m.provenance ? `[${m.provenance}]` : "")),
    donutTitle: chart ? chart.title : "", donut: chart && chart.type === "meter" ? donut([{l: items[0].label, v: items[0].size, c: NAVY, t: items[0].value}, {l: "Remainder", v: 100 - items[0].size, c: GREY, t: fmt(100 - items[0].size, 1) + "%"}], fmt(items[0].size, 1) + "%")
      : chart && chart.type === "donut" ? donut(items.map((x, i) => ({l: x.label, v: x.size, c: PARTS[i], t: x.value})), fmt(100 * items[0].size / items.reduce((s, x) => s + x.size, 0), 0) + "%") : "",
    barsTitle: chart ? chart.title : "", bars: chart && chart.type === "bars" ? bars(items.map(x => ({l: x.label, v: x.size, t: x.value}))) : ""};
}
const noAnswerView = () => ({title: "Your question", lead: F.error ? `Gemini could not answer: ${F.error}.` : "Gemini is not connected, so this question cannot be answered on this page. The Insights panel shows what the offline rules found.", inds: [], rules: []});
function draw() {
  const el = mount(); document.body.classList.toggle("focusing", F.open); el.classList.toggle("hidden", !F.open);
  if (!F.open) return;
  const A = F.answer && F.key === stateKey() ? F.answer : null, v = F.dynamic ? canvasView(A) : F.kind === "general" ? noAnswerView() : build(F.kind, S.spec);
  el.innerHTML = `<button class="btn pill ghost" id="focusBack">← Return to Overview</button>
    <div class="fhead"><h1>${esc(v.title)}</h1>${F.question ? `<div class="fask">You asked: ${esc(F.question)}</div>` : ""}${v.lead ? `<div class="flead">${esc(v.lead)}</div>` : ""}</div>
    ${v.note ? `<div class="fnote">${esc(v.note)}</div>` : ""}${v.controls || ""}
    ${v.inds.length || v.donut ? `<div class="fgrid ${F.dynamic ? "dash" : ""}" style="${v.donut && v.inds.length ? "" : "grid-template-columns:1fr"}">${v.donut ? `<div class="card"><h3>${esc(v.donutTitle)}</h3>${v.donut}</div>` : ""}
      ${v.inds.length ? `<div class="card fkpis"><h3>${F.dynamic ? "Key metrics" : "Risk and impact indicators"}</h3><div class="inds">${v.inds.map(i => `<div class="ind"><div class="l">${esc(i.l)}</div><div class="v">${esc(i.v)}</div>${i.lvl ? `<span class="lvl ${i.lvl}">${{ok: "● OK", watch: "▲ Watch", high: "■ High"}[i.lvl]}</span>` : ""}${i.tags.map(tag).join("")}${i.n ? `<div class="n">${esc(i.n)}</div>` : ""}</div>`).join("")}</div></div>` : ""}</div>` : ""}
    ${!A && !F.thinking && v.rules.length ? `<div class="card fins"><h3>Insights</h3><ul>${v.rules.slice(0, 3).map(l => `<li>${esc(l)}</li>`).join("")}</ul><div class="fsrc">Source: Rule-based, from the calculation layer</div></div>` : ""}
    ${v.bars ? `<div class="card"><h3>${esc(v.barsTitle)}</h3>${v.bars}</div>` : ""}
    ${A ? answerCard(A) : F.thinking ? `<div class="card fanswer" id="fAnswer"><h3>AI insights</h3><p>Working on your question<span id="fWait"></span></p></div>` : ""}`;
  $("#focusBack").onclick = close;
  const pct = $("#fPct"), spend = $("#fSpend");
  if (pct) pct.onchange = () => { const n = parseFloat(pct.value); if (Number.isFinite(n)) { F.pct = Math.max(-50, Math.min(100, n)); draw(); } };
  if (spend) spend.onchange = () => { const n = parseFloat(spend.value); F.contract = Number.isFinite(n) && n > 0 ? n : null; draw(); };
  [["#wScale", "scale"], ["#wRate", "rate"], ["#wPct", "pct"]].forEach(([id, k]) => { const c = $(id); if (c) c.onchange = () => { WHATIF[k] = parseFloat(c.value); draw(); }; });
  const base = $("#wBase"), reset = $("#wReset");
  if (base) base.onchange = () => setSpec({baseline: base.value}, "ui");
  if (reset) reset.onclick = () => { Object.assign(WHATIF, {scale: 1, rate: 1, pct: 0}); draw(); };
}
const stateKey = () => JSON.stringify([S.cid, S.spec.from, S.spec.to, S.spec.sites, S.spec.baseline, F.kind, F.pct, F.contract, WHATIF]);
function open(kind, question, dynamic = false) { Object.assign(F, {open: true, kind, question, dynamic, answer: null, thinking: false, error: "", key: ""}); if (kind === "pricing") F.pct = priceChange(question); draw(); const m = $(".mainp"); if (m) m.scrollTop = 0; window.scrollTo(0, 0); }
function close() { F.open = false; draw(); if (S.spec.tab !== "overview") go("overview"); else renderSidebar(); }

/* ---------------------------------------------------------------- Gemini, through the server so the key stays there */
function accountFacts(v) { // what only the page knows: the focused view, the CRM record and what the KAM has captured
  const C = CUR(), ev = evidence();
  return `${v ? `<what_if_results>${JSON.stringify({title: v.title, summary: v.lead, caution: v.note || "", indicators: v.inds.map(i => ({label: i.l, value: i.v, note: i.n}))})}</what_if_results>\n` : ""}<crm>${JSON.stringify(C.crm)}</crm>
<reported_by_client>${JSON.stringify(ev.all.filter(e => e.status === "active").map(e => ({type: e.type, fields: e.fields, who: e.who, at: e.at})).slice(-40))}</reported_by_client>`;
}
async function askServer(text, spec, v) {
  const a = AGG(spec), hist = HISTORY[S.cid] || (HISTORY[S.cid] = []);
  const stop = new AbortController(), limit = setTimeout(() => stop.abort(), ASK_LIMIT_MS);
  const r = await fetch("/api/ask", {method: "POST", signal: stop.signal, headers: {"content-type": "application/json"}, body: JSON.stringify({
    customer_id: S.cid, question: text, start: D.months[spec.from], end: D.months[spec.to], baseline: spec.baseline,
    site_ids: spec.sites === "all" ? [] : a.siteRows.map(x => x.id), facts: accountFacts(v), canvas: !v, history: hist.slice(-12)})}).catch(e => { throw new Error(e.name === "AbortError" ? "it took too long to reply, please try again" : "the server could not be reached"); }).finally(() => clearTimeout(limit));
  let j = {}; try { j = await r.json(); } catch (e) { /* not JSON */ }
  if (!r.ok) throw new Error(j.error || `server answered ${r.status}`);
  if (!j.text) throw new Error(j.source || "no answer");
  hist.push({role: "user", content: text}, {role: "assistant", content: j.text});
  return j;
}
function typing(on) { let el = $("#iTyping"); if (!el) { el = document.createElement("div"); el.id = "iTyping"; el.textContent = "Thinking…"; const c = $("#ichips"); c.parentElement.insertBefore(el, c); } el.classList.toggle("on", on); }

Engine.name = "Gemini";
Engine.interpret = async (text, c) => {
  const R = parse(text, c), kind = classify(text), said = norm(text).trim(), id = ++REQUEST;
  const asked = QUESTION.test(said) || /^(compare|list|rank|explain|summari[sz]e|break down|top)\b/.test(said);
  const leave = () => { if (F.open) { F.open = false; draw(); renderSidebar(); } return R; };  // never leave an earlier answer on screen
  if ((R.capture && R.capture.length) || R.need || R.act) return leave();                  // new information and commands keep their own flow
  if (kind === "general" && !(AI.on && asked) && (Object.keys(R.patch).length || R.flags.length)) return leave();  // a plain view change stays on the dashboard
  const interactive = kind === "pricing" || (kind === "whatif" && /what.?if|scenario|sensitiv/.test(said));  // these have live controls
  if (kind === "pricing") R.flags = R.flags.filter(f => f !== "no_data");
  delete R.patch.tab; delete R.patch.highlight;                                            // the answer goes on the focused view instead
  const spec = {...S.spec, ...R.patch};
  open(kind, text, AI.on && !interactive);
  R.handled = true;
  if (!AI.on) { if (kind === "general") toggleDrawer(true); return R; }
  F.thinking = true; draw(); typing(true);
  const began = Date.now(), tick = setInterval(() => { const w = $("#fWait"); if (w) w.textContent = `… ${Math.round((Date.now() - began) / 1000)}s`; }, 1000);
  try {
    const j = await askServer(text, spec, interactive ? build(kind, spec) : null);
    R.answer = null; R.rank = null; R.crm = null; R.flags = R.flags.filter(f => !["no_data", "no_forecast", "cx_query"].includes(f));
    Insights.push("bot", `${md(j.text)}<div class="ai-src">${esc(j.source)}${j.untraced.length ? ` · ⚠ not found in the data: ${esc(j.untraced.join(", "))}` : ""}</div>`);
    setTimeout(() => { if (id === REQUEST && F.open) { Object.assign(F, {thinking: false, answer: j, key: stateKey()}); draw(); } }, 0);  // an older reply never replaces a newer question
  } catch (e) { if (id === REQUEST) { Object.assign(F, {thinking: false, dynamic: false, error: String(e.message || e)}); draw(); } Insights.note(`AI unavailable (${e.message || e}). Showing rule-based insights.`); }
  clearInterval(tick); typing(false);
  return R;
};

/* ---------------------------------------------------------------- wiring */
const _render = render; render = function (changed) { _render(changed); if (F.open) draw(); };
const _go = go; go = function (tab) { if (F.open) { F.open = false; draw(); } _go(tab); };
const _openAccount = openAccount; openAccount = function (cid) { F.open = false; F.contract = null; Object.assign(WHATIF, {scale: 1, rate: 1, pct: 0}); draw(); _openAccount(cid); };
const _sidebar = renderSidebar; renderSidebar = function () { // the What-if section takes the place of Data and method
  _sidebar();
  const b = $('#side [data-nav="method"]'); if (!b) return;
  b.querySelector("span").textContent = "What-if"; const here = F.open && F.kind === "whatif"; b.classList.toggle("on", here);
  if (here) { const o = $('#side [data-nav="overview"]'); if (o) o.classList.remove("on"); }
  b.onclick = () => { if (S.spec.tab !== "overview") go("overview"); open("whatif", ""); renderSidebar(); };
};
const _show = show; show = function (screen) { F.open = false; draw(); _show(screen); };
(function disclose() { // the existing cards, unchanged, one click away
  const grid = $(".ovg"), box = document.createElement("details"); box.id = "ovMore"; box.innerHTML = "<summary>Click to view details</summary>";
  grid.parentElement.insertBefore(box, grid); box.appendChild(grid);
})();
function label() { const el = $("#insEngine"); if (el) el.textContent = AI.on ? `${AI.model} · figures from the calculation layer` : `Offline rules · ${AI.reason}`; }
async function probe() {
  if (location.protocol === "file:") { AI.reason = "run server.py for AI"; return label(); }
  try { const j = await (await fetch("/api/health", {cache: "no-store"})).json(); AI.on = !!j.ai; AI.model = j.model || AI.model; AI.reason = j.ai ? "" : "set GEMINI_API_KEY for AI"; }
  catch (e) { AI.reason = "run server.py for AI"; }
  label();
}
window.Review = {F, classify, priceChange, open, close, build};
probe();
})();
