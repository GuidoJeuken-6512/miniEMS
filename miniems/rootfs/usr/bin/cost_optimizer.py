"""Octopus Energy cost optimizer – tracks costs and savings."""
import logging
from collections import defaultdict
from datetime import date, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config_loader import Config

_LOGGER = logging.getLogger(__name__)


class CostOptimizer:
    """Accumulates energy cost and saving metrics."""

    def __init__(self, config: "Config") -> None:
        self._cfg = config
        # Daily accumulators: date → value
        self._grid_import_kwh: dict[date, float] = defaultdict(float)
        self._pv_used_kwh: dict[date, float] = defaultdict(float)
        self._grid_cost_eur: dict[date, float] = defaultdict(float)
        self._pv_saved_eur: dict[date, float] = defaultdict(float)
        # Last interval timestamp
        self._last_tick: datetime | None = None

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
    ) -> None:
        """Accumulate energy for one interval."""
        now = date.today()
        hours = interval_sec / 3600

        # Grid import (positive = import, negative = export)
        if grid_power_w > 0:
            kwh_imported = (grid_power_w / 1000) * hours
            self._grid_import_kwh[now] += kwh_imported
            self._grid_cost_eur[now] += kwh_imported * price_eur_kwh

        # PV contribution to load (energy that would otherwise have been bought)
        pv_to_load_w = max(0.0, min(pv_power_w, load_power_w))
        kwh_pv_used = (pv_to_load_w / 1000) * hours
        self._pv_used_kwh[now] += kwh_pv_used
        self._pv_saved_eur[now] += kwh_pv_used * price_eur_kwh

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

    def week_grid_cost_eur(self) -> float:
        today = date.today()
        return sum(
            v for d, v in self._grid_cost_eur.items()
            if (today - d).days < 7
        )

    def week_pv_saved_eur(self) -> float:
        today = date.today()
        return sum(
            v for d, v in self._pv_saved_eur.items()
            if (today - d).days < 7
        )

    def is_cheap_rate(self, price_eur_kwh: float | None) -> bool:
        """Return True if current price is below the cheap-rate threshold."""
        if price_eur_kwh is None:
            return False
        return price_eur_kwh < self._cfg.cheap_rate_threshold_eur

    def summary(self) -> dict:
        return {
            "today_grid_cost_eur": round(self.today_grid_cost_eur(), 4),
            "today_pv_saved_eur": round(self.today_pv_saved_eur(), 4),
            "today_grid_import_kwh": round(self.today_grid_import_kwh(), 3),
            "today_pv_used_kwh": round(self.today_pv_used_kwh(), 3),
            "week_grid_cost_eur": round(self.week_grid_cost_eur(), 4),
            "week_pv_saved_eur": round(self.week_pv_saved_eur(), 4),
        }
