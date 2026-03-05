"""Octopus Energy cost optimizer – tracks costs and savings."""
import logging
from collections import defaultdict
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

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
        # Peak PV power per day (for yield prediction)
        self._peak_pv_w: dict[date, float] = defaultdict(float)
        # Running averages for price and outdoor temperature
        self._price_sum: dict[date, float] = defaultdict(float)
        self._price_ticks: dict[date, int] = defaultdict(int)
        self._temp_sum: dict[date, float] = defaultdict(float)
        self._temp_ticks: dict[date, int] = defaultdict(int)
        self._last_tick: datetime | None = None

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
        _LOGGER.info(
            "Restored today's accumulators from DB: grid_cost=%.4f€ load_cost=%.4f€ peak_pv=%.0fW",
            self._grid_cost_eur[today],
            self._load_cost_eur[today],
            self._peak_pv_w[today],
        )

    # ------------------------------------------------------------------
    # Called by EMS controller on each update tick
    # ------------------------------------------------------------------

    def record_tick(
        self,
        grid_power_w: float,
        pv_power_w: float,
        load_power_w: float,
        price_eur_kwh: float,
        interval_sec: int,
        outdoor_temp_c: float | None = None,
    ) -> None:
        """Accumulate energy for one interval."""
        now = date.today()
        hours = interval_sec / 3600

        # Grid import (positive = import, negative = export)
        if grid_power_w > 0:
            kwh_imported = (grid_power_w / 1000) * hours
            self._grid_import_kwh[now] += kwh_imported
            self._grid_cost_eur[now]   += kwh_imported * price_eur_kwh

        # PV contribution to load (energy that would otherwise have been bought)
        pv_to_load_w = max(0.0, min(pv_power_w, load_power_w))
        kwh_pv_used = (pv_to_load_w / 1000) * hours
        self._pv_used_kwh[now] += kwh_pv_used
        self._pv_saved_eur[now] += kwh_pv_used * price_eur_kwh

        # Total load cost (what you'd pay if buying all from grid at current price)
        load_kwh = (load_power_w / 1000) * hours
        self._load_total_kwh[now] += load_kwh
        self._load_cost_eur[now]  += load_kwh * price_eur_kwh

        # Peak PV power (for yield prediction model)
        if pv_power_w > self._peak_pv_w[now]:
            self._peak_pv_w[now] = pv_power_w

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
            "grid_import_kwh":   round(self._grid_import_kwh[today], 4),
            "grid_cost_eur":     round(self._grid_cost_eur[today], 4),
            "pv_used_kwh":       round(self._pv_used_kwh[today], 4),
            "pv_savings_eur":    round(self._pv_saved_eur[today], 4),
            "load_total_kwh":    round(self._load_total_kwh[today], 4),
            "load_cost_eur":     round(self._load_cost_eur[today], 4),
            "peak_pv_w":         round(self._peak_pv_w[today], 1),
            "avg_price_eur_kwh": round(avg_price, 5),
            "ticks":             p_ticks,
        }
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

    def week_grid_cost_eur(self) -> float:
        today = date.today()
        return sum(v for d, v in self._grid_cost_eur.items() if (today - d).days < 7)

    def week_pv_saved_eur(self) -> float:
        today = date.today()
        return sum(v for d, v in self._pv_saved_eur.items() if (today - d).days < 7)

    def is_cheap_rate(self, price_eur_kwh: float | None) -> bool:
        if price_eur_kwh is None:
            return False
        return price_eur_kwh < self._cfg.cheap_rate_threshold_eur

    def summary(self) -> dict[str, Any]:
        return {
            "today_grid_cost_eur":    round(self.today_grid_cost_eur(), 4),
            "today_pv_saved_eur":     round(self.today_pv_saved_eur(), 4),
            "today_grid_import_kwh":  round(self.today_grid_import_kwh(), 3),
            "today_pv_used_kwh":      round(self.today_pv_used_kwh(), 3),
            "today_load_total_kwh":   round(self.today_load_total_kwh(), 3),
            "today_load_cost_eur":    round(self.today_load_cost_eur(), 4),
            "week_grid_cost_eur":     round(self.week_grid_cost_eur(), 4),
            "week_pv_saved_eur":      round(self.week_pv_saved_eur(), 4),
        }

    async def summary_with_db(self) -> dict[str, Any]:
        """Like summary() but enriched with monthly/yearly totals from SQLite."""
        from datetime import date as _date
        today = _date.today()
        year_month = today.strftime("%Y-%m")
        month = await self._store.query_month(year_month)
        year  = await self._store.query_year(today.year)

        base = self.summary()
        base.update({
            "month_grid_cost_eur":  round(month.get("grid_cost_eur", 0.0), 4),
            "month_pv_savings_eur": round(month.get("pv_savings_eur", 0.0), 4),
            "month_load_cost_eur":  round(month.get("load_cost_eur", 0.0), 4),
            "year_grid_cost_eur":   round(year.get("grid_cost_eur", 0.0), 4),
            "year_pv_savings_eur":  round(year.get("pv_savings_eur", 0.0), 4),
            "year_load_cost_eur":   round(year.get("load_cost_eur", 0.0), 4),
        })
        return base
