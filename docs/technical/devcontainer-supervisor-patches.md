---
revision_date: 2026-08-14
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

Part B's symlink is host-side only; the `hassio_supervisor` container never had `/run/os` in its own mount list, so on its own it never actually reached the supervisor process. Part A (the API patch) is the one that matters.

---

## 4. `supervisor_run` silently exits before the supervisor ever starts (no TTY)

**Symptom**

Running `supervisor_run` non-interactively (e.g. from `postStartCommand`, which has no controlling terminal) prints:

```
Waiting for Docker to initialize...
stty: 'standard input': Inappropriate ioctl for device
```

...and then just stops. Docker is running fine, but `hassio_supervisor` never gets created — no error, no stack trace, nothing.

**Root cause**

`start_docker()` in `/etc/supervisor_scripts/common` ends with `stty sane` to reset the terminal after `dockerd`'s output. The whole script (and the `common` file it sources) runs under `set -e`. When there is no TTY on stdin, `stty sane` exits non-zero, and `set -e` kills the script right there — before `run_supervisor` is ever called. This is invisible when running the task from an interactive VS Code terminal (which has a TTY), which is why it went unnoticed for a long time; it only bites in non-interactive invocations such as `postStartCommand`.

**Fix**

Make the call non-fatal: `stty sane 2>/dev/null || true`.

---

## Applying the patches (current mechanism)

As of v1.5.6, the patches are applied statically, not at runtime:

1. **`devcontainer_bootstrap`** (runs once, via `postCreateCommand`) patches three things in the container image, before the supervisor is ever started:
   - `/usr/bin/supervisor_run` gets `supervisor_firewall_patch.py` and `supervisor_api_patch.py` added as **read-only bind mounts** onto `supervisor/host/firewall.py` and `supervisor/homeassistant/api.py` inside the `hassio_supervisor` container's `docker run` command. The patched code is active from the very first boot — no `docker cp` + restart dance needed.
   - `/etc/supervisor_scripts/common` gets the `stty sane` fix from section 4 above.
   - Both patches are idempotent (safe to re-run `bash devcontainer_bootstrap` by hand).
2. **`postStartCommand`** (runs on every container start/resume) creates `/run/supervisor` and `/run/os` (with `sudo` — `/run` is root-owned, a plain `mkdir` fails silently otherwise) and the `/run/os/core.sock` symlink, then starts `supervisor_run` in the background via `nohup` if `hassio_supervisor` isn't already running — so Home Assistant comes up automatically when the devcontainer starts, same as before v1.5.6.

`apply_supervisor_patch.sh` and the old `docker cp`-based flow described above are no longer wired into the startup path; they're kept as a manual fallback (e.g. to hot-patch a running supervisor without restarting the devcontainer).

---

## Files changed

| File | Purpose |
|------|---------|
| `.devcontainer.json` | `postStartCommand`: `sudo mkdir` for `/run/supervisor` + `/run/os`, `core.sock` symlink, auto-starts `supervisor_run` in the background if not already running |
| `devcontainer_bootstrap` | `postCreateCommand`: bind-mounts both patch files into `supervisor_run`'s `docker run`, patches the `stty sane` bug in `/etc/supervisor_scripts/common` |
| `supervisor_firewall_patch.py` | Patched `host/firewall.py` — skips gateway check in dev mode |
| `supervisor_api_patch.py` | Patched `homeassistant/api.py` — forces TCP transport in dev mode |
| `apply_supervisor_patch.sh` | Manual fallback: hot-patches and restarts an already-running `hassio_supervisor` without recreating the devcontainer |

---

## Upstream status

- `docker_gateway_unprotected` devcontainer incompatibility is tracked in [home-assistant/supervisor#6650](https://github.com/home-assistant/supervisor/pull/6650) (comment by AlCalzone).
- Unix socket fallback for devcontainers has no upstream issue filed as of 2026-04-11.

Once the upstream supervisor adds a proper devcontainer escape hatch, these patches can be removed.
