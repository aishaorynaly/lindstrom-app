"""Workbook ingestion: schema validation, joins, wearer deduplication.

Missing optional sheets degrade the affected feature instead of crashing; the
reason lands in Dataset.issues and is shown in the app.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path

import pandas as pd

import config

# sheet -> (required columns, is the app unusable without it?)
SCHEMA = {
    "Customers": (["customer_id", "customer_name", "industry", "segment", "priority_topics"], True),
    "Sites": (["site_id", "customer_id", "site_name", "country", "city", "site_type", "primary_laundry_id"], True),
    "Products": (["product_id", "product_name", "product_group", "avg_weight_kg",
                  "synthetic_product_footprint_kgco2e", "synthetic_purchase_cost_eur"], True),
    "Service_Activity": (["month", "customer_id", "site_id", "laundry_id", "product_id", "active_wearers",
                          "allocated_pieces", "active_assets_scanned", "washed_pieces", "customer_washed_kg",
                          "service_requests"], True),
    "Repairs": (["month", "customer_id", "site_id", "product_id", "repair_type", "repair_count"], False),
    "Service_Cycles": (["asset_id", "customer_id", "site_id", "product_id", "pickup_timestamp",
                        "in_laundry_timestamp", "out_laundry_timestamp", "delivered_timestamp",
                        "cycle_hours", "performance_flag"], False),
    "Laundry_Resources": (["month", "laundry_id", "washed_kg", "energy_kwh_per_kg", "fresh_water_l_per_kg",
                           "process_emissions_kgco2e", "delivery_emissions_kgco2e"], False),
    "Waste_Circularity": (["month", "laundry_id", "recycling_kg", "reuse_kg", "textile_waste_total_kg"], False),
    "Baselines": (["baseline_id", "baseline_name", "selection_status", "water_l_per_kg", "energy_kwh_per_kg",
                   "process_kgco2e_per_kg", "delivery_kgco2e_per_kg", "assumption_note"], False),
    "Customer_Impacts": (["month", "site_id", "product_id", "customer_washed_kg", "allocated_water_l",
                          "allocated_energy_kwh", "allocated_process_kgco2e", "allocated_delivery_kgco2e",
                          "allocated_textile_waste_kg"], False),
    "Industry_Priorities": (["industry", "priority_rank", "priority_theme", "dashboard_emphasis"], False),
    "Assumptions": (["assumption_id", "assumption", "value_or_rule", "rationale", "confidence"], False),
    "Data_Quality": (["dataset", "status", "confidence", "note"], False),
    "Suggested_Prompts": (["user_type", "prompt", "expected_output"], False),
}

TIMESTAMPS = ["pickup_timestamp", "in_laundry_timestamp", "out_laundry_timestamp", "delivered_timestamp"]

STAGES = {
    "pickup_to_laundry_h": "Pickup to laundry",
    "in_laundry_h": "In laundry",
    "laundry_to_delivery_h": "Laundry to delivery",
}

SANITIZE = re.compile(r"unused allocation", re.IGNORECASE)


class LoadError(RuntimeError):
    """A sheet the app cannot work without is missing or malformed."""


@dataclass
class Issue:
    level: str  # "warning" | "info"
    sheet: str
    message: str


@dataclass
class Dataset:
    customers: pd.DataFrame
    sites: pd.DataFrame
    products: pd.DataFrame
    activity: pd.DataFrame
    repairs: pd.DataFrame
    cycles: pd.DataFrame
    impacts: pd.DataFrame
    waste: pd.DataFrame
    baselines: pd.DataFrame
    priorities: pd.DataFrame
    assumptions: pd.DataFrame
    data_quality: pd.DataFrame
    prompts: pd.DataFrame
    months: list[str]
    as_of: pd.Timestamp | None
    issues: list[Issue] = field(default_factory=list)


def dedupe_wearers(activity: pd.DataFrame) -> pd.DataFrame:
    """Deduplicated wearers per site and month.

    The same employee wears a jacket, trousers and a coat, so categories overlap
    almost fully. The largest category in the site is the smallest headcount that
    can explain the data, and it is never summed across categories.
    """
    return (
        activity.groupby(["site_id", "month"], as_index=False)
        .agg(wearers=("active_wearers", "max"), categories=("product_id", "nunique"))
    )


def for_customer(ds: Dataset, customer_id: str) -> Dataset:
    """The dataset a KAM sees for one assigned client: only that client's rows, plus shared reference data."""
    if customer_id not in set(ds.customers["customer_id"]):
        raise LoadError(f"Unknown client: {customer_id}")

    def own(frame: pd.DataFrame) -> pd.DataFrame:
        return frame[frame["customer_id"] == customer_id] if "customer_id" in frame.columns else frame

    activity = own(ds.activity)
    if activity.empty:
        raise LoadError(f"No service activity for client {customer_id}")
    return replace(
        ds,
        customers=own(ds.customers), sites=own(ds.sites), activity=activity, repairs=own(ds.repairs),
        cycles=own(ds.cycles), impacts=own(ds.impacts),
        months=sorted(activity["month"].astype(str).unique()),
    )


