"""The KAM flow end to end in the real app: login, the minimal overview, the Insights assistant changing the view
through validated parameters, and the isolated focused view it opens."""
import json
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import engine
import loader
from engine import Scenario, Scope

APP = str(Path(__file__).resolve().parents[1] / "app.py")
RETURN = "← Return to Overview"
QUESTION = "Show me how much water and energy we saved in the last month instead of the full year."


@pytest.fixture(scope="module")
def full():
    return loader.load_workbook()


def period(app) -> tuple[str, str]:
    return app.session_state.period_start, app.session_state.period_end


def page_text(app) -> str:
    return " ".join(m.value for m in app.markdown)


def button(app, label, key=None):
    return next(b for b in app.button if b.label == label and (key is None or b.key == key))


def open_client(customer="CUST001") -> AppTest:
    app = AppTest.from_file(APP, default_timeout=180).run()
    app.selectbox(key="login_client").select(customer)
    button(app, "Open Client Dashboard").click().run()
    assert not app.exception
    return app


def december(full, role=config.CFO_ROLE):
    client = loader.for_customer(full, "CUST001")
    return engine.build_context(client, Scope("CUST001", start="2025-12", end="2025-12"), Scenario(), role)


# --- Step 1: login ---------------------------------------------------------------------
def test_login_page():
    app = AppTest.from_file(APP, default_timeout=180).run()
    assert not app.exception
    assert app.title[0].value == "Welcome back"
    select = app.selectbox(key="login_client")
    assert select.label == "Assigned client"
    assert set(select.options) == {"NorthStar Foods Group", "Apex Industrial Systems", "Mediora Health Network"}
    assert [b.label for b in app.button] == ["Open Client Dashboard"]
    assert "Decision Support System" in page_text(app)


# --- Step 2: dashboard -------------------------------------------------------------------
def test_dashboard_shows_the_four_kpis_with_provenance(full):
    app = open_client("CUST001")
    html = page_text(app)
    assert app.session_state.page == "dashboard" and app.session_state.customer == "CUST001"
    assert "View: Full Year (Default)" in html
    for label in ("Garments allocated", "Carbon footprint of the service (Scope 3 view)", "Water saved vs",
                  "Active risk signals", config.GAP_METRIC):
        assert label in html, label
    for tag in ("[Measured]", "[Calculated]", "[Estimated]", "[TEAM_ASSUMPTION]"):
        assert tag in html, tag
    ctx = engine.build_context(loader.for_customer(full, "CUST001"), Scope("CUST001", start="2025-01", end="2025-12"),
                               Scenario(), config.QUALITY_ROLE)
    for key in config.KPI_METRICS:
        assert ctx.metrics[key].text in html, key
    assert "9,607" in html and "28,367" not in html
    assert not any(term in html.lower() for term in (*config.BANNED_TERMS, "idle"))


def test_only_the_selected_clients_data_is_opened():
    app = open_client("CUST002")
    sites = app.multiselect(key="site_ids")
    assert sites.options and all(name.startswith("Apex") for name in sites.options)
    assert "Apex Industrial Systems" in page_text(app) and "NorthStar" not in page_text(app)


def test_manual_filters_change_the_banner():
    app = open_client("CUST001")
    app.multiselect(key="countries").select("Finland").run()
    assert not app.exception
    html = page_text(app)
    assert "(manual filters)" in html and "Finland" in html and "View: Full Year (Default)" not in html


