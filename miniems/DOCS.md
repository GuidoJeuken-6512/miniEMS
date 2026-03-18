# miniEMS - Technische Dokumentation

## Übersicht

miniEMS ist ein Energy Management System für Home Assistant, das eine umfassende Kostenanalyse von Energiestrategien bietet. Das System sammelt Energie-Sensordaten (kWh), speichert sie in einer SQLite-Datenbank und berechnet Kostenvergleiche zwischen verschiedenen Strategien.

**Wichtige Features:**

- Unterstützt sowohl kumulative (total) als auch täglich zurückgesetzte (daily) Energie-Sensoren
- Automatische Erkennung von Sensor-Typen
- Automatische Einheiten-Normalisierung (Wh → kWh, MWh → kWh)
- Versionsbasiertes Datenbank-Migrationssystem

## Architektur

### Modulare Struktur

Das Add-on verwendet eine modulare Python-Struktur für bessere Wartbarkeit:

```
miniems_app.py          # FastAPI Hauptanwendung
├── config.py           # Konfigurationsverwaltung
├── database.py          # SQLite Datenbank-Operationen
├── cost_calculation.py  # Kostenberechnungslogik
├── ha_integration.py    # Home Assistant API Integration
└── utils.py             # Zentrale Hilfsfunktionen
```

### Module im Detail

#### `miniems_app.py`

- FastAPI-Anwendung mit allen HTTP-Routen
- Background-Tasks für Datensammlung und Sensor-Updates
- HTML-Templates für Web-Interface
- Lebenszyklus-Management (Startup/Shutdown)

#### `config.py`

- `SensorConfig`: Pydantic-Modell für Konfiguration
- `TimeSlot` & `ScheduleConfig`: Zeitplan-Modelle
- `load_config()`: Konfiguration aus Datei/bashio laden
- `save_config()`: Konfiguration atomar speichern
- `get_bashio_config()`: Bashio-Konfiguration lesen

#### `database.py`

- `init_database()`: Datenbank initialisieren und Migrationen ausführen
- `save_energy_reading()`: Sensordaten speichern (Energie-Sensoren in kWh)
- `get_energy_readings()`: Historische Daten abrufen
- `calculate_daily_energy_flow()`: Täglichen Energiefluss berechnen (für Sankey-Diagramm)
- SQLite-Datenbank: `/data/energy_data.db`

#### `migrations.py`

- `run_migrations()`: Führt ausstehende Datenbank-Migrationen aus
- `detect_sensor_type()`: Erkennt Sensor-Typ (total/daily)
- `calculate_energy_difference()`: Berechnet Energie-Differenzen mit Sensor-Typ-Bewusstsein
- Versionsbasiertes Migrationssystem für Schema-Änderungen
- Siehe [docs/migrations.md](../docs/migrations.md) für Details

#### `cost_calculation.py`

- `calculate_energy_costs()`: Hauptfunktion für Kostenberechnung
- Verwendet Energie-Sensoren (kWh) statt Power-Sensoren
- Unterstützt sowohl kumulative (total) als auch täglich zurückgesetzte (daily) Sensoren
- Automatische Sensor-Typ-Erkennung
- Berechnet Energie-Differenzen direkt (keine Zeitintegration nötig)
- Trennt Batterie-Laden und Entladen
- Berechnet hypothetische Kosten ohne Batterie

#### `ha_integration.py`

- `get_sensors_from_ha()`: Sensoren von HA API abrufen (filtert nach Typ: energy, soc, etc.)
- `get_sensor_value()`: Einzelnen Sensor-Wert abrufen
- `update_ha_sensor()`: HA-Sensor erstellen/aktualisieren
- `update_all_cost_sensors()`: Alle Kosten-Sensoren aktualisieren
- `generate_sensor_entity_id()`: Sensor-ID nach Konvention generieren

#### `utils.py`

- `time_to_minutes()` / `minutes_to_time()`: Zeitkonvertierung
- `normalize_time_slot()`: Zeitslot-Normalisierung (über Nacht)
- `check_overlap()`: Überschneidungsprüfung
- `validate_schedule_coverage()`: Zeitplan-Validierung
- `get_price_for_time()`: Preis für Zeitpunkt ermitteln
- `should_charge_battery()`: Entscheidung, ob Batterie laden
- `normalize_energy_value()`: Normalisiert Energie-Werte zu kWh (Wh → kWh, MWh → kWh)
- `get_normalized_energy_sensor_value()`: Holt und normalisiert Energie-Sensor-Werte
- `detect_sensor_type()`: Erkennt automatisch, ob Sensor kumulativ (total) oder täglich zurückgesetzt (daily) ist
- `calculate_energy_difference()`: Berechnet Energie-Differenzen mit Sensor-Typ-Bewusstsein

