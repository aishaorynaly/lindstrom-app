"""Data query tools for the Insights assistant.

The assistant never reads the workbook itself. It calls these functions, which recompute every figure through
the engine, so each number it quotes comes from data.xlsx with its provenance tag. Bad names or timeframes
return an error listing the valid options; they never quietly widen the query.
"""
import functools
import json
from typing import Optional

import pandas as pd

import config
import engine
from engine import Scenario, Scope
from loader import Dataset

KPI_KEYS = ("allocated", "wearers", "washed_kg", "washed_pieces", "service_requests", "repairs", "on_time",
            "mean_cycle", "gap", "gap_value", "garments_per_wearer", "water_saved", "energy_saved", "co2_saved",
            "footprint_co2", "postponed", "avoided_eur", "avoided_co2", "textile_waste", "circular_rate",
            "risk_signals")
SITE_COLUMNS = {
    "allocated_garments_with_no_scan_activity_share_pct": "gap_rate",
    "allocated_garments_with_no_scan_activity": "gap",
    "allocated_garments": "allocated",
    "wearers_deduplicated": "wearers",
    "washed_kg": "washed_kg",
    "water_saved_l": "water_saved",
    "co2_saved_kg": "co2_saved",
    "repairs": "repairs",
    "on_time_pct": "on_time",
    "hygiene_screen_share_pct": "hygiene_share",
    "repair_rate_vs_product_median": "repair_ratio",
}
SERIES = {  # metric -> (provenance tags, unit)
    "water_saved_l": ((config.ESTIMATED,), "L"),
    "energy_saved_kwh": ((config.ESTIMATED,), "kWh"),
    "co2_saved_kg": ((config.ESTIMATED,), "kgCO2e"),
    "washed_kg": ((config.MEASURED,), "kg"),
    "allocated_garments_with_no_scan_activity_share_pct": ((config.CALCULATED,), "%"),
    "repairs": ((config.MEASURED,), "repairs"),
    "service_requests": ((config.MEASURED,), "requests"),
}
BREAKDOWNS = {"site": "site", "month": "month", "product_group": "product_group", "product": "product",
              "repair_type": "repair_type", "delivery_stage": "stage"}
COMMON_ARGS = """
        timeframe: Optional named period: full_year, last_month, last_30_days, last_quarter, last_3_months,
            last_6_months, first_half, second_half, q1, q2, q3, q4, month, custom. Leave empty for the full year.
            The data is monthly, so last_month and last_30_days both mean the latest month.
        month: With timeframe "month", the month as YYYY-MM.
        start_month: With timeframe "custom", the first month as YYYY-MM.
        end_month: With timeframe "custom", the last month as YYYY-MM.
        countries: Optional country names to include. Leave empty for all countries.
        sites: Optional delivery site names to include, for example "NorthStar Oulu". Leave empty for all sites.
        product_groups: Optional product categories to include. Leave empty for all categories.
        baseline: Optional comparison baseline for savings: local, home or inhouse. Leave empty for the one in view.
"""


