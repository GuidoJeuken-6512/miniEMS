---
revision_date: 2026-04-07
---

# Berechnungen

## EMS-Moduse-Entscheidung (`EMSController._determine_mode`)

Der Controller bewertet vier sich gegenseitig ausschließende Modi in Prioritätsreihenfolge:

```
1. BATTERY_PROTECTION  — battery_soc < battery_min_soc
2. PV_CHARGING         — (pv_w − load_w) > pv_surplus_threshold_w  AND  soc bekannt UND soc < max_soc
3. GRID_CHARGING       — price < cheap_rate_threshold_eur
                         AND  soc bekannt UND soc < max_soc
                         AND  bat_kwh_free > solcast_remaining_today_kwh
                              (Fallback auf Verbrauchsmodell wenn Solcast nicht verfügbar)
4. IDLE                — keiner der obigen Fälle, ODER SoC-Sensor nicht verfügbar
```

!!! note "SoC-Sensor nicht verfügbar"
    Wenn der `battery_soc_entity`-Sensor keinen Wert liefert, werden PV Charging
    und Grid Charging deaktiviert und das System verbleibt im **IDLE**-Modus.
    Dies ist eine Sicherheitsmaßnahme, um unkontrolliertes Laden bei unbekanntem
    Akkustand zu verhindern.

### PV-Überschuss

```
surplus_w = pv_power_w - load_power_w
```

Wenn `surplus_w > pv_surplus_threshold_w` (Standard: 200 W) und der Akku nicht voll ist
(`soc < battery_max_soc`), wechselt das System in den Modus **PV Charging**.

### Netzlade-Entscheidung

```python
bat_kwh_free = (max_soc - soc) / 100 * capacity_kwh

# Primärpfad: Solcast verfügbar
should_grid_charge = bat_kwh_free > solcast_remaining_today_kwh

# Fallback: kein Solcast
should_grid_charge = (predicted_load > 0) AND (useable_kwh + predicted_pv < predicted_load)
```

Der Solcast-Pfad wird bevorzugt, da er tatsächliche Solarstrahlungsprognosen berücksichtigt.
Der Fallback verwendet das temperaturbasierte Verbrauchsmodell.

---

## Akkuzustand (`BatteryModel`)

```
free_to_charge_kwh = max(0,  (max_soc − soc) / 100  × capacity_kwh)
useable_kwh        = max(0,  (soc − min_soc) / 100  × capacity_kwh)
```

---

## Kosten & Einsparungen — vollständige Referenz (`CostOptimizer`)

`CostOptimizer.record_tick()` wird einmal pro EMS-Tick (Standard: 30 s) aufgerufen.
Alle Akkumulatoren sind nach Kalenderdatum geordnet und werden nach jedem Tick in
SQLite gespeichert. Werte werden mit 6 Dezimalstellen gespeichert. Beim Neustart
werden die heutigen Akkumulatoren vor dem ersten Tick aus SQLite wiederhergestellt.

### Voraussetzung: Spike-Filterung

Jeder Leistungswert wird vor der Verwendung von `SensorValidator` validiert.
Ein Messwert wird abgelehnt (durch den letzten akzeptierten Wert ersetzt), wenn:

```
|delta| > 500 W  AND  |delta| / vorheriger_Wert > 50 %
```

Wenn kein vorheriger Wert für einen Sensor vorhanden ist, wird 0 W als Fallback verwendet.

### Intervall-Dauer

```
hours = update_interval_sec / 3600    # Standard: 30 s → 0.008333 h
```

---

### Netzimport & Kosten (`today_grid_import_kwh`, `today_grid_cost_eur`)

#### kWh — Quelle A (bevorzugt)

Wenn `grid_import_energy_entity` konfiguriert ist (Standard: `sensor.deye8k_today_energy_import`),
wird der eigene Tageszähler des Wechselrichters direkt verwendet. Der Wert wird pro Tick **gesetzt**:

```
grid_import_kwh = grid_import_energy_entity   ← pro Tick direkt aus HA gelesen
```

