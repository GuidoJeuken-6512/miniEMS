"""Config schema migrations – each step and the overall orchestration."""
from const import CONFIG_SCHEMA_VERSION, FORECAST_MAX_AGE_SEC, PRICE_MAX_AGE_SEC
from migration import migrate, _v3_to_v4, _v5_to_v6, _v19_to_v20


def test_non_dict_input_resets_to_defaults():
    result = migrate("not a dict")
    assert result == {"_version": CONFIG_SCHEMA_VERSION}


def test_already_current_version_untouched():
    data = {"_version": CONFIG_SCHEMA_VERSION, "custom_field": "keep me"}
    result = migrate(dict(data))
    assert result == data


def test_string_version_is_coerced():
    data = {"_version": "5"}
    result = migrate(data)
    assert result["_version"] == CONFIG_SCHEMA_VERSION
    # v5->v6 defaults should have been applied
    assert "solcast_today_entity" in result


def test_garbage_version_string_reruns_all_migrations():
    data = {"_version": "not-a-number"}
    result = migrate(data)
    assert result["_version"] == CONFIG_SCHEMA_VERSION
    assert "battery_control_enabled" in result   # from v1->v2


def test_missing_version_defaults_to_zero_and_runs_full_chain():
    result = migrate({})
    assert result["_version"] == CONFIG_SCHEMA_VERSION
    # Spot-check one default from each broad era of the chain
    assert result["battery_control_enabled"] is False
    assert result["weather_entity"] == "weather.openweathermap"
    assert result["grid_charge_switch_entity"] == "switch.deye8k_battery_grid_charging"
    assert result["inverter_charge_current_entity"] == "number.deye8k_battery_max_charging_current"
    assert result["pv_export_priority_enabled"] is False
    assert result["load_consumption_entity"] == "sensor.deye8k_today_load_consumption"
    assert result["event_log_retention_days"] == 30
    assert result["medium_rate_threshold_eur"] == 0.20
    assert result["feed_in_energy_entity"] == "sensor.deye8k_today_energy_export"
    assert result["grid_import_energy_entity"] == "sensor.deye8k_today_energy_import"
    assert result["grid_import_total_entity"] == "sensor.deye8k_total_energy_import"
    assert result["solcast_last_fetch_entity"] == "sensor.solcast_pv_forecast_zeitpunkt_letzter_api_abruf"
    assert result["inverter_write_stuck_threshold_sec"] == 1800
    assert result["solcast_peak_time_today_entity"] == "sensor.solcast_pv_forecast_zeitpunkt_spitzenleistung_heute"
    assert result["solcast_peak_time_tomorrow_entity"] == "sensor.solcast_pv_forecast_zeitpunkt_spitzenleistung_morgen"
    assert result["battery_voltage_entity"] == "sensor.deye8k_battery_voltage"
    # v19->v20: upgrading a config (is_fresh defaults to False) must never
    # enable detection on its own – see test_v19_to_v20_* below.
    assert result["entity_overrides"] == {}
    assert result["device_detection_enabled"] is False


class TestV19ToV20:
    """docs/roadmap/v3.0-geraeteprofile.md, 'Migration': entity_overrides is
    always empty (nothing is ever inferred here), device_detection_enabled
    depends solely on the caller-supplied is_fresh flag – see
    config_loader.load_config()."""

    def test_upgrade_stays_detection_off(self):
        data = _v19_to_v20({}, is_fresh=False)
        assert data["device_detection_enabled"] is False
        assert data["entity_overrides"] == {}

    def test_fresh_install_turns_detection_on(self):
        data = _v19_to_v20({}, is_fresh=True)
        assert data["device_detection_enabled"] is True
        assert data["entity_overrides"] == {}

    def test_does_not_clobber_an_existing_value(self):
        data = _v19_to_v20(
            {"device_detection_enabled": True, "entity_overrides": {"inverter.pv_power": "sensor.x"}},
            is_fresh=False,
        )
        assert data["device_detection_enabled"] is True
        assert data["entity_overrides"] == {"inverter.pv_power": "sensor.x"}

    def test_migrate_full_chain_threads_is_fresh_through(self):
        # A brand new install migrating from v0 (empty dict) all the way up
        # must end with detection on – the one exception to "migrations
        # never change behaviour", deliberately: see config_loader.py.
        result = migrate({}, is_fresh=True)
        assert result["device_detection_enabled"] is True


def test_v0_to_v1_renames_gbp_to_eur():
    data = migrate({"_version": 0, "cheap_rate_threshold_gbp": 0.15})
    assert data["cheap_rate_threshold_eur"] == 0.15
    assert "cheap_rate_threshold_gbp" not in data


