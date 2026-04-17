---
revision_date: 2026-04-07
---

# Datenspeicherung

## Dateiübersicht

| Pfad | Verwaltet von | Zweck |
|---|---|---|
| `/data/config.json` | miniEMS | Persistente Konfiguration mit Schema-Version |
| `/data/miniems.db` | miniEMS (`store.py`) | SQLite-Tagesenergie-Historie |
| HA Core Entity Registry | Home Assistant | `sensor.miniems_*` Langzeit-Zustandshistorie |
| In-Memory `status_store` | miniEMS Runtime | Live-Werte für Dashboard und Publisher |
| In-Memory `CostOptimizer` | miniEMS Runtime | Intraday-Akkumulatoren (täglich um Mitternacht gespeichert) |
| In-Memory `EventLog` | miniEMS Runtime | Ringpuffer mit 100 Ereignissen (gestützt durch SQLite-Tabelle `event_log`) |

!!! note "options.json und config.json"
    `options.json` wird beim Start gelesen und kann `config.json` für Werte
    überschreiben, die von den Dataclass-Standardwerten abweichen. `config.json`
    ist jedoch die dauerhaft maßgebliche Konfigurationsquelle — bei jedem Start
    wird das zusammengeführte Ergebnis zurückgeschrieben, sodass es jeden
    Supervisor-Reset von `options.json` überlebt.
    Beide Dateien können über die Tabs **config.json** und **options.json**
    in der miniEMS-Oberfläche eingesehen und bearbeitet werden.

---

## `/data/config.json`

Erstellt und gepflegt von `config_loader.py`. Überlebt Add-on-Updates,
Supervisor-Neuladen und Neustarts.

```json
{
  "_version": 10,
  "pv_power_entity": "sensor.deye_pv_total_power",
  "battery_soc_entity": "sensor.deye_battery_soc",
  "battery_capacity_kwh": 25.0,
  "battery_min_soc": 10,
  "battery_max_soc": 95,
  "cheap_rate_threshold_eur": 0.28,
  "medium_rate_threshold_eur": 0.20,
  "feed_in_tariff_eur_kwh": 0.08,
  "fix_price": 0.30,
  "battery_control_enabled": false,
  "battery_control_simulation": true,
  "grid_charge_switch_entity": "switch.deye8k_battery_grid_charging",
  "battery_discharging_power_entity": "number.deye8k_battery_discharging_power",
  "battery_max_charge_power_w": 5500,
  "battery_max_discharge_power_w": 5500,
  "default_discharge_power_w": 185,
  "solcast_remaining_today_entity": "sensor.solcast_pv_forecast_prognose_verbleibende_leistung_heute",
  "solcast_today_entity": "sensor.solcast_pv_forecast_prognose_heute",
  "solcast_tomorrow_entity": "sensor.solcast_pv_forecast_prognose_morgen",
  "weather_entity": "weather.openweathermap",
  "update_interval_sec": 30,
  "event_log_retention_days": 30,
  "long_lived_token": ""
}
```

### Schema-Versionierung

`_version` wird von `migration.py` geschrieben. Die aktuelle Version ist **10**.

Bei einer Schema-Änderung:

1. `CONFIG_SCHEMA_VERSION` in `const.py` erhöhen
2. Eine Migrationsfunktion `_vN_to_vN1(data: dict) -> dict` hinzufügen
3. In `migration.py` registrieren

Migrationskette: v0 → v1 → v2 → v3 → v4 → v5 → v6 → v7 → v8 → v9 → v10

---

## `/data/miniems.db`

SQLite-Datenbank, verwaltet von `store.py`. Enthält zwei Tabellen:

### Tabelle `daily_stats`

