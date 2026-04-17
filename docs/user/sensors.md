---
revision_date: 2026-04-17
---

# Home-Assistant-Sensoren

miniEMS registriert **bis zu 34 Sensoren** in Home Assistant über die mitgelieferte benutzerdefinierte Integration.
Alle Entitäts-IDs verwenden das Präfix `sensor.miniems_`. Die Integration fragt `/api/status`
alle 30 s ab und registriert alle Entitäten unter dem **miniEMS**-Gerät mit Langzeit-
statistik-Unterstützung.

> **Umfang — nur Add-on-native.** Live-Leistungsmesswerte (PV, Last, Netz, Batterieleistung,
> SoC) und der Strompreis werden hier **nicht** dupliziert. Diese Sensoren existieren bereits
> in HA aus deiner Wechselrichter-Integration (z.B. Deye) und deiner Preis-Integration
> (z.B. Tibber, Octopus Energy). miniEMS liest diese Entitäten intern, veröffentlicht sie
> aber nicht erneut.

> **Szenario-2-Sensoren** (Wechselrichter-Effizienz, Energiebilanz-Netzladen, ROI) sind nur
> verfügbar, wenn die zugehörigen Inverter-Entitäten konfiguriert wurden
> (`today_production_entity`, `today_losses_entity`, `battery_discharge_entity` usw.).

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
| `sensor.miniems_battery_capacity_kwh` | kWh | Nutzbare Gesamtkapazität der Batterie |

```
free_to_charge = (max_soc − soc) / 100 × capacity_kwh
useable        = (soc − min_soc)  / 100 × capacity_kwh
```

> Wenn `battery_capacity_entity` konfiguriert ist, wird die Kapazität direkt vom Wechselrichter
> gelesen (z.B. 25,8 kWh). Andernfalls wird der Config-Wert `battery_capacity_kwh` verwendet.

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
| `sensor.miniems_today_cost_fix_price_tariff` | € | Was die heutige Last zum Festtarif kosten würde (inkl. Grundpreis wenn konfiguriert) |
| `sensor.miniems_today_base_price_eur` | € | Tagesfixkosten / Grundpreis (nur wenn `daily_base_price_eur > 0` konfiguriert) |

### Was bedeuten die Kostensensoren im Vergleich?

Die verschiedenen Kostensensoren ermöglichen einen direkten Szenario-Vergleich:

| Sensor | Szenario | Nutzen |
|---|---|---|
| `today_grid_cost_eur` | **Ist-Zustand**: Was du heute wirklich bezahlt hast (inkl. Netzladen) | Deine echte Tagesrechnung |
| `today_cost_without_grid_charge` | **Ohne Netzladen**: Was es ohne die Batterieladung aus dem Netz gekostet hätte | Zeigt, ob das Netzladen die Gesamtkosten erhöht hat |
| `today_load_cost_eur` | **Hypothetisch**: Volle Last zum dynamischen Spotpreis, als ob keine PV vorhanden wäre | Zeigt den Wert deiner PV-Anlage |
| `today_cost_fix_price_tariff` | **Festtarif-Vergleich**: Was die gleiche Last bei einem klassischen Festtarif kosten würde | Zeigt, ob der dynamische Tarif vorteilhafter ist |

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

## Szenario 2: Netzladen – Effizienz & ROI

Diese Sensoren sind **optional** und nur verfügbar, wenn folgende Entitäten in der Konfiguration
gesetzt sind: `today_production_entity`, `today_losses_entity`, `battery_discharge_entity`,
`battery_charge_entity`.

| Entität | Einheit | Beschreibung |
|---|---|---|
| `sensor.miniems_today_efficiency_pct` | % | Tageswirkungsgrad des Wechselrichters |
| `sensor.miniems_today_grid_charge_kwh_bilanz` | kWh | Aus dem Netz geladene Energie (Energiebilanzformel) |
| `sensor.miniems_today_grid_charge_cost_bilanz_eur` | € | Kosten für das Netzladen heute (bilanzbasiert) |
| `sensor.miniems_today_grid_charge_roi_eur` | € | Nettogewinn der Netzladestrategie heute |

### Was bedeuten diese Sensoren?

#### `miniems_today_efficiency_pct` — Wechselrichter-Wirkungsgrad

Der Wechselrichter wandelt gespeicherte Energie in Nutzstrom um — dabei entstehen Verluste.
Der Wirkungsgrad beschreibt, wie viel der produzierten PV-Energie tatsächlich nutzbar ankommt.

```
η = (PV-Produktion heute − Verluste heute) / PV-Produktion heute × 100
```

**Beispiel:** PV-Produktion 32,5 kWh, Verluste 2,6 kWh → η = 92,0 %

> Typische Werte: 88–95 %. Je höher, desto effizienter arbeitet der Wechselrichter.

---

#### `miniems_today_grid_charge_kwh_bilanz` — Netzladen (Energiebilanz)

Zeigt, wie viel Energie heute **tatsächlich aus dem Netz in die Batterie** geflossen ist.
Die Berechnung nutzt die Energiebilanzformel des Wechselrichters — präziser als eine
Näherung über die momentane Leistung:

```
Energie_Netzladen = Netz-Import heute − Verbrauch heute + Batterie-Entladung heute
```

**Warum nicht einfach den Netz-Import nehmen?**
Ein Teil des Netz-Imports deckt direkt den Haushaltsverbrauch. Die Formel subtrahiert
diesen Anteil heraus, sodass nur der echte Batterielade-Anteil übrig bleibt.

**Plausibilitätsprüfung:** Der Wert muss ≥ 0 und ≤ Batterie-Gesamtladung heute sein.

---

#### `miniems_today_grid_charge_roi_eur` — ROI der Netzladestrategie

Der wichtigste Sensor für die Bewertung der Netzladestrategie:

```
Gewinn = (Netzladen kWh × Wirkungsgrad × Ø Entladetarif) − Netzladekosten
```

- **Positiver Wert** → Das Netzladen hat sich heute finanziell gelohnt ✅
- **Negativer Wert** → Das Netzladen war teurer als der Nutzen ❌
- **Keine Anzeige** → Entladetarif (`avg_discharge_tariff_eur_kwh`) nicht konfiguriert

> **Konfiguration erforderlich:** `avg_discharge_tariff_eur_kwh` muss auf den durchschnittlichen
> Tarif gesetzt werden, zu dem der Speicher entladen wird (z.B. 0,394366 für reinen Abend-Hochtarif
> oder 0,369376 für gemischte Abend-/Nacht-Entladung).

**Konkretes Beispiel:**
```
Netzladen: 5,5 kWh, η: 92 %, Entladetarif: 0,394 €/kWh, Ladekosten: 1,51 €
Nutzbare Energie: 5,5 × 0,92 = 5,06 kWh
Einsparung: 5,06 × 0,394 = 1,99 €
ROI = 1,99 − 1,51 = +0,48 € ✅
```

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
