---
revision_date: 2026-11-19
---

# Preise und Entscheidungen der Verbraucher

Diese Seite zeigt, wie miniEMS aus dem Preis-Sensor die Preise bildet, mit denen Akku,
Warmwasser, Heizung und E-Auto entscheiden. Sie ist die Übersicht. Die Einzelheiten stehen
auf den verlinkten Seiten. Stand: v4.2.3.

## 1. Vom Preis-Sensor zur Preiskurve

Grundlage ist der Preis-Sensor (`electricity_price_entity`). Daraus entstehen zwei Dinge:
der **Preis jetzt** und die **Preiskurve** für die kommenden Stunden.

```mermaid
flowchart TD
    S[("Preis-Sensor<br/>electricity_price_entity")] --> U["Einheit umrechnen<br/>price_unit (ct → €)"]
    U --> SP{"price_is_spot?"}
    SP -- ja --> FEE["(Börse + price_fee_eur_kwh)<br/>× (1 + price_vat_pct)"]
    SP -- nein --> AS["unverändert"]
    FEE --> NOW["Preis jetzt<br/>(effektiv, €/kWh)"]
    AS --> NOW

    S --> SRC{"tariff_source"}
    SRC -- "rates / auto" --> R["datierte Marktpreise<br/>rates[], unit_rate_forecast[] …"]
    SRC -- "timeslots / auto ohne rates" --> CAL["Zeitfenster-Tarif<br/>agreement_prices[].activation_rules"]
    R --> PC["PriceCurve<br/>(price_curve.py)"]
    CAL --> PC
    FEE -.->|dieselbe Umrechnung| PC

    PC --> SLOT["Preis je 30-min-Slot<br/>Slot 0 = Preis jetzt,<br/>sonst mean_rate (day_ahead) bzw. rate_at"]
    PC --> CLS["Preisklassen A1<br/>low / medium / high je Kalendertag<br/>(price_classes.py)"]
    PC --> WIN["Fenster<br/>window_end, run_end, windows_before"]
```

- **Fehlt die Preiskurve** (kein Tarifkalender, keine Marktpreise), gibt es keine Planung. Dann
  entscheiden nur noch der Preis jetzt und die festen Schwellen (`cheap_rate_threshold_eur`,
  `medium_rate_threshold_eur`).
- **Preisklassen (A1):** `low` = die `price_cheap_hours` günstigsten Stunden des Tages,
  `high` = deutlich über dem Median. Ist die Spanne des Tages kleiner als
  `price_flat_spread_eur`, ist nichts `low`, und es wird nicht optimiert. Einzelheiten in
  `price_classes.py`.

## 2. Abgeleitete Preise

Aus Preiskurve, Akkustand und Prognosen leitet miniEMS weitere Preise ab. Sie geben an, was
eine kWh **in einem bestimmten Slot aus einer bestimmten Quelle** wirklich kostet.

```mermaid
flowchart LR
    SLOT["Preis je Slot<br/>(Netztarif)"] --> NACH
    ETA["Wirkungsgrad η<br/>gelernt (A7a, wenn an)<br/>sonst Tagesmessung 65–98 %<br/>sonst 0,9"] --> NACH["Nachladepreis Akku<br/>= günstigster Tarif danach ÷ η<br/>(Einspeisevergütung, wenn PV<br/>danach ohnehin überläuft)"]
    FEED["Einspeisevergütung<br/>feed_in_tariff_eur_kwh<br/>0, wenn negativer Preis oder<br/>feed_in_price_entity ≤ 0 (A5)"] --> EFF
    NACH --> EFF["effektiver Preis je Slot<br/>PV-Überschuss → Vergütung bzw. Nachladepreis<br/>Akku entlädt → Nachladepreis<br/>sonst → Netztarif"]
    SLOT --> EFF
    EFF --> TH["Wärmepreis<br/>= effektiver Preis ÷ COP(T_außen)"]
    HIST[("Entlade-Historie<br/>7 Tage")] --> DIS["Entladetarif<br/>Σ(entladene kWh × Preis) ÷ Σ kWh<br/>(Wirtschaftlichkeit, A3)"]
```

| Abgeleiteter Preis | Wer nutzt ihn | Code |
|---|---|---|
| Wirkungsgrad η | Tagesplan, Überbrückungsplan, Warmwasser, Wirtschaftlichkeitsprüfung | `EMSController._round_trip_eta()`, `_measured_eta()` |
| Nachladepreis Akku | Warmwasser (`battery_projection`), Energiewert (A3) | `battery_projection.py`, `energy_value.py` |
| Effektiver Preis je Slot | Warmwasser, E-Auto | `hot_water_planner._slot_price`, `energy_task.effective_price` |
| Wärmepreis (÷ COP) | Warmwasser, Heizung | `hot_water_planner.py`, `heating_controller.py` |
| Entladetarif (Historie) | Wirtschaftlichkeitsprüfung Netzladen | `CostOptimizer.avg_discharge_tariff_eur_kwh()` |
| Einspeisevergütung | Tagesplan (Export), Warmwasser, E-Auto, Export-Halt | `_export_unpaid()` |

## 3. Reihenfolge in einem Takt

Alle 30 Sekunden rechnet `EMSController.update()` in dieser Reihenfolge. Spätere Schritte
nutzen die Ergebnisse der früheren.

