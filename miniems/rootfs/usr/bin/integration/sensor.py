"""Sensor platform for miniEMS – creates one entity per /api/status field."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import CONF_BASE_URL, DOMAIN
from .coordinator import MiniEMSCoordinator

# Euro sign as unit (HA uses plain strings for non-SI monetary units)
_EUR = "€"
_EUR_PER_KWH = "€/kWh"


@dataclass(frozen=True, kw_only=True)
class MiniEMSSensorDescription(SensorEntityDescription):
    """Extends SensorEntityDescription with the /api/status JSON key."""

    status_key: str = ""
    # Optional: attribute name -> /api/status key, for sensors that carry
    # supplementary diagnostic detail (e.g. per-channel counts) alongside
    # their main value. None for the (large majority of) plain-value sensors.
    attributes_keys: dict[str, str] | None = None


# ── Complete sensor list ──────────────────────────────────────────────────────
# Only addon-native sensors: computed values, aggregates, predictions, and
# battery math. Live power readings (pv, load, grid, battery, SoC) and the
# electricity price are sourced from user-configured external HA entities
# (Deye, Tibber, etc.) and are NOT re-published here to avoid duplication.

SENSOR_DESCRIPTIONS: tuple[MiniEMSSensorDescription, ...] = (
    # Mode
    MiniEMSSensorDescription(
        key="miniems_mode",
        translation_key="mode",
        status_key="mode",
        icon="mdi:home-lightning-bolt",
    ),
    # ── Today: cost / savings ────────────────────────────────────────────────
    MiniEMSSensorDescription(
        key="miniems_today_grid_cost_eur",
        translation_key="today_grid_cost",
        status_key="today_grid_cost_eur",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:currency-eur",
    ),
    MiniEMSSensorDescription(
        key="miniems_today_pv_savings_eur",
        translation_key="today_pv_savings",
        status_key="today_pv_savings_eur",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:piggy-bank",
    ),
    MiniEMSSensorDescription(
        key="miniems_today_load_cost_eur",
        translation_key="today_load_cost",
        status_key="today_load_cost_eur",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:cash",
    ),
    MiniEMSSensorDescription(
        key="miniems_today_feed_in_revenue_eur",
        translation_key="today_feed_in_revenue",
        status_key="today_feed_in_revenue_eur",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:cash-plus",
    ),
    MiniEMSSensorDescription(
        key="miniems_today_cost_without_grid_charge",
        translation_key="today_cost_without_grid_charge",
        status_key="today_cost_without_grid_charge",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:cash-minus",
    ),
    MiniEMSSensorDescription(
        key="miniems_today_cost_fix_price_tariff",
        translation_key="today_cost_fix_price_tariff",
        status_key="today_cost_fix_price_tariff",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:cash",
    ),
    # ── Today: energy ────────────────────────────────────────────────────────
    MiniEMSSensorDescription(
        key="miniems_today_pv_used_kwh",
        translation_key="today_pv_used",
        status_key="today_pv_used_kwh",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:solar-power",
    ),
    MiniEMSSensorDescription(
        key="miniems_today_load_total_kwh",
        translation_key="today_load_total",
        status_key="today_load_total_kwh",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:lightning-bolt",
    ),
    MiniEMSSensorDescription(
        key="miniems_today_grid_charge_kwh",
        translation_key="today_grid_charge",
        status_key="today_grid_charge_kwh",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-charging",
    ),
    # ── Today: tariff tiers ──────────────────────────────────────────────────
    MiniEMSSensorDescription(
        key="miniems_today_kwh_high_rate",
        translation_key="today_kwh_high_rate",
        status_key="today_kwh_high_rate",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:lightning-bolt",
    ),
    MiniEMSSensorDescription(
        key="miniems_today_kwh_medium_rate",
        translation_key="today_kwh_medium_rate",
        status_key="today_kwh_medium_rate",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:lightning-bolt-circle",
    ),
    MiniEMSSensorDescription(
        key="miniems_today_kwh_low_rate",
        translation_key="today_kwh_low_rate",
        status_key="today_kwh_low_rate",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:leaf",
    ),
    # ── Week ─────────────────────────────────────────────────────────────────
    MiniEMSSensorDescription(
        key="miniems_week_grid_cost_eur",
        translation_key="week_grid_cost",
        status_key="week_grid_cost_eur",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:calendar-week",
    ),
    MiniEMSSensorDescription(
        key="miniems_week_pv_savings_eur",
        translation_key="week_pv_savings",
        status_key="week_pv_savings_eur",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:piggy-bank-outline",
    ),
    # ── Month ────────────────────────────────────────────────────────────────
    MiniEMSSensorDescription(
        key="miniems_month_grid_cost_eur",
        translation_key="month_grid_cost",
        status_key="month_grid_cost_eur",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:calendar-month",
    ),
    MiniEMSSensorDescription(
        key="miniems_month_pv_savings_eur",
        translation_key="month_pv_savings",
        status_key="month_pv_savings_eur",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:calendar-month",
    ),
    MiniEMSSensorDescription(
        key="miniems_month_load_cost_eur",
        translation_key="month_load_cost",
        status_key="month_load_cost_eur",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:calendar-month",
    ),
    MiniEMSSensorDescription(
        key="miniems_month_kwh_high_rate",
        translation_key="month_kwh_high_rate",
        status_key="month_kwh_high_rate",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:lightning-bolt",
    ),
    MiniEMSSensorDescription(
        key="miniems_month_kwh_medium_rate",
        translation_key="month_kwh_medium_rate",
        status_key="month_kwh_medium_rate",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:lightning-bolt-circle",
    ),
    MiniEMSSensorDescription(
        key="miniems_month_kwh_low_rate",
        translation_key="month_kwh_low_rate",
        status_key="month_kwh_low_rate",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:leaf",
    ),
    # ── Year ─────────────────────────────────────────────────────────────────
    MiniEMSSensorDescription(
        key="miniems_year_grid_cost_eur",
        translation_key="year_grid_cost",
        status_key="year_grid_cost_eur",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:calendar",
    ),
    MiniEMSSensorDescription(
        key="miniems_year_pv_savings_eur",
        translation_key="year_pv_savings",
        status_key="year_pv_savings_eur",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:calendar",
    ),
    MiniEMSSensorDescription(
        key="miniems_year_load_cost_eur",
        translation_key="year_load_cost",
        status_key="year_load_cost_eur",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:calendar",
    ),
    # ── Predictions ──────────────────────────────────────────────────────────
    MiniEMSSensorDescription(
        key="miniems_predicted_load_kwh",
        translation_key="predicted_load",
        status_key="predicted_load_kwh",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:chart-line",
    ),
    MiniEMSSensorDescription(
        key="miniems_predicted_pv_kwh",
        translation_key="predicted_pv_yield",
        status_key="predicted_pv_kwh",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:weather-sunny",
    ),
    MiniEMSSensorDescription(
        key="miniems_remaining_load_kwh",
        translation_key="remaining_load",
        status_key="remaining_load_kwh",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-clock",
    ),
    # ── Battery computed ─────────────────────────────────────────────────────
    MiniEMSSensorDescription(
        key="miniems_battery_kwh_freetochange",
        translation_key="battery_free_to_charge",
        status_key="battery_kwh_freetochange",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-plus",
    ),
    MiniEMSSensorDescription(
        key="miniems_battery_kwh_useable",
        translation_key="battery_useable",
        status_key="battery_kwh_useable",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-minus",
    ),
    MiniEMSSensorDescription(
        key="miniems_battery_capacity_kwh",
        translation_key="battery_capacity",
        status_key="battery_capacity_kwh",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery",
    ),
    # ── Inverter control ──────────────────────────────────────────────────────
    MiniEMSSensorDescription(
        key="miniems_inverter_write_status",
        translation_key="inverter_write_status",
        status_key="inverter_write_status",
        device_class=SensorDeviceClass.ENUM,
        options=["ok", "warning", "error"],
        icon="mdi:transmission-tower",
        attributes_keys={
            "write_errors": "inverter_write_errors",
            "write_unconfirmed": "inverter_write_unconfirmed",
            "stuck_channels": "inverter_write_stuck_channels",
        },
    ),
    # ── Scenario 2: efficiency / bilanz / ROI ────────────────────────────────
    # These sensors are only available when the corresponding inverter entities
    # are configured (today_production_entity, today_losses_entity, etc.).
    MiniEMSSensorDescription(
        key="miniems_today_efficiency_pct",
        translation_key="today_inverter_efficiency",
        status_key="today_efficiency_pct",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:gauge",
    ),
    MiniEMSSensorDescription(
        key="miniems_today_grid_charge_kwh_bilanz",
        translation_key="today_grid_charge_energy_balance",
        status_key="today_grid_charge_kwh_bilanz",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-charging-outline",
    ),
    MiniEMSSensorDescription(
        key="miniems_today_grid_charge_cost_bilanz_eur",
        translation_key="today_grid_charge_cost_energy_balance",
        status_key="today_grid_charge_cost_bilanz_eur",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:cash",
    ),
    MiniEMSSensorDescription(
        key="miniems_today_grid_charge_roi_eur",
        translation_key="today_grid_charge_roi",
        status_key="today_grid_charge_roi_eur",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:trending-up",
    ),
    MiniEMSSensorDescription(
        key="miniems_today_base_price_eur",
        translation_key="today_base_price",
        status_key="today_base_price_eur",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:receipt",
    ),
)


class MiniEMSSensor(CoordinatorEntity[MiniEMSCoordinator], SensorEntity):
    """A single miniEMS sensor backed by the coordinator."""

    entity_description: MiniEMSSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MiniEMSCoordinator,
        description: MiniEMSSensorDescription,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = description.key
        self._attr_device_info = device_info
        # Set the entity_id explicitly instead of letting HA derive it from
        # the (translated!) entity name + device name on first registration.
        # That derivation depends on translations and the device registry
        # entry both being ready at the exact moment the entity is first
        # added; when either isn't, HA falls back to f"{platform}_{unique_id}"
        # (entity_registry.py, _async_get_full_entity_name) — and since our
        # unique_id already starts with "miniems_" like the platform domain
        # does, that fallback doubles the prefix
        # (sensor.miniems_miniems_inverter_write_status instead of
        # sensor.miniems_inverter_write_status). Observed live: every sensor
        # added since early in the project's life still has the correct ID
        # (registered before this fragility mattered); every sensor added
        # more recently doesn't. Setting entity_id ourselves short-circuits
        # that whole derivation (entity_platform.py: "An entity may suggest
        # the entity_id by setting entity_id itself") and also makes the ID
        # independent of the user's HA language, which the translated-name
        # derivation was not.
        self.entity_id = f"sensor.{description.key}"

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self.entity_description.status_key)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        keys = self.entity_description.attributes_keys
        if not keys or not self.coordinator.data:
            return None
        return {
            attr_name: self.coordinator.data.get(status_key)
            for attr_name, status_key in keys.items()
        }

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and self.entity_description.status_key in self.coordinator.data
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MiniEMSCoordinator = hass.data[DOMAIN][entry.entry_id]

    device_info = DeviceInfo(
        identifiers={(DOMAIN, "miniems_addon")},
        name="miniEMS",
        manufacturer="miniEMS Addon",
        configuration_url=entry.data.get(CONF_BASE_URL),
    )

    async_add_entities(
        MiniEMSSensor(coordinator, description, device_info)
        for description in SENSOR_DESCRIPTIONS
    )

