"""Editable 5-slide steering deck: native charts and tables, no pictures."""
from __future__ import annotations

from io import BytesIO

import pandas as pd
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

import config
from engine import HIGH, STATUS_LABEL, WATCH, ReviewContext, format_value
from narrative import Point, headline

INK = RGBColor(0x12, 0x2B, 0x33)
MUTED = RGBColor(0x55, 0x65, 0x6B)
NIGHT = RGBColor(0x0B, 0x3C, 0x49)
ICE = RGBColor(0xEA, 0xF3, 0xF5)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
BLUE = RGBColor(0x2A, 0x78, 0xD6)
ORANGE = RGBColor(0xEB, 0x68, 0x34)
GREY = RGBColor(0xB4, 0xBC, 0xBF)
HEAT = {HIGH: RGBColor(0xF2, 0xA0, 0x7E), WATCH: RGBColor(0xFB, 0xDD, 0xCF), None: RGBColor(0xF1, 0xF3, 0xF4)}

SHORT_NAMES = {"LINDSTROM": "Lindström", "B_LOCAL": "Local laundry", "B_HOME": "Home", "B_INHOUSE": "In-house"}
LEFT, WIDTH = 0.6, 12.13


def _text(slide, x, y, w, h, runs, size=14, color=INK, bold=False, italic=False,
          align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, name=None):
    """runs: a string, or a list of paragraphs. A paragraph is a string or (text, overrides)."""
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        box.name = name
    frame = box.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = anchor
    frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
    paragraphs = runs if isinstance(runs, list) else [runs]
    for index, entry in enumerate(paragraphs):
        content, style = entry if isinstance(entry, tuple) else (entry, {})
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.alignment = align
        paragraph.space_after = Pt(style.get("after", 0))
        run = paragraph.add_run()
        run.text = content
        run.font.size = Pt(style.get("size", size))
        run.font.bold = style.get("bold", bold)
        run.font.italic = style.get("italic", italic)
        run.font.color.rgb = style.get("color", color)
    return box


def _card(slide, x, y, w, h, fill=ICE, name="Card"):
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.name = name
    shape.adjustments[0] = 0.05
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def _content_slide(prs, title: str, number: int, notes: str):
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    title_shape = slide.shapes.title
    title_shape.left, title_shape.top = Inches(LEFT), Inches(0.4)
    title_shape.width, title_shape.height = Inches(WIDTH), Inches(1.0)
    frame = title_shape.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = MSO_ANCHOR.TOP
    frame.margin_left = frame.margin_top = 0
    paragraph = frame.paragraphs[0]
    paragraph.alignment = PP_ALIGN.LEFT
    run = paragraph.add_run()
    run.text = title
    run.font.size = Pt(30)
    run.font.bold = True
    run.font.color.rgb = NIGHT
    _text(slide, LEFT, 6.98, 11.2, 0.3, config.DISCLAIMER, size=10, color=MUTED, name="Footer")
    _text(slide, 12.13, 6.98, 0.6, 0.3, str(number), size=10, color=MUTED, align=PP_ALIGN.RIGHT, name="Slide number")
    slide.notes_slide.notes_text_frame.text = notes
    return slide


def _title_slide(prs, ctx: ReviewContext) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = NIGHT
    _text(slide, 0.8, 1.9, 11.7, 1.7, ctx.customer["customer_name"], size=48, color=WHITE, bold=True,
          anchor=MSO_ANCHOR.BOTTOM, name="Title")
    _text(slide, 0.8, 3.75, 11.7, 0.6, "Customer value review for the steering committee", size=24, color=ICE)
    _text(slide, 0.8, 4.45, 11.7, 0.8, [
        f"{ctx.label.split(' | ', 1)[-1]}  |  {ctx.period}",
        f"Prepared for: {ctx.role}  |  Baseline: {ctx.baseline}",
    ], size=16, color=ICE)
    _text(slide, 0.8, 6.5, 11.7, 0.4, config.DISCLAIMER, size=12, color=ICE)
    slide.notes_slide.notes_text_frame.text = (
        "Opening: this review supports a discussion. Every figure is labelled Measured, Calculated, "
        "Estimated or TEAM_ASSUMPTION, and the data pack is synthetic."
    )