def test_v0_to_v1_does_not_override_existing_eur_value():
    data = {"_version": 0, "cheap_rate_threshold_gbp": 0.15, "cheap_rate_threshold_eur": 0.20}
    result = migrate(data)
    assert result["cheap_rate_threshold_eur"] == 0.20


def test_v3_to_v4_carries_forward_old_temp_entity():
    # Called directly, not through migrate(): a later step (v4->v5) removes
    # weather_temperature_entity again in favour of weather_entity, so
    # asserting on the full chain's *output* couldn't see this step's effect.
    result = _v3_to_v4({"outdoor_temp_entity": "sensor.custom_temp"})
    assert result["weather_temperature_entity"] == "sensor.custom_temp"
    assert "outdoor_temp_entity" not in result


def test_v3_to_v4_removes_retired_owm_fields():
    result = _v3_to_v4({"openweathermap_api_key": "secret", "openweathermap_lat": 1.0})
    assert "openweathermap_api_key" not in result
    assert "openweathermap_lat" not in result


def test_v5_to_v6_carries_forward_renamed_discharge_entity():
    # Called directly: v10->v11 later replaces battery_discharging_power_entity
    # with the current-based field, so the full chain's output can't see this
    # step's rename in isolation.
    result = _v5_to_v6({"inverter_discharge_power_entity": "number.old_discharge"})
    assert result["battery_discharging_power_entity"] == "number.old_discharge"


def test_v10_to_v11_watt_limits_dropped_for_rated_current():
    data = {"_version": 10, "battery_max_charge_power_w": 3000}
    result = migrate(data)
    assert "battery_max_charge_power_w" not in result
    assert result["battery_max_charge_current_a"] == 185


def test_v10_to_v11_keeps_a_real_current_entity():
    data = {"_version": 10, "inverter_charge_power_entity": "number.deye_max_charging_current"}
    result = migrate(data)
    assert result["inverter_charge_current_entity"] == "number.deye_max_charging_current"


def test_v10_to_v11_discards_non_current_entity():
    data = {"_version": 10, "inverter_charge_power_entity": "number.deye_charging_power"}
    result = migrate(data)
    assert result["inverter_charge_current_entity"] == "number.deye8k_battery_max_charging_current"


def test_v10_to_v11_removes_retired_default_discharge_power():
    data = {"_version": 10, "default_discharge_power_w": 185}
    result = migrate(data)
    assert "default_discharge_power_w" not in result


def test_v12_to_v13_raises_old_forecast_and_price_defaults_only_if_unchanged():
    data = {"_version": 12, "forecast_max_age_sec": 10800, "price_max_age_sec": 10800}
    result = migrate(data)
    assert result["forecast_max_age_sec"] == FORECAST_MAX_AGE_SEC
    assert result["price_max_age_sec"] == PRICE_MAX_AGE_SEC


def test_v12_to_v13_leaves_customized_forecast_age_alone():
    data = {"_version": 12, "forecast_max_age_sec": 99999, "price_max_age_sec": 10800}
    result = migrate(data)
    assert result["forecast_max_age_sec"] == 99999


def test_v13_to_v14_raises_exact_old_price_default():
    data = {"_version": 13, "price_max_age_sec": 21600}
    result = migrate(data)
    assert result["price_max_age_sec"] == PRICE_MAX_AGE_SEC


def test_v13_to_v14_leaves_customized_price_age_alone():
    data = {"_version": 13, "price_max_age_sec": 30000}
    result = migrate(data)
    assert result["price_max_age_sec"] == 30000


def test_v17_to_v18_sets_peak_time_entities():
    data = {"_version": 17}
    result = migrate(data)
    assert result["solcast_peak_time_today_entity"] == (
        "sensor.solcast_pv_forecast_zeitpunkt_spitzenleistung_heute"
    )
    assert result["solcast_peak_time_tomorrow_entity"] == (
        "sensor.solcast_pv_forecast_zeitpunkt_spitzenleistung_morgen"
    )


def test_v17_to_v18_does_not_override_explicit_value():
    data = {"_version": 17, "solcast_peak_time_today_entity": "sensor.custom_peak"}
    result = migrate(data)
    assert result["solcast_peak_time_today_entity"] == "sensor.custom_peak"


def test_v18_to_v19_sets_battery_voltage_entity():
    data = {"_version": 18}
    result = migrate(data)
    assert result["battery_voltage_entity"] == "sensor.deye8k_battery_voltage"


def test_v18_to_v19_does_not_override_explicit_value():
    data = {"_version": 18, "battery_voltage_entity": "sensor.custom_voltage"}
    result = migrate(data)
    assert result["battery_voltage_entity"] == "sensor.custom_voltage"


def test_migration_is_idempotent():
    once = migrate({})
    twice = migrate(dict(once))
    assert once == twice
