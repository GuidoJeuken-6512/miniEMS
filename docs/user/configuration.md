---
revision_date: 2026-04-07
---

# Konfiguration

Alle Einstellungen werden über den Tab **Einstellungen** im miniEMS-Dashboard verwaltet.
Die Werte werden in `/data/config.json` gespeichert und überleben Neustarts, Updates und Supervisor-Neuladen.

!!! info "Keine HA Add-on-Konfigurations-UI"
    Ab v1.4.0 ist der HA Add-on-Tab „Configuration" absichtlich leer.
    Die gesamte Konfiguration erfolgt über die miniEMS-Einstellungsseite.

---

## Wechselrichter-Entitäten

Diese findest du unter **HA → Entwicklerwerkzeuge → Zustände** — filtere nach `deye`, um die genauen Entitäts-IDs zu finden.

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `pv_power_entity` | `sensor.deye_pv_total_power` | Gesamte PV-Leistung (W) |
| `battery_soc_entity` | `sensor.deye_battery_soc` | Batterie-Ladezustand (%) |
| `battery_power_entity` | `sensor.deye_battery_power` | Batterieleistung (W) — positiv = Laden beim Deye 8K |
| `grid_power_entity` | `sensor.deye_grid_power` | Netzleistung (W) — positiv = Bezug, negativ = Einspeisung |
| `load_power_entity` | `sensor.deye_load_power` | Hauslast (W) |

---

## Batterieeinstellungen

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `battery_capacity_kwh` | `25.0` | Nutzbare Batteriekapazität in kWh |
| `battery_min_soc` | `10` | Mindest-SoC (%). Entladen wird darunter blockiert |
| `battery_max_soc` | `95` | Maximaler SoC (%). Laden stoppt, wenn erreicht |

---

## Strompreis

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `electricity_price_entity` | `sensor.octopus_a_10fc0646_electricity_price` | Aktueller Spotpreis-Sensor (€/kWh) |
| `cheap_rate_threshold_eur` | `0.28` | Netzladen wird ausgelöst, wenn der Preis **unter** diesem Wert liegt — auch die Obergrenze für den **Niedrig**-Tarif |
| `medium_rate_threshold_eur` | `0.20` | Tarifgrenze: **Niedrig** unter `cheap_rate_threshold_eur`, **Mittel** zwischen beiden, **Hoch** ab diesem Wert |
| `feed_in_tariff_eur_kwh` | `0.08` | Vergütung pro kWh ins Netz eingespeister Energie |
| `grid_import_energy_entity` | `sensor.deye8k_today_energy_import` | HA-Entität mit dem täglichen Netzbezug des Wechselrichters (kWh, Rücksetzen um Mitternacht). Wenn gesetzt, ersetzt sie den berechneten Importwert; Netzkosten werden weiterhin pro Tick akkumuliert. Leer lassen, um auf Berechnung aus `grid_power_entity` zurückzufallen. |
| `feed_in_energy_entity` | `sensor.deye8k_today_energy_export` | HA-Entität mit dem täglichen Einspeisewert des Wechselrichters (kWh, Rücksetzen um Mitternacht). Wenn gesetzt, ersetzt dieser den berechneten Einspeisewert. Leer lassen, um auf Berechnung aus `grid_power_entity` zurückzufallen. |
| `fix_price` | `0.30` | Festtarif für den Vergleichssensor „Kosten zum Festpreis" |

