---
revision_date: 2026-04-07
---

# Erste Schritte

**miniEMS** ist ein Home Assistant Add-on, das das Laden und Entladen einer Solarbatterie automatisch steuert – mithilfe von Echtzeitstrompreisen, Solcast-PV-Prognosen und historischen Verbrauchsdaten.

## Was miniEMS tut

| Funktion | Beschreibung |
|---|---|
| **Intelligentes Netzladen** | Lädt die Batterie nur dann aus dem Netz, wenn der Preis niedrig ist und Solcast bestätigt, dass die Sonne es nicht selbst erledigen wird |
| **PV-Überschussladen** | Erkennt PV-Überschuss und schaltet den Wechselrichter auf Solarladen um |
| **Batterieschutz** | Blockiert das Entladen, wenn der SoC unter den konfigurierten Mindestwert fällt |
| **Kostenverfolgung** | Verfolgt tägliche Netzkosten, PV-Einsparungen, Einspeisevergütung und Netzladekosten |
| **HA-Sensoren** | Veröffentlicht 28 berechnete Sensoren unter `sensor.miniems_*` über eine benutzerdefinierte Integration |
| **Live-Dashboard** | Ingress-Panel mit automatischer Aktualisierung, Warnungsbanner und Moduswechsel-Log |
| **Solcast-Integration** | Liest die Solcast-PV-Prognose direkt aus HA-Entitäten |
| **Vorhersage-Fallback** | Temperaturbasierter Fallback, wenn Solcast oder historische Daten nicht verfügbar sind |

## Voraussetzungen

| Voraussetzung | Hinweise |
|---|---|
| Home Assistant OS oder Supervised | Das Add-on verwendet die Supervisor-API |
| Deye-Wechselrichter + ha-solarman-Integration | Stellt Sensoren für PV-, Batterie-, Netz- und Lastleistung bereit |
| Dynamischer Strompreissensor | z.B. Tibber- oder Octopus-Energy-Integration |
| (Optional) Solcast-Integration | Für genaue PV-Prognosedaten |
| (Optional) Wetter-Integration | Für temperaturbasierte Lastvorhersage |

## Schnellstart

1. **Add-on installieren** — kopiere `miniems/` nach `/addons/local/miniems/` und installiere aus dem Add-on Store (siehe [Installation](installation.md)).
2. **Custom Integration installieren** — kopiere `integration/` nach `/config/custom_components/miniems/`, starte HA neu, dann über **Einstellungen → Integrationen → miniEMS** hinzufügen.
3. **Dashboard öffnen** — über die HA-Seitenleiste → miniEMS.
4. **Konfigurieren** — im Tab **Einstellungen** Entitäts-IDs und Schwellenwerte eintragen.
5. **Batteriesteuerung aktivieren** — zuerst den **Simulationsmodus** nutzen, um die Logik zu prüfen.
6. **Simulationsmodus deaktivieren**, wenn das Verhalten korrekt ist.

!!! tip "Simulationsmodus"
    Mit aktiviertem Simulationsmodus protokolliert miniEMS alle Wechselrichterbefehle mit dem Präfix `[SIM]`, sendet sie aber nie. Im Dashboard erscheint ein **SIM**-Badge. Teste immer zuerst im Simulationsmodus.