```sql
CREATE TABLE daily_stats (
    date               TEXT PRIMARY KEY,  -- ISO-Datum 'YYYY-MM-DD'
    grid_import_kwh    REAL DEFAULT 0,
    grid_cost_eur      REAL DEFAULT 0,
    pv_used_kwh        REAL DEFAULT 0,
    pv_savings_eur     REAL DEFAULT 0,
    load_total_kwh     REAL DEFAULT 0,
    load_cost_eur      REAL DEFAULT 0,
    avg_price_eur_kwh  REAL DEFAULT 0,
    avg_outdoor_temp_c REAL,
    ticks              INTEGER DEFAULT 0,
    -- per ALTER TABLE beim Upgrade hinzugefügt:
    peak_pv_w          REAL DEFAULT 0,
    grid_charge_kwh    REAL DEFAULT 0,
    grid_charge_cost_eur REAL DEFAULT 0,
    feed_in_kwh        REAL DEFAULT 0,
    feed_in_revenue_eur REAL DEFAULT 0,
    last_flush_ts      TEXT,              -- UTC-ISO-Zeitstempel des letzten Schreibvorgangs
    kwh_high_rate      REAL DEFAULT 0,   -- Last bei hohem Preisklasse
    kwh_medium_rate    REAL DEFAULT 0,   -- Last bei mittlerer Preisklasse
    kwh_low_rate       REAL DEFAULT 0    -- Last bei niedriger Preisklasse
);
```

Neue Spalten werden beim ersten Start nach einem Upgrade automatisch per `ALTER TABLE` hinzugefügt.

### Flush-Verhalten

`CostOptimizer` akkumuliert Werte den ganzen Tag im Speicher. Um Mitternacht
(erkannt durch Wechsel von `date.today()`) ruft er `store.flush_day()` auf,
um die fertige Tageszeile in SQLite zu schreiben. Die Spalte `last_flush_ts`
wird bei jedem Flush aktualisiert und beim Start zur Ausfallzeiten-Erkennung
verwendet.

### Aggregation

`store._aggregate()` berechnet rollende/kalendarische Aggregate direkt in SQL:

```sql
-- Monat (Kalendermonat)
SELECT SUM(grid_cost_eur), SUM(kwh_high_rate), SUM(kwh_medium_rate), SUM(kwh_low_rate)
FROM daily_stats
WHERE strftime('%Y-%m', date) = strftime('%Y-%m', 'now')
```

---

### Tabelle `event_log`

Speichert jedes Moduszwechsel- und Preisänderungsereignis, sodass das Log Neustarts
und Updates überlebt.

```sql
CREATE TABLE event_log (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp                TEXT NOT NULL,   -- ISO 8601 Ortszeit
    entry_type               TEXT NOT NULL,   -- "mode_change" | "price_change"
    state                    TEXT NOT NULL,   -- "on" | "off" | "price_change"
    battery_kwh_freetochange REAL DEFAULT 0,
    battery_kwh_useable      REAL DEFAULT 0,
    predicted_load_kwh       REAL,
    price_eur_kwh            REAL             -- NULL bei mode_change-Einträgen
);
```

#### Schreibpfad

`EventLog.append()` wird von `EMSController` bei jedem Moduszwechsel oder
Preisänderung aufgerufen. Es:

1. Fügt den Eintrag zur In-Memory-`deque(maxlen=100)` hinzu
2. Schreibt sofort eine Zeile über `store.append_event()` in `event_log`

#### Startup-Wiederherstellung

`EventLog.restore_from_db()` wird in `main.py` nach `store.open()` aufgerufen.
Es liest die letzten 100 Zeilen (neueste zuerst), kehrt die Reihenfolge um und
füllt den In-Memory-Puffer — sodass `to_list()` sofort befüllt ist.

#### Tägliche Bereinigung

Einmal täglich (beim ersten EMS-Tick nach Mitternacht) ruft `EMSController`
`EventLog.cleanup_old_entries(retention_days)` auf, das Folgendes ausführt:

```sql
DELETE FROM event_log
WHERE timestamp < datetime('now', '-N days')
```