#### kWh — Quelle B (berechneter Fallback)

Wenn `grid_import_energy_entity` leer oder nicht verfügbar ist, wird der kWh-Wert
aus `grid_power_w` akkumuliert, **nur wenn `grid_power_w > 0`** (Nettobezug):

```
kwh_imported     = (grid_power_w / 1000) × hours
grid_import_kwh += kwh_imported               ← pro Tick akkumuliert
```

#### Kosten — immer pro Tick akkumuliert

Netzkosten können nicht aus einem Tagessummensensor abgeleitet werden, da der
Spotpreis zum jeweiligen Intervall benötigt wird. Sie werden immer aus Ticks akkumuliert:

```
grid_cost_eur += (grid_power_w / 1000) × hours × price_eur_kwh
                 (nur wenn grid_power_w > 0)
```

`price_eur_kwh` ist der aktuelle dynamische Spotpreis aus `electricity_price_entity`.

---

### PV-Einsparungen (`today_pv_savings_eur`)

Repräsentiert die **vermiedenen** Stromkosten durch PV-Nutzung anstelle von Netzkauf.
Nur der Anteil der PV, der direkt den Hausverbrauch deckt, wird berücksichtigt —
ins Netz eingespeiste PV ist hier ausgeschlossen (siehe Einspeisung weiter unten).

```
pv_to_load_w    = clamp(pv_power_w, 0, load_power_w)
kwh_pv_used     = (pv_to_load_w / 1000) × hours
pv_used_kwh    += kwh_pv_used
pv_savings_eur += kwh_pv_used × price_eur_kwh
```

Die Bewertung erfolgt zum **aktuellen Spotpreis**, daher trägt PV bei günstigem Tarif
weniger zur Einsparung bei als PV bei Spitzentarif.

---

### Gesamtlastkosten (`today_load_cost_eur`)

Hypothetische Kosten, wenn der **gesamte** Hausverbrauch zum aktuellen Spotpreis
aus dem Netz bezogen worden wäre, unabhängig von der tatsächlichen Quelle (PV, Akku, Netz):

```
load_kwh       = (load_power_w / 1000) × hours
load_total_kwh += load_kwh
load_cost_eur  += load_kwh × price_eur_kwh
```

Immer ≥ `today_grid_cost_eur`, weil PV und Akku den tatsächlichen Netzbezug reduzieren.

---

### Netz-zu-Akku-Ladekosten (`today_grid_charge_cost_eur`)

Abgeleitet aus der Leistungsbilanz — modusunabhängig, kein EMS-Zustand erforderlich.
Die Deye-Vorzeichenkonvention lautet: `battery_power_w > 0` = Entladen,
`battery_power_w < 0` = Laden.

```
battery_charge_w = max(0, −battery_power_w)       # positiv beim Laden
pv_surplus_w     = max(0, pv_power_w − load_power_w)
grid_charge_w    = max(0, battery_charge_w − pv_surplus_w)

kwh_gc              = (grid_charge_w / 1000) × hours
grid_charge_kwh    += kwh_gc
grid_charge_cost_eur += kwh_gc × price_eur_kwh
```

`grid_charge_w` ist der Teil der Akkuleistung, der nicht durch überschüssige PV
gedeckt werden kann — er muss daher aus dem Netz stammen.

---

### Einspeisevergütung (`today_feed_in_revenue_eur`)

#### Quelle A — HA-Sensor (bevorzugt)

Wenn `feed_in_energy_entity` konfiguriert ist (Standard: `sensor.deye8k_today_energy_export`),
wird der eigene Tagesexportzähler des Wechselrichters direkt verwendet. Der Sensor
wird um Mitternacht zurückgesetzt und liefert einen kumulativen kWh-Gesamtwert.
Der Wert wird pro Tick **gesetzt**, nicht akkumuliert:

```
feed_in_kwh     = feed_in_energy_entity   ← pro Tick direkt aus HA gelesen
feed_in_revenue = feed_in_kwh × feed_in_tariff_eur_kwh
```

