---
revision_date: 2026-04-07
---

# HA-Sensor-Referenz

miniEMS stellt **28 native Home-Assistant-Sensoren** über die benutzerdefinierte Integration bereit,
die in `miniems/rootfs/usr/bin/integration/` enthalten ist. Die Integration ruft
`/api/status` alle 30 Sekunden ab und registriert alle Sensoren unter einem einzelnen
**miniEMS**-Gerät.

Alle Entity-IDs verwenden das Präfix `sensor.miniems_`.

> **Umfang:** Hier werden nur addon-native, berechnete Werte registriert. Live-Leistungswerte
> (PV, Last, Netz, Akkuleistung, SoC) und der Strompreis sind bereits über die
> Deye-/Tibber-/Octopus-Integration in HA vorhanden — miniEMS liest diese Entities,
> dupliziert sie jedoch nicht.

---

## Datenfluss

```
EMS Controller (30 s Tick)
  └─► status_store (in-memory dict, main.py)
        └─► Web Server  GET /api/status
              └─► HA Coordinator (ruft alle 30 s ab)
                    └─► Sensor-Entities in Home Assistant
```

Spike-gefilterte Live-Leistungswerte werden von `SensorValidator` validiert, bevor
sie den Status-Store erreichen. Akkumulatoren (heute/Monat/Jahr) werden in SQLite
gespeichert und beim Add-on-Neustart wiederhergestellt.

---

## Entity Registry — Bereinigung von verwaisten Einträgen

`async_setup_entry` in `integration/__init__.py` entfernt beim Start veraltete
Entity-Registry-Einträge aus früheren miniEMS-Config-Entries. Diese Einträge
können `_2`- oder `_3`-Suffixe bei Entity-IDs verursachen, wenn die Integration
gelöscht und erneut hinzugefügt wird.

---

## Betriebsmodus

| Entity-ID | Key | Einheit | Device Class | State Class |
|---|---|---|---|---|
| `sensor.miniems_mode` | `mode` | — | — | `measurement` |

Aktueller EMS-Betriebsmodus. Mögliche Werte: `Idle`, `PV Charging`,
`Grid Charging (Cheap Rate)`, `Battery Protection (Min SoC)`.

Icon: `mdi:home-lightning-bolt`

---

## Akkuzustand (berechnet)

Abgeleitet von `battery_model.py` aus dem aktuellen SoC, der Akkukapazität und
den konfigurierten Min/Max-SoC-Grenzen.

| Entity-ID | Key | Einheit | Device Class | State Class | Beschreibung |
|---|---|---|---|---|---|
| `sensor.miniems_battery_kwh_freetochange` | `battery_kwh_freetochange` | kWh | `energy` | `measurement` | Ladekapazität bis zum maximalen SoC |
| `sensor.miniems_battery_kwh_useable` | `battery_kwh_useable` | kWh | `energy` | `measurement` | Entladekapazität bis zum minimalen SoC |

---

## Heutige Energie

Akkumuliert ab Mitternacht; täglich zurückgesetzt. In der SQLite-Tabelle `daily_stats` gespeichert.

| Entity-ID | Key | Einheit | Device Class | State Class | Beschreibung |
|---|---|---|---|---|---|
| `sensor.miniems_today_pv_used_kwh` | `today_pv_used_kwh` | kWh | `energy` | `total_increasing` | PV-Eigenverbrauch des Hauses |
| `sensor.miniems_today_load_total_kwh` | `today_load_total_kwh` | kWh | `energy` | `total_increasing` | Gesamtverbrauch des Hauses |
| `sensor.miniems_today_grid_charge_kwh` | `today_grid_charge_kwh` | kWh | `energy` | `total_increasing` | Aus dem Netz in den Akku geladene Energie |

!!! note "Entfernte Sensoren"
    `sensor.miniems_today_feed_in_kwh` und `sensor.miniems_today_grid_import_kwh`
    wurden entfernt, da diese Werte direkt von den Deye-Entities stammen und nicht
    addon-nativ sind. Für Einspeisung und Netzimport die entsprechenden
    Deye-Sensoren (`sensor.deye8k_today_energy_export` und
    `sensor.deye8k_today_energy_import`) direkt verwenden.

