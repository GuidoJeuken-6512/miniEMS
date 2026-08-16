# miniEMS

Energiemanagement für einen Deye-Hybrid-Wechselrichter an einem dynamischen
Stromtarif — als Home-Assistant-Add-on mit Ingress-Oberfläche.

miniEMS liest Leistungs-, Zähler- und Preisdaten aus Home Assistant, entscheidet
daraus alle 30 Sekunden einen Betriebsmodus und setzt ihn über die
HA-Entities des Wechselrichters um. Parallel führt es eine eigene Kosten- und
Einsparungsrechnung in SQLite und stellt die Ergebnisse als HA-Sensoren bereit.

## Was es tut

**Netzdienliche PV-Nutzung.** Statt jeden Sonnenstrahl sofort in den Akku zu
schieben, kann miniEMS den Überschuss einspeisen und erst laden, wenn die
Restprognose des Tages ungefähr dem entspricht, was der Akku noch aufnehmen kann.
Das hält bis zur Mittagsspitze Kapazität frei.

**Netzladen zum Billigtarif — aber nur, wenn es sich rechnet.** Geladen wird nur,
wenn der Preis günstig ist *und* die PV den Bedarf nicht deckt *und* die Spanne
zum späteren Entladetarif die Umwandlungsverluste übersteigt. Der Entladetarif
wird dabei aus der eigenen Historie hergeleitet, gewichtet nach dem Zeitpunkt der
tatsächlichen Entladung.

**Kosten- und Einsparungsrechnung.** Netzbezug, Einspeisung, Hausverbrauch und
Netzladung werden tagesweise mit dem jeweils gültigen Preis verrechnet. Die
kWh-Größen stammen bevorzugt aus den Lebenszeit-Zählern des Wechselrichters, mit
einem Anker auf lokaler Mitternacht — dadurch überstehen sie Add-on-Neustarts und
hängen nicht an der Uhr des Wechselrichters.

**Eigene HA-Integration.** Das Add-on installiert `custom_components/miniems` und
veröffentlicht Modus, Kosten, Einsparungen und Energiemengen als reguläre
HA-Sensoren, nutzbar in Dashboards und Automationen.

## Betriebsmodi

| Modus | Bedeutung |
|---|---|
| `Idle` | Kein Eingriff |
| `PV Charging` | Akku wird aus PV-Überschuss geladen |
| `Export Surplus` | Überschuss wird eingespeist statt gespeichert (netzdienlicher Halt) |
| `Grid Charging (Cheap Rate)` | Akku wird zum Billigtarif aus dem Netz geladen |
| `Battery Protection (Min SoC)` | Entladen gesperrt, Mindestladung schützen |

Ein Moduswechsel muss durchgehend über eine einstellbare Verweildauer anstehen,
bevor er greift — außer bei sicherheitsrelevanten Übergängen, die sofort wirken.

## Installation

Repository in Home Assistant hinzufügen und das Add-on installieren:

[![Add-on-Repository in Home Assistant hinzufügen](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FGuidoJeuken-6512%2FminiEMS)

Die Oberfläche ist danach über Ingress erreichbar (Seitenleiste, Eintrag
„miniEMS"), mit den Reitern Dashboard, Einstellungen, Log, `config.json`,
`options.json` und Datenbank.

## Konfiguration

Alle Einstellungen werden in der Add-on-Oberfläche unter **Einstellungen**
gepflegt und in `/data/config.json` abgelegt; ein Migrationssystem hebt
bestehende Konfigurationen bei Updates automatisch auf das aktuelle Schema.

> [!IMPORTANT]
> Die Batteriesteuerung ist im Auslieferungszustand **aus**
> (`battery_control_enabled: false`), und der Simulationsmodus ist **an**
> (`battery_control_simulation: true`). In Simulation wird jede Stellgröße nur
> protokolliert, nichts an den Wechselrichter geschrieben. Erst prüfen, dann
> scharf schalten.

Die vollständige Referenz aller Felder steht in der Dokumentation.

## Dokumentation

- **Benutzerhandbuch, technische Referenz und Roadmaps:**
  [`docs/`](../docs/) — deutsch, mit englischer Spiegelung unter `docs/en/`
- **Kosten und Einsparungen im Detail:** [`docs/user/costs.md`](../docs/user/costs.md)
- **Berechnungen:** [`docs/technical/calculations.md`](../docs/technical/calculations.md)
- **Konfigurationsreferenz:** [`docs/user/configuration.md`](../docs/user/configuration.md)
- **Änderungen je Version:** [`CHANGELOG.md`](./CHANGELOG.md)

## Voraussetzungen

- Home Assistant mit Supervisor (Add-on-fähige Installation)
- Ein Deye-Hybrid-Wechselrichter, eingebunden über die Solarman-Integration
- Ein Sensor mit dem aktuellen Arbeitspreis (dynamischer Tarif)
- Optional: Solcast-PV-Prognose für die vorausschauenden Entscheidungen

Alle Entity-IDs sind konfigurierbar; die Standardwerte entsprechen einer
Deye-8K-Installation.

## Lizenz

Siehe [LICENSE](./LICENSE).