#### Quelle B — berechneter Fallback

Wenn `feed_in_energy_entity` leer oder die Entity nicht verfügbar ist, wird die
Einspeisung aus `grid_power_w` abgeleitet, **nur wenn `grid_power_w < 0`** (Nettoexport):

```
feed_in_w        = max(0, −grid_power_w)
kwh_exported     = (feed_in_w / 1000) × hours
feed_in_kwh     += kwh_exported                   ← pro Tick akkumuliert
feed_in_revenue += kwh_exported × feed_in_tariff_eur_kwh
```

Beide Quellen verwenden den **festen Einspeisevergütungssatz** (`feed_in_tariff_eur_kwh`,
Standard: 0,08 €/kWh), nicht den Spotpreis.

---

### Abgeleitete Metriken (berechnet in `ems_controller.py`)

Diese werden einmal pro Tick aus den akkumulierten Werten oben berechnet und
dem Status-Dict hinzugefügt.

| Entity | Formel | Bedeutung |
| ---|---|---|
| `today_cost_without_grid_charge` | `max(0, grid_cost_eur − grid_charge_cost_eur)` | Was die Netzrechnung ohne Akku-Netzladung gewesen wäre |
| `today_cost_fix_price_tariff` | `load_total_kwh × fix_price` | Was die heutige Last beim festen Referenztarif kosten würde (Standard: 0,30 €/kWh) |

---

### Wöchentliche Aggregation (In-Memory)

In `CostOptimizer` aus den In-Memory-Tagesbuckets ohne DB-Abfrage berechnet:

```
week_grid_cost_eur  = Σ grid_cost_eur[d]   für alle d mit (today − d).days < 7
week_pv_savings_eur = Σ pv_savings_eur[d]  für alle d mit (today − d).days < 7
```

Das rollende Fenster umfasst genau 7 Kalendertage (heute + 6 vorangegangene Tage).

---

### Monatliche / Jährliche Aggregation (SQLite)

`CostOptimizer.summary_with_db()` fragt die `daily_stats`-Tabelle ab:

```sql
-- Monat
SELECT SUM(grid_cost_eur), SUM(pv_savings_eur), SUM(load_cost_eur)
FROM daily_stats WHERE date LIKE 'YYYY-MM-%'

-- Jahr
SELECT SUM(grid_cost_eur), SUM(pv_savings_eur), SUM(load_cost_eur)
FROM daily_stats WHERE strftime('%Y', date) = 'YYYY'
```

| Entities | Quelle |
| ---|---|
| `month_grid_cost_eur`, `month_pv_savings_eur`, `month_load_cost_eur` | Kalendermonat-SUM aus DB |
| `year_grid_cost_eur`, `year_pv_savings_eur`, `year_load_cost_eur` | Kalenderjahr-SUM aus DB |

---

## Preisklassen-Verbrauch

Bei jedem Tick wird `load_kwh` genau einem von drei Tarifklassen-Buckets
basierend auf dem aktuellen Spotpreis zugeordnet. Die drei Buckets summieren sich
immer zu `today_load_total_kwh` für den Tag.

### Klassenzuweisung

```
if price_eur_kwh < cheap_rate_threshold_eur:
    kwh_low_rate    += load_kwh          # günstig / cheap
elif price_eur_kwh < medium_rate_threshold_eur:
    kwh_medium_rate += load_kwh          # mittel / medium
else:
    kwh_high_rate   += load_kwh          # teuer / high
```

### Klassengrenzen

| Klasse | Bedingung | Config-Schlüssel | Standard |
| ---|---|---|---|
| `low` | `price < cheap_rate_threshold_eur` | `cheap_rate_threshold_eur` | 0,10 €/kWh |
| `medium` | `cheap_rate ≤ price < medium_rate_threshold_eur` | `medium_rate_threshold_eur` | 0,20 €/kWh |
| `high` | `price ≥ medium_rate_threshold_eur` | `medium_rate_threshold_eur` | 0,20 €/kWh |

