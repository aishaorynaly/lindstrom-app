"""Reconciles the engine to the workbook and enforces the mandatory business rules."""
import sys
from pathlib import Path
from types import SimpleNamespace

import openpyxl
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import engine
import loader
import narrative
from engine import Scenario, Scope


@pytest.fixture(scope="session")
def ds():
    return loader.load_workbook()


@pytest.fixture(scope="session")
def sample():
    frame = pd.read_excel(config.DATA_PATH, sheet_name="Sample_Output")
    return frame.set_index("metric")["value"]


@pytest.fixture(scope="session")
def ctx(ds):
    return engine.build_context(ds, Scope("CUST001"), Scenario(), config.STANDARD_ROLE)


@pytest.mark.parametrize("key, row, tolerance", [
    ("sites", "Delivery sites", 0),
    ("allocated", "Garments allocated (Dec)", 0),
    ("washed_kg", "Washed kg 2025", 0.1),
    ("repairs", "Repairs 2025", 0),
    ("postponed", "Replacements postponed", 0),
    ("water_saved", "Water saving vs local", 50),
    ("co2_saved", "Emission saving vs local", 10),
    ("textile_waste", "Textile waste attributed", 0.01),
    ("service_requests", "Service requests", 0),
])
def test_reconciles_to_sample_output(ctx, sample, key, row, tolerance):
    assert ctx.metrics[key].value == pytest.approx(float(sample[row]), abs=tolerance)


def test_wearers_are_deduplicated_not_summed(ctx, sample):
    assert float(sample["Active wearers (Dec)"]) == 28367
    wearers = ctx.metrics["wearers"]
    assert wearers.value == 9607
    assert wearers.low <= wearers.value <= wearers.high
    assert config.CALCULATED in wearers.tags


def test_site_wearers_are_largest_category(ds):
    dec = ds.activity[ds.activity["month"] == "2025-12"]
    table = loader.dedupe_wearers(dec).set_index("site_id")["wearers"]
    assert (table == dec.groupby("site_id")["active_wearers"].max()).all()


def test_scope_by_country_is_a_subset(ds, ctx):
    finland = engine.rollup(ds, Scope("CUST001", countries=("Finland",)), Scenario())
    assert finland["sites"].value == 12
    assert finland["washed_kg"].value < ctx.metrics["washed_kg"].value


def test_gap_is_stock_not_summed_over_months(ds, ctx):
    dec = ds.activity[(ds.activity["customer_id"] == "CUST001") & (ds.activity["month"] == "2025-12")]
    assert ctx.metrics["gap"].value == (dec["allocated_pieces"] - dec["active_assets_scanned"]).sum()


def test_every_metric_carries_valid_provenance(ds):
    for customer in ds.customers["customer_id"]:
        for metric in engine.rollup(ds, Scope(customer), Scenario()).values():
            assert metric.tags and set(metric.tags) <= set(config.PROVENANCE)
            assert metric.formula


def test_home_baseline_beats_local_and_scale_moves_savings(ds):
    scope = Scope("CUST001")
    local = engine.rollup(ds, scope, Scenario("B_LOCAL"))["water_saved"].value
    home = engine.rollup(ds, scope, Scenario("B_HOME"))["water_saved"].value
    low = engine.rollup(ds, scope, Scenario("B_LOCAL", baseline_level="Low"))
    high = engine.rollup(ds, scope, Scenario("B_LOCAL", baseline_level="High"))
    assert home > local
    assert low["water_saved"].value < local < high["water_saved"].value
    assert config.TEAM_ASSUMPTION in low["water_saved"].tags


def test_repair_rate_scales_handprint(ds):
    base = engine.rollup(ds, Scope("CUST001"), Scenario())["avoided_eur"].value
    low = engine.rollup(ds, Scope("CUST001"), Scenario(repair_level="Low"))["avoided_eur"].value
    assert low == pytest.approx(base * 0.5)


def test_hygiene_uses_data_end_and_reports_coverage(ds, ctx):
    assert ds.as_of == pd.Timestamp("2026-01-04 09:00:00")
    assert ctx.hygiene["flagged"] == 249 and ctx.hygiene["traced"] == 698
    assert ctx.hygiene["coverage"] < 10
    assert "low confidence" in ctx.metrics["hygiene_flagged"].note


def test_customer_without_hygiene_garments_degrades(ds):
    apex = engine.build_context(ds, Scope("CUST002"), Scenario(), config.QUALITY_ROLE)
    assert apex.metrics["hygiene_flagged"].value is None
    assert apex.metrics["hygiene_flagged"].text == "n/a"
    assert all(p.topic != "hygiene" for p in narrative.discussion_points(apex, 7))


def test_workbook_wording_is_rewritten(ds):
    text = " ".join(ds.priorities["dashboard_emphasis"]).lower()
    assert "unused" not in text
    assert config.GAP_METRIC.lower() in text


@pytest.mark.parametrize("role", list(config.ROLES))
def test_narrative_uses_required_terms(ds, role):
    review = engine.build_context(ds, Scope("CUST001"), Scenario(), role)
    points = narrative.discussion_points(review, 7)
    text = " ".join(f"{p.title} {p.evidence} {p.question}" for p in points).lower()
    assert not any(term in text for term in config.BANNED_TERMS)
    assert "tco" in text and "cannot be stated" in text


