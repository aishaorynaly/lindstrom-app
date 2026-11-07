"""Focused views: one question, one isolated canvas.

Each view is built from the engine's figures alone. It holds a simple visual, a few risk and impact indicators,
and rule-based insights, so it works without Gemini. The assistant may replace the insights, never a figure.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import pandas as pd

import charts
import config
import engine
import narrative
from config import CALCULATED, ESTIMATED, TEAM_ASSUMPTION
from engine import Metric, ReviewContext, format_value
from loader import Dataset

STABLE_BAND_PCT = 2.0  # [TEAM_ASSUMPTION] a smaller move between two three-month windows is noise
NEAR_THRESHOLD_DAYS = 30
PRICE_LIMITS = (-50.0, 100.0)
OK, WATCH, HIGH, INFO = "ok", "watch", "high", "info"
OUTLOOK_NOTE = "Last 3 months against the 3 before. A direction to discuss, not a forecast."


@dataclass
class Indicator:
    label: str
    value: str
    tags: tuple[str, ...] = ()
    level: str = INFO
    note: str = ""


@dataclass
class FocusView:
    kind: str
    title: str
    lead: str
    indicators: list[Indicator] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)
    donut: object = None
    donut_title: str = ""
    chart: object = None
    chart_title: str = ""
    caution: str = ""
    details: list = field(default_factory=list)  # further figures, behind "Click to view details"
    table: pd.DataFrame | None = None
    table_title: str = ""
    method: list[str] = field(default_factory=list)  # thresholds and assumptions behind this view

    @property
    def facts(self) -> str:
        """This view's figures for the assistant, and for checking what it writes."""
        return json.dumps({
            "focused_view": self.title, "summary": self.lead, "caution": self.caution,
            "indicators": [{"label": i.label, "value": i.value, "provenance": list(i.tags), "note": i.note}
                           for i in self.indicators],
            "rule_based_insights": self.insights,
        }, ensure_ascii=False, indent=1)


# --- Reading the question ---------------------------------------------------------
_TOPIC_WORDS = (
    ("export", r"\b(discussion points?|talking points?|steering|export|powerpoint|pptx|slides?|deck)\b|презентац"),
    ("pricing", r"\bpric\w*|\btariff|\bfees?\b|contract value|\bcosts?\b.*\d\s*%|\d\s*%.*\bcosts?\b|\bцен\w*|тариф"),
    ("hygiene", r"\bhygien\w*|food.safety|haccp|contaminat\w*|гигиен|пищев\w* безопас"),
    ("whatif", r"\bwhat.?if\b|\bcompare\b|\bbaselines?\b|sensitiv\w*|\bhome wash\w*|\bin.?house\b|\bvs\b|\bversus\b|сравни"),
    ("repair", r"\brepair\w*|\breplacement|ремонт"),
    ("fleet", r"\ballocat\w*|\bno.scan|\bfleet\b|\binventory\b|\boptimi[sz]ation\b|инвентар"),
    ("service", r"\bon.time\b|\bdeliver\w*|\blate\b|\bcycle|service (request|performance)|доставк"),
    ("sustainability", r"\bwater\b|\benergy\b|\bcarbon\b|\bco2\w*|\bemissions?\b|\bcircular\w*|\brecycl\w*|sustainab\w*|\besg\b"
                       r"|\bвод[аыуе]\b|энерг|углерод|выброс"),
)
_DOWN_WORDS = r"\b(decreas\w*|reduc\w*|lower\w*|drops?|cuts?|falls?|discount\w*)\b|сниж|уменьш|скидк"


def classify(question: str, topic: str = "") -> str:
    """The focused view for a question. The assistant's topic wins; keyword rules cover the offline case."""
    if topic in config.FOCUS_TOPICS and topic != "general":
        return topic
    lowered = question.lower()
    return next((kind for kind, pattern in _TOPIC_WORDS if re.search(pattern, lowered)), "general")


