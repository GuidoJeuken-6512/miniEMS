# Szenario 2: Kostenberechnung mit Netz-zu-Speicher-Ladestrategie

## Grundidee

Wenn **PV-Forecast morgen** und **aktuelle Batterieladung** zusammen den **erwarteten Tagesverbrauch**
nicht decken, wird der Speicher aktiv über den Niedrigtarif aus dem Netz geladen.

**Ziel:** Vergleich der tatsächlichen Kosten (inkl. Netzladen) mit dem hypothetischen Szenario
ohne Netzladen – Nachweis ob sich die Strategie finanziell lohnt.

---

## Sensoren & Entitäten

| Sensor | Entity | Einheit | Aktueller Wert |
|--------|--------|---------|----------------|
| **Gesamtverbrauch heute** | `sensor.deye8k_today_load_consumption` | kWh | 12,2 |
| **PV-Produktion heute** | `sensor.deye8k_today_production` | kWh | 32,5 |
| Batterie geladen heute | `sensor.deye8k_today_battery_charge` | kWh | 6,7 |
| Batterie entladen heute | `sensor.deye8k_today_battery_discharge` | kWh | 4,9 |
| **Verluste heute** | `sensor.deye8k_today_losses` | kWh | 2,6 |
| **Leistungsverluste aktuell** | `sensor.deye8k_power_losses` | W | 87 |
| Netz-Import heute | `sensor.deye8k_today_energy_import` | kWh | 0,2 |
| Einspeisung heute | `sensor.deye8k_today_energy_export` | kWh | 16,2 |
| Netzleistung | `sensor.deye8k_grid_power` | W | 10 |
| Aktuelle Last | `sensor.deye8k_load_power` | W | 1005 |
| Batterieladezustand | `sensor.deye8k_battery` | % | 100 |
| **Batteriekapazität** | `sensor.deye8k_battery_capacity` | kWh | 25,8 |
| Batterieleistung | `sensor.deye8k_battery_power` | W | 170 |
| **Batterie-Zustand** | `sensor.deye8k_battery_state` | enum | discharging |
| PV-Forecast morgen | `sensor.solcast_pv_forecast_prognose_morgen` | kWh | – |
| PV-Forecast heute verbleibend | `sensor.solcast_pv_forecast_prognose_verbleibende_leistung_heute` | kWh | – |
| Grid-Charging Switch | `switch.deye8k_battery_grid_charging` | on/off | – |
| Aktueller Strompreis | `sensor.octopus_a_10fc0646_electricity_price` | €/kWh | – |
| Einspeisevergütung | config: `feed_in_tariff_eur_kwh` | €/kWh | 0,08 |

### Optimierungen durch neue Sensoren

| Sensor | Optimierung |
|--------|-------------|
| `sensor.deye8k_battery_capacity` | **Batterie-kWh direkt verfügbar** (25,8 kWh) – kein config-Wert mehr nötig |
| `sensor.deye8k_today_losses` | **Exakte Wirkungsgrad-Berechnung** statt Defaultwert 0,95 |
| `sensor.deye8k_power_losses` | **Echtzeit-Effizienz** für laufende Kostenberechnung nutzbar |
| `sensor.deye8k_battery_state` | **Laden/Entladen-Status direkt** – kein Vorzeichen-Parsing von battery_power nötig |
| `sensor.deye8k_today_production` | **Vollständige Energiebilanz-Plausibilitätsprüfung** möglich |

---

## Stromtarif (dynamisch)

| Stufe | Preis (€/kWh) | Zeitfenster |
|-------|--------------|-------------|
| **Niedrig** | 0,274414 € | 02:00–06:00 und 12:00–16:00 Uhr |
| **Standard** | 0,344386 € | 06:00–12:00, 16:00–18:00 und 21:00–02:00 Uhr |
| **Hoch** | 0,394366 € | 18:00–21:00 Uhr |
| Grundpreis | 0,338494 €/Tag | (28,4449 ct × 1,19 MwSt.) |

---

## Wirkungsgrad – exakt statt Schätzwert

Statt des Defaultwerts 0,95 liefert der Wechselrichter den echten Wirkungsgrad täglich:

```
η_heute = (today_production - today_losses) / today_production
        = (32,5 - 2,6) / 32,5  =  0,920  (92,0 %)

# Echtzeit-Wirkungsgrad (für laufende Berechnung):
η_aktuell = (pv_power - power_losses) / pv_power
          = (808 - 87) / 808  =  0,892  (89,2 %)
```

