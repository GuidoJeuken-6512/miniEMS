"""Battery state math for miniEMS.

Encapsulates kWh capacity calculations so they stay consistent across the
EMS controller and any sensors that need them.
"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config_loader import Config


class BatteryModel:
    """Computes usable and free-to-charge kWh from current SoC."""

    def __init__(self, config: "Config") -> None:
        self._cfg = config
        # Overridable at runtime from the battery capacity sensor, so the
        # control decision and the dashboard use the same number.
        self.capacity_kwh: float = config.battery_capacity_kwh

    def free_to_charge_kwh(self, soc: float) -> float:
        """Energy that can still be stored before hitting max SoC."""
        cfg = self._cfg
        return max(0.0, (cfg.battery_max_soc - soc) / 100.0 * self.capacity_kwh)

    def useable_kwh(self, soc: float) -> float:
        """Energy available for discharge before hitting min SoC."""
        cfg = self._cfg
        return max(0.0, (soc - cfg.battery_min_soc) / 100.0 * self.capacity_kwh)