def price_change(question: str, default: float = config.PRICE_STEP_DEFAULT) -> float:
    """The percentage in a pricing question, negative for a decrease, within PRICE_LIMITS."""
    lowered = question.lower()
    match = re.search(r"([+-]?\d+(?:[.,]\d+)?)\s*(?:%|percent|per cent|процент)", lowered)
    pct = float(match.group(1).replace(",", ".")) if match else default
    if re.search(_DOWN_WORDS, lowered):
        pct = -abs(pct)
    return max(PRICE_LIMITS[0], min(PRICE_LIMITS[1], pct))


def bullets(text: str, limit: int = 3) -> list[str]:
    """The bullet lines of an assistant reply, without their markers."""
    marker = r"^\s*(?:[-*•]|\d+[.)])\s+"
    return [re.sub(marker, "", line).strip() for line in text.splitlines() if re.match(marker, line)][:limit]


# --- Helpers ----------------------------------------------------------------------
def _from_metric(metric: Metric, level: str = INFO, note: str | None = None, label: str | None = None) -> Indicator:
    return Indicator(label or metric.label, metric.text, metric.tags, level, metric.note if note is None else note)


def _outlook(label: str, values: pd.Series, worse_when_rising: bool = True) -> Indicator:
    """Direction of a monthly series. Needs six months in view."""
    tags = (CALCULATED, TEAM_ASSUMPTION)
    values = pd.to_numeric(values, errors="coerce").dropna()
    if len(values) < 6 or not values.iloc[-6:-3].mean():
        return Indicator(label, "n/a", tags, INFO, "Needs at least six months in view.")
    change = (values.iloc[-3:].mean() / values.iloc[-6:-3].mean() - 1) * 100
    if abs(change) < STABLE_BAND_PCT:
        return Indicator(label, f"Stable ({change:+.1f}%)", tags, OK, OUTLOOK_NOTE)
    rising = change > 0
    level = WATCH if rising == worse_when_rising else OK
    return Indicator(label, f"{'Rising' if rising else 'Falling'} ({change:+.1f}%)", tags, level, OUTLOOK_NOTE)


def _flagged(frame: pd.DataFrame, column: str) -> int:
    return int((pd.to_numeric(frame[column], errors="coerce") >= engine.WATCH).sum())


def _level(count: int) -> str:
    return WATCH if count else OK


def months_in_view(ds: Dataset, ctx: ReviewContext) -> int:
    start, end = ctx.scope.start or ds.months[0], ctx.scope.end or ds.months[-1]
    return len([m for m in ds.months if start <= m <= end])


def hygiene_caution(hygiene: dict) -> str:
    share = f" ({hygiene['coverage']:.1f}%)" if hygiene["coverage"] is not None else ""
    return (f"Low-confidence screen. The cycle log traces {hygiene['traced']:,} of {hygiene['allocated']:,} allocated "
            f"hygiene garments{share}. Every flag comes from the log ending, not from a gap between two recorded "
            "scans, so confirm against full RFID records before raising it as a hygiene concern.")