```mermaid
flowchart TD
    T([Takt, alle 30 s]) --> P["Preis jetzt lesen,<br/>Entladetarif, gelernte Ladeleistung"]
    P --> A1["Preisklassen A1<br/>→ Kostenrechnung und Warmwasser"]
    A1 --> MAINT["Wartung A11<br/>(Volladung fällig?)"]
    MAINT --> LEARN["Lernen A7<br/>(stündlich: η, Kapazität, Prognosekorrektur)"]
    LEARN --> NEG["negativer Preis A5<br/>(Exportgrenze, Wärme speichern)"]
    NEG --> HW["Warmwasser-Plan<br/>(Wärmepreis je Slot)"]
    HW --> BR["Überbrückungsplan<br/>(Fallback ohne Tagesplan)"]
    BR --> EV["Energiewert A3/A4<br/>(lohnt Netzladen? halten?)"]
    EV --> DP["Akku-Tagesplan A6<br/>(DP über alle Slots,<br/>Warmwasser und E-Auto als Last)"]
    DP --> OV["Übersteuerung C2,<br/>Sicherung/E-Auto B1–B5"]
    OV --> MODE["Akku-Modus wählen<br/>(Abschnitt 4)"]
    MODE --> POST["E-Auto und Heizung schreiben,<br/>Energiefahrplan, Wechselrichter schreiben"]
```

## 4. Akku: Wahl des Modus

`_determine_mode()` → `_decide()` → Übersteuerung → Verweildauer. Die erste zutreffende Regel
gewinnt.

```mermaid
flowchart TD
    A{"SoC verfügbar<br/>und frisch?"} -- nein --> I0["IDLE (Wechselrichter-Eigenlogik)"]
    A -- ja --> B{"SoC < min_soc<br/>(oder Hysterese)?"}
    B -- ja --> PROT["PROTECT_BATTERY"]
    B -- nein --> C{"SoC ≥ max_soc?"}
    C -- ja --> I1["IDLE – Akku voll"]
    C -- nein --> M{"Wartungs-Volladung fällig<br/>und Preis günstig? (A11)"}
    M -- ja --> G1["GRID_CHARGING"]
    M -- nein --> N{"Preis ≤ 0? (A5)"}
    N -- ja --> G2["GRID_CHARGING (Platz für PV lassen)"]
    N -- nein --> DP{"Tagesplan aktiv?<br/>(battery_plan_enabled)"}
    DP -- ja --> DPS["Slot 0 des Plans:<br/>CHARGE → GRID_CHARGING<br/>HOLD → HOLD_FOR_PEAK / PV_CHARGING<br/>EXPORT → EXPORT_SURPLUS<br/>AUTO → IDLE / PV_CHARGING"]
    DP -- nein --> BRG{"Überbrückungsplan<br/>braucht Netzladung?"}
    BRG -- ja --> G3["GRID_CHARGING"]
    BRG -- nein --> PV{"PV-Überschuss?"}
    PV -- ja --> H{"Export-Halt?"}
    H -- ja --> EXP["EXPORT_SURPLUS"]
    H -- nein --> PVC["PV_CHARGING"]
    PV -- nein --> GC{"günstig und lohnend?<br/>(_should_grid_charge)"}
    GC -- ja --> G4["GRID_CHARGING"]
    GC -- nein --> HO{"Akku-Halt A4?"}
    HO -- ja --> HOLD["HOLD_FOR_PEAK"]
    HO -- nein --> I2["IDLE"]

    DPS --> OVR
    G1 & G2 & G3 & G4 & EXP & PVC & HOLD & I2 & I1 --> OVR{"Übersteuerung C2?<br/>(nie über harte Grenzen)"}
    OVR --> COM["Verweildauer mode_dwell_sec<br/>(außer urgent)"]
```

- **Der Tagesplan** (`battery_plan.py`) bewertet jeden Slot mit: Import × Slotpreis −
  bezahlter Export × Vergütung + Verschleiß × Entladung + Wechselkosten. Die Energie am Ende
  des Horizonts zählt mit dem Restwert (Perzentil der Preise × η). Details:
  [Netzladen-Entscheidung](netzladen-entscheidung.md).
- **Wirtschaftlichkeit ohne Tagesplan:** Netzladen nur, wenn η × Entladetarif − Preis jetzt >
  `grid_charge_min_margin_eur_kwh` (bzw. A3 über den Energiewert).

## 5. Verbraucher im Überblick

| Verbraucher | Entscheidet nach | Planer | Ausgeführt über |
|---|---|---|---|
| Akku | Slotpreise, η, Einspeisevergütung, Restwert | Tagesplan (DP), Fallback Überbrückungsplan | Lade-/Entladestrom, Netzlade-Schalter |
| Warmwasser | Wärmepreis je Slot (effektiver Preis ÷ COP), Verlust, Zapfprofil | gierig über ganze Ladungen | Boiler-Sollwert |
| E-Auto | effektiver Preis je Slot (PV-Anteil zur Vergütung), Abfahrt, Ziel-SoC | `ev_planner` über `energy_task` | Wallbox-Strom, Freigabe |
| Heizung | Preisklasse A1, Vorladen vor `high`, COP-Prüfung | Regeln (B6), Raummodell (B7) | Vorlauf-Offset |

Warmwasser und E-Auto gehen als **geplante Last** in den Tagesplan des Akkus ein
(`_with_planned_loads`). Der Akku sieht also, was die anderen vorhaben. Umgekehrt lesen
Warmwasser und E-Auto die Akku-Herkunft aus einer eigenen, einfachen Vorwärtsrechnung
(`battery_projection`), nicht aus dem Tagesplan.

## Siehe auch

- [Netzladen-Entscheidung](netzladen-entscheidung.md): Überbrückungsplan, Verbrauchsmodell
- [Warmwasser-Steuerung](warmwasser-steuerung.md): Wärmepreis und Fahrplan im Detail
- [Energiefahrplan](energiefahrplan.md): Netzladefenster und gelernte Ladeleistung
- [Berechnungen](calculations.md): Formeln und Schwellwerte