## Datenbank-Schema

### `energy_readings`

Speichert historische Sensordaten (Energie-Sensoren in kWh):

```sql
CREATE TABLE energy_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    grid_energy_kwh REAL,
    battery_charge_energy_kwh REAL,
    battery_discharge_energy_kwh REAL,
    pv_energy_kwh REAL,
    house_energy_kwh REAL,
    electricity_price REAL,
    battery_soc REAL
)
```

**Hinweis**: Die Datenbank verwendet ein Migrationssystem. Das Schema wird automatisch bei Bedarf aktualisiert. Siehe [docs/migrations.md](../docs/migrations.md) für Details.

### `cost_calculations`

Speichert berechnete Kosten (für zukünftige Erweiterungen):

```sql
CREATE TABLE cost_calculations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    scenario TEXT NOT NULL,
    period_type TEXT,
    cost_with_strategy REAL,
    cost_without_strategy REAL,
    savings REAL,
    metric TEXT
)
```

### `schema_migrations`

Verfolgt angewendete Datenbank-Migrationen:

```sql
CREATE TABLE schema_migrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL UNIQUE,
    applied_at DATETIME NOT NULL,
    description TEXT
)
```

## Kostenberechnung

### Logik

Die Kostenberechnung vergleicht zwei Strategien:

1. **Mit Batterie (Strategie A)**: Tatsächliche Kosten mit Batterie-Laden/Entladen
2. **Ohne Batterie (Strategie B)**: Hypothetische Kosten ohne Batterie

### Energie-Sensoren

Das System verwendet **Energie-Sensoren (kWh, kumulativ oder täglich zurückgesetzt)** statt Power-Sensoren:

- **Kumulative Sensoren (total)**: Werte steigen kontinuierlich (z.B. 100 kWh → 105 kWh → 110 kWh)
- **Täglich zurückgesetzte Sensoren (daily)**: Werte werden um Mitternacht auf 0 zurückgesetzt (z.B. 5 kWh → 0 kWh → 2 kWh)

Das System erkennt automatisch den Sensor-Typ und passt die Berechnung entsprechend an.

### Formel

Für jeden Messpunkt wird die Energie-Differenz berechnet:

**Für kumulative Sensoren:**

```
Energie_Differenz = aktueller_Wert - vorheriger_Wert
```

**Für täglich zurückgesetzte Sensoren:**

```
Wenn Reset erkannt (Wert < 50% des vorherigen Werts am Tageswechsel):
    Energie_Differenz = aktueller_Wert  (Start bei 0)
Sonst:
    Energie_Differenz = aktueller_Wert - vorheriger_Wert
```

**Berechnung:**

- **Grid Import** = max(0, grid_energy_diff) in kWh
- **Batterie Laden** = battery_charge_energy_diff in kWh
- **Batterie Entladen** = battery_discharge_energy_diff in kWh

**Kosten mit Batterie:**

```
cost_with = Σ(grid_import_kwh * price_eur)
```

**Kosten ohne Batterie:**

```
net_import_without = grid_import_kwh - battery_charge_diff + battery_discharge_diff
cost_without = Σ(max(0, net_import_without) * price_eur)
```

**Ersparnis:**

```
savings = cost_without - cost_with
```

### Sensor-Typ-Erkennung

Das System analysiert historische Daten, um den Sensor-Typ zu erkennen:

1. **Tageswechsel-Analyse**: Prüft, ob Werte am Tageswechsel signifikant fallen (>50% Drop)
2. **Trend-Analyse**: Prüft, ob Werte überwiegend steigen (kumulativ) oder variieren (täglich)
3. **Entscheidung**:
   - Wenn Tageswechsel-Drops erkannt → `"daily"`
   - Wenn überwiegend steigend → `"total"`
   - Standard → `"total"` (sicherere Annahme)

### Einheiten-Normalisierung

Alle Energie-Werte werden automatisch auf kWh normalisiert:

- **kWh**: Unverändert
- **Wh**: Durch 1000 geteilt → kWh
- **MWh**: Mit 1000 multipliziert → kWh

Die Normalisierung erfolgt beim Abruf der Sensor-Werte aus Home Assistant.

## Batterie-Strategien

### Schwellwert-Strategie (`threshold`)

Batterie wird geladen, wenn:

```
price_eur < price_threshold
```

### Zeitplan-Strategie (`schedule`)

Batterie wird geladen, wenn:

```
schedule_price < price_threshold
```

