---
revision_date: 2026-11-19
---

# Warmwasser: Überhöhung der Solltemperatur im Niedrigtarif – Auswertung und Plan

!!! info "Status: ausgewertet, nicht umgesetzt"
    Grundlage: Code-Stand v4.2.3 und die Daten der Anlage vom 9. bis 19.11.2026
    (10,1 Tage, so weit reicht der HA-Recorder). Ergebnis in einem Satz: Die
    Überhöhung lohnt sich bei diesem Tarif nicht. Die eigentliche Lücke war der
    Akku, der im Niedrigtarif das Warmwasser bezahlt hat, und die ist seit v4.2.3
    geschlossen.

## 1. Frage

Der Warmwasser-Planer wählt den **Zeitpunkt** einer Ladung nach Preis, COP und gelerntem
Zapfprofil. Die **Zieltemperatur** ist aber fest (`hw_morning_target_c`, hier 50 °C); höher
geht es nur bei PV-Überschuss (bis `hw_max_temp_c`) und bei negativem Preis. Lohnt es sich,
im Niedrigtarif (02–06 und 12–16 Uhr, 27,4 ct) gezielt höher zu laden, damit später im
Standard- (34,4 ct) oder Hochtarif (39,4 ct) nicht nachgeheizt werden muss?

## 2. Daten

| Quelle | Entity | Auflösung |
|---|---|---|
| Ladungen (Start, Ende) | `sensor.eu08l_boil1_operating_state` (`DHW` → `TRAILING-STOP`) | Zustandswechsel |
| Leistung der Wärmepumpe | `sensor.shelly_3em_total_power` | 5-min-Statistik |
| Netz, Akku, PV | `sensor.deye8k_grid_power`, `…_battery_power`, `…_pv_power` | 5-min-Statistik |
| Tarif | OctopusHeat 2.0: NIEDRIG 27,44 ct, STANDARD 34,44 ct, HOCH 39,44 ct | fest |

**Verfahren je Ladung:**

- Strom der Ladung: mittlere WP-Leistung × Dauer. Während einer WW-Ladung pausiert die
  Heizung, deshalb ohne Abzug einer Grundlast.
- Herkunft: Änderung von Netzbezug und Akku-Entladung gegenüber den 30 Minuten davor. Was
  beide nicht erklären, kam aus PV.
- Preis je kWh_el: Netzanteil × Tarif, Akkuanteil × 27,44 ct ÷ 0,82 = 33,5 ct (eine
  Akku-kWh muss im Niedrigtarif zurückgekauft werden, Round Trip ~82 %), PV-Anteil 0 ct
  (keine Einspeisevergütung).

## 3. Ergebnis

45 Ladungen in 10,1 Tagen, 13,8 kWh_el, also **1,36 kWh_el pro Tag** für Warmwasser.

| Tarif | Ladungen | kWh_el | Anteil | Ø ct/kWh_el |
|---|---|---|---|---|
| NIEDRIG | 34 | 10,4 | 76 % | 30,3 |
| STANDARD | 9 | 3,2 | 23 % | 20,1 |
| HOCH | 1 | 0,2 | 1 % | 33,5 |

- **Ladungen im Standard- und Hochtarif waren meist günstig.** Sie liefen am Vormittag aus
  PV oder aus dem Akku. Nur 5 Ladungen (zusammen 1,25 kWh_el) kamen überwiegend aus dem
  Netz zum Standardtarif, jeweils 0,2–0,3 kWh_el, meist morgens zwischen 06:30 und 07:00
  kurz nach dem Ende des Niedrigtarifs.
- **Obergrenze für die Überhöhung:** Hätte man genau diese 5 Ladungen in den Niedrigtarif
  verlegt, spart das 7 ct/kWh_el × 1,25 kWh_el = **8,8 ct in 10 Tagen, ~0,26 €/Monat.**
  Davon gehen noch ab:
  - der schlechtere COP bei heißerem Speicher (grob 2 % pro K, bei +5 K ~10 %),
  - der höhere Speicherverlust (proportional zu T − 20 °C, bei 55 statt 50 °C ~17 % mehr)
    über die ganze Haltezeit.

  **Netto bleibt etwa null.**
- **Die eigentliche Lücke:** 16 Ladungen im Niedrigtarif (5,1 kWh_el) liefen aus dem
  **Akku** statt aus dem Netz, z. B. am 10.11. ab 03:50, am 17.11. ab 04:00 und am 18.11.
  ab 05:30. Jede dieser kWh kostet 33,5 statt 27,4 ct, zusammen **~0,90 €/Monat**, gut das
  Dreifache der Überhöhung. Ursache waren die Planungsfehler, die in v4.2.2/v4.2.3 behoben
  sind: Der Akku entlud im Niedrigtarif, statt das Netz direkt zu nutzen. Seit v4.2.3 hält
  der Tagesplan den Akku in 02–06 Uhr. Die erste Ladung danach (19.11., 04:50 und 05:55)
  lief zu 100 % aus dem Netz.

**Unsicherheiten:**

- Nur 10 Tage, im November, mit wenig PV. Die Zahlen gelten für diese Jahreszeit.
- Die Herkunft ist aus 5-Minuten-Mitteln geschätzt. Bei gleichzeitigem Verbrauch anderer
  Geräte kann sie verrutschen.
- Bis zum 16.11. arbeitete die Akku-Steuerung mit falschen Annahmen (Ladeleistung,
  Wirkungsgrad). Ein Teil der Akku-Ladungen im Niedrigtarif ist dadurch zu erklären.

