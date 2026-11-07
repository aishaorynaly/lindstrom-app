"""The exported deck must obey the same business rules as the dashboard."""
import re
import sys
from io import BytesIO
from pathlib import Path

import pytest
from pptx import Presentation

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import engine
import exporter
import loader
import narrative
from engine import Scenario, Scope

TAG = re.compile(r"\[(Measured|Calculated|Estimated|TEAM_ASSUMPTION)\]")


@pytest.fixture(scope="module")
def ds():
    return loader.load_workbook()


def deck_text(data: bytes) -> tuple[Presentation, str]:
    prs = Presentation(BytesIO(data))
    text = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                text.append(shape.text_frame.text)
            if getattr(shape, "has_table", False) and shape.has_table:
                text += [cell.text for row in shape.table.rows for cell in row.cells]
        text.append(slide.notes_slide.notes_text_frame.text)
    return prs, " ".join(text)


@pytest.mark.parametrize("customer, role", [
    ("CUST001", config.QUALITY_ROLE),
    ("CUST002", config.CFO_ROLE),
    ("CUST003", config.ESG_ROLE),
])
def test_deck_rules(ds, customer, role):
    ctx = engine.build_context(ds, Scope(customer), Scenario(), role)
    points = narrative.discussion_points(ctx, 3)
    prs, text = deck_text(exporter.build_deck(ctx, points, "Rule-based"))

    assert 4 <= len(prs.slides) <= 5
    assert config.GAP_METRIC in text and config.GAP_FRAMING in text
    assert "Decision Support System" in text
    assert len(TAG.findall(text)) >= 15
    lowered = text.lower()
    assert not any(term in lowered for term in (*config.BANNED_TERMS, "idle", "unused"))
    assert sum(shape.has_chart for shape in prs.slides[2].shapes) == 3


def test_deck_shows_deduplicated_wearers(ds):
    ctx = engine.build_context(ds, Scope("CUST001"), Scenario(), config.STANDARD_ROLE)
    _, text = deck_text(exporter.build_deck(ctx, narrative.discussion_points(ctx, 3), "Rule-based"))
    assert "9,607 wearers" in text and "28,367" not in text


def test_low_confidence_hygiene_does_not_drive_site_ranking(ds):
    ctx = engine.build_context(ds, Scope("CUST001"), Scenario(), config.STANDARD_ROLE)
    top = ctx.sites.iloc[:3]
    assert "NorthStar Västerås" in set(top["site_name"])
    assert (ctx.sites["risk_score"] > 0).sum() == 8


def test_build_without_baselines_still_exports(ds, tmp_path):
    import openpyxl

    book = openpyxl.load_workbook(config.DATA_PATH)
    del book["Baselines"]
    path = tmp_path / "partial.xlsx"
    book.save(path)
    partial = loader.load_workbook(path)
    ctx = engine.build_context(partial, Scope("CUST001"), Scenario(), config.ESG_ROLE)
    prs, text = deck_text(exporter.build_deck(ctx, narrative.discussion_points(ctx, 3), "Rule-based"))
    assert len(prs.slides) == 5 and "Baseline comparison unavailable" in text
