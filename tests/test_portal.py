"""The KAM portal in a real browser (Edge or Chrome), served by server.py, with a fake Gemini behind it.

Skipped automatically where no browser or Playwright is installed.
"""
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

playwright_sync = pytest.importorskip("playwright.sync_api")

import config
import engine
import loader
from engine import Scenario, Scope
from server import make_server

NAMES = ["Customer Success Scorecard", "Customer Experience", "Service Performance", "Service Adoption", "Operational Value",
         "Financial Value", "Sustainability Value", "Improvement", "Relationship / Future"]


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as p:
        launched = None
        for channel in ("msedge", "chrome"):
            try:
                launched = p.chromium.launch(channel=channel, headless=True)
                break
            except Exception:  # not installed
                continue
        if launched is None:
            pytest.skip("no Edge or Chrome available for the browser tests")
        yield launched
        launched.close()


@pytest.fixture
def server():
    srv = make_server("127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def offline(server, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return server


@pytest.fixture
def online(server, fake_gemini):
    return server, fake_gemini


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1500, "height": 950}, accept_downloads=True)
    page = context.new_page()
    page.errors, page.requests = [], []
    page.on("pageerror", lambda e: page.errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: page.errors.append(f"console: {m.text}") if m.type == "error" else None)
    page.on("request", lambda r: page.requests.append((r.url, r.post_data or "")))
    yield page
    context.close()


def open_account(page, base, cid="CUST001"):
    page.goto(base + "/index.html")
    page.click("#signin")
    page.click(f"[data-open='{cid}']")
    page.wait_for_selector("#evidence .ev")


def say(page, text):
    page.fill("#iIn", text)
    page.click("#iSend")


AREA_IDS = dict(zip(NAMES, ["scorecard", "experience", "service", "adoption", "operational", "financial", "sustainability",
                            "improvement", "relationship"]))


def area(page, name):
    return page.locator(f"#evidence .ev[data-area={AREA_IDS[name]}]")


def js(page, expression):
    return page.evaluate(expression)


# --- loads clean, offline -------------------------------------------------------------------------------------------
def test_portal_loads_without_errors_and_shows_the_nine_areas(offline, page):
    open_account(page, offline)
    assert [e.inner_text() for e in page.locator("#evidence .ev .hd b").all()] == NAMES
    assert "Offline rules" in page.locator("#insEngine").inner_text()
    assert js(page, "KAM.runSelfTests().filter(r => !r.ok)") == []
    scan = js(page, "KAM.scanInterface()")
    assert len(scan) == 22 and all(r["banned"] is None and r["tags"] > 0 and r["evidence"] == 9 for r in scan)
    assert not page.errors


def test_the_original_prototype_checks_still_pass(offline, page):
    open_account(page, offline)
    assert js(page, "Insights.runTests().filter(r => !r.ok).length") == 0


# --- the portal's numbers are the engine's numbers ----------------------------------------------------------------------
SCOPES = [("full year", 0, 11, "all", Scope), ("December", 11, 11, "all", Scope), ("Q3", 6, 8, "all", Scope),
          ("Oct to Dec at one site", 9, 11, "site:S004", Scope)]


@pytest.mark.parametrize("cid", ["CUST001", "CUST002", "CUST003"])
def test_figures_match_the_python_engine(offline, page, cid):
    open_account(page, offline, cid)
    full = loader.load_workbook()
    ds = loader.for_customer(full, cid)
    for label, f, t, sel, _ in SCOPES:
        site = sel.split(":")[1] if sel.startswith("site:") else None
        if site and not (ds.sites["site_id"] == site).any():
            continue
        scope = Scope(cid, site_ids=(site,) if site else (), start=f"2025-{f + 1:02d}", end=f"2025-{t + 1:02d}")
        py = engine.rollup(ds, scope, Scenario())
        got = js(page, f"""(() => {{ const a = compute('{cid}', {f}, {t}, '{sel}', 'B_LOCAL');
            return {{water: a.save.water.base, energy: a.save.energy.base, co2: a.save.co2.base, kg: a.flows.wkg, foot: a.flows.co2,
                     repairs: a.repairs, alloc: a.alloc, gap: a.unused, wearers: a.wearers, req: a.flows.req}}; }})()""")
        expected = {"water": py["water_saved"].value, "energy": py["energy_saved"].value, "co2": py["co2_saved"].value,
                    "kg": py["washed_kg"].value, "foot": py["footprint_co2"].value, "repairs": py["repairs"].value,
                    "alloc": py["allocated"].value, "gap": py["gap"].value, "wearers": py["wearers"].value,
                    "req": py["service_requests"].value}
        for key, value in expected.items():
            assert got[key] == pytest.approx(value, rel=1e-6, abs=1e-6), f"{cid} {label} {key}"


