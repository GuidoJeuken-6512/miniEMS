---
revision_date: 2026-08-14
---

# miniEMS – Mini Energy Management System

**miniEMS** ist ein Home Assistant Add-on, das das Laden und Entladen einer Solarbatterie automatisch steuert – mithilfe von Echtzeitstrompreisen, Solcast-PV-Prognosen und historischen Verbrauchsdaten.

## Was miniEMS tut

| Funktion | Beschreibung |
|---|---|
| **Intelligentes Netzladen** | Lädt die Batterie nur dann aus dem Netz, wenn der Preis niedrig ist und Solcast bestätigt, dass die Sonne es nicht selbst erledigen wird |
| **Netzfreundliche PV-Strategie** | Exportiert PV-Überschuss optional ins Netz, statt die Batterie tagsüber vorzeitig zu füllen (Phase 7, siehe [Konfiguration](user/configuration.md)) |
| **Batterieschutz** | Blockiert das Entladen, wenn der SoC unter den konfigurierten Mindestwert fällt |
| **Kostenverfolgung** | Verfolgt tägliche Netzkosten, PV-Einsparungen, Einspeisevergütung und Netzladekosten |
| **HA-Sensoren** | Veröffentlicht berechnete Sensoren unter `sensor.miniems_*` über eine benutzerdefinierte Integration |
| **Live-Dashboard** | Ingress-Panel mit automatischer Aktualisierung, Warnungsbanner und Moduswechsel-Log |
| **Solcast-Integration** | Liest die Solcast-PV-Prognose direkt aus HA-Entitäten |
| **Vorhersage-Fallback** | Temperaturbasierter Fallback, wenn Solcast oder historische Daten nicht verfügbar sind |

## Schnellstart

1. **Add-on installieren** — siehe [Installation](user/installation.md).
2. **Dashboard öffnen** — über die HA-Seitenleiste → miniEMS.
3. **Konfigurieren** — im Tab **Einstellungen** Entitäts-IDs und Schwellenwerte eintragen, siehe [Konfiguration](user/configuration.md).
4. **Batteriesteuerung aktivieren** — zuerst den **Simulationsmodus** nutzen, um die Logik zu prüfen.

## Dokumentation

**Benutzerhandbuch**

- [Erste Schritte](user/index.md) — Funktionsüberblick und Voraussetzungen
- [Installation](user/installation.md)
- [Konfiguration](user/configuration.md) — alle Konfigurationswerte erklärt
- [Dashboard & Oberfläche](user/dashboard.md)
- [Ereignisprotokoll](user/log.md)
- [Datenbank](user/database.md)
- [HA-Sensoren](user/sensors.md)

**Technische Referenz**

- [Übersicht](technical/index.md) — Modulübersicht
- [Architektur](technical/architecture.md) — Komponentendiagramm, asyncio-Task-Graph, Authentifizierungsablauf
- [Berechnungen](technical/calculations.md) — alle Formeln im EMS-Loop
- [Datenspeicherung](technical/data-storage.md) — Konfigurationsdateien, SQLite-Schema, In-Memory-Zustand
- [API-Referenz](technical/api.md) — interne HTTP-Endpunkte
- [HA-Sensor-Referenz](technical/sensors.md)
- [Devcontainer-Supervisor-Patches](technical/devcontainer-supervisor-patches.md)

**English documentation** is available under [User Guide (EN)](en/user/index.md) and [Technical Reference (EN)](en/technical/index.md).

## Source Code

Das Projekt ist auf GitHub gehostet:
[github.com/GuidoJeuken-6512/miniEMS](https://github.com/GuidoJeuken-6512/miniEMS)
