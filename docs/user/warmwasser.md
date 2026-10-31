---
revision_date: 2026-10-30
---

# Wärmepumpe: Warmwasser-Steuerung

miniEMS kann die Warmwasserbereitung (WW) deiner Wärmepumpe in die Stunden legen,
in denen sie am wenigsten kostet. Das sind die günstigen Tarifstunden, Stunden mit
PV-Überschuss und Stunden mit milder Außentemperatur, denn dann ist der COP besser.
Dabei bleibt die Mindesttemperatur immer gehalten, und an Werktagen ist der Speicher
morgens warm.

Seit v3.5.0. Getestet mit der **Lambda**-Wärmepumpe (Integration `lambda_heat_pumps`).

!!! info "So steuert miniEMS die Wärmepumpe"
    miniEMS schaltet die Wärmepumpe nicht direkt ein oder aus. Es verändert nur den
    **Boiler-Sollwert**:

    - **Einschalten:** Sollwert deutlich über die Ist-Temperatur setzen (Ist + 10 K).
      Das ist nur ein **Startimpuls**, damit die Wärmepumpe in den WW-Modus wechselt.
      Die Wärmepumpe muss diesen Wert nie erreichen.
    - **Ausschalten:** Wann Schluss ist, entscheidet miniEMS anhand der gemessenen
      Boiler-Temperatur. Erreicht sie die **Abschalttemperatur**
      `hw_morning_target_c` (z. B. 51 °C), setzt miniEMS den Sollwert auf einen festen,
      niedrigen Wert (`hw_idle_setpoint_c`, Standard 35 °C). Damit ist WW aus, und die
      eigene WW-Regelung der Wärmepumpe greift nicht ein. Das Minimum sichert miniEMS
      selbst mit der Schutzladung.

    Wird miniEMS beendet, setzt es den Sollwert auf das Tagesziel zurück. Danach regelt
    die Wärmepumpe wieder allein.

---

## In drei Schritten zum Live-Betrieb

### 1. Sensoren eintragen (Datensammlung)

Im Tab **Einstellungen**, Abschnitt *Heat Pump / Hot Water (data collection)*:

| Einstellung | Was eintragen |
|---|---|
| `boil_temp_entity` | Temperatur im Warmwasserspeicher (°C). **Pflicht.** Ohne sie bleibt die Steuerung aus. |
| `heat_pump_cop_entity` | Live-COP der Wärmepumpe (0 = aus) |
| `heat_pump_mode_entity` | Betriebsmodus der Wärmepumpe |
| `heat_pump_request_type_entity` | Angeforderter Betriebsmodus |
| `outdoor_temp_entity` | Außentemperatur, am besten der Außenfühler der Wärmepumpe |
| `heat_pump_power_entity` | Leistungsmesser der Wärmepumpe in W (optional, macht das Lernen genauer) |

Ab jetzt sammelt miniEMS Daten: COP, Wärmeverlust des Speichers und Zapfungen.
Gesteuert wird noch nichts.

### 2. Simulation prüfen

Im Abschnitt *Hot Water Control* die Schreib-Einstellungen prüfen. Für die Lambda
sind die Defaults schon richtig:

| Einstellung | Lambda-Wert |
|---|---|
| `hw_write_service` | `lambda_heat_pumps.write_modbus_register` |
| `hw_write_data` | `{"register_address": 2050, "value": {value}}` |
| `hw_write_scale` | `10` (51,0 °C werden als 510 geschrieben) |
| `hw_target_entity` | `sensor.eu08l_boil1_target_high_temperature`. Prüfen, ob der Sensor bei dir so heißt. |

Dann ganz oben unter **Control Mode** die **WP-Steuerung** einschalten und
**Simulation** an lassen. Im Log stehen jetzt Zeilen wie

```text
[SIM] lambda_heat_pumps.write_modbus_register({'register_address': 2050, 'value': 570})
```

Prüfe einen Tag lang, ob Werte und Zeitpunkte plausibel sind.

### 3. Live schalten

Unter **Control Mode** die **Simulation** ausschalten. Das wirkt sofort, ohne
Neustart, und gilt auch nach einem Neustart weiter.

!!! warning "Simulation gilt für Akku und Wärmepumpe gemeinsam"
    Der Simulations-Schalter schaltet Akku- **und** WP-Steuerung zugleich live.
    Soll nur die Wärmepumpe live laufen, schalte die **Batteriesteuerung** aus.

---

## Der Tab „Wärmepumpe“

