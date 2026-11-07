"""Metric calculation, provenance tagging and signal triggers.

Every number leaving this module is a Metric or a frame whose columns state their
provenance, so the UI and the exporter cannot show an unlabelled figure.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

import pandas as pd

import config
from config import CALCULATED, ESTIMATED, MEASURED, TEAM_ASSUMPTION
from loader import STAGES, Dataset, dedupe_wearers

OK, WATCH, HIGH = 0, 1, 2
STATUS_LABEL = {OK: "OK", WATCH: "Watch", HIGH: "High"}


@dataclass(frozen=True)
class Scope:
    customer_id: str
    countries: tuple[str, ...] = ()
    site_ids: tuple[str, ...] = ()
    start: str | None = None
    end: str | None = None
    product_groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class Scenario:
    baseline_id: str = config.DEFAULT_BASELINE
    baseline_level: str = "Base"
    repair_level: str = "Base"
    footprint_level: str = "Base"

    @property
    def baseline_scale(self) -> float:
        return config.SENSITIVITY["baseline_scale"].value(self.baseline_level)

    @property
    def repair_rate(self) -> float:
        return config.SENSITIVITY["repair_rate"].value(self.repair_level)

    @property
    def footprint_scale(self) -> float:
        return config.SENSITIVITY["footprint_scale"].value(self.footprint_level)


@dataclass
class Metric:
    key: str
    label: str
    value: float | None
    unit: str
    tags: tuple[str, ...]
    formula: str
    note: str = ""
    low: float | None = None
    high: float | None = None

    @property
    def text(self) -> str:
        return format_value(self.value, self.unit)

    @property
    def range_text(self) -> str:
        if self.low is None or self.high is None:
            return ""
        return f"{format_value(self.low, self.unit)} to {format_value(self.high, self.unit)}"


def format_value(value: float | None, unit: str) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    if unit == "%":
        return f"{value:.1f}%"
    if unit == "EUR":
        return f"€{value / 1e6:,.2f} million" if abs(value) >= 1e6 else f"€{value:,.0f}"
    if unit == "kgCO2e":
        return f"{value:,.0f} kgCO2e" if abs(value) < 1000 else f"{value / 1000:,.1f} tCO2e"
    if unit == "x":
        return f"{value:.2f}"
    if unit == "h":
        return f"{value:.1f} h"
    if abs(value) >= 1e6:
        return f"{value / 1e6:,.2f} million {unit}".rstrip()
    return f"{value:,.0f} {unit}".rstrip()


# --- Scoping ---------------------------------------------------------------
def scope_sites(ds: Dataset, scope: Scope) -> pd.DataFrame:
    sites = ds.sites[ds.sites["customer_id"] == scope.customer_id]
    if scope.countries:
        sites = sites[sites["country"].isin(scope.countries)]
    if scope.site_ids:
        sites = sites[sites["site_id"].isin(scope.site_ids)]
    return sites


def _slice(ds: Dataset, scope: Scope, frame: pd.DataFrame, by_month: bool = True) -> pd.DataFrame:
    out = frame[frame["site_id"].isin(scope_sites(ds, scope)["site_id"])]
    if by_month:
        start = scope.start or ds.months[0]
        end = scope.end or ds.months[-1]
        out = out[(out["month"] >= start) & (out["month"] <= end)]
    if scope.product_groups:
        out = out[out["product_group"].isin(scope.product_groups)]
    return out


def scope_label(ds: Dataset, scope: Scope) -> str:
    name = customer_row(ds, scope.customer_id)["customer_name"]
    parts = [name]
    if scope.site_ids:
        names = scope_sites(ds, scope)["site_name"].tolist()
        parts.append(names[0] if len(names) == 1 else f"{len(names)} sites")
    elif scope.countries:
        parts.append(f"All {' + '.join(scope.countries)} operations")
    else:
        parts.append("All operations")
    if scope.product_groups:
        parts.append(", ".join(scope.product_groups))
    return " | ".join(parts)


def period_label(ds: Dataset, scope: Scope) -> str:
    start, end = scope.start or ds.months[0], scope.end or ds.months[-1]
    return start if start == end else f"{start} to {end}"


def customer_row(ds: Dataset, customer_id: str) -> pd.Series:
    return ds.customers[ds.customers["customer_id"] == customer_id].iloc[0]


# --- Baselines -------------------------------------------------------------
def baseline_row(ds: Dataset, baseline_id: str) -> pd.Series | None:
    match = ds.baselines[ds.baselines["baseline_id"] == baseline_id]
    return None if match.empty else match.iloc[0]


def baseline_name(ds: Dataset, baseline_id: str) -> str:
    row = baseline_row(ds, baseline_id)
    return "baseline" if row is None else str(row["baseline_name"])


def _impact_totals(impacts: pd.DataFrame) -> dict[str, float]:
    return {
        "kg": impacts["customer_washed_kg"].sum(),
        "water": impacts["allocated_water_l"].sum(),
        "energy": impacts["allocated_energy_kwh"].sum(),
        "co2": impacts["allocated_process_kgco2e"].sum() + impacts["allocated_delivery_kgco2e"].sum(),
        "waste": impacts["allocated_textile_waste_kg"].sum(),
    }


def savings(ds: Dataset, impacts: pd.DataFrame, baseline_id: str, scale: float) -> dict[str, float | None]:
    """Saving = baseline factor x washed kg - allocated Lindström footprint."""
    base = baseline_row(ds, baseline_id)
    totals = _impact_totals(impacts)
    if base is None or impacts.empty:
        return {"water": None, "energy": None, "co2": None}
    co2_factor = base["process_kgco2e_per_kg"] + base["delivery_kgco2e_per_kg"]
    return {
        "water": base["water_l_per_kg"] * scale * totals["kg"] - totals["water"],
        "energy": base["energy_kwh_per_kg"] * scale * totals["kg"] - totals["energy"],
        "co2": co2_factor * scale * totals["kg"] - totals["co2"],
    }


def baseline_comparison(ds: Dataset, scope: Scope, scenario: Scenario) -> pd.DataFrame:
    """Per-kg intensity of the service against every baseline, for trade-off charts."""
    impacts = _slice(ds, scope, ds.impacts)
    totals = _impact_totals(impacts)
    if impacts.empty or totals["kg"] == 0 or ds.baselines.empty:
        return pd.DataFrame()
    scale = scenario.baseline_scale
    rows = [{
        "option": "Lindström circular service", "id": "LINDSTROM",
        "water_l_per_kg": totals["water"] / totals["kg"],
        "energy_kwh_per_kg": totals["energy"] / totals["kg"],
        "co2_kg_per_kg": totals["co2"] / totals["kg"],
    }]
    for _, base in ds.baselines.iterrows():
        rows.append({
            "option": base["baseline_name"], "id": base["baseline_id"],
            "water_l_per_kg": base["water_l_per_kg"] * scale,
            "energy_kwh_per_kg": base["energy_kwh_per_kg"] * scale,
            "co2_kg_per_kg": (base["process_kgco2e_per_kg"] + base["delivery_kgco2e_per_kg"]) * scale,
        })
    return pd.DataFrame(rows)


def sensitivity_table(ds: Dataset, scope: Scope, scenario: Scenario) -> pd.DataFrame:
    """Low/Base/High outcomes for the selected baseline, all assumptions moved together."""
    impacts = _slice(ds, scope, ds.impacts)
    repairs = _slice(ds, scope, ds.repairs)
    rows = []
    for level in config.LEVELS:
        case = Scenario(scenario.baseline_id, level, level, level)
        saved = savings(ds, impacts, case.baseline_id, case.baseline_scale)
        hand = _repair_handprint(repairs, case)
        rows.append({
            "Case": level,
            "Water saved [Estimated] (L)": saved["water"],
            "CO2 saved [Estimated] (kgCO2e)": saved["co2"],
            "Repair-avoided CO2 [Estimated] (kgCO2e)": hand["co2"],
            "Purchase cost avoided [Estimated] (EUR)": hand["eur"],
        })
    return pd.DataFrame(rows)


def _repair_handprint(repairs: pd.DataFrame, scenario: Scenario) -> dict[str, float]:
    postponed = repairs["repair_count"] * scenario.repair_rate
    return {
        "postponed": postponed.sum(),
        "co2": (postponed * repairs["synthetic_product_footprint_kgco2e"] * scenario.footprint_scale).sum(),
        "eur": (postponed * repairs["synthetic_purchase_cost_eur"]).sum(),
    }


# --- Signals ---------------------------------------------------------------
def hygiene_assets(ds: Dataset, scope: Scope) -> pd.DataFrame:
    """Latest scan per traced hygiene-critical garment, measured against the data's end."""
    cycles = _slice(ds, scope, ds.cycles, by_month=False)
    cycles = cycles[cycles["product_group"].isin(config.HYGIENE_GROUPS)]
    if cycles.empty or ds.as_of is None:
        return pd.DataFrame(columns=["asset_id", "site_id", "site_name", "product_group", "last_scan", "days_since_scan"])
    assets = cycles.groupby(["asset_id", "site_id", "site_name", "product_group"], as_index=False).agg(
        last_scan=("delivered_timestamp", "max")
    )
    assets["days_since_scan"] = (ds.as_of - assets["last_scan"]).dt.days
    return assets


