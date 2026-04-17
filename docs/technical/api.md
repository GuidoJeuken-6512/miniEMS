---
revision_date: 2026-04-07
---

# API-Referenz

miniEMS stellt eine kleine HTTP-API bereit, die von FastAPI auf Port 8080
(HA Ingress) gehostet wird. Alle Endpunkte sind intern — sie werden vom Dashboard
und dem HA-Ingress-Proxy genutzt und nicht als öffentliche API bereitgestellt.

---

## `GET /`

Rendert `dashboard.html` (Jinja2). Gibt die Live-Dashboard-Seite zurück.

Query-Parameter: keine.
Antwort: `text/html`

---

## `GET /settings`

Rendert `settings.html` (Jinja2). Gibt die Konfigurationsformular-Seite zurück.

Antwort: `text/html`

---

## `GET /log`

Rendert `log.html` (Jinja2). Gibt die vereinheitlichte Ereignislog-Seite zurück,
die Moduszwechsel und Preisänderungen anzeigt.

Antwort: `text/html`

---

## `GET /config-json`

Rendert `config_json.html`. Roher JSON-Editor für `/data/config.json`.

Antwort: `text/html`

---

## `GET /options-json`

Rendert `options_json.html`. Roher JSON-Editor für `/data/options.json`.

Antwort: `text/html`

---

## `GET /database`

Rendert `database.html`. Sortierbarer Browser für die SQLite-Tabelle `daily_stats`.

Antwort: `text/html`

---

## `GET /api/status`

Gibt den aktuellen EMS-Zustand als JSON zurück. Wird vom JavaScript-Auto-Refresh
des Dashboards (alle 5 s) und der Log-Seite konsumiert.

Antwort: `application/json`

```json
{
  "mode": "Idle",
  "mode_label": "Idle",
  "soc": 72,
  "pv_power_w": 1200,
  "load_power_w": 800,
  "grid_power_w": -400,
  "battery_power_w": 0,
  "price_eur_kwh": 0.31,
  "is_cheap_rate": false,

  "today_grid_cost_eur": 1.23,
  "today_pv_savings_eur": 0.87,
  "today_pv_used_kwh": 3.4,
  "today_grid_import_kwh": 4.1,
  "today_load_total_kwh": 7.5,
  "today_cost_without_grid_charge": 0.95,
  "today_cost_fix_price_tariff": 2.25,
  "today_feed_in_kwh": 0.6,
  "today_feed_in_revenue_eur": 0.05,
  "today_grid_charge_kwh": 1.2,

  "week_grid_cost_eur": 8.40,
  "week_pv_savings_eur": 5.10,
  "month_grid_cost_eur": 31.20,
  "month_pv_savings_eur": 18.40,
  "year_grid_cost_eur": 180.00,
  "year_pv_savings_eur": 95.00,

  "battery_kwh_freetochange": 4.5,
  "battery_kwh_useable": 14.0,

  "predicted_load_kwh": 10.5,
  "predicted_pv_kwh": 8.3,
  "prediction_source": "historical",

  "warnings": [],
  "log": [
    {
      "timestamp": "2026-04-07T06:15:00",
      "state": "on",
      "entry_type": "mode_change",
      "battery_kwh_freetochange": 12.5,
      "battery_kwh_useable": 6.0,
      "predicted_load_kwh": 10.5,
      "price_eur_kwh": null
    },
    {
      "timestamp": "2026-04-07T06:00:00",
      "state": "price_change",
      "entry_type": "price_change",
      "battery_kwh_freetochange": 13.1,
      "battery_kwh_useable": 5.8,
      "predicted_load_kwh": 10.5,
      "price_eur_kwh": 0.0850
    }
  ],

  "simulation_mode": false,
  "last_update": "2026-04-07T07:45:00Z"
}
```

### `warnings`-Array

Jede Warnung ist ein String-Schlüssel, der in den Übersetzungsdateien nachgeschlagen
wird, z. B.:

