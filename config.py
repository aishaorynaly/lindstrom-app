"""Thresholds, provenance vocabulary, roles and TEAM_ASSUMPTION parameters.

Anything a reviewer might challenge lives here, with its rationale, so the
engine stays free of magic numbers.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DATA_PATH = Path(os.environ.get("LINDSTROM_DATA", Path(__file__).parent / "data.xlsx"))

# --- Provenance -------------------------------------------------------------
MEASURED = "Measured"
CALCULATED = "Calculated"
ESTIMATED = "Estimated"
TEAM_ASSUMPTION = "TEAM_ASSUMPTION"

PROVENANCE = {
    MEASURED: "Empirical logs: RFID scans, wash counts, repair counts, deliveries.",
    CALCULATED: "Deterministic formula applied to measured inputs.",
    ESTIMATED: "Baseline comparison or allocated share. Not directly measured for this customer.",
    TEAM_ASSUMPTION: "Sensitivity range or threshold set by our team. The rationale is stated next to it.",
}

# --- Terminology ------------------------------------------------------------
GAP_METRIC = "Allocated garments with no scan activity"
GAP_FRAMING = "Optimization Signal for Investigation"
GAP_REASONS = "low wash frequency, delayed pickups or uncaptured RFID scans"

# Wording that must never reach a viewer. The loader rewrites these in workbook
# text, and tests/test_engine.py scans the rendered output for them.
BANNED_TERMS = ("idle garment", "unused garment", "unused allocation")

DISCLAIMER = (
    "Decision Support System for KAM steering meetings. Not audited compliance or "
    "CSRD reporting. All figures are synthetic and illustrative."
)

# --- Sensitivity parameters ([TEAM_ASSUMPTION]) -------------------------------
LEVELS = ("Low", "Base", "High")


@dataclass(frozen=True)
class Assumption:
    key: str
    name: str
    low: float
    base: float
    high: float
    unit: str
    rationale: str

    def value(self, level: str) -> float:
        return {"Low": self.low, "Base": self.base, "High": self.high}[level]


SENSITIVITY = {
    "baseline_scale": Assumption(
        "baseline_scale",
        "Baseline factor scale",
        0.85, 1.00, 1.15, "x baseline factors",
        "Baselines sheet marks its factors as illustrative. +/-15% tests whether the "
        "conclusion survives a materially better or worse competitor.",
    ),
    "repair_rate": Assumption(
        "repair_rate",
        "Replacements postponed per repair",
        0.50, 1.00, 1.20, "replacements / repair",
        "Base is workbook assumption A002 (1:1), rated Low-Medium confidence. Low halves "
        "it because a button repair rarely shifts a whole replacement. High tests "
        "structural repairs such as zippers and patches.",
    ),
    "footprint_scale": Assumption(
        "footprint_scale",
        "Product footprint scale",
        0.80, 1.00, 1.20, "x product kgCO2e",
        "Workbook assumption A005 rates the synthetic product footprints Low confidence "
        "and requires replacement for production, so they get the widest range.",
    ),
}

# --- Signal thresholds ([TEAM_ASSUMPTION]) ------------------------------------
HYGIENE_DAYS = 180
HYGIENE_DAYS_RATIONALE = (
    "Challenge brief: 0 laundry scans for more than 180 days. Longer than any gap "
    "between consecutive scans in the log (maximum 170 days)."
)
HYGIENE_GROUPS = ("Food Industry", "Healthcare")
HYGIENE_GROUPS_RATIONALE = (
    "Food Industry is the brief's scope. Healthcare is added because Mediora lists "
    "hygiene as a priority topic."
)
HYGIENE_WATCH, HYGIENE_HIGH = 0.30, 0.40
HYGIENE_RATE_RATIONALE = (
    "Site share of traced garments. Portfolio range is 21-50%, so these bands separate "
    "sites for review rather than declare a failure."
)

FLEET_WATCH, FLEET_HIGH = 0.17, 0.18
FLEET_RATIONALE = (
    "Sites range 13.6-19.5% around a 16% mean (SD 1.3 pts). 17% is about one SD above "
    "the mean, and 18% singles out clear outliers for a site audit discussion."
)

REPAIR_WATCH, REPAIR_HIGH = 1.10, 1.15
REPAIR_RATIONALE = (
    "Repairs per washed piece compared with the median for the same product group, "
    "since Hi-Vis and coveralls naturally repair more than coats."
)

# --- Personalization (80/20) --------------------------------------------------
STANDARD_ROLE = "Standard review"
QUALITY_ROLE = "Food Industry / Quality Manager"
CFO_ROLE = "CFO / Procurement Lead"
ESG_ROLE = "ESG / Sustainability Lead"

KPI_METRICS = ("allocated", "footprint_co2", "water_saved", "risk_signals")
STANDARD_METRICS = (
    "sites", "wearers", "washed_kg", "co2_saved",
    "gap", "repairs", "on_time",
)

ROLES = {
    STANDARD_ROLE: {
        "blurb": "Standard structure, with no priority tailoring applied.",
        "metrics": (),
        "order": ("baseline", "fleet", "repair", "hygiene", "service", "circular", "tco"),
    },
    QUALITY_ROLE: {
        "blurb": "Hygiene control, food safety risk flags and water savings.",
        "metrics": ("hygiene_flagged", "hygiene_coverage", "water_saved", "on_time"),
        "order": ("hygiene", "baseline", "service", "fleet", "repair", "circular", "tco"),
    },
    CFO_ROLE: {
        "blurb": "TCO inputs, purchase cost avoided through repairs, and inventory balance.",
        "metrics": ("avoided_eur", "gap", "gap_value", "garments_per_wearer"),
        "order": ("fleet", "repair", "tco", "baseline", "service", "circular", "hygiene"),
    },
    ESG_ROLE: {
        "blurb": "Carbon handprint, water efficiency and circular recycling rates.",
        "metrics": ("co2_saved", "avoided_co2", "water_saved", "circular_rate"),
        "order": ("baseline", "circular", "repair", "fleet", "service", "hygiene", "tco"),
    },
}

INDUSTRY_DEFAULT_ROLE = {
    "Food & Beverage": QUALITY_ROLE,
    "Healthcare": QUALITY_ROLE,
    "Manufacturing": CFO_ROLE,
}

DEFAULT_BASELINE = "B_LOCAL"

# What a KAM can ask about. Each topic has its own focused view (focus.py).
FOCUS_TOPICS = ("hygiene", "pricing", "whatif", "fleet", "repair", "sustainability", "service", "export", "general")
PRICE_STEP_DEFAULT = 5.0

# Workbook Sample_Output rows that tests reconcile against.
RECONCILE_CUSTOMER = "CUST001"