def hygiene_summary(ds: Dataset, scope: Scope) -> dict:
    assets = hygiene_assets(ds, scope)
    latest = _slice(ds, scope, ds.activity)
    latest = latest[latest["month"] == latest["month"].max()]
    allocated = latest[latest["product_group"].isin(config.HYGIENE_GROUPS)]["allocated_pieces"].sum()
    flagged = int((assets["days_since_scan"] > config.HYGIENE_DAYS).sum())
    traced = len(assets)
    return {
        "flagged": flagged,
        "traced": traced,
        "allocated": int(allocated),
        "share": flagged / traced * 100 if traced else None,
        "coverage": traced / allocated * 100 if allocated else None,
        "applicable": traced > 0,
    }


def repair_signal(ds: Dataset, scope: Scope) -> pd.DataFrame:
    """Repairs per washed piece by site and product against the product-group median.

    Repairs has no asset_id, so garment-level frequency cannot be calculated. The
    site x product rate is the closest proxy the data supports.
    """
    if ds.repairs.empty:
        return pd.DataFrame()
    portfolio = Scope(scope.customer_id, start=scope.start, end=scope.end)
    start, end = portfolio.start or ds.months[0], portfolio.end or ds.months[-1]
    repairs = ds.repairs[ds.repairs["month"].between(start, end)]
    activity = ds.activity[ds.activity["month"].between(start, end)]
    keys = ["site_id", "site_name", "product_name", "product_group"]
    table = activity.groupby(keys, as_index=False).agg(washed=("washed_pieces", "sum"))
    table = table.merge(repairs.groupby(keys, as_index=False).agg(repairs=("repair_count", "sum")), on=keys, how="left")
    table["repairs"] = table["repairs"].fillna(0)
    table["rate_pct"] = table["repairs"] / table["washed"] * 100
    table["ratio"] = table["rate_pct"] / table.groupby("product_group")["rate_pct"].transform("median")
    table["status"] = table["ratio"].map(
        lambda r: HIGH if r >= config.REPAIR_HIGH else WATCH if r >= config.REPAIR_WATCH else OK
    )
    in_scope = table[table["site_id"].isin(scope_sites(ds, scope)["site_id"])]
    if scope.product_groups:
        in_scope = in_scope[in_scope["product_group"].isin(scope.product_groups)]
    return in_scope.sort_values("ratio", ascending=False)