def _sanitize(frame: pd.DataFrame) -> pd.DataFrame:
    """Rewrite workbook wording that breaks the mandatory metric terminology."""
    out = frame.copy()
    for col in out.select_dtypes(include="object").columns:
        out[col] = out[col].map(
            lambda v: SANITIZE.sub(config.GAP_METRIC.lower(), v) if isinstance(v, str) else v
        )
    return out


def _read_sheets(source: Path | str, issues: list[Issue]) -> dict[str, pd.DataFrame]:
    try:
        raw = pd.read_excel(source, sheet_name=list(_present_sheets(source)))
    except FileNotFoundError as exc:
        raise LoadError(f"Workbook not found: {source}") from exc

    sheets: dict[str, pd.DataFrame] = {}
    for name, (columns, critical) in SCHEMA.items():
        frame = raw.get(name)
        missing = columns if frame is None else [c for c in columns if c not in frame.columns]
        if missing:
            detail = "sheet missing" if frame is None else f"missing columns: {', '.join(missing)}"
            if critical:
                raise LoadError(f"{name}: {detail}")
            issues.append(Issue("warning", name, f"{detail}. Dependent metrics show n/a."))
            frame = pd.DataFrame(columns=columns)
        sheets[name] = frame.dropna(how="all")
    return sheets


def _present_sheets(source: Path | str) -> list[str]:
    with pd.ExcelFile(source) as book:
        return [s for s in book.sheet_names if s in SCHEMA]


def _derive_impacts(activity: pd.DataFrame, resources: pd.DataFrame, waste: pd.DataFrame) -> pd.DataFrame:
    """Rebuild allocated impacts by washed-kg share if Customer_Impacts is absent."""
    if resources.empty:
        return pd.DataFrame(columns=SCHEMA["Customer_Impacts"][0])
    keys = ["month", "laundry_id"]
    frame = activity.merge(resources, on=keys, how="left")
    frame = frame.merge(waste[keys + ["textile_waste_total_kg"]], on=keys, how="left")
    share = frame["customer_washed_kg"] / frame["washed_kg"]
    frame["allocated_water_l"] = frame["customer_washed_kg"] * frame["fresh_water_l_per_kg"]
    frame["allocated_energy_kwh"] = frame["customer_washed_kg"] * frame["energy_kwh_per_kg"]
    frame["allocated_process_kgco2e"] = share * frame["process_emissions_kgco2e"]
    frame["allocated_delivery_kgco2e"] = share * frame["delivery_emissions_kgco2e"]
    frame["allocated_textile_waste_kg"] = (share * frame["textile_waste_total_kg"]).fillna(0)
    return frame[["month", "site_id", "product_id", "customer_washed_kg", "laundry_id", *[
        c for c in frame.columns if c.startswith("allocated_")]]]


