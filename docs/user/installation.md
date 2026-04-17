---
revision_date: 2026-04-07
---

# Installation

miniEMS besteht aus zwei Teilen, die beide installiert werden müssen:

| Teil | Was es ist | Wo es läuft |
|---|---|---|
| **miniEMS Add-on** | Die EMS-Engine – liest Sensoren, trifft Entscheidungen, hostet das Dashboard | HA Add-on (Docker-Container) |
| **miniEMS Integration** | Benutzerdefinierte HA-Integration – erstellt `sensor.miniems_*`-Entitäten in HA | Home Assistant Core (`custom_components`) |

---

## 1. Add-on installieren

miniEMS wird als lokales Home Assistant Add-on verteilt.

1. Kopiere den Ordner `miniems/` per SSH oder Samba in das HA Add-on-Verzeichnis:
   ```
   /addons/local/miniems/
   ```
2. Gehe in HA zu **Einstellungen → Add-ons → Add-on Store → ⋮ → Nach Updates suchen**.
3. Das **miniEMS**-Add-on erscheint unter **Lokale Add-ons**.
4. Klicke auf **Installieren**.
5. Klicke auf **Starten**. Das Add-on startet auf Port 8080 (Ingress).
6. Öffne das Seitenleisten-Panel — du solltest das **miniEMS-Dashboard** im Modus `Idle` sehen. Warnungen sind in dieser Phase zu erwarten.

---

## 2. Custom Integration installieren

Die Integration erstellt alle `sensor.miniems_*`-Entitäten in Home Assistant, indem sie den `/api/status`-Endpunkt des Add-ons abfragt.

!!! info "Automatische Installation"
    Das Add-on kopiert die Integrationsdateien **automatisch** beim Start nach
    `/config/custom_components/miniems/`. Es sind keine manuellen Kopierschritte nötig.
    Stelle sicher, dass das Add-on läuft (Schritt 1), bevor du HA neu startest.

### Home Assistant neu starten

Ein **vollständiger HA-Neustart** ist erforderlich, um die neue benutzerdefinierte Komponente zu laden — nur das Add-on neu zu starten reicht nicht aus.

**Einstellungen → System → Home Assistant neu starten**

### Integration hinzufügen

1. Gehe zu **Einstellungen → Integrationen → + Integration hinzufügen**.
2. Suche nach **miniEMS** und wähle es aus.
3. Gib die **Base-URL** der Add-on-API ein:

   | Setup | URL |
   |---|---|
   | Standard (gleicher Host) | `http://homeassistant:8080` |
   | Benutzerdefiniert / extern | `http://<deine-ha-ip>:8080` |

4. Klicke auf **Senden**. Die Integration testet die Verbindung zu `/api/status`. Bei Erfolg werden das **miniEMS**-Gerät und alle 28 Sensoren sofort erstellt.

### Optionen (optional)

Nach der Einrichtung kann das Abfrageintervall angepasst werden:

**Einstellungen → Integrationen → miniEMS → Konfigurieren**

| Option | Standard | Bereich | Beschreibung |
|---|---|---|---|
| Abfrageintervall | 30 s | 10 – 300 s | Wie oft die Integration `/api/status` abruft |

---

## 3. Add-on konfigurieren

1. Öffne das miniEMS-Seitenleisten-Panel und gehe zum Tab **Einstellungen**.
2. Trage deine Entitäts-IDs (Wechselrichter, Preissensor usw.) und Schwellenwerte ein.
3. Klicke auf **Speichern & Neu starten**.

Siehe [Konfiguration](configuration.md) für die vollständige Einstellungsreferenz.

---

## Updates

### Add-on aktualisieren

1. Ersetze die Add-on-Dateien in `/addons/local/miniems/`.
2. In HA: **Einstellungen → Add-ons → miniEMS → Aktualisieren** (oder Add-on neu starten).
3. Die Konfiguration wird automatisch migriert — keine manuellen Schritte erforderlich.

### Integration aktualisieren

Das Add-on aktualisiert die Integrationsdateien **automatisch** beim Start, wenn eine neue Version erkannt wird.

1. Aktualisiere das Add-on (siehe oben) und starte es neu.
2. Starte Home Assistant neu, um die neue benutzerdefinierte Komponente zu laden.

!!! note "Konfigurationspersistenz"
    Alle Einstellungen werden in `/data/config.json` auf dem HA-Host gespeichert. Sie überleben Add-on-Updates, Neustarts und Supervisor-Neuladen.

---

## Deinstallation

1. Integration entfernen: **Einstellungen → Integrationen → miniEMS → Löschen**.
2. Add-on stoppen und entfernen über **Einstellungen → Add-ons → miniEMS → Deinstallieren**.
3. Integrationsdateien löschen: `/config/custom_components/miniems/`.
4. Um auch gespeicherte Daten zu entfernen: `/data/miniems.db` und `/data/config.json` über das HA-Terminal-Add-on löschen.
