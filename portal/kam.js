/* ======================================================================
   KAM extension, core: information the KAM adds during the conversation (kept as dataset overlays), the
   nine evidence areas, and the What-if, Signals and KAM inputs tabs.

   It reuses the portal's own layer (compute, S, D, render, badge ...). Numbers still come from compute();
   KAM-entered information is stored separately, labelled "[Estimated] KAM input" and never presented as
   measured. The assistant (kam_ai.js) only proposes records; nothing is added without the KAM's confirmation.
   ====================================================================== */
(function () {
"use strict";
const KAM = window.KAM = window.KAM || {};

/* ---------------------------------------------------------------- team thresholds ([TEAM_ASSUMPTION]) */
const TH = {
  onTime: {g: 95, a: 90, why: "The data pack has no contractual SLA, so this is our working target: 95% of tracked trips on time or more is green, below 90% is red."},
  reqRise: {v: 1.15, why: "In a part-year view, a request rate more than 15% above the customer's own full-year rate moves service performance from green to amber."},
  gap: {watch: 17, high: 18, why: "Sites range from about 13.6% to 19.5% of allocated garments with no scan activity, around a 16% mean (SD about 1.3 points). 17% is about one SD above the mean; 18% singles out clear outliers. A signal for investigation, not a conclusion."},
  csat: {g: 4.2, a: 3.8, why: "On a 5-point scale, 4.2 or more is green and below 3.8 is red. Customer-reported and entered by the KAM; not verified."},
  nps: {g: 30, a: 0, why: "An NPS of 30 or more is green and below 0 is red. Customer-reported and entered by the KAM; not verified."},
  freed: {g: 2, a: 5, why: "Share of allocated garments that the median-benchmark right-sizing scenario would free: up to 2% green, up to 5% amber, above that red."},
  value: {g: 5, a: 2, why: "Garment purchases avoided by repairs as a share of contract spend for the period (needs the contract value from the KAM): 5% or more green, below 2% red."},
  cost: {a: 3, r: 8, why: "Year-on-year contract cost development: up to +3% green, up to +8% amber, more than +8% red."},
  hyg: {watch: 30, high: 40, why: "Share of traced hygiene garments with no scan for more than 180 days. Sites range from 21% to 50%, so these bands separate sites for review. The cycle log covers only about 6% of the fleet: low confidence."},
  repair: {watch: 1.10, high: 1.15, why: "Repairs per washed piece compared with the median for the same garment across the customer's sites, because garments differ naturally in how often they need repair."},
  renewal: {r: 90, a: 180, why: "A renewal within 90 days is red unless the scorecard is green; within 180 days it is amber."}
};
KAM.TH = TH;
const RANK = {g: 0, a: 1, r: 2};
const RAG = {g: ["#2e7d32", "Green"], a: ["#d99200", "Amber"], r: ["#c62828", "Red"], na: ["#8a8f98", "No data yet"]};
const MN = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
const worst = list => list.filter(Boolean).reduce((w, s) => (w == null || RANK[s] > RANK[w] ? s : w), null);
const lvl = (v, watch, high) => v == null ? null : v >= high ? "high" : v >= watch ? "watch" : "ok";
const plain = h => String(h).replace(/<[^>]+>/g, "");

/* ---------------------------------------------------------------- KAM inputs: kinds, parsing, validation */
const KIND = {
  feedback: {area: "experience", label: "Customer feedback"},
  incident: {area: "service", label: "Service incident"},
  late_delivery: {area: "service", label: "Late delivery"},
  volume: {area: "adoption", label: "Volume change"},
  price: {area: "financial", label: "Contract value"},
  risk_reduction: {area: "financial", label: "Risk reduction"},
  action: {area: "improvement", label: "Action"},
  issue: {area: "improvement", label: "Issue"},
  opportunity: {area: "relationship", label: "Opportunity"},
  priority: {area: "relationship", label: "Customer priority"}
};
KAM.KIND = KIND;
const clean = (v, max = 200) => String(v ?? "").replace(/<[^>]*>/g, "").replace(/\s+/g, " ").trim().slice(0, max);
const toNum = v => { if (typeof v === "number") return v; const n = parseFloat(String(v ?? "").replace(/\s/g, "").replace(",", ".")); return Number.isFinite(n) ? n : NaN; };

function parseMonth(v) { // calendar month of the data year -> {idx} | {idx:null} | {err}
  if (v == null || v === "") return {idx: null};
  if (typeof v === "number") return v >= 1 && v <= 12 ? {idx: Math.round(v) - 1} : {err: `Month ${v} is not a calendar month.`};
  const t = norm(String(v)); let m;
  if ((m = t.match(/^(\d{4})-(\d{1,2})$/))) return +m[1] !== D.year ? {err: `The data covers ${D.year} only (Jan–Dec ${D.year}); ${t} is outside it.`} : (+m[2] >= 1 && +m[2] <= 12 ? {idx: +m[2] - 1} : {err: `"${v}" is not a month.`});
  if (/^\d{1,2}$/.test(t)) return +t >= 1 && +t <= 12 ? {idx: +t - 1} : {err: `Month ${t} is not a calendar month.`};
  const yr = t.match(/\b(20\d\d)\b/); if (yr && +yr[1] !== D.year) return {err: `The data covers ${D.year} only (Jan–Dec ${D.year}); ${yr[1]} is outside it.`};
  const i = MN.findIndex(x => new RegExp("\\b" + x).test(t));
  return i >= 0 ? {idx: i} : {err: `I couldn't read "${v}" as a month.`};
}
function parseDue(v) {
  if (v == null || v === "") return {iso: null};
  const t = String(v).trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(t)) return Number.isNaN(Date.parse(t)) ? {err: `"${t}" is not a valid date.`} : {iso: t};
  const m = norm(t).match(/^(\d{1,2})\s+([a-z]+)(?:\s+(\d{4}))?$/);
  if (m) { const mi = MN.findIndex(x => m[2].startsWith(x)); if (mi >= 0) {
    let y = m[3] ? +m[3] : +DEMO_TODAY.slice(0, 4); let iso = `${y}-${String(mi + 1).padStart(2, "0")}-${String(+m[1]).padStart(2, "0")}`;
    if (!m[3] && iso < DEMO_TODAY) iso = `${y + 1}${iso.slice(4)}`; return Number.isNaN(Date.parse(iso)) ? {err: `"${t}" is not a valid date.`} : {iso}; } }
  return {err: `I couldn't read "${t}" as a date; use YYYY-MM-DD.`};
}
function resolveSite(v, cid) {
  if (v == null || v === "") return {id: null};
  const C = D.customers[custIx(cid)], t = norm(String(v)).replace(/^site:/, "");
  const hit = C.sites.map(si => D.sites[si]).find(s => norm(s.id) === t || norm(s.name) === t || new RegExp("\\b" + norm(s.name) + "\\b").test(t));
  return hit ? {id: hit.id} : {err: `"${v}" is not one of ${C.name}'s sites (${C.sites.map(si => D.sites[si].name).join(", ")}).`};
}
const monthName = i => `${MLAB[i]} ${D.year}`;
const siteName = id => id ? D.sites[siteById(id)].name : "all sites";

function validateInput(raw, cid = S.cid) {
  const k = String(raw.kind || "").toLowerCase().replace(/[\s-]+/g, "_");
  if (!KIND[k]) return {ok: false, errors: [`I don't know the record type "${raw.kind}".`]};
  const err = [], rec = {kind: k}, add = r => { if (r.err) err.push(r.err); return r; };
  const site = (required) => { const r = add(resolveSite(raw.site, cid)); if (required && !raw.site) err.push("Which site is this about?"); return r.id; };
  const month = (required) => { const r = add(parseMonth(raw.month)); if (required && raw.month == null) err.push("Which month is this about?"); return r.idx; };
  const count = () => { const n = toNum(raw.count ?? 1); if (!Number.isInteger(n) || n < 1 || n > 1000) err.push("The count must be a whole number between 1 and 1,000."); return n; };
  if (k === "feedback") {
    rec.metric = /nps|promoter/i.test(String(raw.metric)) ? "nps" : "csat";
    rec.value = toNum(raw.value);
    if (rec.metric === "csat") { rec.scale = toNum(raw.scale ?? 5); if (![5, 7, 10].includes(rec.scale)) err.push("CSAT scale must be 5, 7 or 10."); else if (!(rec.value >= 0 && rec.value <= rec.scale)) err.push(`CSAT must be between 0 and ${rec.scale}.`); }
    else if (!(rec.value >= -100 && rec.value <= 100)) err.push("NPS must be between -100 and 100.");
    rec.month = month(false); rec.site = site(false);
  } else if (k === "incident") {
    rec.site = site(true); rec.month = month(true); rec.count = count(); rec.type = clean(raw.type || "service request", 40);
  } else if (k === "late_delivery") {
    rec.site = site(true); rec.month = month(true); rec.count = count();
  } else if (k === "volume") {
    rec.site = site(true); rec.month = month(true);
    rec.field = /wear/i.test(String(raw.field)) ? "wearers" : "alloc"; rec.value = toNum(raw.value);
    if (!Number.isInteger(rec.value) || rec.value < 0 || rec.value > 100000) err.push("The new figure must be a whole number between 0 and 100,000.");
  } else if (k === "price") {
    rec.annual_eur = toNum(raw.annual_eur); if (!(rec.annual_eur >= 1000 && rec.annual_eur <= 1e9)) err.push("The annual contract value must be between €1,000 and €1 billion.");
    if (raw.prev_annual_eur != null && raw.prev_annual_eur !== "") { rec.prev_annual_eur = toNum(raw.prev_annual_eur); if (!(rec.prev_annual_eur >= 1000 && rec.prev_annual_eur <= 1e9)) err.push("The previous-year contract value must be between €1,000 and €1 billion."); }
  } else if (k === "risk_reduction") {
    rec.value_eur = toNum(raw.value_eur ?? raw.annual_eur ?? raw.value); if (!(rec.value_eur >= 100 && rec.value_eur <= 1e9)) err.push("The risk reduction must be between €100 and €1 billion.");
    rec.topic = clean(raw.topic || raw.title || "hygiene risk", 60).toLowerCase() || "hygiene risk"; rec.site = site(false);
  } else if (k === "action") {
    rec.title = clean(raw.title); if (!rec.title) err.push("What is the action?");
    rec.owner = clean(raw.owner, 60) || null; const d = add(parseDue(raw.due)); rec.due = d.iso || null;
    rec.status = ["open", "agreed", "done"].includes(String(raw.status)) ? raw.status : "open"; rec.site = site(false);
  } else if (k === "issue") {
    rec.title = clean(raw.title); if (!rec.title) err.push("What is the issue?");
    rec.site = site(false); rec.month = month(false); rec.recurring = raw.recurring !== false && raw.recurring !== "false";
  } else if (k === "opportunity") {
    rec.title = clean(raw.title); if (!rec.title) err.push("What is the opportunity?");
    if (raw.value_eur != null && raw.value_eur !== "") { rec.value_eur = toNum(raw.value_eur); if (!(rec.value_eur >= 0 && rec.value_eur <= 1e9)) err.push("The opportunity value must be between €0 and €1 billion."); }
  } else if (k === "priority") {
    rec.title = clean(raw.title); if (!rec.title) err.push("What is the priority?");
  }
  if (err.length) return {ok: false, errors: err};
  const note = clean(raw.note, 300); if (note) rec.note = note;
  const sig = JSON.stringify({...rec, note: undefined});
  if (KI.list(cid).some(r => JSON.stringify({...stripMeta(r), note: undefined}) === sig)) return {ok: false, errors: ["That is already recorded."], duplicate: true};
  return {ok: true, rec, summary: describeInput(rec)};
}
const stripMeta = r => { const {id, ts, via, text, active, ...rest} = r; return rest; };
KAM.validateInput = validateInput;

function describeInput(r) {
  const a = KIND[r.kind].area, at = r.site ? ` at <b>${esc(siteName(r.site))}</b>` : "";
  const when = r.month != null ? ` in <b>${monthName(r.month)}</b>` : "";
  const html = {
    feedback: () => `${r.metric === "csat" ? "CSAT" : "NPS"} <b>${r.value}${r.metric === "csat" ? ` / ${r.scale}` : ""}</b>${r.month != null ? ` · ${monthName(r.month)}` : " · latest survey"}${r.site ? ` · ${esc(siteName(r.site))}` : " · whole account"}`,
    incident: () => `<b>${r.count}</b> ${esc(r.type)}${r.count > 1 ? "s" : ""}${at}${when} → added to service requests`,
    late_delivery: () => `<b>${r.count}</b> late deliver${r.count > 1 ? "ies" : "y"}${at}${when} → added to tracked and late trips`,
    volume: () => `${r.field === "wearers" ? "Wearers" : "Allocated garments"}${at}${when} set to <b>${fmt(r.value)}</b> (site level; garment split unchanged)`,
    price: () => `Contract value <b>€${fmt(r.annual_eur)}</b> a year${r.prev_annual_eur ? `, previous year €${fmt(r.prev_annual_eur)}` : ""}`,
    risk_reduction: () => `Risk reduced: <b>€${fmt(r.value_eur)}</b> (${esc(r.topic)})${r.site ? ` · ${esc(siteName(r.site))}` : " · whole account"} → added to value, not to measured figures`,
    action: () => `Action: <b>${esc(r.title)}</b>${r.owner ? ` · owner ${esc(r.owner)}` : ""}${r.due ? ` · due ${niceDate(r.due)}` : ""} · ${r.status}`,
    issue: () => `${r.recurring ? "Recurring issue" : "Issue"}: <b>${esc(r.title)}</b>${r.site ? ` · ${esc(siteName(r.site))}` : ""}${r.month != null ? ` · ${monthName(r.month)}` : ""}`,
    opportunity: () => `Opportunity: <b>${esc(r.title)}</b>${r.value_eur ? ` · about €${fmt(r.value_eur)}` : ""}`,
    priority: () => `Customer priority: <b>${esc(r.title)}</b>`
  }[r.kind]();
  return {html, area: a};
}
KAM.describeInput = describeInput;

/* ---------------------------------------------------------------- store + dataset overlays */
const KI = {
  all: {},
  key: cid => "kam_inputs_" + cid,
  load() { D.customers.forEach(c => { KI.all[c.id] = store.get(KI.key(c.id), []); }); },
  save(cid) { store.set(KI.key(cid), KI.all[cid]); },
  list(cid = S.cid) { return (KI.all[cid] || []).filter(r => r.active !== false); },
  add(cid, rec, via = "chat", text = "") {
    const r = {...rec, id: "k" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6), ts: new Date().toISOString(), via, text: clean(text, 300), active: true};
    (KI.all[cid] = KI.all[cid] || []).push(r); KI.save(cid); overlayApply(); return r;
  },
  remove(cid, id) { const r = (KI.all[cid] || []).find(x => x.id === id); if (r) { r.active = false; KI.save(cid); overlayApply(); } return !!r; },
  clear(cid) { (KI.all[cid] || []).forEach(r => { r.active = false; }); KI.save(cid); overlayApply(); }
};
KAM.KI = KI;
const OV = {log: []};
function overlayRevert() { for (let k = OV.log.length - 1; k >= 0; k--) { const {arr, i, old} = OV.log[k]; arr[i] = old; } OV.log = []; }
function overlayApply() {
  overlayRevert();
  D.customers.forEach((C, ci) => KI.list(C.id).forEach(r => {
    const si = r.site ? siteById(r.site) : -1; if (si < 0 || r.month == null) return;
    const set = (arr, i, v) => { OV.log.push({arr, i, old: arr[i]}); arr[i] = v; }, cube = CUBE[ci][si];
    if (r.kind === "incident" && cube) set(cube.f.req, r.month, cube.f.req[r.month] + r.count);
    if (r.kind === "late_delivery") { const c = CYC[ci][si] || (CYC[ci][si] = {n: new Array(NM).fill(0), late: new Array(NM).fill(0)}); set(c.n, r.month, c.n[r.month] + r.count); set(c.late, r.month, c.late[r.month] + r.count); }
    if (r.kind === "volume" && cube) { set(cube[r.field], r.month, r.value); set(cube.present, r.month, true); }
  }));
  _cache.clear();
}
KAM.overlayApply = overlayApply;
function touched(a) { // overlay records inside this selection and period
  return KI.list(a.cid).filter(r => ["incident", "late_delivery", "volume"].includes(r.kind) && r.month >= a.f && r.month <= a.t && a.sites.includes(siteById(r.site)));
}
KAM.touched = touched;

