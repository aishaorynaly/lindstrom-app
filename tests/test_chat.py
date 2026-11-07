"""The KAM chat must only be given grounded data and must flag figures it cannot trace."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import engine
import llm
import loader
import narrative
from engine import Scenario, Scope


@pytest.fixture(scope="module")
def ds():
    return loader.load_workbook()


@pytest.fixture(scope="module")
def ctx(ds):
    return engine.build_context(ds, Scope("CUST001"), Scenario(), config.CFO_ROLE)


def test_facts_carry_terminology_provenance_and_dedup(ctx):
    facts = narrative.chat_facts(ctx)
    lowered = facts.lower()
    assert config.GAP_METRIC in facts
    assert not any(term in lowered for term in (*config.BANNED_TERMS, "idle"))
    assert "9,607 wearers" in facts and "28,367" not in facts
    for tag in config.PROVENANCE:
        assert tag in facts
    assert "NorthStar Västerås" in facts and "cannot be calculated" in facts


def test_facts_follow_the_current_scope(ds):
    finland = engine.build_context(ds, Scope("CUST001", countries=("Finland",)), Scenario("B_HOME"), config.ESG_ROLE)
    facts = narrative.chat_facts(finland)
    assert "Home washing" in facts and "NorthStar Gothenburg" not in facts and "NorthStar Oulu" in facts


def test_untraced_figures_flags_only_new_numbers(ctx):
    facts = narrative.chat_facts(ctx)
    assert narrative.untraced_figures("Water saved is 1.41 million L, 3 sites, in 2025.", facts) == []
    assert narrative.untraced_figures("Savings are 987,654 L.", facts) == ["987654"]


def test_untraced_figures_understands_russian_number_format(ctx):
    facts = narrative.chat_facts(ctx)
    assert narrative.untraced_figures("Выручка 5 230 штук, доля 16,5 %.", facts) == []
    assert narrative.untraced_figures("Всего 77 777 штук.", facts) == ["77777"]


def test_chat_answer_sends_rules_data_and_history(ctx, fake_gemini):
    fake_gemini.reply("Water saved is 1.41 million L [Estimated].")
    history = [{"role": "assistant", "content": "orphan"}, {"role": "user", "content": "Hi"},
               {"role": "assistant", "content": "Hello"}]
    answer = narrative.chat_answer(ctx, history, "How much water?")

    assert answer.text.startswith("Water saved") and answer.untraced == []
    assert answer.source.startswith(llm.MODEL)
    assert "gemini-3.8-flash:generateContent" in fake_gemini.last["path"]
    assert config.GAP_FRAMING in fake_gemini.system and "<dashboard_data>" in fake_gemini.system
    assert config.GAP_METRIC in fake_gemini.system and "[TEAM_ASSUMPTION]" in fake_gemini.system
    assert fake_gemini.roles == ["user", "model", "user"]
    assert fake_gemini.texts[-1] == "How much water?"


def test_chat_answer_flags_invented_numbers(ctx, fake_gemini):
    fake_gemini.reply("You saved 123,456,789 L [Estimated].")
    answer = narrative.chat_answer(ctx, [], "Water?")
    assert answer.untraced == ["123456789"]


def test_chat_history_is_trimmed(ctx, fake_gemini):
    history = []
    for i in range(30):
        history += [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": f"a{i}"}]
    narrative.chat_answer(ctx, history, "latest")
    assert len(fake_gemini.texts) == 2 * narrative.CHAT_HISTORY_TURNS + 1


def test_chat_failures_return_a_reason_not_an_exception(ctx, monkeypatch, fake_gemini):
    fake_gemini.body = {"promptFeedback": {"blockReason": "SAFETY"}}
    assert narrative.chat_answer(ctx, [], "Hi").source.startswith("Gemini blocked the request")

    fake_gemini.fail(401)
    answer = narrative.chat_answer(ctx, [], "Hi")
    assert answer.text == "" and answer.source.startswith("Gemini API error 401")

    monkeypatch.delenv("GEMINI_API_KEY")
    answer = narrative.chat_answer(ctx, [], "Hi")
    assert answer.text == "" and answer.source == "No Gemini API key configured"
