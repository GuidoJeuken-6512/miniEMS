# Development

## Environment

| Tool | Version |
|---|---|
| Platform | WSL2 / Linux |
| Dev container | `.devcontainer.json` |
| HA Supervisor | 2026.03.0.dev (local) |
| Python | 3.12 (Alpine in container) |

The workspace is mounted into the HA Supervisor at
`/data/addons/local/miniEMS/miniems/`.

## VSCode Tasks

All tasks are defined in `.vscode/tasks.json`.

| Task | Action |
|---|---|
| **Start Home Assistant** | Starts the supervisor if not running |
| **Rebuild and Start Addon** | Full rebuild: stops → removes container/image → builds → patches supervisor cache → starts → follows logs |
| **Start Addon** | Quick restart without rebuild |

### Rebuild and Start — what it does

```bash
ha apps stop local_miniems
docker rm -f addon_local_miniems
docker rmi -f <image-id>
ha apps rebuild --force local_miniems

# Patch supervisor cache (dev bug workaround)
docker exec hassio_supervisor python3 -c "
  import json; d=json.load(open('/data/addons.json'));
  s=d['system']['local_miniems'];
  s['schema']['long_lived_token']='str?';
  s['options']['long_lived_token']='';
  s['homeassistant_api']=True;
  json.dump(d,open('/data/addons.json','w'))
"

ha apps start local_miniems
docker logs --follow addon_local_miniems
```

> **Why the cache patch?**
> The dev version of the Supervisor (2026.03.0.dev) does not propagate
> `homeassistant_api: true` from `config.yaml` into its in-memory state on
> reload. The patch writes directly to `/data/addons.json` inside the
> supervisor container. A full HA restart permanently fixes this.

## File Structure

```
miniems/
├── config.yaml                   # Add-on manifest
├── Dockerfile                    # Build instructions
├── requirements.txt              # Python dependencies
├── translations/                 # UI translation source files
│   ├── de.yaml
│   └── en.yaml
└── rootfs/
    └── usr/
        └── bin/
            ├── main.py                    # Entry point
            ├── const.py                   # Shared constants & EMSMode enum
            ├── config_loader.py           # Config + migration runner
            ├── migration.py               # Schema migrations (v0→v5)
            ├── ha_ws_client.py            # HA state reader (REST poll)
            ├── ha_sensor_publisher.py     # HA state writer (REST POST)
            ├── ems_controller.py          # Mode decision logic
            ├── cost_optimizer.py          # Cost/savings accumulator
            ├── consumption_model.py       # Load & PV yield prediction
            ├── weather_client.py          # weather.get_forecasts client
            ├── inverter_controller.py     # Inverter work-mode & power control
            ├── store.py                   # SQLite daily energy history
            ├── web_server.py              # FastAPI dashboard + i18n
            ├── translations/              # UI strings (copied from source)
            │   ├── de.yaml
            │   └── en.yaml
            ├── templates/
            │   ├── dashboard.html         # Dashboard (Jinja2 + JS)
            │   └── settings.html          # Settings page
            └── static/
                ├── style.css
                └── icon.svg
```

## Adding a New Config Field

1. Add the field (with default) to the `Config` dataclass in `config_loader.py`
2. Add it to `options:` and `schema:` in `config.yaml`
3. If renaming or transforming an existing field, add a migration step in
   `migration.py`, increment `CONFIG_SCHEMA_VERSION` in `const.py`, and add
   the new migration function to the `_MIGRATIONS` chain

> The current schema version is **v5**.  The migration chain is:
> `v0→v1→v2→v3→v4→v5`

## Adding a New HA Sensor

Add a tuple to the `_SENSORS` list in `ha_sensor_publisher.py`:

```python
(
    "sensor.miniems_my_new_sensor",   # entity_id
    "my_status_key",                   # key in status_store dict
    {
        "friendly_name": "miniEMS My New Sensor",
        "unit_of_measurement": "W",
        "device_class": "power",
        "state_class": "measurement",
        "icon": "mdi:lightning-bolt",
    },
),
```

The value must be present in the dict returned by `EMSController.update()`.

## Debugging

```bash
# Follow live logs
docker logs -f addon_local_miniems

# Inspect container environment
docker exec addon_local_miniems env | grep -E "SUPERVISOR|HASSIO"

# Test API access manually
docker exec addon_local_miniems wget -qO- \
  --header "Authorization: Bearer $SUPERVISOR_TOKEN" \
  http://hassio/homeassistant/api/ 2>&1

# Check persisted config
docker exec addon_local_miniems cat /data/config.json
```

### Test weather forecast manually

```bash
# From inside the container – replace TOKEN and ENTITY as needed
TOKEN=$(docker exec addon_local_miniems sh -c 'echo $SUPERVISOR_TOKEN')
docker exec addon_local_miniems wget -qO- \
  --header "Authorization: Bearer $TOKEN" \
  --header "Content-Type: application/json" \
  --post-data '{"entity_id":"weather.openweathermap","type":"daily"}' \
  "http://hassio/homeassistant/api/services/weather/get_forecasts?return_response=true"
```

A successful response contains `service_response → weather.openweathermap → forecast`.

---

## Known Supervisor Dev Issue

The Supervisor version `2026.03.0.dev` does not update the in-memory addon
config from `config.yaml` on `ha supervisor reload`. Workaround:

- The Rebuild task patches `/data/addons.json` directly after each rebuild.
- A full HA restart (`supervisor_run` or reboot) loads the patched file cleanly.