---

## Heutige Tarifklassen

Hausverbrauch aufgeteilt nach Preisklasse. Schwellenwerte über
`cheap_rate_threshold` und `medium_rate_threshold` in den Einstellungen konfigurierbar.

| Entity-ID | Key | Einheit | Device Class | State Class | Beschreibung |
|---|---|---|---|---|---|
| `sensor.miniems_today_kwh_high_rate` | `today_kwh_high_rate` | kWh | `energy` | `total_increasing` | Last in Hochpreiszeiten |
| `sensor.miniems_today_kwh_medium_rate` | `today_kwh_medium_rate` | kWh | `energy` | `total_increasing` | Last in mittleren Preiszeiten |
| `sensor.miniems_today_kwh_low_rate` | `today_kwh_low_rate` | kWh | `energy` | `total_increasing` | Last in Niedrigpreiszeiten |

---

## Heutige Kosten & Einsparungen

| Entity-ID | Key | Einheit | Device Class | State Class | Beschreibung |
|---|---|---|---|---|---|
| `sensor.miniems_today_grid_cost_eur` | `today_grid_cost_eur` | € | `monetary` | `total_increasing` | Tatsächliche Kosten des Netzimports |
| `sensor.miniems_today_pv_savings_eur` | `today_pv_savings_eur` | € | `monetary` | `total_increasing` | Einsparungen durch PV-Eigenverbrauch |
| `sensor.miniems_today_load_cost_eur` | `today_load_cost_eur` | € | `monetary` | `total_increasing` | Hypothetische Kosten wenn alle Last aus dem Netz |
| `sensor.miniems_today_feed_in_revenue_eur` | `today_feed_in_revenue_eur` | € | `monetary` | `total_increasing` | Einnahmen aus Netzeinspeisung |
| `sensor.miniems_today_cost_without_grid_charge` | `today_cost_without_grid_charge` | € | `monetary` | `total_increasing` | Netzkosten abzüglich Akku-Ladekosten |
| `sensor.miniems_today_cost_fix_price_tariff` | `today_cost_fix_price_tariff` | € | `monetary` | `total_increasing` | Heutige Last zum konfigurierten Festtarif (`fix_price`) |

---

## Wochentotale

Rollendes 7-Tage-Fenster, summiert aus `daily_stats`.

| Entity-ID | Key | Einheit | Device Class | State Class | Beschreibung |
|---|---|---|---|---|---|
| `sensor.miniems_week_grid_cost_eur` | `week_grid_cost_eur` | € | `monetary` | `measurement` | 7-Tage-Netzkosten |
| `sensor.miniems_week_pv_savings_eur` | `week_pv_savings_eur` | € | `monetary` | `measurement` | 7-Tage-PV-Einsparungen |

---

## Monatstotale

Kalendermonat, summiert aus `daily_stats`.

| Entity-ID | Key | Einheit | Device Class | State Class | Beschreibung |
|---|---|---|---|---|---|
| `sensor.miniems_month_grid_cost_eur` | `month_grid_cost_eur` | € | `monetary` | `measurement` | Monatliche Netzkosten |
| `sensor.miniems_month_pv_savings_eur` | `month_pv_savings_eur` | € | `monetary` | `measurement` | Monatliche PV-Einsparungen |
| `sensor.miniems_month_load_cost_eur` | `month_load_cost_eur` | € | `monetary` | `measurement` | Monatliche hypothetische Vollnetzkosten |
| `sensor.miniems_month_kwh_high_rate` | `month_kwh_high_rate` | kWh | `energy` | `total_increasing` | Monatliche Last bei Hochpreistarifen |
| `sensor.miniems_month_kwh_medium_rate` | `month_kwh_medium_rate` | kWh | `energy` | `total_increasing` | Monatliche Last bei mittlerem Tarif |
| `sensor.miniems_month_kwh_low_rate` | `month_kwh_low_rate` | kWh | `energy` | `total_increasing` | Monatliche Last bei Niedrigpreistarifen |

---

## Jahrestotale

Kalenderjahr, summiert aus `daily_stats`.