| Bereich | Bedeutung |
|---|---|
| **Modus-Badge** | Aktuelle Entscheidung, siehe Tabelle unten |
| **Steuerungs-Badge** | *Steuerung aus*, *Simulation* oder *Live* |
| **Entscheidung** | Kurze Begründung, z. B. `wait – enough until 02:30 (45.9 °C then); now 7.5 ct/kWh_th, then 2.5 ct/kWh_th`, darunter die Kosten einer Ladung jetzt |
| **WW Ad-hoc-Bereitung** | Sofort Warmwasser machen, siehe unten |
| **Boiler-Temperatur, Minimum, Morgenziel** | Ist-Wert und Vorgaben |
| **Sollwert** | Was miniEMS geschrieben hat. *bestätigt* = der Sensor der Wärmepumpe meldet denselben Wert zurück. |
| **COP live / gelernt** | Aktueller und gelernter COP bei der aktuellen Außentemperatur. *Startwert* = noch zu wenige Daten. |
| **Verlust, Heizleistung, Zapfprofil** | Was miniEMS über deinen Speicher gelernt hat. Das Zapfprofil zeigt „lernt noch“ mit den Tagen Historie, bis 7 Tage zusammen sind. |
| **Zapfung läuft** | Unter der Boiler-Temperatur, sobald eine Zapfung erkannt ist: seit wann und wie viel K bisher. Sie steht dann auch gleich als erste Zeile unter „Letzte Zapfungen“. |
| **Warmwasser-Fahrplan** | Geplante Ladungen der nächsten 36 h. Je Ladung: **Kosten je kWh Wärme** (ct/kWh), Start- und Zieltemperatur, Wärme, Strom, Strompreis, COP und Quelle (☀ PV, 🔋 PV-Akku, ⚡ Netz). |
| **Zeitstrahl** | Ladungen grün (PV) bzw. nach Kosten je kWh Wärme gefärbt (grün = günstig, rot = teuer) |
| **Temperaturverlauf** | Prognose der Boiler-Temperatur mit Linien für Minimum, Tagesziel und **Abschalttemperatur** |
| **Tabellen** | Letzte Stunden, Zapfungen und Verlust-Messungen |

### Modi

| Modus | Bedeutung |
|---|---|
| `HW Idle` | Kein Laden nötig, oder Pause nach einer Ladung |
| `HW Charging (Plan)` | Ladung in einem günstigen Fenster aus dem Fahrplan |
| `HW Charging (PV Surplus)` | Ladung mit PV-Überschuss |
| `HW Protection (Min Temp)` | Speicher unter der Mindesttemperatur, Schutzladung. Hat immer Vorrang. |
| `HW Charging (Manual)` | Ad-hoc-Bereitung per Button |
| `HW Handoff (Heat Pump Control)` | Boiler-Temperatur fehlt. Die Wärmepumpe regelt selbst, Sollwert = Tagesziel. |

### Button „WW Ad-hoc-Bereitung“

Für den Fall „Ich brauche jetzt warmes Wasser“:

- Ein Klick setzt den Sollwert sofort auf **Ist-Temperatur + 10 K**
  (`hw_adhoc_delta_k`) als Startimpuls. Ist 47 °C, wird 57 °C geschrieben. Der Wert ist
  mindestens Abschalttemperatur + 2 K und höchstens 65 °C.
- **Ausgeschaltet wird bei Ist ≥ Abschalttemperatur** (`hw_morning_target_c`, z. B.
  51 °C). Dann wird 35 °C geschrieben.
- Ist der Speicher schon auf Abschalttemperatur, passiert nichts. Neben dem Button steht
  dann „Speicher bereits auf Abschalttemperatur“.
- Solange die Ladung läuft, bleibt der Button **eingedrückt** und die rote LED blinkt.
  Daneben stehen Sollwert, Abschalttemperatur und Laufzeit.
- Ein **zweiter Klick** bricht ab. Spätestens nach 2 h endet die Ladung von selbst.
- Liegt der Speicher unter der Mindesttemperatur, hat die Schutzladung Vorrang.

Bei *Steuerung aus* oder *Simulation* steht neben dem Button, dass nichts geschrieben wird.

**Den Button gibt es auch im HA-Dashboard „miniEMS“**, auf der Seite *Haus* im obersten
Abschnitt. Er verhält sich genauso: Er rastet ein, solange die Ladung läuft, und ein
zweiter Klick bricht ab. Er nutzt die Aktion `miniems.hot_water_adhoc`, die du auch in
eigenen Automationen verwenden kannst (siehe [HA-Sensoren](sensors.md)).
Nach einem Update der Integration den Browser einmal mit Strg+Shift+R (bzw. Shift+F5)
neu laden, sonst zeigt er noch die alte Karte.