/* ---------------------------------------------------------------- the nine evidence areas */
const AREAS = [
  {id: "scorecard", name: "Customer Success Scorecard", q: "Are we delivering successfully overall?", ind: "Green / amber / red account health", color: "#1d2f63", tab: "evidence"},
  {id: "experience", name: "Customer Experience", q: "Are our users/stakeholders satisfied?", ind: "CSAT, NPS, trend", color: "#cc2a68", tab: "inputs"},
  {id: "service", name: "Service Performance", q: "Is the service reliable?", ind: "Overall SLA/service performance", color: "#7b3596", tab: "service"},
  {id: "adoption", name: "Service Adoption", q: "Are we actually using the service effectively?", ind: "Active users/sites, service utilization", color: "#2f8fbf", tab: "sites"},
  {id: "operational", name: "Operational Value", q: "Is the service making operations better?", ind: "Efficiency, repairs, utilization, avoided replacements", color: "#16807c", tab: "circ"},
  {id: "financial", name: "Financial Value", q: "Are we getting value for what we spend?", ind: "Savings, avoided cost, cost development", color: "#d9701c", tab: "circ"},
  {id: "sustainability", name: "Sustainability Value", q: "What environmental value are we creating together?", ind: "CO2e, water, circularity, waste", color: "#3f8f2a", tab: "impact"},
  {id: "improvement", name: "Improvement", q: "Are the issues we raise actually being solved?", ind: "Open actions, recurring issues, improvement trend", color: "#4b5875", tab: "inputs"},
  {id: "relationship", name: "Relationship / Future", q: "What should we work on next?", ind: "Priorities, opportunities, agreed actions", color: "#8a6212", tab: "overview"}
];
KAM.AREAS = AREAS;
const EXAMPLES = {
  experience: "CSAT from the latest survey is 4.3 out of 5 and NPS is 32",
  financial: "Contract value is €420k a year, last year it was €405k",
  improvement: "Action: review jacket allocation at Helsinki, owner Anna, by 28 Feb",
  relationship: "Opportunity: extend hygiene audit reporting to the Swedish sites"
};
const byTs = (x, y) => String(x.ts).localeCompare(String(y.ts));
const whenKey = r => (r.month == null ? NM : r.month) * 1e14 + Date.parse(r.ts || 0);

