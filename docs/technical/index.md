---
revision_date: 2026-04-07
---

# Technische Referenz

Dieser Abschnitt dokumentiert das interne Design von miniEMS v1.5.3 für Entwickler und fortgeschrittene Nutzer, die verstehen möchten, wie das System aufgebaut ist.

## Modulübersicht

| Datei | Aufgabe |
|---|---|
| `main.py` | Einstiegspunkt; verbindet alle Komponenten, verwaltet asyncio-Tasks |
| `const.py` | Gemeinsame Konstanten: `EMSMode`-Enum, Schema-Version, API-URLs |
| `config_loader.py` | Lädt und führt Konfiguration zusammen; führt Migration durch; stellt `Config`-Dataclass bereit |
| `migration.py` | Schema-Versionsmigrationen für `config.json` (v0 → v10) |
| `ha_ws_client.py` | Ruft HA-Entity-Zustände per REST ab; verwaltet Token-Fallback |
| `ems_controller.py` | Bestimmt den Betriebsmodus; verbindet alle Subsysteme pro Tick |
| `cost_optimizer.py` | Akkumuliert täglich/wöchentlich Energiekosten und Einsparungen; erkennt Ausfallzeiten |
| `consumption_model.py` | Sagt Last aus dem Verlauf vorher; temperaturbasierter Fallback |
| `battery_model.py` | Berechnet freie Ladekapazität und nutzbare kWh aus dem SoC |
| `sensor_validator.py` | Spike-Erkennung — lehnt unplausible Leistungswerte ab |
| `solcast_client.py` | Liest Solcast-PV-Vorhersage-Entities aus dem HA-State-Cache |
| `event_log.py` | Ringpuffer (100 Einträge) für Moduszwechsel-Ereignisse |
| `weather_client.py` | Ruft `weather.get_forecasts` HA-Action auf; 30-Minuten-Cache |
| `store.py` | SQLite-Persistenz für tägliche Energiehistorie (`/data/miniems.db`) |
| `inverter_controller.py` | Schreibt Lade-/Entladesteuerung an Wechselrichter-Entities |
| `mqtt_publisher.py` | Veröffentlicht Sensoren per MQTT Discovery |
| `ha_sensor_publisher.py` | Schreibt Sensor-Zustände an die HA-Core-REST-API (Fallback) |
| `web_server.py` | FastAPI; liefert Dashboard, Einstellungen, Log und `/api/status` |
| `templates/dashboard.html` | Dashboard-Jinja2-Template mit JavaScript-Auto-Refresh |
| `templates/settings.html` | Einstellungsformular-Jinja2-Template |
| `templates/log.html` | Vollständiges Moduszwechsel-Log-Jinja2-Template |
| `translations/en.yaml` | Englische UI-Texte |
| `translations/de.yaml` | Deutsche UI-Texte |
| `static/style.css` | Dashboard-CSS |

## Wo anfangen

- [Architektur](architecture.md) — Komponentendiagramm, asyncio-Task-Graph, Authentifizierungsablauf
- [Berechnungen](calculations.md) — alle Formeln im EMS-Loop
- [Datenspeicherung](data-storage.md) — Konfigurationsdateien, SQLite-Schema, In-Memory-Zustand
- [API-Referenz](api.md) — interne HTTP-Endpunkte
- [HA-Sensor-Referenz](sensors.md) — alle 28 nativen Home-Assistant-Sensoren
