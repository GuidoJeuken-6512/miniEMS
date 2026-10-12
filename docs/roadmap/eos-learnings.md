---
revision_date: 2026-10-12
---

# Was miniEMS von Akkudoktor-EOS lernen kann

!!! info "Status: Analyse und Planung, nichts umgesetzt"
    Vergleich mit [Akkudoktor-EOS](https://github.com/Akkudoktor-EOS/EOS), Stand `main`
    @ `fab3e17` (2026-10-11). Untersucht wurden Optimierer (`optimization/genetic/`),
    Gerätemodelle (`devices/genetic/`), Prognose-Provider (`prediction/`) und die
    Restwert-Berechnung (`terminalvalue.py`). Ziel ist nicht, EOS nachzubauen, sondern
    einzelne Rechenideen in die reaktive Regelschleife von miniEMS zu übernehmen.

    | Baustein | Stand |
    |---|---|
    | E1 — Stündliches Lastprofil (Werktag/Wochenende) | offen — Voraussetzung für E2, E3, E5 |
    | E2 — Entladesperre für teure Fenster (`HOLD_FOR_PEAK`) | offen — größter Hebel |
    | E3 — SoC-bewusstes Wirtschaftlichkeits-Gate („teuerste ungedeckte Stunde") | offen |
    | E4 — Wirkungsgrad bei Teillast + Akku-Verschleiß (LCOS) | offen |
    | E5 — Energiefahrplan: Bedarf statt freier Platz | offen |
    | E6 — Negative Preise / unvergütete Einspeisung | offen — spätestens mit Day-Ahead |
    | E7 — Preis-Bausteine (Spot + Abgaben + MwSt.) | offen — gehört zu Preisquelle a) |
    | E8 — Startempfehlung für flexible Verbraucher | offen, optional |

## Die beiden Systeme im Vergleich

| | **miniEMS** | **EOS** |
|---|---|---|
| Prinzip | Reaktive Regelschleife alle 30 s: Gates, Hysterese, Dwell-Zeit | Genetischer Algorithmus (300 Individuen × 400 Generationen), simuliert 24–48 h in 60- oder 15-min-Slots |
| Ergebnis | Ein Modus *jetzt* | Fahrplan je Slot: AC-Laden, Entladen erlaubt/gesperrt, Export, EV, Hausgeräte |
| Aktorik | Schreibt selbst, mit Schreibbestätigung und Retry | Steuert nichts — HA/Node-RED müssen den Plan ausführen |
| Geräte | Ein Hybrid-Wechselrichter + Akku | Wechselrichter, Akku, E-Auto, Hausgeräte, Wärmepumpe (je erste Instanz) |
| Lastprognose | Median der Tagessumme, temperaturgematcht | Standardlastprofil je Stunde, mit Messwerten pro Werktag/Wochenende korrigiert |
| PV-Prognose | Solcast über HA | ~8 Provider (pvlib, Forecast.Solar, Solcast, VRM, Import, …) |
| Preis | ToU-Kalender aus `timeslots[]` (`PriceCurve`) | Day-Ahead (energy-charts, SMARD, Tibber) + Gebührenmodell + Einspeisetarif als Zeitreihe |
| Ressourcen | Gering | 140–450 MB RAM; ein Lauf dauert auf einem Raspberry Pi 4 **~10 Minuten** (`docs/akkudoktoreos/smalldevices.md`) |
| Ergebnisgüte | Deterministisch, nachvollziehbar | „cost-optimized … but not necessarily cost-optimal" (EOS-Doku) |

!!! success "Bestätigung der bisherigen Architekturentscheidung"
    Der [Energiefahrplan](energiefahrplan.md) schließt Solver und persistierte
    Fahrpläne bewusst aus. EOS zeigt den Preis dieser Alternative konkret: zehn Minuten
    Rechenzeit auf typischer HA-Hardware, nicht reproduzierbar ohne festen Seed, und ein
    Plan, den ein Drittsystem ausführen muss. Die selbstkorrigierende Regelschleife von
    miniEMS bleibt der Vorteil.

    Auch im Umgang mit fehlenden Daten liegen beide gleich: EOS füllt Prognoselücken
    nicht und extrapoliert nicht (`optimization/genetic/forecast.py`,
    `bounded_forecast_array`), unvollständige Prognosen oder veraltete Akku-Messwerte
    brechen den Lauf ab. miniEMS ist bei der Staleness-Prüfung feiner (Datumsprüfung für
    Tageswerte, Solcast-Cache-Alter).

## E1 — Stündliches Lastprofil

**Bei EOS:** `LoadAkkudoktorAdjusted` (`prediction/loadakkudoktor.py`,
`_calculate_adjustment`) nimmt ein Standardlastprofil und berechnet je Stunde eine
Korrektur aus Messwerten — getrennt für Werktage und Wochenende.

**Bei miniEMS:** `store.py` kennt nur `daily_stats`. `remaining_load_kwh()` rechnet
„Median der Tagessumme minus bisher verbraucht" und weiß damit nicht, *wann* die
Restlast anfällt — abends in HOCH oder vormittags in STANDARD.

**Vorschlag:**

- Neue Tabelle `hourly_load` (`date`, `hour`, `load_kwh`), gefüllt aus dem bereits
  pro Tick akkumulierten Lastzähler beim Stundenwechsel.
- `ConsumptionModel.hourly_profile(weekday: bool) -> list[float]`: Median je Stunde
  über die letzten 28 Tage desselben Typs (Werktag/Wochenende).
- `ConsumptionModel.load_between(start, end) -> float | None`: Summe des Profils über
  ein Zeitintervall, anteilig für angebrochene Stunden.
- Ohne Historie → `None`; alle Aufrufer fallen dann auf ihr heutiges Verhalten zurück.

Kein Selbstzweck: E2, E3 und E5 brauchen alle die Frage „Wie viel Last fällt in
Fenster X an?".

## E2 — Entladesperre für teure Fenster (`HOLD_FOR_PEAK`)

**Bei EOS:** Der Fahrplan legt pro Slot fest, ob der Akku entladen darf
(`bat_discharge_hours`). Die Heuristik-Startlösungen (`_educated_guess_individuals`,
`policy_guess`) entladen nur, wenn der Preis im oberen Quantil liegt und Last > PV ist.

**Bei miniEMS:** Eine Entladesperre gibt es nur unterhalb von `battery_min_soc`
(`PROTECT_BATTERY`). An einem Wintertag entlädt der Akku tagsüber in NIEDRIG- oder
STANDARD-Last und ist leer, wenn um 17 Uhr HOCH beginnt. Jede so verbrauchte kWh
kostet die Spanne (~12 ct) × η.

**Vorschlag:** Neuer Modus `EMSMode.HOLD_FOR_PEAK` — Entladestrom 0, PV-Laden weiter
erlaubt. Der Aktor existiert bereits (`InverterController._set_discharge_current`).

Entscheidung in `_decide()`, nach Batterieschutz und PV-Überschuss, vor Netzladen:

```text
deadline        = nächstes günstiges Ladefenster ODER nächster PV-Peak (das frühere)
rate_now        = curve.window_at(now).rate
teure_fenster   = curve.windows_before(now, deadline) mit rate > rate_now + δ
bedarf_teuer    = Σ load_between(w) − PV-Prognose in w   für w in teure_fenster
nutzbar         = battery_model.useable_kwh(soc)

hold = bedarf_teuer > 0  und  nutzbar ≤ bedarf_teuer × (1 + hyst)
```

Wenn die nutzbare Energie nicht einmal für die teureren Fenster reicht, wird sie für
diese aufgehoben. Reicht sie, wird normal entladen.

**Fail-Regeln (wie überall in miniEMS):** kein Tarifkalender, kein Lastprofil, keine
Deadline oder veralteter SoC → `hold = False`. „Nicht sperren" ist das heutige
Verhalten und kann den Akku nie unnötig voll stehen lassen.

**Schalter:** `discharge_hold_enabled` (Default `false`), wie
`pv_export_priority_enabled`.

**Offen:** Wie stellt der Deye „Entladestrom 0" bei gleichzeitigem PV-Laden sicher, ohne
dass Last aus dem Netz statt aus der PV gedeckt wird? Vor der Umsetzung am
Produktivsystem messen.

## E3 — SoC-bewusstes Wirtschaftlichkeits-Gate

**Bei EOS:** Der Strafterm `ac_charge_break_even` (`genetic.py`,
`_ac_break_even_prices`) rechnet für jeden möglichen AC-Lade-Slot:

1. Künftige Stunden absteigend nach Preis sortieren.
2. Die Energie, die *schon* im Akku ist (PV, „kostenlos"), deckt zuerst die
   teuersten Stunden.
3. Die erste Stunde, die danach ungedeckt bleibt, ist der Preis, gegen den eine
   zusätzlich gekaufte kWh konkurriert.
4. Laden lohnt nur, wenn dieser Preis den Break-even übersteigt:

```text
break_even = preis_laden / η_roundtrip + LCOS / η_dc→ac
η_roundtrip = η_ac→dc × η_laden × η_entladen × η_dc→ac
```

**Bei miniEMS:** `_grid_charge_pays()` vergleicht mit dem 7-Tage-Mittel des
entladungsgewichteten Tarifs (`avg_discharge_tariff_eur_kwh`). Das war der richtige
Schritt weg vom Last- oder Zeitmittel (siehe V4 im Energiefahrplan), ignoriert aber den
aktuellen SoC: Ist der Akku um 02:00 schon zu 60 % gefüllt und deckt damit HOCH
bereits ab, verdrängt jede weitere kWh nur noch STANDARD — die Marge kann negativ
sein, das Gate sagt trotzdem „lohnt".

**Vorschlag:** `_grid_charge_pays()` bekommt eine zweite, genauere Stufe, die greift,
sobald `PriceCurve` und Lastprofil (E1) verfügbar sind:

```text
fenster  = curve.windows_before(now, deadline), absteigend nach rate
frei     = useable_kwh(soc) × η_entladen
for w in fenster:
    last_w = load_between(w) − pv_forecast(w)
    if frei ≥ last_w: frei −= last_w; continue
    best_uncovered_rate = w.rate; break
else:
    best_uncovered_rate = 0          # alles schon gedeckt → nichts zu gewinnen

pays = η × best_uncovered_rate − price > grid_charge_min_margin_eur_kwh
```

Fehlt eine Zutat, gilt weiter die heutige Prüfung mit dem historischen Entladetarif.

## E4 — Wirkungsgrad bei Teillast und Akku-Verschleiß

Die eigene Doku beziffert die Marge als dünn: +4,1 ct bei 91,6 %, −2,2 ct bei 85 %.
Zwei Punkte aus EOS verschieben genau diese Zahlen:

**Teillast-Wirkungsgrad.** EOS kennt `dc_to_ac_efficiency_curve`
(`devices/genetic/inverter.py`), weil Wechselrichter bei kleiner Last deutlich
schlechter arbeiten (EOS-Beispiel: 80 % bei 2 % Last, 93 % bei 7 %, 96 % bei 30 %).
miniEMS nutzt `today_efficiency()` — ein Tagesmittel, dominiert von PV-Stunden mit
hoher Last. Netzgeladene Energie wird aber nachts in 200–400 W Grundlast entladen.

→ Zusätzlich einen **Entlade-Wirkungsgrad** messen: nur Ticks mit
`battery_power_w > 0` und `pv_w ≈ 0`, Verhältnis AC-Last zu DC-Entladeleistung,
7-Tage-Mittel. Diesen Wert im Gate (E3) statt `today_efficiency()` verwenden.

**Verschleißkosten (LCOS).** EOS führt `levelized_cost_of_storage_kwh` je Akku und
rechnet sie in jede Entladung ein. Bei miniEMS übernimmt
`grid_charge_min_margin_eur_kwh = 0.02` diese Rolle stillschweigend. Realistische
Werte für Heimspeicher liegen eher bei 4–8 ct/kWh (Anschaffung ÷ (Zyklen × nutzbare
Kapazität)) — grob geschätzt, anlagenabhängig.

→ Neues Config-Feld `battery_wear_cost_eur_kwh` (Default `0.0`, also heutiges
Verhalten), im Gate explizit: `η × tarif − preis − wear > min_margin`. Die UI erklärt
die Formel und schlägt einen Rechner (Preis, Zyklen, Kapazität) vor.

## E5 — Energiefahrplan: Bedarf statt freier Platz

**Bei EOS:** `terminalvalue.py` begründet ausführlich, warum gespeicherte Energie
**nicht linear** viel wert ist: Die erste kWh ersetzt die teuerste Stunde nach dem
Horizont, die letzte ersetzt nichts mehr. Der Wert ist konkav — eine Konstante pro kWh
führt entweder zum Horten oder zum Leerfahren.

**Bei miniEMS:** `compute_energy_plan()` rechnet

```text
deficit_kwh = max(0, bat_kwh_free − pv_tomorrow × margin)
```

Das misst *freien Platz, den die PV morgen nicht füllt* — nicht, wie viel davon bis
zum PV-Peak tatsächlich gebraucht wird. Fallen bis morgen früh nur 3 kWh Last in
teuren Fenstern an, plant miniEMS trotzdem, den Akku voll zu kaufen.

**Vorschlag:** Defizit auf den Bedarf begrenzen, sobald E1 verfügbar ist:

```text
bedarf_kwh  = Σ (load_between(w) − pv_forecast(w))⁺  über Fenster bis Deadline
              mit rate > günstigster Ladetarif + Break-even-Spanne
deficit_kwh = max(0, min(bat_kwh_free − pv_tomorrow × margin,
                         bedarf_kwh − useable_kwh(soc)))
```

Ohne Lastprofil bleibt die heutige Formel. Wie der Energiefahrplan selbst zunächst
nur Anzeige; die Übernahme in `_should_grid_charge()` ist ein eigener Schritt.

## E6 — Negative Preise und unvergütete Einspeisung

**Bei EOS:** Bei negativem Einspeisetarif wird Einspeisung als Verlust gebucht
(`genetic.py`, `simulate`); Exportaktionen entstehen nur in Slots mit positivem Tarif.

**Bei miniEMS:** `_should_hold_pv_charge()` prüft keinen Preis. Mit Day-Ahead-Tarifen
und bei Neuanlagen ohne Vergütung in Stunden mit negativem Börsenpreis
(Solarspitzengesetz, seit 2025) würde `EXPORT_SURPLUS` genau dann einspeisen, wenn es
nichts bringt oder kostet.

**Vorschlag (zusammen mit Preisquelle a):**

- Export-Halt aufheben, wenn der aktuelle Preis ≤ 0 ist oder ein optionaler
  Einspeisetarif-Sensor ≤ 0 meldet — die PV geht in den Akku.
- Netzladen bei negativem Preis: Wirtschaftlichkeits-Gate überspringen (Laden
  verdient Geld), Hold-Logik für spätere PV aber weiter beachten.
- Neue Grund-Strings im Log: `"negative price"`, `"feed-in not remunerated"`.

## E7 — Preis-Bausteine für Day-Ahead

**Bei EOS:** `prediction/elecfee*.py` trennt den Endpreis in Bestandteile:

```text
endpreis = (spot + abgaben_je_kwh) × (1 + mwst_prozent)
einspeisung = spot × (1 − abzug_prozent) − gebühr_je_kwh
```

Die Abgaben dürfen zeitabhängig sein (Zeitfenster, z. B. §14a-Netzentgelte).

**Bei miniEMS:** Preisquelle a) (Day-Ahead aus `rates[]`/`unit_rate_forecast[]`) ist
geplant, aber noch nicht gebaut. Tibber und aWATTar liefern meist den Endpreis, reine
Börsenquellen (EPEX über Nord Pool/ENTSO-E) nicht.

**Vorschlag:** Beim Bau von Preisquelle a) gleich drei Config-Felder vorsehen —
`price_is_spot` (bool), `price_fee_eur_kwh`, `price_vat_pct` — und in
`PriceCurve.from_entity()` anwenden. Default: Preis ist Endpreis, nichts wird
umgerechnet.

