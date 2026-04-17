---
revision_date: 2026-04-11
---

# Devcontainer – Supervisor Patches

This document records three bugs that prevent miniEMS (and any local add-on) from running correctly inside the Home Assistant devcontainer (`ghcr.io/home-assistant/devcontainer:2-addons`). All three stem from the same root cause: the supervisor assumes it runs on Home Assistant OS (HAOS), not inside a Docker-in-Docker container without systemd.

Each section explains the symptom, root cause, and the patch applied. All patches are applied automatically on every container start via `postStartCommand` in `.devcontainer.json`.

---

## 1. HA Core fails to start (`/run/supervisor` missing)

**Symptom**

```
ERROR [supervisor.docker.manager] Can't create container from
  ghcr.io/home-assistant/qemux86-64-homeassistant:...:
  [400] invalid mount config for type "bind":
  bind source path does not exist: /run/supervisor
```

**Root cause**

The supervisor bind-mounts `/run/supervisor` from the host into the HA Core container so that Core can expose its Unix socket there. In HAOS this directory is created by the OS init process. In the devcontainer it never exists.

**Fix**

Create the directory before `supervisor_run` is called:

```bash
mkdir -p /run/supervisor
```

Added to the front of `postStartCommand` in `.devcontainer.json`.

---

## 2. Add-on installation blocked (`docker_gateway_unprotected`)

**Symptom**

```
WARNING [supervisor.jobs] 'AddonManager.install' blocked from execution,
  system is not healthy - docker_gateway_unprotected
```

**Root cause**

Supervisor PR [home-assistant/supervisor#6650](https://github.com/home-assistant/supervisor/pull/6650) introduced a gateway firewall check. It applies iptables rules via a **systemd transient unit** (D-Bus call to `org.freedesktop.systemd1`). In the devcontainer, systemd is not running — only the D-Bus daemon is. The D-Bus call fails, the supervisor marks the system unhealthy, and all add-on operations are blocked.

The check is in `supervisor/host/firewall.py`, method `apply_gateway_firewall_rules`.

**Fix — `supervisor_firewall_patch.py`**

Skip the entire gateway firewall check when `SUPERVISOR_DEV=1` (the env var already set in every devcontainer run):

```python
async def apply_gateway_firewall_rules(self) -> None:
    if self.sys_dev:          # <-- added
        _LOGGER.info("Skipping gateway firewall rules in developer mode (SUPERVISOR_DEV=1)")
        return
    ...
```

`self.sys_dev` is the `CoreSysAttributes.sys_dev` shortcut, which reads `os.environ.get("SUPERVISOR_DEV") == "1"`.

> **Note:** `self.sys_core.dev` (the `Core` sub-object) does **not** have this property; use `self.sys_dev` or `self.coresys.dev`.

The patched file is stored in the repo as `supervisor_firewall_patch.py` and copied into the supervisor container on every start.

---

## 3. REST calls to HA Core return 502 (`/run/os/core.sock` missing)

**Symptom**

```
WARNING ha_ws_client – REST error [502]: Bad Gateway

DEBUG [supervisor.homeassistant.api] Error on call http://localhost/api/core/state:
  Cannot connect to unix socket /run/os/core.sock ssl:False [No such file or directory]
```

**Root cause**

Modern HA Core versions (≥ 2024.11) support a Unix socket transport for supervisor–core communication. When `supports_unix_socket` is True, the supervisor:

1. Sets `SUPERVISOR_CORE_API_SOCKET=/run/supervisor/core.sock` in the HA Core container environment.
2. HA Core creates the socket at `/run/supervisor/core.sock` (bind-mounted from the devcontainer host at `/run/supervisor/`).
3. The supervisor connects to the socket via the **host-side** path `SOCKET_CORE = Path("/run/os/core.sock")`.

In the devcontainer, `/run/os/` does not exist and the supervisor container does not have `/run/supervisor` in its bind-mount list (only `/run/docker.sock`, `/run/dbus`, `/run/udev`). The socket exists on the devcontainer host but is invisible to the supervisor container. The proxy's pre-flight `check_api_state()` call fails, and the proxy raises HTTP 502 for every add-on API request.

**Two-part fix**

**Part A — `supervisor_api_patch.py`**: Force TCP mode when `SUPERVISOR_DEV=1` by short-circuiting `use_unix_socket`:

```python
@property
def use_unix_socket(self) -> bool:
    if self.sys_dev or not self.supports_unix_socket:  # <-- sys_dev added
        return False
    ...
```

This makes the supervisor use `http://homeassistant:8123` (TCP on the hassio bridge network) instead of the Unix socket. HA Core's API is reachable via TCP from the supervisor container at `172.30.32.1:8123`.

**Part B — `/run/os/core.sock` symlink**: Created on the devcontainer host as a belt-and-suspenders measure for any code path that bypasses the `use_unix_socket` guard:

```bash
mkdir -p /run/os
ln -sf /run/supervisor/core.sock /run/os/core.sock
```

Both are applied in `postStartCommand`.

---

## Applying the patches

The `postStartCommand` in `.devcontainer.json` calls `apply_supervisor_patch()` in the background after the supervisor container starts. The function:

1. Waits up to 60 s for `hassio_supervisor` to appear in `docker ps`.
2. Copies `supervisor_firewall_patch.py` → `supervisor/host/firewall.py` inside the container.
3. Copies `supervisor_api_patch.py` → `supervisor/homeassistant/api.py` inside the container.
4. Deletes the corresponding `.pyc` bytecode files.
5. Creates the `/run/os/core.sock` symlink on the host.
6. Restarts `hassio_supervisor` so the patched code is loaded.

The patches survive container restarts within the same devcontainer session. They are **lost** if the supervisor image is updated (the container is recreated), but `postStartCommand` re-applies them automatically on the next start.

---

## Files changed

| File | Purpose |
|------|---------|
| `.devcontainer.json` | `mkdir -p /run/supervisor`, `apply_supervisor_patch()` wired into `postStartCommand` |
| `supervisor_firewall_patch.py` | Patched `host/firewall.py` — skips gateway check in dev mode |
| `supervisor_api_patch.py` | Patched `homeassistant/api.py` — forces TCP transport in dev mode |

---

## Upstream status

- `docker_gateway_unprotected` devcontainer incompatibility is tracked in [home-assistant/supervisor#6650](https://github.com/home-assistant/supervisor/pull/6650) (comment by AlCalzone).
- Unix socket fallback for devcontainers has no upstream issue filed as of 2026-04-11.

Once the upstream supervisor adds a proper devcontainer escape hatch, these patches can be removed.