def site_table(ds: Dataset, scope: Scope, scenario: Scenario) -> pd.DataFrame:
    activity = _slice(ds, scope, ds.activity)
    if activity.empty:
        return pd.DataFrame()
    latest_month = activity["month"].max()
    latest = activity[activity["month"] == latest_month]
    wearers = dedupe_wearers(latest).set_index("site_id")["wearers"]

    table = activity.groupby(["site_id", "site_name", "country", "site_type"], as_index=False).agg(
        allocated_sum=("allocated_pieces", "sum"),
        gap_sum=("no_scan_gap", "sum"),
        washed_kg=("customer_washed_kg", "sum"),
        requests=("service_requests", "sum"),
    )
    table["gap_rate"] = table["gap_sum"] / table["allocated_sum"] * 100
    snapshot = latest.groupby("site_id").agg(allocated=("allocated_pieces", "sum"), gap=("no_scan_gap", "sum"))
    table = table.join(snapshot, on="site_id")
    table["wearers"] = table["site_id"].map(wearers)

    impacts = _slice(ds, scope, ds.impacts)
    rows = {
        site: savings(ds, frame, scenario.baseline_id, scenario.baseline_scale)
        for site, frame in impacts.groupby("site_id")
    }
    table["water_saved"] = table["site_id"].map(lambda s: rows.get(s, {}).get("water"))
    table["co2_saved"] = table["site_id"].map(lambda s: rows.get(s, {}).get("co2"))

    repairs = _slice(ds, scope, ds.repairs)
    table["repairs"] = table["site_id"].map(repairs.groupby("site_id")["repair_count"].sum()).fillna(0)

    cycles = _slice(ds, scope, ds.cycles)
    on_time = (cycles["performance_flag"] == "ON_TIME").groupby(cycles["site_id"]).mean() * 100
    table["on_time"] = table["site_id"].map(on_time)

    assets = hygiene_assets(ds, scope)
    if assets.empty:
        table["hygiene_flagged"], table["hygiene_traced"], table["hygiene_share"] = 0, 0, None
    else:
        grouped = assets.assign(flag=assets["days_since_scan"] > config.HYGIENE_DAYS).groupby("site_id")
        table["hygiene_flagged"] = table["site_id"].map(grouped["flag"].sum()).fillna(0).astype(int)
        table["hygiene_traced"] = table["site_id"].map(grouped.size()).fillna(0).astype(int)
        table["hygiene_share"] = table["hygiene_flagged"] / table["hygiene_traced"].where(table["hygiene_traced"] > 0) * 100

    repair = repair_signal(ds, scope)
    table["repair_ratio"] = (
        table["site_id"].map(repair.groupby("site_id")["ratio"].max()) if not repair.empty else None
    )

    def band(value, watch, high):
        if value is None or pd.isna(value):
            return None
        return HIGH if value >= high else WATCH if value >= watch else OK

    table["fleet_status"] = table["gap_rate"].map(lambda v: band(v, config.FLEET_WATCH * 100, config.FLEET_HIGH * 100))
    table["hygiene_status"] = table["hygiene_share"].map(
        lambda v: band(v, config.HYGIENE_WATCH * 100, config.HYGIENE_HIGH * 100)
    )
    table["repair_status"] = table["repair_ratio"].map(lambda v: band(v, config.REPAIR_WATCH, config.REPAIR_HIGH))
    # Ranked on the full-coverage signals. The hygiene screen sees a sample of the
    # fleet, so it breaks ties but must not outrank a measured outlier.
    status = table[["fleet_status", "repair_status"]].astype(float)
    table["risk_score"] = status.fillna(0).sum(axis=1)
    order = ["risk_score", "fleet_status", "gap_rate", "hygiene_share"]
    return table.sort_values(order, ascending=False, na_position="last").reset_index(drop=True)


