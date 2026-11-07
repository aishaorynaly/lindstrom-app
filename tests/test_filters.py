"""Dynamic filtering must leave the deterministic math exact: every figure for a period or scope has to
reconcile to the raw workbook and to the other slices of the same data."""
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import engine
import loader
import narrative
from engine import Scenario, Scope

ADDITIVE = ("washed_kg", "washed_pieces", "service_requests", "repairs", "water_saved", "energy_saved", "co2_saved",
            "footprint_co2", "postponed", "avoided_eur", "avoided_co2", "textile_waste")


@pytest.fixture(scope="module")
def full():
    return loader.load_workbook()


@pytest.fixture(scope="module")
def ds(full):
    return loader.for_customer(full, "CUST001")


@pytest.fixture(scope="module")
def raw():
    return pd.read_excel(config.DATA_PATH, sheet_name=["Service_Activity", "Repairs", "Customer_Impacts"])


def values(metrics, keys=ADDITIVE):
    return {k: metrics[k].value for k in keys}


# --- Timeframes ---------------------------------------------------------------
@pytest.mark.parametrize("kind, start, end", [
    ("full_year", "2025-01", "2025-12"),
    ("last_month", "2025-12", "2025-12"),
    ("last_30_days", "2025-12", "2025-12"),
    ("last_quarter", "2025-10", "2025-12"),
    ("last_3_months", "2025-10", "2025-12"),
    ("last_6_months", "2025-07", "2025-12"),
    ("first_half", "2025-01", "2025-06"),
    ("second_half", "2025-07", "2025-12"),
    ("q1", "2025-01", "2025-03"),
    ("q2", "2025-04", "2025-06"),
    ("q3", "2025-07", "2025-09"),
    ("q4", "2025-10", "2025-12"),
])
def test_named_timeframes(ds, kind, start, end):
    frame = engine.resolve_timeframe(ds.months, kind)
    assert (frame.start, frame.end) == (start, end)


def test_special_timeframes(ds):
    assert engine.resolve_timeframe(ds.months, "month", month="2025-3").start == "2025-03"
    custom = engine.resolve_timeframe(ds.months, "custom", start_month="2025-04", end_month="2025-02")
    assert (custom.start, custom.end) == ("2025-02", "2025-04")
    assert engine.resolve_timeframe(ds.months, "month", month="2024-01") is None
    assert engine.resolve_timeframe(ds.months, "custom") is None
    assert engine.resolve_timeframe(ds.months, "next_week") is None
    assert "monthly" in engine.resolve_timeframe(ds.months, "last_30_days").note


def test_last_quarter_uses_the_last_completed_quarter():
    months = [f"2025-{m:02d}" for m in range(1, 9)]  # data ends in August
    frame = engine.resolve_timeframe(months, "last_quarter")
    assert (frame.start, frame.end, frame.label) == ("2025-04", "2025-06", "Last quarter (Q2 2025)")


# --- Exactness under filtering -----------------------------------------------------
@pytest.mark.parametrize("customer", ["CUST001", "CUST002", "CUST003"])
def test_months_add_up_to_the_full_period(full, customer):
    client = loader.for_customer(full, customer)
    whole = values(engine.rollup(client, Scope(customer), Scenario()))
    parts = {k: 0.0 for k in ADDITIVE}
    for month in client.months:
        for key, value in values(engine.rollup(client, Scope(customer, start=month, end=month), Scenario())).items():
            parts[key] += value or 0
    for key in ADDITIVE:
        assert parts[key] == pytest.approx(whole[key], rel=1e-9, abs=1e-6), key


def test_quarters_and_halves_add_up(ds):
    def total(kind):
        t = engine.resolve_timeframe(ds.months, kind)
        return values(engine.rollup(ds, Scope("CUST001", start=t.start, end=t.end), Scenario()))

    year = total("full_year")
    for key in ADDITIVE:
        quarters = sum(total(q)[key] or 0 for q in ("q1", "q2", "q3", "q4"))
        halves = sum(total(h)[key] or 0 for h in ("first_half", "second_half"))
        assert quarters == pytest.approx(year[key], rel=1e-9, abs=1e-6), key
        assert halves == pytest.approx(year[key], rel=1e-9, abs=1e-6), key