def test_signals_reconcile_to_the_python_hygiene_screen(offline, page):
    open_account(page, offline)
    ds = loader.for_customer(loader.load_workbook(), "CUST001")
    py = engine.hygiene_summary(ds, Scope("CUST001"))
    got = js(page, "KAM.signals(AGG()).hygieneTotals")
    assert got == {"traced": py["traced"], "flagged": py["flagged"], "alloc": py["allocated"]}


# --- information from the conversation becomes data (offline rules) ----------------------------------------------------------
def test_a_late_delivery_is_proposed_confirmed_and_updates_the_dashboard(offline, page):
    open_account(page, offline)
    before = js(page, "(() => { const a = compute('CUST001', 10, 10, 'site:S004', 'B_LOCAL'); return [a.cycles, a.late]; })()")
    say(page, "Oulu had 3 late deliveries in November")
    card = page.locator("#imsgs .m.inputs").last
    card.wait_for()
    assert "Add to the dataset?" in card.inner_text() and "3 late deliveries" in card.inner_text()
    assert js(page, "KAM.KI.list().length") == 0  # nothing changes before confirmation
    card.locator("[data-kin^='add']").click()
    page.wait_for_selector("#imsgs .m.inputs.added")
    after = js(page, "(() => { const a = compute('CUST001', 10, 10, 'site:S004', 'B_LOCAL'); return [a.cycles, a.late]; })()")
    assert after == [before[0] + 3, before[1] + 3]
    assert "+3 late trips" in area(page, "Service Performance").inner_text()
    assert "includes KAM input" in area(page, "Service Performance").inner_text()

    page.reload()  # stored in the browser
    page.click("#signin")
    page.click("[data-open='CUST001']")
    page.wait_for_selector("#evidence .ev")
    assert js(page, "KAM.KI.list().length") == 1
    assert js(page, "(() => { const a = compute('CUST001', 10, 10, 'site:S004', 'B_LOCAL'); return a.late; })()") == before[1] + 3

    js(page, "S.spec.tab = 'inputs'; render()")
    page.locator("[data-rm]").first.click()  # removing restores the original figures
    assert js(page, "(() => { const a = compute('CUST001', 10, 10, 'site:S004', 'B_LOCAL'); return [a.cycles, a.late]; })()") == before
    assert "+3 late trips" not in area(page, "Service Performance").inner_text()
    assert not page.errors


def test_reduced_risk_in_euros_updates_financial_value_and_signals(offline, page):
    open_account(page, offline)
    before = js(page, "KAM.signals(AGG()).hygieneTotals.flagged")
    say(page, "this food customer has a hygiene risk, add €25,000 of reduced risk")
    card = page.locator("#imsgs .m.inputs").last
    card.wait_for(timeout=10000)
    assert "€25,000" in card.inner_text() and js(page, "KAM.KI.list().length") == 0
    card.locator("[data-kin^='add']").click()
    page.wait_for_selector("#imsgs .m.inputs.added")
    text = area(page, "Financial Value").inner_text()
    assert "Risk reduced (KAM estimate)" in text and "€25,000" in text and "includes KAM input" in text
    js(page, "S.spec.tab = 'signals'; render()")
    assert "Risk reduction recorded by the KAM: €25,000" in page.locator("#panel").inner_text()
    assert js(page, "KAM.signals(AGG()).hygieneTotals.flagged") == before  # a stated value never rewrites the measured flag
    js(page, "S.spec.tab = 'inputs'; render()")
    page.click("[data-rm]")
    page.wait_for_function("!document.querySelector('#evidence .ev[data-area=financial] .kamchip')")
    assert "Risk reduced" not in area(page, "Financial Value").inner_text()


def test_gemini_can_record_a_risk_reduction(online, page):
    server, fake = online
    fake.call_tool("record_inputs", {"inputs": [{"kind": "risk_reduction", "value_eur": 25000, "title": "hygiene risk"}]})
    fake.reply("I've prepared the risk reduction for your confirmation.")
    open_account(page, server)
    say(page, "add 25 thousand euros of reduced hygiene risk")
    card = page.locator("#imsgs .m.inputs").last
    card.wait_for(timeout=20000)
    assert "Risk reduced: €25,000" in card.inner_text()


