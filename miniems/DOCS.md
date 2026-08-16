# miniEMS

Energiemanagement für einen Deye-Hybrid-Wechselrichter an einem dynamischen
Stromtarif.

Diese Seite führt durch Einrichtung und Betrieb. Die vollständige technische
Referenz — Berechnungswege, Sensorliste, Architektur — steht in der
Projektdokumentation unter [`docs/`](https://github.com/GuidoJeuken-6512/miniEMS/tree/main/docs)
(deutsch, englische Spiegelung unter `docs/en/`).

## Was das Add-on tut

miniEMS liest Leistungs-, Zähler- und Preisdaten aus Home Assistant, entscheidet
daraus alle 30 Sekunden einen Betriebsmodus und setzt ihn über die HA-Entities
des Wechselrichters um. Parallel führt es eine eigene Kosten- und
Einsparungsrechnung und stellt rund 100 Werte als HA-Sensoren bereit.

Die drei Entscheidungen, die es trifft:

- **PV-Überschuss speichern oder einspeisen?** Statt jeden Sonnenstrahl sofort in
  den Akku zu schieben, kann miniEMS zurückhalten und erst laden, wenn die
  Restprognose des Tages ungefähr dem entspricht, was der Akku noch aufnimmt. Das
  hält bis zur Mittagsspitze Kapazität frei.
- **Aus dem Netz nachladen?** Nur wenn der Preis günstig ist, die PV den Bedarf
  nicht deckt *und* die Spanne zum späteren Entladetarif die Umwandlungsverluste
  übersteigt.
- **Entladen sperren?** Unterhalb des eingestellten Mindest-SoC.

## Voraussetzungen

- Ein Deye-Hybrid-Wechselrichter, eingebunden über die **Solarman**-Integration
- Ein Sensor mit dem aktuellen Arbeitspreis in €/kWh
- Empfohlen: **Solcast**-PV-Prognose für die vorausschauenden Entscheidungen

Alle Entity-IDs sind konfigurierbar. Die Standardwerte entsprechen einer
Deye-8K-Installation (`sensor.deye8k_*`).

## Einrichtung

### 1. Oberfläche öffnen

Nach der Installation erscheint miniEMS in der Seitenleiste. Die Oberfläche hat
die Reiter **Dashboard**, **Einstellungen**, **Log**, **config.json**,
**options.json** und **Datenbank**.

### 2. Entities zuordnen

Unter **Einstellungen** die Abschnitte durchgehen. Wichtig sind:

| Abschnitt | Inhalt |
|---|---|
| Inverter Entities | Leistungssensoren, Tages- und Lebenszeit-Zähler |
| Battery Settings | Kapazität, minimaler und maximaler SoC |
| Octopus Energy | Preis-Entity und Tarifschwellen |
| Battery Control | Stellglieder des Wechselrichters (Ströme, Netzlade-Schalter) |
| Solcast PV Forecast | Prognose-Entities |

Das Dashboard zeigt oben ein Warnband, wenn eine Entity fehlt, nicht verfügbar
oder veraltet ist. Es sollte leer sein, bevor es weitergeht.

### 3. Erst beobachten, dann steuern

> [!IMPORTANT]
> Im Auslieferungszustand ist die Batteriesteuerung **aus**
> (`battery_control_enabled: false`) und der Simulationsmodus **an**
> (`battery_control_simulation: true`). In Simulation wird jede Stellgröße nur
> protokolliert, nichts an den Wechselrichter geschrieben.

Empfohlenes Vorgehen: Steuerung einschalten, Simulation zunächst **an** lassen
und im Log verfolgen, welche Modi miniEMS über ein bis zwei Tage wählen würde.
Erst wenn das plausibel aussieht, die Simulation abschalten.

## Betriebsmodi

| Modus | Was gesetzt wird |
|---|---|
| `Idle` | Kein Eingriff |
| `PV Charging` | Laden mit vollem Strom, Netzladen aus |
| `Export Surplus` | Ladestrom auf 0 — der Überschuss geht ins Netz statt in den Akku |
| `Grid Charging (Cheap Rate)` | Netzlade-Schalter an, Laden mit vollem Strom |
| `Battery Protection (Min SoC)` | Entladestrom auf 0 |

Ein Moduswechsel muss durchgehend über die eingestellte Verweildauer
(`mode_dwell_sec`, Standard 300 s) anstehen, bevor er greift. In der Tick-Zeile
des Logs ist ein laufender Wechsel als `→ Export Surplus pending 120/300s`
sichtbar. Sicherheitsrelevante Übergänge — etwa Unterschreiten des Mindest-SoC —
wirken sofort.

## Meldungen im Warnband

| Meldung | Bedeutung |
|---|---|
| `Config missing: … entity not set` | Pflichtfeld in den Einstellungen leer |
| `Sensor unavailable: …` | Entity liefert `unavailable`/`unknown` — meist Verbindungsverlust zum Wechselrichter |
| `Sensor stale: … last update X h ago` | Wert seit zu langer Zeit unverändert |
| `Sensor stale: … no update today` | Tagesprognose wurde heute nicht geschrieben — Solcast-Integration steht |
| `Solcast data stale: last successful API fetch X h ago` | Die Prognose kommt aus dem Cache, der Abruf schlägt fehl. Die Sensoren sehen dabei gesund aus, die Zahlen sind aber alt |
| `Inverter control: N failed write(s)` | Home Assistant hat den Schreibbefehl abgelehnt |
| `Inverter control: N unconfirmed write(s)` | Befehl abgesetzt, der Wechselrichter meldet den neuen Wert noch nicht zurück. Wird jeden Tick erneut versucht |
| `Data gap detected: …` | Das Add-on stand länger als zwei Zyklen; Energie aus dieser Zeit fehlt in der Tick-Rechnung |

Unbestätigte Schreibvorgänge sind kurzzeitig normal: Solarman gibt einen
geschriebenen Wert erst mit dem nächsten Poll zurück.

## Daten und Persistenz

Alles liegt im Add-on-Verzeichnis `/data`:

| Datei | Inhalt |
|---|---|
| `config.json` | Die Konfiguration. Wird bei Updates automatisch auf das aktuelle Schema gehoben |
| `miniems.db` | SQLite mit `daily_stats` (ein Datensatz je Tag) und `event_log` (Moduswechsel, Preiswechsel) |

Beides ist über die Reiter **config.json** und **Datenbank** einsehbar, ohne auf
das Dateisystem zugreifen zu müssen.

Die kWh-Größen stammen bevorzugt aus den **Lebenszeit-Zählern** des
Wechselrichters, verankert auf lokaler Mitternacht. Dadurch überstehen sie
Add-on-Neustarts und hängen nicht an der Uhr des Wechselrichters, die ihre
Tageszähler typischerweise einige Minuten nach Mitternacht zurücksetzt.

## Home-Assistant-Sensoren

Das Add-on installiert die Integration `custom_components/miniems` und
veröffentlicht Modus, Kosten, Einsparungen und Energiemengen als reguläre
Sensoren im Namensraum `sensor.miniems_*` — nutzbar in Dashboards, im
Energie-Dashboard und in Automationen.

Die vollständige Liste steht in der
[HA-Sensor-Referenz](https://github.com/GuidoJeuken-6512/miniEMS/blob/main/docs/technical/sensors.md).

## Weiterführend

- [Kosten & Einsparungen](https://github.com/GuidoJeuken-6512/miniEMS/blob/main/docs/user/costs.md) — jeder Wert mit Rechenweg
- [Berechnungen](https://github.com/GuidoJeuken-6512/miniEMS/blob/main/docs/technical/calculations.md) — die Formeln dahinter
- [Konfigurationsreferenz](https://github.com/GuidoJeuken-6512/miniEMS/blob/main/docs/user/configuration.md) — alle Felder
- [CHANGELOG](https://github.com/GuidoJeuken-6512/miniEMS/blob/main/miniems/CHANGELOG.md) — Änderungen je Version
