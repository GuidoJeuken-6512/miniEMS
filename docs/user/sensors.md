---
revision_date: 2026-04-07
---

# Home-Assistant-Sensoren

miniEMS registriert **28 Sensoren** in Home Assistant über die mitgelieferte benutzerdefinierte Integration.
Alle Entitäts-IDs verwenden das Präfix `sensor.miniems_`. Die Integration fragt `/api/status`
alle 30 s ab und registriert alle Entitäten unter dem **miniEMS**-Gerät mit Langzeit-
statistik-Unterstützung.

> **Umfang — nur Add-on-native.** Live-Leistungsmesswerte (PV, Last, Netz, Batterieleistung,
> SoC) und der Strompreis werden hier **nicht** dupliziert. Diese Sensoren existieren bereits
> in HA aus deiner Wechselrichter-Integration (z.B. Deye) und deiner Preis-Integration
> (z.B. Tibber, Octopus Energy). miniEMS liest diese Entitäten intern, veröffentlicht sie
> aber nicht erneut.

---

## Betriebsmodus

| Entität | Einheit | Beschreibung |
|---|---|---|
| `sensor.miniems_mode` | — | Aktueller EMS-Modus als Zeichenkette |

| Wert | Bedeutung |
|---|---|
| `Idle` | Keine aktive Aktion — nur Überwachung |
| `PV Charging` | PV-Überschuss erkannt — Laden aus Solarenergie |
| `Grid Charging (Cheap Rate)` | Günstigtarif aktiv und Batterie muss geladen werden |
| `Battery Protection (Min SoC)` | SoC unter Minimum — Entladen blockiert |

---

## Batteriezustand

Berechnet aus dem aktuellen SoC und den konfigurierten Kapazitäts- und SoC-Grenzen.

| Entität | Einheit | Beschreibung |
|---|---|---|
| `sensor.miniems_battery_kwh_freetochange` | kWh | Freiraum bis zum maximalen SoC (ladbare Kapazität) |
| `sensor.miniems_battery_kwh_useable` | kWh | Verfügbar bis zum minimalen SoC (entladbare Kapazität) |

```
free_to_charge = (max_soc − soc) / 100 × capacity_kwh
useable        = (soc − min_soc)  / 100 × capacity_kwh
```

---

## Heutige Energie

Akkumuliert ab Mitternacht; tägliches Rücksetzen. `state_class: total_increasing`.

| Entität | Einheit | Beschreibung |
|---|---|---|
| `sensor.miniems_today_pv_used_kwh` | kWh | Heute vom Haus selbst verbrauchte PV-Energie |
| `sensor.miniems_today_load_total_kwh` | kWh | Gesamte Hauslast heute |
| `sensor.miniems_today_grid_charge_kwh` | kWh | Heute aus dem Netz in die Batterie geladene Energie |

---

## Heutige Kosten & Einsparungen

| Entität | Einheit | Beschreibung |
|---|---|---|
| `sensor.miniems_today_grid_cost_eur` | € | Tatsächliche Kosten des Netzbezugs heute |
| `sensor.miniems_today_pv_savings_eur` | € | Einsparungen durch PV-Eigenverbrauch heute |
| `sensor.miniems_today_load_cost_eur` | € | Hypothetische Kosten, wenn alle Last aus dem Netz bezogen würde |
| `sensor.miniems_today_feed_in_revenue_eur` | € | Einnahmen aus Netzeinspeisung heute |
| `sensor.miniems_today_cost_without_grid_charge` | € | Netzkosten abzüglich des für die Netzladung gezahlten Anteils |
| `sensor.miniems_today_cost_fix_price_tariff` | € | Was die heutige Last zum konfigurierten Festtarif kosten würde |

---

## Tarifnutzung

Last (kWh) aufgeteilt nach Strompreistarif. Tarifgrenzen werden durch
`cheap_rate_threshold_eur` und `medium_rate_threshold_eur` in den Einstellungen festgelegt.

| Entität | Einheit | Beschreibung |
|---|---|---|
| `sensor.miniems_today_kwh_high_rate` | kWh | Last heute beim **hohen** Tarif (`price ≥ medium_rate_threshold`) |
| `sensor.miniems_today_kwh_medium_rate` | kWh | Last heute beim **mittleren** Tarif |
| `sensor.miniems_today_kwh_low_rate` | kWh | Last heute beim **niedrigen** Tarif (`price < cheap_rate_threshold`) |
| `sensor.miniems_month_kwh_high_rate` | kWh | Last diesen Kalendermonat beim **hohen** Tarif |
| `sensor.miniems_month_kwh_medium_rate` | kWh | Last diesen Kalendermonat beim **mittleren** Tarif |
| `sensor.miniems_month_kwh_low_rate` | kWh | Last diesen Kalendermonat beim **niedrigen** Tarif |

Alle sechs Sensoren haben `state_class: total_increasing` und werden beim Start aus der
Datenbank wiederhergestellt.

---

## Wochen-/Monats-/Jahrestotale

Aggregiert aus der Datenbanktabelle `daily_stats`.

| Entität | Einheit | Beschreibung |
|---|---|---|
| `sensor.miniems_week_grid_cost_eur` | € | Rollende 7-Tage-Netzkosten |
| `sensor.miniems_week_pv_savings_eur` | € | Rollende 7-Tage-PV-Einsparungen |
| `sensor.miniems_month_grid_cost_eur` | € | Kalendermonat-Netzkosten |
| `sensor.miniems_month_pv_savings_eur` | € | Kalendermonat-PV-Einsparungen |
| `sensor.miniems_month_load_cost_eur` | € | Kalendermonat hypothetische Vollnetz-Kosten |
| `sensor.miniems_year_grid_cost_eur` | € | Kalenderjahr-Netzkosten |
| `sensor.miniems_year_pv_savings_eur` | € | Kalenderjahr-PV-Einsparungen |
| `sensor.miniems_year_load_cost_eur` | € | Kalenderjahr hypothetische Vollnetz-Kosten |

---

## Vorhersagen

| Entität | Einheit | Beschreibung |
|---|---|---|
| `sensor.miniems_predicted_load_kwh` | kWh | Vorhergesagte tägliche Hauslast (temperaturabgeglichene historische Daten) |
| `sensor.miniems_predicted_pv_kwh` | kWh | Interne PV-Ertragsschätzung (Fallback wenn Solcast nicht verfügbar) |

---

## Sensoren in HA verwenden

### Beispiel Lovelace-Karte

```yaml
type: entities
title: miniEMS
entities:
  - sensor.miniems_mode
  - sensor.miniems_today_grid_cost_eur
  - sensor.miniems_today_pv_savings_eur
  - sensor.miniems_today_cost_without_grid_charge
  - sensor.miniems_battery_kwh_freetochange
  - sensor.miniems_battery_kwh_useable
  - sensor.miniems_predicted_load_kwh
```

### Beispiel-Automatisierung

```yaml
alias: Benachrichtigung wenn Günstigtarif beginnt
trigger:
  - platform: state
    entity_id: sensor.miniems_mode
    to: "Grid Charging (Cheap Rate)"
action:
  - service: notify.mobile_app_your_phone
    data:
      message: "miniEMS: Laden aus günstigem Netzstrom."
```