!!! info "Tariflogik"
    Drei Tarife klassifizieren den Verbrauch jedes Ticks für den Abschnitt [Tarifnutzung](dashboard.md#tarifnutzung) im Dashboard und die sechs `sensor.miniems_*kwh*rate`-HA-Sensoren:

    | Tarif | Bedingung |
    |---|---|
    | **Niedrig** | `price < cheap_rate_threshold_eur` |
    | **Mittel** | `cheap_rate_threshold_eur ≤ price < medium_rate_threshold_eur` |
    | **Hoch** | `price ≥ medium_rate_threshold_eur` |

    Der aktuelle Tarif wird neben dem Preis im Dashboard angezeigt (grün / gelb / rot).

---

## Batteriesteuerung

!!! warning "Zuerst den Simulationsmodus aktivieren"
    Bevor du die Live-Steuerung aktivierst, betreibe miniEMS mit aktiviertem `Simulationsmodus`. Überprüfe im Log, ob die richtigen Befehle für deinen Wechselrichter angezeigt werden.

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `battery_control_enabled` | `false` | Hauptschalter für die Wechselrichtersteuerung |
| `battery_control_simulation` | `true` | Befehle protokollieren, aber nicht senden |
| `inverter_charge_power_entity` | `number.deye_battery_charging_power` | Entität zum Setzen der Ladeleistung (W) |
| `grid_charge_switch_entity` | `switch.deye8k_battery_grid_charging` | Schalter-Entität zum Aktivieren/Deaktivieren des Netzladens |
| `battery_discharging_power_entity` | `number.deye8k_battery_discharging_power` | Entität zum Setzen des Entladeleistungslimits (W) |
| `battery_max_charge_power_w` | `5500` | Maximale Ladeleistung (W) |
| `battery_max_discharge_power_w` | `5500` | Maximale Entladeleistung (W) |
| `default_discharge_power_w` | `185` | Entladeleistung im Normalbetrieb (kein Netzladen) |

### Wie die Wechselrichtersteuerung funktioniert

| EMS-Modus | Netzlade-Schalter | Entladeleistung |
|---|---|---|
| Grid Charging | `switch.turn_on` | Auf `0` W setzen |
| PV Charging | `switch.turn_off` | Auf `default_discharge_power_w` zurücksetzen |
| Battery Protection | `switch.turn_off` | Auf `0` W setzen |
| Idle | `switch.turn_off` | Auf `default_discharge_power_w` zurücksetzen |

Befehle sind idempotent — miniEMS sendet einen Service-Call nur, wenn sich der Wert tatsächlich ändert.

---

## Solcast PV-Prognose

[Solcast](https://solcast.com/) liefert hochgenaue Dachanlagen-PV-Prognosen. Installiere die Solcast HA-Integration und konfiguriere die Entitäten hier.

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `solcast_remaining_today_entity` | `sensor.solcast_pv_forecast_prognose_verbleibende_leistung_heute` | Erwartete verbleibende PV für heute (kWh) — wird für die Netzladeentscheidung verwendet |
| `solcast_today_entity` | `sensor.solcast_pv_forecast_prognose_heute` | Gesamte erwartete PV für heute (kWh) — Dashboard-Anzeige |
| `solcast_tomorrow_entity` | `sensor.solcast_pv_forecast_prognose_morgen` | Erwartete PV für morgen (kWh) — Dashboard-Anzeige |

!!! tip "Warum der Solcast-Restwert wichtig ist"
    Die Netzladeentscheidung vergleicht `battery_kwh_freetochange` mit `solcast_remaining_today_kwh`.
    Wenn die Batterie mehr Platz hat, als die Sonne heute liefern kann, lädt miniEMS aus dem Netz.
    Falls Solcast nicht verfügbar ist, wird die interne temperaturbasierte Vorhersage als Fallback verwendet.

---

## Prognose & Vorhersage

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `weather_entity` | `weather.openweathermap` | HA-Wetter-Entität für temperaturbasierte Lastvorhersage |

Das Vorhersagemodell verwendet historische Verbrauchsdaten von Tagen mit ähnlicher Temperatur. Wenn noch keine Historie vorhanden ist, gelten temperaturbasierte Fallback-Regeln:

| Bedingung | Vorhergesagte Last |
|---|---|
| Nachttemperatur < 0 °C und Tagestemperatur < 0 °C | 30 kWh |
| Nachttemperatur < 0 °C und Tagestemperatur < 10 °C | 20 kWh |
| Nachttemperatur > 0 °C und Tagestemperatur < 15 °C | 10 kWh |

---

## EMS-Parameter

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `pv_surplus_threshold_w` | `200` | Mindest-PV-Überschuss (W) zum Auslösen des PV-Lademodus |
| `update_interval_sec` | `30` | Wie oft die EMS-Schleife läuft (Sekunden) |
| `event_log_retention_days` | `30` | Wie viele Tage Ereignislog-Einträge in der Datenbank aufbewahrt werden |

---

## Authentifizierung

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `long_lived_token` | *(leer)* | Langlebiger HA-Token — wird als Fallback verwendet, wenn der Supervisor-Token mit 401 abgelehnt wird. Normalerweise nicht erforderlich. |

Zum Erstellen: **HA → Profil → Langlebige Zugriffstoken → Token erstellen**.