def test_countries_add_up_to_the_customer(ds):
    whole = values(engine.rollup(ds, Scope("CUST001"), Scenario()))
    countries = sorted(ds.sites["country"].unique())
    parts = [values(engine.rollup(ds, Scope("CUST001", countries=(c,)), Scenario())) for c in countries]
    for key in ADDITIVE:
        assert sum(p[key] or 0 for p in parts) == pytest.approx(whole[key], rel=1e-9, abs=1e-6), key


@pytest.mark.parametrize("start, end", [("2025-12", "2025-12"), ("2025-04", "2025-06"), ("2025-01", "2025-12")])
def test_period_figures_match_the_raw_workbook(ds, raw, start, end):
    def rows(frame):
        return frame[(frame["customer_id"] == "CUST001") & frame["month"].between(start, end)]

    metrics = engine.rollup(ds, Scope("CUST001", start=start, end=end), Scenario("B_LOCAL"))
    impacts, activity, repairs = rows(raw["Customer_Impacts"]), rows(raw["Service_Activity"]), rows(raw["Repairs"])

    # Raw savings are rounded per row, so allow a small absolute tolerance.
    assert metrics["water_saved"].value == pytest.approx(impacts["water_saving_vs_local_l"].sum(), abs=len(impacts))
    assert metrics["energy_saved"].value == pytest.approx(impacts["energy_saving_vs_local_kwh"].sum(), abs=len(impacts))
    assert metrics["co2_saved"].value == pytest.approx(impacts["emissions_saving_vs_local_kgco2e"].sum(), abs=len(impacts))
    footprint = impacts["allocated_process_kgco2e"].sum() + impacts["allocated_delivery_kgco2e"].sum()
    assert metrics["footprint_co2"].value == pytest.approx(footprint, abs=2 * len(impacts))
    assert metrics["washed_kg"].value == pytest.approx(activity["customer_washed_kg"].sum(), abs=0.01)
    assert metrics["repairs"].value == repairs["repair_count"].sum()
    # Stocks come from the last month in the period, never a sum over months.
    last = activity[activity["month"] == end]
    assert metrics["allocated"].value == last["allocated_pieces"].sum()
    assert metrics["gap"].value == (last["allocated_pieces"] - last["active_assets_scanned"]).sum()
    assert metrics["wearers"].value == last.groupby("site_id")["active_wearers"].max().sum()


def test_filtered_wearers_are_never_summed_across_categories(ds, raw):
    december = raw["Service_Activity"].query("customer_id == 'CUST001' and month == '2025-12'")
    metrics = engine.rollup(ds, Scope("CUST001", start="2025-12", end="2025-12"), Scenario())
    assert metrics["wearers"].value == 9607 < december["active_wearers"].sum() == 28367


def test_single_category_filter_is_exact(ds, raw):
    activity = raw["Service_Activity"].query("customer_id == 'CUST001'")
    coats = activity[activity["product_id"] == "P1004"]
    metrics = engine.rollup(ds, Scope("CUST001", product_groups=("Food Industry",)), Scenario())
    assert metrics["washed_kg"].value == pytest.approx(coats["customer_washed_kg"].sum(), abs=0.01)


def test_provenance_survives_every_period(ds):
    for kind in engine.TIMEFRAMES[:-3]:
        frame = engine.resolve_timeframe(ds.months, kind)
        ctx = engine.build_context(ds, Scope("CUST001", start=frame.start, end=frame.end), Scenario(), config.CFO_ROLE)
        for metric in ctx.metrics.values():
            assert metric.tags and set(metric.tags) <= set(config.PROVENANCE) and metric.formula
        assert ctx.metrics["gap"].label == config.GAP_METRIC


def test_monthly_savings_reconcile_and_flag_the_selected_months(ds):
    scope = Scope("CUST001", start="2025-04", end="2025-06")
    monthly = engine.monthly_savings(ds, scope, Scenario())
    whole = engine.rollup(ds, Scope("CUST001"), Scenario())
    assert len(monthly) == 12 and monthly["selected"].sum() == 3
    assert monthly["water"].sum() == pytest.approx(whole["water_saved"].value)
    assert monthly["energy"].sum() == pytest.approx(whole["energy_saved"].value)
    assert list(monthly[monthly["selected"]]["month"]) == ["2025-04", "2025-05", "2025-06"]


