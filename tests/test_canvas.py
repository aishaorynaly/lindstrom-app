"""The focused canvas: only what traces to the data that answered the question."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import narrative
from narrative import checked_canvas

DATA = 'water 1.41 million L, co2: 255.1 tCO2e, on time 98.3%, sites {"Oulu": 512, "Turku": 1204} and 73.8 tCO2e'


def test_traced_values_are_kept_and_invented_ones_are_dropped():
    out = checked_canvas({"title": "Water", "metrics": [{"label": "Saved", "value": "1.41 million L", "provenance": "Estimated"},
                                                        {"label": "Invented", "value": "987,654 L"}]}, DATA)
    assert [m["label"] for m in out["metrics"]] == ["Saved"] and out["metrics"][0]["provenance"] == "Estimated"


def test_a_value_with_no_figure_is_not_a_metric():
    assert checked_canvas({"title": "x", "metrics": [{"label": "Mood", "value": "good"}]}, DATA)["metrics"] == []


def test_a_chart_with_one_invented_item_is_dropped_whole():
    chart = {"type": "bars", "title": "t", "items": [{"label": "Oulu", "value": "512"}, {"label": "Fake", "value": "4,444"}]}
    assert checked_canvas({"title": "x", "metrics": [], "chart": chart}, DATA)["chart"] is None


def test_chart_sizes_come_from_the_checked_text_with_their_scale():
    chart = {"type": "bars", "title": "t", "items": [{"label": "A", "value": "255.1 tCO2e"}, {"label": "B", "value": "73.8 tCO2e"}]}
    sizes = [i["size"] for i in checked_canvas({"title": "x", "metrics": [], "chart": chart}, DATA)["chart"]["items"]]
    assert sizes == [255100.0, 73800.0]


def test_a_donut_needs_two_to_five_parts():
    one = {"type": "donut", "title": "t", "items": [{"label": "A", "value": "512"}]}
    assert checked_canvas({"title": "x", "metrics": [], "chart": one}, DATA)["chart"] is None


def test_banned_wording_is_dropped():
    bad = {"title": "Unused garments", "metrics": [{"label": "Idle garment count", "value": "512"}]}
    out = checked_canvas(bad, DATA)
    assert out["title"] == "" and out["metrics"] == []
    assert config.BANNED_TERMS


def test_at_most_three_metrics():
    metrics = [{"label": f"M{i}", "value": "512"} for i in range(9)]
    assert len(checked_canvas({"title": "x", "metrics": metrics}, DATA)["metrics"]) == 3


def test_a_text_answer_can_be_a_card_when_it_is_in_the_data():
    out = checked_canvas({"title": "Site", "metrics": [{"label": "City", "value": "Oulu"}, {"label": "Made up", "value": "Atlantis"}]}, DATA)
    assert [m["value"] for m in out["metrics"]] == ["Oulu"]


def test_a_meter_needs_one_percentage():
    meter = lambda value: {"type": "meter", "title": "t", "items": [{"label": "On time", "value": value}]}
    assert checked_canvas({"title": "x", "metrics": [], "chart": meter("98.3%")}, DATA)["chart"]["items"][0]["size"] == 98.3
    assert checked_canvas({"title": "x", "metrics": [], "chart": meter("512")}, DATA)["chart"] is None


def test_a_thin_canvas_is_filled_from_the_table_that_answered():
    outputs = ['{"by": "site", "rows": [{"Site": "Oulu", "Repairs [Measured]": 7}, {"Site": "Turku", "Repairs [Measured]": 12.5}]}']
    out = narrative.filled_canvas({"title": "Repairs", "metrics": [], "chart": None}, outputs)
    assert out["chart"]["type"] == "bars" and [i["label"] for i in out["chart"]["items"]] == ["Turku", "Oulu"]
    assert [m["label"] for m in out["metrics"]] == ["Highest: Turku", "Lowest: Oulu"] and out["metrics"][0]["value"] == "12.5"


def test_filling_never_replaces_what_is_there():
    chart = {"type": "meter", "title": "t", "items": []}
    canvas = {"title": "T", "metrics": [{"label": "A", "value": "1"}, {"label": "B", "value": "2"}], "chart": chart}
    out = narrative.filled_canvas(canvas, ['{"rows": [{"n": "x", "v": 1}, {"n": "y", "v": 2}]}'])
    assert out["chart"] is chart and len(out["metrics"]) == 2


def test_no_table_leaves_the_canvas_as_it_is():
    assert narrative.filled_canvas(None, ['{"metrics": {}}', "not json"]) == {"title": "", "metrics": [], "chart": None}


def test_a_bad_reply_gives_no_canvas(fake_gemini):
    fake_gemini.reply_json("not json")
    assert narrative.focus_canvas("q", "a", DATA) is None


def test_the_request_is_about_this_question_and_its_data(fake_gemini):
    fake_gemini.reply_json({"title": "Water", "metrics": [{"label": "Saved", "value": "1.41 million L"}]})
    out = narrative.focus_canvas("How much water?", "1.41 million L.", DATA)
    assert out["title"] == "Water"
    assert "How much water?" in fake_gemini.texts[0] and "Never use a topic the question did not ask about" in fake_gemini.system