# --- Drill-down ------------------------------------------------------------
def breakdown(ds: Dataset, scope: Scope, by: str) -> pd.DataFrame:
    """One drill level. Flows are summed; stocks use the last month in the group."""
    if by == "repair_type":
        repairs = _slice(ds, scope, ds.repairs)
        if repairs.empty:
            return pd.DataFrame()
        out = repairs.groupby("repair_type", as_index=False)["repair_count"].sum()
        out = out.sort_values("repair_count", ascending=False)
        return out.rename(columns={"repair_type": "Repair type", "repair_count": "Repairs [Measured]"})

    if by == "stage":
        cycles = _slice(ds, scope, ds.cycles)
        if cycles.empty:
            return pd.DataFrame()
        return pd.DataFrame([
            {"Delivery stage": label, "Mean hours [Measured]": cycles[col].mean()}
            for col, label in STAGES.items()
        ])

    activity = _slice(ds, scope, ds.activity)
    if activity.empty:
        return pd.DataFrame()
    column = {"site": "site_name", "month": "month", "product_group": "product_group", "product": "product_name"}[by]
    title = {"site": "Site", "month": "Month", "product_group": "Product category", "product": "Product"}[by]

    flows = activity.groupby(column, as_index=False).agg(
        washed_kg=("customer_washed_kg", "sum"),
        washed_pieces=("washed_pieces", "sum"),
        requests=("service_requests", "sum"),
    )
    last = activity[activity["month"] == activity.groupby(column)["month"].transform("max")]
    stocks = last.groupby(column, as_index=False).agg(
        allocated=("allocated_pieces", "sum"), gap=("no_scan_gap", "sum")
    )
    if by in ("product_group", "product"):
        # One category at a time, so nothing overlaps and summing across sites is safe.
        people = last.groupby(column, as_index=False)["active_wearers"].sum().rename(columns={"active_wearers": "wearers"})
    else:
        deduped = last.groupby([column, "site_id", "month"], as_index=False)["active_wearers"].max()
        people = deduped.groupby(column, as_index=False)["active_wearers"].sum().rename(columns={"active_wearers": "wearers"})

    out = flows.merge(stocks, on=column).merge(people, on=column)
    out["gap_rate"] = out["gap"] / out["allocated"] * 100
    wearer_title = "Wearers in category [Measured]" if by in ("product_group", "product") else "Wearers, deduplicated [Calculated]"
    out = out.rename(columns={
        column: title,
        "wearers": wearer_title,
        "allocated": "Allocated pieces [Measured]",
        "gap": f"{config.GAP_METRIC} [Calculated]",
        "gap_rate": "Share of allocated, % [Calculated]",
        "washed_kg": "Washed kg [Measured]",
        "washed_pieces": "Washed pieces [Measured]",
        "requests": "Service requests [Measured]",
    })
    ordered = [title, wearer_title, "Allocated pieces [Measured]", f"{config.GAP_METRIC} [Calculated]",
               "Share of allocated, % [Calculated]", "Washed kg [Measured]", "Washed pieces [Measured]",
               "Service requests [Measured]"]
    return out[ordered].sort_values(title).reset_index(drop=True)


def monthly_trend(ds: Dataset, scope: Scope) -> pd.DataFrame:
    activity = _slice(ds, scope, ds.activity)
    trend = activity.groupby("month", as_index=False).agg(
        allocated=("allocated_pieces", "sum"), gap=("no_scan_gap", "sum"), washed_kg=("customer_washed_kg", "sum")
    )
    trend["gap_rate"] = trend["gap"] / trend["allocated"] * 100
    return trend


def monthly_sum(ds: Dataset, scope: Scope, table: str, column: str) -> pd.DataFrame:
    """Monthly total of one column of the repairs or activity table, within the scope and its period."""
    frame = {"repairs": ds.repairs, "activity": ds.activity}[table]
    if frame.empty:
        return pd.DataFrame(columns=["month", column])
    return _slice(ds, scope, frame).groupby("month", as_index=False)[column].sum()


def monthly_savings(ds: Dataset, scope: Scope, scenario: Scenario) -> pd.DataFrame:
    """Savings per month over the whole data range, with the selected period flagged for charts."""
    impacts = _slice(ds, replace(scope, start=None, end=None), ds.impacts)
    rows = []
    for month, frame in impacts.groupby("month"):
        saved = savings(ds, frame, scenario.baseline_id, scenario.baseline_scale)
        rows.append({"month": month, "water": saved["water"], "energy": saved["energy"], "co2": saved["co2"]})
    out = pd.DataFrame(rows, columns=["month", "water", "energy", "co2"])
    start, end = scope.start or ds.months[0], scope.end or ds.months[-1]
    out["selected"] = out["month"].between(start, end)
    return out


# --- Timeframes and view plans ----------------------------------------------
# A plan is the structured request an assistant (or a rule) hands to the engine. The engine validates it
# against the data and recalculates every figure deterministically; no number comes from the assistant.
TIMEFRAMES = ("full_year", "last_month", "last_30_days", "last_quarter", "last_3_months", "last_6_months",
              "first_half", "second_half", "q1", "q2", "q3", "q4", "month", "custom")