function evidenceAreas(a, p) {
  const C = CUR(), cid = a.cid, ci = custIx(cid), y = compute(cid, 0, NM - 1, a.sel, a.bl);
  const recs = KI.list(cid).filter(r => !r.site || a.sites.includes(siteById(r.site)));
  const out = {}, put = (id, o) => { out[id] = {lines: [], tags: new Set(), kam: false, status: "na", ...o}; };
  const ln = (o, label, value, tag) => { o.lines.push([label, value, tag]); o.tags.add(tag); if (tag === "KAM") o.kam = true; };

  /* Customer Experience */
  { const o = {lines: [], tags: new Set(), kam: false, status: "na", head: "No survey data yet"};
    const fb = recs.filter(r => r.kind === "feedback"), sorted = m => fb.filter(r => r.metric === m).sort((x, z) => whenKey(x) - whenKey(z));
    const cs = sorted("csat"), np = sorted("nps"), st = [];
    if (cs.length) { const l = cs[cs.length - 1], v5 = l.value * 5 / l.scale, pv = cs.length > 1 ? cs[cs.length - 2] : null;
      st.push(v5 >= TH.csat.g ? "g" : v5 >= TH.csat.a ? "a" : "r");
      ln(o, "CSAT" + (l.month != null ? ` (${MLAB[l.month]})` : ""), `${l.value} / ${l.scale}`, "KAM");
      if (pv) { const d = v5 - pv.value * 5 / pv.scale; ln(o, "Change vs previous CSAT", `${d >= 0 ? "+" : ""}${fmt(d, 1)}`, "KAM"); } }
    if (np.length) { const l = np[np.length - 1], pv = np.length > 1 ? np[np.length - 2] : null;
      st.push(l.value >= TH.nps.g ? "g" : l.value >= TH.nps.a ? "a" : "r");
      ln(o, "NPS" + (l.month != null ? ` (${MLAB[l.month]})` : ""), `${l.value >= 0 ? "+" : ""}${l.value}`, "KAM");
      if (pv) ln(o, "Change vs previous NPS", `${l.value - pv.value >= 0 ? "+" : ""}${l.value - pv.value}`, "KAM"); }
    if (st.length) { o.status = worst(st); o.tags.add("TEAM"); o.head = [cs.length ? `CSAT ${cs[cs.length - 1].value}/${cs[cs.length - 1].scale}` : "", np.length ? `NPS ${np[np.length - 1].value >= 0 ? "+" : ""}${np[np.length - 1].value}` : ""].filter(Boolean).join(" · "); }
    else o.need = {text: "Satisfaction is not in the data pack. Add it from the conversation.", ask: EXAMPLES.experience};
    out.experience = o; }

  /* Service Performance */
  { const o = {lines: [], tags: new Set(), kam: false, status: "na", head: "No tracked trips"};
    const added = touched(a).filter(r => r.kind === "incident" || r.kind === "late_delivery");
    if (a.onTime != null) {
      o.head = `${fmt(a.onTime, 1)}% of trips on time`;
      let st = a.onTime >= TH.onTime.g ? "g" : a.onTime >= TH.onTime.a ? "a" : "r";
      if (st === "g" && a.nM < NM && a.reqPer1000 != null && y.reqPer1000 && a.reqPer1000 > y.reqPer1000 * TH.reqRise.v) st = "a";
      o.status = st; o.tags.add("TEAM");
      ln(o, "Tracked trips · late", `${fmt(a.cycles)} · ${fmt(a.late)}`, "SYNTHETIC");
      if (p && p.onTime != null) ln(o, `Change vs ${p.label}`, `${a.onTime - p.onTime >= 0 ? "+" : ""}${fmt(a.onTime - p.onTime, 1)} pts`, "CALCULATED");
    }
    ln(o, "Requests per 1,000 wearers / month", fmt(a.reqPer1000, 1), "CALCULATED");
    ln(o, "Request-free site-months", `${a.reqFree.n} of ${a.reqFree.of}`, "CALCULATED");
    if (added.length) { const rq = added.filter(r => r.kind === "incident").reduce((s, r) => s + r.count, 0), lt = added.filter(r => r.kind === "late_delivery").reduce((s, r) => s + r.count, 0);
      ln(o, "Added by the KAM", [rq ? `+${rq} requests` : "", lt ? `+${lt} late trips` : ""].filter(Boolean).join(", "), "KAM"); }
    out.service = o; }

  /* Service Adoption */
  { const o = {lines: [], tags: new Set(), kam: false, status: "na", head: "No data"};
    const active = a.siteRows.filter(r => r.alloc > 0).length, gap = a.util == null ? null : 100 - a.util;
    o.head = a.util == null ? "No data" : `${fmt(a.util, 1)}% of allocated garments active`;
    if (gap != null) { o.status = gap >= TH.gap.high ? "r" : gap >= TH.gap.watch ? "a" : "g"; o.tags.add("TEAM"); }
    ln(o, `Active wearers (end ${MLAB[a.t]})`, fmt(a.wearers), "CALCULATED");
    ln(o, "Sites using the service", `${active} of ${a.sites.length}`, "SYNTHETIC");
    ln(o, "Allocated garments with no scan activity", fmt(a.unused), "CALCULATED");
    ln(o, "Allocated garments per wearer", a.wearers ? fmt(a.alloc / a.wearers, 2) : "–", "CALCULATED");
    if (touched(a).some(r => r.kind === "volume")) ln(o, "Volume changes added by the KAM", String(touched(a).filter(r => r.kind === "volume").length), "KAM");
    out.adoption = o; }

  /* Operational Value */
  { const o = {lines: [], tags: new Set(), kam: false, status: "na", head: `${fmt(a.repairs)} repairs`};
    const freed = a.alloc ? 100 * a.rs.base.pieces / a.alloc : null;
    if (freed != null) { o.status = freed <= TH.freed.g ? "g" : freed <= TH.freed.a ? "a" : "r"; o.tags.add("TEAM"); }
    ln(o, "Replacements postponed", `${fmt(a.postponed.low)}–${fmt(a.postponed.high)}`, "ESTIMATED");
    ln(o, "Garments with scan activity", a.util == null ? "–" : fmt(a.util, 1) + "%", "CALCULATED");
    ln(o, "Right-sizing: garments freed (median benchmark)", `${fmt(a.rs.base.pieces)}${freed != null ? ` (${fmt(freed, 1)}% of allocated)` : ""}`, "SCENARIO");
    ln(o, "Recycled or reused textile waste", a.recycledPct == null ? "–" : fmt(a.recycledPct, 0) + "%", "ALLOCATED");
    out.operational = o; }

  /* Financial Value */
  { const o = {lines: [], tags: new Set(), kam: false, status: "na", head: `€${big(a.avoidedEur.low)}–${big(a.avoidedEur.high)} purchases avoided`};
    const price = KI.list(cid).filter(r => r.kind === "price").sort(byTs).pop(), whole = a.sel === "all", st = [];
    ln(o, "Purchases avoided by repairs", `€${big(a.avoidedEur.low)}–${big(a.avoidedEur.high)}`, "ESTIMATED");
    ln(o, "Right-sizing: future purchases avoided", `€${fmt(a.rs.low.eur)}–${fmt(a.rs.high.eur)}`, "SCENARIO");
    if (price) {
      ln(o, "Contract value", `€${fmt(price.annual_eur)} a year`, "KAM");
      if (whole) { const spend = price.annual_eur * a.days / 365, lo = 100 * a.avoidedEur.low / spend, hi = 100 * a.avoidedEur.high / spend;
        ln(o, "Avoided purchases vs contract spend", `${fmt(lo, 1)}–${fmt(hi, 1)}%`, "KAM"); st.push(lo >= TH.value.g ? "g" : lo >= TH.value.a ? "a" : "r"); }
      else ln(o, "Contract value scope", "account level; ratio shown for all sites", "KAM");
      if (price.prev_annual_eur) { const dev = 100 * (price.annual_eur / price.prev_annual_eur - 1);
        ln(o, "Cost development vs previous year", `${dev >= 0 ? "+" : ""}${fmt(dev, 1)}%`, "KAM"); st.push(dev <= TH.cost.a ? "g" : dev <= TH.cost.r ? "a" : "r"); }
    } else o.need = {text: "Contract value is not in the data pack. Add it to compare spend with value.", ask: EXAMPLES.financial};
    const rr = recs.filter(r => r.kind === "risk_reduction"), rrSum = rr.reduce((t, r) => t + r.value_eur, 0);
    if (rr.length) {
      o.head = `€${big(a.avoidedEur.low + rrSum)}–${big(a.avoidedEur.high + rrSum)} value incl. €${big(rrSum)} risk reduced`;
      ln(o, "Risk reduced (KAM estimate)", `€${fmt(rrSum)} · ${[...new Set(rr.map(r => r.topic))].join(", ")}`, "KAM");
      ln(o, "Purchases avoided + risk reduced", `€${fmt(a.avoidedEur.low + rrSum)}–${fmt(a.avoidedEur.high + rrSum)}`, "KAM");
    }
    if (st.length) { o.status = worst(st); o.tags.add("TEAM"); }
    out.financial = o; }

  /* Sustainability Value */
  { const sv = a.save, o = {lines: [], tags: new Set(), kam: false, status: sv.co2.low > 0 ? "g" : sv.co2.high > 0 ? "a" : "r", head: `${tonnes(sv.co2.base)} CO2e saved`};
    o.tags.add("TEAM");
    ln(o, "CO2e saved vs " + bname(a).toLowerCase(), `${tonnes(sv.co2.low)}–${tonnes(sv.co2.high)}`, "ALLOCATED");
    ln(o, "Water saved", `${big(sv.water.low, "L")}–${big(sv.water.high, "L")}`, "ALLOCATED");
    ln(o, "Circularity: recycled or reused", a.recycledPct == null ? "–" : fmt(a.recycledPct, 0) + "% of " + tonnes(a.waste), "ALLOCATED");
    ln(o, "Scope 3 input: washing and delivery CO2e", tonnes(a.flows.co2), "ALLOCATED");
    out.sustainability = o; }

  /* Improvement */
  { const o = {lines: [], tags: new Set(), kam: false, status: "na", head: "No actions or issues tracked yet"};
    const acts = recs.filter(r => r.kind === "action"), open = acts.filter(r => r.status !== "done"), overdue = open.filter(r => r.due && r.due < DEMO_TODAY);
    const issues = recs.filter(r => r.kind === "issue"), recurring = issues.filter(r => r.recurring);
    const proposed = (S.actions[cid] || []).filter(x => x.a).length;
    let trend = null;
    if (p && a.reqPer1000 != null && p.reqPer1000) { const d = 100 * (a.reqPer1000 / p.reqPer1000 - 1), ot = a.onTime != null && p.onTime != null ? a.onTime - p.onTime : 0;
      trend = d < -3 || ot > 0.3 ? (d > 3 || ot < -0.3 ? "mixed" : "improving") : d > 3 || ot < -0.3 ? "worsening" : "stable";
      ln(o, `Trend vs ${p.label}`, `requests per 1,000 ${d >= 0 ? "+" : ""}${fmt(d, 0)}%, on-time ${ot >= 0 ? "+" : ""}${fmt(ot, 1)} pts → ${trend}`, "CALCULATED"); }
    if (acts.length || issues.length || trend) {
      o.head = `${open.length} open action${open.length === 1 ? "" : "s"}`; o.tags.add("TEAM");
      o.status = overdue.length || recurring.length >= 2 || trend === "worsening" ? "r" : open.length || recurring.length || trend === "mixed" ? "a" : "g";
      if (trend === "worsening" && !overdue.length && recurring.length < 2) o.status = "a";
    }
    ln(o, "Open actions · overdue", `${open.length} · ${overdue.length}`, "KAM");
    ln(o, "Recurring issues", String(recurring.length), "KAM");
    if (proposed) ln(o, "Actions proposed in the review table", String(proposed), "CALCULATED");
    if (!acts.length && !issues.length) o.need = {text: "No actions or issues recorded yet. Add them as they come up.", ask: EXAMPLES.improvement};
    out.improvement = o; }

  /* Scorecard: worst of the seven evidence areas that have data */
  { const ids = ["experience", "service", "adoption", "operational", "financial", "sustainability", "improvement"], have = ids.filter(id => out[id].status !== "na");
    const cnt = {g: 0, a: 0, r: 0}; have.forEach(id => cnt[out[id].status]++);
    const o = {lines: [], tags: new Set(["TEAM"]), kam: have.some(id => out[id].kam), status: worst(have.map(id => out[id].status)) || "na", head: ""};
    o.head = o.status === "na" ? "No area has data" : `${RAG[o.status][1]} account health`;
    ln(o, "Green · amber · red", `${cnt.g} · ${cnt.a} · ${cnt.r}`, "CALCULATED");
    const need = ids.filter(id => out[id].status === "na").map(id => AREAS.find(x => x.id === id).name);
    ln(o, "Areas still without a status", need.length ? need.join(", ") : "none", "CALCULATED");
    out.scorecard = o; }

  /* Relationship / Future */
  { const o = {lines: [], tags: new Set(), kam: false, status: "g", head: ""};
    const rd = daysBetween(DEMO_TODAY, C.crm.renewal), card = out.scorecard.status;
    o.status = rd <= TH.renewal.r && card !== "g" ? "r" : rd <= TH.renewal.a ? "a" : "g"; o.tags.add("TEAM");
    o.head = `Renewal in ${rd} days`;
    const pri = [...C.stated, ...recs.filter(r => r.kind === "priority").map(r => r.title)];
    ln(o, "Customer priorities", pri.slice(0, 4).join(", "), pri.length > C.stated.length ? "KAM" : "SYNTHETIC");
    const opps = recs.filter(r => r.kind === "opportunity");
    ln(o, "Opportunities", opps.length ? opps.slice(0, 2).map(r => r.title).join("; ") : `right-sizing could free ${fmt(a.rs.base.pieces)} garments (scenario)`, opps.length ? "KAM" : "SCENARIO");
    ln(o, "Agreed actions", String(recs.filter(r => r.kind === "action" && r.status !== "open").length), "KAM");
    ln(o, "Next meeting", `${niceDate(C.crm.next_meeting[0])}`, "SYNTHETIC");
    out.relationship = o; }

  return AREAS.map(spec => {
    const o = out[spec.id], tags = [...o.tags]; return {...spec, ...o, tags};
  });
}
KAM.evidenceAreas = evidenceAreas;