# --- New KPIs -----------------------------------------------------------------------
def test_kpi_tiles_have_tags_and_terms(ds):
    ctx = engine.build_context(ds, Scope("CUST001"), Scenario(), config.STANDARD_ROLE)
    for key in config.KPI_METRICS:
        assert ctx.metrics[key].tags
    assert ctx.metrics["allocated"].label == "Garments allocated" and ctx.metrics["allocated"].value == 31743
    assert "Scope 3" in ctx.metrics["footprint_co2"].label and config.ESTIMATED in ctx.metrics["footprint_co2"].tags
    assert "not audited" in ctx.metrics["footprint_co2"].note
    assert config.TEAM_ASSUMPTION in ctx.metrics["risk_signals"].tags


def test_risk_signals_count_sites_at_or_above_watch(ds):
    ctx = engine.build_context(ds, Scope("CUST001"), Scenario(), config.STANDARD_ROLE)
    expected = int((ctx.sites["fleet_status"].fillna(0) >= engine.WATCH).sum()
                   + (ctx.sites["repair_status"].fillna(0) >= engine.WATCH).sum())
    assert ctx.metrics["risk_signals"].value == expected > 0
    assert "low confidence" in ctx.metrics["risk_signals"].note  # hygiene is reported, not counted


def test_short_periods_carry_a_caution_on_risk_signals(ds):
    month = engine.build_context(ds, Scope("CUST001", start="2025-12", end="2025-12"), Scenario(), config.STANDARD_ROLE)
    year = engine.build_context(ds, Scope("CUST001"), Scenario(), config.STANDARD_ROLE)
    assert "Short period (1 month)" in month.metrics["risk_signals"].note
    assert "full year" in month.metrics["risk_signals"].note
    assert "Short period" not in year.metrics["risk_signals"].note


# --- Per-client dataset -----------------------------------------------------------------
@pytest.mark.parametrize("customer", ["CUST002", "CUST003"])
def test_client_dataset_gives_the_same_numbers(full, customer):
    client = loader.for_customer(full, customer)
    assert set(client.customers["customer_id"]) == {customer}
    assert set(client.sites["customer_id"]) == {customer} and set(client.activity["customer_id"]) == {customer}
    a = engine.rollup(full, Scope(customer), Scenario())
    b = engine.rollup(client, Scope(customer), Scenario())
    for key, metric in a.items():
        other = b[key].value
        assert (metric.value is None and other is None) or math.isclose(metric.value, other, rel_tol=1e-9, abs_tol=1e-9), key


def test_client_dataset_rejects_unknown_clients(full):
    with pytest.raises(loader.LoadError):
        loader.for_customer(full, "CUST999")


# --- Plans -------------------------------------------------------------------------------
def test_plan_validation(ds):
    plan = engine.plan_from_params(ds, "CUST001", {
        "change_view": True, "timeframe": "last_month", "focus": "CFO", "countries": ["finland", "Narnia"],
        "sites": ["Oulu", "Atlantis"], "product_groups": ["food industry"], "baseline": "home"})
    assert plan.timeframe.start == "2025-12" and plan.role == config.CFO_ROLE
    assert plan.countries == ("Finland",) and plan.baseline_id == "B_HOME"
    assert plan.product_groups == ("Food Industry",)
    assert plan.site_ids == (ds.sites.set_index("site_name").loc["NorthStar Oulu", "site_id"],)
    assert any("Narnia" in n for n in plan.notes) and any("Atlantis" in n for n in plan.notes)


def test_plan_rejects_what_the_data_cannot_answer(ds):
    plan = engine.plan_from_params(ds, "CUST001", {"change_view": True, "timeframe": "month", "month": "2019-05",
                                                    "focus": "astrology", "baseline": "moon"})
    assert not plan.changes and len(plan.notes) == 3


def test_a_plain_question_changes_nothing(ds):
    plan = engine.plan_from_params(ds, "CUST001", {"change_view": False, "timeframe": "last_month"})
    assert not plan.changes