FOCUS_ROLES = {
    "standard": config.STANDARD_ROLE,
    "quality": config.QUALITY_ROLE, "food": config.QUALITY_ROLE, "hygiene": config.QUALITY_ROLE,
    "cfo": config.CFO_ROLE, "finance": config.CFO_ROLE, "procurement": config.CFO_ROLE,
    "esg": config.ESG_ROLE, "sustainability": config.ESG_ROLE,
}
BASELINE_WORDS = {"local": "B_LOCAL", "home": "B_HOME", "inhouse": "B_INHOUSE", "in-house": "B_INHOUSE"}
_MONTH = re.compile(r"^(\d{4})-(\d{1,2})$")


@dataclass(frozen=True)
class Timeframe:
    kind: str
    start: str
    end: str
    label: str
    note: str = ""


def normalise_month(value) -> str | None:
    match = _MONTH.match(str(value or "").strip())
    if not match:
        return None
    year, month = int(match[1]), int(match[2])
    return f"{year:04d}-{month:02d}" if 1 <= month <= 12 else None


def resolve_timeframe(months: list[str], kind: str, month=None, start_month=None, end_month=None) -> Timeframe | None:
    """Turn a named timeframe into data months. The data is monthly, so 'last 30 days' means the latest month."""
    months = sorted(months)
    if not months or kind not in TIMEFRAMES:
        return None
    first, last = months[0], months[-1]
    year, last_number = int(last[:4]), int(last[5:7])

    def pick(selected, label, note="") -> Timeframe | None:
        chosen = [m for m in months if m in set(selected)]
        return Timeframe(kind, chosen[0], chosen[-1], label, note) if chosen else None

    def in_year(numbers, in_year_=year) -> list[str]:
        return [m for m in months if int(m[:4]) == in_year_ and int(m[5:7]) in set(numbers)]

    if kind == "full_year":
        return pick(months, "Full Year" if len(months) == 12 else f"{first} to {last}")
    if kind == "last_month":
        return pick([last], f"Last month ({last})")
    if kind == "last_30_days":
        return pick([last], f"Last 30 days ({last})",
                    f"Data is monthly, so the latest month ({last}) is the closest period to the last 30 days.")
    if kind in ("last_3_months", "last_6_months"):
        chosen = months[-(3 if kind == "last_3_months" else 6):]
        return pick(chosen, f"Last {len(chosen)} months ({chosen[0]} to {chosen[-1]})")
    if kind == "last_quarter":
        quarter, quarter_year = (last_number - 1) // 3 + 1, year
        if last_number % 3:
            quarter -= 1
        if quarter == 0:
            quarter, quarter_year = 4, year - 1
        return pick(in_year(range(3 * quarter - 2, 3 * quarter + 1), quarter_year), f"Last quarter (Q{quarter} {quarter_year})")
    if kind == "first_half":
        return pick(in_year(range(1, 7)), f"First half {year}")
    if kind == "second_half":
        return pick(in_year(range(7, 13)), f"Second half {year}")
    if kind in ("q1", "q2", "q3", "q4"):
        n = int(kind[1])
        return pick(in_year(range(3 * n - 2, 3 * n + 1)), f"Q{n} {year}")
    if kind == "month":
        single = normalise_month(month)
        return pick([single], f"Month {single}") if single in months else None
    start, end = normalise_month(start_month) or first, normalise_month(end_month) or last
    if not (normalise_month(start_month) or normalise_month(end_month)):
        return None
    start, end = min(start, end), max(start, end)
    chosen = [m for m in months if start <= m <= end]
    return pick(chosen, f"{chosen[0]} to {chosen[-1]}") if chosen else None


@dataclass
class ViewPlan:
    """What a request wants changed in the dashboard. None means 'leave as it is'."""
    reset: bool = False
    timeframe: Timeframe | None = None
    role: str | None = None
    countries: tuple[str, ...] | None = None
    site_ids: tuple[str, ...] | None = None
    product_groups: tuple[str, ...] | None = None
    baseline_id: str | None = None
    source: str = "rules"
    labels: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    topic: str = ""  # what the request is about, which picks the focused view; it changes no figure

    @property
    def changes(self) -> bool:
        return self.reset or any(v is not None for v in (
            self.timeframe, self.role, self.countries, self.site_ids, self.product_groups, self.baseline_id))

    def describe(self) -> str:
        return "; ".join(self.labels) if self.labels else "no change to the dashboard"


def _match_names(wanted, options: list[str], notes: list[str], what: str) -> tuple[str, ...]:
    """Case-insensitive matching of requested names to real ones. Unknown names are reported, never invented."""
    found, lowered = [], {o.lower(): o for o in options}
    for name in wanted or []:
        key = str(name).strip().lower()
        hit = lowered.get(key) or next((o for low, o in lowered.items() if key and (key in low or low.endswith(key))), None)
        if hit and hit not in found:
            found.append(hit)
        elif not hit and key:
            notes.append(f"Ignored unknown {what} '{name}'.")
    return tuple(found)


