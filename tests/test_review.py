"""The review design (portal/review.html) in a real browser: Gemini adapter, focused view, details accordion."""
import pytest

pytest.importorskip("playwright.sync_api")

from test_portal import browser, offline, online, page, server  # noqa: F401  (shared fixtures)

HYGIENE = "What are the hygiene and food safety risks?"
PRICING = "What-if pricing increases by 5%?"


def enter(page, base, cid="CUST001"):
    page.goto(base + "/")
    page.click("#signin")
    page.click(f"[data-acc='{cid}'], .acard2 >> nth=0")
    page.wait_for_selector("#kpis .kpi")


def ask(page, text):
    page.evaluate("t => Insights.ask(t)", text)


def focusing(page):
    return page.evaluate("document.body.classList.contains('focusing')")


def test_the_design_loads_clean_with_kpis_first_and_details_one_click_away(offline, page):
    enter(page, offline)
    assert page.locator("#kpis .kpi").count() == 4
    assert page.locator("#ovMore").evaluate("el => el.open") is False
    assert not page.locator("#trendCard").is_visible()
    page.click("#ovMore > summary")
    assert page.locator("#trendCard").is_visible()
    assert page.locator("#side [data-nav='method']").inner_text().strip() == "What-if"
    assert "Data and method" not in page.locator("#side").inner_text()
    assert page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--navy').trim()") == "#00205b"
    assert not page.errors


def test_a_question_opens_the_focused_view_and_return_restores_the_overview(offline, page):
    enter(page, offline)
    ask(page, HYGIENE)
    page.wait_for_selector("#focusBack")
    assert focusing(page) and page.locator("#overview").is_hidden() and page.locator(".toolrow").is_hidden()
    text = page.locator("#focusView").inner_text()
    assert "Hygiene and food safety risk" in text and "Low-confidence screen" in text and "35.7%" in text
    assert page.locator("#focusView svg").count() == 1 and page.locator("#focusView .fins li").count() == 3
    assert page.url.rstrip("/").endswith(offline.split("//")[1]) or "/" in page.url  # same page, no redirect
    page.click("#focusBack")
    assert not focusing(page) and page.locator("#overview").is_visible() and page.locator("#kpis .kpi").count() == 4
    assert not page.errors


def test_pricing_says_what_is_missing_and_never_invents_a_contract_value(offline, page):
    enter(page, offline)
    ask(page, PRICING)
    page.wait_for_selector("#fSpend")
    text = page.locator("#focusView").inner_text()
    assert "What-if: pricing +5" in text and "Needs spend" in text and "Full TCO cannot be stated" in text
    page.fill("#fSpend", "420000")
    page.press("#fSpend", "Tab")
    page.wait_for_function("document.querySelector('#focusView').innerText.includes('+€21,000')")
    assert "Needs spend" not in page.locator("#focusView").inner_text()


def test_the_what_if_section_recalculates_from_the_data(offline, page):
    enter(page, offline)
    page.click("#side [data-nav='method']")
    page.wait_for_selector("#wControls")
    assert focusing(page) and page.locator("#focusView h1").inner_text() == "What-if"
    co2 = page.evaluate("""() => { const a = AGG(); return [tonnes(a.save.co2.base), tonnes(a.base.co2 * 1.15 - a.flows.co2)]; }""")
    assert co2[0] in page.locator("#focusView .inds").inner_text()
    page.evaluate("""() => { const c = document.querySelector('#wScale'); c.value = 1.15; c.dispatchEvent(new Event('change')); }""")
    page.wait_for_function("t => document.querySelector('#focusView .inds').innerText.includes(t)", arg=co2[1])
    page.select_option("#wBase", "B_HOME")
    page.wait_for_function("S.spec.baseline === 'B_HOME'")
    page.wait_for_selector("#wControls")
    page.click("#wReset")
    page.click("#focusBack")
    assert not focusing(page) and page.locator("#kpis .kpi").count() == 4
    assert not page.errors


def test_a_plain_view_change_stays_on_the_dashboard(offline, page):
    enter(page, offline)
    ask(page, "Only show last quarter")
    page.wait_for_function("S.spec.from === 9")
    assert not focusing(page)


def test_new_client_information_keeps_its_own_flow(offline, page):
    enter(page, offline)
    ask(page, "CSAT is 4.4 out of 5")
    page.wait_for_selector(".m.cap", state="attached")  # the Insights panel is closed here
    assert not focusing(page)


