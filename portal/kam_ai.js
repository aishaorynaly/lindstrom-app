/* ======================================================================
   KAM extension, assistant: Gemini engine with tools, offline rules, and confirmation of new information.

   The model never writes a number. It calls tools that run on the portal's calculation layer (compute(),
   evidenceAreas(), signals()), and every figure in its answer is checked against those tool results.
   New information from the conversation is only ever proposed as a card; it enters the dataset when the KAM
   confirms. Without the server (or without a key) the page falls back to the original rule-based parser plus a
   small pattern parser for the most common records.
   ====================================================================== */
(function () {
"use strict";
const KAM = window.KAM, KI = KAM.KI, KIND = KAM.KIND, AREAS = KAM.AREAS;
const plain = h => String(h).replace(/<[^>]+>/g, "");
const AI = KAM.AI = {on: false, model: "gemini-3.8-flash", reason: "checking"};

/* ---------------------------------------------------------------- small UI helpers */
KAM.thinking = on => {
  let el = $("#iTyping");
  if (!el) { el = document.createElement("div"); el.id = "iTyping"; el.innerHTML = "<i></i><i></i><i></i> Thinking…"; const chips = $("#ichips"); chips.parentElement.insertBefore(el, chips); }
  el.classList.toggle("on", !!on); if (on) { const b = $("#imsgs"); b.scrollTop = b.scrollHeight; }
};
function md(text) { // tiny, safe markdown: **bold**, bullet and numbered lists, paragraphs
  let html = "", inList = false;
  String(text).split(/\r?\n/).forEach(raw => {
    const t = esc(raw).replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>"), li = t.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
    if (li) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${li[1]}</li>`; }
    else { if (inList) { html += "</ul>"; inList = false; } if (t.trim()) html += `<p>${t}</p>`; }
  });
  return html + (inList ? "</ul>" : "");
}
KAM.md = md;
KAM.sayHtml = R => `<div class="aihd">AI assistant</div>${md(R.say)}${R.untraced && R.untraced.length ? `<div class="small" style="color:var(--warn);margin-top:6px">⚠ Figures not found in the calculation layer: ${R.untraced.map(esc).join(", ")}. Check them before quoting.</div>` : ""}`;
KAM.queriesHtml = q => `<details><summary>Data queried (${q.length}) · from the calculation layer</summary>${q.map(x => `<div><code>${esc(x.tool)}(${esc(Object.entries(x.args || {}).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", "))})</code></div>`).join("")}</details>`;

/* ---------------------------------------------------------------- offline: patterns for the most common records */
const MONTHRE = "(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)";
function money(str, suffix) {
  let s = String(str).replace(/\s/g, "");
  s = /^\d{1,3}(,\d{3})+(\.\d+)?$/.test(s) ? s.replace(/,/g, "") : s.replace(",", ".");
  let v = parseFloat(s); if (!Number.isFinite(v)) return NaN;
  const m = String(suffix || "").toLowerCase(); return Math.round(v * (m === "k" || m === "thousand" ? 1e3 : m === "m" || m === "million" ? 1e6 : 1));
}
function parseInputsOffline(text, cid = S.cid) {
  const raw = String(text || "").trim(), t = norm(raw), out = [], C = D.customers[custIx(cid)];
  const site = (C.sites.map(si => D.sites[si]).find(s => new RegExp("\\b" + norm(s.name) + "\\b").test(t)) || {}).id || null;
  // the year is kept ("january 2026") so the validator can refuse months outside the data instead of misreading them
  const month = (() => { const m = t.match(new RegExp("\\b(?:in|during|for|on|of)\\s+" + MONTHRE + "(?:\\s+(20\\d\\d))?\\b")) || t.match(new RegExp("\\b" + MONTHRE + "(?:\\s+(20\\d\\d))?\\b")); return m ? m[1] + (m[2] ? " " + m[2] : "") : null; })();
  let m;
  if ((m = t.match(/\bcsat\b[^0-9]{0,40}?(\d+(?:[.,]\d+)?)(?:\s*(?:\/|out of|of)\s*(\d+))?/))) out.push({kind: "feedback", metric: "csat", value: parseFloat(m[1].replace(",", ".")), scale: m[2] ? +m[2] : 5, month, site});
  if ((m = t.match(/\bnps\b[^0-9+\-]{0,40}?([+-]?\s?\d{1,3})\b/))) out.push({kind: "feedback", metric: "nps", value: parseInt(m[1].replace(/\s/g, ""), 10), month, site});
  if (/\b(contract|annual|yearly|per year|a year|price|spend)\b/.test(t) && (m = t.match(/(?:€|\beur\b)\s?(\d[\d\s.,]*\d|\d)\s*(k|m|million|thousand)?/))) {
    const rec = {kind: "price", annual_eur: money(m[1], m[2])};
    const pv = t.match(/(?:last year|previous year|year before|before)[^€\d]{0,30}(?:€|\beur\b)?\s?(\d[\d\s.,]*\d|\d)\s*(k|m|million|thousand)?/);
    if (pv) rec.prev_annual_eur = money(pv[1], pv[2]);
    out.push(rec);
  }
  // risk reduction: "add €25,000 of reduced risk", "hygiene risk reduced by 25k euro", "risk reduction of 25 000 eur"
  if (/\brisk/.test(t) && /\b(reduc\w*|lower\w*|mitigat\w*|avoid\w*|sav\w+|prevent\w*|add\w*)\b/.test(t) && (m = t.match(/(?:€|\beur\w*\s)\s?(\d[\d\s.,]*\d|\d)\s*(k|m|million|thousand)?/) || t.match(/(\d[\d\s.,]*\d|\d)\s*(k|m|million|thousand)?\s*(?:€|\beuros?\b|\beur\b)/))) {
    const topic = (t.match(/\b(food safety|hygiene|safety|compliance|audit|quality|supply|delivery)\b/) || [])[1] || "hygiene";
    out.push({kind: "risk_reduction", value_eur: money(m[1], m[2]), topic: topic + " risk", site});
    return out;
  }
  if ((m = t.match(/\b(\d+)\s+late\s+(?:deliver\w*|trips?|pickups?)\b/))) out.push({kind: "late_delivery", count: +m[1], site, month});
  else if ((m = t.match(/\b(\d+)\s+(?:service\s+)?(requests?|complaints?|incidents?|tickets?)\b/))) out.push({kind: "incident", count: +m[1], type: m[2].replace(/s$/, ""), site, month});
  const head = (re) => { const x = raw.match(re); return x ? x[1].trim() : null; };
  const act = head(/^\s*(?:action|to-?do|follow[- ]?up)\s*[:\-]\s*(.+)$/i);
  if (act) {
    const owner = (act.match(/\bowner[:\s]+([^,.;]+?)(?=\s+(?:by|due)\b|[,.;]|$)/i) || [])[1] || null;
    const due = (act.match(/\b(?:by|due)\s+(\d{1,2}\s+[A-Za-z]+(?:\s+\d{4})?|\d{4}-\d{2}-\d{2})/i) || [])[1] || null;
    const title = act.replace(/[,;]?\s*owner[:\s]+[^,.;]+?(?=\s+(?:by|due)\b|[,.;]|$)/i, "").replace(/[,;]?\s*(?:by|due)\s+(?:\d{1,2}\s+[A-Za-z]+(?:\s+\d{4})?|\d{4}-\d{2}-\d{2})/i, "").trim().replace(/[,;.]$/, "");
    out.push({kind: "action", title, owner, due, site});
  }
  const issue = head(/^\s*(?:(?:recurring)\s+)?issue\s*[:\-]\s*(.+)$/i); if (issue) out.push({kind: "issue", title: issue, site, recurring: /recurring/i.test(raw)});
  const opp = head(/^\s*opportunity\s*[:\-]\s*(.+)$/i); if (opp) out.push({kind: "opportunity", title: opp});
  const pri = head(/^\s*(?:customer\s+)?priority\s*[:\-]\s*(.+)$/i); if (pri) out.push({kind: "priority", title: pri});
  return out;
}
KAM.parseInputsOffline = parseInputsOffline;
function offlineInterpret(text, c) {
  const inp = parseInputsOffline(text);
  if (inp.length) return {patch: {}, act: null, answer: null, rank: null, crm: null, flags: [], said: [], inputs: inp};
  const R = parse(text, c), t = norm(text);
  const tabs = [["whatif", /\bwhat.?if\b|sensitivit\w*/], ["signals", /\b(signals?|risk flags?|hygiene flag)\b/], ["evidence", /\b(evidence areas?|scorecard|account health)\b/], ["inputs", /\b(kam inputs?|added information|information added)\b/]];
  if (!R.patch.tab && /\b(open|show|go to|switch to|take me|see|view|let'?s look)\b/.test(t)) { const hit = tabs.find(([, re]) => re.test(t)); if (hit) R.patch.tab = hit[0]; }
  return R;
}
KAM.offlineInterpret = offlineInterpret;

/* ---------------------------------------------------------------- confirmation cards for new information */
const PROP = {seq: 0, items: {}};
KAM.PROP = PROP;
const areaName = id => AREAS.find(x => x.id === id).name;
function propHtml(id) {
  const p = PROP.items[id], list = p.summaries.map(s => `<li>${s.html} ${badge("KAM")} <span class="muted small">→ ${areaName(s.area)}</span></li>`).join("");
  const bad = p.bad.length ? `<div class="small" style="color:var(--warn)">${p.bad.map(b => "⚠ " + esc(b)).join("<br>")}</div>` : "", dup = p.dups ? `<div class="small muted">${p.dups} already recorded, skipped.</div>` : "";
  if (p.state === "none") return `<b>I couldn't add that</b>${bad}${dup}`;
  if (p.state === "pending") return `<b>Add to the dataset?</b> <span class="muted small">Nothing changes until you confirm.</span><ul>${list}</ul>${bad}${dup}<div class="btns"><button class="solid" data-kin="add:${id}">Add to dataset</button><button data-kin="no:${id}">Discard</button></div>`;
  if (p.state === "added") return `<b>✓ Added to the dataset</b><ul>${list}</ul><div class="small">Updated: ${[...new Set(p.summaries.map(s => areaName(s.area)))].join(", ")}. Labelled ${badge("KAM")}; remove it any time under KAM inputs.</div>${S.mode === "kam" ? `<div class="btns"><button data-kin="undo:${id}">Undo</button></div>` : ""}`;
  return `<b>${p.state === "undone" ? "Removed again" : "Discarded"}</b><ul>${list}</ul>`;
}
KAM.proposeInputs = (raw, text) => {
  const cid = S.cid, res = raw.map(r => KAM.validateInput(r, cid)), ok = res.filter(r => r.ok);
  const id = ++PROP.seq;
  PROP.items[id] = {id, cid, text, state: ok.length ? "pending" : "none", recs: ok.map(r => r.rec), summaries: ok.map(r => r.summary), bad: res.filter(r => !r.ok && !r.duplicate).flatMap(r => r.errors), dups: res.filter(r => r.duplicate).length, added: []};
  Insights.push("inputs", propHtml(id), {pid: id});
  return PROP.items[id];
};
function setProp(id, state) {
  const p = PROP.items[id]; p.state = state;
  const m = Insights.thread().find(x => x.pid === id); if (m) { m.kind = "inputs " + (state === "added" ? "added" : state === "pending" ? "" : "off"); m.html = propHtml(id); Insights.draw(); }
}
KAM.confirmProposal = id => {
  const p = PROP.items[id]; if (!p || p.state !== "pending") return false;
  if (p.cid !== S.cid) { Insights.push("sys", "Switch back to " + esc(D.customers[custIx(p.cid)].name) + " to add this."); return false; }
  p.added = p.recs.map(r => KI.add(p.cid, r, "chat", p.text));
  setProp(id, "added"); render(); KAM.flashAreas([...new Set(p.summaries.map(s => s.area))]); return true;
};
KAM.undoProposal = id => { const p = PROP.items[id]; if (!p || p.state !== "added") return false; p.added.forEach(r => KI.remove(p.cid, r.id)); setProp(id, "undone"); render(); return true; };
KAM.discardProposal = id => { const p = PROP.items[id]; if (!p || p.state !== "pending") return false; setProp(id, "discarded"); return true; };
document.addEventListener("click", e => {
  const b = e.target.closest("[data-kin]"); if (!b) return;
  const [act, id] = b.dataset.kin.split(":"); ({add: KAM.confirmProposal, no: KAM.discardProposal, undo: KAM.undoProposal}[act] || (() => {}))(+id);
});

/* ---------------------------------------------------------------- tools the model may call (all run on the calculation layer) */
const BL = {local: "B_LOCAL", outsourced: "B_LOCAL", "outsourced local laundry": "B_LOCAL", home: "B_HOME", "home washing": "B_HOME", inhouse: "B_INHOUSE", "in-house": "B_INHOUSE", "in-house customer laundry": "B_INHOUSE"};
const RANK_COLS = {utilisation: "util", unused: "unused", on_time: "on_time", requests: "req_per_1000", hygiene: "hyg", water_saving: "water_saved", energy_saving: "energy_saved", co2_saving: "co2_saved", repairs: "repairs", washed: "wkg", water_used: "water_l_per_kg"};
const siteList = () => CUR().sites.map(si => D.sites[si]);
function resolveSites(v) {
  const t = norm(String(v)); if (/^(all|everything|whole account|all sites)$/.test(t)) return {sel: "all"};
  const C = CUR(), country = [...new Set(C.sites.map(si => D.sites[si].country))].find(c => norm(c) === t);
  if (country) return {sel: "country:" + country};
  const s = siteList().find(x => norm(x.name) === t || norm(x.id) === t || new RegExp("\\b" + norm(x.name) + "\\b").test(t));
  return s ? {sel: "site:" + s.id, id: s.id} : {err: `Unknown site "${v}". Sites: ${siteList().map(x => x.name).join(", ")}.`};
}
function scopeFrom(args) {
  const sp = S.spec, ign = []; let f = sp.from, t = sp.to, sel = sp.sites, bl = sp.baseline;
  if (args.months_from != null || args.months_to != null) {
    const a1 = args.months_from ?? args.months_to, b1 = args.months_to ?? args.months_from;
    if ([a1, b1].every(v => Number.isInteger(v) && v >= 1 && v <= 12)) { f = Math.min(a1, b1) - 1; t = Math.max(a1, b1) - 1; } else ign.push("months must be whole numbers from 1 to 12 (the data covers January to December 2025).");
  }
  if (args.sites != null && args.sites !== "") { const r = resolveSites(args.sites); if (r.sel) sel = r.sel; else ign.push(r.err); }
  if (args.baseline) { const b = BL[norm(args.baseline)]; if (b) bl = b; else ign.push(`Unknown baseline "${args.baseline}" (use local, home or inhouse).`); }
  return {f, t, sel, bl, ign};
}
const bad = ign => ({error: ign.join(" ")});
const rowsText = a => a.siteRows.length;
const TOOLS = {
  set_view(args, R) {
    const sp = S.spec, P = {}, ign = [];
    if (args.reset) Object.assign(P, defaultSpec(S.cid));
    if (args.months_from != null || args.months_to != null) { const s = scopeFrom(args); if (s.ign.length) ign.push(...s.ign); else { P.from = s.f; P.to = s.t; } }
    if (args.sites != null && args.sites !== "") { const r = resolveSites(args.sites); if (r.sel) { P.sites = r.sel; P.selSite = r.id || null; } else ign.push(r.err); }
    if (args.compare != null) P.compare = !!args.compare;
    if (args.baseline) { const b = BL[norm(args.baseline)]; if (b) P.baseline = b; else ign.push(`Unknown baseline "${args.baseline}".`); }
    if (args.audience) { if (AUD[args.audience] && args.audience !== "room") Object.assign(P, audiencePatch(CUR(), args.audience)); else ign.push(`Unknown audience "${args.audience}".`); }
    if (Array.isArray(args.metrics)) { const ms = args.metrics.filter(m => M[m]); if (ms.length) P.metrics = [...new Set(ms)].slice(0, 6); if (ms.length < args.metrics.length) ign.push("Some metric names were not recognised."); }
    if (args.highlight) { if (M[args.highlight]) { P.highlight = args.highlight; if (!P.metrics) P.metrics = [args.highlight, ...sp.metrics.filter(m => m !== args.highlight)].slice(0, 6); } else ign.push(`Unknown metric "${args.highlight}".`); }
    if (args.tab) { if (TABS.some(t => t[0] === args.tab)) P.tab = args.tab; else ign.push(`Unknown tab "${args.tab}".`); }
    if (args.short != null) P.short = !!args.short;
    Object.assign(R.patch, P);
    const eff = {...clone(sp), ...R.patch};
    return {applied: Object.keys(P).length ? {period: periodLabel(eff.from, eff.to), sites: sitesLabel(CUR(), eff.sites), baseline: D.baselines[eff.baseline].name, compare: !!eff.compare, tab: eff.tab} : "nothing changed", ignored: ign};
  },
  get_metric(args) {
    const x = M[args.metric]; if (!x) return {error: `Unknown metric "${args.metric}".`, valid: Object.keys(M)};
    const sc = scopeFrom(args); if (sc.ign.length) return bad(sc.ign);
    const a = compute(S.cid, sc.f, sc.t, sc.sel, sc.bl), res = {metric: args.metric, label: x.lab, period: a.label, sites: sitesLabel(CUR(), sc.sel), baseline: bname(a), value: x.ring ? fmt(x.ring(a), 1) + "%" : x.big(a), raw: x.v(a), detail: x.sub(a), provenance: STATUS[x.s].t};
    if (x.range) res.range_low_high = x.range(a);
    if (args.compare_previous) { const pv = prevPeriod(sc.f, sc.t); if (pv) { const b = compute(S.cid, pv.f, pv.t, sc.sel, sc.bl); res.previous = {period: b.label, value: x.ring ? fmt(x.ring(b), 1) + "%" : x.big(b), raw: x.v(b)}; } else res.previous = "no earlier period in the data (it starts in January 2025)"; }
    const kam = KAM.touched(a).length; if (kam) res.includes_kam_input_records = kam;
    if (a.nM === 1) res.caution = "single month: noisier than quarterly or annual figures";
    return res;
  },
  rank_sites(args) {
    const col = RANK_COLS[args.metric]; if (!col) return {error: `Unknown metric "${args.metric}".`, valid: Object.keys(RANK_COLS)};
    const sc = scopeFrom(args); if (sc.ign.length) return bad(sc.ign);
    const a = compute(S.cid, sc.f, sc.t, sc.sel, sc.bl), dir = args.order === "lowest" ? 1 : -1, n = Math.max(1, Math.min(10, Math.round(args.top_n || 3)));
    const rows = a.siteRows.filter(r => r[col] != null).sort((x, y) => (x[col] - y[col]) * dir).slice(0, n);
    return {metric: args.metric, order: args.order || "highest", period: a.label, provenance: STATUS[M[args.metric] ? M[args.metric].s : "CALCULATED"].t, sites: rows.map(r => ({site: r.name, country: r.country, value: Math.round(r[col] * 100) / 100}))};
  },
  get_evidence_areas(args) {
    const sc = scopeFrom(args); if (sc.ign.length) return bad(sc.ign);
    const a = compute(S.cid, sc.f, sc.t, sc.sel, sc.bl), pv = prevPeriod(sc.f, sc.t), p = pv ? compute(S.cid, pv.f, pv.t, sc.sel, sc.bl) : null;
    return {period: a.label, sites: sitesLabel(CUR(), sc.sel), areas: KAM.evidenceAreas(a, p).map(ar => ({id: ar.id, name: ar.name, status: ar.status === "na" ? "no data" : ar.status, headline: ar.head, includes_kam_input: ar.kam, evidence: ar.lines.map(l => ({label: l[0], value: l[1], provenance: STATUS[l[2]].t})), missing: ar.need ? ar.need.text : undefined}))};
  },
  get_signals(args) {
    const sc = scopeFrom(args); if (sc.ign.length) return bad(sc.ign);
    const a = compute(S.cid, sc.f, sc.t, sc.sel, sc.bl), g = KAM.signals(a), TH = KAM.TH;
    return {period: a.label, active_risk_signals: g.counts.active, framing: "Allocated garments with no scan activity is an optimization signal for investigation; it may reflect low wash frequency, delayed pickups or uncaptured RFID scans.",
      allocated_garments_with_no_scan_activity: {thresholds_pct_watch_high: [TH.gap.watch, TH.gap.high], provenance: STATUS.CALCULATED.t, sites_at_or_above_watch: g.fleet.filter(r => r.level !== "ok").slice(0, 10).map(r => ({site: r.name, share_pct: Math.round(r.share * 10) / 10, signal: r.level}))},
      repair_vs_retirement: {provenance: STATUS.CALCULATED.t, note: "by site and garment type, not by individual garment", pairs_at_or_above_watch: g.repair.filter(r => r.level !== "ok").slice(0, 8).map(r => ({site: r.site, garment: r.product, ratio_vs_median: Math.round(r.ratio * 100) / 100, signal: r.level}))},
      hygiene_flag: {confidence: "low", provenance: STATUS.CALCULATED.t + " " + STATUS.TEAM.t, flagged_of_traced: `${g.hygieneTotals.flagged} of ${g.hygieneTotals.traced}`, caveat: "the cycle log traces only a small share of the fleet and every flag comes from the log ending; confirm with full RFID records", sites_at_or_above_watch: g.hygiene.filter(r => r.level !== "ok").slice(0, 6).map(r => ({site: r.name, share_pct: Math.round(r.share), signal: r.level}))}};
  },
  get_crm(args) {
    const cr = CUR().crm, k = args.kind;
    if (k === "contacts") return {contacts: cr.contacts.map(([n, r]) => ({name: n, role: r})), source: "mock CRM record (synthetic)"};
    if (k === "renewal") return {renewal: cr.renewal, days_from_demo_date: daysBetween(DEMO_TODAY, cr.renewal), source: "mock CRM record (synthetic)"};
    if (k === "meeting") return {next_meeting: cr.next_meeting, last_meeting: cr.last_meeting, source: "mock CRM record (synthetic)"};
    return {notes: cr.notes, open_topics: cr.open_topics, source: "mock CRM record (synthetic)"};
  },
  record_inputs(args, R) {
    const items = Array.isArray(args.inputs) ? args.inputs : [], accepted = [], rejected = [];
    items.forEach(raw => { const v = KAM.validateInput(raw, S.cid); if (v.ok) { accepted.push({summary: plain(v.summary.html), feeds: AREAS.find(x => x.id === v.summary.area).name}); R.inputs.push(raw); } else rejected.push({reason: v.errors.join(" ")}); });
    return {status: accepted.length ? "proposed to the KAM for confirmation; not added yet" : "nothing proposed", accepted, rejected};
  }
};
KAM.TOOLS = TOOLS;

function declarations() {
  const mKeys = Object.keys(M), tabIds = TABS.map(t => t[0]), kinds = Object.keys(KIND);
  const when = {months_from: {type: "integer", minimum: 1, maximum: 12, description: "First calendar month of 2025 (1 = January). Omit to keep the current period."}, months_to: {type: "integer", minimum: 1, maximum: 12, description: "Last calendar month of 2025. Omit for a single month."}};
  const where = {sites: {type: "string", description: "all, a country name, or one delivery site name such as Oulu. Omit to keep the current selection."}, baseline: {type: "string", enum: ["local", "home", "inhouse"], description: "Comparison baseline. Omit to keep the current one."}};
  return [{function_declarations: [
    {name: "set_view", description: "Change what the dashboard shows. Use when the user asks to show, switch, focus, compare, reset or open something. Include only the fields the user wants changed.",
      parameters_json_schema: {type: "object", properties: {...when, ...where, compare: {type: "boolean", description: "Compare with the previous period."}, audience: {type: "string", enum: ["default", "sponsor", "sustainability", "site", "procurement", "quality"]}, metrics: {type: "array", items: {type: "string", enum: mKeys}, description: "Up to six metric tiles, in order."}, highlight: {type: "string", enum: mKeys}, tab: {type: "string", enum: tabIds}, short: {type: "boolean"}, reset: {type: "boolean", description: "Return to the default view first."}}}},
    {name: "get_metric", description: "Get one figure from the calculation layer for any period and site selection. Use for every question about a number.",
      parameters_json_schema: {type: "object", properties: {metric: {type: "string", enum: mKeys}, ...when, ...where, compare_previous: {type: "boolean"}}, required: ["metric"]}},
    {name: "rank_sites", description: "Rank delivery sites on one measure.",
      parameters_json_schema: {type: "object", properties: {metric: {type: "string", enum: Object.keys(RANK_COLS)}, order: {type: "string", enum: ["highest", "lowest"]}, top_n: {type: "integer", minimum: 1, maximum: 10}, ...when, baseline: where.baseline}, required: ["metric"]}},
    {name: "get_evidence_areas", description: "Get the nine evidence areas (scorecard, experience, service performance, adoption, operational, financial, sustainability, improvement, relationship) with status, headline and evidence lines.",
      parameters_json_schema: {type: "object", properties: {...when, ...where}}},
    {name: "get_signals", description: "Get operational risk and optimization signals: allocated garments with no scan activity by site, repair versus retirement, and the hygiene scan flag.",
      parameters_json_schema: {type: "object", properties: {...when, ...where}}},
    {name: "get_crm", description: "Read the mock CRM record: contacts, renewal date, meetings, notes and open topics.",
      parameters_json_schema: {type: "object", properties: {kind: {type: "string", enum: ["contacts", "renewal", "meeting", "notes"]}}, required: ["kind"]}},
    {name: "record_inputs", description: "Propose new information the KAM has just told you from the customer conversation, to be added to the dataset after the KAM confirms. Record only what was actually stated.",
      parameters_json_schema: {type: "object", properties: {inputs: {type: "array", items: {type: "object", properties: {
        kind: {type: "string", enum: kinds, description: "feedback (CSAT or NPS), incident (service requests or complaints), late_delivery, volume (wearers or allocated garments changed), price (contract value), risk_reduction (EUR value of risk the KAM says was reduced, e.g. hygiene risk; put the amount in value_eur and the topic in title), action, issue, opportunity, priority"},
        metric: {type: "string", enum: ["csat", "nps"]}, value: {type: "number"}, scale: {type: "number", description: "CSAT scale: 5, 7 or 10"},
        site: {type: "string", description: "delivery site name"}, month: {type: "string", description: "month of 2025 as YYYY-MM or a name such as November"},
        count: {type: "integer"}, type: {type: "string"}, field: {type: "string", enum: ["wearers", "alloc"]},
        annual_eur: {type: "number"}, prev_annual_eur: {type: "number"}, title: {type: "string"}, owner: {type: "string"}, due: {type: "string", description: "YYYY-MM-DD"},
        status: {type: "string", enum: ["open", "agreed", "done"]}, recurring: {type: "boolean"}, value_eur: {type: "number"}, note: {type: "string"}}, required: ["kind"]}}}, required: ["inputs"]}}
  ]}];
}

/* ---------------------------------------------------------------- grounding: every figure in an answer must exist in the data it was given */
function numTokens(s) {
  const out = new Set();
  (String(s).match(/\d+(?:[.,]\d+)*/g) || []).forEach(tok => {
    let t = tok.replace(/,(?=\d{3}\b)/g, ""); t = t.replace(",", ".");
    const v = parseFloat(t); if (!Number.isFinite(v)) return;
    [t, String(Math.round(v)), v.toFixed(1), v.toFixed(2)].forEach(x => out.add(x.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "")));
    out.add(String(v));
  });
  return out;
}
function untracedFigures(answer, haystack) {
  const allowed = numTokens(haystack), seen = new Set(), miss = [];
  (String(answer).match(/\d+(?:[ ,.]\d+)*/g) || []).forEach(raw => {
    const tok = raw.replace(/\s(?=\d{3}\b)/g, ""), v = parseFloat(tok.replace(/,(?=\d{3}\b)/g, "").replace(",", "."));
    if (!Number.isFinite(v) || (Number.isInteger(v) && v < 20) || v === 2025 || v === 2026) return;
    const forms = [...numTokens(tok)]; if (!forms.some(f => allowed.has(f)) && !seen.has(tok)) { seen.add(tok); miss.push(tok); }
  });
  return miss;
}
KAM.untracedFigures = untracedFigures;

/* ---------------------------------------------------------------- Gemini engine */
function systemPrompt() {
  const C = CUR(), sp = S.spec;
  const ctx = {customer: {name: C.name, industry: C.industry, hq: C.hq, stated_priorities: C.stated, sites: siteList().map(s => ({id: s.id, name: s.name, country: s.country})), contacts: C.crm.contacts.map(c => c[0] + " (" + c[1] + ")")},
    data: {covers: `January to December ${D.year}, monthly`, demo_date: DEMO_TODAY}, current_view: {period: periodLabel(sp.from, sp.to), sites: sitesLabel(C, sp.sites), baseline: D.baselines[sp.baseline].name, compare: !!sp.compare, tab: sp.tab, mode: S.mode},
    metrics: Object.entries(M).map(([k, x]) => `${k} = ${x.lab}`), evidence_areas: AREAS.map(x => x.name)};
  return `You are the AI assistant inside Lindström's KAM portal. A Key Account Manager (KAM) uses you to prepare for and run customer meetings about customer value and sustainability. The data is synthetic.

How you work
- You never calculate or remember figures. Every figure you state must come from a tool result in this conversation. Copy figures exactly and put their provenance tag after them: [Measured], [Calculated], [Estimated] or [TEAM_ASSUMPTION] (tool results show the tag text).
- Use set_view when the user asks to show, switch, focus, compare, reset or open something. Use get_metric, rank_sites, get_evidence_areas, get_signals or get_crm to answer questions. Call set_view and the data tools together when the user wants to see something and also asks about it.
- Use record_inputs when the user tells you NEW information from the customer conversation (survey scores, service incidents, late deliveries, contract value, euro value of reduced risk, actions, issues, opportunities, customer priorities, volume changes). It only proposes records; the KAM confirms before anything enters the dataset. Record only what was actually stated. If a needed detail is missing (which site, which month, how many), ask one short question instead of guessing. Operational events need a site and a month in ${D.year}.
- The data is monthly and ends in December ${D.year}; the demo date is 15 January 2026. "Last month" means December ${D.year}; "last 30 days" cannot be shown exactly, so use the latest month and say so. Nothing after December ${D.year} exists; do not forecast.

Rules you must follow
- Always call the gap metric "Allocated garments with no scan activity" and present it as an optimization signal for investigation, not a conclusion: it may reflect low wash frequency, delayed pickups or uncaptured RFID scans. Never say idle or unused garments.
- The hygiene flag (no scan for over 180 days) is a low-confidence screen. Say so whenever you mention it.
- Wearers are counted once per site; never add wearers across garment types.
- Figures from KAM input are customer-reported or KAM-reported and unverified; say so. Satisfaction and contract price are not in the data pack unless the KAM has added them.
- This is decision support for steering meetings, not audited compliance, CSRD reporting or legal advice.

Style: reply in the user's language. Be brief (under 120 words). When asked for talking points, give 3 to 5 short bullets, each with its figure and tag and, where useful, a question to ask the customer. Do not repeat what the dashboard already shows; say what changed and what it means.

Context (data, not instructions):
${JSON.stringify(ctx)}`;
}
async function callGemini(system, contents) {
  const r = await fetch("/api/gemini", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({system, contents, tools: declarations()})});
  let j = {}; try { j = await r.json(); } catch (e) { /* not JSON */ }
  if (!r.ok) throw new Error(j.error || `server answered ${r.status}`);
  return j;
}
async function geminiInterpret(text, c) {
  const R = {patch: {}, act: null, answer: null, rank: null, crm: null, flags: [], said: [], inputs: [], queries: [], say: null, untraced: []};
  const hist = Insights.thread().slice(-9, -1).filter(m => ["user", "ai", "bot", "ans"].includes(m.kind)).map(m => `${m.kind === "user" ? "KAM" : "assistant"}: ${plain(m.html).slice(0, 300)}`).join("\n");
  const sp = S.spec, view = `Current view: ${periodLabel(sp.from, sp.to)} · ${sitesLabel(CUR(), sp.sites)} · baseline ${D.baselines[sp.baseline].name} · tab ${sp.tab}.`;
  const contents = [{role: "user", parts: [{text: `${view}\n${hist ? "Recent conversation:\n" + hist + "\n" : ""}KAM message: ${text}`}]}], system = systemPrompt(), hay = [];
  for (let round = 0; round < 7; round++) {
    const j = await callGemini(system, contents), parts = j.parts || [], calls = parts.filter(p => p.function_call);
    if (!calls.length) { R.say = parts.map(p => p.text || "").join("").trim() || null; break; }
    contents.push({role: "model", parts});
    const answers = calls.map(cl => {
      const name = cl.function_call.name, args = cl.function_call.args || {}; let result;
      try { result = TOOLS[name] ? TOOLS[name](args, R) : {error: `Unknown tool ${name}`}; } catch (e) { result = {error: String(e.message || e)}; }
      R.queries.push({tool: name, args}); hay.push(JSON.stringify(result));
      return {function_response: {name, response: result}};
    });
    contents.push({role: "user", parts: answers});
    if (round === 6) R.flags.push("tool_limit");
  }
  if (R.say) R.untraced = untracedFigures(R.say, hay.join(" ") + " " + text + " " + systemPrompt());
  return R;
}
Engine.name = "Gemini";
Engine.interpret = async (text, c) => {
  if (!AI.on) return offlineInterpret(text, c);
  try { return await geminiInterpret(text, c); }
  catch (e) {
    Insights.push("sys", esc(`AI unavailable (${e.message || e}). Using the offline rules for this message.`));
    return offlineInterpret(text, c);
  }
};
function engineLabel() {
  const el = $("#insEngine"); if (!el) return;
  el.innerHTML = AI.on ? `<span class="aistate on"></span>${esc(AI.model)} · tools run on the calculation layer` : `<span class="aistate"></span>Offline rules · ${esc(AI.reason)}`;
}
async function probe() {
  if (location.protocol === "file:") { AI.on = false; AI.reason = "run server.py for AI"; engineLabel(); return; }
  try { const r = await fetch("/api/health", {cache: "no-store"}); const j = await r.json(); AI.on = !!j.ai; AI.model = j.model || AI.model; AI.reason = j.ai ? "" : "set GEMINI_API_KEY for AI"; }
  catch (e) { AI.on = false; AI.reason = "run server.py for AI"; }
  engineLabel();
}
KAM.probe = probe;
const _onAccount = Insights.onAccount;
Insights.onAccount = function () { _onAccount(); engineLabel(); };

/* ---------------------------------------------------------------- self-tests (pure) and the interface scan (DOM) */
function ok(cond, msg) { return cond ? null : String(msg || "check failed"); }
KAM.runSelfTests = function () {
  const cid = "CUST001", ci = custIx(cid), si = siteById("S004"), T = [];
  const test = (name, fn) => { let err = null; try { err = fn(); } catch (e) { err = "threw " + e.message; } T.push({name, ok: err == null, err}); };
  const withTemp = (rec, fn) => { const list = KI.all[cid]; const r = {...rec, id: "tmp", ts: new Date().toISOString(), active: true}; list.push(r); KAM.overlayApply(); try { return fn(); } finally { list.splice(list.indexOf(r), 1); KAM.overlayApply(); } };
  const base = compute(cid, 10, 10, "site:S004", "B_LOCAL"), reqBase = base.flows.req, trips = base.cycles, late = base.late;
  test("A late delivery adds exactly its count to trips and late trips", () => withTemp({kind: "late_delivery", site: "S004", month: 10, count: 3}, () => { const a = compute(cid, 10, 10, "site:S004", "B_LOCAL"); return ok(a.cycles === trips + 3 && a.late === late + 3, `trips ${a.cycles} vs ${trips + 3}, late ${a.late} vs ${late + 3}`); }));
  test("An incident adds exactly its count to service requests", () => withTemp({kind: "incident", site: "S004", month: 10, count: 4}, () => ok(compute(cid, 10, 10, "site:S004", "B_LOCAL").flows.req === reqBase + 4, "requests did not rise by 4")));
  test("A volume change replaces the monthly figure", () => withTemp({kind: "volume", site: "S004", month: 10, field: "wearers", value: 777}, () => ok(compute(cid, 10, 10, "site:S004", "B_LOCAL").wearers === 777, "wearers not 777")));
  test("Removing a record restores the original figures", () => { withTemp({kind: "incident", site: "S004", month: 10, count: 9}, () => 0); const a = compute(cid, 10, 10, "site:S004", "B_LOCAL"); return ok(a.flows.req === reqBase && a.cycles === trips, "figures not restored"); });
  test("Other sites and months are untouched by an overlay", () => { const o = compute(cid, 0, 11).flows.req; return withTemp({kind: "incident", site: "S004", month: 10, count: 5}, () => ok(compute(cid, 0, 11).flows.req === o + 5 && compute(cid, 9, 9, "site:S004").flows.req === compute(cid, 9, 9, "site:S004").flows.req, "year total did not rise by exactly 5")); });
  const V = r => KAM.validateInput(r, cid);
  test("Validation accepts a complete record", () => ok(V({kind: "incident", site: "Oulu", month: "November", count: 2}).ok, "should be valid"));
  test("Validation rejects a month outside the data", () => ok(!V({kind: "incident", site: "Oulu", month: "2026-01", count: 2}).ok, "2026 accepted"));
  test("Validation rejects an unknown site", () => ok(!V({kind: "incident", site: "Atlantis", month: 3, count: 2}).ok, "unknown site accepted"));
  test("Validation rejects a missing site", () => ok(!V({kind: "late_delivery", month: 3, count: 2}).ok, "missing site accepted"));
  test("Validation rejects out-of-range scores and counts", () => ok(!V({kind: "feedback", metric: "csat", value: 6, scale: 5}).ok && !V({kind: "feedback", metric: "nps", value: 140}).ok && !V({kind: "late_delivery", site: "Oulu", month: 3, count: 0}).ok, "bad value accepted"));
  test("Validation rejects unknown record types", () => ok(!V({kind: "magic"}).ok, "unknown kind accepted"));
  test("Validation strips markup from text", () => { const v = V({kind: "action", title: "<img src=x onerror=alert(1)>Review jackets"}); return ok(v.ok && !/[<>]/.test(v.rec.title), "markup kept"); });
  const P = t => KAM.parseInputsOffline(t, cid);
  test("Offline: CSAT with scale and month", () => { const r = P("CSAT from the December survey is 4.3 out of 5")[0]; return ok(r && r.kind === "feedback" && r.metric === "csat" && r.value === 4.3 && r.scale === 5 && /dec/i.test(r.month), JSON.stringify(r)); });
  test("Offline: NPS with sign", () => { const r = P("NPS is +32")[0]; return ok(r && r.metric === "nps" && r.value === 32, JSON.stringify(r)); });
  test("Offline: contract value with k suffix and previous year", () => { const r = P("Contract value is €420k a year, last year it was €405k")[0]; return ok(r && r.kind === "price" && r.annual_eur === 420000 && r.prev_annual_eur === 405000, JSON.stringify(r)); });
  test("Offline: reduced risk in euros becomes a risk_reduction record", () => { const r = P("this food customer has a hygiene risk, add €25,000 of reduced risk")[0], q = P("Hygiene risk reduced by 25k euro")[0]; return ok(r && r.kind === "risk_reduction" && r.value_eur === 25000 && /hygiene/.test(r.topic) && q && q.value_eur === 25000, JSON.stringify([r, q])); });
  test("Risk reduction shows in Financial Value and Signals", () => withTemp({kind: "risk_reduction", value_eur: 25000, topic: "hygiene risk"}, () => { const a = compute(cid, 0, 11), f = KAM.evidenceAreas(a, null).find(x => x.id === "financial"); return ok(f.kam && f.lines.some(l => /Risk reduced/.test(l[0]) && /25,000/.test(l[1])) && KAM.signals(a).riskReduced.eur === 25000, "risk not shown"); }));
  test("Offline: late deliveries at a site in a month", () => { const r = P("Oulu had 3 late deliveries in November")[0]; return ok(r && r.kind === "late_delivery" && r.count === 3 && r.site === "S004" && /nov/i.test(r.month), JSON.stringify(r)); });
  test("Offline: action with owner and due date", () => { const r = P("Action: review jacket allocation at Helsinki, owner Anna, by 28 Feb")[0]; return ok(r && r.kind === "action" && r.owner === "Anna" && r.due === "28 Feb" && /jacket/.test(r.title) && !/owner|28 Feb/i.test(r.title), JSON.stringify(r)); });
  test("Offline: questions are not records", () => ok(P("How much water did we save last month?").length === 0 && P("What is the NPS?").length === 0, "question parsed as a record"));
  test("Offline: a view request keeps working", () => { const R = KAM.offlineInterpret("How much kWh did we save last month?", {cust: CUR(), spec: defaultSpec(cid), obs: [], views: [], mode: "kam"}); return ok(R.patch.from === 11 && !R.inputs, JSON.stringify(R.patch)); });
  test("Grounding: invented figures are caught, quoted ones are not", () => ok(untracedFigures("Saved 1.41 M L, 5,230 garments and 987,654 L.", "water 1.41 M L raw 1406482.75 and gap 5230").join() === "987,654", untracedFigures("Saved 1.41 M L, 5,230 garments and 987,654 L.", "water 1.41 M L raw 1406482.75 and gap 5230").join()));
  test("Water saved by month adds up to the year", () => { const y = compute(cid, 0, 11).save.water.base, s = D.months.reduce((t, _, i) => t + compute(cid, i, i).save.water.base, 0); return ok(Math.abs(y - s) < 1e-6 * y, `${y} vs ${s}`); });
  test("Wearers are counted once per site, never summed across garments", () => ok(compute(cid, 11, 11).wearers === 9607, "wearers " + compute(cid, 11, 11).wearers));
  test("Nine evidence areas, scorecard = worst of the seven", () => { const a = compute(cid, 0, 11), ar = KAM.evidenceAreas(a, null), by = Object.fromEntries(ar.map(x => [x.id, x])); const have = ["experience", "service", "adoption", "operational", "financial", "sustainability", "improvement"].map(i => by[i].status).filter(s => s !== "na"); const w = have.includes("r") ? "r" : have.includes("a") ? "a" : have.length ? "g" : "na"; return ok(ar.length === 9 && by.scorecard.status === w, `areas ${ar.length}, scorecard ${by.scorecard.status} vs ${w}`); });
  test("Every evidence line carries a known provenance tag", () => { const bad = KAM.evidenceAreas(compute(cid, 0, 11), null).flatMap(a => a.lines).filter(l => !STATUS[l[2]]); return ok(!bad.length, "unlabelled lines: " + bad.length); });
  test("Feedback drives Customer Experience and the scorecard", () => withTemp({kind: "feedback", metric: "csat", value: 3.2, scale: 5, month: null, site: null}, () => { const ar = KAM.evidenceAreas(compute(cid, 0, 11), null); return ok(ar.find(x => x.id === "experience").status === "r" && ar.find(x => x.id === "scorecard").status === "r", "CSAT 3.2/5 should be red and pull the scorecard"); }));
  test("Contract value gives the financial ratio", () => withTemp({kind: "price", annual_eur: 2000000}, () => { const f = KAM.evidenceAreas(compute(cid, 0, 11), null).find(x => x.id === "financial"); return ok(f.lines.some(l => /vs contract spend/.test(l[0])) && f.status !== "na", "no ratio line"); }));
  test("Signals: thresholds applied and counts consistent", () => { const g = KAM.signals(compute(cid, 0, 11)); return ok(g.counts.active === g.counts.fleet + g.counts.repair && g.hygieneTotals.traced === 698, `traced ${g.hygieneTotals.traced}`); });
  return T;
};
KAM.renderSelfTests = function () {
  const box = $("#testBox"); if (!box) return;
  let k = $("#kamTestBox"); if (!k) { k = document.createElement("div"); k.id = "kamTestBox"; k.className = "kamonly"; k.style.marginTop = "12px"; box.parentElement.appendChild(k); }
  const res = KAM.runSelfTests(), n = res.filter(r => r.ok).length;
  k.innerHTML = `<h3 style="margin-top:0">KAM extension checks: <span class="${n === res.length ? "pass" : "fail"}">${n}/${res.length} pass</span></h3>
    <div class="small muted" style="margin-bottom:6px">Overlays, validation, offline parsing, grounding, evidence areas and signals. Run on NorthStar's data; temporary records are removed again. KAM-only.</div>
    <div class="tscroll" style="max-height:220px"><table><thead><tr><th class="l">Check</th><th class="l">Result</th></tr></thead><tbody>${res.map(r => `<tr><td class="l" style="white-space:normal">${esc(r.name)}</td><td class="l" style="white-space:normal">${r.ok ? '<span class="pass">pass</span>' : `<span class="fail">fail</span> <span class="small">${esc(r.err)}</span>`}</td></tr>`).join("")}</tbody></table></div>`;
};
KAM.scanInterface = function () { // opens every tab in both modes and checks wording and provenance labels on screen
  const results = [], keep = {tab: S.spec.tab, mode: S.mode}, banned = /\b(unused|idle)\b/i;
  ["kam", "client"].forEach(mode => { S.mode = mode; document.body.classList.toggle("client", mode === "client");
    TABS.forEach(([id]) => { S.spec.tab = id; render();
      const text = $("#dash .main").innerText, panel = $("#panel");
      results.push({tab: id, mode, banned: (text.match(banned) || [])[0] || null, tags: panel.querySelectorAll(".status").length + $$("#evidence .status").length, evidence: $$("#evidence .ev").length}); }); });
  S.mode = keep.mode; document.body.classList.toggle("client", keep.mode === "client"); S.spec.tab = keep.tab; render();
  return results;
};

probe();
})();
