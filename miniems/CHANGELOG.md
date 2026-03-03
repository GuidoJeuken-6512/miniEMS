<!-- https://developers.home-assistant.io/docs/add-ons/presentation#keeping-a-changelog -->

## 1.8.1

### Bugfixes:
- **Behobene Berechtigungen**: `run`-Skripte haben jetzt korrekte Ausführungsrechte im Docker-Container
- **Optimierte Installation**: Verbesserte Dockerfile-Struktur für besseres Caching und schnellere Builds
- **requirements.txt**: Python-Abhängigkeiten in separater Datei für optimiertes Layer-Caching

### Technische Verbesserungen:
- Dockerfile optimiert: Separate RUN-Layer für besseres Caching
- requirements.txt hinzugefügt für bessere Build-Performance
- Automatische Berechtigungsvergabe für run-Skripte und Python-Dateien im Container

## 1.8.0

### Neue Features:
- **Grid Import/Export Trennung**: Separate Sensoren für Grid-Import und Grid-Export
- **Sensor-Typ Konfiguration**: Checkboxen für jeden Sensor zur expliziten Konfiguration (täglich zurückgesetzt vs. kumulativ)
- **Intelligente Auto-Erkennung**: Automatische Erkennung basierend auf Sensornamen ("today" = daily) und Tag-Grenzen
- **Datenbank-Housekeeping**: Automatische Löschung alter Daten basierend auf konfigurierbarer Aufbewahrungsdauer

### Technische Verbesserungen:
- Separate Datenbankfelder für Grid-Import und Grid-Export
- Konfigurierbare Sensor-Typen mit Fallback auf Auto-Erkennung
- UI: Checkboxen mit Erklärungen für jeden Sensor
- Automatische Erkennung: Sensorname (z.B. "today") und Tag-Grenzen-Analyse

### Datenbank-Änderungen:
- Migration 1.8.0: `grid_energy_kwh` aufgeteilt in `grid_import_energy_kwh` und `grid_export_energy_kwh`
- Neue Konfigurationsfelder: `grid_import_energy_sensor_daily`, `grid_export_energy_sensor_daily`, etc.

## 1.7.0

### Neue Features:
- **Energie-Sensoren statt Power-Sensoren**: Umstellung auf kumulative Energie-Sensoren (kWh) für genauere Berechnungen
- **Automatische Sensor-Typ-Erkennung**: System erkennt automatisch, ob Sensoren kumulativ (total) oder täglich zurückgesetzt (daily) sind
- **Automatische Einheiten-Umrechnung**: Unterstützt kWh, Wh (automatisch zu kWh) und MWh (automatisch zu kWh)
- **Datenbank-Migrationssystem**: Versionsbasiertes System für Datenbank-Schema-Änderungen
- **Robuste Berechnung**: Unterstützt sowohl kumulative als auch täglich zurückgesetzte Sensoren in derselben Installation

### Technische Verbesserungen:
- Umstellung von Power-Sensoren (Watt) auf Energie-Sensoren (kWh, kumulativ)
- Automatische Erkennung von Sensor-Typen (total vs. daily) basierend auf historischen Daten
- Automatische Normalisierung von Energie-Einheiten (Wh → kWh, MWh → kWh)
- Verbesserte Kostenberechnung mit direkten Energie-Differenzen statt Zeitintegration
- Migrationssystem für zukünftige Datenbank-Änderungen
- Bereinigung alter Power-Sensor-Konfigurationen

### Datenbank-Änderungen:
- Neue Felder in `energy_readings`: `grid_energy_kwh`, `battery_charge_energy_kwh`, `battery_discharge_energy_kwh`, `pv_energy_kwh`, `house_energy_kwh`
- Entfernte Felder: `grid_power`, `battery_power`, `pv_power`
- Neue Tabelle: `schema_migrations` für Migrations-Tracking

### Konfiguration:
- Neue Optionen: `grid_energy_sensor`, `pv_energy_sensor`, `battery_charge_energy_sensor`, `battery_discharge_energy_sensor`, `house_energy_sensor`
- Entfernte Optionen: `grid_power_sensor`, `pv_power_string1/2/3`, `battery_power_sensor`
- Automatische Bereinigung alter Konfigurationsfelder

### API-Änderungen:
- `GET /api/sensors/energy` - Neue Endpunkt für Energie-Sensoren (ersetzt `/api/sensors/power`)