Der `schedule_price` wird aus dem konfigurierten Zeitplan basierend auf aktuellem Zeitpunkt und Wochentag ermittelt.

## Zeitplan-Validierung

Die Validierung prüft:

1. **Abdeckung**: Alle 24 Stunden müssen abgedeckt sein
2. **Überschneidungen**: Keine überlappenden Zeitslots
3. **Lücken**: Keine fehlenden Zeiträume

### Berechnungslogik im Detail

#### 1. Zeitkonvertierung

Jeder Zeitslot wird in Minuten seit Mitternacht umgewandelt:

```python
def time_to_minutes(time_str: str) -> int:
    hours, minutes = map(int, time_str.split(':'))
    return hours * 60 + minutes
```

**Beispiele:**

- `00:00` → 0 Minuten
- `12:00` → 720 Minuten
- `21:00` → 1260 Minuten
- `23:59` → 1439 Minuten

#### 2. Normalisierung von Über-Nacht-Slots

Zeitslots, die über Mitternacht gehen (z.B. `21:00 - 00:00`), werden normalisiert:

```python
def normalize_time_slot(start: str, end: str) -> Tuple[int, int]:
    start_min = time_to_minutes(start)  # z.B. 21:00 = 1260
    end_min = time_to_minutes(end)      # z.B. 00:00 = 0

    # Wenn Ende <= Start, ist es ein Über-Nacht-Slot
    if end_min <= start_min:
        end_min += 24 * 60  # Addiere 1440 Minuten (24 Stunden)

    return start_min, end_min
```

**Beispiele:**

- `21:00 - 00:00` → `(1260, 0)` → normalisiert zu `(1260, 1440)`
- `22:00 - 06:00` → `(1320, 360)` → normalisiert zu `(1320, 1800)`
- `12:00 - 14:00` → `(720, 840)` → bleibt `(720, 840)` (kein Über-Nacht)

#### 3. Abdeckungsprüfung

Die Abdeckungsprüfung erfolgt in mehreren Schritten:

**Schritt 1: Über-Nacht-Slots aufteilen**

Über-Nacht-Slots werden in zwei Bereiche aufgeteilt:

- Teil 1: Von Start bis Mitternacht (24:00 = 1440 Minuten)
- Teil 2: Von Mitternacht bis Ende

```python
if end_min > 24 * 60:  # Über-Nacht-Slot (z.B. end_min = 1800)
    covered_ranges.append((start_min, 24 * 60))      # 21:00-24:00
    covered_ranges.append((0, end_min - 24 * 60))   # 00:00-06:00
else:
    covered_ranges.append((start_min, end_min))     # Normaler Slot
```

**Beispiel `21:00 - 00:00`:**

- Normalisiert: `(1260, 1440)`
- `end_min (1440) > 24 * 60 (1440)` → **False** → wird als normaler Slot behandelt
- `covered_ranges.append((1260, 1440))` = 180 Minuten (3 Stunden)

**Beispiel `22:00 - 06:00`:**

- Normalisiert: `(1320, 1800)`
- `end_min (1800) > 24 * 60 (1440)` → **True** → Über-Nacht-Slot
- `covered_ranges.append((1320, 1440))` = 120 Minuten (22:00-24:00)
- `covered_ranges.append((0, 360))` = 360 Minuten (00:00-06:00)
- **Gesamt:** 480 Minuten (8 Stunden)

**Schritt 2: Bereiche sortieren und zusammenführen**

Alle Bereiche werden nach Startzeit sortiert und überlappende/angrenzende Bereiche werden zusammengeführt:

```python
covered_ranges.sort(key=lambda x: x[0])

total_minutes = 0
current_start, current_end = covered_ranges[0]
for start, end in covered_ranges[1:]:
    if start <= current_end:  # Überlappung oder angrenzend
        current_end = max(current_end, end)  # Erweitere Bereich
    else:  # Lücke gefunden
        total_minutes += (current_end - current_start)
        current_start, current_end = start, end
total_minutes += (current_end - current_start)
```

**Beispiel mit Slots:**

- Slot 1: `00:00 - 02:00` → `(0, 120)`
- Slot 2: `02:00 - 06:00` → `(120, 360)`
- Slot 3: `06:00 - 12:00` → `(720, 360)` → **Fehler!** (sollte `(360, 720)` sein)
- Slot 4: `21:00 - 00:00` → `(1260, 1440)`

**Korrigierte Berechnung:**