def test_survey_scores_drive_experience_and_the_scorecard(offline, page):
    open_account(page, offline)
    assert "No data yet" in area(page, "Customer Experience").inner_text()
    say(page, "CSAT from the December survey is 4.3 out of 5")
    page.locator("#imsgs .m.inputs [data-kin^='add']").last.click()
    experience = area(page, "Customer Experience")
    experience.wait_for()
    page.wait_for_function("document.querySelector('#evidence .ev[data-area=experience] .rag').innerText === 'Green'")
    assert "CSAT 4.3/5" in experience.inner_text() and "[Estimated] KAM input" in experience.inner_text()
    say(page, "NPS is -12")
    page.locator("#imsgs .m.inputs [data-kin^='add']").last.click()
    page.wait_for_function("document.querySelector('#evidence .ev[data-area=experience] .rag').innerText === 'Red'")
    assert js(page, "KAM.evidenceAreas(AGG(), null).find(a => a.id === 'scorecard').status") == "r"


def test_contract_value_unlocks_the_financial_ratio(offline, page):
    open_account(page, offline)
    say(page, "Contract value is €2.4m a year, last year it was €2.3m")
    page.locator("#imsgs .m.inputs [data-kin^='add']").last.click()
    page.wait_for_function("!document.querySelector('#evidence .ev[data-area=financial] .need')")
    text = area(page, "Financial Value").inner_text()
    assert "Contract value" in text and "€2,400,000 a year" in text and "Avoided purchases vs contract spend" in text and "13.8–27.5%" in text
    js(page, "S.spec.tab = 'evidence'; render()")  # the full set of lines is in the Evidence areas tab
    assert "Cost development vs previous year: +4.3%" in " ".join(page.locator("table.evtab").inner_text().split())


def test_records_the_data_cannot_hold_are_refused_with_a_reason(offline, page):
    open_account(page, offline)
    for text, reason in (("Oulu had 3 late deliveries in January 2026", "outside it"),
                         ("Atlantis had 2 complaints in March", "Which site"),  # an unknown name is simply not a site in the text
                         ("2 complaints in March", "Which site")):
        say(page, text)
        card = page.locator("#imsgs .m.inputs").last
        page.wait_for_function("document.querySelectorAll('#imsgs .m.inputs').length > 0")
        card.wait_for()
        assert "I couldn't add that" in card.inner_text() and reason in card.inner_text(), text
        assert card.locator("[data-kin^='add']").count() == 0
    assert js(page, "KAM.KI.list().length") == 0


def test_discarding_changes_nothing(offline, page):
    open_account(page, offline)
    say(page, "Action: review jacket allocation at Helsinki, owner Anna, by 28 Feb")
    card = page.locator("#imsgs .m.inputs").last
    card.wait_for()
    assert "owner Anna" in card.inner_text() and "28 Feb 2026" in card.inner_text()
    card.locator("[data-kin^='no']").click()
    page.wait_for_selector("#imsgs .m.inputs.off")
    assert js(page, "KAM.KI.list().length") == 0


# --- the Gemini assistant ------------------------------------------------------------------------------------------------------
def test_gemini_changes_the_view_and_answers_from_tool_results(online, page):
    server, fake = online
    ds = loader.for_customer(loader.load_workbook(), "CUST001")
    energy = engine.rollup(ds, Scope("CUST001", start="2025-12", end="2025-12"), Scenario())["energy_saved"].value
    fake.call_tool("set_view", {"months_from": 12, "months_to": 12, "highlight": "energy_saving"})
    fake.call_tool("get_metric", {"metric": "energy_saving", "months_from": 12, "months_to": 12})
    fake.reply(f"Last month you saved {energy:,.0f} kWh [Estimated] against local laundry.")
    open_account(page, server)
    assert "gemini-2.5-flash" in page.locator("#insEngine").inner_text()
    say(page, "How much kWh did we save last month?")
    reply = page.locator("#imsgs .m.ai").last
    reply.wait_for(timeout=20000)
    assert f"{energy:,.0f} kWh" in reply.inner_text() and "Figures not found" not in reply.inner_text()
    assert js(page, "[S.spec.from, S.spec.to, S.spec.highlight]") == [11, 11, "energy_saving"]
    assert "Data queried (2)" in page.locator("#imsgs details summary").last.inner_text()
    assert page.locator("#periodline").inner_text().startswith("Showing Dec 2025")

    system = fake.requests[0]["body"]["systemInstruction"]["parts"][0]["text"]
    assert "Allocated garments with no scan activity" in system and "[TEAM_ASSUMPTION]" in system and "idle or unused" in system
    names = [d["name"] for d in fake.requests[0]["body"]["tools"][0]["functionDeclarations"]]
    assert names == ["set_view", "get_metric", "rank_sites", "get_evidence_areas", "get_signals", "get_crm", "record_inputs"]
    result = [r for r in fake.tool_results if r["name"] == "get_metric"][0]["response"]
    assert result["period"] == "Dec 2025" and result["provenance"] == "[Estimated]" and result["raw"] == pytest.approx(energy, rel=1e-6)
    assert not page.errors