Beide Schwellenwerte sind auf der Einstellungsseite konfigurierbar.

### Tages-Entities

| Entity | Akkumulator | Zurückgesetzt |
| ---|---|---|
| `today_kwh_low_rate` | In-Memory, beim Neustart aus DB wiederhergestellt | Mitternacht |
| `today_kwh_medium_rate` | In-Memory, beim Neustart aus DB wiederhergestellt | Mitternacht |
| `today_kwh_high_rate` | In-Memory, beim Neustart aus DB wiederhergestellt | Mitternacht |

### Monatliche Aggregation

`month_kwh_high_rate`, `month_kwh_medium_rate`, `month_kwh_low_rate` sind
SQLite-SUMs der täglichen Spalten `kwh_high_rate`, `kwh_medium_rate`, `kwh_low_rate`,
abgefragt über `store.query_month()`.

---

## Verbrauchs- & PV-Vorhersage (`ConsumptionModel`)

Einmal pro EMS-Tick in `consumption_model.py` berechnet.
Datenquelle: SQLite-Tageshistorie (`store.py`) + optionale HA-Wettervorhersage.

### Vorhergesagte Last (`predicted_load_kwh`)

```
Wenn weather_entity konfiguriert UND Vorhersage verfügbar:
  target_temp   = maximale Temperatur von morgen (aus HA-Vorhersage)
  similar_days  = Tage der letzten 60 Tage mit |avg_temp − target| ≤ 4 °C
  Wenn len(similar_days) ≥ 3:
    predicted_load = Median(load_total_kwh ähnlicher Tage)  → Quelle: "historical"
  Sonst:
    → temperaturbasierte Fallback-Regeln (siehe unten)       → Quelle: "fallback"
Sonst:
  → temperaturbasierte Fallback-Regeln                       → Quelle: "fallback"
Keine Historie:
  predicted_load = 0.0 kWh                                   → Quelle: "fallback"
```

#### Temperatur-Fallback-Regeln

| Bedingung | Vorhergesagte Last |
| ---|---|
| Nachttemp. < 0 °C und Tagtemp. < 0 °C | 30 kWh |
| Nachttemp. < 0 °C und Tagtemp. < 10 °C | 20 kWh |
| Nachttemp. > 0 °C und Tagtemp. < 15 °C | 10 kWh |
| Andernfalls | 5 kWh |

### Vorhergesagter PV-Ertrag (`predicted_pv_kwh`)

Interne Schätzung, nur verwendet wenn Solcast nicht verfügbar.

```
peaks    = [peak_pv_w | letzte 14 Tage, peak_pv_w > 100 W]
p75      = 75. Perzentile(peaks)   (konservative Schätzung)

Mit Vorhersage:
  clear_frac  = Mittelwert(1 − cloud_coverage / 100) über Vorhersage-Slots
  daylight_h  = astronomische Tageslänge für HA-Breitengrad + aktueller Monat
  pv_factor   = clear_frac × min(1.0, daylight_h / 12.0)

Ohne Vorhersage:
  pv_factor   = 0.5          (neutrale Annahme)
  daylight_h  = Näherung für 51°N + aktueller Monat

predicted_pv = max(0, (p75 / 1000) × pv_factor × daylight_h)
```

**Tageslängen-Formel** (`daylight_hours_approx` in `weather_client.py`):

```
day_of_year = (month − 1) × 30 + 15
decl        = 23,45° × sin(360° × (284 + day_of_year) / 365)
cos_ha      = −tan(lat) × tan(decl)   [auf −1 … 1 begrenzt]
daylight_h  = 2 × arccos(cos_ha) / 15
```

### Wetter-Daten-Cache

`WeatherClient` cacht das Ergebnis von `weather.get_forecasts` für **30 Minuten**.
Der HA-Breitengrad wird einmalig von `http://supervisor/core/api/config` gelesen und gecacht.