def plan_from_params(ds: Dataset, customer_id: str, params: dict, source: str = "rules") -> ViewPlan:
    """Validate raw parameters (from an assistant or a rule) against this customer's data."""
    topic = str(params.get("topic") or "").strip().lower()
    plan = ViewPlan(source=source, topic=topic if topic in config.FOCUS_TOPICS else "")
    if params.get("change_view") is False:
        return plan
    sites = ds.sites[ds.sites["customer_id"] == customer_id]
    groups = sorted(ds.activity[ds.activity["customer_id"] == customer_id]["product_group"].dropna().unique())

    if params.get("reset"):
        plan.reset = True
        plan.labels.append("reset to the default view")

    kind = params.get("timeframe")
    if kind:
        timeframe = resolve_timeframe(ds.months, str(kind), params.get("month"),
                                      params.get("start_month"), params.get("end_month"))
        if timeframe:
            plan.timeframe = timeframe
            plan.labels.append(timeframe.label)
            if timeframe.note:
                plan.notes.append(timeframe.note)
        else:
            plan.notes.append(f"Ignored a timeframe the data cannot answer ('{kind}').")

    focus = str(params.get("focus") or "").strip().lower()
    if focus and focus not in ("none", "keep"):
        role = FOCUS_ROLES.get(focus)
        if role:
            plan.role = role
            plan.labels.append(f"focus: {role}")
        else:
            plan.notes.append(f"Ignored unknown focus '{focus}'.")

    if params.get("countries"):
        plan.countries = _match_names(params["countries"], sorted(sites["country"].unique()), plan.notes, "country") or None
        if plan.countries:
            plan.labels.append(" + ".join(plan.countries))
    if params.get("sites"):
        names = dict(zip(sites["site_name"], sites["site_id"]))
        matched = _match_names(params["sites"], list(names), plan.notes, "site")
        if matched:
            plan.site_ids = tuple(names[n] for n in matched)
            plan.labels.append(", ".join(matched))
    if params.get("product_groups"):
        plan.product_groups = _match_names(params["product_groups"], groups, plan.notes, "product category") or None
        if plan.product_groups:
            plan.labels.append(", ".join(plan.product_groups))

    baseline = str(params.get("baseline") or "").strip().lower()
    if baseline and baseline not in ("none", "keep"):
        baseline_id = BASELINE_WORDS.get(baseline, baseline.upper() if baseline.upper() in set(ds.baselines["baseline_id"]) else None)
        if baseline_id and baseline_id in set(ds.baselines["baseline_id"]):
            plan.baseline_id = baseline_id
            plan.labels.append(f"baseline: {baseline_name(ds, baseline_id)}")
        else:
            plan.notes.append(f"Ignored unknown baseline '{baseline}'.")
    return plan


def apply_plan(scope: Scope, scenario: Scenario, role: str, plan: ViewPlan,
               default_role: str | None = None) -> tuple[Scope, Scenario, str]:
    """The new scope, scenario and role after a plan. Pure: the same inputs always give the same view."""
    if plan.reset:
        scope, scenario, role = Scope(scope.customer_id), Scenario(), default_role or role
    if plan.timeframe:
        scope = replace(scope, start=plan.timeframe.start, end=plan.timeframe.end)
    if plan.countries is not None:
        scope = replace(scope, countries=plan.countries)
    if plan.site_ids is not None:
        scope = replace(scope, site_ids=plan.site_ids)
    if plan.product_groups is not None:
        scope = replace(scope, product_groups=plan.product_groups)
    if plan.baseline_id:
        scenario = replace(scenario, baseline_id=plan.baseline_id)
    if plan.role:
        role = plan.role
    return scope, scenario, role