# --- Views ------------------------------------------------------------------------
def _hygiene(ds: Dataset, ctx: ReviewContext, **_) -> FocusView:
    h, m = ctx.hygiene, ctx.metrics
    title = "Hygiene and food safety risk"
    if not h["applicable"]:
        lead = "This scope has no traced Food Industry or Healthcare garments, so the hygiene screen does not apply."
        return FocusView("hygiene", title, lead, insights=[lead])

    assets = engine.hygiene_assets(ds, ctx.scope)
    days = assets["days_since_scan"]
    near = int(days.between(config.HYGIENE_DAYS - NEAR_THRESHOLD_DAYS, config.HYGIENE_DAYS).sum())
    sites = ctx.sites[pd.to_numeric(ctx.sites["hygiene_share"], errors="coerce").notna()]
    in_review = _flagged(sites, "hygiene_status") if not sites.empty else 0
    flagged_list = assets[days > config.HYGIENE_DAYS].sort_values("days_since_scan", ascending=False)

    insights = [
        f"{h['flagged']:,} of {h['traced']:,} traced garments ({h['share']:.1f}%) have no scan for over "
        f"{config.HYGIENE_DAYS} days [Calculated]. This is a low-confidence screen, not a finding.",
        f"The log traces only {h['coverage']:.1f}% of this fleet [Calculated], so pull full RFID records for the "
        "flagged garments before raising it.",
    ]
    if not sites.empty:
        top = sites.sort_values("hygiene_share", ascending=False).iloc[0]
        insights.append(f"{top['site_name']} has the highest flagged share at {top['hygiene_share']:.1f}% [Calculated]. "
                        "Start the RFID check there.")

    return FocusView(
        "hygiene", title,
        f"{h['share']:.1f}% of traced hygiene-critical garments have no recent scan.",
        indicators=[
            _from_metric(m["hygiene_flagged"], WATCH if h["flagged"] else OK, note="Low-confidence screen."),
            _from_metric(m["hygiene_coverage"], WATCH, note="How much of the fleet the screen can see."),
            Indicator("Sites in a review band", f"{in_review} of {len(sites)}", (CALCULATED, TEAM_ASSUMPTION), _level(in_review),
                      f"Watch from {config.HYGIENE_WATCH:.0%}, High from {config.HYGIENE_HIGH:.0%} of traced garments."),
            Indicator(f"Reach {config.HYGIENE_DAYS} days within {NEAR_THRESHOLD_DAYS} days", f"{near:,} garments",
                      (CALCULATED, TEAM_ASSUMPTION), _level(near), "Next to flag if no new scan is logged."),
        ],
        insights=insights,
        donut=charts.donut([(f"No scan for over {config.HYGIENE_DAYS} days", h["flagged"]), ("Scanned more recently", h["traced"] - h["flagged"])],
                           f"{h['share']:.1f}%", colors=(charts.ORANGE, charts.NAVY)),
        donut_title="Traced garments by last scan [Calculated]",
        chart=None if sites.empty else charts.status_bars(sites, "site_name", "hygiene_share", "hygiene_status",
                                                          ""),
        chart_title="Flagged share of traced garments by site [Calculated]",
        caution=hygiene_caution(h),
        table=flagged_list.assign(last_scan=flagged_list["last_scan"].dt.date).rename(columns={
            "asset_id": "Garment", "site_name": "Site", "product_group": "Category",
            "last_scan": "Last scan [Measured]", "days_since_scan": "Days since scan [Calculated]",
        })[["Garment", "Site", "Category", "Last scan [Measured]", "Days since scan [Calculated]"]].head(200),
        table_title=f"Flagged garments (longest gap first, {min(len(flagged_list), 200):,} of {len(flagged_list):,} shown)",
        method=[config.HYGIENE_DAYS_RATIONALE, config.HYGIENE_GROUPS_RATIONALE, config.HYGIENE_RATE_RATIONALE],
    )


