"""Focused views: routing a question, and figures that come only from the engine."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import engine
import focus
import loader
from engine import Scenario, Scope


@pytest.fixture(scope="module")
def full():
    return loader.load_workbook()


def context(full, customer, start="2025-01", end="2025-12"):
    ds = loader.for_customer(full, customer)
    return ds, engine.build_context(ds, Scope(customer, start=start, end=end), Scenario(), config.STANDARD_ROLE)


@pytest.mark.parametrize("question, kind", [
    ("What are the hygiene and food safety risks?", "hygiene"),
    ("What-if pricing increases by 5%?", "pricing"),
    ("What if costs go up 3%?", "pricing"),
    ("What if the tariff changes?", "pricing"),
    ("Compare us with home washing", "whatif"),
    ("Which repair type was most common in Oulu?", "repair"),
    ("Which sites have the most allocated garments with no scan activity?", "fleet"),
    ("How much water did we save?", "sustainability"),
    ("Are deliveries on time?", "service"),
    ("Prepare talking points for the CFO", "export"),
    ("Какие риски по гигиене?", "hygiene"),
    ("Что если цены вырастут на 5%?", "pricing"),
    ("Hello there", "general"),
])
def test_keyword_routing(question, kind):
    assert focus.classify(question) == kind


def test_the_assistants_topic_wins_over_keywords():
    assert focus.classify("How much water did we save?", "hygiene") == "hygiene"
    assert focus.classify("How much water did we save?", "general") == "sustainability"
    assert focus.classify("How much water did we save?", "nonsense") == "sustainability"


@pytest.mark.parametrize("question, pct", [
    ("What-if pricing increases by 5%?", 5.0), ("prices up 7.5 percent", 7.5), ("цены вырастут на 3,5%", 3.5),
    ("a 4% price decrease", -4.0), ("what if we cut prices by 10%", -10.0), ("what if prices change", 5.0),
    ("prices up 900%", 100.0), ("prices drop 90%", -50.0),
])
def test_price_change(question, pct):
    assert focus.price_change(question) == pct


def test_bullets():
    text = "Intro\n- one\n* two\n1. three\n- four\nnot a bullet"
    assert focus.bullets(text) == ["one", "two", "three"]
    assert focus.bullets("plain text") == []


@pytest.mark.parametrize("customer", ["CUST001", "CUST002", "CUST003"])
@pytest.mark.parametrize("kind", config.FOCUS_TOPICS)
@pytest.mark.parametrize("period", [("2025-01", "2025-12"), ("2025-12", "2025-12")])
def test_every_view_builds_for_every_client_with_the_required_wording(full, customer, kind, period):
    ds, ctx = context(full, customer, *period)
    view = focus.build(ds, ctx, kind, 5.0, 420000.0)
    assert view.title and view.lead and 1 <= len(view.insights[:3]) <= 3
    words = f"{view.facts} {view.caution} {' '.join(view.method)}".lower()
    assert not any(term in words for term in (*config.BANNED_TERMS, "idle"))
    for item in view.indicators:
        assert item.value and item.tags, item.label


def test_pricing_arithmetic_is_deterministic(full):
    ds, ctx = context(full, "CUST001")
    view = focus.build(ds, ctx, "pricing", 5.0, 400000.0)
    values = {i.label: i.value for i in view.indicators}
    assert values["Contract cost change, 12 months"] == "+€20,000"
    avoided = ctx.metrics["avoided_eur"].value
    assert values["Purchase cost avoided by repairs"] == engine.format_value(avoided * 1.05, "EUR")
    assert values["Repair value against the increase"] == f"{avoided * 1.05 / 20000:,.1f}x"


def test_a_short_period_uses_its_share_of_the_annual_contract(full):
    ds, ctx = context(full, "CUST001", "2025-10", "2025-12")
    view = focus.build(ds, ctx, "pricing", 10.0, 400000.0)
    assert {i.label: i.value for i in view.indicators}["Contract cost change, 3 months"] == "+€10,000"


def test_pricing_without_a_contract_says_what_is_missing(full):
    ds, ctx = context(full, "CUST001")
    view = focus.build(ds, ctx, "pricing", 5.0, None)
    assert {i.label: i.value for i in view.indicators}["Contract cost change"] == "Needs contract value"


def test_a_price_decrease_is_described_as_one(full):
    ds, ctx = context(full, "CUST001")
    view = focus.build(ds, ctx, "pricing", -5.0, 400000.0)
    assert view.title == "What-if: pricing -5%" and "decrease" in view.lead
    assert {i.label: i.value for i in view.indicators}["Contract cost change, 12 months"] == "-€20,000"


def test_hygiene_counts_come_from_the_engine(full):
    ds, ctx = context(full, "CUST001")
    view = focus.build(ds, ctx, "hygiene")
    assert view.indicators[0].value == ctx.metrics["hygiene_flagged"].text
    assets = engine.hygiene_assets(ds, ctx.scope)
    near = int(assets["days_since_scan"].between(150, 180).sum())
    assert view.indicators[3].value == f"{near:,} garments"


def test_the_outlook_needs_six_months(full):
    ds, ctx = context(full, "CUST001", "2025-10", "2025-12")
    assert focus.build(ds, ctx, "fleet").indicators[3].value == "n/a"


def test_the_facts_carry_the_view_figures_for_the_assistant_check(full):
    ds, ctx = context(full, "CUST001")
    view = focus.build(ds, ctx, "pricing", 5.0, 400000.0)
    assert "+€20,000" in view.facts and "Contract cost change" in view.facts
