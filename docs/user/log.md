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

The ring buffer holds the **last 100 events** (mode changes and price changes combined). Older events are silently dropped. The hint line at the top of the table shows the current count.

---

## Auto-Refresh

The page polls `/api/status` every **5 seconds** and re-renders the table in place. No manual reload is needed.

---

## Implementation Notes

Events are kept entirely in memory — they are not persisted to the SQLite database and are lost on addon restart. For persistent cost and energy history, see [Data Storage](../technical/data-storage.md).

Price changes are only recorded when the price *differs* from the previously observed value. The very first reading after startup is not logged as a change; subsequent readings are compared against the running value.