Das Aufbewahrungsfenster wird durch `event_log_retention_days` in der Konfiguration
festgelegt (Standard: 30 Tage).

---

## HA-Entity-Zustände (`sensor.miniems_*`)

Zustände werden über zwei Pfade geschrieben:

### MQTT Discovery (bevorzugt)

miniEMS veröffentlicht Config-Topics an `homeassistant/sensor/miniems_*/config`
beim Start, dann Daten an `homeassistant/sensor/miniems_*/state` bei jedem Tick.
Sensoren erscheinen unter dem **miniEMS**-Gerät in HA.

### REST API (Fallback)

```
POST http://hassio/homeassistant/api/states/sensor.miniems_<name>
{
  "state": "<wert>",
  "attributes": { "unit_of_measurement": "...", "state_class": "..." }
}
```

HA speichert diese Zustände in seiner eigenen Datenbank. Das Add-on pflegt
keine lokale Kopie über `status_store` hinaus.

---

## In-Memory-Laufzeitzustand

### `status_store` (shared dict)

Nach jedem EMS-Tick von `ems_loop` aktualisiert. Gelesen von:

- `web_server` → `/api/status` → Dashboard-Auto-Refresh
- `mqtt_publisher` → MQTT-Zustands-Topics
- `ha_sensor_publisher` → REST POST an HA Core

### `CostOptimizer`-Akkumulatoren

Pro-Tag-`defaultdict(float)` nach `date.today()` geordnet:

```python
_grid_import_kwh     : {date: float}
_pv_used_kwh         : {date: float}
_grid_cost_eur       : {date: float}
_pv_saved_eur        : {date: float}
_load_total_kwh      : {date: float}
_grid_charge_kwh     : {date: float}
_grid_charge_cost_eur: {date: float}
_feed_in_kwh         : {date: float}
_feed_in_revenue_eur : {date: float}
_kwh_high_rate       : {date: float}   # Preisklassen-Akkumulatoren
_kwh_medium_rate     : {date: float}
_kwh_low_rate        : {date: float}
```

In-Memory-Akkumulatoren werden beim Add-on-Neustart zurückgesetzt. Langzeit-Persistenz
wird durch die `total_increasing` HA-Sensoren (HA zeichnet diese in seiner eigenen DB auf)
und die `daily_stats`-Wiederherstellung beim Start bereitgestellt.

### `EventLog`-Ringpuffer + SQLite

`event_log.py` pflegt eine `deque(maxlen=100)`, **gestützt durch die SQLite-Tabelle `event_log`**.
Zwei Ereignistypen teilen denselben Puffer:

```python
@dataclass
class LogEntry:
    timestamp: str              # ISO 8601 String
    state: str                  # "on" | "off" | "price_change"
    battery_kwh_freetochange: float
    battery_kwh_useable: float
    predicted_load_kwh: float | None
    entry_type: str             # "mode_change" | "price_change"
    price_eur_kwh: float | None # gesetzt bei price_change-Einträgen
```

| `entry_type` | Ausgelöst wenn | `state`-Wert |
|---|---|---|
| `mode_change` | EMS wechselt den Betriebsmodus | `"on"` (Netzladung gestartet) oder `"off"` |
| `price_change` | Strompreis-Sensor meldet einen neuen Wert | `"price_change"` |

Preisänderungs-Einträge werden nur aufgezeichnet, wenn der Preis vom zuvor
beobachteten Wert abweicht. Der allererste Wert nach dem Start wird nicht geloggt.

Das Log wird über `/api/status` → `log`-Array bereitgestellt und auf der
`/log`-Seite dargestellt. Der Puffer hält die letzten 100 Ereignisse beider
Typen zusammen. Ereignisse **überleben Neustarts** — sie werden in SQLite
gespeichert und beim Start wiederhergestellt. Siehe das
[`event_log`-Tabellen-Schema](#tabelle-event_log) oben.