def test_an_invented_figure_in_an_answer_is_flagged(online, page):
    server, fake = online
    fake.call_tool("get_metric", {"metric": "water_saving"})
    fake.reply("You saved 1.41 M L, which is 777,777 L more than last year.")
    open_account(page, server)
    say(page, "How much water did we save?")
    reply = page.locator("#imsgs .m.ai").last
    reply.wait_for(timeout=20000)
    assert "Figures not found in the calculation layer" in reply.inner_text() and "777,777" in reply.inner_text()


def test_gemini_proposes_new_information_and_the_kam_confirms(online, page):
    server, fake = online
    fake.call_tool("record_inputs", {"inputs": [{"kind": "feedback", "metric": "csat", "value": 4.3, "scale": 5, "month": "December"},
                                                  {"kind": "incident", "site": "Oulu", "month": "2025-11", "count": 2, "type": "complaint"}]})
    fake.reply("I've prepared the survey score and the complaints for your confirmation.")
    open_account(page, server)
    base = js(page, "compute('CUST001', 10, 10, 'site:S004').flows.req")
    say(page, "The client said the December CSAT was 4.3 and Oulu had two complaints in November")
    card = page.locator("#imsgs .m.inputs").last
    card.wait_for(timeout=20000)
    assert "CSAT 4.3 / 5" in card.inner_text() and "2 complaints at Oulu in Nov 2025" in card.inner_text()
    assert js(page, "KAM.KI.list().length") == 0
    reply = [r for r in fake.tool_results if r["name"] == "record_inputs"][0]["response"]
    assert reply["status"].startswith("proposed to the KAM") and len(reply["accepted"]) == 2
    card.locator("[data-kin^='add']").click()
    page.wait_for_selector("#imsgs .m.inputs.added")
    assert js(page, "compute('CUST001', 10, 10, 'site:S004').flows.req") == base + 2
    assert js(page, "KAM.KI.list().map(r => r.via)") == ["chat", "chat"]


def test_a_bad_record_from_the_model_is_rejected_before_it_reaches_the_kam(online, page):
    server, fake = online
    fake.call_tool("record_inputs", {"inputs": [{"kind": "incident", "site": "Atlantis", "month": "2026-02", "count": 5}]})
    fake.reply("I could not record that.")
    open_account(page, server)
    say(page, "Atlantis had five incidents")
    page.locator("#imsgs .m.ai").last.wait_for(timeout=20000)
    reply = [r for r in fake.tool_results if r["name"] == "record_inputs"][0]["response"]
    assert reply["status"] == "nothing proposed" and reply["rejected"]
    assert page.locator("#imsgs .m.inputs").count() == 0


def test_when_gemini_fails_the_offline_rules_take_over(online, page):
    server, fake = online
    fake.fail(500)
    open_account(page, server)
    say(page, "Show Q3 compared with the previous quarter")
    page.wait_for_function("document.querySelector('#imsgs').innerText.includes('Using the offline rules')", timeout=20000)
    assert "AI unavailable (Gemini API error 500" in page.locator("#imsgs").inner_text()
    page.wait_for_function("S.spec.from === 6 && S.spec.to === 8 && S.spec.compare === true")


def test_the_api_key_never_reaches_the_browser(online, page):
    server, fake = online
    fake.reply("Hello.")
    open_account(page, server)
    say(page, "Hello")
    page.locator("#imsgs .m.ai").last.wait_for(timeout=20000)
    assert "test-key" not in page.content()
    assert all("test-key" not in url and "test-key" not in body for url, body in page.requests)
    assert all(url.startswith(server) or url.startswith("data:") for url, _ in page.requests)  # nothing goes to Google directly


