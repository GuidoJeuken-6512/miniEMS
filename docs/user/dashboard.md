---
revision_date: 2026-04-07
---

# Dashboard & UI

Das miniEMS-Dashboard ist ein Ingress-Panel, das direkt über die HA-Seitenleiste zugänglich ist. Es aktualisiert sich alle 5 Sekunden automatisch und ist vollständig übersetzt (Deutsch/Englisch, automatisch aus HA erkannt).

## Navigation

Sechs Tabs stehen zur Verfügung:

| Tab | Pfad | Zweck |
|---|---|---|
| **Dashboard** | `/` | Live-Status, Kosten, Batterie, Solcast, Vorhersage |
| **Einstellungen** | `/settings` | Konfigurationsformular |
| **Log** | `/log` | Einheitliches Ereignislog (Moduswechsel + Preisänderungen) |
| **config.json** | `/config-json` | Raw-Editor für `/data/config.json` |
| **options.json** | `/options-json` | Raw-Editor für `/data/options.json` |
| **Datenbank** | `/database` | Alle Zeilen in `daily_stats` durchsuchen |

---

## Dashboard-Tab

### Warnungsbanner

Wenn ein erforderlicher Sensor nicht verfügbar oder ein Konfigurationsfeld fehlt, erscheint oben ein gelbes Banner mit einer Liste aller Probleme. Behebe die Probleme in den Einstellungen — das Banner verschwindet automatisch bei der nächsten Aktualisierung.

Hier erscheint auch eine Datenlücken-Warnung, wenn miniEMS erkennt, dass das Add-on länger als zwei Update-Intervalle ausgefallen war (Energiebilanzlücke).

### Modus-Badge

Zeigt den aktuellen EMS-Betriebsmodus:

| Badge | Bedeutung |
|---|---|
| `Idle` (grau) | Normalbetrieb |
| `PV Charging` (grün) | Batterie lädt durch PV-Überschuss |
| `Grid Charging (Cheap Rate)` (blau) | Batterie lädt aus günstigem Netzstrom |
| `Battery Protection (Min SoC)` (rot) | SoC unter Minimum — Entladen blockiert |

Ein **SIM**-Badge erscheint, wenn der Simulationsmodus aktiv ist.

### Live-Leistungsraster

Sechs Karten mit Echtzeitwerten: PV-Leistung, Lastleistung, Netzleistung, Batterie-SoC, Batterieleistung und Strompreis. Die Preiskarte wird grün hervorgehoben, wenn der Günstigschwellenwert erreicht ist.

### Kosten & Einsparungen

> Ausführliche Erklärung jedes Werts inkl. Rechenbeispiel: [Kosten & Einsparungen](costs.md).

Tages- und Wochenkumulierte Werte:

- **Heute gespart (PV)** — eingesparter Betrag durch Nutzung von Solarstrom statt Netzkauf
- **Netzkosten heute** — tatsächliche Kosten des bezogenen Netzstroms
- **PV genutzt heute** — kWh verbrauchte PV-Energie im Haus
- **Netzbezug heute** — gesamter Netzbezug in kWh

### Kostendetails

| Karte | Formel |
|---|---|
| Kosten ohne Netzladung | `netzkosten_heute − netzladekosten_heute` |
| Kosten zum Festpreis | `last_gesamt_kwh × festpreis` |
| Einspeisung heute | kWh ins Netz exportiert |
| Einspeisevergütung | `einspeisung_kwh × einspeisevergütung` |
| Netzladung heute | kWh aus dem Netz für die Batterie geladen (nicht aus PV) |

### Batteriezustand

Freie Ladekapazität und nutzbare kWh, berechnet aus SoC und konfigurierter Kapazität sowie SoC-Grenzen.

### Solcast PV-Prognose

Zeigt die verbleibende erwartete PV für heute, die Tagessumme und die morgige Gesamtmenge aus Solcast. Nur sichtbar, wenn Solcast-Entitäten konfiguriert sind.

### Prognose & Vorhersage

Ausgabe des internen Verbrauchsmodells. Zeigt die vorhergesagte Last und die Fallback-PV-Ertragsschätzung. Das Badge gibt die Datenquelle an:

- **historically estimated** — basierend auf temperaturabgeglichenen historischen Tagen
- **fallback estimation** — basierend auf Temperaturregeln (noch nicht genug Historie)

### Moduswechsel-Log (kompakt)

Zeigt den letzten Moduswechsel mit einem Link zur vollständigen Log-Seite.

---

## Log-Tab

Die dedizierte Log-Seite zeigt:

- **Zusammenfassungsleiste** — aktueller Modus, SoC, freie Ladekapazität in kWh, Solcast-Rest, aktueller Preis
- **Vollständige Ereignistabelle** — bis zu den letzten 100 Ereignissen: Netzlade-Moduswechsel und Strompreisänderungen

Siehe [Ereignislog](log.md) für vollständige Details zu allen Ereignistypen und Spalten.

---

## config.json-Tab

Direkt-Raw-Editor für `/data/config.json` — die persistente miniEMS-Einstellungsdatei. Zeigt die Datei als hübsch formatiertes JSON in einem Monospace-Textbereich an. Änderungen werden client-seitig validiert, bevor sie gespeichert werden, danach startet das Add-on automatisch neu.

Ein **JSON neu formatieren**-Button formatiert den Inhalt ohne Speichern neu — nützlich zum Überprüfen von Änderungen.

!!! warning "Fortgeschrittene Nutzung"
    Bevorzuge für die normale Konfiguration den Tab **Einstellungen**. Der Raw-Editor ist für Debugging, Migrationskorrekturen oder das Setzen von Werten gedacht, die nicht im Einstellungsformular angezeigt werden.

---

## options.json-Tab

Raw-Editor für `/data/options.json` — die vom HA Supervisor geschriebene Datei. Identische Oberfläche wie beim config.json-Tab, mit einem zusätzlichen Warnungsbanner, das darauf hinweist, dass der Supervisor diese Datei überschreiben kann, wenn das Add-on über die HA-UI neu konfiguriert wird.

!!! tip "Welche Datei hat Vorrang?"
    `config.json` hat Vorrang für alle Werte, die dem Dataclass-Standard entsprechen. Für alle anderen Werte gewinnt `options.json` beim Start. Nach dem ersten Durchlauf wird das zusammengeführte Ergebnis zurück in `config.json` geschrieben, sodass `config.json` die dauerhafte Wahrheitsquelle ist.

---

## Datenbank-Tab

Zeigt alle Zeilen in der SQLite-Tabelle `daily_stats`. Zeigt eine Zusammenfassungsleiste (Anzahl aufgezeichneter Tage, Datumsbereich, Temperaturabdeckung) gefolgt von einer sortierbaren vollständigen Tabelle.

Klicke auf eine Spaltenüberschrift zum Auf- oder Abwärtssortieren. Zeilen ohne Temperaturdaten werden grau angezeigt — diese Tage werden von der temperaturabgeglichenen Lastvorhersage ausgeschlossen.

Siehe [Datenspeicherung](../technical/data-storage.md) für die vollständige Spaltenreferenz.

---

## Einstellungen-Tab

Die Einstellungsseite bietet ein Formular für alle Konfigurationsoptionen. Nach dem Bearbeiten auf **Speichern & Neu starten** klicken — das Add-on startet neu und übernimmt die neue Konfiguration automatisch.

!!! note "Neustartzeit"
    Das Add-on startet typischerweise innerhalb von 2–5 Sekunden neu. Der Browser leitet nach 6 Sekunden automatisch zurück zum Dashboard.