def load_workbook(source: Path | str = config.DATA_PATH) -> Dataset:
    issues: list[Issue] = []
    sheets = _read_sheets(source, issues)

    sites = sheets["Sites"]
    products = sheets["Products"]
    site_cols = ["site_id", "site_name", "country", "site_type"]
    product_cols = ["product_id", "product_name", "product_group"]

    def enrich(frame: pd.DataFrame) -> pd.DataFrame:
        return frame.merge(sites[site_cols], on="site_id", how="left").merge(
            products[product_cols], on="product_id", how="left"
        )

    activity = sheets["Service_Activity"]
    duplicates = int(activity.duplicated(["month", "site_id", "product_id"]).sum())
    if duplicates:
        issues.append(Issue("warning", "Service_Activity", f"{duplicates} duplicate rows dropped."))
        activity = activity.drop_duplicates(["month", "site_id", "product_id"])
    over_scanned = int((activity["active_assets_scanned"] > activity["allocated_pieces"]).sum())
    if over_scanned:
        issues.append(Issue("warning", "Service_Activity", f"{over_scanned} rows scan more than allocated; gap floored at 0."))
    activity = enrich(activity)
    activity["no_scan_gap"] = (activity["allocated_pieces"] - activity["active_assets_scanned"]).clip(lower=0)

    orphans = int(activity["site_name"].isna().sum() + activity["product_group"].isna().sum())
    if orphans:
        issues.append(Issue("warning", "Service_Activity", f"{orphans} rows reference unknown sites or products."))

    repairs = sheets["Repairs"]
    if not repairs.empty:
        cost = products[["product_id", "synthetic_product_footprint_kgco2e", "synthetic_purchase_cost_eur"]]
        repairs = enrich(repairs).merge(cost, on="product_id", how="left")
    else:
        repairs = repairs.reindex(columns=[*repairs.columns, "site_name", "country", "site_type", "product_name",
                                           "product_group", "synthetic_product_footprint_kgco2e",
                                           "synthetic_purchase_cost_eur"])

    cycles = sheets["Service_Cycles"].copy()
    for col in TIMESTAMPS:
        cycles[col] = pd.to_datetime(cycles[col], errors="coerce")
    cycles = enrich(cycles)
    cycles["month"] = cycles["pickup_timestamp"].dt.strftime("%Y-%m")
    hours = lambda end, start: (cycles[end] - cycles[start]).dt.total_seconds() / 3600
    cycles["pickup_to_laundry_h"] = hours("in_laundry_timestamp", "pickup_timestamp")
    cycles["in_laundry_h"] = hours("out_laundry_timestamp", "in_laundry_timestamp")
    cycles["laundry_to_delivery_h"] = hours("delivered_timestamp", "out_laundry_timestamp")

    waste = sheets["Waste_Circularity"]
    impacts = sheets["Customer_Impacts"]
    if impacts.empty and not sheets["Laundry_Resources"].empty:
        impacts = _derive_impacts(sheets["Service_Activity"], sheets["Laundry_Resources"], waste)
        issues.append(Issue("info", "Customer_Impacts", "Rebuilt from Laundry_Resources by washed-kg share."))
    if not impacts.empty:
        impacts = enrich(impacts.drop(columns=[c for c in ("customer_id",) if c in impacts.columns]))
    else:
        impacts = impacts.reindex(columns=[*impacts.columns, "laundry_id", "site_name", "country", "site_type",
                                           "product_name", "product_group"])
    impacts = impacts.merge(sites[["site_id", "customer_id"]], on="site_id", how="left")

    if sheets["Baselines"].empty:
        issues.append(Issue("warning", "Baselines", "No baseline factors, so savings and comparisons show n/a."))

    months = sorted(activity["month"].astype(str).unique())
    as_of = cycles["delivered_timestamp"].max() if not cycles.empty else None
    if pd.isna(as_of):
        as_of = None

    return Dataset(
        customers=sheets["Customers"],
        sites=sites,
        products=products,
        activity=activity,
        repairs=repairs,
        cycles=cycles,
        impacts=impacts,
        waste=waste,
        baselines=sheets["Baselines"],
        priorities=_sanitize(sheets["Industry_Priorities"]),
        assumptions=_sanitize(sheets["Assumptions"]),
        data_quality=sheets["Data_Quality"],
        prompts=sheets["Suggested_Prompts"],
        months=months,
        as_of=as_of,
        issues=issues,
    )