def _pricing(ds: Dataset, ctx: ReviewContext, pct: float = config.PRICE_STEP_DEFAULT, contract: float | None = None,
             **_) -> FocusView:
    """A price what-if. The workbook has no service prices, so a service figure needs the KAM's contract value."""
    m, factor = ctx.metrics, 1 + pct / 100
    move = "increase" if pct >= 0 else "decrease"
    tags = (ESTIMATED, TEAM_ASSUMPTION)
    months = months_in_view(ds, ctx)
    avoided, gap_value = m["avoided_eur"].value, m["gap_value"].value

    indicators, insights, rows = [], [], []
    if contract:
        period_value = contract * months / 12
        change = period_value * pct / 100
        signed = f"{'+' if change >= 0 else '-'}{format_value(abs(change), 'EUR')}"
        indicators.append(Indicator(f"Contract cost change, {months} months", signed, tags, WATCH if pct > 0 else OK,
                                    f"KAM input: {format_value(contract, 'EUR')} a year. Customer-reported, not verified."))
        insights.append(f"A {abs(pct):g}% {move} changes the contract by {signed} over {months} months [Estimated], "
                        "from the contract value you entered.")
        if avoided and pct > 0:
            indicators.append(Indicator("Repair value against the increase", f"{avoided * factor / change:,.1f}x", tags, INFO,
                                        "Purchase cost avoided by repairs at the new prices, divided by the increase."))
    else:
        indicators.append(Indicator("Contract cost change", "Needs contract value", tags, INFO,
                                    "The workbook has no contract or service prices."))
        insights.append("The workbook has no contract or service prices, so the service cost impact needs the annual "
                        "contract value entered above.")

    if avoided is not None:
        indicators.append(Indicator("Purchase cost avoided by repairs", format_value(avoided * factor, "EUR"), tags, OK,
                                    f"Now {m['avoided_eur'].text}, if garment prices move {pct:+g}%."))
        rows += [("Repairs avoid, today", avoided), ("Repairs avoid, what-if", avoided * factor)]
        insights.append(f"If garment purchase prices move {pct:+g}%, repairs avoid {format_value(avoided * factor, 'EUR')} "
                        f"instead of {m['avoided_eur'].text} [Estimated].")
    if gap_value is not None:
        indicators.append(Indicator(f"Purchase value of {config.GAP_METRIC.lower()}", format_value(gap_value * factor, "EUR"),
                                    tags, INFO, f"Now {m['gap_value'].text}. {config.GAP_FRAMING}, not a saving."))
        rows += [("No-scan value, today", gap_value), ("No-scan value, what-if", gap_value * factor)]
        insights.append(f"The {config.GAP_METRIC.lower()} would carry {format_value(gap_value * factor, 'EUR')} of purchase "
                        f"value [Estimated]. This is an {config.GAP_FRAMING.lower()}.")

    slices = ([("Current price", 100.0), ("Increase", pct)] if pct >= 0
              else [("New price", 100.0 + pct), ("Reduction", -pct)])
    return FocusView(
        "pricing", f"What-if: pricing {pct:+g}%",
        f"A {abs(pct):g}% {move} applied to the synthetic garment purchase costs"
        + (" and your contract value." if contract else ". Add the contract value to see the service cost impact."),
        indicators=indicators, insights=insights[:3],
        donut=charts.donut(slices, f"{pct:+g}%", colors=(charts.NAVY, charts.ORANGE)),
        donut_title="Price change against the current price [TEAM_ASSUMPTION]",
        chart=charts.compare_bars(rows, "", ",.0f", "€") if rows else None,
        chart_title="Value at today's prices and at the what-if [Estimated]",
        caution=("Full TCO cannot be stated: the data pack has no contract or service pricing. Purchase costs are "
                 "synthetic, and this applies one percentage to all of them."),
        method=[config.SENSITIVITY["repair_rate"].rationale],
    )


def _whatif(ds: Dataset, ctx: ReviewContext, **_) -> FocusView:
    m = ctx.metrics
    donut, chart, details = None, None, []
    if not ctx.baselines.empty:
        frame = ctx.baselines.set_index("id")
        own = frame.loc["LINDSTROM", "water_l_per_kg"] if "LINDSTROM" in frame.index else None
        other = (frame.loc[ctx.scenario.baseline_id, "water_l_per_kg"]
                 if ctx.scenario.baseline_id in frame.index else None)
        if own is not None and other is not None and other > own:
            donut = charts.donut([("Lindström water", own), ("Saved against the baseline", other - own)],
                                 f"{(other - own) / other * 100:.0f}%", colors=(charts.NAVY, charts.GREY))
        pick = ctx.scenario.baseline_id
        chart = charts.baseline_bars(ctx.baselines, "co2_kg_per_kg", "", pick, ".2f")
        details = [charts.baseline_bars(ctx.baselines, "water_l_per_kg", "Water, L per kg [Estimated]", pick, ".1f"),
                   charts.baseline_bars(ctx.baselines, "energy_kwh_per_kg", "Energy, kWh per kg [Estimated]", pick, ".2f")]
    if not ctx.sensitivity.empty:
        details += [charts.range_bars(ctx.sensitivity, "CO2 saved [Estimated] (kgCO2e)", "CO2 saved, kgCO2e [Estimated]"),
                    charts.range_bars(ctx.sensitivity, "Water saved [Estimated] (L)", "Water saved, L [Estimated]")]
    co2 = m["co2_saved"]
    return FocusView(
        "whatif", f"What-if against {ctx.baseline}",
        f"{co2.text} avoided and {m['water_saved'].text} water saved at the current assumptions.",
        indicators=[_from_metric(m[k], OK if k in ("water_saved", "co2_saved") else INFO,
                                 note=f"Range: {m[k].range_text}" if m[k].range_text else "")
                    for k in ("water_saved", "co2_saved", "avoided_co2", "avoided_eur")],
        insights=[
            f"Against {ctx.baseline}: {co2.text} avoided [Estimated], range {co2.range_text} [TEAM_ASSUMPTION].",
            f"Repairs avoided {m['avoided_eur'].text} of purchases [Estimated] [TEAM_ASSUMPTION], assuming "
            f"{ctx.scenario.repair_rate:.2f} replacements postponed per repair.",
            f"Is {ctx.baseline} the alternative this customer would really use? Change it under What-if on the left.",
        ],
        donut=donut, donut_title="Water per kg washed against the baseline [Estimated]",
        chart=chart, chart_title="CO2e per kg washed [Estimated]",
        details=details,
        table=ctx.sensitivity.round(0) if not ctx.sensitivity.empty else None,
        table_title="Low / Base / High, all assumptions moved together [TEAM_ASSUMPTION]",
        method=[f"{a.name}: Low {a.low}, Base {a.base}, High {a.high} {a.unit}. {a.rationale}"
                for a in config.SENSITIVITY.values()],
    )


