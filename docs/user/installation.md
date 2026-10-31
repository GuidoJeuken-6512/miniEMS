---
revision_date: 2026-10-31
---

# Installation

miniEMS besteht aus zwei Teilen, die beide installiert werden müssen:

| Teil | Was es ist | Wo es läuft |
|---|---|---|
| **miniEMS Add-on** | Die EMS-Engine – liest Sensoren, trifft Entscheidungen, hostet das Dashboard | HA Add-on (Docker-Container) |
| **miniEMS Integration** | Benutzerdefinierte HA-Integration – erstellt `sensor.miniems_*`-Entitäten in HA | Home Assistant Core (`custom_components`) |

---

## 1. Add-on installieren

miniEMS wird als Home-Assistant-Add-on-Repository verteilt. Du musst nichts kopieren.

1. Öffne den Knopf **„Repository hinzufügen“**, der direkt auf deine HA-Instanz zeigt:
   [![Repository hinzufügen](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FGuidoJeuken-6512%2FminiEMS)

   Alternativ manuell: **Einstellungen → Add-ons → Add-on Store → ⋮ → Repositories**
   und `https://github.com/GuidoJeuken-6512/miniEMS` eintragen.
2. Im Add-on Store erscheint **miniEMS**. Klicke darauf und dann auf **Installieren**.
   HA lädt ein fertiges Image, es wird nichts auf deinem Gerät gebaut.
3. Klicke auf **Starten**. Das Add-on startet auf Port 8080 (Ingress).
4. Öffne das Seitenleisten-Panel — du solltest das **miniEMS-Dashboard** im Modus `Idle` sehen. Warnungen sind in dieser Phase zu erwarten.

Unterstützt werden **aarch64** (Raspberry Pi 4/5, HA Green/Yellow) und **amd64**
(Intel/AMD-PCs, VMs). 32-Bit-Systeme werden nicht unterstützt.

Updates erscheinen wie bei allen Add-ons unter **Einstellungen → Add-ons → miniEMS**,
sobald eine neue Version veröffentlicht ist.

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
