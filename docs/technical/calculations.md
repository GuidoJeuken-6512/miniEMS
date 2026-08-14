---
revision_date: 2026-08-14
---

# Berechnungen

## EMS-Modus-Entscheidung (`EMSController._decide` / `_commit`)

Der Controller bewertet fünf sich gegenseitig ausschließende Modi, in dieser Prioritätsreihenfolge, bei jedem Tick neu:

```
1. IDLE               — battery_soc_entity fehlt oder veraltet (sensor_max_age_sec)
2. PROTECT_BATTERY     — soc < battery_min_soc
                         ODER (zuvor PROTECT_BATTERY UND soc < battery_min_soc + battery_soc_hysteresis_pct)
3. IDLE                — soc ≥ battery_max_soc ("battery full")
4. EXPORT_SURPLUS       — (pv_w − load_w) > pv_surplus_threshold_w  UND  Export-Halt aktiv (siehe unten)
   PV_CHARGING          — (pv_w − load_w) > pv_surplus_threshold_w  UND  Export-Halt NICHT aktiv
5. GRID_CHARGING        — Preis günstig UND Netzlade-Bedingungen erfüllt (siehe unten)
6. IDLE                — keiner der obigen Fälle ("no action")
```

Jede Bedingung wird **fail closed** ausgewertet: Fehlt oder veraltet ein benötigter Sensorwert, wählt der Controller den sicheren Weg (kein Laden vom Netz, kein Zurückhalten von PV-Ladung) statt zu raten.

!!! note "SoC-Sensor nicht verfügbar"
    Liefert `battery_soc_entity` keinen Wert oder ist länger als `sensor_max_age_sec` (Standard 300 s) nicht aktualisiert worden, springt der Controller sofort und ohne Verzögerung (`urgent`) in **IDLE** und überlässt die Steuerung der eigenen Selbstverbrauchslogik des Wechselrichters. Das ist die zentrale Sicherheitsmaßnahme gegen unkontrolliertes Laden bei unbekanntem Akkustand.

### PV-Überschuss

```
surplus_w = pv_power_w − load_power_w
```

Nur berechnet, wenn **beide** Leistungssensoren einen aktuellen Wert liefern (nicht älter als `sensor_max_age_sec`); sonst gilt `surplus_w` als unbekannt und der Controller fährt mit Schritt 5 (Netzladen) fort. Ist `surplus_w > pv_surplus_threshold_w` (Standard 200 W), entscheidet der Export-Halt (nächster Abschnitt), ob der Modus **PV Charging** oder **Export Surplus** wird.

### Netzfreundlicher Export-Halt (`_should_hold_pv_charge`, Modus `EXPORT_SURPLUS`)

Optionale Strategie (`pv_export_priority_enabled`, Standard aus): exportiert PV-Überschuss ins Netz, statt die Batterie sofort zu laden, solange die Solcast-Restprognose für den Tag über dem noch benötigten Bedarf liegt. Jeder der folgenden Punkte muss erfüllt sein, damit gehalten (`hold = True`, Modus `EXPORT_SURPLUS`) statt geladen wird — **jeder** fehlgeschlagene Punkt löst sofort `PV_CHARGING` aus:

```
1. pv_export_priority_enabled == true
2. now.hour < pv_charge_backstop_hour              (Standard 14 Uhr — danach immer laden)
3. bat_soc ≥ pv_export_min_soc_pct                 (Standard 30 % — darunter immer laden)
4. bat_kwh_free > 0.05 kWh                          (praktisch noch Platz vorhanden)
5. solcast_remaining_today_kwh verfügbar UND nicht veraltet (forecast_max_age_sec)

target    = bat_kwh_free × pv_charge_margin_factor          # Standard-Faktor 1,2
hyst      = clamp(pv_charge_hysteresis_frac, 0.0, 0.5)       # Standard 0,10 (±10 %)
threshold = target × (1 − hyst)   wenn aktuell EXPORT_SURPLUS   # leichter halten
          = target × (1 + hyst)   sonst                        # schwerer eintreten

hold = solcast_remaining_today_kwh > threshold
```