def _fleet(ds: Dataset, ctx: ReviewContext, **_) -> FocusView:
    m, sites = ctx.metrics, ctx.sites
    if sites.empty or m["gap"].value is None:
        return _empty("fleet", "Fleet allocation signal")
    gap, allocated = m["gap"].value, m["allocated"].value
    share = gap / allocated * 100 if allocated else 0
    trend = engine.monthly_trend(ds, ctx.scope)
    top = sites.sort_values("gap_rate", ascending=False).iloc[0]
    in_review = _flagged(sites, "fleet_status")
    return FocusView(
        "fleet", "Fleet allocation signal",
        f"{config.GAP_METRIC}: {m['gap'].text}, {share:.1f}% of allocated. {config.GAP_FRAMING}.",
        indicators=[
            _from_metric(m["gap"], WATCH if in_review else OK, note=f"May reflect {config.GAP_REASONS}."),
            _from_metric(m["gap_value"], INFO, note="Not a saving or a write-off."),
            Indicator("Sites at or above the review threshold", f"{in_review} of {len(sites)}", (CALCULATED, TEAM_ASSUMPTION),
                      _level(in_review), f"Watch from {config.FLEET_WATCH:.0%}, High from {config.FLEET_HIGH:.0%} of allocated."),
            _outlook("Direction of the monthly share", trend["gap_rate"]),
        ],
        insights=[
            f"{config.GAP_METRIC}: {m['gap'].text} [Calculated]. It may reflect {config.GAP_REASONS}.",
            f"{top['site_name']} has the highest share at {top['gap_rate']:.1f}% [Calculated].",
            "Ask the site to confirm the cause before discussing any allocation change.",
        ],
        donut=charts.donut([(config.GAP_METRIC, gap), ("Scanned", allocated - gap)], f"{share:.1f}%",
                           colors=(charts.ORANGE, charts.NAVY)),
        donut_title="Allocated garments, latest month [Calculated]",
        chart=charts.status_bars(sites, "site_name", "gap_rate", "fleet_status", ""),
        chart_title=f"{config.GAP_METRIC}, share of allocated by site [Calculated]",
        details=[charts.trend_line(trend, "gap_rate", f"{config.GAP_METRIC}, monthly share [Calculated]", ".1f")],
        method=[config.FLEET_RATIONALE],
    )