def test_apply_plan_is_pure_and_reset_restores_defaults(ds):
    scope, scenario, role = Scope("CUST001", countries=("Sweden",), start="2025-03", end="2025-04"), Scenario("B_HOME"), config.ESG_ROLE
    plan = engine.plan_from_params(ds, "CUST001", {"timeframe": "last_month", "focus": "cfo"})
    first = engine.apply_plan(scope, scenario, role, plan)
    assert first == engine.apply_plan(scope, scenario, role, plan)
    assert first[0].countries == ("Sweden",) and first[0].start == "2025-12" and first[2] == config.CFO_ROLE
    assert scope.start == "2025-03"  # the input is untouched
    reset = engine.plan_from_params(ds, "CUST001", {"reset": True})
    assert engine.apply_plan(scope, scenario, role, reset, default_role=config.QUALITY_ROLE) == (
        Scope("CUST001"), Scenario(), config.QUALITY_ROLE)


# --- Keyword fallback ----------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("Show me how much water and energy we saved in the last month instead of the full year.", "Last month (2025-12)"),
    ("Покажи, сколько воды и энергии мы сэкономили за прошлый месяц", "Last month (2025-12)"),
    ("Results for the last 30 days", "Last 30 days (2025-12)"),
    ("What happened in March?", "Month 2025-03"),
    ("Что было в марте?", "Month 2025-03"),
    ("Q2 please", "Q2 2025"),
    ("за последние 3 месяца", "Last 3 months (2025-10 to 2025-12)"),
    ("первое полугодие", "First half 2025"),
])
def test_keyword_timeframes(ds, text, expected):
    assert narrative.rule_plan(ds, "CUST001", text).timeframe.label == expected


def test_keyword_plan_combines_and_ignores_questions(ds):
    plan = narrative.rule_plan(ds, "CUST001", "CFO view for Finland against home washing in the last quarter")
    assert (plan.role, plan.countries, plan.baseline_id) == (config.CFO_ROLE, ("Finland",), "B_HOME")
    assert plan.timeframe.label == "Last quarter (Q4 2025)"
    assert not narrative.rule_plan(ds, "CUST001", "How trustworthy is the hygiene flag?").changes
    assert narrative.rule_plan(ds, "CUST001", "сбрось фильтры").reset


# --- Gemini extraction ---------------------------------------------------------------------
def test_gemini_parameters_are_validated_by_the_engine(ds, fake_gemini):
    fake_gemini.reply_json({"change_view": True, "timeframe": "last_month", "focus": "cfo", "sites": ["Atlantis"]})
    view = {"period": "2025-01 to 2025-12", "countries": [], "sites": [], "product_groups": [],
            "focus": "standard", "baseline": "Outsourced local laundry"}
    plan = narrative.plan_view(ds, "CUST001", view, "water and energy for the last month please")

    assert plan.source == "Gemini" and plan.timeframe.label == "Last month (2025-12)" and plan.role == config.CFO_ROLE
    assert plan.site_ids is None and any("Atlantis" in n for n in plan.notes)
    body = fake_gemini.last["body"]
    props = body["generationConfig"]["responseJsonSchema"]["properties"]
    assert props["timeframe"]["enum"] == list(engine.TIMEFRAMES)
    assert "NorthStar Oulu" in props["sites"]["items"]["enum"] and props["countries"]["items"]["enum"] == ["Finland", "Poland", "Sweden"]
    assert "2025-12" in fake_gemini.system and "Current view" in fake_gemini.system
    assert fake_gemini.texts == ["water and energy for the last month please"]


def test_gemini_says_it_is_only_a_question(ds, fake_gemini):
    fake_gemini.reply_json({"change_view": False})
    assert not narrative.plan_view(ds, "CUST001", {}, "How is the CO2 calculated?").changes


@pytest.mark.parametrize("setup, reason", [
    (lambda f: f.reply_json("not json at all"), "Gemini returned unreadable parameters"),
    (lambda f: f.reply_json("[1, 2]"), "Gemini returned unreadable parameters"),
    (lambda f: f.fail(401), "Gemini API error 401"),
    (lambda f: f.fail(429), "Gemini API error 429"),
])
def test_extraction_falls_back_to_keywords(ds, fake_gemini, setup, reason):
    setup(fake_gemini)
    plan = narrative.plan_view(ds, "CUST001", {}, "water for the last month")
    assert plan.source.startswith(f"keyword rules ({reason}") and plan.timeframe.label == "Last month (2025-12)"


def test_extraction_without_a_key_falls_back(ds, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    plan = narrative.plan_view(ds, "CUST001", {}, "за прошлый месяц")
    assert plan.source == "keyword rules (No Gemini API key configured)" and plan.timeframe.start == "2025-12"
