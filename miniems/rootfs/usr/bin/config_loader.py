"""Load add-on configuration with persistent storage and migration support.

Priority (highest → lowest):
  1. /data/options.json — supervisor-managed UI values that differ from defaults
     (user actively changed them in the HA config page)
  2. /data/config.json  — previously persisted values
     (survives options.json resets caused by supervisor reloads / addon updates)
  3. Dataclass defaults

On every startup the merged result is written back to /data/config.json so the
user's settings are never lost even if options.json is reset to defaults.
"""
import json
import logging
import os
from dataclasses import dataclass, field, fields
from typing import Any

from const import (
    BATTERY_MAX_CURRENT_A,
    CONFIG_FILE,
    CONFIG_SCHEMA_VERSION,
    FORECAST_MAX_AGE_SEC,
    INVERTER_WRITE_STUCK_THRESHOLD_SEC,
    OPTIONS_FILE,
    PRICE_MAX_AGE_SEC,
    SENSOR_MAX_AGE_SEC,
    SUN_ENTITY,
)
from migration import migrate

_LOGGER = logging.getLogger(__name__)

CURRENT_VERSION = CONFIG_SCHEMA_VERSION


@dataclass
class Config:
    # Deye Inverter Entities
    pv_power_entity: str = "sensor.deye_pv_total_power"
    battery_soc_entity: str = "sensor.deye_battery_soc"
    battery_power_entity: str = "sensor.deye_battery_power"
    grid_power_entity: str = "sensor.deye_grid_power"
    load_power_entity: str = "sensor.deye_load_power"
    # Needed to convert a charge/discharge power target (W) into the current
    # (A) the Deye's number entities actually accept – see roadmap
    # docs/technical/energiefahrplan.md, V1 ("Ladeleistung strecken") and V3a
    # ("Gelernte Ladeleistung"), which both share this field.
    battery_voltage_entity: str = "sensor.deye8k_battery_voltage"
    battery_capacity_kwh: float = 10.0
    battery_min_soc: int = 15
    battery_max_soc: int = 95
    # Authentication
    long_lived_token: str = ""
    # Octopus Energy Entities
    electricity_price_entity: str = "sensor.octopus_energy_electricity_current_rate"
    cheap_rate_threshold_eur: float = 0.10
    medium_rate_threshold_eur: float = 0.20
    # EMS Parameters
    pv_surplus_threshold_w: int = 200
    update_interval_sec: int = 30
    event_log_retention_days: int = 30
    # Battery Control (Phase 2)
    battery_control_enabled: bool = False
    battery_control_simulation: bool = True
    # Battery limits are CURRENT (A) on the Deye, not power (W) – see const.BATTERY_MAX_CURRENT_A
    inverter_charge_current_entity: str = "number.deye8k_battery_max_charging_current"
    # The Deye's own limit for charging from the grid ("Battery Grid Charging
    # Current"). While grid charging the smaller of it and the charge current
    # above applies, so miniEMS writes the same value to both. Empty = not
    # written (only for inverters without such a limit).
    inverter_grid_charge_current_entity: str = "number.deye8k_battery_grid_charging_current"
    battery_max_charge_current_a: int = 185
    battery_max_discharge_current_a: int = 185
    # Forecast / Prediction (Phase 5)
    weather_entity: str = "weather.openweathermap"
    # Solcast PV Forecast entities (Phase 6)
    solcast_today_entity: str = "sensor.solcast_pv_forecast_prognose_heute"
    solcast_tomorrow_entity: str = "sensor.solcast_pv_forecast_prognose_morgen"
    solcast_remaining_today_entity: str = "sensor.solcast_pv_forecast_prognose_verbleibende_leistung_heute"
    # Timestamp of the last *successful* Solcast API fetch. Its value – not its
    # own last_updated – is the age of the forecast data. Needed because Solcast
    # keeps serving its disk cache when the API is unreachable: the sensors stay
    # `available`, their timestamps keep advancing, and nothing else reveals that
    # the numbers are days old. Leave empty to skip the freshness check.
    solcast_last_fetch_entity: str = "sensor.solcast_pv_forecast_zeitpunkt_letzter_api_abruf"
    # Time of day the forecast expects peak PV power, today/tomorrow. Same
    # SensorUpdatePolicy.DEFAULT write behaviour as solcast_today/tomorrow_entity
    # (written once per day, on fetch/date-change) – staleness is checked the
    # same way: by date, not by age (see is_stale_daily()).
    solcast_peak_time_today_entity: str = "sensor.solcast_pv_forecast_zeitpunkt_spitzenleistung_heute"
    solcast_peak_time_tomorrow_entity: str = "sensor.solcast_pv_forecast_zeitpunkt_spitzenleistung_morgen"
    # Grid charge control via switch + discharge power entity (Phase 6)
    grid_charge_switch_entity: str = "switch.deye8k_battery_grid_charging"
    battery_discharging_current_entity: str = "number.deye8k_battery_max_discharging_current"
    # NOTE: default_discharge_power_w (185) was retired. The 185 came from the
    # inverter's ampere limit, not watts – at ~48 V that is ≈8.9 kW, i.e. full
    # power, never a throttle. Discharge now uses battery_max_discharge_current_a.
    # Feed-in tariff and fixed price tariff (Phase 6)
    feed_in_tariff_eur_kwh: float = 0.08
    fix_price: float = 0.30
    # Feed-in energy sensor – daily total from inverter (optional; falls back to calculated)
    feed_in_energy_entity: str = "sensor.deye8k_today_energy_export"
    # Grid import energy sensor – daily total from inverter (optional; falls back to calculated)
    grid_import_energy_entity: str = "sensor.deye8k_today_energy_import"
    # Load consumption energy sensor – daily total from inverter (optional; falls back to
    # calculated). Unlike grid import/feed-in, today_load_total_kwh had no such anchor before
    # v2.0.3 – it was purely tick-accumulated and therefore permanently under-counted across
    # every addon restart (the inverter's own counter is unaffected by that).
    load_consumption_entity: str = "sensor.deye8k_today_load_consumption"
    # Lifetime (never-resetting) counterparts of the three daily sensors above.
    # Preferred over them since v2.0.4: the inverter resets its daily counters on
    # its own clock – measured 4min54s after local midnight – and in that window
    # the daily sensor still reports yesterday's closing total. A monotonic
    # lifetime counter lets miniEMS cut the day where it cuts everything else,
    # so kWh and cost finally cover the same 24h. The daily sensors above stay in
    # use as the anchor bootstrap after a mid-day restart.
    grid_import_total_entity: str = "sensor.deye8k_total_energy_import"
    feed_in_total_entity: str = "sensor.deye8k_total_energy_export"
    load_consumption_total_entity: str = "sensor.deye8k_total_load_consumption"
    # --- Scenario 2: Grid-charge cost / efficiency / ROI (optional; enables bilanz-based calculation) ---
    # Daily battery charge/discharge totals from inverter
    battery_charge_entity: str = "sensor.deye8k_today_battery_charge"
    battery_discharge_entity: str = "sensor.deye8k_today_battery_discharge"
    # Battery capacity sensor – replaces fixed battery_capacity_kwh when set
    battery_capacity_entity: str = "sensor.deye8k_battery_capacity"
    # Battery state enum sensor (charging / discharging / idle)
    battery_state_entity: str = "sensor.deye8k_battery_state"
    # PV production and losses – required for inverter efficiency calculation
    today_production_entity: str = "sensor.deye8k_today_production"
    today_losses_entity: str = "sensor.deye8k_today_losses"
    # Real-time power losses (W) – for live efficiency display
    power_losses_entity: str = "sensor.deye8k_power_losses"
    # --- Grid-friendly PV strategy (Phase 7) ---
    # Master switch. OFF = behave exactly as before (charge on any PV surplus).
    # Honours battery_control_simulation like every other inverter action.
    pv_export_priority_enabled: bool = False
    # Start charging once remaining_forecast <= free_capacity * factor.
    # >1 charges earlier (safer against an over-optimistic forecast).
    pv_charge_margin_factor: float = 1.2
    # Deadband around that trigger so the mode cannot flap (0.10 = ±10 %)
    pv_charge_hysteresis_frac: float = 0.10
    # Never withhold charging from a battery below this SoC
    pv_export_min_soc_pct: int = 30
    # Local hour from which the battery always charges, whatever the forecast says
    pv_charge_backstop_hour: int = 14
    # Minimum economic margin before buying grid energy for the battery (€/kWh).
    # Storing grid energy only pays when the later discharge displaces a more
    # expensive purchase than the round trip cost. With this installation's
    # numbers that margin is thin: NIEDRIG 27.44 ct stored at 91.6% efficiency
    # to displace STANDARD 34.44 ct yields 0.916 × 34.44 − 27.44 = +4.1 ct/kWh,
    # and at 85% efficiency it turns into a loss of −2.2 ct/kWh. 2 ct of margin
    # keeps a marginal case from being decided by measurement noise.
    grid_charge_min_margin_eur_kwh: float = 0.02
    # Charge current while holding (0 A = block charging entirely)
    export_hold_charge_current_a: int = 0
    # A new mode must be requested continuously for this long before it applies
    mode_dwell_sec: int = 300
    # Leave PROTECT_BATTERY only above battery_min_soc + this
    battery_soc_hysteresis_pct: int = 2
    # Deadband on free capacity before grid charging is considered worthwhile
    grid_charge_min_free_kwh: float = 1.0
    # No-forecast fallback window in which grid charging is still allowed
    grid_charge_dark_start_hour: int = 21
    grid_charge_dark_end_hour: int = 6
    # Sensor staleness limits (seconds); see const.py for the rationale
    sensor_max_age_sec: int = SENSOR_MAX_AGE_SEC
    forecast_max_age_sec: int = FORECAST_MAX_AGE_SEC
    price_max_age_sec: int = PRICE_MAX_AGE_SEC
    # Fixed daily base/standing charge (€/day) added on top of energy costs
    daily_base_price_eur: float = 0.0
    # Average discharge tariff for ROI calculation (€/kWh); 0 = auto-derive from price tiers
    avg_discharge_tariff_eur_kwh: float = 0.0
    # How long a single inverter write channel must stay continuously
    # unconfirmed before sensor.miniems_inverter_write_status reports "error"
    # instead of "warning". Provisional default – see const.py.
    inverter_write_stuck_threshold_sec: int = INVERTER_WRITE_STUCK_THRESHOLD_SEC
    # --- Device detection (docs/technical/geraeteprofile.md) ---------
    # Highest-priority resolver override, keyed "<class>.<role>" -> entity_id
    # (e.g. "inverter.battery_power": "sensor.foo") – see
    # device_resolver.resolve_class()'s `overrides` parameter. Empty by
    # default; only ever set explicitly (today via the raw config.json
    # editor – /devices' entity-picker UI is a later step). Never has a form
    # field in settings.html, so it can never be corrupted by _coerce()'s
    # str(value) fallback for unknown keys – see web_server._DICT_FIELDS.
    entity_overrides: dict[str, str] = field(default_factory=dict)
    # Gates whether device_resolver is allowed to actually drive runtime
    # config, vs. today's read-only /devices preview. False on every
    # upgrade (migration.py's v19->v20 step is byte-identical for existing
    # installs); True only for a genuinely new install with no config.json
    # yet, so it never inherits the five historically-wrong Deye defaults.
    device_detection_enabled: bool = False
    # --- Stündliche Bedarfsprognose / Überbrückungsplanung (v3.3.0) ------
    # docs/technical/netzladen-entscheidung.md. Master switch: OFF = the
    # grid-charge decision stays exactly as before. ON = once enough hourly
    # history exists (load_forecast_min_days), grid charging buys exactly the
    # energy needed to bridge the next expensive tariff period, computed per
    # 30-min slot from a temperature-driven load forecast, the Solcast
    # detailedForecast and the tariff calendar.
    load_forecast_enabled: bool = False
    # Days of hourly history before the forecast may drive decisions. Until
    # then it is computed and shown, but the old logic decides.
    load_forecast_min_days: int = 14
    # Optional power sensor(s) of a heat pump, in W. Several entities (e.g.
    # the three phases of a Shelly 3EM) may be given comma-separated and are
    # summed. Empty = no separate heat-pump meter: the temperature dependence
    # is then learned from the total household load.
    heat_pump_power_entity: str = ""
    # Optional outdoor temperature sensor (°C). Empty = the current hour's
    # value from the weather entity's hourly forecast is recorded instead.
    outdoor_temp_entity: str = ""
    # At startup, fill missing daily load/temperature of the last 60 days
    # from HA's long-term statistics (history_backfill.py). Gaps only. Opt-in.
    history_backfill_enabled: bool = False
    # Heating limit temperature: above it the heat pump does not heat (only
    # hot water, which the model learns as a flat base).
    heating_limit_temp_c: float = 15.0
    # --- Wärmepumpe / Warmwasser – Datensammlung (v3.4.0) ------------------
    # docs/technical/warmwasser-steuerung.md. All empty = nothing recorded.
    heat_pump_cop_entity: str = ""
    # Numeric operating-mode / ad-hoc request-type sensors of the heat pump.
    heat_pump_mode_entity: str = ""
    heat_pump_request_type_entity: str = ""
    boil_temp_entity: str = ""
    # Codes meaning "hot water": the boiler (hot-water storage) is being heated, so no standby loss
    # can be measured while either matches.
    hp_mode_hot_water_code: int = 2
    hp_request_hot_water_code: int = 4
    hot_water_min_temp_c: float = 45.0
    # COP readings right after a compressor start / mode change are
    # unrealistic transients and are discarded for this long.
    hp_cop_settle_sec: int = 300
    # A boiler-temperature drop of at least this many K within
    # HW_DRAW_WINDOW_SEC is a hot-water draw, not standby loss.
    hw_draw_drop_k: float = 1.5
    # --- Warmwasser-Steuerung (v3.5.0, docs/technical/warmwasser-steuerung.md) ---
    # Off = decisions are only computed and shown; simulation = logged as
    # [SIM], never written. Both must be changed on purpose to go live.
    hw_control_enabled: bool = False
    hw_control_simulation: bool = True
    # Read back to confirm a write (°C).
    hw_target_entity: str = "sensor.eu08l_boil1_target_high_temperature"
    # "domain.service" of the heat pump integration's write service, and its
    # JSON data template; {value} is replaced by °C × hw_write_scale.
    # Lambda: Modbus register 2050 = boiler 1 target high temperature (0.1 °C).
    hw_write_service: str = "lambda_heat_pumps.write_modbus_register"
    hw_write_data: str = '{"register_address": 2050, "value": {value}}'
    hw_write_scale: int = 10
    hw_volume_l: float = 200.0
    hw_day_target_c: float = 45.0
    # Shut-off temperature of every charge except a spontaneous PV-surplus one
    # (that one runs to hw_max_temp_c), and the workday morning target. The
    # written setpoint is only a start impulse above it – miniEMS ends the
    # charge when the measured boiler temperature reaches this value.
    hw_morning_target_c: float = 50.0
    hw_morning_time: str = "06:30"
    # Optional HA binary_sensor (on = workday); empty = Monday–Friday.
    hw_workday_entity: str = ""
    # Start impulse: the setpoint is raised this far above the boiler so the
    # heat pump switches to hot-water mode (at least shut-off + 2 K).
    hw_boost_delta_k: float = 10.0
    # Setpoint written when no charge runs (goal reached, cancelled, idle).
    # Far below the day target so the heat pump's own hysteresis never starts
    # a charge; the minimum is guarded by miniEMS' protection charge.
    hw_idle_setpoint_c: float = 35.0
    hw_max_temp_c: float = 55.0
    # Thermal heating power until learned from history.
    hw_heat_power_kw: float = 4.0
    # Starting point for the learned COP while history is thin.
    hw_cop_prior: float = 4.77
    hw_cop_prior_temp_c: float = 8.5
    hw_pv_min_export_w: int = 1500
    hw_min_off_sec: int = 1800
    # Ad-hoc button: setpoint = current boiler temperature + this, written
    # immediately (capped at 65 °C).
    hw_adhoc_delta_k: float = 10.0
    # A charge starts now when that is at most this much dearer (per kWh of
    # heat) than the best later option – "will I make it to the next cheap
    # window?" is answered with "no" only when waiting saves more than this.
    hw_decision_tolerance_pct: float = 5.0
    # Minimum charge (K below the shut-off temperature) before a planned charge
    # may start: every start costs (compressor start, flow pipes, trailing run),
    # so tiny top-ups are avoided. A protection or ad-hoc charge ignores it; a
    # requirement that no charge of this size can meet falls back to 0.5 K.
    hw_min_charge_k: float = 3.0
    # The same while the compressor already runs (space heating): switching to
    # hot water then needs no extra start, so a smaller top-up is worth it.
    hw_min_charge_running_k: float = 1.5
    # Shows whether the compressor runs: a numeric sensor (> 0 = running, e.g.
    # the Lambda's compressor rating in %) or a text state (CH/DHW/CC/DEFROST =
    # running). Empty or unavailable = treated as off (hw_min_charge_k).
    hw_compressor_entity: str = "sensor.eu08l_hp1_compressor_unit_rating"
    # Which Solcast scenario feeds the plan: "estimate" (median), "estimate10"
    # (pessimistic) or "blend" = w × estimate + (1−w) × estimate10. Under-
    # charging costs the full tariff spread per kWh, over-charging only the
    # round-trip losses – hence the default leans pessimistic.
    pv_forecast_scenario: str = "blend"
    pv_forecast_blend_weight: float = 0.7
    # Tariff calendar source: "auto" (dated rates[] if present, else the
    # time-of-use timeslots[]), "timeslots" or "rates".
    tariff_source: str = "auto"
    # Unit of a dated price list: "auto" (decided once for the whole list),
    # "eur" (€/kWh), "ct" (ct/kWh) or "eur_mwh" (€/MWh, raw exchange prices).
    price_unit: str = "auto"

    # ── Phase A (v4.0.0, docs/roadmap/gesamtplan.md) ─────────────────────
    # Every switch below is off by default: with all of them off, every
    # decision is identical to v3.7.2.
    # A1 – relative price classes instead of cheap_rate_threshold_eur
    # wherever a tariff calendar exists: the cheapest price_cheap_hours of the
    # calendar day are "low", the peak above the median is "high".
    price_classes_enabled: bool = False
    price_cheap_hours: float = 6.0
    price_hysteresis_min_eur: float = 0.005
    price_hysteresis_max_eur: float = 0.02
    # A day whose prices span less than this is flat: nothing is low.
    price_flat_spread_eur: float = 0.03
    # A2 – the hourly load model (load_forecast) replaces the day median in
    # the fallback path, and V2 defers only when the battery is not needed
    # before the later window.
    load_profile_enabled: bool = False
    # A3 – the grid-charge gate values a bought kWh by the dearest hour the
    # battery cannot already cover, instead of a historical average.
    soc_aware_gate_enabled: bool = False
    # Battery wear per discharged kWh (€/kWh): price ÷ (cycles × usable kWh).
    battery_wear_cost_eur_kwh: float = 0.0
    # A4 – hold the battery (no discharge) in cheap hours when the stored
    # energy is needed for dearer hours later.
    discharge_hold_enabled: bool = False
    # Minimum price difference (€/kWh) between now and the later hour.
    discharge_hold_min_spread_eur: float = 0.03
    # A5 – negative prices and unremunerated feed-in.
    negative_price_enabled: bool = False
    # Optional sensor with the current feed-in price (€/kWh or ct/kWh);
    # ≤ 0 means exporting earns nothing (Solarspitzengesetz).
    feed_in_price_entity: str = ""
    # Feed-in limit at the grid connection (W, 0 = none), e.g. 60 % of the
    # module power: surplus above it is curtailed unless the battery takes it.
    feed_in_limit_w: int = 0
    # Optional export-limit actuator (number, W): set to 0 while the price is
    # negative and the battery is full, back to export_limit_normal_w after.
    grid_export_limit_entity: str = ""
    grid_export_limit_normal_w: int = 0
    # Price components for a spot tariff that delivers raw exchange prices:
    # effective = (spot + fee) × (1 + VAT).
    price_is_spot: bool = False
    price_fee_eur_kwh: float = 0.0
    price_vat_pct: float = 0.0
    # A6 – battery day plan (dynamic programming). Off = computed and shown
    # only (shadow mode); on = slot 0 of the plan decides.
    battery_plan_enabled: bool = False
    # "careful" | "balanced" | "bold": scales PV down / load up and the
    # residual value of the stored energy.
    battery_plan_style: str = "careful"
    # Cost of one change of action (€) – keeps the plan calm.
    battery_plan_switch_cost_eur: float = 0.01
    # A7 – learned corrections. Recorded always; used only when switched on.
    battery_learned_values_enabled: bool = False
    pv_correction_enabled: bool = False
    plan_feedback_enabled: bool = False
    # A8 – hot water: learn the heat lost per start and charge it to every
    # planned charge. Needs the heat pump's thermal hot-water counter (kWh).
    hw_start_cost_enabled: bool = False
    hw_thermal_energy_entity: str = ""
    # A9 – day-ahead prices: tolerance band instead of equality, capacity
    # check of later windows, V1 over the whole cheap run, slot mean price.
    day_ahead_enabled: bool = False
    # A10 – the HA integration resets every actuator to its safe value when
    # the add-on has not reported for this long (minutes, 0 = off).
    failsafe_timeout_min: int = 15
    # Maximum changes of one actuator target per hour (0 = unlimited).
    write_budget_per_hour: int = 0
    # A11 – charge the battery to battery_max_soc at least every n days
    # (0 = off), from PV if possible, else in the cheapest window.
    battery_full_charge_days: int = 0
    # Weekly hot-water hygiene charge (optional, off).
    hw_hygiene_enabled: bool = False
    hw_hygiene_temp_c: float = 60.0
    hw_hygiene_interval_days: int = 7

    # ── Phase B (v4.1.0, docs/roadmap/gesamtplan.md) ─────────────────────
    # Every switch below is off by default: with all of them off, every
    # decision is identical to v4.0.0.
    # B1 – main-fuse guard: throttles grid charging and the wallbox when a
    # phase gets close to the fuse. Needs the rated current and three phase
    # current sensors (A); phase powers (W, + = import) mark exporting phases.
    fuse_guard_enabled: bool = False
    fuse_rated_a: float = 0.0
    fuse_work_limit_pct: float = 90.0
    phase_current_entities: str = ("sensor.deye8k_external_ct1_current,"
                                   "sensor.deye8k_external_ct2_current,"
                                   "sensor.deye8k_external_ct3_current")
    phase_power_entities: str = ("sensor.deye8k_grid_l1_power,sensor.deye8k_grid_l2_power,"
                                 "sensor.deye8k_grid_l3_power")
    phase_voltage_v: float = 230.0
    # Phase readings older than this count as missing (s).
    fuse_stale_sec: int = 60
    # Fast check of the phase currents while a large load runs (s).
    fuse_poll_sec: int = 10
    # §14a EnWG: binary_sensor of the control box (on = dimmed) and the
    # guaranteed minimum power for all controllable loads together (W).
    grid_dimming_entity: str = ""
    grid_dimming_limit_w: int = 4200
    # B2 – who gets the PV surplus first: "battery" (up to
    # battery_priority_soc_pct, then the EV) or "ev".
    charge_priority: str = "battery"
    battery_priority_soc_pct: int = 50
    # While the EV charges: "block" = the battery does not discharge
    # (it would feed the car), "allow" = leave the battery alone.
    ev_battery_mode: str = "block"
    # B3 – EV / wallbox. Read-only as soon as a power or plug entity is set.
    ev_control_enabled: bool = False
    ev_control_simulation: bool = True
    ev_charge_power_entity: str = ""
    ev_plugged_entity: str = ""
    ev_status_entity: str = ""
    # Status texts meaning "waiting for the car to start" (charge hangs).
    ev_status_waiting_states: str = "waiting,waiting_for_vehicle,ready"
    ev_enable_entity: str = ""
    ev_current_entity: str = ""
    ev_phases_entity: str = ""
    ev_soc_entity: str = ""
    # Override helper (input_select): Auto / Jetzt / Nur PV / Stopp.
    ev_mode_entity: str = ""
    ev_target_soc_entity: str = ""
    ev_departure_entity: str = ""
    ev_target_soc_pct: float = 80.0
    ev_departure_time: str = "07:00"
    ev_capacity_kwh: float = 50.0
    ev_max_charge_kw: float = 11.0
    ev_max_current_a: float = 16.0
    ev_min_current_a: float = 6.0
    ev_phases_max: int = 3
    # Pause: "zero_current" (0 A, session stays) or "switch" (enable off).
    ev_pause_mode: str = "zero_current"
    # B4 – morning sub-target (0 = none).
    ev_morning_soc_pct: float = 50.0
    ev_morning_time: str = "06:00"
    # B6 – heating via the flow-line offset (D1 capability).
    heating_control_enabled: bool = False
    heating_control_simulation: bool = True
    heating_offset_entity: str = "number.eu08l_hc1_flow_line_offset_temperature"
    heating_offset_up_k: float = 2.0
    heating_offset_down_k: float = 2.0
    # Optional: report the PV surplus to the heat pump (number, W). Only
    # with the heat pump's own surplus option switched off – two regulators
    # on one heat pump fight each other.
    heating_surplus_entity: str = ""
    # Electrical power of the compressor alone (W, 0 = unknown); far above
    # it the heating rod runs.
    heating_compressor_max_w: float = 0.0
    # COP loss per K higher flow temperature (%).
    heating_cop_drop_pct_per_k: float = 2.5
    # PV forecast (kWh, next hours) above which pre-loading is halved.
    heating_sun_kwh: float = 10.0
    # B7 – room temperature and pre-heat planner.
    heating_room_temp_entity: str = "sensor.eu08l_hc1_room_device_temperature"
    heating_room_control_enabled: bool = False
    heating_comfort_min_c: float = 0.0
    heating_room_setpoint_c: float = 21.0
    # D1 – hot-water setpoint as a number entity instead of the service
    # template hw_write_service/hw_write_data (empty = service template).
    hw_setpoint_entity: str = ""
    # B8 – start advice: "Name:kWh:hours, …" (empty = none).
    flex_devices: str = ""

    @property
    def heat_pump_entities(self) -> list[str]:
        """heat_pump_power_entity split on commas, blanks dropped."""
        return [e.strip() for e in (self.heat_pump_power_entity or "").split(",") if e.strip()]

    @property
    def monitored_entities(self) -> list[str]:
        base = [
            self.pv_power_entity,
            self.battery_soc_entity,
            self.battery_power_entity,
            self.battery_voltage_entity,
            self.grid_power_entity,
            self.load_power_entity,
            self.electricity_price_entity,
        ]
        solcast = [
            e for e in [
                self.solcast_today_entity,
                self.solcast_tomorrow_entity,
                self.solcast_remaining_today_entity,
                self.solcast_last_fetch_entity,
                self.solcast_peak_time_today_entity,
                self.solcast_peak_time_tomorrow_entity,
            ]
            if e
        ]
        optional = [
            e for e in [
                self.feed_in_energy_entity,
                self.grid_import_energy_entity,
                self.load_consumption_entity,
                self.grid_import_total_entity,
                self.feed_in_total_entity,
                self.load_consumption_total_entity,
            ]
            if e
        ]
        scenario2 = [
            e for e in [
                self.battery_charge_entity,
                self.battery_discharge_entity,
                self.battery_capacity_entity,
                self.battery_state_entity,
                self.today_production_entity,
                self.today_losses_entity,
                self.power_losses_entity,
            ]
            if e
        ]
        # Write-confirmation targets: InverterController reads these back from
        # ws.state_cache to decide whether a service call actually landed
        # (inverter_controller.py, _set_charge_current/_set_discharge_current/
        # _set_grid_charge). Omitted here until now, so the cache never held
        # them and every write silently never confirmed – invisible in
        # simulation mode (which confirms without checking the cache) but
        # fatal for real control: a live write would retry forever and trip
        # inverter_write_status="error" after INVERTER_WRITE_STUCK_THRESHOLD_SEC.
        control = [
            e for e in [
                self.inverter_charge_current_entity,
                self.inverter_grid_charge_current_entity,
                self.battery_discharging_current_entity,
                self.grid_charge_switch_entity,
                self.grid_export_limit_entity,
            ]
            if e
        ]
        tariff = [e for e in [self.feed_in_price_entity] if e]
        phase_b = [
            e for e in [
                *(_split(self.phase_current_entities) if self.fuse_guard_enabled else []),
                *(_split(self.phase_power_entities) if self.fuse_guard_enabled else []),
                self.grid_dimming_entity,
                self.ev_charge_power_entity, self.ev_plugged_entity, self.ev_status_entity,
                self.ev_enable_entity, self.ev_current_entity, self.ev_phases_entity,
                self.ev_soc_entity, self.ev_mode_entity, self.ev_target_soc_entity,
                self.ev_departure_entity,
                self.heating_offset_entity if self.heating_control_enabled else "",
                self.heating_surplus_entity,
                self.heating_room_temp_entity if self.heating_control_enabled else "",
                self.hw_setpoint_entity,
            ]
            if e
        ]
        forecast = [
            e for e in [*self.heat_pump_entities, self.outdoor_temp_entity] if e
        ]
        heat_pump = [
            e for e in [
                self.heat_pump_cop_entity,
                self.heat_pump_mode_entity,
                self.heat_pump_request_type_entity,
                self.boil_temp_entity,
                self.hw_target_entity,
                self.hw_workday_entity,
                self.hw_compressor_entity,
                self.hw_thermal_energy_entity,
            ]
            if e
        ]
        # sun.sun: is it dark? Silences the PV-power staleness warning at night.
        context = [SUN_ENTITY]
        seen: set[str] = set()
        out: list[str] = []
        for e in (base + solcast + optional + scenario2 + control + tariff + forecast
                  + heat_pump + phase_b + context):
            if e not in seen:
                seen.add(e)
                out.append(e)
        return out


