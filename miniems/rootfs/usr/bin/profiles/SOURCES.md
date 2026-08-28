# Herkunft der Profile

Diese Profile sind **eigene** miniEMS-Dateien, nicht zur Laufzeit aus einer fremden
Integration gelesen (siehe `docs/roadmap/v3.0-geraeteprofile.md`, Warnkasten). Wo ein
Profil offline von einer Solarman-Gerätedefinition abgeleitet wurde, steht das im Profil
selbst (`derived_from:`) und hier.

## Solarman (`custom_components/solarman`)

- Lizenz: **MIT**, © 2024 David Rapan und Mitwirkende (`license`-Datei im installierten
  Paket, geprüft 28.08.2026).
- Verwendet als **reine Autoring-Hilfe**: die Item-Namen aus
  `inverter_definitions/*.yaml` (z. B. `"Battery Max Charging Current"`) sind Vorlage für
  `translation_key`-Hinweise in `roles.<rolle>.translation_key`. Register-Adressen,
  Skalierungsfaktoren und alles Protokollspezifische werden **nicht** übernommen —
  miniEMS spricht nie direkt mit dem Wechselrichter, das bleibt Sache der Solarman-
  Integration.
- `profiles/inverter/deye_sg0x_lp3.yaml` — abgeleitet von `deye_p3.yaml` (Version
  `25.08.16` bei Erstellung dieses Profils), zusätzlich live gegen eine echte
  Produktivanlage verifiziert (`verified: true`).
- `profiles/battery/pylontech_force.yaml` — abgeleitet von `pylontech_force.yaml`, **nicht**
  an echter Hardware verifiziert (`verified: false`).

## SEM (`solar_energy_management`)

Kein Lizenz-File im installierten Paket vorhanden (Stand 28.08.2026, Version
`2.0.0-beta.18`) — die Nutzungsbedingungen sind von dieser Maschine aus nicht prüfbar.
**Keine Zeilen aus `hardware_detection.py`/`consts/devices.py` (Marken-/Muster-Tabellen)
wurden übernommen.** Die dort verwendete Idee — gerätebezogene Suche mit
konfidenzgewichteten Mustern statt einzelner Treffer — ist frei nachgebaut in
`device_profile.match_profiles()` (Scoring nach `strong`/`resolved_role_count`), aber ohne
eine einzige aus SEM kopierte Zeichenkette.

## evcc

Nicht verwendet für `inverter`/`battery` — evccs Templates liegen auf Protokoll-/Go-
Template-Ebene (Modbus-Register-Zugriff), die bei uns bereits Solarman übernimmt. Bleibt
als mögliche Quelle für die `ev`-Klasse vorgemerkt, da Solarman keine Wallbox-Definition
hat — siehe `docs/roadmap/v3.x-ev-lasten-zaehler.md`.