const tagChips = (tags) => tags.map(badge).join(" ");
function evCard(ar) {
  const [rc, rl] = RAG[ar.status];
  return `<div class="ev" data-area="${ar.id}" data-tab="${ar.tab}" style="--ec:${ar.color}">
    <div class="hd"><b>${ar.name}</b><span class="rag" style="--rc:${rc}">${rl}</span></div>
    <div class="bd"><div class="q">${ar.q}</div><div class="big">${esc(ar.head)}</div>
      ${ar.lines.slice(0, 5).map(l => `<div><div class="ln"><span>${esc(l[0])}</span><b>${l[1] === "" ? "–" : esc(l[1])}</b></div><div class="tg" style="margin-top:2px;padding:0">${badge(l[2])}</div></div>`).join("")}
      ${ar.need ? `<div class="need kam">${esc(ar.need.text)}<br><button data-ask="${esc(ar.need.ask)}">Tell the assistant</button></div>` : ""}
      <div class="tg">${ar.status !== "na" ? badge("TEAM") + " " : ""}${ar.kam ? '<span class="kamchip">includes KAM input</span>' : ""}</div></div></div>`;
}
function renderEvidence() {
  const el = $("#evidence"); if (!el || !S.cid) return;
  const a = AGG(), p = PREV(), areas = evidenceAreas(a, p);
  el.innerHTML = `<div class="evh"><h2>Evidence areas · ${a.label}</h2><span class="muted small">${sitesLabel(CUR(), S.spec.sites)} · status colours follow team thresholds ${badge("TEAM")} (see Data &amp; method) · click an area to open its detail</span></div>
    <div class="evgrid">${areas.map(evCard).join("")}</div>`;
  $$("#evidence .ev").forEach(c => c.onclick = e => { if (e.target.closest("[data-ask]")) return; S.spec.tab = c.dataset.tab; render(); $("#tabs").scrollIntoView({behavior: "smooth", block: "start"}); });
  $$("#evidence [data-ask]").forEach(b => b.onclick = () => KAM.prefill(b.dataset.ask));
}
KAM.renderEvidence = renderEvidence;
KAM.flashAreas = ids => ids.forEach(id => $$(`#evidence .ev[data-area="${id}"]`).forEach(el => { el.classList.remove("flash2"); void el.offsetWidth; el.classList.add("flash2"); }));
KAM.prefill = text => { S.insClosed = false; document.body.classList.remove("insclosed"); const i = $("#iIn"); if (i) { i.value = text; i.focus(); } if (typeof fitPanel === "function") fitPanel(); };

