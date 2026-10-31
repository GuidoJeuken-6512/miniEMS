"""Config loading, merging (options.json > config.json > defaults), validation."""
import json

import pytest

import config_loader
from config_loader import Config, _defaults, _validate, load_config


@pytest.fixture(autouse=True)
def _isolated_files(tmp_path, monkeypatch):
    """Point CONFIG_FILE/OPTIONS_FILE at a scratch dir for every test."""
    config_file = tmp_path / "config.json"
    options_file = tmp_path / "options.json"
    monkeypatch.setattr(config_loader, "CONFIG_FILE", str(config_file))
    monkeypatch.setattr(config_loader, "OPTIONS_FILE", str(options_file))
    return config_file, options_file


class TestDefaults:
    def test_monitored_entities_includes_base_sensors(self):
        cfg = Config()
        assert cfg.pv_power_entity in cfg.monitored_entities
        assert cfg.battery_soc_entity in cfg.monitored_entities
        assert cfg.electricity_price_entity in cfg.monitored_entities

    def test_monitored_entities_includes_new_peak_time_sensors(self):
        cfg = Config()
        assert cfg.solcast_peak_time_today_entity in cfg.monitored_entities
        assert cfg.solcast_peak_time_tomorrow_entity in cfg.monitored_entities

    def test_monitored_entities_includes_battery_voltage(self):
        cfg = Config()
        assert cfg.battery_voltage_entity in cfg.monitored_entities

    def test_monitored_entities_includes_control_entities(self):
        """Regression: InverterController reads these back from
        ws.state_cache to confirm a write landed (inverter_controller.py,
        _set_charge_current/_set_discharge_current/_set_grid_charge). Before
        this fix they were never subscribed, so the cache entry was always
        empty and a real (non-simulated) write could never confirm –
        retried forever, eventually tripping inverter_write_status="error"."""
        cfg = Config()
        assert cfg.inverter_charge_current_entity in cfg.monitored_entities
        assert cfg.battery_discharging_current_entity in cfg.monitored_entities
        assert cfg.grid_charge_switch_entity in cfg.monitored_entities

    def test_monitored_entities_excludes_blanked_control_entities(self):
        cfg = Config()
        cfg.grid_charge_switch_entity = ""
        assert "" not in cfg.monitored_entities

    def test_monitored_entities_excludes_blanked_optional_fields(self):
        cfg = Config()
        cfg.feed_in_energy_entity = ""
        assert "" not in cfg.monitored_entities

    def test_monitored_entities_has_no_duplicates_by_default(self):
        cfg = Config()
        assert len(cfg.monitored_entities) == len(set(cfg.monitored_entities))