Die Schwelle ist bewusst asymmetrisch (Hysterese): Um **in** den Export-Halt zu wechseln, muss die Restprognose deutlich über dem Bedarf liegen; um ihn **zu verlassen**, genügt ein kleinerer Rückgang. So flattert der Modus nicht um den Umschaltpunkt.

Ist der Export-Halt aktiv, setzt `InverterController.apply_mode()` den Ladestrom auf `export_hold_charge_current_a` (Standard 0 A = Laden komplett blockiert) und lässt den Entladestrom auf Maximum, damit eine vorbeiziehende Wolke weiterhin aus der Batterie statt aus dem Netz gedeckt wird.

### Netzlade-Entscheidung (`_should_grid_charge`, Modus `GRID_CHARGING`)

```
1. electricity_price_entity verfügbar UND nicht veraltet (price_max_age_sec)
2. price_eur_kwh < cheap_rate_threshold_eur
3. bat_kwh_free > grid_charge_min_free_kwh          (Standard 1,0 kWh — sonst lohnt sich Laden nicht)

Wenn solcast_remaining_today_kwh verfügbar (nicht veraltet):
    should_grid_charge = bat_kwh_free > solcast_remaining_today_kwh × pv_charge_margin_factor
                                          + grid_charge_min_free_kwh

Sonst (keine Prognose verfügbar):
    # "Es kann heute keine Sonne mehr kommen" — nur im konfigurierten Dunkelfenster laden
    should_grid_charge = grid_charge_dark_start_hour ≤ now.hour < grid_charge_dark_end_hour
                          (Standard 21–6 Uhr; über Mitternacht hinweg zulässig)
```