/* ---------------------------------------------------------------- Evidence areas tab */
function panelEvidence(a, p) {
  const areas = evidenceAreas(a, p);
  $("#panel").innerHTML = `<h2>The nine evidence areas · ${a.label}</h2>
    <div class="muted small">What the customer wants to know, the indicator we use, the current evidence and its status. Every line carries its provenance tag. Statuses use team thresholds ${badge("TEAM")}; where the data pack has no figure, the KAM can add it from the conversation through the assistant.</div>
    <div class="tscroll" style="margin-top:12px;max-height:none"><table class="evtab"><thead><tr><th class="l">Evidence area</th><th class="l">What the customer wants to know</th><th class="l">Indicator</th><th class="l">Current evidence</th><th class="l">Status</th></tr></thead><tbody>
    ${areas.map(ar => `<tr><td class="l"><b style="color:${ar.color}">${ar.name}</b></td><td class="l">${ar.q}</td><td class="l">${ar.ind}</td>
      <td class="l"><b>${esc(ar.head)}</b>${ar.lines.map(l => `<div style="margin-top:3px">${esc(l[0])}: <b>${esc(l[1])}</b> ${badge(l[2])}</div>`).join("")}${ar.need ? `<div class="kam" style="margin-top:6px"><span class="muted small">${esc(ar.need.text)}</span> <button class="pill ghost" style="padding:2px 10px;font-size:12px" data-ask="${esc(ar.need.ask)}">Tell the assistant</button></div>` : ""}</td>
      <td class="l"><span class="rag" style="--rc:${RAG[ar.status][0]};border:1px solid #d5d8de">${RAG[ar.status][1]}</span>${ar.kam ? '<div style="margin-top:4px"><span class="kamchip">includes KAM input</span></div>' : ""}</td></tr>`).join("")}
    </tbody></table></div>
    <div class="note">Scorecard rule: the worst status among the seven evidence areas that have data; areas without data are listed, not guessed. KAM-entered figures are customer-reported or KAM-reported and are not verified against operational records.</div>`;
  $$("#panel [data-ask]").forEach(b => b.onclick = () => KAM.prefill(b.dataset.ask));
}

/* ---------------------------------------------------------------- What-if tab */
const WI = {scale: 1, rate: 0.75, preset: "base"};
const WI_PRESETS = {low: {scale: 0.8, rate: 0.5}, base: {scale: 1, rate: 0.75}, high: {scale: 1.2, rate: 1}};
function whatIf(a, scale = WI.scale, rate = WI.rate) {
  const F = a.flows, out = {};
  ["water", "energy", "co2"].forEach(k => { out[k] = scale * a.base[k] - F[k]; });
  out.postponed = F.rep * rate; out.eur = F.rep_eur * rate; out.prodKg = F.rep_kg * rate;
  return out;
}
KAM.whatIf = whatIf;
function panelWhatIf(a, p) {
  const sp = S.spec, w = whatIf(a), lo = whatIf(a, 0.8, 0.5), hi = whatIf(a, 1.2, 1);
  const rows = Object.entries(D.baselines).map(([id, b]) => { const sv = k => b[k] * a.flows.wkg - a.flows[k]; return {id, name: b.name, water: sv("water"), energy: sv("energy"), co2: sv("co2")}; });
  $("#panel").innerHTML = `<h2>What-if · ${a.label} · ${sitesLabel(CUR(), sp.sites)}</h2>
    <div class="muted small">Change the comparison baseline and the team assumptions to see how far the estimated savings move. Everything here is exploratory ${badge("ESTIMATED")}; the dashboard keeps the standard assumptions until you change the baseline in the view bar.</div>
    <div class="wi-ctl">
      <div class="box"><b>Comparison baseline</b><div class="seg" style="margin-top:6px">${Object.entries(D.baselines).map(([id, b]) => `<label class="${sp.baseline === id ? "on" : ""}" style="min-width:0;flex:1 1 100%"><input type="radio" name="wibl" value="${id}" ${sp.baseline === id ? "checked" : ""}>${b.name}</label>`).join("")}</div></div>
      <div class="box"><b>Baseline factor scale</b> ${badge("TEAM")}<div class="val" id="wiScaleV">${fmt(WI.scale, 2)}×</div><input type="range" id="wiScale" min="0.8" max="1.2" step="0.05" value="${WI.scale}"><div class="muted small">Illustrative baseline factors can be off by ±20% (T001).</div></div>
      <div class="box"><b>Replacements postponed per repair</b> ${badge("TEAM")}<div class="val" id="wiRateV">${fmt(WI.rate, 2)}</div><input type="range" id="wiRate" min="0.5" max="1" step="0.05" value="${WI.rate}"><div class="muted small">0.5–1 per repair (A002, T004).</div></div></div>
    <div class="seg" style="margin:0 0 12px">${["low", "base", "high"].map(c => `<label class="${WI.preset === c ? "on" : ""}" style="flex:0 1 160px;min-width:0"><input type="radio" name="wip" value="${c}" ${WI.preset === c ? "checked" : ""}><b>${c[0].toUpperCase() + c.slice(1)} case</b><div class="muted small">${WI_PRESETS[c].scale}× baseline · ${WI_PRESETS[c].rate} per repair</div></label>`).join("")}</div>
    <div class="cols"><div><h3>Result at your settings ${badge("ESTIMATED")}</h3>
      <table><thead><tr><th class="l">Estimated saving vs ${esc(bname(a).toLowerCase())}</th><th>Low case</th><th>Your settings</th><th>High case</th></tr></thead><tbody>
      <tr><td class="l">Water</td><td>${big(lo.water, "L")}</td><td><b>${big(w.water, "L")}</b></td><td>${big(hi.water, "L")}</td></tr>
      <tr><td class="l">Energy</td><td>${big(lo.energy, "kWh")}</td><td><b>${big(w.energy, "kWh")}</b></td><td>${big(hi.energy, "kWh")}</td></tr>
      <tr><td class="l">CO2e</td><td>${tonnes(lo.co2)}</td><td><b>${tonnes(w.co2)}</b></td><td>${tonnes(hi.co2)}</td></tr>
      <tr><td class="l">Replacements postponed</td><td>${fmt(lo.postponed)}</td><td><b>${fmt(w.postponed)}</b></td><td>${fmt(hi.postponed)}</td></tr>
      <tr><td class="l">Garment purchases avoided</td><td>€${big(lo.eur)}</td><td><b>€${big(w.eur)}</b></td><td>€${big(hi.eur)}</td></tr>
      <tr><td class="l">Manufacturing CO2e avoided</td><td>${tonnes(lo.prodKg)}</td><td><b>${tonnes(w.prodKg)}</b></td><td>${tonnes(hi.prodKg)}</td></tr></tbody></table>
      <div class="note">Same ${tonnes(a.flows.wkg)} washed kilograms in every case. A saving is the difference between two estimates, so baseline uncertainty widens it.</div></div>
      <div><h3>The three baselines side by side ${badge("ALLOCATED")}</h3>
      <table><thead><tr><th class="l">Baseline</th><th>Water</th><th>Energy</th><th>CO2e</th></tr></thead><tbody>
      ${rows.map(r => `<tr class="${r.id === sp.baseline ? "sel" : ""}"><td class="l">${r.name}${r.id === sp.baseline ? " ✓" : ""}</td><td>${big(r.water, "L")}</td><td>${big(r.energy, "kWh")}</td><td>${tonnes(r.co2)}</td></tr>`).join("")}</tbody></table>
      <div class="note">Home washing and in-house laundry factors are illustrative values from the data pack. Standard-scale figures; the scale slider on the left does not change this table.</div>
      <h3 style="margin-top:14px">Right-sizing benchmark ${badge("SCENARIO")}</h3>
      <div class="seg">${["low", "base", "high"].map(x => `<label class="${S.rcase === x ? "on" : ""}" style="min-width:0"><input type="radio" name="wirc" value="${x}" ${S.rcase === x ? "checked" : ""}><b>${x === "low" ? "Cautious" : x === "base" ? "Median" : "Ambitious"}</b><div class="muted small">${fmt(a.rs[x].pieces)} garments · €${fmt(a.rs[x].eur)}</div></label>`).join("")}</div></div></div>`;
  $$("input[name=wibl]").forEach(r => r.onchange = () => setSpec({baseline: r.value}));
  $$("input[name=wip]").forEach(r => r.onchange = () => { WI.preset = r.value; Object.assign(WI, WI_PRESETS[r.value]); render(); });
  $$("input[name=wirc]").forEach(r => r.onchange = () => { S.rcase = r.value; render(); });
  // while dragging only the number moves; the panel is recomputed on release so the slider keeps focus
  $("#wiScale").oninput = e => { $("#wiScaleV").textContent = fmt(+e.target.value, 2) + "×"; };
  $("#wiScale").onchange = e => { WI.scale = +e.target.value; WI.preset = ""; render(); };
  $("#wiRate").oninput = e => { $("#wiRateV").textContent = fmt(+e.target.value, 2); };
  $("#wiRate").onchange = e => { WI.rate = +e.target.value; WI.preset = ""; render(); };
}

