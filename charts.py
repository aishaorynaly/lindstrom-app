"""Plotly figures. One color per job: navy is Lindström, orange the selected
baseline or a signal, grey everything else. Provenance tags sit in each title.
"""
from __future__ import annotations

import textwrap

import pandas as pd
import plotly.graph_objects as go

import config
from engine import STATUS_LABEL

NAVY, ORANGE, GREY = "#00205b", "#d9701c", "#cbd5e1"
INK, MUTED, GRID = "#0a1b3d", "#64748b", "#e2e8f0"
HEAT_SCALE = [[0.0, "#f1f5f9"], [0.33, "#f1f5f9"], [0.33, "#fde7c7"], [0.66, "#fde7c7"], [0.66, "#f8b98a"], [1.0, "#f8b98a"]]
FONT = "Inter, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif"
# Fixed order for charts with several parts, then grey for "other". Validated for lightness, colour-blind separation
# and contrast. Brand navy is too dark for this job, so it is kept for two-part charts, with orange.
CATEGORICAL = ("#2f5fb3", ORANGE, "#0e8f7e", "#8250c4", GREY)
CONFIG = {"displayModeBar": False, "responsive": True}


def _layout(fig: go.Figure, title: str, height: int = 300, left: int = 8) -> go.Figure:
    fig.update_layout(
        title={"text": title, "font": {"size": 14, "color": INK}, "x": 0, "xanchor": "left"},
        height=height,
        margin={"l": left, "r": 16, "t": 44, "b": 8},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": MUTED, "size": 12, "family": FONT},
        showlegend=False,
        hoverlabel={"bgcolor": "white", "font_color": INK},
    )
    return fig


def donut(slices: list[tuple[str, float]], center: str, colors: tuple[str, ...] = CATEGORICAL) -> go.Figure:
    """A part-to-whole ring. The answer sits in the middle and the legend below names every slice."""
    fig = go.Figure(go.Pie(
        labels=[label for label, _ in slices], values=[value for _, value in slices], hole=.72, sort=False,
        direction="clockwise", textinfo="none",
        marker={"colors": list(colors[:len(slices)]), "line": {"color": "#ffffff", "width": 2}},
        hovertemplate="%{label}: %{value:,.1f} (%{percent})<extra></extra>",
    ))
    fig.add_annotation(text=f"<b>{center}</b>", x=.5, y=.5, showarrow=False, font={"size": 24, "color": INK, "family": FONT})
    _layout(fig, "", height=270 + 18 * max(0, len(slices) - 2))
    fig.update_layout(showlegend=True, margin={"l": 8, "r": 8, "t": 8, "b": 8},
                      legend={"orientation": "h", "x": .5, "xanchor": "center", "y": -.04, "yanchor": "top", "font": {"size": 11}})
    return fig


def compare_bars(rows: list[tuple[str, float]], title: str, number: str = ",.0f", prefix: str = "") -> go.Figure:
    """Today against the what-if, in pairs. The what-if bar is the orange one."""
    fig = go.Figure(go.Bar(
        x=[r[1] for r in rows], y=[r[0] for r in rows], orientation="h",
        marker={"color": [NAVY if i % 2 == 0 else ORANGE for i in range(len(rows))], "cornerradius": 4},
        text=[prefix + format(r[1], number) for r in rows], textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: %{text}<extra></extra>",
    ))
    fig.update_yaxes(autorange="reversed", showgrid=False, automargin=True)
    fig.update_xaxes(visible=False, range=[0, max(r[1] for r in rows) * 1.25])
    return _layout(fig, title, height=max(200, 70 + 30 * len(rows)))


def status_bars(frame: pd.DataFrame, category: str, value: str, status: str, title: str, suffix: str = "%") -> go.Figure:
    """Ranked bars. Rows in a review band are orange and also say their band in words."""
    ranked = frame.sort_values(value, ascending=False)
    bands = pd.to_numeric(ranked[status], errors="coerce").fillna(0).astype(int)
    fig = go.Figure(go.Bar(
        x=ranked[value], y=ranked[category], orientation="h",
        marker={"color": [ORANGE if b else GREY for b in bands], "cornerradius": 4},
        text=[f"{v:.1f}{suffix}" + (f" {STATUS_LABEL[b]}" if b else "") for v, b in zip(ranked[value], bands)],
        textposition="outside", cliponaxis=False, hovertemplate="%{y}: %{text}<extra></extra>",
    ))
    fig.update_yaxes(autorange="reversed", showgrid=False, automargin=True)
    fig.update_xaxes(visible=False, range=[0, ranked[value].max() * 1.35])
    return _layout(fig, title, height=max(240, 60 + 26 * len(ranked)))


def baseline_bars(frame: pd.DataFrame, column: str, title: str, selected: str, number: str) -> go.Figure:
    colors = [NAVY if i == "LINDSTROM" else ORANGE if i == selected else GREY for i in frame["id"]]
    fig = go.Figure(go.Bar(
        x=frame[column], y=frame["option"], orientation="h", marker={"color": colors, "cornerradius": 4},
        text=[format(v, number) for v in frame[column]], textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: %{text}<extra></extra>",
    ))
    fig.update_yaxes(autorange="reversed", showgrid=False, automargin=True)
    fig.update_xaxes(visible=False, range=[0, frame[column].max() * 1.2])
    return _layout(fig, title, height=250)