# --- Step 3: Insights -----------------------------------------------------------------------
def test_insights_recalculates_the_dashboard_from_gemini_parameters(fake_gemini, full):
    app = open_client("CUST001")
    assert [b.label for b in app.button if b.label.startswith("Show me")] == [QUESTION]

    fake_gemini.reply_json({"change_view": True, "timeframe": "last_month", "focus": "cfo"})
    fake_gemini.reply("Talking points: water and energy savings for the month [Estimated].")
    app.chat_input[0].set_value(QUESTION).run()
    assert not app.exception

    expected = december(full)
    state = app.session_state
    assert state.ai_label.startswith("Last month (2025-12)")
    assert period(app) == ("2025-12", "2025-12") and state.role == config.CFO_ROLE
    html = page_text(app)
    assert "AI Custom Filter: Last month (2025-12)" in html and "View: Full Year (Default)" not in html
    assert expected.metrics["water_saved"].text in html and expected.metrics["energy_saved"].text in html

    # Two Gemini calls: parameter extraction, then talking points written from the recalculated numbers.
    extraction, talking = fake_gemini.requests
    assert extraction["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert "responseMimeType" not in talking["body"]["generationConfig"]
    system = talking["body"]["systemInstruction"]["parts"][0]["text"]
    assert "The dashboard was just changed" in system and "Last month (2025-12)" in system
    assert expected.metrics["water_saved"].text in system and expected.metrics["energy_saved"].text in system
    assert config.GAP_METRIC in system and "[TEAM_ASSUMPTION]" in system

    assert [m.name for m in app.chat_message] == ["user", "assistant"]
    assert "Talking points" in app.chat_message[1].markdown[0].value
    assert any("Dashboard updated: Last month (2025-12)" in i.value for i in app.info)

    # The overview follows the same context, including its graphics.
    button(app, RETURN).click().run()
    html = page_text(app)
    assert "AI Custom Filter: Last month (2025-12)" in html and expected.metrics["water_saved"].text in html
    assert expected.metrics["allocated"].text in html and expected.metrics["footprint_co2"].text in html

    # Reset returns to the default view.
    button(app, "Reset to default view").click().run()
    assert app.session_state.ai_label == "" and period(app) == ("2025-01", "2025-12")
    assert app.session_state.role == config.QUALITY_ROLE
    assert "View: Full Year (Default)" in page_text(app)


def test_a_plain_question_leaves_the_view_alone(fake_gemini):
    app = open_client("CUST001")
    fake_gemini.reply_json({"change_view": False})
    fake_gemini.reply("The hygiene flag is a low-confidence screen [Calculated].")
    app.chat_input[0].set_value("How reliable is the hygiene flag?").run()
    assert not app.exception and app.session_state.ai_label == ""
    assert "View: Full Year (Default)" in page_text(app) and not app.info
    assert len(app.chat_message) == 2
    assert app.session_state.focus["kind"] == "hygiene"


def test_insights_still_works_without_a_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    app = open_client("CUST001")
    app.chat_input[0].set_value("Покажи данные за прошлый месяц").run()
    assert not app.exception
    assert app.session_state.ai_label.startswith("Last month") and period(app) == ("2025-12", "2025-12")
    reply = app.chat_message[1].markdown[0].value
    assert "needs a Gemini API key" in reply and "GEMINI_API_KEY" in reply
    assert "Meanwhile, the figures for this view" in reply and "Water saved vs" in reply and "[Estimated]" in reply
    assert "Rule-based talking points" in reply
    assert any("keyword rules" in i.value for i in app.info)
    assert "AI Custom Filter: Last month (2025-12)" in page_text(app)


def test_data_limits_are_explained_not_hidden(fake_gemini):
    app = open_client("CUST001")
    fake_gemini.reply_json({"change_view": True, "timeframe": "last_30_days"})
    fake_gemini.reply("Latest month figures [Estimated].")
    app.chat_input[0].set_value("last 30 days please").run()
    assert "AI Custom Filter: Last 30 days (2025-12)" in page_text(app)
    assert "Data is monthly" in page_text(app)


def test_a_manual_change_after_an_ai_filter_drops_the_ai_label(fake_gemini):
    app = open_client("CUST001")
    fake_gemini.reply_json({"change_view": True, "timeframe": "last_month"})
    app.chat_input[0].set_value("last month").run()
    assert "AI Custom Filter: Last month (2025-12)" in page_text(app)

    app.multiselect(key="countries").select("Sweden").run()
    html = page_text(app)
    assert "AI Custom Filter" not in html and "(manual filters)" in html


def test_a_corrupted_period_falls_back_to_the_full_range():
    app = AppTest.from_file(APP, default_timeout=180)
    app.session_state.page, app.session_state.customer = "dashboard", "CUST001"
    app.session_state.period_start = "not-a-month"  # stale state, set before the first run
    app.session_state.period_end = "2031-01"
    app.run()
    assert not app.exception and period(app) == ("2025-01", "2025-12")


def test_period_selectors_drive_the_dashboard_and_survive_view_changes(full):
    app = open_client("CUST001")
    app.selectbox(key="period_start").select("2025-04")
    app.selectbox(key="period_end").select("2025-06").run()
    assert not app.exception and period(app) == ("2025-04", "2025-06")
    html = page_text(app)
    assert "View: 2025-04 to 2025-06 (manual filters)" in html
    expected = engine.build_context(loader.for_customer(full, "CUST001"),
                                    Scope("CUST001", start="2025-04", end="2025-06"), Scenario(), config.QUALITY_ROLE)
    assert expected.metrics["water_saved"].text in html

    # Opening and leaving a focused view must not reset the period, although the overview's controls disappear.
    button(app, "↗", "open_allocated").click().run()
    assert not app.exception and app.session_state.focus["kind"] == "fleet" and period(app) == ("2025-04", "2025-06")
    button(app, RETURN).click().run()
    assert not app.exception and period(app) == ("2025-04", "2025-06") and "2025-04 to 2025-06" in page_text(app)


def test_reversed_months_are_put_in_order():
    app = open_client("CUST001")
    app.selectbox(key="period_start").select("2025-09")
    app.selectbox(key="period_end").select("2025-02").run()
    assert not app.exception and "2025-02 to 2025-09" in page_text(app)


def test_the_assistant_answers_from_a_data_query(fake_gemini, full):
    app = open_client("CUST001")
    fake_gemini.reply_json({"change_view": False})
    fake_gemini.call_tool("get_breakdown", {"by": "repair_type", "timeframe": "last_month", "sites": ["Oulu"]})
    fake_gemini.reply("Oulu's most common repair last month was other seam stitching, 7 repairs [Measured].")
    app.chat_input[0].set_value("Which repair type was most common in Oulu last month?").run()
    assert not app.exception

    reply = app.chat_message[1].markdown[0].value
    assert "other seam stitching" in reply
    assert [e.label for e in app.expander if e.label.startswith("Data queried")] == ["Data queried (1)"]
    assert app.session_state.ai_label == ""  # answering a question does not change the dashboard
    returned = json.dumps(fake_gemini.tool_results[0]["response"])
    assert "OTHER SEAM STITCHING" in returned and "NorthStar Oulu" in returned


# --- Progressive disclosure -------------------------------------------------------------
def overview_is_showing(app) -> bool:
    return any(e.label == "Savings by month" for e in app.expander)


def test_the_overview_keeps_detail_in_collapsed_accordions():
    app = open_client("CUST001")
    assert overview_is_showing(app) and not app.session_state.focus
    assert all(not e.proto.expanded for e in app.expander)
    assert not any(b.label == RETURN for b in app.button)
    labels = [e.label for e in app.expander]
    for label in ("Savings by month", "Service and operations", "Drill-down", "How to read these figures"):
        assert label in labels, label


def test_the_method_tab_is_gone_but_its_thresholds_moved_next_to_the_figures(fake_gemini):
    app = open_client("CUST001")
    assert not any(e.label.startswith("Data") for e in app.expander)
    button(app, "↗", "open_risk_signals").click().run()
    fake_gemini.reply_json({"change_view": False, "topic": "fleet"})
    fake_gemini.reply("Fine.")
    app.chat_input[0].set_value("Which sites are outliers?").run()
    captions = " ".join(c.value for c in app.caption)
    assert config.FLEET_RATIONALE in captions


# --- Focused view ------------------------------------------------------------------------
HYGIENE = "What are the hygiene and food safety risks?"
PRICING = "What-if pricing increases by 5%?"


def ask(app, question):
    app.chat_input[0].set_value(question).run()
    assert not app.exception
    return app


def test_a_question_replaces_the_overview_with_a_focused_view(monkeypatch, full):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    app = ask(open_client("CUST001"), HYGIENE)
    assert not overview_is_showing(app) and app.session_state.focus["kind"] == "hygiene"
    html = page_text(app)
    assert "Hygiene and food safety risk" in html and "Risk and impact indicators" in html and "Low-confidence screen" in html
    assert button(app, RETURN)
    assert app.get("plotly_chart"), "a simple visual is shown"

    ctx = engine.build_context(loader.for_customer(full, "CUST001"), Scope("CUST001", start="2025-01", end="2025-12"),
                               Scenario(), config.QUALITY_ROLE)
    assert f"{ctx.hygiene['share']:.1f}%" in html

    button(app, RETURN).click().run()
    assert not app.exception and overview_is_showing(app) and app.session_state.focus is None
    assert len(app.chat_message) == 2  # the conversation stays


def test_ai_insights_show_when_their_figures_trace_to_the_data(fake_gemini, full):
    app = open_client("CUST001")
    ctx = engine.build_context(loader.for_customer(full, "CUST001"), Scope("CUST001", start="2025-01", end="2025-12"),
                               Scenario(), config.QUALITY_ROLE)
    fake_gemini.reply_json({"change_view": False, "topic": "hygiene"})
    fake_gemini.reply(f"- {ctx.hygiene['flagged']:,} garments flagged [Calculated].\n- Low-confidence screen, confirm first.")
    ask(app, "Are we exposed on contamination?")  # no keyword rule needed: the assistant's topic picks the view
    focus_state = app.session_state.focus
    assert focus_state["kind"] == "hygiene" and len(focus_state["insights"]) == 2
    assert "figures checked" in focus_state["source"]
    assert any(m.value == "- Low-confidence screen, confirm first." for m in app.markdown)
    assert "exactly 2 or 3 bullet points" in fake_gemini.requests[-1]["body"]["systemInstruction"]["parts"][0]["text"]


def test_an_unchecked_figure_never_reaches_the_canvas(fake_gemini):
    app = open_client("CUST001")
    fake_gemini.reply_json({"change_view": False, "topic": "hygiene"})
    fake_gemini.reply("- 987,654 garments failed [Calculated].\n- Another 123,456 are at risk [Calculated].")
    ask(app, HYGIENE)
    assert app.session_state.focus["insights"] is None
    assert not any(m.value == "- 987,654 garments failed [Calculated]." for m in app.markdown)
    assert any("987654" in w.value for w in app.warning)  # the chat still says it could not be traced


def test_pricing_needs_a_contract_value_and_never_invents_one(monkeypatch, full):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    app = ask(open_client("CUST001"), PRICING)
    html = page_text(app)
    assert "What-if: pricing +5%" in html and "Needs contract value" in html
    assert "Full TCO cannot be stated" in html
    assert app.slider[0].value == 5.0

    ctx = engine.build_context(loader.for_customer(full, "CUST001"), Scope("CUST001", start="2025-01", end="2025-12"),
                               Scenario(), config.QUALITY_ROLE)
    assert engine.format_value(ctx.metrics["avoided_eur"].value * 1.05, "EUR") in html

    app.number_input[0].set_value(420000.0).run()
    assert not app.exception
    html = page_text(app)
    assert "+€21,000" in html and "Needs contract value" not in html

    app.slider[0].set_value(10.0).run()
    assert "+€42,000" in page_text(app) and "What-if: pricing +10%" in page_text(app)


def test_changing_a_what_if_drops_ai_insights_written_for_the_old_one(fake_gemini):
    app = open_client("CUST001")
    fake_gemini.reply_json({"change_view": False, "topic": "pricing"})
    fake_gemini.reply("- Prices move 5% in this what-if [Estimated].\n- Check the contract [Estimated].")
    ask(app, PRICING)
    bullet = "- Prices move 5% in this what-if [Estimated]."
    assert any(m.value == bullet for m in app.markdown)
    app.slider[0].set_value(10.0).run()
    assert not any(m.value == bullet for m in app.markdown)


def test_a_reset_request_goes_back_to_the_overview(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    app = ask(open_client("CUST001"), "Show the last month")
    assert app.session_state.focus and period(app) == ("2025-12", "2025-12")
    ask(app, "reset to the default view")
    assert app.session_state.focus is None and overview_is_showing(app) and period(app) == ("2025-01", "2025-12")


def test_filters_keep_working_inside_a_focused_view(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    app = ask(open_client("CUST001"), HYGIENE)
    app.multiselect(key="countries").select("Finland").run()
    assert not app.exception and app.session_state.focus["kind"] == "hygiene" and "Finland" in page_text(app)


def test_a_client_without_hygiene_garments_gets_a_plain_answer(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    app = ask(open_client("CUST002"), HYGIENE)
    assert "does not apply" in page_text(app) and not any(b.label == "Open Client Dashboard" for b in app.button)


def test_switching_client_closes_the_focused_view(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    app = ask(open_client("CUST001"), PRICING)
    app.number_input[0].set_value(420000.0).run()
    button(app, "Switch client").click().run()
    app.selectbox(key="login_client").select("CUST003")
    button(app, "Open Client Dashboard").click().run()
    state = app.session_state
    assert not app.exception and state.focus is None and state.contract_value == 0.0 and not state.chat