def _repair(ds: Dataset, ctx: ReviewContext, **_) -> FocusView:
    m = ctx.metrics
    types = engine.breakdown(ds, ctx.scope, "repair_type")
    if types.empty:
        return _empty("repair", "Repair and replacement signal")
    column = "Repairs [Measured]"
    head, rest = types.iloc[:4], types.iloc[4:]
    slices = [(str(r["Repair type"]).capitalize(), float(r[column])) for _, r in head.iterrows()]
    if not rest.empty:
        slices.append(("Other types", float(rest[column].sum())))
    total = float(types[column].sum())
    monthly = engine.monthly_sum(ds, ctx.scope, "repairs", "repair_count")
    review = ctx.repair[ctx.repair["status"] > engine.OK] if not ctx.repair.empty else ctx.repair
    insights = [
        f"{m['repairs'].text} [Measured] avoided an estimated {m['avoided_eur'].text} of purchases [Estimated].",
        f"{slices[0][0]} is the most common repair, {slices[0][1] / total * 100:.0f}% of the total [Calculated].",
    ]
    if not ctx.repair.empty:
        worst = ctx.repair.iloc[0]
        insights.append(f"{worst['site_name']} repairs {worst['product_name']} at {worst['ratio']:.2f}x the product median "
                        "[Calculated]. Would an upgrade cost less than continued repair?")
    table = None
    if not review.empty:
        table = review.assign(
            rate_pct=review["rate_pct"].round(2), ratio=review["ratio"].round(2),
            status=review["status"].map(engine.STATUS_LABEL),
        ).rename(columns={
            "site_name": "Site", "product_name": "Product", "repairs": "Repairs [Measured]",
            "rate_pct": "Repairs per 100 washes [Calculated]", "ratio": "Vs product median [Calculated]",
            "status": "Status [TEAM_ASSUMPTION]",
        })[["Site", "Product", "Repairs [Measured]", "Repairs per 100 washes [Calculated]",
            "Vs product median [Calculated]", "Status [TEAM_ASSUMPTION]"]]
    return FocusView(
        "repair", "Repair and replacement signal",
        f"{m['repairs'].text} completed, avoiding an estimated {m['avoided_eur'].text} of purchases.",
        indicators=[
            _from_metric(m["repairs"]),
            _from_metric(m["avoided_eur"], OK, note=f"Range: {m['avoided_eur'].range_text}"),
            Indicator("Site and product pairs above threshold", f"{len(review):,}", (CALCULATED, TEAM_ASSUMPTION),
                      _level(len(review)), f"Watch from {config.REPAIR_WATCH:.2f}x, High from {config.REPAIR_HIGH:.2f}x the median."),
            _outlook("Direction of monthly repairs", monthly["repair_count"]),
        ],
        insights=insights,
        donut=charts.donut(slices, f"{total:,.0f}"), donut_title="Repairs by type [Measured]",
        chart=charts.trend_line(monthly, "repair_count", "", ",.0f"), chart_title="Repairs by month [Measured]",
        table=table, table_title="Site and product pairs above the review threshold",
        method=[config.REPAIR_RATIONALE, "The Repairs sheet has no asset IDs, so this is by site and product, not by garment."],
    )


def _sustainability(ds: Dataset, ctx: ReviewContext, **_) -> FocusView:
    m = ctx.metrics
    monthly = engine.monthly_savings(ds, ctx.scope, ctx.scenario)
    if monthly.empty or m["water_saved"].value is None:
        return _empty("sustainability", "Sustainability value")
    rate = m["circular_rate"].value
    donut = None if rate is None else charts.donut([("Recycled or reused", rate), ("Other routes", 100 - rate)], f"{rate:.1f}%",
                                                  colors=(charts.NAVY, charts.GREY))
    in_view = monthly[monthly["selected"]]
    return FocusView(
        "sustainability", "Sustainability value",
        f"{m['water_saved'].text} water, {m['energy_saved'].text} energy and {m['co2_saved'].text} saved vs {ctx.baseline}.",
        indicators=[
            _from_metric(m["water_saved"], OK, note=f"Range: {m['water_saved'].range_text}"),
            _from_metric(m["energy_saved"], OK, note=""),
            _from_metric(m["co2_saved"], OK, note=f"Range: {m['co2_saved'].range_text}"),
            _outlook("Direction of monthly CO2 savings", in_view["co2"], worse_when_rising=False),
        ],
        insights=[
            f"Against {ctx.baseline}: {m['water_saved'].text} water and {m['co2_saved'].text} avoided [Estimated].",
            f"The service footprint is {m['footprint_co2'].text} [Estimated], excluding garment production.",
            f"{m['circular_rate'].text} of attributed textile waste goes to recycling or reuse [Estimated]."
            if rate is not None else "No circularity data in this scope.",
        ],
        donut=donut, donut_title="Attributed textile waste by route [Estimated]",
        chart=charts.monthly_bars(monthly, "water", ""), chart_title=f"Water saved by month vs {ctx.baseline}, L [Estimated]",
        details=[charts.monthly_bars(monthly, "energy", "Energy saved by month, kWh [Estimated]"),
                 charts.monthly_bars(monthly, "co2", "CO2 saved by month, kgCO2e [Estimated]")],
    )


