---
revision_date: 2026-11-03
---

# Was miniEMS von EnergyWizard lernen kann

!!! info "Status: Analyse und Planung, nichts umgesetzt"
    Vergleich mit [EnergyWizard Open](https://github.com/murre87/energywizard)
    (AGPL-3.0), Stand `main` vom 2026-11-03. Gelesen wurden README sowie
    `smartbox/learning.py`, `smartbox/mpc.py`, `smartbox/battery_learning.py` und
    `smartbox/fuse_guard.py`. Wegen der AGPL werden nur **Ideen und Rechenwege**
    übernommen, kein Code.

    | Baustein | Stand |
    |---|---|
    | W1 — Negative Preise als Ergänzung des netzdienlichen Modus | offen — kleinster Aufwand, sofort Nutzen |
    | W2 — Hauptsicherungs-Wächter (thermisches Modell) | offen — Sicherheit |
    | W3 — Akku-Kennwerte lernen (Wirkungsgrad, Kapazität, Leistung) | offen |
    | W4 — PV-Prognose je Tagesstunde korrigieren | offen — Voraussetzung für W6 |
    | W5 — Plan gegen Ergebnis (selbstkorrigierende Prognose) | offen |
    | W6 — Sonneneintrag gelernt | offen — braucht W4 |
    | W7 — Hausmasse vorheizen | offen — größter Baustein, braucht Innentemperatur |
    | W8 — Entity-Auswahl und Prüfung beim Speichern | offen — Bedienung |

    Bewusst ausgelassen: die Gegenrechnung „was hätte die Simulation gespart"
    (auf Wunsch zurückgestellt).

## Die beiden Systeme im Vergleich

| | **miniEMS** | **EnergyWizard** |
|---|---|---|
| Prinzip | Regelschleife alle 30 s mit Gates, Hysterese, Verweildauer | Deterministischer Prioritätsmotor (`gpm.py`), Minutentakt |
| Geräte | Deye-Hybrid-Wechselrichter + Akku, Wärmepumpe (Warmwasser) | E-Auto, Wärmepumpe (Heizung), Akku, PV-Export, Whirlpool, Sicherung |
| Lernen | Erklärbare Modelle: Mediane, lineare Regression, Startwert + Grenzen | RandomForest (scikit-learn), per `pickle` in Postgres gespeichert, täglich neu trainiert |
| Hausmodell | Stündliche Last aus Außentemperatur (`load_forecast.py`) | Aufheiz- und Auskühlrate der **Innentemperatur** aus Außentemp., Wind, PV-Leistung, Uhrzeit |
| Planung | Bridge-Plan in 30-min-Slots, WW-Planer (gierig) | Akku-Tagesplan in 15-min-Slots, Vorheiz-Planer (gierig, „MPC-lite") |
| Sicherheit | Simulationsmodus, Schreibbestätigung | Schattenmodus, thermischer Sicherungswächter |

!!! success "Bestätigung bisheriger Entscheidungen"
    - **Gelernt ist ein Rat, keine Wahrheit.** EnergyWizard nutzt gelernte Werte nur mit
      Mindestdaten, innerhalb physikalischer Grenzen und mit Rückfall auf die
      Konfiguration. miniEMS macht das schon so (COP-Startwert, Warmwasser-Modell,
      Ladeleistung je SoC).
    - **Verlust über Phasen messen.** Die Auskühlrate des Hauses misst EnergyWizard
      über Phasen „Wärmepumpe aus" — dieselbe Methode wie unsere
      `hot_water_loss_segments` für den Boiler.
    - **Zapfungen auf Rohwerten erkennen.** EnergyWizard hatte genau hier einen
      Fehler: Durch 15-min-Mittelwerte verschmierten die Temperatursprünge, das
      Zapfprofil lernte nichts. miniEMS sucht den Abfall im 10-min-Fenster auf den
      Rohwerten (`HW_DRAW_WINDOW_SEC`, `hw_draw_drop_k`).
    - **Gierige Planung genügt.** Der Vorheiz-Planer von EnergyWizard ist im Kern
      derselbe Algorithmus wie `hot_water_planner.py`: erste Verletzung suchen,
      billigsten freien Slot davor belegen, neu simulieren.

## W1 — Negative Preise als Ergänzung des netzdienlichen Modus

**Bei EnergyWizard:** Export aus, sobald der Preis unter 0 fällt, wieder an über 0.

**Bei miniEMS:** Der netzdienliche Modus (`pv_export_priority_enabled`,
`_should_hold_pv_charge()` in `ems_controller.py`) hält den Akku vormittags frei und
lädt rechtzeitig vor der PV-Spitze. Das deckt die Mittagsspitze ab, entscheidet aber
nur nach Uhrzeit und Prognose, **nie nach Preis**:

- Vormittags bei negativem Preis läuft `EXPORT_SURPLUS` weiter — Einspeisen kostet Geld,
  obwohl der Akku Platz hätte.
- Ist der Akku voll (`battery full` → IDLE), fließt der Überschuss ungebremst ins Netz.
- Die Solcast-Spitze muss nicht mit dem Fenster negativer Preise übereinstimmen.

Überschneidet sich mit [E6 aus den EOS-Lehren](eos-learnings.md#e6-negative-preise-und-unvergutete-einspeisung);
W1 ist die konkrete Fassung.

**Vorschlag:**

1. `_should_hold_pv_charge()`: Preis ≤ 0 → Halt aufheben, laden (Grund `"negative price"`).
2. `_should_grid_charge()`: bei Preis ≤ 0 Wirtschaftlichkeits-Gate überspringen.
3. Akku voll und Preis ≤ 0 → Warmwasser bis `hw_max_temp_c` (neuer Auslöser im
   `HotWaterController` neben dem PV-Export-Auslöser, Modus `HW_PV`).
4. Optional: Einspeisebegrenzung über die vorhandene Rolle `inverter.grid_export_limit`
   (`roles.yaml`) auf 0 W, solange Preis < 0 und Akku voll. Nur Simulation zuerst,
   Rückstellung bei Shutdown und Handoff wie bei den übrigen Stellgrößen.

**Dateien:** `ems_controller.py`, `hot_water_controller.py`, `inverter_controller.py`,
Tests.

## W2 — Hauptsicherungs-Wächter (thermisches Modell)

**Bei EnergyWizard (`fuse_guard.py`):** Eine Sicherung löst durch **Wärme** aus, nicht
durch eine einzelne Messung. Deshalb:

- je Phase wird I² mit τ = 60 s tiefpassgefiltert — die „Wärme" der Sicherung;
- Arbeitsgrenze = Nennstrom − Sicherheitsmarge;
- Drosseln, wenn der gefilterte Strom die Grenze überschreitet, **sofort**, wenn eine
  einzelne Messung über 1,2 × Nennstrom liegt;
- nach dem Drosseln 20 s keine Erhöhung;
- einspeisende Phasen nie drosseln (weniger Last erhöht nur die Einspeisung);
- veraltete Messwerte → vorsichtige feste Obergrenze, nicht „Hauslast = 0 A".

**Bei miniEMS:** Netzladen mit bis zu 185 A (DC) ohne Blick auf die Phasenströme.
Zusammen mit Wärmepumpe, Herd oder E-Auto kann das die Hausanschlusssicherung
überlasten. Die Rollen `energy_meter.l1_current … l3_current` gibt es schon
(`roles.yaml`).

**Vorschlag:**

- Neues reines Modul `fuse_guard.py` (ohne I/O, testbar), Config `main_fuse_a`,
  `fuse_margin_a`, Phasenstrom-Entities.
- Wirkt auf den Ladestrom in `GRID_CHARGING`: freie Leistung je Phase → W → A über die
  Akkuspannung (wie `_config_charge_power_kw()`).
- Grenze: Der 30-s-Takt ist für das thermische Modell ausreichend, für die
  Sofort-Abschaltung zu langsam. Option: die drei Phasenströme alle 10 s abfragen, nur
  solange `GRID_CHARGING` aktiv ist.

## W3 — Akku-Kennwerte lernen

**Bei EnergyWizard (`battery_learning.py`):**

- Lade- und Entladeabschnitte aus dem Verlauf: |P| > 150 W, Lücken ≤ 300 s,
  ΔSoC ≥ 5 %.
- k_in = kWh (AC) je % SoC beim Laden, k_out beim Entladen (jeweils Median).
- **Wirkungsgrad** η = k_out / k_in, **nutzbare Kapazität** = 100 · √(k_in · k_out).
- **Leistung** = 95. Perzentil der gemessenen Lade- bzw. Entladeleistung.
- Verwendet erst ab 3 Abschnitten je Richtung und nur innerhalb von Grenzen:
  η 0,70–0,98, Kapazität 50–130 % der konfigurierten, Leistung 30–150 %.

**Bei miniEMS:** Gelernt wird nur die Ladeleistung je SoC-Bereich
(`battery_capability.py`) und ein Tageswirkungsgrad aus dem Verlust-Sensor. Die
Kapazität ist fest (`battery_capacity_kwh`) oder kommt vom Deye
(`battery_capacity_entity`) — Alterung bleibt unerkannt.

**Vorschlag:**

- Abschnitte online erfassen (Muster: Segmente im `HeatPumpRecorder`), Tabelle
  `battery_segments`.
- Neues Modul `battery_learning.py` mit den Formeln oben, Grenzen und Mindestdaten.
- Nutzer: `battery_model.py` (`useable_kwh`), `_grid_charge_pays()` (η), Bridge-Plan.
- Anzeige im Dashboard: „gelernt" oder „Startwert", wie beim COP.

## W4 — PV-Prognose je Tagesstunde korrigieren

**Bei EnergyWizard:** Faktor je Stunde des Tages = Σ gemessen / Σ Prognose, ab 5
Stunden Daten je Tagesstunde, begrenzt auf 0,3–2,0. Fängt lokale Verschattung,
Morgennebel im Tal oder einen Baum am Nachmittag.

**Bei miniEMS:** Solcast wird ungefiltert verwendet; `hourly_stats` kennt keine
PV-Spalte.

**Vorschlag:**

- `pv_kwh` (gemessen) und `pv_forecast_kwh` (Solcast) je Stunde in `hourly_stats`.
- Faktor je Tagesstunde (Monate gleitend, z. B. letzte 30 Tage) in
  `_build_plan_slots()` anwenden.
- Mindestdaten und Grenzen wie oben, Anzeige der Faktoren im Dashboard.

## W5 — Plan gegen Ergebnis

**Bei EnergyWizard (`outcome_bias`):** Die Prognose je 15-min-Slot wird gespeichert und
später mit der Messung verglichen. Ergibt sich ein systematischer Fehler, wird er als
Faktor eingerechnet:

- Quote = Σ gemessen / Σ Prognose (Summen statt Mittel der Quoten — robust bei Slots
  nahe null);
- erst ab 3 Tagen (Last) bzw. 40 sonnigen Slots (PV);
- begrenzt auf 0,8–1,25 (Last) und 0,7–1,2 (PV), unter 3 % Abweichung keine Korrektur.

**Bei miniEMS:** Bridge-Plan und Lastprognose korrigieren sich nicht am eigenen Fehler.

**Vorschlag:** Slot-Prognosen des Bridge-Plans persistieren, täglich die Quote bilden,
als Faktor auf Last und PV anwenden. Dashboard: „Prognose lag zuletzt x % zu hoch".

## W6 — Sonneneintrag gelernt

Nur sinnvoll als **gelernte** Größe — ein pauschaler Abschlag wäre zu ungenau.

**Bei EnergyWizard:** Die eigene PV-Leistung dient als Einstrahlungs-Sensor, dazu ihr
3-Stunden-Mittel für die Trägheit des Hauses. Beides sind Merkmale im RandomForest der
Auskühlrate; negative Auskühlung (Sonne heizt das Haus) ist ausdrücklich erlaubt.

**miniEMS-Übersetzung (erklärbar, ohne ML):** Das Wärmepumpen-Stundenmodell in
`load_forecast.py` (`fit_load_model`) wird erweitert zu

    hp(h) = hp_base + k · HDD(h) − s · PV_3h(h)

- `s` per kleinsten Quadraten aus `hourly_stats` (braucht `pv_kwh` aus W4), `s ≥ 0`.
- Aktiv nur mit genug sonnigen Heizstunden (z. B. ≥ 50 Stunden mit HDD > 0 und PV), sonst
  `s = 0`.
- Prognose: Solcast-PV je Slot als Ersatz für die Einstrahlung.
- Gütetest: Fehler der Lastprognose im Rückblick mit und ohne `s` — nur übernehmen, wenn
  der Fehler sinkt. `s` im Dashboard anzeigen.

## W7 — Hausmasse vorheizen

**Bei EnergyWizard (`mpc.py`, `learning.py`):**

- **Lernen über Phasen:** Der Verlauf wird in Phasen „Wärmepumpe heizt" und „Wärmepumpe
  ruht" zerlegt (Lücken > 5 min trennen, Dauer ≥ 30 min, Änderung > 0,05 K). Je Phase:
  Rate der Innentemperatur in K/h, plausibel nur −1,0…0,6 K/h (Auskühlen) bzw.
  0,05…1,0 K/h (Aufheizen).
- **Merkmale:** Außentemperatur, Wind, PV-Leistung, PV-3h-Mittel, Uhrzeit (Auskühlen);
  Außentemperatur, Wind, Innentemperatur (Aufheizen). Modell erst ab 30 bzw. 15 Phasen,
  sonst fester Rückfallwert.
- **Planen:** Innentemperatur stündlich simulieren; bei der ersten Verletzung des
  Komfortbands billigste freie Stunde in den 6 Stunden davor heizen; wiederholen.
- **Erst Rückblick, dann live:** Der Planer wird gegen eine reaktive Basis auf der
  eigenen Historie nachgerechnet. Die geschätzte Ersparnis wird gespeichert („so viel bin
  ich wert") — erst danach wird gesteuert.

**Bei miniEMS:** Gieriger Planer und Phasen-Messung existieren bereits
(`hot_water_planner.py`, `hot_water_loss_segments`). Es fehlen: ein
Innentemperatur-Sensor, das Signal „Wärmepumpe heizt" (Betriebsmodus-Code wie beim
Warmwasser) und ein Aktor (Raum-Soll oder Heizkurven-Offset der Lambda über
`write_modbus_register`, analog zum Boiler-Sollwert).

**Vorschlag in Stufen:**

1. **Daten:** Innentemperatur erfassen, Heiz- und Auskühlphasen speichern; Raten
   **linear** statt RandomForest: Auskühlen `a · (T_innen − T_außen) − b · PV_3h`,
   Aufheizen `c + d · T_außen`.
2. **Rückblick-Test** im Wärmepumpen-Tab: Planer gegen reaktive Basis, geschätzte
   Ersparnis.
3. **Simulation:** Plan und `[SIM]`-Schreibvorgänge, wie beim Warmwasser.
4. **Live:** mit Komfortband, Mindestlaufzeiten und Rückgabe an die Wärmepumpe bei
   fehlenden Daten.

## W8 — Entity-Auswahl und Prüfung beim Speichern

**Bei EnergyWizard:** Einrichtungsassistent mit Entity-Auswahl je Gerät, Verbindungstest
vor dem Speichern, keine Dateien zu bearbeiten.

**Bei miniEMS:** Entity-IDs sind Freitextfelder. Ein Tippfehler
(`sensor.shelly_3em_total_powe`) und eine falsche Wetter-Entity
(`weather.openweathermap` statt `…_2`) blieben bis zur Fehlersuche unbemerkt.

**Vorschlag:**

- Auswahlliste je Feld aus den HA-Zuständen, gefiltert nach Domain und Einheit
  (`accepts_unit` aus `roles.yaml`).
- Beim Speichern: Warnung „existiert nicht" oder „unerwartete Einheit", Speichern bleibt
  möglich.
- Vorhanden: `ha_ws_api.list_entities()`, `sensor_validator.py`, `device_resolver.py`.

## Was wir bewusst *nicht* übernehmen

| Idee | Grund |
|---|---|
| RandomForest/scikit-learn + `pickle` | Black Box, schwere Abhängigkeit für Alpine/aarch64, widerspricht dem Grundsatz in `load_forecast.py` („ein Dutzend Zahlen, die man prüfen kann") |
| Telemetrie standardmäßig an | miniEMS sendet nichts nach außen |
| Eigene Handy-App (PWA) | HA-App und Ingress genügen |
| Treiber für viele Hersteller | Geräteprofile (v3.0) decken das generischer ab |
| Gegenrechnung „Simulation hätte x € gespart" | auf Wunsch zurückgestellt |

## Umsetzungsreihenfolge

1. **W1** — eine Abfrage plus Test, sofort Nutzen bei dynamischem Tarif.
2. **W8** — verhindert die Konfigurationsfehler, die zuletzt Zeit gekostet haben.
3. **W2** — Sicherheit, sobald mit hohen Strömen aus dem Netz geladen wird.
4. **W3** — genauere Planung, Alterungserkennung.
5. **W5** — selbstkorrigierende Prognose, profitiert von W3.
6. **W4** — PV-Korrektur, liefert `pv_kwh` für W6.
7. **W6** — Sonneneintrag gelernt.
8. **W7** — Vorheizen, größter Baustein, braucht neue Sensoren und einen Aktor.

## Betroffene Dateien

- Regelung: `ems_controller.py`, `hot_water_controller.py`, `inverter_controller.py`
- Neu: `fuse_guard.py`, `battery_learning.py`, später ein Modul für das Hausmodell (W7)
- Daten: `store.py` (`hourly_stats` + `pv_kwh`/`pv_forecast_kwh`, `battery_segments`,
  Slot-Prognosen), `load_forecast.py`, `bridge_planner.py`
- Konfiguration: `config_loader.py`, `migration.py`, `templates/settings.html`,
  `web_server.py`
- Doku: `docs/user/configuration.md`, `docs/technical/calculations.md`

## Verifikation

- Je Baustein reine Funktionen mit Unit-Tests (Muster: `test_hot_water_model.py`,
  `test_history_backfill.py`).
- Gelernte Werte immer mit „gelernt/Startwert"-Anzeige und Grenzen; Rückblick-Test gegen
  die bisherige Logik, bevor ein gelernter Wert Entscheidungen beeinflusst.
- Neue Stellgrößen (W1 Einspeisebegrenzung, W2 Ladestrom, W7 Heizung) zuerst nur in
  Simulation, Prüfung per HA-MCP auf der Testinstanz.

## Offene Fragen

- **W2:** Nennstrom der Hauptsicherung? Quelle der Phasenströme — Shelly 3EM am
  Netzanschluss oder die Netz-CTs des Deye?
- **W1:** Bietet der Deye über Solarman eine schreibbare Einspeisebegrenzung (W)?
- **W7:** Welcher Innentemperatur-Sensor ist repräsentativ? Welches Lambda-Register
  verschiebt Raum-Soll oder Heizkurve, und welcher Betriebsmodus-Code bedeutet „heizt"?