> Für die ROI-Berechnung des Netzladens wird `η_heute` verwendet –
> er ist der präziseste Tageswert und wird automatisch vom Wechselrichter berechnet.

---

## Ladeentscheidungslogik

```
# battery_capacity kommt jetzt direkt vom Sensor (kein config-Wert mehr nötig):
battery_kwh_verfügbar  = (battery_soc [%] / 100) × battery_capacity [kWh]
                       = (100 / 100) × 25,8  =  25,8 kWh

pv_erwartung_gesamt    = solcast_remaining_today + solcast_tomorrow
deckung                = battery_kwh_verfügbar + pv_erwartung_gesamt
erwarteter_verbrauch   = historischer Ø-Tagesverbrauch (intern aus today_load_consumption)

→ Netzladen aktiv, wenn:
    deckung < erwarteter_verbrauch
    UND Preis == Niedrigtarif (0,274414 €/kWh)
    UND grid_charge_switch == ON
```

---

## Energiebilanz-Plausibilitätsprüfung

```
today_production + today_energy_import
= today_load_consumption + today_energy_export + today_battery_charge - today_battery_discharge + today_losses

Beispiel:
  32,5 + 0,2  =  12,2 + 16,2 + 6,7 - 4,9 + 2,6
  32,7        ≈  32,8  ✅
```

---

## Kernformel: Netzladeenergie per Energiebilanz

```
Energie_Netzladen [kWh] = today_energy_import
                         - today_load_consumption
                         + today_battery_discharge
```

> **Plausibilitätsprüfung:**
> `Energie_Netzladen ≥ 0`
> `Energie_Netzladen ≤ today_battery_charge`
> Bei `grid_charge_switch == OFF` den ganzen Tag → `Energie_Netzladen ≈ 0`

### Herleitung

```
today_energy_import  =  Haushalt_aus_Netz  +  Energie_Netzladen
Haushalt_aus_Netz    =  today_load_consumption  -  today_battery_discharge  -  PV_Direktverbrauch

Da PV_Direktverbrauch sich in der Bilanz herauskürzt:
Energie_Netzladen    =  today_energy_import - today_load_consumption + today_battery_discharge
```

---

## Berechnung 1: Fixpreis-Szenario (31 ct/kWh, ohne PV)

```
Kosten_Fixpreis [€] = today_load_consumption × 0,31 + 0,338494
```

---

## Berechnung 2: Dynamischer Tarif (ohne PV, ohne Netzladen)

### Akkumulation (10-Sekunden-Intervalle)

```
Energie_Intervall [kWh] = load_power [W] / 1000 × (10 / 3600)

if   02:00–06:00 or 12:00–16:00  → Energie_Niedrig  += Energie_Intervall
elif 18:00–21:00                 → Energie_Hoch     += Energie_Intervall
else                             → Energie_Standard += Energie_Intervall
```

```
Kosten_DynOhnePV [€] = (Energie_Niedrig  × 0,274414)
                      + (Energie_Standard × 0,344386)
                      + (Energie_Hoch     × 0,394366)
                      + 0,338494
```

---

## Berechnung 3a: Tatsächliche Kosten (mit PV & Netzladen)

### Akkumulation Netz-Import gesamt (10-Sekunden-Intervalle)

```
Netz_Intervall [kWh] = max(grid_power [W], 0) / 1000 × (10 / 3600)

if   02:00–06:00 or 12:00–16:00  → Import_Niedrig  += Netz_Intervall
elif 18:00–21:00                 → Import_Hoch     += Netz_Intervall
else                             → Import_Standard += Netz_Intervall
```

```
Einnahmen_Einspeisung [€] = today_energy_export × 0,08

Kosten_DynMitPV_MitNetzladen [€] = (Import_Niedrig  × 0,274414)
                                  + (Import_Standard × 0,344386)
                                  + (Import_Hoch     × 0,394366)
                                  + 0,338494
                                  - Einnahmen_Einspeisung
```

---

## Berechnung 3b: Hypothetische Kosten OHNE Netzladen (aber mit PV)

### Schritt 1: Netzladekosten isolieren

```
Energie_Netzladen [kWh] = today_energy_import - today_load_consumption + today_battery_discharge

Kosten_Netzladen [€]    = Energie_Netzladen × 0,274414
```

### Schritt 2: Kosten ohne Netzladen

```
Kosten_DynMitPV_OhneNetzladen [€] = Kosten_DynMitPV_MitNetzladen - Kosten_Netzladen
```

---