## E8 — Startempfehlung für flexible Verbraucher (optional)

**Bei EOS:** Hausgeräte werden mit Verbrauch, Laufzeit und erlaubten Zeitfenstern
konfiguriert (`devices/genetic/homeappliance.py`, `configtimewindow.md`); der
Optimierer legt die Startzeit fest.

**Für miniEMS** reicht eine Anzeige ohne Steuerung: Sensor
`sensor.miniems_best_start_<gerät>` mit dem günstigsten Startzeitpunkt in den nächsten
12 h, berechnet aus `PriceCurve` und PV-Prognose (V3b-Kurve, sobald vorhanden), für
konfigurierbare Geräte (kWh, Dauer). Wenig Aufwand, hoher sichtbarer Nutzen, passt zur
`ChargeTask`-Idee im Energiefahrplan.

## Was wir bewusst *nicht* übernehmen

- **Genetischen Algorithmus / numpy / scipy / 48-h-Optimierung.** Siehe oben: zu schwer
  für ein HA-Add-on, nicht reproduzierbar, schwer erklärbar.
- **Plan-Architektur „rechnen hier, ausführen dort".** Schreibbestätigung und
  Nachregeln im nächsten Tick sind der Vorteil von miniEMS.
- **Probabilistisches Modell für den PV-Direktverbrauch innerhalb einer Stunde**
  (`inverter.py`, `calculate_expected_direct_consumption`). EOS braucht das wegen
  grober Slots; miniEMS misst alle 30 s.
