"""The assistant's data tools: every figure must come from the workbook through the engine, and the
function-calling loop must run through the real SDK."""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest
from google.genai import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import engine
import llm
import loader
import narrative
from engine import Scenario, Scope
from tools import DataTools


@pytest.fixture(scope="module")
def ds():
    return loader.for_customer(loader.load_workbook(), "CUST001")


@pytest.fixture(scope="module")
def raw():
    return pd.read_excel(config.DATA_PATH, sheet_name=["Service_Activity", "Repairs"])


@pytest.fixture
def tools(ds):
    return DataTools(ds, Scope("CUST001"), Scenario(), config.CFO_ROLE)


# --- Numbers come from the engine and the raw data ------------------------------------------------
def test_kpis_match_the_engine_for_any_slice(ds, tools):
    result = tools.get_kpis(timeframe="last_month", countries=["Finland"])
    expected = engine.build_context(ds, Scope("CUST001", countries=("Finland",), start="2025-12", end="2025-12"),
                                    Scenario(), config.CFO_ROLE)
    assert result["period"] == "2025-12" and "Finland" in result["scope"]
    for key in ("allocated", "water_saved", "energy_saved", "co2_saved", "footprint_co2", "repairs", "risk_signals"):
        assert result["metrics"][key]["value"] == expected.metrics[key].text, key
        assert result["metrics"][key]["provenance"] == list(expected.metrics[key].tags)
    assert result["metrics"]["gap"]["label"] == config.GAP_METRIC


def test_breakdown_matches_the_raw_repairs_sheet(ds, tools, raw):
    result = tools.get_breakdown(by="repair_type", timeframe="last_month", sites=["Oulu"])
    repairs = raw["Repairs"].query("customer_id == 'CUST001' and site_id == 'S004' and month == '2025-12'")
    expected = repairs.groupby("repair_type")["repair_count"].sum().to_dict()
    assert {r["Repair type"]: r["Repairs [Measured]"] for r in result["rows"]} == expected
    assert "Oulu" in result["scope"]


def test_site_table_ranks_by_the_requested_measure(ds, tools, raw):
    result = tools.get_site_table(sort_by="washed_kg", top_n=3)
    activity = raw["Service_Activity"].query("customer_id == 'CUST001'")
    top = activity.groupby("site_id")["customer_washed_kg"].sum().sort_values(ascending=False).head(3).index
    names = ds.sites.set_index("site_id").loc[top, "site_name"].tolist()
    assert [r["site"] for r in result["sites"]] == names
    lowest = tools.get_site_table(sort_by="washed_kg", top_n=1, descending=False)["sites"][0]["site"]
    assert lowest == ds.sites.set_index("site_id").loc[activity.groupby("site_id")["customer_washed_kg"].sum().idxmin(), "site_name"]


def test_monthly_series_adds_up_to_the_period_total(tools):
    for metric, key in (("water_saved_l", "water_saved"), ("energy_saved_kwh", "energy_saved"), ("repairs", "repairs"),
                        ("washed_kg", "washed_kg")):
        series = tools.get_monthly_series(metric=metric)["series"]
        total = tools.get_kpis()["metrics"][key]["raw"]
        assert len(series) == 12 and sum(p["value"] for p in series) == pytest.approx(total, rel=1e-3), metric
    quarter = tools.get_monthly_series(metric="water_saved_l", timeframe="last_quarter")
    assert [p["month"] for p in quarter["series"]] == ["2025-10", "2025-11", "2025-12"]


def test_signals_keep_the_required_framing(tools):
    result = tools.get_signals(timeframe="last_month")
    gap = result["allocated_garments_with_no_scan_activity"]
    assert gap["framing"] == config.GAP_FRAMING and config.GAP_REASONS in gap["may_reflect"]
    assert result["hygiene_screen"]["confidence"] == "low" and "RFID" in result["hygiene_screen"]["caveat"]
    assert "Short period" in result["active_risk_signals"]["note"]


def test_no_tool_output_uses_banned_wording(tools):
    tools.list_options()
    tools.get_kpis(), tools.get_signals(), tools.get_site_table(), tools.get_breakdown(by="site")
    text = " ".join(tools.outputs).lower()
    assert not any(term in text for term in (*config.BANNED_TERMS, "idle"))
    assert "allocated_garments_with_no_scan_activity" in text


def test_list_options(ds, tools):
    options = tools.list_options()
    assert options["months"] == ds.months and "NorthStar Oulu" in options["sites"]
    assert options["countries"] == ["Finland", "Poland", "Sweden"] and "Food Industry" in options["product_groups"]


# --- Bad requests never widen the query ----------------------------------------------------------------
@pytest.mark.parametrize("call, fragment", [
    (lambda t: t.get_kpis(sites=["Atlantis"]), "unknown site"),
    (lambda t: t.get_kpis(countries=["Narnia"]), "unknown country"),
    (lambda t: t.get_kpis(timeframe="next_week"), "timeframe"),
    (lambda t: t.get_kpis(timeframe="month", month="2019-01"), "timeframe"),
    (lambda t: t.get_kpis(baseline="moon"), "unknown baseline"),
    (lambda t: t.get_site_table(sort_by="vibes"), "unknown sort_by"),
    (lambda t: t.get_monthly_series(metric="vibes"), "unknown metric"),
    (lambda t: t.get_breakdown(by="astrology"), "unknown dimension"),
])
def test_invalid_requests_return_an_error_with_valid_options(tools, call, fragment):
    result = call(tools)
    assert fragment in result["error"].lower() and "valid_options" in result
    assert "metrics" not in result and "series" not in result and "rows" not in result
    assert tools.calls[-1]["error"]