def test_role_changes_priority_order(ds):
    first = {}
    for role in (config.QUALITY_ROLE, config.CFO_ROLE, config.ESG_ROLE):
        review = engine.build_context(ds, Scope("CUST001"), Scenario(), role)
        first[role] = narrative.discussion_points(review, 3)[0].topic
    assert first == {config.QUALITY_ROLE: "hygiene", config.CFO_ROLE: "fleet", config.ESG_ROLE: "baseline"}


def test_every_suggested_prompt_is_understood(ds):
    for prompt in ds.prompts["prompt"]:
        assert narrative.interpret_prompt(prompt, ds).understood, prompt


def test_prompt_mapping(ds):
    intent = narrative.interpret_prompt("Compare our circular service with local laundry in Finland", ds)
    assert intent.baseline_id == "B_LOCAL" and intent.countries == ("Finland",) and intent.view == "What-if"
    five = narrative.interpret_prompt("Prepare five discussion points for the annual steering committee.", ds)
    assert five.points == 5 and five.view == "Export"


def test_invented_figures_are_rejected():
    facts = "water saved 1,406,483 L"
    good = narrative.Point("x", "Water", "Saved 1,406,483 L [Estimated]", "Next step?")
    bad = narrative.Point("x", "Water", "Saved 2,000,000 L [Estimated]", "Next step?")
    banned = narrative.Point("x", "Unused allocation", "Saved 1,406,483 L", "Next step?")
    assert narrative._point_is_grounded(good, facts)
    assert not narrative._point_is_grounded(bad, facts)
    assert not narrative._point_is_grounded(banned, facts)


DRAFT = '{"points":[{"topic":"a","title":"%s","evidence":"%s","question":"Agree?"}]}'


def test_gemini_draft_with_invented_number_falls_back(ctx, fake_gemini):
    fake_gemini.reply(DRAFT % ("T", "Saved 123,456,789 L"))
    points, source = narrative.ai_discussion_points(ctx, 1)
    assert source.startswith("Rule-based (Gemini draft rejected")
    assert points == narrative.discussion_points(ctx, 1)


def test_gemini_grounded_draft_is_used(ctx, fake_gemini):
    fact = ctx.metrics["water_saved"].text
    fake_gemini.reply(DRAFT % ("Water", f"Saved {fact} [Estimated]"))
    points, source = narrative.ai_discussion_points(ctx, 1)
    assert source.startswith("AI-generated (gemini-2.5-flash)") and points[0].title == "Water"
    assert "gemini-2.5-flash:generateContent" in fake_gemini.last["path"]
    config_sent = fake_gemini.last["body"]["generationConfig"]
    assert config_sent["responseMimeType"] == "application/json"
    assert config.GAP_FRAMING in fake_gemini.system


def test_unreadable_gemini_draft_falls_back(ctx, fake_gemini):
    fake_gemini.reply("this is not json")
    points, source = narrative.ai_discussion_points(ctx, 1)
    assert source == "Rule-based (Gemini returned an unreadable draft)"
    assert points == narrative.discussion_points(ctx, 1)


def test_gemini_failures_fall_back_with_a_reason(ctx, fake_gemini, monkeypatch):
    fake_gemini.fail(429)
    assert narrative.ai_discussion_points(ctx, 1)[1].startswith("Rule-based (Gemini API error 429")
    monkeypatch.delenv("GEMINI_API_KEY")
    assert narrative.ai_discussion_points(ctx, 1)[1] == "Rule-based (No Gemini API key configured)"


@pytest.mark.parametrize("missing", [
    ("Service_Cycles",),
    ("Repairs",),
    ("Baselines",),
    ("Customer_Impacts",),
    ("Customer_Impacts", "Laundry_Resources", "Waste_Circularity"),
    ("Service_Cycles", "Repairs", "Baselines", "Customer_Impacts", "Laundry_Resources", "Waste_Circularity",
     "Industry_Priorities", "Assumptions", "Data_Quality", "Suggested_Prompts"),
])
def test_missing_optional_sheets_degrade_gracefully(tmp_path, missing):
    book = openpyxl.load_workbook(config.DATA_PATH)
    for name in missing:
        del book[name]
    path = tmp_path / "partial.xlsx"
    book.save(path)

    partial = loader.load_workbook(path)
    assert {issue.sheet for issue in partial.issues} >= set(missing) - {"Customer_Impacts"}
    review = engine.build_context(partial, Scope("CUST001"), Scenario(), config.ESG_ROLE)
    assert review.metrics["washed_kg"].value == pytest.approx(502074.7, abs=0.1)
    assert review.metrics["wearers"].value == 9607
    narrative.discussion_points(review, 3)


def test_customer_impacts_rebuilt_from_resources(tmp_path):
    book = openpyxl.load_workbook(config.DATA_PATH)
    del book["Customer_Impacts"]
    path = tmp_path / "partial.xlsx"
    book.save(path)
    partial = loader.load_workbook(path)
    water = engine.rollup(partial, Scope("CUST001"), Scenario())["water_saved"].value
    assert water == pytest.approx(1406482.6, rel=0.001)


def test_missing_critical_sheet_raises(tmp_path):
    book = openpyxl.load_workbook(config.DATA_PATH)
    del book["Service_Activity"]
    path = tmp_path / "broken.xlsx"
    book.save(path)
    with pytest.raises(loader.LoadError):
        loader.load_workbook(path)