# --- Roll-up ---------------------------------------------------------------
def rollup(ds: Dataset, scope: Scope, scenario: Scenario) -> dict[str, Metric]:
    activity = _slice(ds, scope, ds.activity)
    repairs = _slice(ds, scope, ds.repairs)
    cycles = _slice(ds, scope, ds.cycles)
    impacts = _slice(ds, scope, ds.impacts)
    sites = scope_sites(ds, scope)
    latest_month = activity["month"].max() if not activity.empty else None
    latest = activity[activity["month"] == latest_month]

    deduped = dedupe_wearers(activity).groupby("month")["wearers"].sum()
    wearers = deduped.get(latest_month) if latest_month else None
    allocated = latest["allocated_pieces"].sum()
    gap = latest["no_scan_gap"].sum()
    gap_cost = (latest["no_scan_gap"] * latest["product_id"].map(
        ds.products.set_index("product_id")["synthetic_purchase_cost_eur"])).sum()

    saved = savings(ds, impacts, scenario.baseline_id, scenario.baseline_scale)
    base_low = savings(ds, impacts, scenario.baseline_id, config.SENSITIVITY["baseline_scale"].low)
    base_high = savings(ds, impacts, scenario.baseline_id, config.SENSITIVITY["baseline_scale"].high)
    hand = _repair_handprint(repairs, scenario)
    hand_low = _repair_handprint(repairs, Scenario(repair_level="Low", footprint_level="Low"))
    hand_high = _repair_handprint(repairs, Scenario(repair_level="High", footprint_level="High"))
    hygiene = hygiene_summary(ds, scope)
    totals = _impact_totals(impacts)
    vs = baseline_name(ds, scenario.baseline_id)
    scale_tag = (ESTIMATED, TEAM_ASSUMPTION) if scenario.baseline_level != "Base" else (ESTIMATED,)

    circular = None
    if not impacts.empty and not ds.waste.empty:
        waste = ds.waste.assign(share=(ds.waste["recycling_kg"] + ds.waste["reuse_kg"]) / ds.waste["textile_waste_total_kg"])
        joined = impacts.merge(waste[["month", "laundry_id", "share"]], on=["month", "laundry_id"], how="left")
        if totals["waste"]:
            circular = (joined["allocated_textile_waste_kg"] * joined["share"]).sum() / totals["waste"] * 100

    on_time = (cycles["performance_flag"] == "ON_TIME").mean() * 100 if len(cycles) else None
    mean_cycle = cycles["cycle_hours"].mean() if len(cycles) else None
    washed_pieces = activity["washed_pieces"].sum()
    has_repairs = not repairs.empty
    gap_rate = gap / allocated * 100 if allocated else None

    metric = [
        Metric("sites", "Delivery sites", float(len(sites)), "sites", (MEASURED,), "Count of delivery sites in Sites master data."),
        Metric("wearers", "Active wearers, deduplicated", wearers, "wearers", (CALCULATED,),
               "Largest product category per site, then summed across sites. Never summed across categories.",
               note=f"Latest month {latest_month}. Range is the lowest and highest month in the period.",
               low=deduped.min() if len(deduped) else None, high=deduped.max() if len(deduped) else None),
        Metric("allocated", "Garments allocated", allocated, "pieces", (MEASURED,), f"Allocated pieces in {latest_month}."),
        Metric("washed_kg", "Washed textile", activity["customer_washed_kg"].sum(), "kg", (MEASURED,), "Sum of customer_washed_kg."),
        Metric("washed_pieces", "Washed pieces", washed_pieces, "pieces", (MEASURED,), "Sum of washed_pieces."),
        Metric("service_requests", "Service requests", activity["service_requests"].sum(), "requests", (MEASURED,), "Sum of service_requests."),
        Metric("repairs", "Repairs completed", repairs["repair_count"].sum() if has_repairs else None, "repairs", (MEASURED,), "Sum of repair_count."),
        Metric("on_time", "Service cycles on time", on_time, "%", (MEASURED,), "ON_TIME cycles / all cycles in the log (late means over 120 h)."),
        Metric("mean_cycle", "Mean service cycle", mean_cycle, "h", (MEASURED,), "Mean cycle_hours, pickup to delivery."),
        Metric("gap", config.GAP_METRIC, gap, "pieces", (CALCULATED,),
               f"Allocated pieces - active assets scanned, {latest_month}.",
               note=f"{config.GAP_FRAMING}: {format_value(gap_rate, '%')} of allocated. May reflect {config.GAP_REASONS}."),
        Metric("gap_value", "Purchase value of those garments", gap_cost, "EUR", (ESTIMATED,),
               f"({config.GAP_METRIC}) x synthetic purchase cost per product.",
               note=f"{config.GAP_FRAMING}. Not a saving or a write-off."),
        Metric("garments_per_wearer", "Allocated garments per wearer", allocated / wearers if wearers else None, "x", (CALCULATED,),
               "Allocated pieces / deduplicated wearers."),
        Metric("water_saved", f"Water saved vs {vs}", saved["water"], "L", scale_tag,
               "Baseline L/kg x washed kg - allocated Lindström water.",
               low=base_low["water"], high=base_high["water"], note="Range is the TEAM_ASSUMPTION Low to High baseline scale."),
        Metric("energy_saved", f"Energy saved vs {vs}", saved["energy"], "kWh", scale_tag,
               "Baseline kWh/kg x washed kg - allocated Lindström energy."),
        Metric("co2_saved", f"Avoided CO2 vs {vs}", saved["co2"], "kgCO2e", scale_tag,
               "Baseline process + delivery kgCO2e/kg x washed kg - allocated Lindström emissions.",
               low=base_low["co2"], high=base_high["co2"], note="Range is the TEAM_ASSUMPTION Low to High baseline scale."),
        Metric("footprint_co2", "Carbon footprint of the service (Scope 3 view)",
               totals["co2"] if not impacts.empty else None, "kgCO2e", (ESTIMATED,),
               "Allocated laundry process + delivery emissions, by share of washed kg (workbook allocation).",
               note="Garment production is not included. Decision support, not audited GHG reporting."),
        Metric("postponed", "Replacements postponed", hand["postponed"] if has_repairs else None, "events", (ESTIMATED, TEAM_ASSUMPTION),
               f"Repairs x {scenario.repair_rate:.2f} replacements per repair (workbook A002 is 1.00).",
               note=config.SENSITIVITY["repair_rate"].rationale),
        Metric("avoided_eur", "Purchase cost avoided by repairs", hand["eur"] if has_repairs else None, "EUR", (ESTIMATED, TEAM_ASSUMPTION),
               "Replacements postponed x synthetic purchase cost per product.",
               low=hand_low["eur"] if has_repairs else None, high=hand_high["eur"] if has_repairs else None,
               note="Range is the TEAM_ASSUMPTION Low to High repair rate. Not an audited avoided-purchase claim."),
        Metric("avoided_co2", "Avoided product CO2 from repairs", hand["co2"] if has_repairs else None, "kgCO2e", (ESTIMATED, TEAM_ASSUMPTION),
               "Replacements postponed x synthetic product footprint.",
               low=hand_low["co2"] if has_repairs else None, high=hand_high["co2"] if has_repairs else None,
               note="Range is the TEAM_ASSUMPTION Low to High repair and footprint scales."),
        Metric("textile_waste", "Textile waste attributed", totals["waste"] if not impacts.empty else None, "kg", (ESTIMATED,),
               "Laundry waste allocated by share of washed kg (workbook A003)."),
        Metric("circular_rate", "Circular recycling rate", circular, "%", (ESTIMATED,),
               "(Recycling + reuse kg) / total textile waste, weighted by attributed waste.",
               note="Laundry-level routes allocated to the customer, not customer-measured."),
        Metric("hygiene_flagged", f"Traced garments with no scan for over {config.HYGIENE_DAYS} days",
               float(hygiene["flagged"]) if hygiene["applicable"] else None, "garments", (CALCULATED, TEAM_ASSUMPTION),
               "Traced Food Industry and Healthcare garments whose last scan is more than "
               f"{config.HYGIENE_DAYS} days before {ds.as_of.date() if ds.as_of is not None else 'n/a'}.",
               note=(f"Screening flag, low confidence: {hygiene['flagged']:,} of {hygiene['traced']:,} traced garments. "
                     "A follow-up prompt, not a finding of a hygiene failure.")
                    if hygiene["applicable"] else "No hygiene-critical garments in this scope."),
        Metric("hygiene_coverage", "Hygiene garments traced in cycle log", hygiene["coverage"], "%", (CALCULATED,),
               "Traced hygiene garments / allocated hygiene garments.",
               note="How much of the fleet the screening flag can see. Read it alongside the flag."),
    ]
    return {m.key: m for m in metric}


