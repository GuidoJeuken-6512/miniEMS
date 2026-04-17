---
revision_date: 2026-04-07
---

# Datenbank-Viewer

Der Tab **Datenbank** (`/database`) bietet eine direkte Ansicht der SQLite-Tabelle `daily_stats` — dieselben Daten, die das Verbrauchsvorhersagemodell und die Kosten- & Einsparungs-Dashboard-Karten antreiben.

---

## Zusammenfassungsleiste

Vier Karten oben bieten eine schnelle Übersicht:

| Karte | Beschreibung |
|---|---|
| Aufgezeichnete Tage | Gesamtzahl der Zeilen in `daily_stats` |
| Datumsbereich | Ältestes → neuestes Datum in der Tabelle |
| Tage mit Temperatur | Zeilen mit einem gültigen `avg_outdoor_temp_c`-Wert |
| Tage ohne Temperatur | Zeilen ohne Temperatur — **gelb** hervorgehoben, wenn ungleich Null |

Tage ohne Temperatur können von der temperaturabgeglichenen Lastvorhersage nicht verwendet werden. Wenn viele Zeilen ohne Temperatur vorhanden sind, prüfe, ob `weather_entity` korrekt konfiguriert und erreichbar ist.

---

## Datentabelle

Alle Zeilen aus `daily_stats` werden angezeigt, standardmäßig neueste zuerst sortiert. Klicke auf eine Spaltenüberschrift, um die Sortierrichtung umzuschalten.

| Spalte | Einheit | Beschreibung |
|---|---|---|
| Datum | — | ISO-Datum `JJJJ-MM-TT` |
| Temp (°C) | °C | Durchschnittliche Außentemperatur — farbkodiert: blau (< 0), cyan (0–10), grün (10–20), gelb (> 20). Grau wenn fehlend. |
| Last (kWh) | kWh | Gesamter Haushaltsstromverbrauch |
| PV genutzt (kWh) | kWh | Direkt vom Haus verbrauchte PV-Energie |
| Netzbezug (kWh) | kWh | Gesamt aus dem Netz bezogene Energie |
| Netzladung (kWh) | kWh | Aus dem Netz speziell für die Batterieladung bezogene Energie |
| Einspeisung (kWh) | kWh | Ins Netz exportierte Energie |
| Peak PV (W) | W | Höchste erfasste PV-Momentanleistung des Tages |
| Netzkosten (€) | € | Kosten aller Netzbezüge |
| PV-Einsparungen (€) | € | Durch PV-Eigenverbrauch vermiedene Kosten |
| Durchschn. Preis (€/kWh) | €/kWh | Tick-gewichteter durchschnittlicher Strompreis |
| Ticks | — | Anzahl aufgezeichneter EMS-Update-Zyklen (Datenqualitätsindikator — ein voller Tag bei 30-s-Intervallen = 2880 Ticks) |
| Hoher Tarif (kWh) | kWh | Verbrauch beim hohen Tarif (`price ≥ medium_rate_threshold_eur`) |
| Mittlerer Tarif (kWh) | kWh | Verbrauch beim mittleren Tarif |
| Niedriger Tarif (kWh) | kWh | Verbrauch beim niedrigen Tarif (`price < cheap_rate_threshold_eur`) |

**Grau** angezeigte Zeilen haben fehlende Temperaturdaten und werden von der temperaturabgeglichenen Vorhersage ausgeschlossen.

---

## Wie die Daten erfasst werden

Werte akkumulieren den ganzen Tag über im Speicher innerhalb von `CostOptimizer`. Um Mitternacht (erkannt wenn `date.today()` sich ändert) wird die vollständige Tageszeile in SQLite geschrieben. Die Spalte `last_flush_ts` zeichnet auf, wann der letzte Schreibvorgang stattgefunden hat.

Die Datenbank wird durch die UI nicht geändert — diese Seite ist schreibgeschützt.

---

## Zusammenhang mit der Verbrauchsvorhersage

Die temperaturbasierte Lastvorhersage (`consumption_model.py`) fragt diese Tabelle nach Tagen mit ähnlicher `avg_outdoor_temp_c` ab (±4 °C, letzte 60 Tage). Wenn mindestens 3 solche Tage existieren, wird der Median `load_total_kwh` dieser Tage als vorhergesagte Last verwendet. Tage ohne Temperatur werden von dieser Abfrage übersprungen, weshalb die grauen Zeilen wichtig sind.

Siehe [Datenspeicherung](../technical/data-storage.md) für die vollständige `daily_stats`-Schemareferenz.
