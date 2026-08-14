"""Octopus Energy cost optimizer – tracks costs and savings."""
import logging
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any

from sensor_validator import SensorValidator

if TYPE_CHECKING:
    from config_loader import Config
    from store import EnergyStore

_LOGGER = logging.getLogger(__name__)


class CostOptimizer:
    """Accumulates energy cost and saving metrics; persists to SQLite."""

    def __init__(self, config: "Config", store: "EnergyStore") -> None:
        self._cfg = config
        self._store = store
        # Daily accumulators: date → value
        self._grid_import_kwh: dict[date, float] = defaultdict(float)
        self._pv_used_kwh: dict[date, float] = defaultdict(float)
        self._grid_cost_eur: dict[date, float] = defaultdict(float)
        self._pv_saved_eur: dict[date, float] = defaultdict(float)
        self._load_total_kwh: dict[date, float] = defaultdict(float)
        self._load_cost_eur: dict[date, float] = defaultdict(float)
        # Grid-charge and feed-in accumulators (Phase 6)
        self._grid_charge_kwh: dict[date, float] = defaultdict(float)
        self._grid_charge_cost_eur: dict[date, float] = defaultdict(float)
        self._feed_in_kwh: dict[date, float] = defaultdict(float)
        self._feed_in_revenue_eur: dict[date, float] = defaultdict(float)
        # Scenario 2: energy-balance based grid charge (from inverter daily totals)
        # Updated each tick when bat_discharge / grid_import / load_total sensors available
        self._grid_charge_kwh_bilanz: dict[date, float | None] = defaultdict(lambda: None)
        # Latest inverter efficiency η (today_production - today_losses) / today_production
        self._efficiency: dict[date, float | None] = defaultdict(lambda: None)
        # Latest load_total from inverter daily sensor (for bilanz formula)
        self._load_kwh_ha: dict[date, float | None] = defaultdict(lambda: None)
        # Latest bat_discharge from inverter daily sensor
        self._bat_discharge_kwh_ha: dict[date, float | None] = defaultdict(lambda: None)
        # Price tier load accumulators
        self._kwh_high_rate:   dict[date, float] = defaultdict(float)
        self._kwh_medium_rate: dict[date, float] = defaultdict(float)
        self._kwh_low_rate:    dict[date, float] = defaultdict(float)
        # Peak PV power per day (for yield prediction)
        self._peak_pv_w: dict[date, float] = defaultdict(float)
        # Running averages for price and outdoor temperature
        self._price_sum: dict[date, float] = defaultdict(float)
        self._price_ticks: dict[date, int] = defaultdict(int)
        self._temp_sum: dict[date, float] = defaultdict(float)
        self._temp_ticks: dict[date, int] = defaultdict(int)
        self._last_tick: datetime | None = None
        # Spike detection
        self._validator = SensorValidator()
        # Startup warnings (downtime gap detection)
        self._startup_warnings: list[str] = []

    async def restore_today(self) -> None:
        """Load today's running totals from SQLite after startup."""
        today = date.today()
        row = await self._store.load_day(today)
        if not row:
            return
        self._grid_import_kwh[today] = row.get("grid_import_kwh", 0.0)
        self._pv_used_kwh[today]     = row.get("pv_used_kwh", 0.0)
        self._grid_cost_eur[today]   = row.get("grid_cost_eur", 0.0)
        self._pv_saved_eur[today]    = row.get("pv_savings_eur", 0.0)
        self._load_total_kwh[today]  = row.get("load_total_kwh", 0.0)
        self._load_cost_eur[today]   = row.get("load_cost_eur", 0.0)
        self._peak_pv_w[today]       = row.get("peak_pv_w", 0.0)
        self._grid_charge_kwh[today]     = row.get("grid_charge_kwh", 0.0)
        self._grid_charge_cost_eur[today] = row.get("grid_charge_cost_eur", 0.0)
        self._feed_in_kwh[today]         = row.get("feed_in_kwh", 0.0)
        self._feed_in_revenue_eur[today] = row.get("feed_in_revenue_eur", 0.0)
        self._kwh_high_rate[today]       = row.get("kwh_high_rate", 0.0)
        self._kwh_medium_rate[today]     = row.get("kwh_medium_rate", 0.0)
        self._kwh_low_rate[today]        = row.get("kwh_low_rate", 0.0)
        bilanz = row.get("grid_charge_kwh_bilanz")
        self._grid_charge_kwh_bilanz[today] = bilanz  # None if never recorded
        eff = row.get("efficiency_pct")
        self._efficiency[today] = eff / 100.0 if eff is not None else None

        # Downtime gap detection
        last_ts_str = row.get("last_flush_ts")
        if last_ts_str:
            try:
                last_ts = datetime.fromisoformat(last_ts_str)
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=timezone.utc)
                gap_sec = (datetime.now(timezone.utc) - last_ts).total_seconds()
                cfg_interval = self._cfg.update_interval_sec
                if gap_sec > 2 * cfg_interval:
                    msg = (
                        f"Data gap detected: {gap_sec:.0f}s since last flush "
                        f"(>{2 * cfg_interval}s) – energy during downtime is not counted"
                    )
                    self._startup_warnings.append(msg)
                    _LOGGER.warning(msg)
            except ValueError:
                pass

        _LOGGER.info(
            "Restored today's accumulators from DB: grid_cost=%.6f€ load_cost=%.6f€ peak_pv=%.0fW",
            self._grid_cost_eur[today],
            self._load_cost_eur[today],
            self._peak_pv_w[today],
        )

    def get_startup_warnings(self) -> list[str]:
        """Return any warnings generated during restore_today (e.g. downtime gaps)."""
        return list(self._startup_warnings)

    # ------------------------------------------------------------------
    # Called by EMS controller on each update tick
    # ------------------------------------------------------------------

    def record_tick(
        self,
        grid_power_w: float,
        pv_power_w: float,
        load_power_w: float,
        battery_power_w: float,
        price_eur_kwh: float,
        interval_sec: int,
        outdoor_temp_c: float | None = None,
        feed_in_kwh_ha: float | None = None,
        grid_import_kwh_ha: float | None = None,
        bat_charge_kwh_ha: float | None = None,
        bat_discharge_kwh_ha: float | None = None,
        today_production_kwh_ha: float | None = None,
        today_losses_kwh_ha: float | None = None,
    ) -> None:
        """Accumulate energy for one interval."""
        cfg = self._cfg
        now = date.today()
        hours = interval_sec / 3600

        # Spike validation – use last-accepted values; skip accumulation on spike
        pv_w   = self._validator.validate(cfg.pv_power_entity, pv_power_w)
        grid_w = self._validator.validate(cfg.grid_power_entity, grid_power_w)
        load_w = self._validator.validate(cfg.load_power_entity, load_power_w)
        bat_w  = self._validator.validate(cfg.battery_power_entity, battery_power_w)

        if pv_w is None:
            pv_w = 0.0
        if grid_w is None:
            grid_w = 0.0
        if load_w is None:
            load_w = 0.0
        if bat_w is None:
            bat_w = 0.0

        # Grid import (positive = import, negative = export)
        # kWh: prefer inverter's daily-total sensor; fall back to accumulation from grid_w.
        # Cost is always accumulated per tick (requires spot price × kWh each interval).
        if grid_w > 0:
            kwh_imported = (grid_w / 1000) * hours
            if grid_import_kwh_ha is None:
                self._grid_import_kwh[now] += kwh_imported
            self._grid_cost_eur[now] += kwh_imported * price_eur_kwh
        if grid_import_kwh_ha is not None:
            self._grid_import_kwh[now] = grid_import_kwh_ha

        # PV contribution to load (energy that would otherwise have been bought)
        pv_to_load_w = max(0.0, min(pv_w, load_w))
        kwh_pv_used = (pv_to_load_w / 1000) * hours
        self._pv_used_kwh[now] += kwh_pv_used
        self._pv_saved_eur[now] += kwh_pv_used * price_eur_kwh

        # Total load cost (what you'd pay if buying all from grid at current price)
        load_kwh = (load_w / 1000) * hours
        self._load_total_kwh[now] += load_kwh
        self._load_cost_eur[now]  += load_kwh * price_eur_kwh
        # Keep running load total for bilanz formula (power-based accumulation)
        self._load_kwh_ha[now] = self._load_total_kwh[now]

        # Peak PV power (for yield prediction model)
        if pv_w > self._peak_pv_w[now]:
            self._peak_pv_w[now] = pv_w

        # Price tier load accumulation
        if price_eur_kwh < cfg.cheap_rate_threshold_eur:
            self._kwh_low_rate[now] += load_kwh
        elif price_eur_kwh < cfg.medium_rate_threshold_eur:
            self._kwh_medium_rate[now] += load_kwh
        else:
            self._kwh_high_rate[now] += load_kwh

        # Grid-to-battery flow (derived, no EMS mode dependency)
        # battery_power_w > 0 = discharging, < 0 = charging
        pv_surplus_w     = max(0.0, pv_w - load_w)
        battery_charge_w = max(0.0, -bat_w)           # positive when battery is charging
        grid_charge_w    = max(0.0, battery_charge_w - pv_surplus_w)
        if grid_charge_w > 0:
            kwh_gc = (grid_charge_w / 1000) * hours
            self._grid_charge_kwh[now]      += kwh_gc
            self._grid_charge_cost_eur[now] += kwh_gc * price_eur_kwh

        # Feed-in (grid export)
        # Prefer the HA daily-total sensor; fall back to accumulation from grid_w.
        if feed_in_kwh_ha is not None:
            self._feed_in_kwh[now] = feed_in_kwh_ha
            self._feed_in_revenue_eur[now] = feed_in_kwh_ha * cfg.feed_in_tariff_eur_kwh
        elif grid_w < 0:
            kwh_exported = (abs(grid_w) / 1000) * hours
            self._feed_in_kwh[now]         += kwh_exported
            self._feed_in_revenue_eur[now] += kwh_exported * cfg.feed_in_tariff_eur_kwh

        # Scenario 2: energy-balance based grid charge
        # Energie_Netzladen = today_energy_import - today_load_consumption + today_battery_discharge
        # Uses inverter daily-total sensors → more accurate than power-based accumulation
        if bat_discharge_kwh_ha is not None and grid_import_kwh_ha is not None:
            # Keep load_kwh_ha from latest HA load sensor if available, otherwise from accumulator
            load_ha = self._load_kwh_ha.get(now)
            if load_ha is not None and load_ha > 0:
                bilanz = grid_import_kwh_ha - load_ha + bat_discharge_kwh_ha
                self._grid_charge_kwh_bilanz[now] = max(0.0, bilanz)
        # Track bat_discharge and load for bilanz formula
        if bat_discharge_kwh_ha is not None:
            self._bat_discharge_kwh_ha[now] = bat_discharge_kwh_ha

        # Scenario 2: inverter efficiency η = (production - losses) / production
        if today_production_kwh_ha is not None and today_losses_kwh_ha is not None:
            prod = today_production_kwh_ha
            if prod > 0:
                self._efficiency[now] = (prod - today_losses_kwh_ha) / prod
            else:
                self._efficiency[now] = None

        # Running average price
        if price_eur_kwh > 0:
            self._price_sum[now]   += price_eur_kwh
            self._price_ticks[now] += 1

        # Running average outdoor temperature
        if outdoor_temp_c is not None:
            self._temp_sum[now]   += outdoor_temp_c
            self._temp_ticks[now] += 1

    async def flush_to_db(self) -> None:
        """Write today's accumulators to SQLite (call after each tick)."""
        today = date.today()
        p_ticks = self._price_ticks.get(today, 0)
        avg_price = self._price_sum[today] / p_ticks if p_ticks > 0 else 0.0
        t_ticks = self._temp_ticks.get(today, 0)
        avg_temp = self._temp_sum[today] / t_ticks if t_ticks > 0 else None
        fields: dict = {
            "grid_import_kwh":      round(self._grid_import_kwh[today], 6),
            "grid_cost_eur":        round(self._grid_cost_eur[today], 6),
            "pv_used_kwh":          round(self._pv_used_kwh[today], 6),
            "pv_savings_eur":       round(self._pv_saved_eur[today], 6),
            "load_total_kwh":       round(self._load_total_kwh[today], 6),
            "load_cost_eur":        round(self._load_cost_eur[today], 6),
            "peak_pv_w":            round(self._peak_pv_w[today], 1),
            "avg_price_eur_kwh":    round(avg_price, 5),
            "ticks":                p_ticks,
            "grid_charge_kwh":      round(self._grid_charge_kwh[today], 6),
            "grid_charge_cost_eur": round(self._grid_charge_cost_eur[today], 6),
            "feed_in_kwh":          round(self._feed_in_kwh[today], 6),
            "feed_in_revenue_eur":  round(self._feed_in_revenue_eur[today], 6),
            "kwh_high_rate":        round(self._kwh_high_rate[today], 6),
            "kwh_medium_rate":      round(self._kwh_medium_rate[today], 6),
            "kwh_low_rate":         round(self._kwh_low_rate[today], 6),
            "last_flush_ts":        datetime.now(timezone.utc).isoformat(),
        }
        bilanz = self._grid_charge_kwh_bilanz.get(today)
        if bilanz is not None:
            fields["grid_charge_kwh_bilanz"] = round(bilanz, 6)
        eff = self._efficiency.get(today)
        if eff is not None:
            fields["efficiency_pct"] = round(eff * 100, 2)
        if avg_temp is not None:
            fields["avg_outdoor_temp_c"] = round(avg_temp, 2)
        await self._store.upsert_day(today, fields)

    # ------------------------------------------------------------------
    # Public read accessors
    # ------------------------------------------------------------------

    def today_grid_cost_eur(self) -> float:
        return self._grid_cost_eur.get(date.today(), 0.0)

    def today_pv_saved_eur(self) -> float:
        return self._pv_saved_eur.get(date.today(), 0.0)

    def today_grid_import_kwh(self) -> float:
        return self._grid_import_kwh.get(date.today(), 0.0)

    def today_pv_used_kwh(self) -> float:
        return self._pv_used_kwh.get(date.today(), 0.0)

    def today_load_total_kwh(self) -> float:
        return self._load_total_kwh.get(date.today(), 0.0)

    def today_load_cost_eur(self) -> float:
        return self._load_cost_eur.get(date.today(), 0.0)

    def today_grid_charge_kwh(self) -> float:
        return self._grid_charge_kwh.get(date.today(), 0.0)

    def today_grid_charge_cost_eur(self) -> float:
        return self._grid_charge_cost_eur.get(date.today(), 0.0)

    def today_feed_in_kwh(self) -> float:
        return self._feed_in_kwh.get(date.today(), 0.0)

    def today_feed_in_revenue_eur(self) -> float:
        return self._feed_in_revenue_eur.get(date.today(), 0.0)

    def week_grid_cost_eur(self) -> float:
        today = date.today()
        return sum(v for d, v in self._grid_cost_eur.items() if (today - d).days < 7)

    def week_pv_saved_eur(self) -> float:
        today = date.today()
        return sum(v for d, v in self._pv_saved_eur.items() if (today - d).days < 7)

    def today_grid_charge_kwh_bilanz(self) -> float | None:
        return self._grid_charge_kwh_bilanz.get(date.today())

    def today_efficiency(self) -> float | None:
        return self._efficiency.get(date.today())

    def today_grid_charge_roi_eur(self) -> float | None:
        """ROI of today's grid-charging strategy.

        Gewinn_Netzladen = (Energie_Netzladen × η × Ø_Tarif_Entladung) - Kosten_Netzladen

        Returns None when required inputs are unavailable.
        Requires cfg.avg_discharge_tariff_eur_kwh > 0 in config.
        """
        bilanz = self.today_grid_charge_kwh_bilanz()
        if bilanz is None or bilanz <= 0:
            return None
        eta = self.today_efficiency()
        if eta is None or eta <= 0:
            return None
        avg_discharge_tariff = self._cfg.avg_discharge_tariff_eur_kwh
        if avg_discharge_tariff <= 0:
            return None

        # Cost of grid charging is price-weighted per tick from power-based accumulator
        gc_cost = self.today_grid_charge_cost_eur()
        usable_kwh = bilanz * eta
        saving = usable_kwh * avg_discharge_tariff
        return round(saving - gc_cost, 6)

    def is_cheap_rate(self, price_eur_kwh: float | None) -> bool:
        if price_eur_kwh is None:
            return False
        return price_eur_kwh < self._cfg.cheap_rate_threshold_eur

    def price_tier(self, price_eur_kwh: float | None) -> str | None:
        if price_eur_kwh is None:
            return None
        if price_eur_kwh < self._cfg.cheap_rate_threshold_eur:
            return "low"
        if price_eur_kwh < self._cfg.medium_rate_threshold_eur:
            return "medium"
        return "high"

    def summary(self) -> dict[str, Any]:
        today = date.today()
        gc_cost = self.today_grid_charge_cost_eur()
        grid_cost = self.today_grid_cost_eur()
        load_kwh = self.today_load_total_kwh()
        result: dict[str, Any] = {
            "today_grid_cost_eur":          round(grid_cost, 6),
            "today_pv_savings_eur":         round(self.today_pv_saved_eur(), 6),
            "today_grid_import_kwh":        round(self.today_grid_import_kwh(), 3),
            "today_pv_used_kwh":            round(self.today_pv_used_kwh(), 3),
            "today_load_total_kwh":         round(load_kwh, 3),
            "today_load_cost_eur":          round(self.today_load_cost_eur(), 6),
            "today_grid_charge_kwh":        round(self.today_grid_charge_kwh(), 3),
            "today_grid_charge_cost_eur":   round(gc_cost, 6),
            "today_feed_in_kwh":            round(self.today_feed_in_kwh(), 3),
            "today_feed_in_revenue_eur":    round(self.today_feed_in_revenue_eur(), 6),
            "today_cost_without_grid_charge": round(max(0.0, grid_cost - gc_cost), 6),
            "week_grid_cost_eur":           round(self.week_grid_cost_eur(), 6),
            "week_pv_savings_eur":          round(self.week_pv_saved_eur(), 6),
            "today_kwh_high_rate":          round(self._kwh_high_rate.get(today, 0.0), 3),
            "today_kwh_medium_rate":        round(self._kwh_medium_rate.get(today, 0.0), 3),
            "today_kwh_low_rate":           round(self._kwh_low_rate.get(today, 0.0), 3),
        }
        # Scenario 2 – optional fields (None when sensor data unavailable)
        bilanz = self.today_grid_charge_kwh_bilanz()
        if bilanz is not None:
            result["today_grid_charge_kwh_bilanz"] = round(bilanz, 3)
            result["today_grid_charge_cost_bilanz_eur"] = round(
                bilanz * (gc_cost / self.today_grid_charge_kwh() if self.today_grid_charge_kwh() > 0 else 0.0),
                6,
            )
        eta = self.today_efficiency()
        if eta is not None:
            result["today_efficiency_pct"] = round(eta * 100, 1)
        roi = self.today_grid_charge_roi_eur()
        if roi is not None:
            result["today_grid_charge_roi_eur"] = roi
        base = self._cfg.daily_base_price_eur
        if base > 0:
            result["today_base_price_eur"] = round(base, 6)
        return result

    async def summary_with_db(self) -> dict[str, Any]:
        """Like summary() but enriched with monthly/yearly totals from SQLite."""
        today = date.today()
        year_month = today.strftime("%Y-%m")
        month = await self._store.query_month(year_month)
        year  = await self._store.query_year(today.year)

        base = self.summary()
        base.update({
            "month_grid_cost_eur":  round(month.get("grid_cost_eur", 0.0), 6),
            "month_pv_savings_eur": round(month.get("pv_savings_eur", 0.0), 6),
            "month_load_cost_eur":  round(month.get("load_cost_eur", 0.0), 6),
            "year_grid_cost_eur":   round(year.get("grid_cost_eur", 0.0), 6),
            "year_pv_savings_eur":  round(year.get("pv_savings_eur", 0.0), 6),
            "year_load_cost_eur":   round(year.get("load_cost_eur", 0.0), 6),
            "month_kwh_high_rate":   round(month.get("kwh_high_rate", 0.0), 3),
            "month_kwh_medium_rate": round(month.get("kwh_medium_rate", 0.0), 3),
            "month_kwh_low_rate":    round(month.get("kwh_low_rate", 0.0), 3),
        })
        return base