### Migration:
- Alte Datenbank wird automatisch migriert (alte Power-Daten werden gelöscht)
- Alte Konfigurationsfelder werden automatisch bereinigt
- Siehe [docs/migrations.md](../docs/migrations.md) für Details zum Migrationssystem

## 1.6.0

### Neue Features:
- **Energiekosten-Management System**: Umfassende Kostenanalyse mit Vergleich mit/ohne Batterie-Strategie
- **SQLite-Datenbank**: Persistente Speicherung historischer Sensordaten und Kostenberechnungen
- **Grafische Visualisierung**: Chart.js-basierte Diagramme für Kostenvergleiche
- **Flexible Batterie-Strategien**:
  - Schwellwert-basierte Strategie (Preis unter Schwellwert)
  - Zeitplan-basierte Strategie mit mehreren Zeitslots
- **Zeitplan-Konfiguration**:
  - Mehrere Zeitslots mit individuellen Preisen pro kWh
  - Wochentage-Auswahl pro Zeitslot
  - Automatische Validierung (Überschneidungen, Lücken)
- **Home Assistant Sensoren**: Automatische Erstellung von Kosten-Sensoren als Entities
- **Verbesserte Kostenberechnung**: Präzise Berechnung mit Zeitdifferenzen und korrekter Batterie-Logik
- **Modulare Code-Struktur**: Refactoring in separate Module (config.py, database.py, cost_calculation.py, ha_integration.py, utils.py)
- **UI-Verbesserungen**:
  - Kompakterer Header mit Logo
  - Verbesserte Navigation
  - Neue Energiekosten-Seite

### Technische Verbesserungen:
- Refactoring der Python-Codebasis in modulare Struktur
- Verbesserte Kostenberechnungslogik mit korrekter Zeitberechnung
- Schedule-Validierung in utils.py zentralisiert
- Atomare Datei-Schreibvorgänge für Konfiguration
- Erweiterte API-Endpunkte für Kostenanalyse

### API-Erweiterungen:
- `GET /energy-costs` - Neue Energiekosten-Seite
- `GET /api/energy-costs/compare` - Kostenvergleich für Zeitraum
- `GET /api/energy-costs/sensors` - Liste aller Kosten-Sensoren
- `POST /api/energy-costs/calculate` - Kosten berechnen
- `POST /api/schedule/validate` - Zeitplan validieren

### Konfiguration:
- Neue Option: `battery_strategy_type` (threshold/schedule)
- Neue Option: `schedule_config` (flexibler Zeitplan)
- Neue Option: `update_interval` (Aktualisierungsintervall)

## 1.5.0

- Added electricity price sensor configuration option
- Display electricity price sensor value on main dashboard
- Added new API endpoint `/api/sensors/all` to get all sensors (for price sensor selection)
- Updated configuration page to include electricity price sensor dropdown

## 1.4.0

- Replaced single PV Power sensor with three configurable PV Power Strings (String 1, 2, 3)
- Added virtual PV Power Total sensor that automatically sums all configured strings
- Fault-tolerant: Missing or unavailable strings are ignored (0 added)
- Updated dashboard to display all three strings and total
- Backward compatibility: Old `pv_power_sensor` config is automatically migrated to `pv_power_string1`

## 1.3.1

- Fixed configuration persistence issue - configuration now survives addon updates and restarts
- Improved configuration loading with automatic sync from bashio to file
- Added atomic file writes for configuration to prevent corruption
- Added startup logging for configuration status

## 1.3.0

- Added PV Power sensor configuration option
- Display PV Power sensor value on main dashboard
- Updated configuration page to include PV Power sensor selection

## 1.2.1

- Improved error messages for API token issues
- Updated documentation for development environment token requirements

## 1.2.0

- Added support for Long-Lived Access Token configuration
- Added configuration page for sensor selection
- Display configured sensor values on main dashboard
- Implemented API endpoints for sensor listing and configuration management

## 1.1.0

- Added configuration page for sensor selection
- Added three sensor dropdowns: Grid Power, Battery Power, Battery SOC
- Added sensor value display on main page
- Integrated Home Assistant API for sensor data
- Auto-refresh of sensor values every 5 seconds
- Filter sensors by type (power, SOC)

## 1.0.0

- Initial release
- Added FastAPI and Uvicorn web server
- Added Hello World web interface
- Integrated Home Assistant Ingress for web UI access
- Web interface accessible through Home Assistant menu