def _clean(value):
    """JSON-safe values with sensible rounding."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, float):
        return None if value != value else round(value, 2 if abs(value) < 100 else 0)
    if hasattr(value, "item"):
        return _clean(value.item())
    return value


def _records(frame: pd.DataFrame, limit: int = 60) -> list[dict]:
    return _clean(frame.head(limit).to_dict("records"))


def tool(function):
    """Share the argument docs, never raise, and record what was queried."""
    @functools.wraps(function)
    def wrapper(self, *args, **kwargs):
        try:
            return function(self, *args, **kwargs)
        except Exception as exc:  # a failing query is reported to the assistant, not raised into the app
            return self._record(function.__name__, kwargs, {"error": f"{type(exc).__name__}: {exc}"})

    if function.__doc__ and "{COMMON}" in function.__doc__:
        wrapper.__doc__ = function.__doc__.replace("{COMMON}", COMMON_ARGS)
    return wrapper


class DataTools:
    def __init__(self, ds: Dataset, scope: Scope, scenario: Scenario, role: str):
        self.ds, self.customer_id = ds, scope.customer_id
        self.scenario, self.role = scenario, role
        self.calls: list[dict] = []     # what was queried, for the interface
        self.outputs: list[str] = []    # everything returned, so figures in answers can be traced

    @property
    def functions(self) -> list:
        return [self.list_options, self.get_kpis, self.get_site_table, self.get_monthly_series,
                self.get_breakdown, self.get_signals, self.get_reference]

    # --- plumbing -----------------------------------------------------------
    def _options(self) -> dict:
        sites = self.ds.sites[self.ds.sites["customer_id"] == self.customer_id]
        groups = sorted(self.ds.activity["product_group"].dropna().unique())
        return {
            "months": self.ds.months, "countries": sorted(sites["country"].unique()),
            "sites": sorted(sites["site_name"]), "product_groups": groups,
            "timeframes": list(engine.TIMEFRAMES), "baselines": ["local", "home", "inhouse"],
        }

    def _record(self, name: str, args: dict, result: dict) -> dict:
        shown = {k: v for k, v in args.items() if v not in (None, "", [], ())}
        self.calls.append({"tool": name, "args": shown, "error": result.get("error")})
        self.outputs.append(json.dumps(result, ensure_ascii=False, default=str))
        return result

    def _resolve(self, name: str, args: dict):
        """(scope, scenario, notes) for the requested slice, or an error result."""
        params = {"change_view": True, "timeframe": args.get("timeframe") or None, "month": args.get("month"),
                  "start_month": args.get("start_month"), "end_month": args.get("end_month"),
                  "countries": args.get("countries") or None, "sites": args.get("sites") or None,
                  "product_groups": args.get("product_groups") or None, "baseline": args.get("baseline") or None}
        plan = engine.plan_from_params(self.ds, self.customer_id, params, source="tool")
        problems = [n for n in plan.notes if n.startswith("Ignored")]
        if problems:
            return None, None, self._record(name, args, {"error": " ".join(problems), "valid_options": self._options()})
        scope, scenario, _ = engine.apply_plan(Scope(self.customer_id), self.scenario, self.role, plan)
        return scope, scenario, [n for n in plan.notes if not n.startswith("Ignored")]

    def _finish(self, name: str, args: dict, scope: Scope, scenario: Scenario, notes: list[str], payload: dict) -> dict:
        result = {
            "period": engine.period_label(self.ds, scope), "scope": engine.scope_label(self.ds, scope),
            "baseline": engine.baseline_name(self.ds, scenario.baseline_id), **payload,
        }
        if notes:
            result["notes"] = notes
        return self._record(name, args, _clean(result))

    # --- tools -------------------------------------------------------------------
    @tool
    def get_reference(self, topic: str) -> dict:
        """Reference tables of the workbook. topic is one of: customer, sites, products, baselines,
        industry_priorities, assumptions, data_quality. Use it for facts such as a site's city or type, garment
        purchase costs and footprints, baseline factors, or what an assumption says.
        """
        ds, customer = self.ds, self.customer_id
        frames = {
            "customer": ds.customers[ds.customers["customer_id"] == customer],
            "sites": ds.sites[ds.sites["customer_id"] == customer],
            "products": ds.products, "baselines": ds.baselines, "assumptions": ds.assumptions,
            "data_quality": ds.data_quality,
            "industry_priorities": ds.priorities[ds.priorities["industry"] == engine.customer_row(ds, customer)["industry"]],
        }
        if topic not in frames:
            return self._record("get_reference", {"topic": topic}, {"error": f"Unknown topic '{topic}'.", "valid_options": list(frames)})
        return self._record("get_reference", {"topic": topic}, _clean({"topic": topic, "rows": _records(frames[topic], 200)}))

    @tool
    def list_options(self) -> dict:
        """List the months, countries, delivery sites, product categories, baselines and timeframes that exist
        in this client's data. Use it to find the exact names to pass to the other tools."""
        return self._record("list_options", {}, self._options())

    @tool
    def get_kpis(self, timeframe: str = "", month: str = "", start_month: str = "", end_month: str = "",
                 countries: Optional[list[str]] = None, sites: Optional[list[str]] = None,
                 product_groups: Optional[list[str]] = None, baseline: str = "") -> dict:
        """Get the headline figures for any slice of the client's data: garments allocated, allocated garments
        with no scan activity, washed kg, repairs, water, energy and CO2 saved against a baseline, carbon
        footprint of the service, repair savings, textile waste, service reliability and active risk signals.
        Every figure carries its provenance tag.

        Args:{COMMON}
        """
        args = dict(timeframe=timeframe, month=month, start_month=start_month, end_month=end_month,
                    countries=countries, sites=sites, product_groups=product_groups, baseline=baseline)
        scope, scenario, extra = self._resolve("get_kpis", args)
        if scope is None:
            return extra
        ctx = engine.build_context(self.ds, scope, scenario, self.role)
        metrics = {}
        for key in KPI_KEYS:
            m = ctx.metrics[key]
            entry = {"label": m.label, "value": m.text, "raw": m.value, "provenance": list(m.tags)}
            if m.range_text:
                entry["range_low_to_high"] = m.range_text
            if m.note and key in ("gap", "risk_signals", "footprint_co2", "hygiene_flagged"):
                entry["note"] = m.note
            metrics[key] = entry
        return self._finish("get_kpis", args, scope, scenario, extra, {"metrics": metrics})

    @tool
    def get_site_table(self, sort_by: str = "allocated_garments_with_no_scan_activity_share_pct", top_n: int = 10,
                       descending: bool = True, timeframe: str = "", month: str = "", start_month: str = "",
                       end_month: str = "", countries: Optional[list[str]] = None,
                       sites: Optional[list[str]] = None, product_groups: Optional[list[str]] = None,
                       baseline: str = "") -> dict:
        """Rank delivery sites on one measure and return the top rows with their other figures, signal
        status and provenance. sort_by is one of: allocated_garments_with_no_scan_activity_share_pct,
        allocated_garments_with_no_scan_activity, allocated_garments, wearers_deduplicated, washed_kg,
        water_saved_l, co2_saved_kg, repairs, on_time_pct, hygiene_screen_share_pct,
        repair_rate_vs_product_median. top_n is the number of sites to return (at most 30).

        Args:{COMMON}
        """
        args = dict(sort_by=sort_by, top_n=top_n, descending=descending, timeframe=timeframe, month=month,
                    start_month=start_month, end_month=end_month, countries=countries, sites=sites,
                    product_groups=product_groups, baseline=baseline)
        if sort_by not in SITE_COLUMNS:
            return self._record("get_site_table", args, {"error": f"Unknown sort_by '{sort_by}'.",
                                                         "valid_options": list(SITE_COLUMNS)})
        scope, scenario, extra = self._resolve("get_site_table", args)
        if scope is None:
            return extra
        table = engine.site_table(self.ds, scope, scenario)
        if table.empty:
            return self._finish("get_site_table", args, scope, scenario, extra, {"sites": []})
        table = table.sort_values(SITE_COLUMNS[sort_by], ascending=not descending, na_position="last")
        rows = []
        for _, row in table.head(max(1, min(int(top_n), 30))).iterrows():
            entry = {"site": row["site_name"], "country": row["country"], "type": row["site_type"]}
            entry.update({label: row[column] for label, column in SITE_COLUMNS.items()})
            entry["fleet_allocation_signal"] = engine.STATUS_LABEL.get(row["fleet_status"]) if pd.notna(row["fleet_status"]) else None
            entry["repair_signal"] = engine.STATUS_LABEL.get(row["repair_status"]) if pd.notna(row["repair_status"]) else None
            rows.append(entry)
        provenance = {"allocated_garments*": [config.MEASURED], "wearers_deduplicated": [config.CALCULATED],
                      "allocated_garments_with_no_scan_activity*": [config.CALCULATED],
                      "washed_kg, repairs, on_time_pct": [config.MEASURED],
                      "water_saved_l, co2_saved_kg": [config.ESTIMATED]}
        return self._finish("get_site_table", args, scope, scenario, extra,
                            {"sorted_by": sort_by, "provenance": provenance, "sites": rows,
                             "sites_in_scope": len(table)})

    @tool
    def get_monthly_series(self, metric: str, timeframe: str = "", month: str = "", start_month: str = "",
                           end_month: str = "", countries: Optional[list[str]] = None,
                           sites: Optional[list[str]] = None, product_groups: Optional[list[str]] = None,
                           baseline: str = "") -> dict:
        """Get one measure month by month, to answer questions about trends or a specific month. metric is one of:
        water_saved_l, energy_saved_kwh, co2_saved_kg, washed_kg,
        allocated_garments_with_no_scan_activity_share_pct, repairs, service_requests.

        Args:{COMMON}
        """
        args = dict(metric=metric, timeframe=timeframe, month=month, start_month=start_month, end_month=end_month,
                    countries=countries, sites=sites, product_groups=product_groups, baseline=baseline)
        if metric not in SERIES:
            return self._record("get_monthly_series", args, {"error": f"Unknown metric '{metric}'.",
                                                             "valid_options": list(SERIES)})
        scope, scenario, extra = self._resolve("get_monthly_series", args)
        if scope is None:
            return extra
        if metric in ("water_saved_l", "energy_saved_kwh", "co2_saved_kg"):
            frame = engine.monthly_savings(self.ds, scope, scenario)
            frame = frame[frame["selected"]]
            values = frame[{"water_saved_l": "water", "energy_saved_kwh": "energy", "co2_saved_kg": "co2"}[metric]]
        elif metric in ("washed_kg", "allocated_garments_with_no_scan_activity_share_pct"):
            frame = engine.monthly_trend(self.ds, scope)
            values = frame["washed_kg" if metric == "washed_kg" else "gap_rate"]
        else:
            column, table = ("repair_count", "repairs") if metric == "repairs" else ("service_requests", "activity")
            frame = engine.monthly_sum(self.ds, scope, table, column)
            values = frame[column]
        series = [{"month": m, "value": v} for m, v in zip(frame["month"], values)]
        tags, unit = SERIES[metric]
        return self._finish("get_monthly_series", args, scope, scenario, extra,
                            {"metric": metric, "unit": unit, "provenance": list(tags), "series": series})

    @tool
    def get_breakdown(self, by: str, timeframe: str = "", month: str = "", start_month: str = "",
                      end_month: str = "", countries: Optional[list[str]] = None,
                      sites: Optional[list[str]] = None, product_groups: Optional[list[str]] = None,
                      baseline: str = "") -> dict:
        """Break the data down along one dimension. by is one of: site, month, product_group, product, repair_type,
        delivery_stage. repair_type gives completed repairs by type, delivery_stage gives mean hours per stage of
        the service cycle, the others give wearers, allocated garments, allocated garments with no scan
        activity, washed kg and service requests. Column names carry their provenance tags.

        Args:{COMMON}
        """
        args = dict(by=by, timeframe=timeframe, month=month, start_month=start_month, end_month=end_month,
                    countries=countries, sites=sites, product_groups=product_groups, baseline=baseline)
        if by not in BREAKDOWNS:
            return self._record("get_breakdown", args, {"error": f"Unknown dimension '{by}'.",
                                                        "valid_options": list(BREAKDOWNS)})
        scope, scenario, extra = self._resolve("get_breakdown", args)
        if scope is None:
            return extra
        frame = engine.breakdown(self.ds, scope, BREAKDOWNS[by])
        return self._finish("get_breakdown", args, scope, scenario, extra, {"by": by, "rows": _records(frame)})

    @tool
    def get_signals(self, timeframe: str = "", month: str = "", start_month: str = "", end_month: str = "",
                    countries: Optional[list[str]] = None, sites: Optional[list[str]] = None,
                    product_groups: Optional[list[str]] = None, baseline: str = "") -> dict:
        """Get the operational risk and optimization signals: sites with a high share of allocated garments with
        no scan activity (an optimization signal for investigation), site and product pairs with a high repair
        rate (repair versus retirement), and the hygiene scan screen with its coverage and confidence.

        Args:{COMMON}
        """
        args = dict(timeframe=timeframe, month=month, start_month=start_month, end_month=end_month,
                    countries=countries, sites=sites, product_groups=product_groups, baseline=baseline)
        scope, scenario, extra = self._resolve("get_signals", args)
        if scope is None:
            return extra
        ctx = engine.build_context(self.ds, scope, scenario, self.role)
        fleet = ctx.sites[pd.to_numeric(ctx.sites["fleet_status"], errors="coerce") >= engine.WATCH]
        repair = ctx.repair[ctx.repair["status"] >= engine.WATCH] if not ctx.repair.empty else ctx.repair
        payload = {
            "active_risk_signals": {"value": ctx.metrics["risk_signals"].text, "note": ctx.metrics["risk_signals"].note,
                                    "provenance": list(ctx.metrics["risk_signals"].tags)},
            "allocated_garments_with_no_scan_activity": {
                "framing": config.GAP_FRAMING, "may_reflect": config.GAP_REASONS, "provenance": [config.CALCULATED],
                "watch_and_high_thresholds_pct": [config.FLEET_WATCH * 100, config.FLEET_HIGH * 100],
                "sites_at_or_above_watch": [
                    {"site": r["site_name"], "share_pct": r["gap_rate"], "status": engine.STATUS_LABEL[int(r["fleet_status"])]}
                    for _, r in fleet.sort_values("gap_rate", ascending=False).head(15).iterrows()],
            },
            "repair_vs_retirement": {
                "provenance": [config.CALCULATED], "note": "By site and product, not by garment: repairs have no garment IDs.",
                "pairs_at_or_above_watch": [
                    {"site": r["site_name"], "product": r["product_name"], "repairs": r["repairs"],
                     "ratio_vs_product_median": r["ratio"], "status": engine.STATUS_LABEL[int(r["status"])]}
                    for _, r in repair.head(15).iterrows()] if not repair.empty else [],
            },
            "hygiene_screen": {
                "provenance": [config.CALCULATED, config.TEAM_ASSUMPTION], "confidence": "low",
                "garments_flagged_no_scan_over_days": config.HYGIENE_DAYS, **ctx.hygiene,
                "caveat": "The cycle log traces only a small share of the fleet and every flag comes from the log "
                          "ending, so confirm against full RFID records.",
            },
        }
        return self._finish("get_signals", args, scope, scenario, extra, payload)