def test_a_failing_tool_does_not_raise(ds, monkeypatch):
    broken = DataTools(ds, Scope("CUST001"), Scenario(), config.CFO_ROLE)
    monkeypatch.setattr(engine, "build_context", lambda *a, **k: 1 / 0)
    result = broken.get_kpis()
    assert "ZeroDivisionError" in result["error"] and broken.calls[-1]["error"]


# --- The SDK can use them ------------------------------------------------------------------------------------
def test_every_tool_becomes_a_valid_declaration(tools):
    declarations = {}
    for function in tools.functions:
        declaration = types.FunctionDeclaration.from_callable_with_api_option(callable=function, api_option="GEMINI_API")
        declarations[declaration.name] = declaration
    assert set(declarations) == {"list_options", "get_kpis", "get_site_table", "get_monthly_series", "get_breakdown",
                                 "get_signals", "get_reference"}
    kpis = declarations["get_kpis"].parameters.properties
    assert {"timeframe", "countries", "sites", "product_groups", "baseline"} <= set(kpis)
    assert "full_year" in declarations["get_kpis"].description


# --- The loop through Gemini ------------------------------------------------------------------------------------
def ask(ds, question, fake, role=config.CFO_ROLE, **kw):
    scope = Scope("CUST001")
    tools = DataTools(ds, scope, Scenario(), role)
    ctx = engine.build_context(ds, scope, Scenario(), role)
    return narrative.chat_answer(ctx, [], question, tools=tools, **kw), tools


def test_gemini_queries_the_data_and_answers_from_the_result(ds, fake_gemini):
    fake_gemini.call_tool("get_kpis", {"timeframe": "last_month", "countries": ["Finland"]})
    fake_gemini.reply("Finland saved 86,799 L of water and 10,073 kWh of energy in the last month [Estimated].")
    answer, tools = ask(ds, "Water and energy in Finland last month?", fake_gemini)

    declared = [d["name"] for d in fake_gemini.requests[0]["body"]["tools"][0]["functionDeclarations"]]
    assert declared == ["list_options", "get_kpis", "get_site_table", "get_monthly_series", "get_breakdown", "get_signals",
                        "get_reference"]
    assert len(fake_gemini.requests) == 2
    returned = fake_gemini.tool_results[0]
    assert returned["name"] == "get_kpis" and "86,799 L" in json.dumps(returned["response"])
    assert answer.text.startswith("Finland saved 86,799 L") and answer.untraced == []
    assert answer.queries == [{"tool": "get_kpis", "args": {"timeframe": "last_month", "countries": ["Finland"]},
                               "error": None}]
    assert answer.source.endswith("1 data query")
    assert "get_kpis" in fake_gemini.system or "call a tool" in fake_gemini.system


def test_numbers_not_in_the_tool_results_are_still_flagged(ds, fake_gemini):
    fake_gemini.call_tool("get_kpis", {"timeframe": "last_month"})
    fake_gemini.reply("We saved 124,100 L, which is 777,777 L more than last year.")
    answer, _ = ask(ds, "Compare with last year", fake_gemini)
    assert answer.untraced == ["777777"]


def test_gemini_can_recover_from_a_bad_name(ds, fake_gemini):
    fake_gemini.call_tool("get_kpis", {"sites": ["Atlantis"]})
    fake_gemini.reply("I could not find that site.")
    answer, tools = ask(ds, "How is Atlantis doing?", fake_gemini)
    response = json.dumps(fake_gemini.tool_results[0]["response"])
    assert "valid_options" in response and "NorthStar Oulu" in response
    assert answer.queries[0]["error"] and "Atlantis" in answer.queries[0]["error"]


def test_several_queries_in_one_answer(ds, fake_gemini):
    fake_gemini.call_tool("get_site_table", {"sort_by": "repairs", "top_n": 2})
    fake_gemini.call_tool("get_breakdown", {"by": "repair_type", "timeframe": "last_month"})
    fake_gemini.reply("Done.")
    answer, tools = ask(ds, "Which sites repair most, and what types?", fake_gemini)
    assert [q["tool"] for q in answer.queries] == ["get_site_table", "get_breakdown"] and len(fake_gemini.requests) == 3
    assert answer.source.endswith("2 data queries")


def test_endless_tool_use_is_stopped(ds, fake_gemini):
    for _ in range(llm.MAX_TOOL_CALLS + 4):
        fake_gemini.call_tool("list_options", {})
    answer, _ = ask(ds, "Loop forever", fake_gemini)
    assert answer.text == "" and "more than 8 data queries" in answer.source


def test_without_tools_the_request_has_none(ds, fake_gemini):
    ctx = engine.build_context(ds, Scope("CUST001"), Scenario(), config.CFO_ROLE)
    narrative.chat_answer(ctx, [], "Hi")
    assert "tools" not in fake_gemini.last["body"] and "call a tool" not in fake_gemini.system


def test_reference_tables_and_product_breakdown(tmp_path=None):
    import loader
    from engine import Scenario, Scope

    ds = loader.for_customer(loader.load_workbook(), "CUST001")
    tools = DataTools(ds, Scope("CUST001"), Scenario(), config.STANDARD_ROLE)
    sites = tools.get_reference("sites")
    assert sites["rows"] and all(r["customer_id"] == "CUST001" for r in sites["rows"])
    assert tools.get_reference("products")["rows"] and "error" in tools.get_reference("nonsense")
    rows = tools.get_breakdown("product")["rows"]
    assert rows and "Product" in rows[0]
