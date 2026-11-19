---
revision_date: 2026-11-19
---

# Heizkreis: Steuerung über den Vorlauf-Offset – erster Plan

!!! info "Status: Plan, nicht umgesetzt"
    Grundlage: Code-Stand v4.2.3 und die Daten der Anlage vom 9. bis 19.11.2026 (über
    den HA-MCP). Der Plan baut auf B6 und B7 aus dem
    [Gesamtplan](gesamtplan.md#b6-heizung-stufe-1-regeln-uber-den-vorlauf-offset) auf.
    Deren Code ist vorhanden (`heating_controller.py`, `heating_model.py`), aber
    ausgeschaltet (`heating_control_enabled: false`).

## 1. Ziel

Die Heizung soll Wärme dann erzeugen, wenn sie günstig ist: im Niedrigtarif, aus PV oder
aus einem vollen Akku. In teuren Stunden soll sie weniger heizen, ohne dass der Raum das
Komfortband verlässt. Vorhersehbare Sonnengewinne sollen eingeplant werden, damit die
Heizung vormittags nicht unnötig vorheizt.

**Einzige Stellgröße:** `number.eu08l_hc1_flow_line_offset_temperature` (Vorlauf-Offset,
−10 … +10 K). Die Anlage hat genau einen Heizkreis (HC1), aber der Plan wird für mehrere
Heizkreise ausgelegt (eine Offset-Entity und ein Raumfühler je Kreis).

## 2. Warum sich das lohnt

| Größe (Nov. 2026) | Wert | Quelle |
|---|---|---|
| Heizstrom | ~13 kWh_el/Tag (249,6 kWh in 19 Tagen) | `sensor.eu08l_hp1_heating_energy_monthly` |
| Heizwärme | ~61 kWh_th/Tag (1158 kWh) | `sensor.eu08l_hp1_heating_thermal_energy_monthly` |
| COP Heizen | 4,6 im Monat, 5,4 heute | `…_heating_cop_monthly`, `…_daily` |
| Warmwasser zum Vergleich | ~1,4 kWh_el/Tag | [Warmwasser-Auswertung](warmwasser-ueberhoehung.md) |

Die Heizung macht also fast den gesamten Hausverbrauch aus (~14,5 kWh/Tag). Das Haus speichert
Wärme gut. In Phasen ohne Heizung sank die Raumtemperatur um ~0,2 K pro Stunde, z. B. am
12./13.11. von 23,3 auf 21,1 °C in 11 h. Daraus folgt grob geschätzt:

- **Wärmeverlust:** H ≈ 2,5 kW ÷ 18 K ≈ 0,14 kW/K
- **Zeitkonstante:** τ ≈ 90 h
- **Wärmekapazität:** C = H · τ ≈ 12 kWh_th/K. **1 K Raumtemperatur speichert etwa so viel
  wie 2,7 kWh Akkustrom** (bei COP 4,6).

Wärme in den Raum zu verschieben, konkurriert dabei direkt mit dem Akku:

| Speicher | Verlust beim Verschieben vom Niedrig- in den Hochtarif |
|---|---|
| Akku | Round Trip ~82 %: Eine kWh kostet 27,4 ÷ 0,82 = 33,4 ct |
| Haus +1 K | höherer Vorlauf, COP −2,5 %/K: 27,4 ÷ 0,975 = 28,1 ct, dazu ~5 % mehr Wärmeverlust |

Bei kleinen Offsets ist das Haus der bessere Speicher. Der Akku bleibt dann frei für den Rest
des Hauses. Im Hochwinter, wenn die Heizung 25 kWh_el/Tag und mehr braucht, reicht der Akku
(20,7 kWh nutzbar) nicht mehr aus. Dann wird die Verschiebung über das Haus wichtig. Eine
grobe Abschätzung des Nutzens steht in Abschnitt 8.

## 3. Befunde aus den Daten (9.–19.11.)

1. **Die Lambda regelt selbst schon nach Raumtemperatur.** Der Raumeinfluss ist aktiv
   (`number.eu08l_hc1_room_thermostat_factor = 1.0`, Raum-Soll 23,5 °C). Nach sonnigen Tagen
   ging der Heizkreis stundenlang auf `STBY`: am 9.11. von 12:36 bis 21:03, am 10.11. ab 11:32
   bis 01:26 des Folgetags. Seit dem 14.11. (bewölkt, kälter) heizt er durch.
   **Folge:** miniEMS regelt nicht allein. Jede Absenkung über den Offset gleicht die Lambda
   teilweise wieder aus, sobald der Raum unter das Soll fällt. Die Wirkung eines Offsets muss
   deshalb **gemessen** werden, nicht angenommen.
2. **Die Sonne wirkt deutlich und vorhersagbar.** An sonnigen Tagen (PV 3–4 kW mittags am
   9., 10., 12., 13. und 14.11.) stieg der Raum mittags um 1–1,5 K. An bewölkten Tagen gab es
   keinen Anstieg.
3. **Der Raumfühler hat Ausreißer.** An sonnigen Tagen gegen 15 Uhr zeigt das Stundenmaximum
   26,3–26,9 °C bei einem Mittel von ~24,4 °C. Vermutlich trifft die Sonne den Fühler direkt.
   Solche Spitzen dürfen nicht in das Modell eingehen.
4. **Der Fühler löst nur in 0,2-K-Stufen auf.** Kurzfristige Raten (K/h) sind entsprechend
   unscharf. Lernen über mehrere Stunden.
5. **Der Offset steht heute manuell auf −2 K** (seit 17.11., 11:56). Wird B6 eingeschaltet,
   schreibt miniEMS beim Start und beim Beenden 0 K. Das würde diese Einstellung
   überschreiben. Siehe Abschnitt 6.1.
6. **Für Messung und Schutz ist alles da:**
   - Heizleistung: `sensor.eu08l_hp1_actual_heating_capacity`, `…_heating_thermal_energy_hourly`
   - Strom: `sensor.eu08l_hp1_inverter_power_consumption` und Shelly 3EM
   - Effizienz: COP live (`sensor.eu08l_hp1_cop`) und je Stunde
   - Temperaturen: Vorlauf (Kreis und WP), Rücklauf, Heizkurve (`…_heating_curve_flow_line_temperature_calc`)
   - Schutz: Laufzeit des Heizstabs (`sensor.eu08l_hp1_heating_rod_runtime_daily`, bisher 0 h),
     Verdichterstarts (`…_compressor_start_cycling_daily`), 2. Heizstufe

## 4. Modell

Ein Wärmeknoten pro Heizkreis, gelernt in Stunden-Schritten (eine Erweiterung von
`heating_model.py`):

```text
ΔT_raum = ( Q_heiz − H · (T_raum − T_außen) + g · Sonne ) / C       [K/h]
Q_heiz  = gemessene Heizleistung (kW_th, Lambda)                    nicht mehr nur „Anteil Heizen“
Sonne   = PV-Leistung als Einstrahlungs-Ersatz, um 0–2 h verschoben (Verschiebung wird gelernt)
```

- **Gelernt:** H/C (Auskühlen), 1/C (Wirkung der Heizwärme) und g/C (Sonnengewinn), mit
  Kleinsten Quadraten über die letzten 14–28 Tage. Physikalische Vorzeichen sind Pflicht
  (wie heute in `heating_model.py`).
- **Unterschied zum heutigen B7-Modell:**
  - Statt „Anteil der Stunde geheizt“ geht die **gemessene Heizleistung** ein. Die Anlage heizt
    oft durch und moduliert; Stunden mit Heizanteil ≤ 0,1 wären im Winter selten, und das
    Auskühl-Modell lernte dann nie.
  - Ein Modell für alle Stunden, keine getrennten Auskühl- und Aufheizraten.
- **Wirkung des Offsets:** Ein zweites, kleines Modell bildet ab, wie sich der Offset auf die
  Heizleistung auswirkt:
  `Q_heiz = f(Heizkurve + Offset, T_außen, Raumabweichung)`. Das ist nötig, weil die
  Lambda über den Raumeinfluss gegensteuert (Befund 1). Gemessen wird es mit geplanten
  Sprungversuchen (Abschnitt 7, Phase 2).
- **COP:** `COP = a + b · T_außen − c · T_vorlauf` aus dem Stunden-COP der Lambda. So lässt
  sich der COP-Verlust je K Offset gegen das Preisgefälle abwägen. Heute ist er als
  `heating_cop_drop_pct_per_k = 2.5` fest eingestellt.
- **Daten säubern:** Raumfühler-Spitzen (> 1 K über dem gleitenden Median), Abtau- und
  Warmwasser-Stunden werden verworfen. Warmwasser-Stunden fehlen dem Heizkreis, sie sind
  aber in `heat_pump_recorder` bekannt.
- **Prognose:** Die Sonne kommt aus Solcast (dieselben Slots wie für den Akku), die
  Außentemperatur aus `weather_client`.

## 5. Kosten der Wärme je Slot

Wie beim Warmwasser (`hot_water_planner._slot_price`), aber gekoppelt an den **Tagesplan
des Akkus**:

| Herkunft im Slot | Preis je kWh_el |
|---|---|
| PV-Überschuss, Akku voll | Einspeisevergütung (hier 0) |
| PV-Überschuss, Akku nicht voll | Nachladepreis des Akkus |
| Akku entlädt laut Tagesplan | Nachladepreis (27,4 ct ÷ η) |
| Tagesplan hält oder lädt den Akku | Netztarif des Slots |

Die Kosten je kWh Wärme sind dann Preis ÷ COP(T_außen, Vorlauf). Die Mehrlast durch
Vorheizen geht als **geplante Last** zurück in den Akku-Tagesplan, wie heute Warmwasser und
E-Auto (`_with_planned_loads`). Sonst plant der Akku mit dem falschen Verbrauch.

## 6. Regelung

### 6.1 Grundsatz und Grenzen

- **Basis-Offset statt 0 K:** Die Einstellung des Nutzers (heute −2 K) ist der neutrale
  Wert. miniEMS steuert **relativ** dazu und schreibt beim Start, beim Beenden und bei der
  Übergabe den Basis-Offset zurück, nicht 0 K. Neue Option `heating_base_offset_k`; ohne
  Angabe gilt der beim Einschalten gelesene Wert.
- **Komfortband je Heizkreis:** `[T_min, T_max]` um das Raum-Soll der Lambda, z. B.
  Soll −0,8 K bis Soll +1,0 K. Die Grenzen sind einstellbar. `T_min` ist eine harte Grenze.
- **Kleine Schritte:** Offset zwischen −3 und +3 K relativ zur Basis, Änderungen in 1-K-Schritten.
  Die bestehenden Sperrzeiten bleiben (60 min nach oben, 45 min nach unten), ebenso
  die Grenzen bei Kälte und der Heizstab-Schutz aus B6, der neu über die Laufzeit des
  Heizstabs ausgelöst wird.

### 6.2 Planer (Fortsetzung von B7)

Horizont 24–36 h in 30-min-Slots, dieselben Slots wie beim Akku-Tagesplan:

1. **Grundlauf:** Raumtemperatur ohne Eingriff (Offset = Basis) simulieren, mit
   Außentemperatur- und Sonnenprognose.
2. **Sonne vorwegnehmen:** Erreicht der Raum mittags `T_max` durch Sonne, wird vormittags
   abgesenkt, so weit `T_min` es erlaubt. Die Sonne heizt dann statt der Wärmepumpe. Das ist
   die sichere Ersparnis: Die Wärme wird gar nicht erst gekauft.
3. **Verschieben nach Preis:** Für jeden teuren Slot (Wärmekosten über dem Minimum des
   Horizonts plus Schwelle) prüfen, ob „vorher günstig anheben + jetzt absenken“ billiger
   ist. Gerechnet wird mit COP-Verlust und zusätzlichem Hausverlust. Gierig wie beim
   Warmwasser: die teuerste Stunde zuerst, nach jeder Entscheidung neu simulieren.
4. **Prüfen:** Jeder Plan muss im ganzen Horizont innerhalb von `[T_min, T_max]` bleiben.
   Was das verletzt, wird zurückgenommen.
5. Ausgeführt wird **nur Slot 0**. Neu gerechnet wird alle 15 min (Grundsatz 1 des
   Gesamtplans).

### 6.3 Zusammenspiel

- **Warmwasser:** Vorheizen startet den Verdichter. Das ist ein günstiger Zeitpunkt für eine
  Warmwasserladung (B6). Eine Absenkung darf keinen eigenen Verdichterstart für Warmwasser
  auslösen.
- **Akku:** Der Tagesplan bekommt die geplante Heizlast und entscheidet weiter allein über
  den Akku. Der Heizkreis-Planer liest die Akku-Aktionen nur, um die Herkunft der Energie zu
  bestimmen (Abschnitt 5). Damit gibt es keine Rückkopplung im selben Takt.
- **Lambda:** Raumeinfluss, Eco-Absenkung und Heizgrenze bleiben in der Lambda. miniEMS
  verstellt nur den Offset.

## 7. Phasen

| Phase | Inhalt | Ergebnis, Bedingung für die nächste Phase |
|---|---|---|
| 0 – Aufzeichnen | Stundenwerte je Heizkreis erweitern: Heizleistung th., Strom, COP, Vorlauf ist/Kurve, Offset, Raum (gesäubert), PV, T_außen, Betriebsstatus | 14 Tage Daten. Läuft auch bei ausgeschalteter Steuerung |
| 1 – Modell und Rückblick | Modell aus Abschnitt 4 fitten, auf der Historie nachrechnen (abwechselnde Stunden wie heute) | Mittlerer Fehler < 0,15 K über 6 h Prognose |
| 2 – Sprungversuche | An 3–5 milden Tagen geplant ±2 K für 3 h, im Niedrigtarif, mit Zustimmung des Nutzers | Wirkung des Offsets auf Heizleistung und Raum bekannt, Gegensteuern der Lambda beziffert |
| 3 – Schatten | Planer rechnet und zeigt Plan und Begründung (Timeline-Zeile „Heizung“), steuert nicht | 2 Wochen: berechnete Ersparnis gegen eine reaktive Basis, kein Bruch von `T_min` in der Simulation |
| 4 – Live, vorsichtig | ±1 K, nur Sonnen-Vorwegnahme und Hochtarif-Absenkung | Komfortband gehalten, Verdichterstarts nicht über +20 %, Heizstab 0 h |
| 5 – Live, voll | ±3 K, Preisverschiebung über den Akku hinaus | – |

## 8. Nutzen (grobe Abschätzung, zu prüfen in Phase 3)

- **Sonnen-Vorwegnahme:** Im Zeitraum gab es 5 sonnige Tage. An ihnen schaltete die
  Lambda erst ab, als der Raum schon über dem Soll lag, und hatte vormittags noch voll
  geheizt. Wird vormittags 1–2 h abgesenkt, spart das geschätzt 2–4 kWh_th pro Sonnentag,
  also ~0,5–1 kWh_el (~0,15–0,30 €).
- **Preisverschiebung bei vollem Akku:** Der Akku verschiebt schon. Der Vorteil des Hauses ist
  nur der kleinere Verlust (~5 ct/kWh_el, Abschnitt 2), etwa 1–2 €/Monat.
- **Hochwinter** (Heizung > Akku): Jede kWh_el, die aus dem Hochtarif in den Niedrigtarif
  wandert, spart ~11 ct. Bei 5 kWh_el/Tag sind das ~15 €/Monat.

Insgesamt ist der Nutzen im Hochwinter deutlich, im November mäßig. Die Zahlen sind
Schätzungen und gehören in Phase 3 auf den Prüfstand.

## 9. Risiken

| Risiko | Gegenmittel |
|---|---|
| Lambda und miniEMS regeln gegeneinander (Raumeinfluss) | Wirkung des Offsets messen (Phase 2), Planer rechnet mit der gemessenen Wirkung |
| Komfortverlust | `T_min` hart, Kältealarm aus B7 (30 min), kleine Schritte |
| Verdichter taktet | Sperrzeiten, Wechselkosten im Planer, Starts als Kennzahl |
| Heizstab holt die Absenkung teuer nach | Laufzeit des Heizstabs als Schutz: ab > 0 h am Tag keine Absenkung mehr |
| Sonnenprognose falsch | Absenkung nur, solange `T_min` mit der pessimistischen Sonne (estimate10) hält |
| Raumfühler in der Sonne | Spitzen verwerfen, später ein zweiter Fühler im Raum (offen) |

## 10. Offene Fragen an den Nutzer

1. Ist der Lambda-Raumfühler (`sensor.eu08l_hc1_room_device_temperature`) repräsentativ, oder
   gibt es einen besseren Raumfühler in HA?
2. Komfortband: Welche Unter- und Obergrenze sind in Ordnung, und gelten sie auch nachts?
3. Soll der Raumeinfluss der Lambda (Faktor 1,0) bleiben? Ohne ihn wäre die Wirkung des
   Offsets eindeutiger, aber die Lambda hätte keine eigene Sicherung mehr.
4. Sind Sprungversuche (Phase 2) mit ±2 K für einige Stunden an milden Tagen in Ordnung?
5. Warum steht der Offset auf −2 K, und soll das die Basis bleiben?