!!! warning "Nicht mehr an das interne Verbrauchsmodell gekoppelt"
    Frühere miniEMS-Versionen nutzten bei fehlender Solcast-Prognose die interne Verbrauchsvorhersage (`ConsumptionModel`, Abschnitt unten) als Fallback für die Netzlade-Entscheidung. Das ist seit der netzfreundlichen PV-Strategie (Phase 7) nicht mehr der Fall: Ohne Solcast-Prognose wird **ausschließlich** anhand des Dunkelfensters entschieden. `ConsumptionModel.predicted_pv_kwh` und `.predicted_load_kwh` fließen aktuell in **keine** Steuerungsentscheidung mehr ein — sie sind reine Dashboard-Anzeigewerte (siehe Abschnitt „Verbrauchs- & PV-Vorhersage" weiter unten auf dieser Seite).

### Batterieschutz-Hysterese

```
Eintritt:  soc < battery_min_soc                                     → PROTECT_BATTERY (sofort)
Verlassen: erst wenn soc ≥ battery_min_soc + battery_soc_hysteresis_pct   (Standard 2 %)
```

Ohne dieses Totband würde der Modus bei einem SoC, der exakt um `battery_min_soc` schwankt, bei jedem Tick zwischen `PROTECT_BATTERY` und einem anderen Modus hin- und herspringen.

### Modus-Entprellung (`_commit`, `mode_dwell_sec`)

Ein neu vorgeschlagener Modus wird nicht sofort übernommen, sondern muss erst eine Weile stabil angefordert werden — außer die Entscheidung ist als **urgent** markiert (SoC-Schutz, fehlender/veralteter SoC-Sensor, oder ein durch eine Sicherheits-Guard beendeter Export-Halt):

```
Wenn decision.mode == aktueller Modus:
    kein Wechsel, pending zurückgesetzt

Wenn decision.urgent ODER mode_dwell_sec ≤ 0:
    sofort übernehmen

Sonst:
    Wenn decision.mode ≠ zuvor vorgeschlagener Modus:
        neuer Vorschlag, Timer bei now starten
    waited = now − pending_since
    Wenn waited < mode_dwell_sec:
        aktuellen Modus beibehalten (wartet weiter)
    Sonst:
        Modus übernehmen
```

Standard `mode_dwell_sec` = 300 s. Das verhindert schnelles Umschalten bei kurzen Preis- oder PV-Schwankungen, ohne bei echten Notfällen (leerer Akku, Sensorausfall) zu verzögern.

---

## Akkuzustand (`BatteryModel`)

```
free_to_charge_kwh = max(0,  (max_soc − soc) / 100  × capacity_kwh)
useable_kwh        = max(0,  (soc − min_soc) / 100  × capacity_kwh)
```

`capacity_kwh` kommt standardmäßig aus `battery_capacity_kwh` (fester Config-Wert), wird aber bei jedem Tick durch den Live-Sensor `battery_capacity_entity` ersetzt, falls dieser konfiguriert ist **und** ein plausibler Wert liefert:

```
plausibel := 0.5 × battery_capacity_kwh ≤ Sensorwert ≤ 2.0 × battery_capacity_kwh
```

Diese Plausibilitätsbande verhindert, dass ein falsch skalierter Sensor (z. B. eine Ah- statt kWh-Angabe) die Lade-/Entscheidungslogik mit einem absurden Kapazitätswert versorgt — außerhalb der Bande wird stattdessen der feste Config-Wert verwendet.

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

Wenn kein vorheriger Wert für einen Sensor vorhanden ist, wird die erste Messung immer akzeptiert.

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
gedeckt werden kann — er muss daher aus dem Netz stammen. Diese leistungsbasierte
Schätzung ist die Grundlage für `today_grid_charge_cost_eur`; sie ist unabhängig
von der bilanzbasierten Scenario-2-Berechnung weiter unten.

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

### Bilanzbasierte Netzladung, Wirkungsgrad & ROI (Scenario 2, optional)

Nur aktiv, wenn die entsprechenden erweiterten Sensoren konfiguriert sind — siehe Abschnitt „Erweiterte Sensoren für bilanzbasierte Kostenberechnung" in der [Konfiguration](../user/configuration.md). Ergänzt — ersetzt nicht — die leistungsbasierte Berechnung oben; alle Werte sind `None`/abwesend, solange die nötigen Eingaben fehlen.

#### Bilanzierte Netzlademenge (`today_grid_charge_kwh_bilanz`)

Berechnet aus den **Tages-Gesamtsensoren** des Wechselrichters statt aus Momentanleistungen — dadurch robuster gegenüber kurzen Messlücken:

```
Energie_Netzladen = today_energy_import − today_load_consumption + today_battery_discharge
today_grid_charge_kwh_bilanz = max(0, Energie_Netzladen)
```

Nur berechnet, wenn `battery_discharge_entity` **und** `grid_import_energy_entity` verfügbar sind (`today_load_consumption` kommt aus dem intern mitgeführten `load_total_kwh`-Akkumulator).

`today_grid_charge_cost_bilanz_eur` wendet den durchschnittlichen Preis pro kWh des leistungsbasierten Akkumulators auf diese Menge an:

```
today_grid_charge_cost_bilanz_eur = today_grid_charge_kwh_bilanz
                                     × (today_grid_charge_cost_eur / today_grid_charge_kwh)
                                     (0, wenn today_grid_charge_kwh == 0)
```

#### Wechselrichter-Wirkungsgrad (`today_efficiency_pct`)

```
η = (today_production_entity − today_losses_entity) / today_production_entity
    (None, wenn today_production_entity ≤ 0 oder eine der beiden Entities fehlt)
```

#### ROI der Netzladung (`today_grid_charge_roi_eur`)

```
Gewinn_Netzladen = (Energie_Netzladen × η × Ø_Tarif_Entladung) − Kosten_Netzladen
```

```
usable_kwh = today_grid_charge_kwh_bilanz × η
saving_eur = usable_kwh × avg_discharge_tariff_eur_kwh
roi_eur    = saving_eur − today_grid_charge_cost_eur
```

Nur berechnet, wenn `today_grid_charge_kwh_bilanz > 0`, `η > 0` **und** `avg_discharge_tariff_eur_kwh > 0` konfiguriert ist (Standard `0.0` = deaktiviert). `avg_discharge_tariff_eur_kwh` ist der angenommene Bezugspreis, den die entladene Energie sonst gekostet hätte — vergleicht also die Kosten der Netzladung mit dem Wert der damit später verdrängten (sonst teureren) Netzentnahme.

#### Auswirkung auf `today_cost_without_grid_charge`

Ist die bilanzbasierte Kostenschätzung verfügbar, wird sie anstelle der leistungsbasierten für diese Metrik bevorzugt:

```
gc_cost_für_subtraktion = today_grid_charge_cost_bilanz_eur, falls vorhanden,
                          sonst today_grid_charge_cost_eur
today_cost_without_grid_charge = max(0, today_grid_cost_eur − gc_cost_für_subtraktion)
```

---

### Abgeleitete Metriken (berechnet in `ems_controller.py`)

Diese werden einmal pro Tick aus den akkumulierten Werten oben berechnet und
dem Status-Dict hinzugefügt.

| Entity | Formel | Bedeutung |
| ---|---|---|
| `today_cost_without_grid_charge` | `max(0, grid_cost_eur − gc_cost_für_subtraktion)` | Was die Netzrechnung ohne Akku-Netzladung gewesen wäre (siehe Scenario-2-Vorrang oben) |
| `today_cost_fix_price_tariff` | `load_total_kwh × fix_price + daily_base_price_eur` | Was die heutige Last beim festen Referenztarif zzgl. Grundpreis kosten würde (Standard: 0,30 €/kWh, `daily_base_price_eur` Standard 0) |

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

!!! info "Nur Dashboard-Anzeige, keine Steuerungswirkung"
    Beide Vorhersagewerte (`predicted_load_kwh`, `predicted_pv_kwh`) werden unabhängig
    davon berechnet, ob Solcast konfiguriert ist, und ausschließlich für die
    Dashboard-Anzeige verwendet. Die eigentliche Netzlade- und Export-Entscheidung
    verwendet ausschließlich die Solcast-Restprognose (siehe Abschnitt
    „Netzlade-Entscheidung" weiter oben auf dieser Seite) — dieses Modell
    fließt dort **nicht** mehr ein.

### Vorhergesagte Last (`predicted_load_kwh`)

```
Wenn weather_entity konfiguriert UND Vorhersage verfügbar:
  target_temp   = morgiger Vorhersagewert, den das Modell als "Nachttemperatur" führt
                  (tatsächlich: morgige Tageshöchsttemperatur aus der HA-Vorhersage — die
                  Benennung ist historisch, siehe Quellcode-Kommentar in consumption_model.py)
  similar_days  = Tage der letzten 60 Tage mit |avg_temp − target| ≤ 4 °C
  Wenn len(similar_days) ≥ 3:
    predicted_load = Median(load_total_kwh ähnlicher Tage)  → Quelle: "historical"
  Sonst:
    → temperaturbasierte Fallback-Regeln (siehe unten)       → Quelle: "fallback"
Sonst:
  → temperaturbasierte Fallback-Regeln                       → Quelle: "fallback"
```

#### Temperatur-Fallback-Regeln

| Bedingung | Vorhergesagte Last |
| ---|---|
| Nachttemp. < 0 °C und Tagtemp. < 0 °C | 30 kWh |
| Nachttemp. < 0 °C und Tagtemp. < 10 °C | 20 kWh |
| Nachttemp. > 0 °C und Tagtemp. < 15 °C | 10 kWh |
| Andernfalls (z. B. milde/warme Tage außerhalb der drei Regeln) | Median vorhandener historischer Tage, sonst `0,0 kWh` |

### Vorhergesagter PV-Ertrag (`predicted_pv_kwh`)

```
peaks    = [peak_pv_w | letzte 14 Tage, peak_pv_w > 100 W]
p75      = 75. Perzentil(peaks), Rangverfahren: sortierte Liste, Index ⌊n × 0,75⌋

Mit Vorhersage:
  clear_frac  = Mittelwert(1 − cloud_coverage / 100) über alle Vorhersage-Slots
  daylight_h  = astronomische Tageslänge für HA-Breitengrad + aktueller Monat
  pv_factor   = clear_frac × min(1.0, daylight_h / 12.0)

Ohne Vorhersage:
  pv_factor   = 0.5          (neutrale Annahme)
  daylight_h  = Näherung für 51°N + aktueller Monat

predicted_pv = max(0, (p75 / 1000) × pv_factor × daylight_h)
```

Liefert `0,0 kWh`, wenn in den letzten 14 Tagen kein Peak-PV-Wert über 100 W aufgezeichnet wurde (z. B. direkt nach der Installation).

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
