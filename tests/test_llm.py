"""The Gemini wrapper: request shape, key handling and every failure mode, through the real SDK."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm
from llm import LLMUnavailable

MESSAGES = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"},
            {"role": "user", "content": "How much water?"}]


def test_request_shape(fake_gemini):
    fake_gemini.reply("Water saved is 1.41 million L [Estimated].")
    text = llm.generate("You are a test.", MESSAGES)

    assert text == "Water saved is 1.41 million L [Estimated]."
    assert fake_gemini.last["path"] == "/v1beta/models/gemini-2.5-flash:generateContent"
    assert fake_gemini.last["headers"]["x-goog-api-key"] == "test-key"
    assert fake_gemini.system == "You are a test."
    assert fake_gemini.roles == ["user", "model", "user"]
    assert fake_gemini.texts == ["Hi", "Hello", "How much water?"]
    assert fake_gemini.last["body"]["generationConfig"]["maxOutputTokens"] == llm.MAX_OUTPUT_TOKENS


def test_schema_requests_json(fake_gemini):
    fake_gemini.reply('{"points": []}')
    schema = {"type": "object", "properties": {"points": {"type": "array", "items": {"type": "string"}}}}
    llm.generate("sys", MESSAGES[:1], schema=schema)
    config = fake_gemini.last["body"]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"]["properties"]["points"]["type"] == "array"


def test_only_gemini_api_key_is_used(monkeypatch, fake_gemini):
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.setenv("GOOGLE_API_KEY", "some-other-google-key")
    with pytest.raises(LLMUnavailable, match="No Gemini API key configured"):
        llm.generate("sys", MESSAGES[:1])
    assert fake_gemini.requests == []


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500])
def test_api_errors_report_the_status(fake_gemini, status):
    fake_gemini.fail(status)
    with pytest.raises(LLMUnavailable, match=f"Gemini API error {status}"):
        llm.generate("sys", MESSAGES[:1])


def test_blocked_prompt(fake_gemini):
    fake_gemini.body = {"promptFeedback": {"blockReason": "SAFETY"}}
    with pytest.raises(LLMUnavailable, match=r"blocked the request \(SAFETY\)"):
        llm.generate("sys", MESSAGES[:1])


def test_no_candidates(fake_gemini):
    fake_gemini.body = {}
    with pytest.raises(LLMUnavailable, match="returned no answer"):
        llm.generate("sys", MESSAGES[:1])


def test_truncated_answer_is_rejected(fake_gemini):
    fake_gemini.reply("A half-finished sentence", finish="MAX_TOKENS")
    with pytest.raises(LLMUnavailable, match="cut off"):
        llm.generate("sys", MESSAGES[:1])


def test_empty_text_is_rejected(fake_gemini):
    fake_gemini.reply("   ", finish="SAFETY")
    with pytest.raises(LLMUnavailable, match=r"no text \(SAFETY\)"):
        llm.generate("sys", MESSAGES[:1])


def test_unreachable_server(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", "http://127.0.0.1:9")
    with pytest.raises(LLMUnavailable, match="Could not reach the Gemini API"):
        llm.generate("sys", MESSAGES[:1])


def test_missing_package_degrades(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(llm, "genai", None)
    with pytest.raises(LLMUnavailable, match="google-genai package not installed"):
        llm.generate("sys", MESSAGES[:1])


# --- A retired model ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("names, best", [
    (["models/gemini-2.0-flash", "models/gemini-3.5-flash", "models/gemini-3.5-flash-lite"], "gemini-3.5-flash"),
    (["models/gemini-3.10-pro", "models/gemini-flash-latest"], "gemini-flash-latest"),
    (["models/gemini-3.0-flash-preview-05", "models/gemini-3.0-flash-image-preview"], "gemini-3.0-flash-preview-05"),
    (["models/text-embedding-004"], None),
])
def test_pick_model(names, best):
    assert llm.pick_model(names) == best


def test_a_retired_model_moves_to_one_the_key_can_use(fake_gemini, monkeypatch):
    monkeypatch.setattr(llm, "MODEL", "gemini-2.5-flash")
    fake_gemini.missing = {"gemini-2.5-flash"}
    fake_gemini.models = ["gemini-3.0-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite", "text-embedding-004"]
    fake_gemini.reply("Hello")
    assert llm.generate("system", [{"role": "user", "content": "hi"}]) == "Hello"
    assert llm.MODEL == "gemini-3.5-flash"
    assert "gemini-3.5-flash:" in fake_gemini.last["path"]


def test_no_usable_model_reports_the_404_clearly(fake_gemini, monkeypatch):
    monkeypatch.setattr(llm, "MODEL", "gemini-2.5-flash")
    fake_gemini.missing = {"gemini-2.5-flash"}
    with pytest.raises(llm.LLMUnavailable, match="404 .model gemini-2.5-flash not found"):
        llm.generate("system", [{"role": "user", "content": "hi"}])


# --- A busy service --------------------------------------------------------------------------------------------
def test_ranking_keeps_flash_lite_as_the_last_resort():
    ranked = llm.rank_models(["models/gemini-3.8-flash-lite", "models/gemini-3.8-flash", "models/gemini-3.5-flash", "models/gemini-3.5-pro"])
    assert ranked == ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.8-flash-lite"]


def test_a_busy_model_is_retried(fake_gemini, monkeypatch):
    monkeypatch.setattr(llm, "MODEL", "gemini-3.8-flash")
    fake_gemini.busy = ["gemini-3.8-flash", "gemini-3.8-flash"]
    fake_gemini.reply("Hello")
    assert llm.generate("system", [{"role": "user", "content": "hi"}]) == "Hello"
    assert llm.MODEL == "gemini-3.8-flash"


def test_a_model_that_stays_busy_moves_to_another(fake_gemini, monkeypatch):
    monkeypatch.setattr(llm, "MODEL", "gemini-3.8-flash")
    fake_gemini.always_busy = {"gemini-3.8-flash"}
    fake_gemini.models = ["gemini-3.8-flash", "gemini-3.5-flash"]
    fake_gemini.reply("Hello")
    assert llm.generate("system", [{"role": "user", "content": "hi"}]) == "Hello"
    assert llm.MODEL == "gemini-3.5-flash"


def test_when_everything_is_busy_the_reason_is_readable(fake_gemini, monkeypatch):
    monkeypatch.setattr(llm, "MODEL", "gemini-3.8-flash")
    fake_gemini.always_busy = {"gemini-3.8-flash", "gemini-3.5-flash"}
    fake_gemini.models = ["gemini-3.8-flash", "gemini-3.5-flash"]
    with pytest.raises(llm.LLMUnavailable, match="503 .Gemini is busy"):
        llm.generate("system", [{"role": "user", "content": "hi"}])