| Entity-ID | Key | Einheit | Device Class | State Class | Beschreibung |
|---|---|---|---|---|---|
| `sensor.miniems_year_grid_cost_eur` | `year_grid_cost_eur` | € | `monetary` | `measurement` | Jährliche Netzkosten |
| `sensor.miniems_year_pv_savings_eur` | `year_pv_savings_eur` | € | `monetary` | `measurement` | Jährliche PV-Einsparungen |
| `sensor.miniems_year_load_cost_eur` | `year_load_cost_eur` | € | `monetary` | `measurement` | Jährliche hypothetische Vollnetzkosten |

---

## Vorhersagen

Berechnet von `consumption_model.py` (Last) und `solcast_client.py` / internem
Modell (PV). Einmal pro Tag oder bei Bedingungsänderungen aktualisiert.

| Entity-ID | Key | Einheit | Device Class | State Class | Beschreibung |
|---|---|---|---|---|---|
| `sensor.miniems_predicted_load_kwh` | `predicted_load_kwh` | kWh | `energy` | `measurement` | Vorhergesagter täglicher Hausverbrauch |
| `sensor.miniems_predicted_pv_kwh` | `predicted_pv_kwh` | kWh | `energy` | `measurement` | Interne PV-Ertragsschätzung (Fallback wenn Solcast nicht verfügbar) |

### Vorhersagequelle

Die `/api/status`-Antwort enthält ein `prediction_source`-Feld (kein HA-Sensor):

| Wert | Bedeutung |
|---|---|
| `"historical"` | Median temperaturähnlicher Tage aus `daily_stats` |
| `"fallback"` | Temperaturregelbasierte Schätzung |

---

## Sensor-Anzahl-Zusammenfassung

| Kategorie | Anzahl |
|---|---|
| Betriebsmodus | 1 |
| Akkuzustand (berechnet) | 2 |
| Heutige Energie | 3 |
| Heutige Tarifklassen | 3 |
| Heutige Kosten & Einsparungen | 6 |
| Wochentotale | 2 |
| Monatstotale | 6 |
| Jahrestotale | 3 |
| Vorhersagen | 2 |
| **Gesamt** | **28** |

---

## Felder in `/api/status` ohne HA-Sensor-Entity

Die folgenden Felder werden vom REST-Endpunkt zurückgegeben und vom Dashboard
verwendet, sind aber **nicht** als Home-Assistant-Sensor-Entities registriert:

| Feld | Typ | Beschreibung |
|---|---|---|
| `is_cheap_rate` | bool | Aktueller Preis unter dem Günstigtarifschwellenwert |
| `price_tier` | string | `"low"` / `"medium"` / `"high"` |
| `battery_control_active` | bool | Akkusteuerungsschleife läuft |
| `battery_control_simulation` | bool | Simulationsmodus aktiv |
| `warnings` | array | Konfigurations-/Sensor-Verfügbarkeitswarnungs-Schlüssel |
| `log` | array | Ereignislog-Einträge (Modus- und Preisänderungen) |
| `solcast_remaining_today_kwh` | float? | Verbleibende Solcast-Vorhersage für heute |
| `solcast_today_kwh` | float? | Solcast-Gesamtvorhersage heute |
| `solcast_tomorrow_kwh` | float? | Solcast-Gesamtvorhersage morgen |
| `charge_power_limit_w` | int? | Aktives Ladelimits |
| `discharge_power_limit_w` | int? | Aktives Entladelimit |
| `prediction_confidence` | string | `"high"` / `"medium"` / `"low"` / `"none"` |
| `prediction_source` | string | `"historical"` oder `"fallback"` |
| `temp_today_c` | float? | Heutige Außentemperatur von der HA-Wetter-Entity |
| `temp_tomorrow_c` | float? | Außentemperaturvorhersage für morgen |

---

## Integrations-Datei-Standorte

| Datei | Zweck |
|---|---|
| `integration/sensor.py` | `SENSOR_DESCRIPTIONS`-Tuple — alle 28 Sensor-Definitionen |
| `integration/coordinator.py` | `DataUpdateCoordinator` — ruft `/api/status` alle 30 s ab |
| `integration/__init__.py` | Integration-Setup, Geräte-Registrierung und Entity-Registry-Bereinigung |