def _value_slide(prs, ctx: ReviewContext) -> None:
    m = ctx.metrics
    keys = ("water_saved", "co2_saved", "avoided_eur", "repairs", "wearers", "gap")
    notes = "\n".join(f"{m[k].label}: {m[k].text} [{', '.join(m[k].tags)}]. {m[k].formula}" for k in keys)
    slide = _content_slide(prs, headline(ctx), 2, notes)
    card_w, card_h, gap = 3.87, 2.45, 0.26
    for index, key in enumerate(keys):
        metric = m[key]
        x = LEFT + (index % 3) * (card_w + gap)
        y = 1.55 + (index // 3) * (card_h + gap)
        _card(slide, x, y, card_w, card_h, name=f"Card {metric.label}")
        detail = metric.note if key in ("gap",) else f"Range: {metric.range_text}" if metric.range_text else metric.note
        if key == "gap":
            detail = f"{config.GAP_FRAMING}, not a conclusion"
        elif key == "wearers":
            detail = f"Largest category per site. Range {metric.range_text}"
        elif key == "repairs":
            detail = "Completed repair events in the period"
        tags = "  ".join(f"[{t}]" for t in metric.tags)
        _text(slide, x + 0.25, y + 0.15, card_w - 0.5, card_h - 0.3, [
            (metric.text, {"size": 32, "bold": True, "color": NIGHT, "after": 5}),
            (metric.label, {"size": 15, "bold": True, "after": 5}),
            (detail, {"size": 13, "color": MUTED, "after": 6}),
            (tags, {"size": 11, "bold": True, "color": BLUE}),
        ], anchor=MSO_ANCHOR.MIDDLE, name=f"Value {metric.label}")


def _style_chart(chart, title: str, frame: pd.DataFrame, selected: str, number_format: str) -> None:
    chart.has_legend = False
    chart.has_title = True
    chart.chart_title.include_in_layout = False
    title_frame = chart.chart_title.text_frame
    title_frame.text = title
    run = title_frame.paragraphs[0].runs[0]
    run.font.size = Pt(13)
    run.font.bold = True
    run.font.color.rgb = INK
    chart.font.size = Pt(12)
    chart.font.name = "Calibri"
    chart.font.color.rgb = MUTED
    chart.value_axis.visible = False
    chart.value_axis.has_major_gridlines = False
    chart.value_axis.minimum_scale = 0
    chart.category_axis.format.line.color.rgb = GREY
    plot = chart.plots[0]
    plot.gap_width = 55
    plot.has_data_labels = True
    labels = plot.data_labels
    labels.number_format = number_format
    labels.number_format_is_linked = False
    labels.position = XL_LABEL_POSITION.OUTSIDE_END
    labels.font.size = Pt(12)
    labels.font.color.rgb = INK
    series = plot.series[0]
    for index, option in enumerate(frame["id"]):
        point = series.points[index]
        point.format.fill.solid()
        point.format.fill.fore_color.rgb = BLUE if option == "LINDSTROM" else ORANGE if option == selected else GREY


def _baseline_slide(prs, ctx: ReviewContext) -> None:
    notes = (
        "Bars are intensity per washed kg. Lindström values are allocated by washed-kg share [Estimated]; "
        "baseline factors come from the Baselines sheet and are illustrative. Orange marks the selected baseline. "
        "Sensitivity moves all TEAM_ASSUMPTION parameters together: "
        + "; ".join(f"{a.name} {a.low}/{a.base}/{a.high}" for a in config.SENSITIVITY.values())
    )
    floor = ctx.sensitivity[ctx.sensitivity.columns[1:3]].min().min() if not ctx.sensitivity.empty else None
    title = (
        f"The advantage over {ctx.baseline} holds across the sensitivity range"
        if floor is not None and pd.notna(floor) and floor > 0
        else f"The advantage over {ctx.baseline} depends on the sensitivity case"
    )
    if ctx.baselines.empty:
        slide = _content_slide(prs, "Baseline comparison unavailable", 3, notes)
        _text(slide, LEFT, 1.6, WIDTH, 1, "The workbook has no Baselines or impact data for this scope.", size=16)
        return

    slide = _content_slide(prs, title, 3, notes)
    series = (
        ("water_l_per_kg", "Water, L/kg [Estimated]", "0.0"),
        ("energy_kwh_per_kg", "Energy, kWh/kg [Estimated]", "0.00"),
        ("co2_kg_per_kg", "CO2e, kg/kg [Estimated]", "0.00"),
    )
    chart_w = 3.95
    for index, (column, chart_title, number_format) in enumerate(series):
        data = CategoryChartData()
        data.categories = [SHORT_NAMES.get(i, i) for i in ctx.baselines["id"]]
        data.add_series(chart_title, [float(v) for v in ctx.baselines[column]])
        frame = slide.shapes.add_chart(
            XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(LEFT + index * (chart_w + 0.14)), Inches(1.45),
            Inches(chart_w), Inches(3.2), data,
        )
        frame.name = chart_title
        _style_chart(frame.chart, chart_title, ctx.baselines, ctx.scenario.baseline_id, number_format)

    columns = list(ctx.sensitivity.columns[1:4])
    headers = ["Sensitivity case [TEAM_ASSUMPTION]", "Water saved [Estimated]", "CO2 saved [Estimated]", "Repair-avoided CO2 [Estimated]"]
    units = ["L", "kgCO2e", "kgCO2e"]
    shape = slide.shapes.add_table(4, 4, Inches(LEFT), Inches(4.8), Inches(WIDTH), Inches(2.0))
    shape.name = "Sensitivity table"
    table = shape.table
    for col, header in enumerate(headers):
        _cell(table.cell(0, col), header, fill=NIGHT, color=WHITE, bold=True, size=12)
    for row, (_, values) in enumerate(ctx.sensitivity.iterrows(), start=1):
        _cell(table.cell(row, 0), values["Case"], fill=ICE, bold=True, size=12)
        for col, (column, unit) in enumerate(zip(columns, units), start=1):
            _cell(table.cell(row, col), format_value(values[column], unit), fill=WHITE, size=12)


def _cell(cell, text: str, fill=WHITE, color=INK, bold=False, size=12) -> None:
    cell.fill.solid()
    cell.fill.fore_color.rgb = fill
    cell.vertical_anchor = MSO_ANCHOR.MIDDLE
    cell.margin_top = cell.margin_bottom = Inches(0.03)
    frame = cell.text_frame
    frame.word_wrap = True
    run = frame.paragraphs[0].add_run()
    run.text = str(text)
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color


def _status(value: str, status) -> tuple[str, RGBColor]:
    if status is None or pd.isna(status):
        return "n/a", HEAT[None]
    status = int(status)
    return f"{value}  {STATUS_LABEL[status]}", HEAT.get(status, WHITE)


def _heatmap_slide(prs, ctx: ReviewContext) -> None:
    notes = (
        f"{config.GAP_METRIC} is an {config.GAP_FRAMING}: it may reflect {config.GAP_REASONS}.\n"
        f"Fleet bands: {config.FLEET_RATIONALE}\nHygiene bands: {config.HYGIENE_RATE_RATIONALE}\n"
        f"Repair bands: {config.REPAIR_RATIONALE}\n"
        "The Repairs sheet has no asset IDs, so repair frequency is shown by site and product."
    )
    sites = ctx.sites.head(10)
    flagged = int((ctx.sites["risk_score"] > 0).sum())
    title = f"{flagged} of {len(ctx.sites)} sites {'needs' if flagged == 1 else 'need'} a fleet or repair review"
    slide = _content_slide(prs, title, 4, notes)
    headers = ["Site", f"{config.GAP_METRIC}, % [Calculated]",
               "Hygiene screen, % of traced [Calculated]", "Repair rate vs median [Calculated]"]
    widths = (2.5, 2.35, 2.2, 1.6)
    shape = slide.shapes.add_table(len(sites) + 1, 4, Inches(LEFT), Inches(1.45), Inches(sum(widths)), Inches(5.3))
    shape.name = "Site risk heatmap"
    table = shape.table
    for col, width in enumerate(widths):
        table.columns[col].width = Inches(width)
    for col, header in enumerate(headers):
        _cell(table.cell(0, col), header, fill=NIGHT, color=WHITE, bold=True, size=11)
    for row, (_, site) in enumerate(sites.iterrows(), start=1):
        _cell(table.cell(row, 0), site["site_name"], bold=True, size=12, fill=WHITE)
        cells = (
            (f"{site['gap_rate']:.1f}%", site["fleet_status"]),
            (f"{site['hygiene_share']:.0f}%" if pd.notna(site["hygiene_share"]) else "", site["hygiene_status"]),
            (f"{site['repair_ratio']:.2f}x" if pd.notna(site["repair_ratio"]) else "", site["repair_status"]),
        )
        for col, (value, status) in enumerate(cells, start=1):
            text, fill = _status(value, status)
            _cell(table.cell(row, col), text, fill=fill, size=12)

    _card(slide, 9.5, 1.45, 3.23, 5.3, name="How to read")
    hygiene = ctx.hygiene
    coverage = (
        f"Hygiene screen sees only {hygiene['coverage']:.1f}% of this fleet, so it is low confidence."
        if hygiene["applicable"] else "No hygiene-critical garments in this scope."
    )
    _text(slide, 9.72, 1.65, 2.8, 4.9, [
        ("How to read this", {"size": 16, "bold": True, "color": NIGHT, "after": 8}),
        (config.GAP_FRAMING + ".", {"size": 14, "bold": True, "after": 3}),
        (f"May reflect {config.GAP_REASONS}.", {"size": 14, "after": 10}),
        (coverage, {"size": 14, "after": 10}),
        ("Ranked on fleet and repair signals. Bands are [TEAM_ASSUMPTION]."
         + (" Top 10 sites shown." if len(ctx.sites) > 10 else ""), {"size": 12, "color": MUTED}),
    ], name="Reading guide")


def _discussion_slide(prs, ctx: ReviewContext, points: list[Point], source: str) -> None:
    notes = f"Discussion point source: {source}.\n" + "\n".join(f"{p.title}: {p.evidence}" for p in points)
    slide = _content_slide(prs, "Three questions for the steering committee", 5, notes)
    shown = points[:3]
    width = (WIDTH - 0.26 * (len(shown) - 1)) / max(len(shown), 1)
    for index, point in enumerate(shown):
        x = LEFT + index * (width + 0.26)
        _card(slide, x, 1.45, width, 5.0, name=f"Point {index + 1}")
        badge = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x + 0.25), Inches(1.68), Inches(0.5), Inches(0.5))
        badge.name = f"Point number {index + 1}"
        badge.fill.solid()
        badge.fill.fore_color.rgb = NIGHT
        badge.line.fill.background()
        badge.shadow.inherit = False
        badge_frame = badge.text_frame
        badge_frame.margin_left = badge_frame.margin_right = badge_frame.margin_top = badge_frame.margin_bottom = 0
        badge_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        paragraph = badge_frame.paragraphs[0]
        paragraph.alignment = PP_ALIGN.CENTER
        run = paragraph.add_run()
        run.text = str(index + 1)
        run.font.size = Pt(16)
        run.font.bold = True
        run.font.color.rgb = WHITE
        _text(slide, x + 0.25, 2.3, width - 0.5, 4.05, [
            (point.title, {"size": 18, "bold": True, "color": NIGHT, "after": 8}),
            (point.evidence, {"size": 15, "after": 12}),
            (point.question, {"size": 15, "bold": True}),
        ], name=f"Point text {index + 1}")
    _text(slide, LEFT, 6.55, WIDTH, 0.3, f"Discussion points: {source}", size=11, color=MUTED, name="Point source")


def build_deck(ctx: ReviewContext, points: list[Point], source: str) -> bytes:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    prs.core_properties.title = f"{ctx.customer['customer_name']} customer value review"
    prs.core_properties.subject = "Decision support for KAM steering meetings. Synthetic data."
    _title_slide(prs, ctx)
    _value_slide(prs, ctx)
    _baseline_slide(prs, ctx)
    _heatmap_slide(prs, ctx)
    _discussion_slide(prs, ctx, points, source)
    buffer = BytesIO()
    prs.save(buffer)
    return buffer.getvalue()
