---
revision_date: 2026-03-14
---

# Event Log

The **Log** tab (`/log`) provides a chronological, auto-refreshing event journal for miniEMS. It captures every significant state transition in a single unified view, making it easy to understand *why* the system acted the way it did.

---

## Summary Bar

At the top of the page a live summary bar always shows the current system state:

| Field | Description |
|---|---|
| Mode | Current EMS operating mode (badge) |
| Battery SoC | Current state-of-charge (%) |
| Free to Charge | Battery headroom available (kWh) |
| Solcast Remaining | PV energy still expected today (kWh) |
| Price | Current electricity price – highlighted green on cheap rate |

---

## Event Table

Below the summary bar the event table lists up to the **last 100 events**, newest first. Two types of events appear in the same chronological stream:

### Mode Change Events

Recorded every time the EMS switches between operating modes.

| Column | Description |
|---|---|
| Event | **▲ ON** (grid charging started) or **▼ OFF** (grid charging stopped) — shown in green / grey |
| Time | ISO 8601 timestamp of the transition |
| Price (€/kWh) | `–` (not applicable to mode changes) |
| Free (kWh) | Battery free-to-charge headroom at the moment of the change |
| Useable (kWh) | Battery useable energy at the moment of the change |
| Pred. Load (kWh) | Predicted daily household load at the moment of the change |

### Price Change Events

Recorded every time the electricity price sensor reports a new value that differs from the previous one. This lets you correlate grid-charge decisions with the exact price steps that triggered them.

| Column | Description |
|---|---|
| Event | **€ Price Change** — shown in amber |
| Time | ISO 8601 timestamp of the price update |
| Price (€/kWh) | The **new** electricity price |
| Free (kWh) | Battery free-to-charge headroom at the time |
| Useable (kWh) | Battery useable energy at the time |
| Pred. Load (kWh) | Predicted daily load at the time |

!!! tip "Reading the log"
    Look for a **€ Price Change** entry just before a **▲ ON** entry — that is the price drop that crossed the cheap-rate threshold and triggered grid charging. Similarly, a price increase followed by a **▼ OFF** entry explains why charging stopped.

---

## Event Capacity

The in-memory buffer holds the **last 100 events** (mode changes and price changes combined). Older events are silently evicted from the buffer. The hint line at the top of the table shows the current count.

---

## Persistence

Events are written to the `event_log` table in `/data/miniems.db` on every append. On startup the last 100 entries are restored from the database so the log is **not lost across restarts or updates**.

Old entries are pruned once per day based on the **Event Log Retention** setting (default: 30 days). See [Configuration](configuration.md#ems-parameters).

---

## Auto-Refresh

The page polls `/api/status` every **5 seconds** and re-renders the table in place. No manual reload is needed.

---

## Implementation Notes

Price changes are only recorded when the price *differs* from the previously observed value. The very first reading after startup is not logged as a change; subsequent readings are compared against the running value.

For the database schema of the `event_log` table, see [Data Storage](../technical/data-storage.md#event_log-table).