# --- layout and the tabs -----------------------------------------------------------------------------------------------------------
def test_the_assistant_panel_sits_on_the_right_and_collapses(offline, page):
    open_account(page, offline)
    main, panel = page.locator(".main").bounding_box(), page.locator("#ins").bounding_box()
    assert panel["x"] >= main["x"] + main["width"] and panel["width"] >= 380 and panel["height"] >= 460
    assert page.locator("#iIn").is_visible() and page.locator("#iSend").is_visible()
    page.click("#insClose")
    assert not page.locator("#ins").is_visible() and page.locator("#insFab").is_visible()
    page.click("#insFab", force=True)  # the button bobs continuously, so it is never "stable"
    assert page.locator("#ins").is_visible()


def test_the_new_tabs_show_real_content(offline, page):
    open_account(page, offline)
    page.click(".tab[data-t=evidence]")
    assert page.locator("table.evtab tbody tr").count() == 9
    page.click(".tab[data-t=signals]")
    text = page.locator("#panel").inner_text()
    assert "Optimization signal for investigation, not a conclusion" in text and "249 of 698" in text and "Low-confidence screen" in text
    assert "Allocated garments with no scan activity" in text
    page.click(".tab[data-t=whatif]")
    base_water = js(page, "KAM.whatIf(AGG()).water")
    page.eval_on_selector("#wiScale", "e => { e.value = '1.2'; e.dispatchEvent(new Event('change')); }")
    page.wait_for_function("document.querySelector('#wiScaleV').innerText.startsWith('1.20')")
    kg = js(page, "AGG().flows.wkg")
    ds = loader.for_customer(loader.load_workbook(), "CUST001")
    used = 9.5 * kg - engine.rollup(ds, Scope("CUST001"), Scenario())["water_saved"].value
    assert js(page, "KAM.whatIf(AGG()).water") == pytest.approx(1.2 * 9.5 * kg - used, rel=1e-6) and base_water != js(page, "KAM.whatIf(AGG()).water")
    page.click(".tab[data-t=inputs]")
    assert "Nothing added yet" in page.locator("#panel").inner_text()
    page.click(".tab[data-t=method]")
    assert "how status is set" in page.locator("#panel").inner_text() and "KAM extension checks" in page.locator("#panel").inner_text()
    assert "pass" in page.locator("#kamTestBox").inner_text()
    assert not page.errors


def test_client_view_hides_what_is_only_for_the_kam(offline, page):
    open_account(page, offline)
    assert page.locator("#evidence .need").first.is_visible()
    page.click("#mClient")
    assert not page.locator("#evidence .need").first.is_visible() and not page.locator("#crm").is_visible()
    assert page.locator("#evidence .ev").count() == 9


def test_the_powerpoint_export_carries_the_evidence_areas(offline, page, tmp_path):
    from pptx import Presentation

    open_account(page, offline)
    say(page, "CSAT is 4.1 out of 5")
    page.locator("#imsgs .m.inputs [data-kin^='add']").last.click()
    js(page, "S.spec.tab = 'actions'; render()")
    with page.expect_download(timeout=30000) as info:
        page.click("#pptx")
    target = tmp_path / "review.pptx"
    info.value.save_as(target)
    text = " ".join(shape.text_frame.text for slide in Presentation(target).slides for shape in slide.shapes if shape.has_text_frame)
    assert "Evidence areas" in text and "Customer Experience" in text and "Information added by the KAM" not in text or "CSAT" in text
    assert "[Estimated] KAM input" in text and "Signals to investigate" in text and "[TEAM_ASSUMPTION]" in text
    assert not any(term in text.lower() for term in (*config.BANNED_TERMS, "idle", "unused"))


def test_the_self_checks_really_fail_when_something_is_broken(offline, page):
    open_account(page, offline)
    assert js(page, "KAM.runSelfTests().filter(r => !r.ok).length") == 0
    broken = js(page, """(() => { const orig = KAM.parseInputsOffline; KAM.parseInputsOffline = () => [];
        try { return KAM.runSelfTests().filter(r => !r.ok).map(r => r.name); } finally { KAM.parseInputsOffline = orig; } })()""")
    assert len(broken) >= 5 and all(name.startswith("Offline:") for name in broken if "questions" not in name and "view request" not in name)