@dataclass
class ReviewContext:
    scope: Scope
    scenario: Scenario
    role: str
    customer: pd.Series
    label: str
    period: str
    baseline: str
    metrics: dict[str, Metric]
    sites: pd.DataFrame
    baselines: pd.DataFrame
    sensitivity: pd.DataFrame
    repair: pd.DataFrame
    hygiene: dict
    stages: pd.DataFrame = field(default_factory=pd.DataFrame)


def risk_metric(sites: pd.DataFrame, hygiene: dict, months_in_period: int = 12) -> Metric:
    """Count of site-level signals at or above Watch. The low-confidence hygiene screen is reported, not counted."""
    formula = ("Sites at or above the Watch band for the fleet allocation signal, plus site and product pairs at "
               "or above Watch for the repair signal. Bands are TEAM_ASSUMPTION thresholds.")
    if sites.empty:
        return Metric("risk_signals", "Active risk signals", None, "signals", (CALCULATED, TEAM_ASSUMPTION), formula)
    fleet = int((pd.to_numeric(sites["fleet_status"], errors="coerce") >= WATCH).sum())
    repair = int((pd.to_numeric(sites["repair_status"], errors="coerce") >= WATCH).sum())
    note = f"{fleet} fleet allocation and {repair} repair signals across {len(sites)} sites."
    if hygiene["applicable"]:
        note += (f" The hygiene screen ({hygiene['flagged']:,} traced garments, low confidence) is shown "
                 "separately and not counted.")
    if months_in_period < 3:
        note += (f" Short period ({months_in_period} month{'s' if months_in_period != 1 else ''}): the thresholds were "
                 "set on the full year, so short periods vary more and flag more sites. Compare with the full year "
                 "before calling it a trend.")
    return Metric("risk_signals", "Active risk signals", float(fleet + repair), "signals",
                  (CALCULATED, TEAM_ASSUMPTION), formula, note=note)


def build_context(ds: Dataset, scope: Scope, scenario: Scenario, role: str) -> ReviewContext:
    sites = site_table(ds, scope, scenario)
    hygiene = hygiene_summary(ds, scope)
    metrics = rollup(ds, scope, scenario)
    start, end = scope.start or ds.months[0], scope.end or ds.months[-1]
    metrics["risk_signals"] = risk_metric(sites, hygiene, len([m for m in ds.months if start <= m <= end]))
    return ReviewContext(
        scope=scope,
        scenario=scenario,
        role=role,
        customer=customer_row(ds, scope.customer_id),
        label=scope_label(ds, scope),
        period=period_label(ds, scope),
        baseline=baseline_name(ds, scenario.baseline_id),
        metrics=metrics,
        sites=sites,
        baselines=baseline_comparison(ds, scope, scenario),
        sensitivity=sensitivity_table(ds, scope, scenario),
        repair=repair_signal(ds, scope),
        hygiene=hygiene,
        stages=breakdown(ds, scope, "stage"),
    )