def _split(raw: str) -> list[str]:
    return [e.strip() for e in (raw or "").split(",") if e.strip()]


def _defaults() -> dict:
    cfg = Config()
    return {f.name: getattr(cfg, f.name) for f in fields(cfg)}


def _load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, json.JSONDecodeError) as exc:
        _LOGGER.error("Failed to read %s: %s – ignored", path, exc)
        return {}


# Minimum distance of hw_idle_setpoint_c below hot_water_min_temp_c.
_HW_IDLE_MIN_GAP_K = 5.0
# Minimum distance of the shut-off temperature (hw_morning_target_c) above the day target.
_HW_SHUTOFF_MIN_GAP_K = 2.0

# Simulation / control switches. Owned by config.json (settings page, switched
# at runtime via /api/control_mode); options.json never overrides them.
CONTROL_MODE_KEYS: frozenset[str] = frozenset({
    "battery_control_enabled", "battery_control_simulation",
    "hw_control_enabled", "hw_control_simulation",
    "ev_control_enabled", "ev_control_simulation",
    "heating_control_enabled", "heating_control_simulation",
})

_OPTIONS_RENAMES: dict[str, str] = {
    # old options.json key → current Config field name
    "inverter_discharge_power_entity": "battery_discharging_current_entity",
    "battery_discharging_power_entity": "battery_discharging_current_entity",
    "inverter_charge_power_entity": "inverter_charge_current_entity",
}