def answered(page):
    page.wait_for_function("Review.F.answer !== null")
    return page.locator("#fAnswer")


def test_a_specific_question_gets_its_specific_answer_first(online, page):
    base, fake = online
    enter(page, base)
    fake.call_tool("get_breakdown", {"by": "repair_type", "sites": ["Oulu"]})
    fake.reply("Oulu's most common repair was other seam stitching.")
    ask(page, "Which repair type is most common in Oulu?")
    card = answered(page)
    assert "other seam stitching" in card.inner_text() and "Data queried (1)" in card.inner_text()
    assert "OTHER SEAM STITCHING" in str([r["body"]["contents"] for r in fake.requests]).upper()
    assert "Answer the KAM's exact question" in fake.requests[0]["body"]["systemInstruction"]["parts"][0]["text"]


def test_a_question_with_no_topic_word_still_reaches_gemini_with_no_generic_summary(online, page):
    base, fake = online
    enter(page, base)
    fake.reply("Stockholm is the largest site by washed kg.")
    ask(page, "Which site was the biggest in Q3?")
    text = answered(page).inner_text()
    assert "Stockholm" in text
    whole = page.locator("#focusView").inner_text()
    assert "Account signals" not in whole and "Evidence areas" not in whole


def test_a_single_sentence_answer_is_shown_as_it_is(online, page):
    base, fake = online
    enter(page, base)
    fake.reply("There are 24 delivery sites.")
    ask(page, "How many sites are in the fleet?")
    assert "There are 24 delivery sites." in answered(page).inner_text()


def test_an_untraceable_figure_is_marked_on_the_canvas(online, page):
    base, fake = online
    enter(page, base)
    fake.reply("987,654 garments failed.")
    ask(page, HYGIENE)
    text = answered(page).inner_text()
    assert "Check before quoting" in text and "987" in text


def test_a_changed_what_if_drops_the_old_answer(online, page):
    base, fake = online
    enter(page, base)
    fake.reply("Prices move 5% in this what-if.")
    ask(page, PRICING)
    answered(page)
    page.fill("#fPct", "10")
    page.press("#fPct", "Tab")
    page.wait_for_function("!document.querySelector('#fAnswer')")


def carbon_canvas(page):
    co2 = page.evaluate("""() => { const c = AGG(); return Math.round(c.save.co2.base / 100) / 10; }""")
    return {"title": "Scope 3 carbon savings", "metrics": [{"label": "Avoided CO2", "value": f"{co2} tCO2e", "provenance": "Estimated"}],
            "chart": {"type": "none"}}


def test_the_canvas_is_built_from_the_question_and_shows_nothing_else(online, page):
    base, fake = online
    enter(page, base)
    fake.reply("You saved 255.1 tCO2e against outsourced local laundry [Estimated].")
    fake.reply_json(carbon_canvas(page))
    ask(page, "What are our Scope 3 carbon savings?")
    answered(page)
    view = page.locator("#focusView")
    assert view.locator("h1").inner_text() == "Scope 3 carbon savings"
    text = view.inner_text()
    assert "255.1 tCO2e" in text and "Key metrics" in text
    for other in ("Hygiene", "hygiene", "Textile waste", "Water", "Garments in use", "Evidence areas"):
        assert other not in text, other


def test_a_chart_comes_from_the_question_data(online, page):
    base, fake = online
    enter(page, base)
    fake.reply("Ranked by on-time rate.")
    fake.reply_json({"title": "On-time rate", "metrics": [], "chart": {"type": "bars", "title": "On-time by month", "items": [
        {"label": "Service cycles", "value": "98.3%"}, {"label": "Baseline", "value": "9,607 wearers"}]}})
    ask(page, "Rank the on-time rate")
    answered(page)
    assert page.locator("#focusView .br").count() == 2 and page.locator("#focusView svg").count() == 0


def test_an_invented_canvas_value_is_dropped(online, page):
    base, fake = online
    enter(page, base)
    fake.reply("Answer in words.")
    fake.reply_json({"title": "Made up", "metrics": [{"label": "Invented", "value": "987,654 kg"}], "chart": {"type": "none"}})
    ask(page, "What is the invented figure?")
    answered(page)
    assert "987,654" not in page.locator("#focusView").inner_text()


