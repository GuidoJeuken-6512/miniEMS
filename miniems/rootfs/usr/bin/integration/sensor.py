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


# ── Complete sensor list ──────────────────────────────────────────────────────
# Only addon-native sensors: computed values, aggregates, predictions, and
# battery math. Live power readings (pv, load, grid, battery, SoC) and the
# electricity price are sourced from user-configured external HA entities
# (Deye, Tibber, etc.) and are NOT re-published here to avoid duplication.

SENSOR_DESCRIPTIONS: tuple[MiniEMSSensorDescription, ...] = (
    # Mode
    MiniEMSSensorDescription(
        key="miniems_mode",
        status_key="mode",
        name="Mode",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt",
    ),
    # ── Today: cost / savings ────────────────────────────────────────────────
    MiniEMSSensorDescription(
        key="miniems_today_grid_cost_eur",
        status_key="today_grid_cost_eur",
        name="Today Grid Cost",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:currency-eur",
    ),
    MiniEMSSensorDescription(
        key="miniems_today_pv_savings_eur",
        status_key="today_pv_savings_eur",
        name="Today PV Savings",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:piggy-bank",
    ),
    MiniEMSSensorDescription(
        key="miniems_today_load_cost_eur",
        status_key="today_load_cost_eur",
        name="Today Load Cost",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:cash",
    ),
    MiniEMSSensorDescription(
        key="miniems_today_feed_in_revenue_eur",
        status_key="today_feed_in_revenue_eur",
        name="Today Feed-in Revenue",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:cash-plus",
    ),
    MiniEMSSensorDescription(
        key="miniems_today_cost_without_grid_charge",
        status_key="today_cost_without_grid_charge",
        name="Today Cost Without Grid Charge",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:cash-minus",
    ),
    MiniEMSSensorDescription(
        key="miniems_today_cost_fix_price_tariff",
        status_key="today_cost_fix_price_tariff",
        name="Today Cost Fix Price Tariff",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:cash",
    ),
    # ── Today: energy ────────────────────────────────────────────────────────
    MiniEMSSensorDescription(
        key="miniems_today_pv_used_kwh",
        status_key="today_pv_used_kwh",
        name="Today PV Used",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:solar-power",
    ),
    MiniEMSSensorDescription(
        key="miniems_today_load_total_kwh",
        status_key="today_load_total_kwh",
        name="Today Load Total",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:lightning-bolt",
    ),
    MiniEMSSensorDescription(
        key="miniems_today_grid_charge_kwh",
        status_key="today_grid_charge_kwh",
        name="Today Grid Charge",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-charging",
    ),
    # ── Today: tariff tiers ──────────────────────────────────────────────────
    MiniEMSSensorDescription(
        key="miniems_today_kwh_high_rate",
        status_key="today_kwh_high_rate",
        name="Today kWh High Rate",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:lightning-bolt",
    ),
    MiniEMSSensorDescription(
        key="miniems_today_kwh_medium_rate",
        status_key="today_kwh_medium_rate",
        name="Today kWh Medium Rate",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:lightning-bolt-circle",
    ),
    MiniEMSSensorDescription(
        key="miniems_today_kwh_low_rate",
        status_key="today_kwh_low_rate",
        name="Today kWh Low Rate",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:leaf",
    ),
    # ── Week ─────────────────────────────────────────────────────────────────
    MiniEMSSensorDescription(
        key="miniems_week_grid_cost_eur",
        status_key="week_grid_cost_eur",
        name="Week Grid Cost",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:calendar-week",
    ),
    MiniEMSSensorDescription(
        key="miniems_week_pv_savings_eur",
        status_key="week_pv_savings_eur",
        name="Week PV Savings",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:piggy-bank-outline",
    ),
    # ── Month ────────────────────────────────────────────────────────────────
    MiniEMSSensorDescription(
        key="miniems_month_grid_cost_eur",
        status_key="month_grid_cost_eur",
        name="Month Grid Cost",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:calendar-month",
    ),
    MiniEMSSensorDescription(
        key="miniems_month_pv_savings_eur",
        status_key="month_pv_savings_eur",
        name="Month PV Savings",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:calendar-month",
    ),
    MiniEMSSensorDescription(
        key="miniems_month_load_cost_eur",
        status_key="month_load_cost_eur",
        name="Month Load Cost",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:calendar-month",
    ),
    MiniEMSSensorDescription(
        key="miniems_month_kwh_high_rate",
        status_key="month_kwh_high_rate",
        name="Month kWh High Rate",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:lightning-bolt",
    ),
    MiniEMSSensorDescription(
        key="miniems_month_kwh_medium_rate",
        status_key="month_kwh_medium_rate",
        name="Month kWh Medium Rate",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:lightning-bolt-circle",
    ),
    MiniEMSSensorDescription(
        key="miniems_month_kwh_low_rate",
        status_key="month_kwh_low_rate",
        name="Month kWh Low Rate",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:leaf",
    ),
    # ── Year ─────────────────────────────────────────────────────────────────
    MiniEMSSensorDescription(
        key="miniems_year_grid_cost_eur",
        status_key="year_grid_cost_eur",
        name="Year Grid Cost",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:calendar",
    ),
    MiniEMSSensorDescription(
        key="miniems_year_pv_savings_eur",
        status_key="year_pv_savings_eur",
        name="Year PV Savings",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:calendar",
    ),
    MiniEMSSensorDescription(
        key="miniems_year_load_cost_eur",
        status_key="year_load_cost_eur",
        name="Year Load Cost",
        native_unit_of_measurement=_EUR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:calendar",
    ),
    # ── Predictions ──────────────────────────────────────────────────────────
    MiniEMSSensorDescription(
        key="miniems_predicted_load_kwh",
        status_key="predicted_load_kwh",
        name="Predicted Load",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:chart-line",
    ),
    MiniEMSSensorDescription(
        key="miniems_predicted_pv_kwh",
        status_key="predicted_pv_kwh",
        name="Predicted PV Yield",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:weather-sunny",
    ),
    # ── Battery computed ─────────────────────────────────────────────────────
    MiniEMSSensorDescription(
        key="miniems_battery_kwh_freetochange",
        status_key="battery_kwh_freetochange",
        name="Battery Free to Charge",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-plus",
    ),
    MiniEMSSensorDescription(
        key="miniems_battery_kwh_useable",
        status_key="battery_kwh_useable",
        name="Battery Useable",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-minus",
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

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self.entity_description.status_key)

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


# ConfigEntry import needed for async_setup_entry signature
from homeassistant.config_entries import ConfigEntry  # noqa: E402