/* ---------------------------------------------------------------- Signals tab */
function signals(a) {
  const C = CUR(), ci = custIx(a.cid), cube = CUBE[ci], sel = new Set(a.sites);
  const fleet = a.siteRows.filter(r => r.alloc > 0).map(r => { const share = 100 * r.unused / r.alloc; return {id: r.id, name: r.name, country: r.country, alloc: r.alloc, gap: r.unused, share, level: lvl(share, TH.gap.watch, TH.gap.high)}; }).sort((x, z) => z.share - x.share);
  const ex = (typeof EXTRA !== "undefined" && EXTRA.sites) || {};
  const hyg = a.sites.map(si => D.sites[si]).filter(s => ex[s.id] && ex[s.id].traced).map(s => { const e = ex[s.id], share = 100 * e.flagged / e.traced; return {id: s.id, name: s.name, alloc: e.allocated, traced: e.traced, flagged: e.flagged, share, level: lvl(share, TH.hyg.watch, TH.hyg.high)}; }).sort((x, z) => z.share - x.share);
  const rep = []; // repairs per washed piece by site and garment, against the garment's median over the customer's sites
  Object.keys(D.products).forEach(pi => {
    const rows = C.sites.map(si => { const pr = cube[si] && cube[si].prod[pi]; if (!pr) return null; let w = 0, r = 0; for (let m = a.f; m <= a.t; m++) { w += pr.wpieces[m]; r += pr.rep[m]; } return w ? {si, repairs: r, washed: w, rate: 100 * r / w} : null; }).filter(Boolean);
    const med = quant(rows.map(r => r.rate), .5); if (!med) return;
    rows.filter(r => sel.has(r.si)).forEach(r => { const ratio = r.rate / med; rep.push({site: D.sites[r.si].name, product: D.products[pi].name, repairs: r.repairs, rate: r.rate, ratio, level: lvl(ratio, TH.repair.watch, TH.repair.high)}); });
  });
  rep.sort((x, z) => z.ratio - x.ratio);
  const n = l => l.filter(x => x.level === "watch" || x.level === "high").length;
  const riskRecs = KI.list(a.cid).filter(r => r.kind === "risk_reduction" && (!r.site || sel.has(siteById(r.site))));
  return {riskReduced: {eur: riskRecs.reduce((t, r) => t + r.value_eur, 0), recs: riskRecs}, fleet, hygiene: hyg, repair: rep, counts: {fleet: n(fleet), repair: n(rep), hygiene: n(hyg), active: n(fleet) + n(rep)},
    hygieneTotals: hyg.reduce((t, h) => ({traced: t.traced + h.traced, flagged: t.flagged + h.flagged, alloc: t.alloc + h.alloc}), {traced: 0, flagged: 0, alloc: 0})};
}
KAM.signals = signals;
const sigChip = l => l ? `<span class="sig ${l}">${l === "ok" ? "OK" : l === "watch" ? "Watch" : "High"}</span>` : "–";
function panelSignals(a) {
  const g = signals(a), c = g.counts, ht = g.hygieneTotals, ex = typeof EXTRA !== "undefined" ? EXTRA : null;
  const fleetAcc = a.alloc ? 100 * a.unused / a.alloc : null;
  $("#panel").innerHTML = `<h2>Operational risk and optimization signals · ${a.label}</h2>
    <div class="muted small">${badge("TEAM")} Bands are team thresholds with a stated rationale (Data &amp; method). Signals tell us where to look first; they are not findings.</div>
    <div class="sigsum"><div><b>${c.active}</b><span class="muted small">active risk signals (fleet + repair)</span><br>${badge("CALCULATED")} ${badge("TEAM")}</div>
      <div><b>${c.fleet}</b><span class="muted small">sites with a high share of allocated garments with no scan activity</span><br>${badge("CALCULATED")}</div>
      <div><b>${c.repair}</b><span class="muted small">site and garment pairs with a high repair rate</span><br>${badge("CALCULATED")}</div>
      <div><b>${ht.traced ? `${fmt(ht.flagged)} of ${fmt(ht.traced)}` : "–"}</b><span class="muted small">traced hygiene garments with no scan for over ${ex ? ex.days : 180} days (low confidence)</span><br>${badge("CALCULATED")}</div>${g.riskReduced.eur ? `<div><b>€${fmt(g.riskReduced.eur)}</b><span class="muted small">risk reduced, recorded by the KAM</span><br>${badge("KAM")}</div>` : ""}</div>

    <h3>1. Fleet allocation signal: allocated garments with no scan activity ${badge("CALCULATED")}</h3>
    <div class="caveat"><b>Optimization signal for investigation, not a conclusion.</b> The gap may reflect low wash frequency, delayed pickups or uncaptured RFID scans. Account level at end of ${MLAB[a.t]}: <b>${fmt(a.unused)}</b> of ${fmt(a.alloc)} allocated garments${fleetAcc != null ? ` (${fmt(fleetAcc, 1)}%)` : ""}. Watch from ${TH.gap.watch}%, high from ${TH.gap.high}% of a site's allocation.</div>
    <div class="cols"><div class="tscroll" style="max-height:380px"><table><thead><tr><th class="l">Site</th><th>Allocated</th><th class="wrapth">Allocated garments with no scan activity</th><th>Share %</th><th>Signal</th></tr></thead><tbody>
      ${g.fleet.map(r => `<tr><td class="l">${esc(r.name)}</td><td>${fmt(r.alloc)}</td><td>${fmt(r.gap)}</td><td>${fmt(r.share, 1)}</td><td>${sigChip(r.level)}</td></tr>`).join("")}</tbody></table></div>
      <div class="tscroll" style="max-height:380px;border:0">${hbars(g.fleet.slice(0, 14).map(r => ({l: r.name, v: r.share})), {dec: 1, unit: "%", ref: fleetAcc, refLabel: "account", highlight: it => it.v >= TH.gap.watch, color: "var(--t-orange)"})}</div></div>

    <h3 style="margin-top:20px">2. Hygiene risk flag: no laundry scan for over ${ex ? ex.days : 180} days ${badge("CALCULATED")} ${badge("TEAM")}</h3>
    ${g.riskReduced.recs.length ? `<div class="caveat" style="background:#fde7f0;border-color:#f2bcd3;color:#8a1c52"><b>Risk reduction recorded by the KAM: €${fmt(g.riskReduced.eur)}</b> ${badge("KAM")}<br>${g.riskReduced.recs.map(r => `${esc(r.topic)}${r.site ? ` · ${esc(siteName(r.site))}` : " · whole account"}: €${fmt(r.value_eur)}`).join("<br>")}<br><span class="small">Customer-reported or KAM-reported value, not measured. It is included in Financial Value and does not change the scan-based flag below.</span></div>` : ""}
    ${g.hygiene.length ? `<div class="caveat"><b>Low-confidence screen.</b> The cycle log traces ${fmt(ht.traced)} of ${fmt(ht.alloc)} allocated hygiene garments (${fmt(100 * ht.traced / Math.max(1, ht.alloc), 1)}%), and no garment has more than 170 days between two recorded scans, so every flag comes from the log ending (as of ${niceDate(ex.asOf)}), not from an observed gap. Confirm with full RFID records before raising it as a hygiene concern. Independent of the selected period. ${esc(ex.groups.join(" and "))} garments only.</div>
      <div class="tscroll" style="max-height:300px"><table><thead><tr><th class="l">Site</th><th>Allocated hygiene garments</th><th>Traced in log</th><th>Flagged</th><th>Share of traced %</th><th>Signal</th></tr></thead><tbody>
      ${g.hygiene.map(r => `<tr><td class="l">${esc(r.name)}</td><td>${fmt(r.alloc)}</td><td>${fmt(r.traced)}</td><td>${fmt(r.flagged)}</td><td>${fmt(r.share, 0)}</td><td>${sigChip(r.level)}</td></tr>`).join("")}</tbody></table></div>`
      : `<p class="muted">No hygiene-critical garments (food industry or healthcare) are traced in this selection.</p>`}

    <h3 style="margin-top:20px">3. Repair versus retirement signal ${badge("CALCULATED")}</h3>
    <div class="note" style="margin:0 0 6px">Repairs per 100 washed pieces compared with the median for the same garment across ${esc(CUR().name)}'s sites. The repair data has no garment IDs, so this is by site and garment type, not by individual garment. Watch from ${TH.repair.watch}×, high from ${TH.repair.high}× the median. High repair rates suggest asking whether an upgrade would cost less than continued repair.</div>
    <div class="tscroll" style="max-height:300px"><table><thead><tr><th class="l">Site</th><th class="l">Garment</th><th>Repairs</th><th>Per 100 washes</th><th>vs median</th><th>Signal</th></tr></thead><tbody>
      ${g.repair.filter(r => r.level !== "ok").slice(0, 25).map(r => `<tr><td class="l">${esc(r.site)}</td><td class="l">${esc(r.product)}</td><td>${fmt(r.repairs)}</td><td>${fmt(r.rate, 2)}</td><td>${fmt(r.ratio, 2)}×</td><td>${sigChip(r.level)}</td></tr>`).join("") || `<tr><td class="l" colspan="6">No site and garment pair is at or above the watch band.</td></tr>`}</tbody></table></div>`;
}