def _validate(cfg: "Config") -> None:
    """Clamp values that would be rejected by, or unsafe for, the inverter.

    Battery limits are written straight into HA `number` entities whose range
    is 0–BATTERY_MAX_CURRENT_A. A hand-edited config (or a value carried over
    from the old watt-based fields) must never reach the inverter unclamped.
    """
    for field_name in ("battery_max_charge_current_a", "battery_max_discharge_current_a"):
        value = getattr(cfg, field_name)
        clamped = max(0, min(BATTERY_MAX_CURRENT_A, int(value)))
        if clamped != value:
            _LOGGER.warning(
                "%s = %r is out of range (0–%d A) – clamped to %d",
                field_name, value, BATTERY_MAX_CURRENT_A, clamped,
            )
            setattr(cfg, field_name, clamped)

    if cfg.battery_min_soc >= cfg.battery_max_soc:
        _LOGGER.warning(
            "battery_min_soc (%d) >= battery_max_soc (%d) – battery control disabled",
            cfg.battery_min_soc, cfg.battery_max_soc,
        )
        cfg.battery_control_enabled = False

    # --- grid-friendly PV strategy -------------------------------------
    # The export hold must never be able to strand the battery empty.
    if cfg.pv_export_min_soc_pct <= cfg.battery_min_soc:
        new = min(cfg.battery_min_soc + 10, cfg.battery_max_soc)
        _LOGGER.warning(
            "pv_export_min_soc_pct (%d) must exceed battery_min_soc (%d) – raised to %d",
            cfg.pv_export_min_soc_pct, cfg.battery_min_soc, new,
        )
        cfg.pv_export_min_soc_pct = new

    clamps = (
        ("pv_charge_margin_factor", 0.5, 3.0),
        ("pv_charge_hysteresis_frac", 0.0, 0.5),
        ("pv_charge_backstop_hour", 0, 23),
        ("grid_charge_dark_start_hour", 0, 23),
        ("grid_charge_dark_end_hour", 0, 23),
        ("mode_dwell_sec", 0, 3600),
        ("export_hold_charge_current_a", 0, BATTERY_MAX_CURRENT_A),
        ("load_forecast_min_days", 1, 60),
        ("heating_limit_temp_c", 5.0, 25.0),
        ("pv_forecast_blend_weight", 0.0, 1.0),
        ("hot_water_min_temp_c", 30.0, 65.0),
        ("hp_cop_settle_sec", 0, 1800),
        ("hw_draw_drop_k", 0.5, 10.0),
        ("hw_write_scale", 1, 1000),
        ("hw_volume_l", 30.0, 2000.0),
        ("hw_day_target_c", 30.0, 65.0),
        ("hw_morning_target_c", 30.0, 65.0),
        ("hw_boost_delta_k", 2.0, 20.0),
        ("hw_idle_setpoint_c", 20.0, 60.0),
        ("hw_max_temp_c", 40.0, 70.0),
        ("hw_heat_power_kw", 0.5, 20.0),
        ("hw_cop_prior", 1.5, 8.0),
        ("hw_cop_prior_temp_c", -20.0, 35.0),
        ("hw_pv_min_export_w", 0, 20000),
        ("hw_min_off_sec", 0, 14400),
        ("hw_adhoc_delta_k", 2.0, 20.0),
        ("hw_decision_tolerance_pct", 0.0, 50.0),
        ("hw_min_charge_k", 0.5, 15.0),
        ("hw_min_charge_running_k", 0.5, 15.0),
        ("price_cheap_hours", 1.0, 20.0),
        ("price_hysteresis_min_eur", 0.0, 0.2),
        ("price_hysteresis_max_eur", 0.0, 0.5),
        ("price_flat_spread_eur", 0.0, 0.5),
        ("battery_wear_cost_eur_kwh", 0.0, 0.5),
        ("discharge_hold_min_spread_eur", 0.0, 0.5),
        ("feed_in_limit_w", 0, 100000),
        ("grid_export_limit_normal_w", 0, 100000),
        ("price_fee_eur_kwh", -0.5, 1.0),
        ("price_vat_pct", 0.0, 50.0),
        ("battery_plan_switch_cost_eur", 0.0, 1.0),
        ("failsafe_timeout_min", 0, 1440),
        ("write_budget_per_hour", 0, 3600),
        ("battery_full_charge_days", 0, 90),
        ("hw_hygiene_temp_c", 50.0, 70.0),
        ("hw_hygiene_interval_days", 1, 30),
        ("fuse_rated_a", 0.0, 250.0),
        ("fuse_work_limit_pct", 50.0, 100.0),
        ("phase_voltage_v", 100.0, 260.0),
        ("fuse_stale_sec", 10, 600),
        ("fuse_poll_sec", 5, 60),
        ("grid_dimming_limit_w", 0, 100000),
        ("battery_priority_soc_pct", 0, 100),
        ("ev_target_soc_pct", 10.0, 100.0),
        ("ev_capacity_kwh", 5.0, 250.0),
        ("ev_max_charge_kw", 1.0, 50.0),
        ("ev_max_current_a", 6.0, 64.0),
        ("ev_min_current_a", 1.0, 32.0),
        ("ev_phases_max", 1, 3),
        ("ev_morning_soc_pct", 0.0, 100.0),
        ("heating_offset_up_k", 0.0, 10.0),
        ("heating_offset_down_k", 0.0, 10.0),
        ("heating_compressor_max_w", 0.0, 30000.0),
        ("heating_cop_drop_pct_per_k", 0.0, 10.0),
        ("heating_sun_kwh", 0.0, 200.0),
        ("heating_comfort_min_c", 0.0, 25.0),
        ("heating_room_setpoint_c", 15.0, 26.0),
    )
    for name, lo, hi in clamps:
        value = getattr(cfg, name)
        clamped = max(lo, min(hi, value))
        if clamped != value:
            _LOGGER.warning("%s = %r is out of range (%s–%s) – clamped to %s",
                            name, value, lo, hi, clamped)
            setattr(cfg, name, clamped)

    if cfg.pv_forecast_scenario not in ("estimate", "estimate10", "blend"):
        _LOGGER.warning("pv_forecast_scenario = %r is unknown – using 'blend'",
                        cfg.pv_forecast_scenario)
        cfg.pv_forecast_scenario = "blend"
    if cfg.tariff_source not in ("auto", "timeslots", "rates"):
        _LOGGER.warning("tariff_source = %r is unknown – using 'auto'", cfg.tariff_source)
        cfg.tariff_source = "auto"
    if cfg.price_unit not in ("auto", "eur", "ct", "eur_mwh"):
        _LOGGER.warning("price_unit = %r is unknown – using 'auto'", cfg.price_unit)
        cfg.price_unit = "auto"
    if cfg.battery_plan_style not in ("careful", "balanced", "bold"):
        _LOGGER.warning("battery_plan_style = %r is unknown – using 'careful'",
                        cfg.battery_plan_style)
        cfg.battery_plan_style = "careful"
    for name, allowed, fallback in (
        ("charge_priority", ("battery", "ev"), "battery"),
        ("ev_battery_mode", ("block", "allow"), "block"),
        ("ev_pause_mode", ("zero_current", "switch"), "zero_current"),
    ):
        if getattr(cfg, name) not in allowed:
            _LOGGER.warning("%s = %r is unknown – using %r", name, getattr(cfg, name), fallback)
            setattr(cfg, name, fallback)
    if cfg.ev_min_current_a > cfg.ev_max_current_a:
        cfg.ev_min_current_a = cfg.ev_max_current_a
    for name, fallback in (("ev_departure_time", "07:00"), ("ev_morning_time", "06:00")):
        if parse_hhmm(getattr(cfg, name)) is None:
            _LOGGER.warning("%s = %r is not HH:MM – using %r", name, getattr(cfg, name), fallback)
            setattr(cfg, name, fallback)
    if cfg.fuse_guard_enabled and cfg.fuse_rated_a <= 0:
        _LOGGER.warning("fuse_guard_enabled but fuse_rated_a is 0 – the fuse guard stays off")
    if cfg.price_hysteresis_max_eur < cfg.price_hysteresis_min_eur:
        cfg.price_hysteresis_max_eur = cfg.price_hysteresis_min_eur

    if parse_hhmm(cfg.hw_morning_time) is None:
        _LOGGER.warning("hw_morning_time = %r is not HH:MM – using '06:30'", cfg.hw_morning_time)
        cfg.hw_morning_time = "06:30"
    if cfg.hw_day_target_c < cfg.hot_water_min_temp_c:
        _LOGGER.warning("hw_day_target_c (%s) below hot_water_min_temp_c (%s) – raised",
                        cfg.hw_day_target_c, cfg.hot_water_min_temp_c)
        cfg.hw_day_target_c = cfg.hot_water_min_temp_c
    # The idle setpoint must stay well below the minimum, or the heat pump's
    # own hysteresis would start charges miniEMS did not plan.
    idle_max = cfg.hot_water_min_temp_c - _HW_IDLE_MIN_GAP_K
    if cfg.hw_idle_setpoint_c > idle_max:
        _LOGGER.warning("hw_idle_setpoint_c (%s) not at least %s K below hot_water_min_temp_c "
                        "(%s) – lowered to %s", cfg.hw_idle_setpoint_c, _HW_IDLE_MIN_GAP_K,
                        cfg.hot_water_min_temp_c, idle_max)
        cfg.hw_idle_setpoint_c = idle_max
    # Shut-off must lie above the day target, or every charge would end at once.
    shutoff_min = cfg.hw_day_target_c + _HW_SHUTOFF_MIN_GAP_K
    if cfg.hw_morning_target_c < shutoff_min:
        _LOGGER.warning("hw_morning_target_c (shut-off, %s) not at least %s K above "
                        "hw_day_target_c (%s) – raised to %s", cfg.hw_morning_target_c,
                        _HW_SHUTOFF_MIN_GAP_K, cfg.hw_day_target_c, shutoff_min)
        cfg.hw_morning_target_c = shutoff_min
    if cfg.hw_max_temp_c < max(cfg.hw_morning_target_c, cfg.hw_day_target_c):
        new = max(cfg.hw_morning_target_c, cfg.hw_day_target_c)
        _LOGGER.warning("hw_max_temp_c (%s) below a hot-water target – raised to %s",
                        cfg.hw_max_temp_c, new)
        cfg.hw_max_temp_c = new

    if cfg.hw_min_charge_running_k > cfg.hw_min_charge_k:
        _LOGGER.warning("hw_min_charge_running_k (%s) above hw_min_charge_k (%s) – lowered",
                        cfg.hw_min_charge_running_k, cfg.hw_min_charge_k)
        cfg.hw_min_charge_running_k = cfg.hw_min_charge_k

    if cfg.pv_export_priority_enabled and not cfg.battery_control_enabled:
        _LOGGER.warning(
            "pv_export_priority_enabled has no effect while battery_control_enabled is off"
        )