## Berechnung 4: ROI des Netzladens

### Exakter Wirkungsgrad statt Schätzwert

```
η = (today_production - today_losses) / today_production   ← direkt vom Wechselrichter

nutzbare_Energie [kWh] = Energie_Netzladen × η
```

### Einsparung durch Speicherentladung

```
Einsparung_Entladung [€] = nutzbare_Energie × Ø_verdrängter_Tarif
```

| Entladung überwiegend... | Ø verdrängter Tarif |
|--------------------------|---------------------|
| Abends 18–21 Uhr (Hoch) | 0,394366 €/kWh |
| Abends + Nacht gemischt | 0,369376 €/kWh |
| Tagsüber Standard | 0,344386 €/kWh |

### ROI-Formel

```
Gewinn_Netzladen [€] = Einsparung_Entladung - Kosten_Netzladen

Gewinn_Netzladen > 0  →  Netzladen lohnt sich      ✅
Gewinn_Netzladen ≤ 0  →  Netzladen lohnt sich nicht ❌
```

### Break-even Wirkungsgrad

```
η_min (Niedrig→Hoch)    = 0,274414 / 0,394366 = 69,6 %
η_min (Niedrig→Standard)= 0,274414 / 0,344386 = 79,7 %

Dein Wechselrichter heute: η = 92,0 %  →  weit über beiden Schwellen ✅
```

### Konkretes Beispiel

```
today_energy_import       =  8,0 kWh
today_load_consumption    =  5,5 kWh
today_battery_discharge   =  3,0 kWh
today_production          = 20,0 kWh
today_losses              =  1,6 kWh
η                         = (20,0 - 1,6) / 20,0  =  0,92

Energie_Netzladen         =  8,0 - 5,5 + 3,0     =  5,5 kWh
Kosten_Netzladen          =  5,5 × 0,274414         =  1,48 €
nutzbare_Energie          =  5,5 × 0,92           =  5,06 kWh
Einsparung (Hochtarif)    =  5,06 × 0,394366        =  1,97 €

Gewinn_Netzladen          =  1,97 - 1,48          = +0,49 €  ✅
```

---

## Zusammenfassung aller Kennzahlen

```
η                              = (today_production - today_losses) / today_production

Energie_Netzladen              = today_energy_import
                                - today_load_consumption
                                + today_battery_discharge

battery_kwh_verfügbar          = (battery_soc / 100) × battery_capacity   ← Sensor, kein Config!

Kosten_Fixpreis                = today_load_consumption × 0,31 + 0,338494
Kosten_DynOhnePV               = Σ(Energie_je_Zone × Preis_je_Zone) + 0,338494
Kosten_DynMitPV_MitNetzladen   = Σ(Import_je_Zone × Preis_je_Zone) + 0,338494 - Einspeisung
Kosten_Netzladen               = Energie_Netzladen × 0,274414
Kosten_DynMitPV_OhneNetzladen  = Kosten_DynMitPV_MitNetzladen - Kosten_Netzladen
Gewinn_Netzladen               = (Energie_Netzladen × η × Ø_Tarif_Entladung) - Kosten_Netzladen
```

| Kennzahl | Bedeutung |
|----------|-----------|
| `Kosten_DynMitPV_MitNetzladen` | Was ich heute tatsächlich bezahlt habe |
| `Kosten_Netzladen` | Davon: reiner Anteil fürs Akku-Netzladen |
| `Kosten_DynMitPV_OhneNetzladen` | Was es ohne Netzladen gekostet hätte |
| `Gewinn_Netzladen` | Nettogewinn der Netzladestrategie heute |

---

## Aktualisierte config.txt Ergänzungen

```json
{
  "battery_charge_entity":      "sensor.deye8k_today_battery_charge",
  "battery_discharge_entity":   "sensor.deye8k_today_battery_discharge",
  "battery_capacity_entity":    "sensor.deye8k_battery_capacity",
  "battery_state_entity":       "sensor.deye8k_battery_state",
  "today_production_entity":    "sensor.deye8k_today_production",
  "today_losses_entity":        "sensor.deye8k_today_losses",
  "power_losses_entity":        "sensor.deye8k_power_losses"
}
```

> `battery_capacity_kwh` als fester config-Wert wird nicht mehr benötigt –
> `sensor.deye8k_battery_capacity` liefert den Wert direkt (aktuell: **25,8 kWh**).
> `battery_charge_efficiency` als fester config-Wert entfällt ebenfalls –
> wird durch `today_losses / today_production` exakt berechnet.