---

## Wie miniEMS entscheidet (kurz)

1. **Lernen:** Aus den gesammelten Daten lernt miniEMS:
    - den COP im Warmwasser-Betrieb abhängig von der Außentemperatur (COP-Werte aus
      dem Heizbetrieb werden dafür nicht verwendet),
    - wie schnell der Speicher auskühlt,
    - die Heizleistung der Wärmepumpe,
    - wann ihr typischerweise Warmwasser zapft.

    Bis genug Daten da sind, gelten Startwerte (z. B. COP 4,77 bei 8,5 °C).
2. **Vorausrechnen:** Bei jedem Tarifwechsel (spätestens alle 5 Minuten) rechnet
   miniEMS die Boiler-Temperatur für die nächsten 36 h voraus, mit Wärmeverlust und
   erwarteter Zapfung. Daraus ergibt sich, wann der Speicher sonst unter das
   **Tagesziel** fallen würde bzw. ob er an Werktagen um `hw_morning_time` frisch geladen
   ist.
3. **Die Frage:** *Reicht der Speicher bis ins nächste günstige Fenster?*
    - **Ja** → warten, Sollwert 35 °C.
    - **Nein** → jetzt laden, bis zur Abschalttemperatur (z. B. 51 °C).

   „Günstig“ heißt: niedrigste **Kosten je kWh Wärme** = Strompreis ÷ COP. Dabei zählt,
   woher der Strom kommt:
    - **PV-Überschuss:** Einspeisevergütung. Ist der Akku noch nicht voll, gilt der Preis,
      zu dem diese kWh später nachgeladen werden müsste.
    - **Akku:** Hat der Akku Energie, gilt sein Nachladepreis (günstigster Tarif danach ÷
      Akku-Wirkungsgrad), nicht der teure Abendtarif.
    - **Netz:** Tarif aus dem Tarifkalender.

   Wer früher lädt, verliert bis zum Bedarf mehr Wärme. Das ist eingerechnet. Ist
   „jetzt laden“ höchstens 5 % teurer als die beste spätere Möglichkeit
   (`hw_decision_tolerance_pct`), wird sofort geladen.
4. **Immer sofort**, unabhängig vom Preis: Schutzladung unter dem Minimum, Ad-hoc-Button,
   spontaner PV-Überschuss (Einspeisung ≥ 1500 W).

Jede Entscheidung und jede Ladung steht im **Ereignisprotokoll** (Tab *Protokoll*,
Kennzeichen „Warmwasser“), mit Begründung und den angenommenen Kosten in ct/kWh Wärme.
Zum Beispiel: „tariff change 30.1 ct: wait – enough until 02:30 (45.9 °C then); now
7.5 ct/kWh_th, then 2.5 ct/kWh_th“. Am Ende einer Ladung stehen Temperaturen, Dauer,
erzeugte Wärme und die ungefähren Kosten.

Läuft die Steuerung live, fließt der WW-Fahrplan auch in die Akku-Planung ein.
Strom, den die Wärmepumpe aus PV nimmt, steht dem Akku nicht zur Verfügung.

---

## Einstellungen im Überblick

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `hw_control_enabled` | `false` | WP-Steuerung an/aus. Aus = Entscheidungen werden nur angezeigt. Auch unter *Control Mode*. |
| `hw_control_simulation` | `true` | Nur protokollieren (`[SIM]`). Unter *Control Mode* gemeinsam mit dem Akku geschaltet. |
| `hot_water_min_temp_c` | `45` | Mindesttemperatur. Darunter startet die Schutzladung (bis zur Abschalttemperatur). |
| `hw_day_target_c` | `45` | Temperatur, die über den Tag gehalten wird (mindestens das Minimum) |
| `hw_morning_target_c` | `50` | **Abschalttemperatur** von Fahrplan-, Schutz- und Ad-hoc-Ladung. Zugleich das Morgenziel. Mindestens Tagesziel + 2 K, sonst wird sie beim Start angehoben. |
| `hw_morning_time` | `06:30` | Bis dahin ist der Speicher an Werktagen frisch geladen |
| `hw_workday_entity` | leer | Binärsensor „heute Werktag“ (an = Werktag), sonst Mo–Fr |
| `hw_max_temp_c` | `55` | Abschalttemperatur einer spontanen PV-Überschuss-Ladung |
| `hw_boost_delta_k` | `10` | Startimpuls: Sollwert = Ist + dieser Wert (mindestens Abschalttemperatur + 2 K) |
| `hw_idle_setpoint_c` | `35` | Sollwert, wenn keine Ladung läuft (Abschalttemperatur erreicht, abgebrochen, Pause). Muss mindestens 5 K unter `hot_water_min_temp_c` liegen, sonst wird er beim Start abgesenkt. |
| `hw_adhoc_delta_k` | `10` | Startimpuls beim Ad-hoc-Button: Ist + dieser Wert |
| `hw_decision_tolerance_pct` | `5` | Jetzt laden, wenn das höchstens so viel Prozent teurer je kWh Wärme ist als die beste spätere Möglichkeit |
| `hw_volume_l` | `200` | Speichervolumen in Litern |
| `hw_heat_power_kw` | `4` | Thermische Heizleistung, bis sie gelernt ist |
| `hw_cop_prior` / `hw_cop_prior_temp_c` | `4.77` / `8.5` | Start-COP und zugehörige Außentemperatur |
| `hw_pv_min_export_w` | `1500` | Ab diesem Netzexport startet eine PV-Überschuss-Ladung |
| `hw_min_off_sec` | `1800` | Mindestpause zwischen zwei Ladungen |
| `hw_write_service` / `hw_write_data` / `hw_write_scale` | Lambda | Schreibbefehl der WP-Integration, siehe oben |
| `hw_target_entity` | Lambda-Sensor | Rücklese-Sensor zur Bestätigung |

