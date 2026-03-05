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
├── docs/                         # This documentation
└── rootfs/
    └── usr/
        └── bin/
            ├── main.py           # Entry point
            ├── config_loader.py  # Config + migration
            ├── migration.py      # Schema migrations
            ├── ha_ws_client.py   # HA state reader
            ├── ha_sensor_publisher.py  # HA state writer
            ├── ems_controller.py # Mode decision logic
            ├── cost_optimizer.py # Cost/savings accumulator
            ├── web_server.py     # FastAPI dashboard
            ├── templates/
            │   └── dashboard.html
            └── static/
                └── style.css
```

## Adding a New Config Field

1. Add the field (with default) to the `Config` dataclass in `config_loader.py`
2. Add it to `options:` and `schema:` in `config.yaml`
3. If renaming or transforming an existing field, add a migration step in
   `migration.py` and increment `CURRENT_VERSION`

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

## Known Supervisor Dev Issue

The Supervisor version `2026.03.0.dev` does not update the in-memory addon
config from `config.yaml` on `ha supervisor reload`. Workaround:

- The Rebuild task patches `/data/addons.json` directly after each rebuild.
- A full HA restart (`supervisor_run` or reboot) loads the patched file cleanly.
