Sensores from Home assistant:
PV inverter :
sensor.deye8k_pv_power
sensor.deye8k_battery (SOC)
sensor.deye8k_battery_power
sensor.deye8k_grid_power
sensor.deye8k_load_power
number.deye8k_battery_charging_power
number.deye8k_battery_discharging_power
switch.deye8k_battery_grid_charging

electricity price (dynamical)
sensor.octopus_a_10fc0646_electricity_price

Wetter forecast:
weather.openweathermap

PV-forecast:
sensor.solcast_pv_forecast_prognose_heute
sensor.solcast_pv_forecast_prognose_morgen
sensor.solcast_pv_forecast_prognose_verbleibende_leistung_heute

additional config fields :
prices:
Cheap Rate Threshold (€/kWh) default=0,28
feed-in tariff for solar power (€/kWh) default = 0,08
fix_price (€) default 0,30
inverter:
Max Charge Power (W) default=5500
Max Discharge Power (W) default=5500
Battery:
Capacity (kWh) default: 25
min SOC Default 10
max SOC Default 95

internal calculated Sensores published to HA as mqtt device / Sensors:
Battery kwh freetochange = max SOC – SOC/100 _ Battery Capacity
Battery kwh useable = (SOC – min SOC) / 100 _ Battery Capacity
predicted_load_kwh = s. Logik 2) prediction
real_costs (€)
cost_without_solar (€)
cost_without_grid_charge (€)
cost_fix_price_tarif (€)
Logic

1. Logic Grid charge→Battery
   simulation mode (log only)
   Battery Control mode (controll inverter to charge battery from grid)

schedule every 5 min:
if energy price < „Cheap Rate Threshold“
and Battery kwh freetochange > sensor.solcast_pv_forecast_prognose_verbleibende_leistung_heute
→ Grid Charge = on and number.deye8k_battery_discharging_power =0 (charge from grid and do not use battery to load)
if not Grid Charge = off and number.deye8k_battery_discharging_power =185

2. Prediction
   save todays power consumption, todays high&low temperature in local DB

predicted_load_kwh= get temperatur high & low from weather.openweathermap
search for similar temperatur vaulues in DB → get power consumption of simular day
if no simular days in DB → show in FrontEnd (estimate)
then if min temp <0 and max temp < 0 = 30 kWh
then if min temp <0 and max temp < 10 = 20 kWh
then if min temp > 0 and max temp < 15 =10 kWh 3) Track energy prices and consumption
alle prices as today, monthly, yearly, total (save in Addon DB) → publish to HA
track power deltas to calculate costs, reboot and error resiliens (Check whether the value is realistic (last value vs. current value; if unrealistic, use the next value for the delta calculation) save last value for resilience)

cost_without_solar (€) = (load_power _ price)
cost_fix_price_tarif (€) = (load_power _ fix_price)

additional changes:
remove APP configuration settings, configuration is only done in the FrontEnd
Show info in Frontend, if nesessary configuration is missed
Show info in Frontend, if nesessary sensors have no value
Show Log in Fronteend (grid charge mode changed with on/off, dateTime, Battery kwh freetochange, Battery kwh useable, predicted_load_kwh)