## 4. Warum die Überhöhung hier wenig bringt

Der Akku verschiebt die Energie schon, und er ist dabei der bessere Speicher:

| Speicher | Verlust beim Verschieben vom Niedrig- in den Standardtarif |
|---|---|
| Akku | Round Trip ~82 %: Eine kWh kostet 33,5 statt 34,4 ct |
| Warmwasser +5 K | COP −10 % und Speicherverlust +17 %, ohne Begrenzung durch die Kapazität |

Wird im Standardtarif aus dem Akku geheizt, kostet die Wärme fast dasselbe wie im
Niedrigtarif aus dem Netz. Der Planer selbst meldet dafür z. B. „now 6.7 ct/kWh_th, then
6.2 ct/kWh_th“. Die Überhöhung würde nur dann deutlich lohnen, wenn **der Akku leer ist**
und **im Hochtarif aus dem Netz** nachgeheizt werden müsste. Das kam im Zeitraum nicht vor.
Die einzige Ladung im Hochtarif (13.11., 19:35, 0,17 kWh_el) lief aus dem Akku.

## 5. Plan

### Schritt 1 – Beobachten statt bauen (sofort, ohne Code)

Prüfen, ob v4.2.3 die Akku-Lücke wirklich schließt: Zwei Wochen nach dem Update dieselbe
Auswertung wiederholen (Skript siehe Anhang). Erfolgskriterium: Im Niedrigtarif kommt der
Strom für Warmwasser zu mindestens 90 % aus dem Netz.

### Schritt 2 – Kennzahl einführen (klein, sinnvoll auch ohne Überhöhung)

Je Ladung Herkunft und effektiven Preis protokollieren. Das Ereignis `hw_on` nennt heute
nur den geplanten Preis. Neu: `hw_off` mit gemessener Herkunft (Netz, Akku, PV) und
tatsächlichem ct/kWh_el, dazu ein Tageswert „Warmwasser ct/kWh_el“. Damit wird Schritt 1
ein Blick aufs Dashboard statt eines Skripts, und eine spätere Überhöhung ließe sich messen.

### Schritt 3 – Überhöhung nur, wenn die Daten sie rechtfertigen

Umsetzen erst, wenn **eine** dieser Bedingungen erfüllt ist:

- Die Kennzahl aus Schritt 2 zeigt über vier Wochen mehr als 0,5 kWh_el pro Woche aus dem
  Netz im Hoch- oder Standardtarif.
- Der Tarif ändert sich (z. B. dynamischer Börsentarif mit Spannen über 15 ct).
- Der Akku ist regelmäßig vor dem nächsten günstigen Fenster leer, z. B. im Hochwinter.

Bausteine dann, in dieser Reihenfolge:

1. **COP abhängig von der Speichertemperatur lernen.** Heute lernt das Modell den COP nur
   über die Außentemperatur (`hot_water_model.py`). Ohne den Einfluss der
   Speichertemperatur sähe der Planer die Überhöhung immer zu günstig. Daten dafür liegen
   in `heat_pump_recorder` vor (Speichertemperatur je Tick).
2. **Variable Zieltemperatur je Ladung im Planer** (`hot_water_planner.py`): Kandidaten für
   das Ziel von `hw_morning_target_c` bis `hw_boost_max_c` in 2-K-Schritten. Bewertet wird
   die Kostensumme bis zum nächsten Niedrigtarif-Fenster, also Ladung jetzt plus vermiedene
   Nachladungen, mit Verlust und Zapfprofil. Die bestehende Gier über ganze Ladungen bleibt.
3. **Kopplung an den Akkuplan:** Für die Herkunft der späteren Nachladungen gilt die
   Aktion des Tagesplans (`battery_plan`) im jeweiligen Slot, nicht die eigene
   Akku-Projektion. Nur wo der Tagesplan Netzbezug vorsieht, zählt der Tarif.
4. **Grenzen:** neue Option `hw_boost_max_c`, Vorgabe 55 °C. Sie muss mindestens 5 K unter
   der WP-Grenze `number.eu08l_boil1_dhw_maximum_temperature_number` (60 °C) liegen, damit
   kein Heizstab einspringt. Ohne Option bleibt das Verhalten wie heute.
5. **Tests** mit den Ladungen dieses Zeitraums als Fixture: Mit Akku darf keine Überhöhung
   entstehen. Mit leerem Akku vor dem Hochtarif wird höher geladen, aber höchstens bis
   `hw_boost_max_c`.

Aufwand geschätzt: Baustein 1–2 je ein bis zwei Tage, 3–5 zusammen ein Tag.

## Anhang: Auswertung wiederholen

Die Auswertung lief mit einem einmaligen Skript gegen die HA-Statistik (5 Minuten) über den
MCP. Für eine Wiederholung braucht es:

1. Die Ladungen aus `sensor.eu08l_boil1_operating_state` (Wechsel `DHW` → `TRAILING-STOP`).
2. Die 5-min-Statistik von `sensor.shelly_3em_total_power`, `sensor.deye8k_grid_power`,
   `sensor.deye8k_battery_power` und `sensor.deye8k_pv_power` im selben Zeitraum.
3. Je Ladung: Energie = mittlere WP-Leistung × Dauer. Herkunft = Änderung von Netz und
   Akku-Entladung gegenüber den 30 Minuten davor. Preis wie in Abschnitt 2.

Mit Schritt 2 entfällt das Skript.