- **Generisches Gerätemodell für beliebig viele Verbraucher.** Bleibt bei der
  `ChargeTask`-Struktur aus dem Energiefahrplan.

## Umsetzungsreihenfolge

| Schritt | Inhalt | Aufwand | Nutzen |
|---|---|---|---|
| 1 | **E1** Stündliches Lastprofil (Tabelle + `load_between()`) | mittel | Freischalter für 2, 3, 5 |
| 2 | **E4a** Entlade-Wirkungsgrad messen und als Sensor anzeigen | klein | macht das Gate belastbar |
| 3 | **E2** `HOLD_FOR_PEAK` hinter Schalter, zuerst Simulation | mittel | **hoch** — größter Hebel |
| 4 | **E3** SoC-bewusstes Gate | klein–mittel | mittel–hoch |
| 5 | **E4b** `battery_wear_cost_eur_kwh` | klein | Ehrlichkeit des Gates |
| 6 | **E5** Bedarfsbasiertes Defizit im Energiefahrplan (Anzeige) | klein | mittel |
| 7 | **E6 + E7** mit Preisquelle a) zusammen | mittel | hoch bei Day-Ahead-Nutzern |
| 8 | **E8** Startempfehlung | klein | Komfort |

Jeder Schritt einzeln abschaltbar; fehlt eine Zutat, gilt das heutige Verhalten.