class TestLoadConfig:
    def test_fresh_install_uses_defaults(self, _isolated_files):
        cfg = load_config()
        assert cfg.pv_power_entity == Config().pv_power_entity
        assert cfg.battery_min_soc == 15

    def test_writes_config_file_on_load(self, _isolated_files):
        config_file, _ = _isolated_files
        load_config()
        assert config_file.exists()
        data = json.loads(config_file.read_text())
        assert data["_version"] == config_loader.CURRENT_VERSION

    def test_options_json_value_wins_when_it_differs_from_default(self, _isolated_files):
        _, options_file = _isolated_files
        options_file.write_text(json.dumps({"battery_min_soc": 25}))
        cfg = load_config()
        assert cfg.battery_min_soc == 25

    def test_persisted_config_survives_options_reset(self, _isolated_files):
        """A previously changed value must not be lost when options.json resets."""
        config_file, options_file = _isolated_files
        # First run: user changes battery_min_soc via options.json
        options_file.write_text(json.dumps({"battery_min_soc": 25}))
        load_config()
        # Supervisor resets options.json back to defaults (key removed)
        options_file.write_text(json.dumps({}))
        cfg = load_config()
        assert cfg.battery_min_soc == 25   # survived via config.json

    def test_options_default_equal_value_does_not_shadow_stored_value(self, _isolated_files):
        """options.json carrying the *default* must not look like a user edit."""
        _, options_file = _isolated_files
        # Persist a customized value first (simulating a prior save)
        cfg1 = load_config()
        cfg1.battery_min_soc = 40
        # Manually write it as if a previous run had persisted it
        import config_loader as cl
        with open(cl.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({**_defaults(), "battery_min_soc": 40, "_version": cl.CURRENT_VERSION}, f)
        # options.json still holds the untouched default
        options_file.write_text(json.dumps({"battery_min_soc": Config().battery_min_soc}))
        cfg2 = load_config()
        assert cfg2.battery_min_soc == 40

    def test_option_rename_carried_forward(self, _isolated_files):
        """An old options.json key not yet migrated still reaches the new field.

        _OPTIONS_RENAMES rewrites the raw options.json value straight onto the
        new key, ahead of the defaults-merge – it does not re-run the
        watt->current validation that migrate() applies to config.json, so
        the value is carried through verbatim.
        """
        _, options_file = _isolated_files
        options_file.write_text(json.dumps({
            "inverter_discharge_power_entity": "number.legacy_discharge",
        }))
        cfg = load_config()
        assert cfg.battery_discharging_current_entity == "number.legacy_discharge"

    def test_malformed_json_falls_back_to_defaults(self, _isolated_files):
        config_file, _ = _isolated_files
        config_file.write_text("{not valid json")
        cfg = load_config()
        assert cfg.battery_min_soc == 15   # default, no crash

    def test_fresh_install_enables_device_detection(self, _isolated_files):
        """No config.json at all yet -> migration.py's v19->v20 step sees
        is_fresh=True -> device_detection_enabled defaults on, so a new
        install never inherits the five historically-wrong Deye defaults
        (docs/roadmap/v3.0-geraeteprofile.md, 'Migration')."""
        cfg = load_config()
        assert cfg.device_detection_enabled is True
        assert cfg.entity_overrides == {}

    def test_upgrade_keeps_device_detection_off(self, _isolated_files):
        """A config.json already exists (even pre-v20, no _version at all) ->
        not a fresh install -> detection must stay off, byte-identical to
        pre-v20 behaviour."""
        config_file, _ = _isolated_files
        config_file.write_text(json.dumps({"battery_min_soc": 25}))
        cfg = load_config()
        assert cfg.device_detection_enabled is False
        assert cfg.battery_min_soc == 25

    def test_entity_overrides_persists_across_reload(self, _isolated_files):
        config_file, _ = _isolated_files
        load_config()   # first run creates config.json (fresh -> detection on)
        data = json.loads(config_file.read_text())
        data["entity_overrides"] = {"inverter.pv_power": "sensor.custom_pv"}
        data["device_detection_enabled"] = False
        config_file.write_text(json.dumps(data))
        cfg = load_config()
        assert cfg.entity_overrides == {"inverter.pv_power": "sensor.custom_pv"}
        assert cfg.device_detection_enabled is False


class TestValidate:
    def test_clamps_charge_current_above_max(self):
        cfg = Config()
        cfg.battery_max_charge_current_a = 999
        _validate(cfg)
        assert cfg.battery_max_charge_current_a == 350

    def test_clamps_negative_charge_current(self):
        cfg = Config()
        cfg.battery_max_charge_current_a = -5
        _validate(cfg)
        assert cfg.battery_max_charge_current_a == 0

    def test_disables_battery_control_when_min_soc_not_below_max(self):
        cfg = Config()
        cfg.battery_control_enabled = True
        cfg.battery_min_soc = 90
        cfg.battery_max_soc = 90
        _validate(cfg)
        assert cfg.battery_control_enabled is False

    def test_raises_export_floor_above_min_soc(self):
        cfg = Config()
        cfg.battery_min_soc = 40
        cfg.pv_export_min_soc_pct = 40
        cfg.battery_max_soc = 95
        _validate(cfg)
        assert cfg.pv_export_min_soc_pct > cfg.battery_min_soc

    def test_export_floor_untouched_when_already_above_min_soc(self):
        cfg = Config()
        cfg.battery_min_soc = 15
        cfg.pv_export_min_soc_pct = 30
        _validate(cfg)
        assert cfg.pv_export_min_soc_pct == 30

    def test_clamps_pv_charge_margin_factor(self):
        cfg = Config()
        cfg.pv_charge_margin_factor = 10.0
        _validate(cfg)
        assert cfg.pv_charge_margin_factor == 3.0

    def test_clamps_hour_fields_to_valid_range(self):
        cfg = Config()
        cfg.grid_charge_dark_start_hour = 30
        _validate(cfg)
        assert cfg.grid_charge_dark_start_hour == 23


class TestIdleSetpoint:
    def test_too_close_to_minimum_is_lowered(self):
        cfg = Config(hot_water_min_temp_c=45.0, hw_idle_setpoint_c=43.0)
        _validate(cfg)
        assert cfg.hw_idle_setpoint_c == 40.0   # min − 5 K

    def test_shutoff_not_above_day_target_is_raised(self):
        cfg = Config(hw_day_target_c=45.0, hw_morning_target_c=44.0)
        _validate(cfg)
        assert cfg.hw_morning_target_c == 47.0   # day target + 2 K

    def test_default_kept(self):
        cfg = Config()
        _validate(cfg)
        assert cfg.hw_idle_setpoint_c == 35.0


class TestControlModeIsRestartSafe:
    """Control switches set on the settings page live in config.json only –
    a stale options.json must not flip them after a restart."""

    def test_options_json_cannot_switch_back_to_live(self, _isolated_files):
        config_file, options_file = _isolated_files
        config_file.write_text(json.dumps({
            "_version": config_loader.CURRENT_VERSION,
            "battery_control_enabled": True, "battery_control_simulation": True,
            "hw_control_enabled": False, "hw_control_simulation": True,
        }), encoding="utf-8")
        options_file.write_text(json.dumps({
            "battery_control_simulation": False, "hw_control_enabled": True,
            "hw_control_simulation": False,
        }), encoding="utf-8")
        cfg = load_config()
        assert cfg.battery_control_enabled is True
        assert cfg.battery_control_simulation is True
        assert cfg.hw_control_enabled is False
        assert cfg.hw_control_simulation is True

    def test_live_mode_survives_restart(self, _isolated_files):
        config_file, _ = _isolated_files
        config_file.write_text(json.dumps({
            "_version": config_loader.CURRENT_VERSION,
            "battery_control_enabled": True, "battery_control_simulation": False,
        }), encoding="utf-8")
        load_config()   # first restart rewrites config.json
        cfg = load_config()
        assert cfg.battery_control_enabled is True
        assert cfg.battery_control_simulation is False


class TestStoredStringsAreCoerced:
    """Regression: a float persisted as a string in config.json crashed the
    EMS loop ("'>' not supported between 'float' and 'str'" in
    _grid_charge_pays)."""

    def test_string_numbers_become_numbers_and_are_healed_on_disk(self, _isolated_files):
        config_file, _ = _isolated_files
        config_file.write_text(json.dumps({
            "_version": config_loader.CURRENT_VERSION,
            "grid_charge_min_margin_eur_kwh": "0.03",
            "battery_min_soc": "15",
            "battery_control_enabled": "true",
        }), encoding="utf-8")
        cfg = load_config()
        assert cfg.grid_charge_min_margin_eur_kwh == 0.03
        assert cfg.battery_min_soc == 15
        assert cfg.battery_control_enabled is True
        stored = json.loads(config_file.read_text(encoding="utf-8"))
        assert stored["grid_charge_min_margin_eur_kwh"] == 0.03

    def test_garbage_falls_back_to_default(self, _isolated_files):
        config_file, _ = _isolated_files
        config_file.write_text(json.dumps({
            "_version": config_loader.CURRENT_VERSION,
            "grid_charge_min_margin_eur_kwh": "abc",
        }), encoding="utf-8")
        assert load_config().grid_charge_min_margin_eur_kwh == Config().grid_charge_min_margin_eur_kwh