def range_bars(frame: pd.DataFrame, column: str, title: str) -> go.Figure:
    colors = [ORANGE if c == "Low" else NAVY if c == "Base" else GREY for c in frame["Case"]]
    fig = go.Figure(go.Bar(
        x=frame["Case"], y=frame[column], marker={"color": colors, "cornerradius": 4},
        text=[f"{v:,.0f}" for v in frame[column]], textposition="outside", cliponaxis=False,
        hovertemplate="%{x}: %{text}<extra></extra>",
    ))
    fig.update_yaxes(visible=False, rangemode="tozero", range=[0, frame[column].max() * 1.2])
    fig.update_xaxes(showgrid=False)
    return _layout(fig, title, height=250)


def site_heatmap(sites: pd.DataFrame) -> go.Figure:
    columns = (
        (f"{config.GAP_METRIC}, %", "gap_rate", "fleet_status", "{:.1f}%"),
        ("Hygiene screen, % of traced", "hygiene_share", "hygiene_status", "{:.0f}%"),
        ("Repair rate vs median", "repair_ratio", "repair_status", "{:.2f}x"),
    )
    z, text = [], []
    for _, site in sites.iterrows():
        row_z, row_text = [], []
        for _, value, status, template in columns:
            has_status = pd.notna(site[status])
            row_z.append(float(site[status]) if has_status else None)
            row_text.append(
                f"{template.format(site[value])} {STATUS_LABEL[int(site[status])]}" if has_status else "n/a"
            )
        z.append(row_z)
        text.append(row_text)
    fig = go.Figure(go.Heatmap(
        z=z, x=["<br>".join(textwrap.wrap(c[0], 26)) for c in columns], y=sites["site_name"], text=text, texttemplate="%{text}",
        textfont={"color": INK, "size": 12}, colorscale=HEAT_SCALE, zmin=0, zmax=2, showscale=False,
        xgap=2, ygap=2, hovertemplate="%{y}<br>%{x}: %{text}<extra></extra>", hoverongaps=False,
    ))
    fig.update_yaxes(autorange="reversed", showgrid=False, automargin=True)
    fig.update_xaxes(side="top", showgrid=False, tickangle=0)
    _layout(fig, "Site risk heatmap [Calculated], bands [TEAM_ASSUMPTION]", height=max(260, 110 + 30 * len(sites)))
    fig.update_layout(margin={"t": 112})
    return fig


def gap_bars(sites: pd.DataFrame) -> go.Figure:
    ranked = sites.sort_values("gap_rate", ascending=False)
    colors = [ORANGE if status else GREY for status in ranked["fleet_status"]]
    fig = go.Figure(go.Bar(
        x=ranked["gap_rate"], y=ranked["site_name"], orientation="h", marker={"color": colors, "cornerradius": 4},
        text=[f"{v:.1f}%" for v in ranked["gap_rate"]], textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: %{text}<extra></extra>",
    ))
    fig.add_vline(x=config.FLEET_WATCH * 100, line_dash="dot", line_color=MUTED,
                  annotation_text="Review threshold", annotation_position="top right")
    fig.update_yaxes(autorange="reversed", showgrid=False, automargin=True)
    fig.update_xaxes(visible=False, range=[0, ranked["gap_rate"].max() * 1.2])
    return _layout(fig, f"{config.GAP_METRIC}, share of allocated [Calculated]", height=max(240, 60 + 24 * len(ranked)))


def trend_line(trend: pd.DataFrame, column: str, title: str, number: str) -> go.Figure:
    fig = go.Figure(go.Scatter(
        x=trend["month"], y=trend[column], mode="lines+markers", line={"color": NAVY, "width": 2},
        marker={"size": 8, "color": NAVY}, hovertemplate="%{x}: %{y:" + number + "}<extra></extra>",
    ))
    fig.update_yaxes(gridcolor=GRID, zeroline=False, rangemode="tozero")
    fig.update_xaxes(showgrid=False, tickangle=0, nticks=6)
    return _layout(fig, title, height=260)


def monthly_bars(frame: pd.DataFrame, column: str, title: str) -> go.Figure:
    """One bar per month over the whole data range. The months in the current view are blue, the rest grey."""
    labels = pd.to_datetime(frame["month"] + "-01").dt.strftime("%b")
    fig = go.Figure(go.Bar(
        x=labels, y=frame[column], marker={"color": [NAVY if s else GREY for s in frame["selected"]], "cornerradius": 4},
        customdata=frame["month"], hovertemplate="%{customdata}: %{y:,.0f}<extra></extra>",
    ))
    fig.update_yaxes(gridcolor=GRID, zeroline=False, rangemode="tozero")
    fig.update_xaxes(showgrid=False, tickangle=0)
    return _layout(fig, title, height=260, left=8)


def simple_bars(frame: pd.DataFrame, category: str, value: str, title: str, number: str = ",.0f") -> go.Figure:
    fig = go.Figure(go.Bar(
        x=frame[value], y=frame[category], orientation="h", marker={"color": NAVY, "cornerradius": 4},
        text=[format(v, number) for v in frame[value]], textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: %{text}<extra></extra>",
    ))
    fig.update_yaxes(autorange="reversed", showgrid=False, automargin=True)
    fig.update_xaxes(visible=False, range=[0, frame[value].max() * 1.2])
    return _layout(fig, title, height=max(220, 70 + 34 * len(frame)))