/* ---------------------------------------------------------------- KAM inputs tab */
function panelInputs(a) {
  const cid = a.cid, list = KI.list(cid), C = CUR();
  $("#panel").innerHTML = `<h2>Information added by the KAM · ${esc(C.name)}</h2>
    <div class="muted small">Things that come up in the conversation and are not in the data pack: survey scores, incidents, contract value, actions, opportunities. The assistant proposes each record and nothing is added until you confirm. Records are stored in this browser and labelled ${badge("KAM")}: customer-reported or KAM-reported, not verified against operational records. Incidents, late deliveries and volume changes also update the figures they belong to.</div>
    <div class="kin-ex kam">${Object.values(EXAMPLES).concat(["Hygiene risk reduced by €25,000 at this account", "Oulu had 3 late deliveries in November", "2 complaints at Helsinki in October", "Recurring issue: wrong jacket sizes at Turku"]).map(t => `<button data-ask="${esc(t)}">${esc(t)}</button>`).join("")}</div>
    ${list.length ? `<div class="tscroll" style="max-height:none"><table class="kin-table"><thead><tr><th class="l">Added</th><th class="l">Record</th><th class="l">Feeds</th><th class="l">Source</th><th></th></tr></thead><tbody>
      ${list.slice().reverse().map(r => { const d = describeInput(r); return `<tr><td class="l">${new Date(r.ts).toLocaleString("en-GB", {day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"})}</td><td class="l">${d.html}${r.note ? `<div class="muted small">${esc(r.note)}</div>` : ""}</td><td class="l">${AREAS.find(x => x.id === d.area).name}</td><td class="l">${r.via} ${badge("KAM")}</td><td><button class="pill ghost" data-rm="${r.id}" style="padding:2px 10px">✕</button></td></tr>`; }).join("")}</tbody></table></div>`
      : `<p class="muted" style="margin-top:12px">Nothing added yet. Try one of the examples above, or just tell the assistant.</p>`}
    <div class="footer-actions kam" style="justify-content:flex-start"><button class="pill" id="kinExport">Export JSON</button><label class="pill ghost" style="cursor:pointer">Import JSON<input type="file" id="kinImport" accept="application/json" style="display:none"></label><button class="pill ghost" id="kinClear" ${list.length ? "" : "disabled"}>Remove all</button></div>
    <div class="note">Limits: incidents, late deliveries and volume changes need a site and a month within ${D.year}, because the data pack is monthly and ends in December ${D.year}. Anything else (actions, priorities, opportunities, issues, survey scores, contract value) can be recorded at any time.</div>`;
  $$("#panel [data-ask]").forEach(b => b.onclick = () => KAM.prefill(b.dataset.ask));
  $$("[data-rm]").forEach(b => b.onclick = () => { KI.remove(cid, b.dataset.rm); render(); });
  $("#kinClear").onclick = () => { if (confirm("Remove all information added for this account?")) { KI.clear(cid); render(); } };
  $("#kinExport").onclick = () => { const blob = new Blob([JSON.stringify({account: cid, exported: new Date().toISOString(), records: list.map(r => ({...r}))}, null, 1)], {type: "application/json"}); const l = document.createElement("a"); l.href = URL.createObjectURL(blob); l.download = `${C.name.replace(/\s+/g, "_")}_kam_inputs.json`; l.click(); };
  $("#kinImport").onchange = e => { const f = e.target.files[0]; if (!f) return; f.text().then(t => { let n = 0; try { (JSON.parse(t).records || []).forEach(r => { const v = validateInput(r, cid); if (v.ok) { KI.add(cid, v.rec, "import", r.text || ""); n++; } }); } catch (x) { alert("That file is not a valid export."); return; } render(); alert(`Imported ${n} record${n === 1 ? "" : "s"}.`); }); };
}