```json
["warn_missing_pv_entity", "warn_missing_price_entity"]
```

Das Dashboard rendert diese mit ihrem übersetzten Text im Warnungsbanner.

### `prediction_source`

| Wert | Bedeutung |
|---|---|
| `"historical"` | Median temperaturähnlicher Historientage |
| `"fallback"` | Temperaturregelbasierte Schätzung |

---

## `GET /api/config`

Gibt die aktuelle Konfiguration aus `/data/config.json` als JSON zurück
(interne Schlüssel werden entfernt).

Antwort: `application/json`

---

## `POST /api/config`

Speichert aktualisierte Konfigurationsschlüssel in `/data/config.json` und
startet das Add-on neu. Werte werden typbasiert konvertiert (bool/int/float)
entsprechend der bekannten Feldliste in `web_server.py`. Unbekannte oder intern
(`_`-prefixierte) Schlüssel werden ignoriert.

Anfrage: `application/json` — partielles oder vollständiges Konfigurationsobjekt.

Antwort: `{"status": "restarting"}` oder `{"error": "..."}`.

---

## `GET /api/rawfile/{name}`

Gibt den rohen Inhalt einer Konfigurationsdatei als JSON zurück.
`name` ist `config` oder `options`.

| name | Datei |
|---|---|
| `config` | `/data/config.json` |
| `options` | `/data/options.json` |

Antwort: `application/json` — der geparste JSON-Inhalt der Datei oder `{}` wenn die Datei nicht vorhanden ist.

---

## `POST /api/rawfile/{name}`

Schreibt den Anfragekörper wörtlich als JSON in die genannte Datei und startet
das Add-on neu. Es wird keine Typkonvertierung durchgeführt — das genaue JSON
wird geschrieben.

Anfrage: `application/json`

Antwort: `{"status": "restarting"}` oder `{"error": "..."}`.

---

## `GET /api/database`

Gibt alle Zeilen aus der SQLite-Tabelle `daily_stats` zurück, neueste zuerst.

Antwort: `application/json`

```json
{
  "rows": [
    {
      "date": "2026-04-07",
      "grid_import_kwh": 4.123,
      "grid_cost_eur": 1.234,
      "pv_used_kwh": 8.456,
      "pv_savings_eur": 2.537,
      "load_total_kwh": 12.579,
      "load_cost_eur": 3.774,
      "avg_price_eur_kwh": 0.3001,
      "avg_outdoor_temp_c": 8.4,
      "ticks": 2880,
      "peak_pv_w": 5863.0,
      "grid_charge_kwh": 1.2,
      "grid_charge_cost_eur": 0.102,
      "feed_in_kwh": 0.5,
      "feed_in_revenue_eur": 0.04,
      "last_flush_ts": "2026-04-07T23:59:58"
    }
  ]
}
```

---

## `GET /static/<file>`

Liefert statische Assets (`style.css` usw.) aus dem Verzeichnis `static/`.

---

## Supervisor-API-Aufrufe (ausgehend)

miniEMS tätigt folgende ausgehende Aufrufe an den HA Supervisor/Core:

| Endpunkt | Richtung | Zweck |
|---|---|---|
| `GET http://hassio/homeassistant/api/states/<entity>` | Lesen | HA-Entity-Zustände abfragen (alle 15 s) |
| `POST http://hassio/homeassistant/api/states/sensor.miniems_*` | Schreiben | Sensor-Zustände veröffentlichen (REST-Fallback) |
| `POST http://hassio/homeassistant/api/services/<domain>/<service>` | Schreiben | Wechselrichter-Steuerbefehle |
| `POST http://hassio/homeassistant/api/services/weather/get_forecasts` | Lesen | Wettervorhersage (30-Minuten-Cache) |
| `GET http://supervisor/core/api/config` | Lesen | HA-Sprache + Breitengrad (beim Start) |
| `POST http://supervisor/addons/self/restart` | Aktion | Ausgelöst beim Speichern der Einstellungen |