def _service(ds: Dataset, ctx: ReviewContext, **_) -> FocusView:
    m, sites = ctx.metrics, ctx.sites
    if m["on_time"].value is None:
        return _empty("service", "Service reliability")
    rate = m["on_time"].value
    requests = engine.monthly_sum(ds, ctx.scope, "activity", "service_requests")
    timed = sites[pd.to_numeric(sites["on_time"], errors="coerce").notna()] if not sites.empty else sites
    insights = [f"{m['on_time'].text} of logged cycles on time, mean {m['mean_cycle'].text} [Measured]."]
    indicators = [_from_metric(m["on_time"], note="Late means over 120 h."), _from_metric(m["mean_cycle"]),
                  _from_metric(m["service_requests"]), _outlook("Direction of service requests", requests["service_requests"])]
    if not timed.empty:
        low = timed.sort_values("on_time").iloc[0]
        insights.append(f"{low['site_name']} has the lowest on-time rate at {low['on_time']:.1f}% [Measured].")
    insights.append("Which stage of the delivery cycle matters most to site operations next year?")
    return FocusView(
        "service", "Service reliability", f"{m['on_time'].text} of logged cycles on time.",
        indicators=indicators, insights=insights,
        donut=charts.donut([("On time", rate), ("Late", 100 - rate)], f"{rate:.1f}%", colors=(charts.NAVY, charts.ORANGE)),
        donut_title="Logged service cycles [Measured]",
        chart=charts.simple_bars(ctx.stages, "Delivery stage", "Mean hours [Measured]", "", ".1f") if not ctx.stages.empty else None,
        chart_title="Mean hours by delivery stage [Measured]",
        caution="From the sampled cycle log, by pickup month.",
    )


def _general(ds: Dataset, ctx: ReviewContext, **_) -> FocusView:
    m, sites = ctx.metrics, ctx.sites
    if sites.empty:
        return _empty("general", "Account signals")
    worst = pd.concat([pd.to_numeric(sites[c], errors="coerce") for c in ("fleet_status", "repair_status")], axis=1).max(axis=1)
    counts = [(engine.STATUS_LABEL[level], int((worst.fillna(0) == level).sum()))
              for level in (engine.OK, engine.WATCH, engine.HIGH)]
    points = narrative.discussion_points(ctx, 3)
    return FocusView(
        "general", "Account signals", narrative.headline(ctx),
        indicators=[_from_metric(m[k]) for k in config.KPI_METRICS],
        insights=[f"{p.title}: {p.evidence}" for p in points],
        donut=charts.donut(counts, f"{len(sites)}", colors=("#cbd5e1", charts.ORANGE, "#b91c1c")),
        donut_title="Sites by highest signal [Calculated]",
        chart=charts.site_heatmap(sites), chart_title="",
    )


def _export(ds: Dataset, ctx: ReviewContext, **_) -> FocusView:
    return FocusView("export", "Steering review", f"{ctx.label}, {ctx.period}, against {ctx.baseline}.",
                     insights=[f"{p.title}: {p.evidence}" for p in narrative.discussion_points(ctx, 3)])


def _empty(kind: str, title: str) -> FocusView:
    lead = "No data for this topic in the current view. Widen the filters on the left."
    return FocusView(kind, title, lead, insights=[lead])


_BUILDERS = {"hygiene": _hygiene, "pricing": _pricing, "whatif": _whatif, "fleet": _fleet, "repair": _repair,
             "sustainability": _sustainability, "service": _service, "export": _export, "general": _general}


def build(ds: Dataset, ctx: ReviewContext, kind: str, pct: float = config.PRICE_STEP_DEFAULT,
          contract: float | None = None) -> FocusView:
    return _BUILDERS.get(kind, _general)(ds, ctx, pct=pct, contract=contract)