/* ---------------------------------------------------------------- data readiness, exports, method */
KAM.readiness = () => {
  const has = k => KI.list().some(r => r.kind === k), missing = [];
  if (!has("feedback")) missing.push("satisfaction (CSAT, NPS)");
  if (!has("price")) missing.push("contract price");
  const base = "service-request types and resolution times, data after December " + D.year + " and a prior-year comparison are not in the data pack";
  const added = KI.list().length;
  return `${missing.length ? esc(missing.join(" and ")) + ", " : ""}${base}.${added ? ` <b>${added}</b> record${added === 1 ? "" : "s"} added by the KAM are included and labelled.` : " Tell the assistant to add what comes up in the meeting."}`;
};
function methodExtras() {
  const rows = [["Customer Experience", "CSAT 4.2+ green, below 3.8 red; NPS 30+ green, below 0 red (worst of the two)", TH.csat.why],
    ["Service Performance", `On-time ≥ ${TH.onTime.g}% green, < ${TH.onTime.a}% red; in part-year views, requests > ${TH.reqRise.v}× the full-year rate lowers green to amber`, TH.onTime.why],
    ["Service Adoption", `Allocated garments with no scan activity: below ${TH.gap.watch}% green, ${TH.gap.watch}–${TH.gap.high}% amber, ${TH.gap.high}%+ red`, TH.gap.why],
    ["Operational Value", `Garments freed by the median right-sizing benchmark: ≤ ${TH.freed.g}% green, ≤ ${TH.freed.a}% amber`, TH.freed.why],
    ["Financial Value", `Avoided purchases vs contract spend ≥ ${TH.value.g}% green, < ${TH.value.a}% red; cost development ≤ +${TH.cost.a}% green, > +${TH.cost.r}% red`, TH.value.why + " " + TH.cost.why],
    ["Sustainability Value", "CO2e saving range above zero green, straddling zero amber, below zero red", "The saving is the difference of two estimates, so the sign is what the range can safely say."],
    ["Improvement", "Overdue actions or 2+ recurring issues red; any open action or recurring issue amber; none green", "Needs actions or issues recorded by the KAM; the trend uses requests and on-time against the previous period."],
    ["Relationship / Future", `Renewal within ${TH.renewal.r} days red unless the scorecard is green, within ${TH.renewal.a} days amber`, TH.renewal.why],
    ["Customer Success Scorecard", "Worst status among the seven areas above that have data", "A conservative roll-up: one red area makes the account amber or red until it is explained."],
    ["Signals: hygiene flag", `${TH.hyg.watch}% watch, ${TH.hyg.high}% high (share of traced hygiene garments with no scan for over 180 days)`, TH.hyg.why],
    ["Signals: repair vs retirement", `${TH.repair.watch}× watch, ${TH.repair.high}× high the median repair rate`, TH.repair.why]];
  return `<div style="grid-column:1/-1;margin-top:8px"><h2>Evidence areas and signals: how status is set ${badge("TEAM")}</h2>
    <div class="tscroll" style="max-height:340px"><table><thead><tr><th class="l">Area</th><th class="l">Rule</th><th class="l">Rationale</th></tr></thead><tbody>${rows.map(r => `<tr><td class="l"><b>${r[0]}</b></td><td class="l" style="white-space:normal">${esc(r[1])}</td><td class="l" style="white-space:normal">${esc(r[2])}</td></tr>`).join("")}</tbody></table></div>
    <h2 style="margin-top:16px">Information added by the KAM</h2><ul class="small"><li>Entered through the assistant (or imported), shown only after the KAM confirms, and labelled ${badge("KAM")}: it is customer-reported or KAM-reported, not measured.</li>
      <li>Incidents add to service requests, late deliveries add to tracked and late trips, volume changes replace the site's monthly wearers or allocated garments. Removing a record restores the original figure.</li>
      <li>Provenance tags: ${badge("SYNTHETIC")} operational records; ${badge("CALCULATED")} deterministic formulas; ${badge("ALLOCATED")} baseline comparisons and allocated shares; ${badge("ESTIMATED")} ranges and thresholds set by our team.</li>
      <li>This is a decision-support tool for steering meetings, not audited compliance or CSRD reporting, and the data is synthetic.</li></ul></div>`;
}

/* ---------------------------------------------------------------- exports */
KAM.pdfSection = sec => {
  const a = AGG(), p = PREV(), areas = evidenceAreas(a, p);
  return sec("★", "Evidence areas", `<table class="act"><thead><tr><th>Area</th><th>Status</th><th>Headline</th><th>Evidence</th></tr></thead><tbody>${areas.map(ar => `<tr><td>${ar.name}</td><td>${RAG[ar.status][1]}</td><td>${esc(ar.head)}</td><td>${ar.lines.map(l => `${esc(l[0])}: ${esc(l[1])} ${STATUS[l[2]].t}`).join("<br>")}</td></tr>`).join("")}</tbody></table>
    <p class="note">Status colours follow team thresholds [TEAM_ASSUMPTION]. Lines tagged KAM input are customer-reported or KAM-reported and not verified.</p>`);
};
KAM.pptxExtras = (p, a, title) => {
  const areas = evidenceAreas(a, PREV()), hex = {g: "2E7D32", a: "D99200", r: "C62828", na: "8A8F98"};
  let s = p.addSlide(); title(s, `Evidence areas · ${a.label}`, "What the customer wants to know, with status and provenance tags");
  areas.forEach((ar, i) => { const x = .5 + (i % 3) * 4.15, y = 1.5 + Math.floor(i / 3) * 1.85;
    s.addShape(p.ShapeType.rect, {x, y, w: 3.95, h: 1.7, fill: {color: "FFFFFF"}, line: {color: "E3E5E8", width: 1}});
    s.addShape(p.ShapeType.rect, {x, y, w: 3.95, h: .38, fill: {color: ar.color.replace("#", "")}});
    s.addText(ar.name, {x: x + .1, y, w: 2.7, h: .38, fontSize: 11, bold: true, color: "FFFFFF"});
    s.addText(RAG[ar.status][1], {x: x + 2.75, y: y + .05, w: 1.1, h: .28, fontSize: 9, bold: true, color: "FFFFFF", fill: {color: hex[ar.status]}, align: "center"});
    s.addText(ar.head, {x: x + .1, y: y + .42, w: 3.75, h: .5, fontSize: 13, color: "1F2328", bold: true});
    s.addText(ar.lines.slice(0, 2).map(l => `${l[0]}: ${l[1]} ${STATUS[l[2]].t}`).join("\n"), {x: x + .1, y: y + .9, w: 3.75, h: .75, fontSize: 8.5, color: "4A4F57", valign: "top"}); });
  const g = signals(a);
  s = p.addSlide(); title(s, "Signals to investigate", "Optimization signals, not conclusions · bands are team assumptions");
  s.addTable([["Signal", "Count", "Note"].map(t => ({text: t, options: {bold: true}})),
    ["Allocated garments with no scan activity (sites at watch or high)", String(g.counts.fleet), "May reflect low wash frequency, delayed pickups or uncaptured RFID scans"],
    ["Repair vs retirement (site and garment pairs at watch or high)", String(g.counts.repair), "By site and garment type; repairs have no garment IDs"],
    ["Hygiene flag (traced garments, no scan over 180 days)", `${g.hygieneTotals.flagged} of ${g.hygieneTotals.traced}`, "Low confidence: the cycle log traces a small share of the fleet"]], {x: .5, y: 1.6, w: 12.3, fontSize: 12, border: {type: "solid", color: "E3E5E8", pt: 1}});
  const kin = KI.list(a.cid);
  if (kin.length) { s = p.addSlide(); title(s, "Information added by the KAM", "Customer-reported or KAM-reported; not verified against operational records");
    s.addTable([["Record", "Feeds"].map(t => ({text: t, options: {bold: true}})), ...kin.slice(0, 14).map(r => { const d = describeInput(r); return [plain(d.html), AREAS.find(x => x.id === d.area).name]; })], {x: .5, y: 1.6, w: 12.3, fontSize: 11, border: {type: "solid", color: "E3E5E8", pt: 1}}); }
};

/* ---------------------------------------------------------------- wiring into the portal */
TABS.splice(0, TABS.length, ["overview", "Overview"], ["evidence", "Evidence areas"], ["sites", "Sites"], ["service", "Service & hygiene"], ["circ", "Circularity & cost"],
  ["impact", "Environmental impact"], ["whatif", "What-if"], ["signals", "Signals"], ["inputs", "KAM inputs"], ["method", "Data & method"], ["actions", "Actions & export"]);
const _renderPanel = renderPanel;
renderPanel = function (a, p) {
  const t = S.spec.tab;
  if (t === "evidence") return panelEvidence(a, p);
  if (t === "whatif") return panelWhatIf(a, p);
  if (t === "signals") return panelSignals(a, p);
  if (t === "inputs") return panelInputs(a, p);
  _renderPanel(a, p);
  if (t === "method") { const cols = $("#panel .cols"); if (cols) cols.insertAdjacentHTML("beforeend", methodExtras()); }
};
const _render = render;
render = function (changed = []) { _render(changed); renderEvidence(); };
const _renderTests = Insights.renderTests;
Insights.renderTests = function () { _renderTests(); if (KAM.renderSelfTests) KAM.renderSelfTests(); };

KI.load(); overlayApply();
})();
