"""Streamlit interface for the Customer Review & Value Generator.

Three parts on one page: a filter sidebar, a canvas and the Insights assistant. The canvas shows a minimal overview,
or, once the KAM asks something, an isolated focused view of that one question (see focus.py).
"""
from __future__ import annotations

from dataclasses import replace
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

import charts
import config
import engine
import exporter
import focus
import narrative
from engine import Metric, Scenario, Scope
from loader import Dataset, LoadError, for_customer, load_workbook
from tools import DataTools

ALL = "All"
LEVEL_KEYS = {"baseline_scale": "lvl_baseline_scale", "repair_rate": "lvl_repair_rate",
              "footprint_scale": "lvl_footprint_scale"}
HERO_KPI = "water_saved"
KPI_FOCUS = {"water_saved": "sustainability", "allocated": "fleet", "footprint_co2": "sustainability",
             "risk_signals": "general"}
RULE_SOURCE = "Rule-based, generated from the engine"
THEME = Path(__file__).parent / "static" / "theme.css"

st.set_page_config(page_title="Customer Review & Value Generator", page_icon="static/logo.png", layout="wide",
                   initial_sidebar_state="expanded")
st.markdown(f"<style>{THEME.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


@st.cache_resource(show_spinner="Loading workbook")
def get_dataset() -> Dataset:
    return load_workbook()


@st.cache_resource(show_spinner="Loading client data")
def client_dataset(customer_id: str) -> Dataset:
    """Only the selected client's rows, so a KAM session never holds other clients' data."""
    return for_customer(get_dataset(), customer_id)


@st.cache_data(show_spinner=False)
def get_context(_ds: Dataset, scope: Scope, scenario: Scenario, role: str) -> engine.ReviewContext:
    return engine.build_context(_ds, scope, scenario, role)


# --- Small pieces --------------------------------------------------------------
def tag_html(tag: str) -> str:
    return f'<span class="tag tag-{tag}" title="{escape(config.PROVENANCE[tag])}">[{tag}]</span>'


def tags_html(tags: tuple[str, ...]) -> str:
    return "".join(tag_html(t) for t in tags)


def tiles(metrics: list[Metric]) -> None:
    html = []
    for m in metrics:
        rows = [m.range_text and f"Range: {m.range_text}", m.note]
        notes = "".join(f'<div class="note">{escape(r)}</div>' for r in rows if r)
        html.append(
            f'<div class="tile" title="{escape(m.formula)}"><div class="label">{escape(m.label)}</div>'
            f'<div class="value">{escape(m.text)}</div>{tags_html(m.tags)}{notes}</div>'
        )
    st.markdown(f'<div class="tiles">{"".join(html)}</div>', unsafe_allow_html=True)


def plot(fig) -> None:
    st.plotly_chart(fig, config=charts.CONFIG, width="stretch")


def table(frame: pd.DataFrame) -> None:
    st.dataframe(frame, hide_index=True, width="stretch")


def caution(text: str) -> None:
    st.markdown(f'<div class="caution">{escape(text)}</div>', unsafe_allow_html=True)


# --- State -----------------------------------------------------------------
def default_role(ds: Dataset, customer_id: str) -> str:
    industry = engine.customer_row(ds, customer_id)["industry"]
    return config.INDUSTRY_DEFAULT_ROLE.get(industry, config.STANDARD_ROLE)


def default_period(ds: Dataset) -> tuple[str, str]:
    return ds.months[0], ds.months[-1]


def current_period() -> tuple[str, str]:
    """The chosen months in order, even if 'from' was set after 'to'."""
    s = st.session_state
    return tuple(sorted((s.period_start, s.period_end)))


def ensure_state(ds: Dataset, customer_id: str) -> None:
    defaults = {
        "countries": [], "site_ids": [], "groups": [], "period_start": ds.months[0], "period_end": ds.months[-1],
        "role": default_role(ds, customer_id), "baseline": config.DEFAULT_BASELINE,
        "ai_label": "", "ai_signature": None, "ai_notes": [], "chat": [], "point_count": 3,
        "focus": None, "focus_seq": 0, "pending": "", "contract_value": 0.0,
        **{key: "Base" for key in LEVEL_KEYS.values()},
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)
    # A month the data does not have (stale or corrupted state) falls back to the edge of the range.
    first, last = default_period(ds)
    if st.session_state.period_start not in ds.months:
        st.session_state.period_start = first
    if st.session_state.period_end not in ds.months:
        st.session_state.period_end = last


def read_state() -> tuple[Scope, Scenario, str]:
    s = st.session_state
    start, end = current_period()
    scope = Scope(s.customer, tuple(s.countries), tuple(s.site_ids), start, end, tuple(s.groups))
    scenario = Scenario(s.baseline, s.lvl_baseline_scale, s.lvl_repair_rate, s.lvl_footprint_scale)
    return scope, scenario, s.role


def write_state(ds: Dataset, scope: Scope, scenario: Scenario, role: str) -> None:
    s = st.session_state
    s.countries, s.site_ids, s.groups = list(scope.countries), list(scope.site_ids), list(scope.product_groups)
    s.period_start, s.period_end = scope.start or ds.months[0], scope.end or ds.months[-1]
    s.baseline, s.role = scenario.baseline_id, role
    s.lvl_baseline_scale, s.lvl_repair_rate, s.lvl_footprint_scale = (
        scenario.baseline_level, scenario.repair_level, scenario.footprint_level)


def reset_view(ds: Dataset) -> None:
    scope = Scope(st.session_state.customer)
    write_state(ds, scope, Scenario(), default_role(ds, scope.customer_id))
    st.session_state.ai_label, st.session_state.ai_signature, st.session_state.ai_notes = "", None, []


def view_signature() -> tuple:
    """The current filters as one comparable value. The status line shows 'AI Custom Filter' only while it still matches."""
    s = st.session_state
    return (current_period(), tuple(s.countries), tuple(s.site_ids), tuple(s.groups), s.role, s.baseline,
            s.lvl_baseline_scale, s.lvl_repair_rate, s.lvl_footprint_scale)


def open_client() -> None:
    """Login button: open the selected client's dataset with a fresh default view."""
    customer_id = st.session_state.login_client
    ds = client_dataset(customer_id)
    st.session_state.customer = customer_id
    reset_view(ds)
    s = st.session_state
    s.chat, s.focus, s.pending, s.contract_value = [], None, "", 0.0
    s.page = "dashboard"


def go_login() -> None:
    st.session_state.page = "login"


def is_default_view(ds: Dataset) -> bool:
    s = st.session_state
    return (current_period() == default_period(ds) and not s.countries and not s.site_ids and not s.groups
            and s.baseline == config.DEFAULT_BASELINE and s.role == default_role(ds, s.customer)
            and all(s[key] == "Base" for key in LEVEL_KEYS.values()))


def view_summary(ds: Dataset) -> dict:
    """The current view in words, so the assistant can resolve 'now only Finland' style requests."""
    s = st.session_state
    sites = ds.sites.set_index("site_id")["site_name"].to_dict()
    return {
        "period": " to ".join(current_period()), "countries": list(s.countries),
        "sites": [sites[i] for i in s.site_ids if i in sites], "product_groups": list(s.groups),
        "focus": s.role, "baseline": engine.baseline_name(ds, s.baseline),
    }


# --- Focused view state ------------------------------------------------------------
def open_focus(kind: str, question: str = "", pct: float = config.PRICE_STEP_DEFAULT) -> None:
    """Replace the overview with one focused view. The rule-based insights show until the assistant supplies its own."""
    s = st.session_state
    s.focus_seq += 1
    s.focus = {"id": s.focus_seq, "kind": kind, "question": question, "pct": pct,
               "insights": None, "source": RULE_SOURCE, "signature": None}


def close_focus() -> None:
    st.session_state.focus = None


def focus_signature(pct: float, contract: float) -> tuple:
    """AI insights belong to these inputs. If any of them changes, the view falls back to rule-based insights."""
    return (view_signature(), pct, contract)


def run_question(ds: Dataset, question: str) -> None:
    """One assistant turn: Gemini extracts parameters, the engine recalculates, the canvas changes to a focused view,
    and Gemini writes its insights from those exact numbers."""
    s = st.session_state
    scope, scenario, role = read_state()
    plan = narrative.plan_view(ds, scope.customer_id, view_summary(ds), question)
    scope, scenario, role = engine.apply_plan(scope, scenario, role, plan, default_role(ds, scope.customer_id))
    only_reset = plan.reset and len(plan.labels) == 1
    if plan.changes:
        write_state(ds, scope, scenario, role)
        s.ai_label = "" if only_reset else plan.describe()
        s.ai_signature = None if only_reset else view_signature()
        s.ai_notes = [] if only_reset else list(plan.notes)

    ctx = engine.build_context(ds, scope, scenario, role)
    kind, pct = focus.classify(question, plan.topic), focus.price_change(question)
    view = focus.build(ds, ctx, kind, pct, s.contract_value or None)
    history = [{"role": m["role"], "content": m["content"]} for m in s.chat]
    answer = narrative.chat_answer(ctx, history, question, note=plan.describe() if plan.changes else "",
                                   tools=DataTools(ds, scope, scenario, role), extra_facts=view.facts, brief=True)
    if answer.text:
        text, source, untraced = answer.text, answer.source, answer.untraced
    else:
        head = ("The AI assistant needs a Gemini API key to answer questions. Set GEMINI_API_KEY in the terminal "
                "that runs the app and restart it." if answer.source.startswith("No Gemini API key")
                else f"The AI assistant is unavailable ({answer.source}).")
        text = (f"{head}\n\nMeanwhile, the figures for this view, straight from the data:\n\n"
                f"{narrative.snapshot_text(ctx)}\n\nRule-based talking points:\n\n{narrative.talking_points_text(ctx)}")
        source, untraced = RULE_SOURCE, []
    s.chat += [
        {"role": "user", "content": question},
        {"role": "assistant", "content": text, "source": source, "untraced": untraced,
         "scope": f"{ctx.label}, {ctx.period}, {ctx.baseline}",
         "applied": plan.describe() if plan.changes else "", "interpreted_by": plan.source, "notes": list(plan.notes),
         "queries": answer.queries},
    ]

    if only_reset:  # "back to the default view" is a request for the overview itself
        s.focus = None
        return
    open_focus(kind, question, pct)
    insights = focus.bullets(answer.text) if answer.text and not untraced else []
    if len(insights) >= 2:  # otherwise the rule-based insights stay, so no unchecked figure reaches the canvas
        s.focus.update(insights=insights, source=answer.source, signature=focus_signature(pct, float(s.contract_value)))


def queue_question(question: str) -> None:
    """Questions run at the top of the next script run, before the filter widgets exist, because they can change them."""
    st.session_state.pending = question


def on_chat_submit() -> None:
    question = (st.session_state.get("chat_text") or "").strip()
    if question:
        queue_question(question)


def clear_chat() -> None:
    st.session_state.chat = []


# --- Sidebar ---------------------------------------------------------------------
def sidebar(ds: Dataset) -> tuple[Scope, Scenario, str]:
    state = st.session_state
    with st.sidebar:
        st.markdown('<div class="brand"><img src="app/static/logo.png" alt="">Lindström</div>'
                    '<div class="nav-label">Account</div><div class="client-name">'
                    f"{escape(str(engine.customer_row(ds, state.customer)['customer_name']))}</div>",
                    unsafe_allow_html=True)
        st.button("Switch client", on_click=go_login)

        st.markdown('<div class="nav-label">View</div>', unsafe_allow_html=True)
        with st.expander("Filters"):
            own_sites = ds.sites[ds.sites["customer_id"] == state.customer]
            state.countries = [c for c in state.countries if c in set(own_sites["country"])]
            st.multiselect("Country operations", sorted(own_sites["country"].unique()), key="countries",
                           placeholder="All countries")
            shown = own_sites[own_sites["country"].isin(state.countries)] if state.countries else own_sites
            names = shown.set_index("site_id")["site_name"].to_dict()
            state.site_ids = [i for i in state.site_ids if i in names]
            st.multiselect("Delivery sites", list(names), format_func=names.get, key="site_ids",
                           placeholder="All sites")
            groups = sorted(ds.activity[ds.activity["customer_id"] == state.customer]["product_group"].unique())
            state.groups = [g for g in state.groups if g in groups]
            st.multiselect("Product categories", groups, key="groups", placeholder="All categories")
            left, right = st.columns(2)
            left.selectbox("Period from", ds.months, key="period_start")
            right.selectbox("Period to", ds.months, key="period_end")

        with st.expander("Audience"):
            st.radio("Priority role", list(config.ROLES), key="role")

        with st.expander("What-if assumptions"):
            if ds.baselines.empty:
                st.caption("Baselines sheet missing, so comparisons are unavailable.")
            else:
                options = ds.baselines.set_index("baseline_id")["baseline_name"].to_dict()
                st.radio("Comparison baseline", list(options), format_func=options.get, key="baseline")
            for key, assumption in config.SENSITIVITY.items():
                st.select_slider(
                    f"{assumption.name} [TEAM_ASSUMPTION]", config.LEVELS, key=LEVEL_KEYS[key],
                    help=f"Low {assumption.low} / Base {assumption.base} / High {assumption.high} "
                         f"{assumption.unit}. {assumption.rationale}",
                )
        st.markdown(f'<div class="fine">{escape(config.DISCLAIMER)}</div>', unsafe_allow_html=True)
    return read_state()


def status_line(ds: Dataset, ctx: engine.ReviewContext) -> None:
    """Which view the figures belong to, in one quiet line, with a way back when it is not the default."""
    s = st.session_state
    ai_active = bool(s.ai_label) and s.ai_signature == view_signature()
    if ai_active:
        kind, head = "ai", f"AI Custom Filter: {s.ai_label}"
    elif is_default_view(ds):
        first, last = default_period(ds)
        kind, head = "default", f"View: {'Full Year' if len(ds.months) == 12 else f'{first} to {last}'} (Default)"
    else:
        kind, head = "manual", f"View: {ctx.period} (manual filters)"
    detail = f"{ctx.label} | {ctx.period} | focus: {ctx.role} | baseline: {ctx.baseline}"
    notes = "".join(f'<span class="aside">{escape(n)}</span>' for n in s.ai_notes) if ai_active else ""
    st.markdown(f'<div class="status status-{kind}"><b>{escape(head)}</b><span class="detail">{escape(detail)}</span>'
                f"{notes}</div>", unsafe_allow_html=True)
    if not is_default_view(ds):
        st.button("Reset to default view", on_click=reset_view, args=(ds,))


# --- Overview ---------------------------------------------------------------------
def kpi_cards(ctx: engine.ReviewContext) -> None:
    keys = [HERO_KPI, *[k for k in config.KPI_METRICS if k != HERO_KPI]]
    for column, key in zip(st.columns(len(keys)), keys):
        m = ctx.metrics[key]
        with column, st.container(key="kpi_hero" if key == HERO_KPI else f"kpi_{key}"):
            tip = " ".join(x for x in (m.formula, m.range_text and f"Range: {m.range_text}.", m.note) if x)
            st.markdown(f'<div title="{escape(tip)}"><div class="kpi-label">{escape(m.label)}</div>'
                        f'<div class="kpi-value">{escape(m.text)}</div>{tags_html(m.tags)}</div>',
                        unsafe_allow_html=True)
            st.button("↗", key=f"open_{key}", help="Open this in a focused view", on_click=open_focus,
                      args=(KPI_FOCUS[key],))


def section_savings(ds: Dataset, ctx: engine.ReviewContext) -> None:
    monthly = engine.monthly_savings(ds, ctx.scope, ctx.scenario)
    if monthly.empty:
        return
    with st.expander("Savings by month"):
        left, right = st.columns(2)
        with left:
            plot(charts.monthly_bars(monthly, "water", "Water saved by month, L [Estimated]"))
        with right:
            plot(charts.monthly_bars(monthly, "energy", "Energy saved by month, kWh [Estimated]"))
        st.caption(f"Against {ctx.baseline}. Dark bars are the months in the current view.")


def section_priority(ctx: engine.ReviewContext) -> None:
    role = config.ROLES[ctx.role]
    if not role["metrics"]:
        return
    with st.expander(f"Priority for {ctx.role}"):
        st.caption(role["blurb"])
        tiles([ctx.metrics[k] for k in role["metrics"]])
        if ctx.role == config.CFO_ROLE:
            caution("Full TCO cannot be stated: the data pack has no contract or service pricing. "
                    "The figures above are the TCO inputs it does support.")
        if ctx.role == config.QUALITY_ROLE and ctx.hygiene["applicable"]:
            caution(focus.hygiene_caution(ctx.hygiene))


def section_sites(ctx: engine.ReviewContext) -> None:
    with st.expander(f"Sites ({len(ctx.sites)})"):
        if ctx.sites.empty:
            st.info("No activity in this scope.")
            return
        plot(charts.site_heatmap(ctx.sites))
        table(ctx.sites.assign(
            gap_rate=ctx.sites["gap_rate"].round(1),
            washed_kg=ctx.sites["washed_kg"].round(0),
            water_saved=pd.to_numeric(ctx.sites["water_saved"]).round(0),
            co2_saved=pd.to_numeric(ctx.sites["co2_saved"]).round(0),
        ).rename(columns={
            "site_name": "Site", "country": "Country", "site_type": "Type",
            "wearers": "Wearers, deduplicated [Calculated]",
            "allocated": "Allocated pieces [Measured]",
            "gap": f"{config.GAP_METRIC} [Calculated]",
            "gap_rate": "Share of allocated, % [Calculated]",
            "washed_kg": "Washed kg [Measured]",
            "water_saved": "Water saved, L [Estimated]",
            "co2_saved": "CO2 saved, kgCO2e [Estimated]",
            "repairs": "Repairs [Measured]",
        })[["Site", "Country", "Type", "Wearers, deduplicated [Calculated]", "Allocated pieces [Measured]",
            f"{config.GAP_METRIC} [Calculated]", "Share of allocated, % [Calculated]", "Washed kg [Measured]",
            "Water saved, L [Estimated]", "CO2 saved, kgCO2e [Estimated]", "Repairs [Measured]"]])
        st.caption("Wearers use the largest product category per site, because one employee wears several garment types.")


def section_drill(ds: Dataset, ctx: engine.ReviewContext) -> None:
    scope = ctx.scope
    with st.expander("Drill-down"):
        sites = engine.scope_sites(ds, scope)
        left, middle, right = st.columns(3)
        names = sites.set_index("site_id")["site_name"].to_dict()
        site = left.selectbox("Delivery site", [ALL, *names], format_func=lambda s: names.get(s, "All sites in scope"))
        months = [m for m in ds.months if (scope.start or ds.months[0]) <= m <= (scope.end or ds.months[-1])]
        month = middle.selectbox("Month", [ALL, *months], format_func=lambda m: "All months" if m == ALL else m)
        groups = sorted(ds.activity[ds.activity["customer_id"] == scope.customer_id]["product_group"].unique())
        groups = [g for g in groups if not scope.product_groups or g in scope.product_groups]
        group = right.selectbox("Product category", [ALL, *groups], format_func=lambda g: "All categories" if g == ALL else g)

        drilled = scope
        if site != ALL:
            drilled = replace(drilled, site_ids=(site,))
        if month != ALL:
            drilled = replace(drilled, start=month, end=month)
        if group != ALL:
            drilled = replace(drilled, product_groups=(group,))

        trail = [ctx.customer["customer_name"], names.get(site, "All sites"),
                 "All months" if month == ALL else month, "All categories" if group == ALL else group]
        st.caption(" > ".join(trail) + " > Repair type / Delivery stage")

        next_level = "site" if site == ALL else "month" if month == ALL else "product_group"
        frame = engine.breakdown(ds, drilled, next_level)
        if frame.empty:
            st.info("No activity for this selection.")
        else:
            table(frame.round(1))

        left, right = st.columns(2)
        repairs = engine.breakdown(ds, drilled, "repair_type")
        stages = engine.breakdown(ds, drilled, "stage")
        with left:
            if repairs.empty:
                st.info("No repair data for this selection.")
            else:
                plot(charts.simple_bars(repairs, "Repair type", "Repairs [Measured]", "Repairs by type [Measured]"))
        with right:
            if stages.empty:
                st.info("No cycle data for this selection.")
            else:
                plot(charts.simple_bars(stages, "Delivery stage", "Mean hours [Measured]",
                                        "Mean hours by delivery stage [Measured]", ".1f"))
                st.caption("From the sampled cycle log, by pickup month.")


def section_reading(ds: Dataset, ctx: engine.ReviewContext) -> None:
    priorities = ds.priorities[ds.priorities["industry"] == ctx.customer["industry"]].sort_values("priority_rank")
    with st.expander("How to read these figures"):
        for tag, description in config.PROVENANCE.items():
            st.markdown(f'<div class="legend-row">{tag_html(tag)} {escape(description)}</div>', unsafe_allow_html=True)
        st.caption("Hover a card for its formula. Each focused view states the thresholds and assumptions behind it.")
        for limit in narrative.DATA_LIMITS:
            st.markdown(f"- {limit}")
        if not priorities.empty:
            st.markdown(f"**{ctx.customer['industry']} priorities used for this customer**")
            for _, row in priorities.iterrows():
                st.markdown(f"{row['priority_rank']}. **{row['priority_theme']}.** {row['dashboard_emphasis']}")
        for issue in ds.issues:
            st.warning(f"{issue.sheet}: {issue.message}")


def view_overview(ds: Dataset, ctx: engine.ReviewContext) -> None:
    left, right = st.columns([4, 1.3], vertical_alignment="bottom")
    left.markdown(f'<div class="phead"><h1>{escape(str(ctx.customer["customer_name"]))}</h1>'
                  f'<div class="pmeta">{escape(narrative.headline(ctx))}</div></div>', unsafe_allow_html=True)
    right.button("Steering review", type="primary", key="open_export", on_click=open_focus, args=("export",),
                 width="stretch")
    status_line(ds, ctx)
    kpi_cards(ctx)
    st.markdown("")  # breathing room between the cards and the accordions
    section_savings(ds, ctx)
    with st.expander("Service and operations"):
        tiles([ctx.metrics[k] for k in config.STANDARD_METRICS])
    section_priority(ctx)
    section_sites(ctx)
    section_drill(ds, ctx)
    section_reading(ds, ctx)


# --- Focused view --------------------------------------------------------------------
def indicators_html(items: list[focus.Indicator]) -> str:
    html = []
    for i in items:
        word = {"ok": "● OK", "watch": "▲ Watch", "high": "■ High"}.get(i.level, "")
        level = f'<span class="level level-{i.level}">{word}</span>' if word else ""
        note = f'<div class="note">{escape(i.note)}</div>' if i.note else ""
        html.append(f'<div class="ind"><div class="label">{escape(i.label)}</div>'
                    f'<div class="value">{escape(i.value)}</div>{level}{tags_html(i.tags)}{note}</div>')
    return f'<div class="inds">{"".join(html)}</div>'


def pricing_controls(f: dict) -> tuple[float, float]:
    """The what-if inputs, keyed to this focused view so a new question starts from its own percentage."""
    s = st.session_state
    left, right = st.columns(2)
    pct = left.slider("Price change, %", *focus.PRICE_LIMITS, value=float(f["pct"]), step=0.5, format="%+.1f%%",
                      key=f"price_pct_{f['id']}")
    contract = right.number_input("Annual contract value, EUR [Estimated] KAM input", min_value=0.0, step=10_000.0,
                                  format="%.0f", value=float(s.contract_value), key=f"contract_{f['id']}",
                                  help="Not in the workbook. Customer-reported, not verified.")
    s.contract_value = contract
    return pct, contract


def view_steering(ctx: engine.ReviewContext) -> None:
    st.number_input("Discussion points on screen", 3, 7, key="point_count", help="The deck always carries the top three.")
    count = st.session_state.point_count
    signature = (ctx.scope, ctx.scenario, ctx.role, count)

    if st.button("Redraft with Gemini", help="Sends the engine's figures, not the workbook, to the Gemini API."):
        with st.spinner("Drafting"):
            st.session_state.ai_draft = (signature, *narrative.ai_discussion_points(ctx, count))

    draft = st.session_state.get("ai_draft")
    if draft and draft[0] == signature:
        points, source = draft[1], draft[2]
    else:
        points, source = narrative.discussion_points(ctx, count), RULE_SOURCE

    st.caption(f"Source: {source}")
    for index, point in enumerate(points, start=1):
        st.markdown(f"**{index}. {point.title}**  \n{point.evidence}  \n*{point.question}*")

    name = str(ctx.customer["customer_name"]).replace(" ", "_")
    st.download_button(
        "Download PowerPoint (5 editable slides)",
        exporter.build_deck(ctx, points, source),
        file_name=f"{name}_review_{ctx.period.replace(' ', '')}.pptx",
        mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        type="primary",
    )
    st.caption(f"Reflects the current scope, role and what-if settings: {ctx.label} | {ctx.period} | {ctx.baseline}.")


def view_focus(ds: Dataset, ctx: engine.ReviewContext, f: dict) -> None:
    st.button("← Return to Overview", key="return_overview", on_click=close_focus)
    head = st.container()
    pct, contract = pricing_controls(f) if f["kind"] == "pricing" else (f["pct"], float(st.session_state.contract_value))
    view = focus.build(ds, ctx, f["kind"], pct, contract or None)

    with head:
        ask = f'<div class="ask">You asked: {escape(f["question"])}</div>' if f["question"] else ""
        st.markdown(f'<div class="focus-head"><h1>{escape(view.title)}</h1>{ask}'
                    f'<div class="lead">{escape(view.lead)}</div></div>', unsafe_allow_html=True)
        status_line(ds, ctx)

    if view.caution:
        caution(view.caution)

    if view.donut is not None or view.indicators:
        left, right = st.columns([1, 1.5]) if view.donut is not None else (None, st.container())
        if left is not None:
            with left, st.container(key="card_visual"):
                st.markdown(f'<div class="card-title">{escape(view.donut_title)}</div>', unsafe_allow_html=True)
                plot(view.donut)
        with right, st.container(key="card_indicators"):
            st.markdown('<div class="card-title">Risk and impact indicators</div>' + indicators_html(view.indicators),
                        unsafe_allow_html=True)

    valid = f["insights"] and f["signature"] == focus_signature(pct, float(contract))
    with st.container(key="card_insights"):
        st.markdown(f'<div class="card-title">{"AI insights" if valid else "Insights"}</div>', unsafe_allow_html=True)
        for line in f["insights"] if valid else view.insights[:3]:
            st.markdown(f"- {line}")
        st.caption(f"Source: {f['source'] if valid else RULE_SOURCE}")

    if view.chart is not None:
        with st.container(key="card_chart"):
            if view.chart_title:
                st.markdown(f'<div class="card-title">{escape(view.chart_title)}</div>', unsafe_allow_html=True)
            plot(view.chart)

    if f["kind"] == "export":
        with st.container(key="card_steering"):
            view_steering(ctx)

    if view.details or view.table is not None or view.method:
        with st.expander("Click to view details"):
            if view.details:
                for column, figure in zip(st.columns(min(len(view.details), 2)) * len(view.details), view.details):
                    with column:
                        plot(figure)
            if view.table is not None and not view.table.empty:
                st.markdown(f"**{view.table_title}**")
                table(view.table)
            for line in view.method:
                st.caption(line)


# --- Assistant -----------------------------------------------------------------------
def show_assistant_details(message: dict) -> None:
    st.caption(f"{message['source']} | view: {message['scope']}")
    if message.get("applied"):
        st.info(f"Dashboard updated: {message['applied']} (understood by {message['interpreted_by']}).")
    for note in message.get("notes", []):
        st.caption(note)
    if message.get("queries"):
        with st.expander(f"Data queried ({len(message['queries'])})"):
            for query in message["queries"]:
                args = ", ".join(f"{k}={v!r}" for k, v in query["args"].items())
                st.caption(f"{query['tool']}({args})" + (f" returned an error: {query['error']}" if query["error"] else ""))
    if message["untraced"]:
        st.warning(
            "Figures not found in the dashboard data: " + ", ".join(message["untraced"])
            + ". They may be derived by the assistant, so check them before quoting."
        )


def suggestions(ds: Dataset, ctx: engine.ReviewContext) -> list[str]:
    starters = ["What-if pricing increases by 5%?", *narrative.chat_suggestions(ds, ctx.scope.customer_id)[:2]]
    if ctx.hygiene["applicable"]:
        starters.insert(0, "What are the hygiene and food safety risks?")
    return starters


def assistant(ds: Dataset, ctx: engine.ReviewContext) -> None:
    with st.container(key="rail"):
        st.markdown('<div class="rail-head"><b>Insights</b><span>Ask a question or try a what-if</span></div>',
                    unsafe_allow_html=True)
        history = st.session_state.chat
        if history:
            with st.container(height=480, border=False):
                for message in history:
                    with st.chat_message(message["role"]):
                        st.markdown(message["content"])
                        if message["role"] == "assistant":
                            show_assistant_details(message)
        else:
            for question in suggestions(ds, ctx):
                st.button(question, key=f"suggest_{question}", on_click=queue_question, args=(question,))
        st.chat_input("Ask, or run a what-if", key="chat_text", on_submit=on_chat_submit)
        if history:
            st.button("Clear conversation", on_click=clear_chat)
        st.caption("Sends this view's figures, not the workbook, to the Gemini API.")


# --- Pages -----------------------------------------------------------------------------
def page_login(full: Dataset) -> None:
    _, middle, _ = st.columns([1, 1.15, 1])
    with middle, st.container(key="login_card"):
        st.markdown('<img class="lg" src="app/static/logo.png" alt="Lindström">', unsafe_allow_html=True)
        st.title("Welcome back")
        st.caption("Customer value reviews for your accounts. Only the client you choose is opened.")
        customers = full.customers.set_index("customer_id")["customer_name"].to_dict()
        choice = st.selectbox("Assigned client", list(customers), format_func=customers.get, key="login_client")
        row = engine.customer_row(full, choice)
        st.caption(f"{row['industry']} | {row['segment']} | HQ {row['hq_country']}")
        st.button("Open Client Dashboard", type="primary", on_click=open_client, width="stretch")
        st.markdown(f'<div class="fine-box">{escape(config.DISCLAIMER)}</div>', unsafe_allow_html=True)


def page_dashboard() -> None:
    customer_id = st.session_state.customer
    ds = client_dataset(customer_id)
    ensure_state(ds, customer_id)
    if st.session_state.pending:
        question, st.session_state.pending = st.session_state.pending, ""
        with st.spinner("Working on your question"):
            run_question(ds, question)

    scope, scenario, role = sidebar(ds)
    ctx = get_context(ds, scope, scenario, role)
    canvas, rail = st.columns([2.7, 1], gap="medium")
    with canvas:
        if st.session_state.focus:
            view_focus(ds, ctx, st.session_state.focus)
        else:
            view_overview(ds, ctx)
    with rail:
        assistant(ds, ctx)


def main() -> None:
    try:
        full = get_dataset()
    except LoadError as exc:
        st.error(f"The workbook cannot be used. {exc}")
        st.stop()

    if st.session_state.get("page") == "dashboard" and st.session_state.get("customer"):
        try:
            page_dashboard()
        except LoadError as exc:
            st.error(f"This client's data cannot be opened. {exc}")
            st.button("Back to client selection", on_click=go_login)
    else:
        page_login(full)


main()