---

## Häufige Fragen

**Der Sollwert bleibt „ausstehend“.**
Der Rücklese-Sensor `hw_target_entity` meldet nicht den geschriebenen Wert. Prüfe in
HA → Entwicklerwerkzeuge → Aktionen, ob der Schreibbefehl von Hand funktioniert.
Prüfe auch, ob der Sensor °C liefert (51,0) und keinen Rohwert (510).

**Der Modus ist „HW Handoff“.**
Die Boiler-Temperatur ist nicht verfügbar. miniEMS übergibt die Regelung an die
Wärmepumpe, bis der Sensor wieder Werte liefert.

**Der COP zeigt „Startwert“.**
Es gibt noch weniger als 6 h echte WW-Laufzeit in der Historie. Das gibt sich nach
einigen Tagen.

**Die Wärmepumpe erreicht den Sollwert (z. B. 57 °C) nie. Ist das ein Problem?**
Nein. Der Sollwert ist nur ein Startimpuls. miniEMS schaltet ab, sobald der Speicher
die Abschalttemperatur erreicht, z. B. 51 °C.

**Warum steht der Sollwert nach einer Ladung auf 35 °C?**
Das ist gewollt. Läge der Sollwert in der Nähe der Haltetemperatur, würde die
Wärmepumpe über ihre eigene Hysterese nachheizen, zu Zeiten, die miniEMS nicht geplant
hat. Bei 35 °C bleibt sie aus. Sinkt der Speicher unter das Minimum, startet miniEMS
die Schutzladung.

**Wann sehe ich eine Zapfung (z. B. Duschen)?**
Sobald der Speicher in 10 Minuten um mehr als `hw_draw_drop_k` (1,5 K) fällt, zeigt der Tab
„Wärmepumpe“ „Zapfung läuft“ unter der Boiler-Temperatur und als erste Zeile unter
„Letzte Zapfungen“. Im Ereignisprotokoll steht „hot-water draw detected“. Endgültig
gespeichert wird sie, wenn 10 Minuten lang kein neuer Tiefstwert mehr kommt. Dann folgt
„hot-water draw finished“ mit dem gesamten Abfall. Auf die niedrigere Temperatur
reagiert der Fahrplan sofort.

**Warum zeigt das Zapfprofil „lernt noch“?**
Das Profil (wann ihr typischerweise wie viel Warmwasser braucht) wird erst aus 7 Tagen
Zapf-Historie gebildet. Bis dahin rechnet der Fahrplan nur mit dem Wärmeverlust und
reagiert auf jede Zapfung, sobald sie passiert.

**Warum steht beim Fahrplan „PV-Akku“?**
Wird heute noch so viel PV erwartet, dass der Akku voll wird und Strom eingespeist werden
müsste, kostet eine kWh aus dem Akku jetzt nur die Einspeisevergütung, denn sie wird
später einfach nicht eingespeist. Dann ist der PV-Akku die günstigste Quelle.

**Kann ich miniEMS gefahrlos beenden?**
Ja. Beim Beenden und beim Umschalten von Live auf Simulation oder aus wird der Sollwert
auf das Tagesziel gesetzt. Danach regelt die Wärmepumpe wieder allein.

Technische Details: [Warmwasser-Steuerung (technisch)](../technical/warmwasser-steuerung.md).