## Betroffene Dateien

- `store.py` — Tabelle `hourly_load`, Migration
- `consumption_model.py` — `hourly_profile()`, `load_between()`
- `cost_optimizer.py` — Stunden-Akkumulation, Entlade-Wirkungsgrad
- `const.py` — `EMSMode.HOLD_FOR_PEAK`
- `ems_controller.py` — `_decide()`, `_should_hold_discharge()`, `_grid_charge_pays()`
- `inverter_controller.py` — `apply_mode()` für den neuen Modus
- `energy_plan.py` — bedarfsbasiertes Defizit
- `price_curve.py` — Spot-/Gebühren-Umrechnung (E7)
- `config_loader.py`, `migration.py` — neue Felder
- `ha_sensor_publisher.py`, Frontend-Karten — neue Sensoren und Modus-Badge

## Verifikation

1. **Unit-Tests je Baustein** mit synthetischem ToU-Kalender (NIEDRIG 02–06 und 12–16,
   STANDARD, HOCH 17–21) und festem Lastprofil.
2. **E2:** Wintertag ohne PV, SoC 50 %, HOCH-Bedarf 6 kWh → `HOLD_FOR_PEAK` von
   Mittag bis 17:00, danach Entladen. Gegenprobe: SoC 95 % → keine Sperre.
3. **E3:** Gleicher Preis, SoC 20 % vs. 70 % → Gate lässt einmal laden, einmal nicht.
4. **Fail-Pfade:** Jede Zutat einzeln auf `None` → heutiges Verhalten, nachgewiesen
   über bestehende Tests, die unverändert grün bleiben.
5. **Simulation auf der Produktivinstanz** über mindestens eine Woche (Modus wird
   geloggt, aber nicht geschrieben), Auswertung der eingesparten Spanne gegen das
   Event-Log, bevor der Schalter scharf geschaltet wird.

## Offene Fragen

- Verhält sich der Deye bei Entladestrom 0 und gleichzeitigem PV-Überschuss wie
  erwartet, oder braucht `HOLD_FOR_PEAK` zusätzlich eine Grid-Charge-Sperre?
- Reichen 28 Tage Historie für ein stabiles Stundenprofil, oder braucht es — wie bei
  EOS — ein Standardlastprofil als Startwert für frische Installationen?
- Sollen E2 und E3 dieselbe „ungedeckte Fenster"-Berechnung teilen (eine Funktion in
  `energy_plan.py`), damit Anzeige und Entscheidung nicht auseinanderlaufen?