1. Sortiert: `[(0, 120), (120, 360), (360, 720), (720, 1080), (1080, 1200), (1200, 1380), (1260, 1440)]`
2. Zusammenführen:
   - `(0, 120)` + `(120, 360)` → `(0, 360)` (angrenzend)
   - `(0, 360)` + `(360, 720)` → `(0, 720)` (angrenzend)
   - `(0, 720)` + `(720, 1080)` → `(0, 1080)` (angrenzend)
   - `(0, 1080)` + `(1080, 1200)` → `(0, 1200)` (angrenzend)
   - `(0, 1200)` + `(1200, 1380)` → `(0, 1380)` (angrenzend)
   - `(0, 1380)` + `(1260, 1440)` → `(0, 1440)` (überlappend: 1260 < 1380)
3. **Gesamt:** 1440 Minuten = 24 Stunden ✓

**Schritt 3: Validierung**

```python
if total_minutes < 24 * 60:
    return False, f"Lücke: {1440 - total_minutes} Minuten nicht abgedeckt"
elif total_minutes > 24 * 60:
    return False, f"Überlappung: Mehr als 24 Stunden abgedeckt"
else:
    return True, None  # Gültig
```

#### 4. Überschneidungsprüfung

Für jeden Tag werden alle Slots paarweise auf Überschneidungen geprüft:

```python
for i in range(len(slots)):
    for j in range(i + 1, len(slots)):
        slot1 = (slots[i][0], slots[i][1])
        slot2 = (slots[j][0], slots[j][1])

        if slot1[1] > 24 * 60:  # Über-Nacht-Slot
            # Aufteilen in zwei Teile
            part1 = (slot1[0], 24 * 60)
            part2 = (0, slot1[1] - 24 * 60)
            if check_overlap(part1, slot2) or check_overlap(part2, slot2):
                return False, "Überschneidung gefunden"
        elif check_overlap(slot1, slot2):
            return False, "Überschneidung gefunden"
```

**Überschneidungsprüfung:**

```python
def check_overlap(slot1: Tuple[int, int], slot2: Tuple[int, int]) -> bool:
    start1, end1 = slot1
    start2, end2 = slot2
    return not (end1 <= start2 or end2 <= start1)
```

Zwei Slots überlappen, wenn:

- `end1 > start2` UND `end2 > start1`

### Beispiel: Vollständiger Zeitplan

**Eingabe:**

```json
{
  "time_slots": [
    {
      "start": "00:00",
      "end": "02:00",
      "price": 0.34,
      "days": [0, 1, 2, 3, 4, 5, 6]
    },
    {
      "start": "02:00",
      "end": "06:00",
      "price": 0.27,
      "days": [0, 1, 2, 3, 4, 5, 6]
    },
    {
      "start": "06:00",
      "end": "12:00",
      "price": 0.34,
      "days": [0, 1, 2, 3, 4, 5, 6]
    },
    {
      "start": "12:00",
      "end": "16:00",
      "price": 0.27,
      "days": [0, 1, 2, 3, 4, 5, 6]
    },
    {
      "start": "16:00",
      "end": "18:00",
      "price": 0.34,
      "days": [0, 1, 2, 3, 4, 5, 6]
    },
    {
      "start": "18:00",
      "end": "21:00",
      "price": 0.39,
      "days": [0, 1, 2, 3, 4, 5, 6]
    },
    {
      "start": "21:00",
      "end": "00:00",
      "price": 0.34,
      "days": [0, 1, 2, 3, 4, 5, 6]
    }
  ]
}
```

**Berechnung:**

1. Normalisiert: `[(0,120), (120,360), (360,720), (720,960), (960,1080), (1080,1260), (1260,1440)]`
2. Zusammenführt: `(0, 1440)` = 1440 Minuten = 24 Stunden ✓
3. **Ergebnis:** Gültig

## Home Assistant Integration

### Sensor-Namenskonvention

```
sensor.miniems_cost_{comparison_type}_{scenario}_{metric}_{period}
```

- `comparison_type`: `with`, `without`, `savings`
- `scenario`: `batterie`
- `metric`: `grid_load`
- `period`: `today`, `week`, `month`

### Beispiel-Sensoren

- `sensor.miniems_cost_with_batterie_grid_load_today`
- `sensor.miniems_cost_without_batterie_grid_load_today`
- `sensor.miniems_cost_savings_batterie_grid_load_today`

### Sensor-Attribute

Jeder Sensor hat folgende Attribute:

- `unit_of_measurement`: "€"
- `device_class`: "monetary"
- `friendly_name`: Beschreibender Name
- `scenario`: Szenario (z.B. "batterie_grid_load")
- `metric`: Metrik (z.B. "grid_load")
- `period`: Periode (z.B. "today")
- `last_update`: ISO-Timestamp