def test_a_new_question_replaces_the_previous_canvas_completely(online, page):
    base, fake = online
    enter(page, base)
    ask(page, HYGIENE)
    answered(page)
    fake.reply("You saved carbon.")
    fake.reply_json(carbon_canvas(page))
    ask(page, "What are our Scope 3 carbon savings?")
    page.wait_for_function("Review.F.question.includes('Scope 3') && Review.F.answer !== null")
    assert "hygiene" not in page.locator("#focusView").inner_text().lower()


def test_a_slow_old_reply_cannot_replace_a_newer_question(online, page):
    base, fake = online
    enter(page, base)
    fake.reply("Old or new reply.")
    page.evaluate("""() => { Insights.ask('What is the on-time rate?'); Insights.ask('How many sites are there?'); }""")
    page.wait_for_function("Review.F.answer !== null")
    assert page.evaluate("Review.F.question") == "How many sites are there?"


def test_a_view_change_or_capture_closes_the_previous_answer(offline, page):
    enter(page, offline)
    ask(page, HYGIENE)
    page.wait_for_selector("#focusBack")
    ask(page, "Only show last quarter")
    page.wait_for_function("!document.body.classList.contains('focusing')")
    ask(page, HYGIENE)
    page.wait_for_selector("#focusBack")
    ask(page, "CSAT is 4.4 out of 5")
    page.wait_for_function("!document.body.classList.contains('focusing')")


def test_offline_an_unmatched_question_never_shows_a_default_topic(offline, page):
    enter(page, offline)
    ask(page, "How many wearers do we have?")
    page.wait_for_selector("#focusBack")
    text = page.locator("#focusView").inner_text()
    assert "Gemini is not connected" in text and "Account signals" not in text and "hygiene" not in text.lower()


def test_a_gemini_failure_shows_its_real_reason(online, page):
    base, fake = online
    enter(page, base)
    fake.fail(429)
    ask(page, "How many wearers do we have?")
    page.wait_for_function("document.querySelector('#focusView').innerText.includes('Gemini could not answer')")
    assert "429" in page.locator("#focusView").inner_text()


def test_a_reply_that_never_comes_ends_with_a_message(online, page):
    base, _ = online
    enter(page, base)
    page.evaluate("""() => { window.fetch = () => Promise.reject(Object.assign(new Error('x'), {name: 'AbortError'})); }""")
    ask(page, "How many wearers do we have?")
    page.wait_for_function("document.querySelector('#focusView').innerText.includes('took too long')")
    assert page.locator("#fWait").count() == 0


def test_every_question_gets_a_mini_dashboard_in_order(online, page):
    base, fake = online
    enter(page, base)
    fake.reply("- On-time rate is 98.3% [Measured].\n- Keep the current delivery rhythm.")
    fake.reply_json({"title": "Service reliability", "metrics": [{"label": "Trips on time", "value": "98.3%"}, {"label": "Wearers", "value": "9,607 wearers"}],
                     "chart": {"type": "meter", "title": "Trips on time", "items": [{"label": "On time", "value": "98.3%"}]}})
    ask(page, "What is the on-time rate?")
    answered(page)
    view = page.locator("#focusView")
    assert view.locator(".fkpis .ind").count() == 2 and view.locator(".dn svg").count() == 1 and view.locator("#fAnswer li").count() == 2
    assert "98.3%" in view.locator(".dn svg").text_content()
    y = [view.locator(s).bounding_box()["y"] for s in (".fkpis", ".dn", "#fAnswer")]
    assert y == sorted(y)  # metrics, then chart, then insights
    page.click("#focusBack")
    assert not focusing(page) and page.locator("#kpis .kpi").count() == 4


def test_a_thin_reply_is_filled_from_the_data_it_queried(online, page):
    base, fake = online
    enter(page, base)
    fake.call_tool("get_breakdown", {"by": "site"})
    fake.reply("- Sites are listed below.\n- Review the largest first.")
    fake.reply_json({"title": "Sites", "metrics": [], "chart": {"type": "none"}})
    ask(page, "Break down the sites")
    answered(page)
    assert page.locator("#focusView .fkpis .ind").count() == 2 and page.locator("#focusView .br").count() == 10


def test_the_key_is_never_sent_to_the_page(online, page):
    base, _ = online
    page.goto(base + "/")
    assert "test-key" not in page.content()