def parse_hhmm(value: str) -> tuple[int, int] | None:
    """"06:30" → (6, 30); None when not a valid time of day."""
    try:
        h, m = (int(p) for p in str(value).strip().split(":"))
    except (TypeError, ValueError):
        return None
    if 0 <= h <= 23 and 0 <= m <= 59:
        return h, m
    return None


def _coerce_to_default_type(key: str, value: Any, default: Any) -> Any:
    """Bring a stored/option value to the type of the field's default.

    Older versions persisted some numeric fields as strings (a float missing
    from web_server._FLOAT_FIELDS fell through to str()); those strings would
    otherwise stay in config.json forever and crash the first comparison.
    """
    if value is None or isinstance(default, (str, dict, list)) or default is None:
        return value
    try:
        if isinstance(default, bool):
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("true", "1", "on", "yes")
        if isinstance(default, int):
            return value if type(value) is int else int(float(value))
        if isinstance(default, float):
            return value if type(value) is float else float(value)
    except (TypeError, ValueError):
        _LOGGER.warning("%s = %r is not a valid %s – using default %r",
                        key, value, type(default).__name__, default)
        return default
    return value


def load_config() -> Config:
    """Load, merge, migrate and persist configuration."""
    defs = _defaults()

    # Checked before _load_json (which returns {} either way) – the only
    # signal that distinguishes "genuinely new install" from "an ancient
    # unversioned config", both of which start migrate() at version 0. See
    # migration._v19_to_v20()'s device_detection_enabled default.
    is_fresh = not os.path.exists(CONFIG_FILE)

    # Load both sources
    stored = migrate(_load_json(CONFIG_FILE), is_fresh=is_fresh)
    options = _load_json(OPTIONS_FILE)

    # Carry forward renamed keys that may only exist in options.json
    for old_key, new_key in _OPTIONS_RENAMES.items():
        if old_key in options and new_key not in options:
            options[new_key] = options[old_key]
            _LOGGER.info("options.json rename: %s → %s = %r", old_key, new_key, options[new_key])

    merged: dict = {}
    for key in defs:
        opt_val = options.get(key)  # None = key not present in options.json

        # Switched at runtime from the settings page and persisted to
        # config.json only – a stale options.json must never flip them back
        # (e.g. silently going LIVE again after a restart).
        if key in CONTROL_MODE_KEYS:
            if opt_val is not None and opt_val != stored.get(key, defs[key]):
                _LOGGER.warning("options.json %s = %r ignored – control mode is set on the "
                                "settings page (config.json: %r)",
                                key, opt_val, stored.get(key, defs[key]))
            merged[key] = stored.get(key, defs[key])
            continue

        # options.json wins only when the key is explicitly present AND differs
        # from the dataclass default (i.e. the user actively changed it via UI).
        # A missing key (None) must NOT win over a previously saved value.
        if opt_val is not None and opt_val != defs[key]:
            merged[key] = opt_val
        else:
            # Fall back to persisted value; use default if not yet stored
            merged[key] = stored.get(key, defs[key])

    for key, value in merged.items():
        merged[key] = _coerce_to_default_type(key, value, defs[key])

    merged["_version"] = CURRENT_VERSION

    # Persist so the next startup survives an options.json reset
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2)
    except OSError as exc:
        _LOGGER.error("Failed to write %s: %s", CONFIG_FILE, exc)

    cfg = Config()
    for key, value in merged.items():
        if not key.startswith("_") and hasattr(cfg, key):
            setattr(cfg, key, value)

    _validate(cfg)

    _LOGGER.info(
        "Config loaded (v%d): %d entities monitored",
        CURRENT_VERSION,
        len(cfg.monitored_entities),
    )
    return cfg