## Background-Tasks

### Datensammlung (`data_collection_worker`)

- Läuft kontinuierlich im Hintergrund
- Sammelt alle `update_interval` Sekunden Sensordaten
- Speichert Daten in SQLite-Datenbank
- Läuft nur, wenn Sensoren konfiguriert sind

### Sensor-Updates (`sensor_update_worker`)

- Läuft alle 7 Sekunden
- Berechnet Kosten für heute/Woche/Monat
- Aktualisiert alle HA-Sensoren
- Verwendet `update_all_cost_sensors()`

## API-Endpunkte

### Web-Interface

- `GET /` - Hauptseite mit Sensor-Anzeige
- `GET /config` - Konfigurationsseite
- `GET /energy-costs` - Energiekosten-Seite mit Diagrammen

### REST API

#### Sensoren

- `GET /api/sensors/energy` - Liste Energie-Sensoren (kWh)
- `GET /api/sensors/soc` - Liste SOC-Sensoren
- `GET /api/sensors/all` - Liste aller Sensoren
- `GET /api/sensors/values` - Aktuelle Sensor-Werte
- `GET /api/sensor/{entity_id}` - Einzelner Sensor-Wert

#### Konfiguration

- `GET /api/config` - Aktuelle Konfiguration
- `POST /api/config` - Konfiguration speichern
- `POST /api/schedule/validate` - Zeitplan validieren

#### Energiekosten

- `GET /api/energy-costs/compare` - Kostenvergleich
- `GET /api/energy-costs/sensors` - Liste Kosten-Sensoren
- `POST /api/energy-costs/calculate` - Kosten berechnen

#### System

- `GET /api/health` - Health Check
- `GET /icon.png` - Add-on Logo

## Konfiguration

### Konfigurationsdatei

Die Konfiguration wird in `/data/sensor_config.json` gespeichert:

```json
{
  "ha_api_token": "...",
  "grid_energy_sensor": "sensor.grid_energy",
  "pv_energy_sensor": "sensor.pv_energy",
  "battery_charge_energy_sensor": "sensor.battery_charge_energy",
  "battery_discharge_energy_sensor": "sensor.battery_discharge_energy",
  "house_energy_sensor": "sensor.house_energy",
  "battery_soc_sensor": "sensor.battery_soc",
  "electricity_price_sensor": "sensor.electricity_price",
  "battery_strategy_type": "threshold",
  "price_threshold": 0.2,
  "charge_schedule": "",
  "schedule_config": "",
  "update_interval": 60
}
```

### Zeitplan-Konfiguration

Beispiel für `schedule_config`:

```json
{
  "time_slots": [
    {
      "start": "22:00",
      "end": "06:00",
      "price": 0.15,
      "days": [0, 1, 2, 3, 4, 5, 6]
    },
    {
      "start": "12:00",
      "end": "14:00",
      "price": 0.18,
      "days": [0, 1, 2, 3, 4]
    }
  ]
}
```

## Fehlerbehandlung

### Sensoren nicht verfügbar

- Fehlende Sensoren werden als "Nicht verfügbar" angezeigt
- PV-Strings: Fehlende Strings werden ignoriert (0 addiert)
- Datenbank: Fehlende Werte werden als NULL gespeichert

### API-Fehler

- Home Assistant API: Automatischer Fallback auf alternative URLs
- Token-Probleme: Klare Fehlermeldungen mit Anweisungen
- Validierung: Detaillierte Fehlermeldungen bei ungültigen Konfigurationen

## Erweiterbarkeit

### Neue Szenarien

Die Architektur unterstützt mehrere Szenarien:

- Aktuell: `batterie_grid_load`
- Zukünftig: Weitere Szenarien möglich (z.B. `pv_optimization`, `grid_export`)

### Neue Metriken

Die Sensor-Namenskonvention erlaubt verschiedene Metriken:

- Aktuell: `grid_load`
- Zukünftig: Weitere Metriken möglich

### Neue Perioden

Einfach erweiterbar:

- Aktuell: `today`, `week`, `month`
- Zukünftig: `year`, `custom` möglich

## Performance

- SQLite-Indizes auf `timestamp` und `scenario/period_type`
- Background-Tasks laufen asynchron
- Atomare Datei-Operationen für Konfiguration
- Effiziente Datenbankabfragen mit Datumsbereich-Filtern

## Sicherheit

- Konfiguration wird atomar gespeichert (keine Korruption)
- Home Assistant Ingress für sicheren Zugriff
- Token-basierte Authentifizierung für HA API
- Keine sensiblen Daten in Logs
