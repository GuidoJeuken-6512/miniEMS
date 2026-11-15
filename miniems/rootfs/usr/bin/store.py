"""SQLite persistence layer for miniEMS daily energy statistics.

Data survives addon restarts and updates.  All I/O is async via aiosqlite.
"""
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

import aiosqlite

from const import DB_FILE

_LOGGER = logging.getLogger(__name__)

_CREATE_EVENT_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS event_log (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp                TEXT NOT NULL,
    entry_type               TEXT NOT NULL,
    state                    TEXT NOT NULL,
    battery_kwh_freetochange REAL DEFAULT 0,
    battery_kwh_useable      REAL DEFAULT 0,
    predicted_load_kwh       REAL,
    price_eur_kwh            REAL,
    mode                     TEXT,
    reason                   TEXT
)
"""

_CREATE_CAPABILITY_TODAY_TABLE = """
CREATE TABLE IF NOT EXISTS battery_charge_capability_today (
    date          TEXT NOT NULL,
    soc_bucket    TEXT NOT NULL,
    max_power_w   REAL NOT NULL DEFAULT 0,
    sample_count  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (date, soc_bucket)
)
"""

_CREATE_CAPABILITY_HISTORY_TABLE = """
CREATE TABLE IF NOT EXISTS battery_charge_capability_history (
    date          TEXT NOT NULL,
    soc_bucket    TEXT NOT NULL,
    max_power_w   REAL NOT NULL,
    sample_count  INTEGER NOT NULL,
    PRIMARY KEY (date, soc_bucket)
)
"""

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS daily_stats (
    date               TEXT PRIMARY KEY,
    grid_import_kwh    REAL DEFAULT 0,
    grid_cost_eur      REAL DEFAULT 0,
    pv_used_kwh        REAL DEFAULT 0,
    pv_savings_eur     REAL DEFAULT 0,
    load_total_kwh     REAL DEFAULT 0,
    load_cost_eur      REAL DEFAULT 0,
    avg_price_eur_kwh  REAL DEFAULT 0,
    avg_outdoor_temp_c REAL,
    ticks              INTEGER DEFAULT 0
)
"""


# One row per local clock hour (docs/technical/netzladen-entscheidung.md,
# "Bedarfsprognose"). hour_start is the local ISO timestamp of the hour's
# start incl. offset ("2026-10-22T14:00:00+02:00"), so DST days keep 23/25
# distinct rows. heat_pump_kwh / outdoor_temp_c are NULL when not measured.
_CREATE_HOURLY_TABLE = """
CREATE TABLE IF NOT EXISTS hourly_stats (
    hour_start      TEXT PRIMARY KEY,
    date            TEXT NOT NULL,
    hour            INTEGER NOT NULL,
    load_kwh        REAL NOT NULL DEFAULT 0,
    heat_pump_kwh   REAL,
    outdoor_temp_c  REAL,
    ticks           INTEGER NOT NULL DEFAULT 0
)
"""


# Heat pump / hot water (docs/technical/warmwasser-steuerung.md). One row
# per local hour and raw operating-mode code; cop_* only counts ticks whose
# COP survived the settle/plausibility filter.
_CREATE_HP_HOURLY_TABLE = """
CREATE TABLE IF NOT EXISTS heat_pump_hourly (
    hour_start      TEXT NOT NULL,
    mode            INTEGER NOT NULL,
    date            TEXT NOT NULL,
    hour            INTEGER NOT NULL,
    ticks           INTEGER NOT NULL DEFAULT 0,
    cop_ticks       INTEGER NOT NULL DEFAULT 0,
    cop_avg         REAL,
    outdoor_temp_c  REAL,
    boil_temp_c     REAL,
    heat_pump_kwh   REAL,
    PRIMARY KEY (hour_start, mode)
)
"""

_CREATE_HW_LOSS_TABLE = """
CREATE TABLE IF NOT EXISTS hot_water_loss_segments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    start_ts            TEXT NOT NULL,
    end_ts              TEXT NOT NULL,
    duration_h          REAL NOT NULL,
    start_temp_c        REAL NOT NULL,
    end_temp_c          REAL NOT NULL,
    k_per_h             REAL NOT NULL,
    avg_boil_temp_c     REAL,
    avg_outdoor_temp_c  REAL,
    end_reason          TEXT NOT NULL
)
"""

_CREATE_HW_DRAWS_TABLE = """
CREATE TABLE IF NOT EXISTS hot_water_draws (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    temp_before_c   REAL NOT NULL,
    temp_after_c    REAL NOT NULL,
    drop_k          REAL NOT NULL
)
"""

# A8: one row per finished hot-water charge – heat counted by the heat pump
# against the heat that arrived in the boiler.
_CREATE_HW_CHARGES_TABLE = """
CREATE TABLE IF NOT EXISTS hot_water_charges (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    start_ts         TEXT NOT NULL,
    end_ts           TEXT NOT NULL,
    start_c          REAL NOT NULL,
    end_c            REAL NOT NULL,
    heat_lambda_kwh  REAL NOT NULL,
    heat_boiler_kwh  REAL NOT NULL,
    from_running     INTEGER NOT NULL,
    valid            INTEGER NOT NULL
)
"""

# B6/B7: small persistent state that must survive a restart (compressor
# lock of the heating offset, ...). value is JSON.
_CREATE_KV_TABLE = """
CREATE TABLE IF NOT EXISTS kv_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


class EnergyStore:
    """Async SQLite wrapper for daily energy statistics."""

    def __init__(self) -> None:
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._db = await aiosqlite.connect(DB_FILE)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute(_CREATE_TABLE)
        # Add columns if missing (upgrade for existing DBs)
        for col_def in (
            "peak_pv_w REAL DEFAULT 0",
            "grid_charge_kwh REAL DEFAULT 0",
            "grid_charge_cost_eur REAL DEFAULT 0",
            "feed_in_kwh REAL DEFAULT 0",
            "feed_in_revenue_eur REAL DEFAULT 0",
            "last_flush_ts TEXT",
            "kwh_high_rate REAL DEFAULT 0",
            "kwh_medium_rate REAL DEFAULT 0",
            "kwh_low_rate REAL DEFAULT 0",
            "grid_charge_kwh_bilanz REAL DEFAULT 0",
            "efficiency_pct REAL DEFAULT 0",
            # Lifetime-counter readings at our own local midnight. NULL (not 0)
            # when no lifetime counter is configured – 0 would be a valid anchor.
            "anchor_grid_import_kwh REAL",
            "anchor_feed_in_kwh REAL",
            "anchor_load_total_kwh REAL",
            # Discharge-weighted price: Σ(kWh) and Σ(kWh × price) while the
            # battery discharges. Their quotient is what a stored kWh displaces.
            "discharge_kwh_ticks REAL DEFAULT 0",
            "discharge_value_eur REAL DEFAULT 0",
            # A11: daily maxima – when was the battery last full, the boiler
            # last at hygiene temperature? NULL = not recorded that day.
            "max_soc_pct REAL",
            "max_boil_temp_c REAL",
            # A7a: battery energy per day from the power sensor (ticks), the
            # SoC at the first and last tick, and the discharge efficiency
            # numerator/denominator (house load ÷ battery output while the
            # battery alone supplies the house).
            "bat_in_kwh REAL",
            "bat_out_kwh REAL",
            "soc_first_pct REAL",
            "soc_last_pct REAL",
            "dis_eff_load_kwh REAL",
            "dis_eff_bat_kwh REAL",
            # C4: plan fidelity – minutes observed / kept per day.
            "plan_minutes INTEGER",
            "plan_kept_minutes INTEGER",
        ):
            try:
                await self._db.execute(f"ALTER TABLE daily_stats ADD COLUMN {col_def}")
            except Exception:
                pass   # column already exists – ignore
        await self._db.execute(_CREATE_HOURLY_TABLE)
        # A7b/c: measured PV and what was forecast for the hour – plan vs. result.
        for col_def in ("pv_kwh REAL", "pv_fc_kwh REAL", "load_pred_kwh REAL",
                        # B7: room temperature, share of the hour the heat pump
                        # heated, mean flow-line offset.
                        "room_temp_c REAL", "heating_frac REAL", "flow_offset_k REAL",
                        # C5: mean price of the hour, for the savings baseline.
                        "price_eur_kwh REAL"):
            try:
                await self._db.execute(f"ALTER TABLE hourly_stats ADD COLUMN {col_def}")
            except Exception:
                pass   # column already exists – ignore
        await self._db.execute(_CREATE_HP_HOURLY_TABLE)
        await self._db.execute(_CREATE_KV_TABLE)
        await self._db.execute(_CREATE_HW_LOSS_TABLE)
        await self._db.execute(_CREATE_HW_DRAWS_TABLE)
        await self._db.execute(_CREATE_HW_CHARGES_TABLE)
        await self._db.execute(_CREATE_CAPABILITY_TODAY_TABLE)
        await self._db.execute(_CREATE_CAPABILITY_HISTORY_TABLE)
        await self._db.execute(_CREATE_EVENT_LOG_TABLE)
        # Add columns if missing (upgrade for existing DBs)
        for col_def in (
            "mode TEXT", "reason TEXT",
            # write_confirm entries only – see InverterController.pop_write_events()
            "write_channel TEXT",
            "write_latency_sec REAL",
            "write_outcome TEXT",
        ):
            try:
                await self._db.execute(f"ALTER TABLE event_log ADD COLUMN {col_def}")
            except Exception:
                pass   # column already exists – ignore
        await self._db.commit()
        _LOGGER.info("EnergyStore opened: %s", DB_FILE)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def upsert_day(self, day: date, fields: dict[str, Any]) -> None:
        """Insert or update a day's accumulated statistics."""
        if not self._db:
            return
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" * len(fields))
        updates = ", ".join(f"{k} = excluded.{k}" for k in fields)
        sql = (
            f"INSERT INTO daily_stats (date, {cols}) VALUES (?, {placeholders}) "
            f"ON CONFLICT(date) DO UPDATE SET {updates}"
        )
        await self._db.execute(sql, [str(day), *fields.values()])
        await self._db.commit()

    async def upsert_hour(self, hour_start: str, fields: dict[str, Any]) -> None:
        """Insert or update one hour's row in hourly_stats.

        `fields` must carry `date` and `hour` on first insert; later updates
        may send only the changing columns.
        """
        if not self._db:
            return
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" * len(fields))
        updates = ", ".join(f"{k} = excluded.{k}" for k in fields)
        sql = (
            f"INSERT INTO hourly_stats (hour_start, {cols}) VALUES (?, {placeholders}) "
            f"ON CONFLICT(hour_start) DO UPDATE SET {updates}"
        )
        await self._db.execute(sql, [hour_start, *fields.values()])
        await self._db.commit()

    async def set_state(self, key: str, value: Any) -> None:
        """B6: persist a small JSON value under ``key``."""
        if not self._db:
            return
        await self._db.execute(
            "INSERT INTO kv_state (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            [key, json.dumps(value), datetime.now(timezone.utc).isoformat(timespec="seconds")],
        )
        await self._db.commit()

    async def get_state(self, key: str) -> Any:
        """B6: the JSON value stored under ``key``, or None."""
        if not self._db:
            return None
        async with self._db.execute("SELECT value FROM kv_state WHERE key = ?", [key]) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["value"])
        except (ValueError, TypeError):
            return None

    async def query_hours_since(self, since_date: date) -> list[dict[str, Any]]:
        """Every hourly_stats row with date >= since_date, oldest first."""
        if not self._db:
            return []
        async with self._db.execute(
            "SELECT * FROM hourly_stats WHERE date >= ? ORDER BY hour_start ASC",
            [str(since_date)],
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Heat pump / hot water
    # ------------------------------------------------------------------

    async def upsert_hp_hour(self, hour_start: str, mode: int, fields: dict[str, Any]) -> None:
        """Insert or update one (hour, mode) row in heat_pump_hourly."""
        if not self._db:
            return
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" * len(fields))
        updates = ", ".join(f"{k} = excluded.{k}" for k in fields)
        sql = (
            f"INSERT INTO heat_pump_hourly (hour_start, mode, {cols}) "
            f"VALUES (?, ?, {placeholders}) "
            f"ON CONFLICT(hour_start, mode) DO UPDATE SET {updates}"
        )
        await self._db.execute(sql, [hour_start, mode, *fields.values()])
        await self._db.commit()

    async def query_hp_hours_since(self, since_date: date) -> list[dict[str, Any]]:
        """Every heat_pump_hourly row with date >= since_date, oldest first."""
        if not self._db:
            return []
        async with self._db.execute(
            "SELECT * FROM heat_pump_hourly WHERE date >= ? ORDER BY hour_start ASC, mode ASC",
            [str(since_date)],
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def insert_loss_segment(self, fields: dict[str, Any]) -> None:
        if not self._db:
            return
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" * len(fields))
        await self._db.execute(
            f"INSERT INTO hot_water_loss_segments ({cols}) VALUES ({placeholders})",
            list(fields.values()),
        )
        await self._db.commit()

    async def query_loss_segments(self, limit: int = 500) -> list[dict[str, Any]]:
        """Most recent standby-loss segments, newest first."""
        if not self._db:
            return []
        async with self._db.execute(
            "SELECT * FROM hot_water_loss_segments ORDER BY id DESC LIMIT ?", [limit],
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def insert_draw(self, ts: str, temp_before_c: float, temp_after_c: float) -> None:
        if not self._db:
            return
        await self._db.execute(
            "INSERT INTO hot_water_draws (ts, temp_before_c, temp_after_c, drop_k) "
            "VALUES (?, ?, ?, ?)",
            [ts, temp_before_c, temp_after_c, round(temp_before_c - temp_after_c, 2)],
        )
        await self._db.commit()

    async def insert_hw_charge(self, fields: dict[str, Any]) -> None:
        if not self._db:
            return
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        await self._db.execute(f"INSERT INTO hot_water_charges ({cols}) VALUES ({marks})",
                               list(fields.values()))
        await self._db.commit()

    async def query_hw_charges(self, limit: int = 200) -> list[dict[str, Any]]:
        """Most recent hot-water charges, newest first."""
        if not self._db:
            return []
        async with self._db.execute(
            "SELECT * FROM hot_water_charges ORDER BY id DESC LIMIT ?", (limit,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def query_draws(self, limit: int = 500) -> list[dict[str, Any]]:
        """Most recent hot-water draws, newest first."""
        if not self._db:
            return []
        async with self._db.execute(
            "SELECT * FROM hot_water_draws ORDER BY id DESC LIMIT ?", [limit],
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def load_day(self, day: date) -> dict[str, Any]:
        """Return a single day's row as dict, or empty dict if not found."""
        if not self._db:
            return {}
        async with self._db.execute(
            "SELECT * FROM daily_stats WHERE date = ?", [str(day)]
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else {}

    async def query_month(self, year_month: str) -> dict[str, float]:
        """Return summed stats for a month (format: 'YYYY-MM')."""
        if not self._db:
            return {}
        pattern = f"{year_month}-%"
        return await self._aggregate("date LIKE ?", [pattern])

    async def query_year(self, year: int) -> dict[str, float]:
        """Return summed stats for a full year."""
        if not self._db:
            return {}
        return await self._aggregate("date LIKE ?", [f"{year}-%"])

    async def query_recent_days(self, n: int) -> list[dict[str, Any]]:
        """Return last N days of rows, newest first (for week stats & model)."""
        if not self._db:
            return []
        cutoff = str(date.today() - timedelta(days=n))
        async with self._db.execute(
            "SELECT * FROM daily_stats WHERE date >= ? ORDER BY date DESC", [cutoff]
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def last_day_at_least(self, column: str, value: float) -> date | None:
        """A11: the most recent day whose `column` reached `value`, or None."""
        if column not in ("max_soc_pct", "max_boil_temp_c"):
            raise ValueError(column)
        if self._db is None:
            return None
        async with self._db.execute(
            f"SELECT MAX(date) FROM daily_stats WHERE {column} >= ?", (value,)
        ) as cur:
            row = await cur.fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None

    async def first_day_with(self, column: str) -> date | None:
        """A11: the first day on which `column` was recorded at all."""
        if column not in ("max_soc_pct", "max_boil_temp_c"):
            raise ValueError(column)
        if self._db is None:
            return None
        async with self._db.execute(
            f"SELECT MIN(date) FROM daily_stats WHERE {column} IS NOT NULL"
        ) as cur:
            row = await cur.fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None

    async def query_all_days(self) -> list[dict[str, Any]]:
        """Return every row in daily_stats, newest first."""
        if not self._db:
            return []
        async with self._db.execute(
            "SELECT * FROM daily_stats ORDER BY date DESC"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def query_days_similar_temp(
        self, target_temp_c: float, tolerance: float, lookback_days: int
    ) -> list[dict[str, Any]]:
        """Return days within ±tolerance °C of target_temp_c from the last lookback_days."""
        if not self._db:
            return []
        cutoff = str(date.today() - timedelta(days=lookback_days))
        async with self._db.execute(
            """SELECT * FROM daily_stats
               WHERE date >= ?
                 AND avg_outdoor_temp_c IS NOT NULL
                 AND avg_outdoor_temp_c BETWEEN ? AND ?
               ORDER BY date DESC""",
            [cutoff, target_temp_c - tolerance, target_temp_c + tolerance],
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Battery charge capability (gelernte Ladeleistung) – see
    # battery_capability.py for the full rationale.
    # ------------------------------------------------------------------

    async def upsert_capability_today(
        self, day: date, bucket: str, max_power_w: float, sample_count: int
    ) -> None:
        """Insert or overwrite one bucket's running max for `day`."""
        if not self._db:
            return
        await self._db.execute(
            """INSERT INTO battery_charge_capability_today
                   (date, soc_bucket, max_power_w, sample_count)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(date, soc_bucket) DO UPDATE SET
                   max_power_w = excluded.max_power_w,
                   sample_count = excluded.sample_count""",
            [str(day), bucket, max_power_w, sample_count],
        )
        await self._db.commit()

    async def rollover_capability_day(self, day: date, min_samples: int) -> None:
        """Copy `day`'s qualifying buckets into permanent history, then drop
        that day's row from the temporary table (a new day's ticks start it
        fresh regardless)."""
        if not self._db:
            return
        await self._db.execute(
            """INSERT INTO battery_charge_capability_history
                   (date, soc_bucket, max_power_w, sample_count)
               SELECT date, soc_bucket, max_power_w, sample_count
               FROM battery_charge_capability_today
               WHERE date = ? AND sample_count >= ?
               ON CONFLICT(date, soc_bucket) DO UPDATE SET
                   max_power_w = excluded.max_power_w,
                   sample_count = excluded.sample_count""",
            [str(day), min_samples],
        )
        await self._db.execute(
            "DELETE FROM battery_charge_capability_today WHERE date = ?", [str(day)]
        )
        await self._db.commit()

    async def query_capability_history(
        self, bucket: str, lookback_days: int, min_samples: int
    ) -> list[float]:
        """max_power_w of every qualifying day for `bucket`, newest first."""
        if not self._db:
            return []
        cutoff = str(date.today() - timedelta(days=lookback_days))
        async with self._db.execute(
            """SELECT max_power_w FROM battery_charge_capability_history
               WHERE soc_bucket = ? AND date >= ? AND sample_count >= ?
               ORDER BY date DESC""",
            [bucket, cutoff, min_samples],
        ) as cur:
            rows = await cur.fetchall()
            return [r["max_power_w"] for r in rows]

    # ------------------------------------------------------------------
    # Event log
    # ------------------------------------------------------------------

    async def append_event(self, entry: dict) -> None:
        """Persist one log entry to the event_log table."""
        if not self._db:
            return
        await self._db.execute(
            """INSERT INTO event_log
               (timestamp, entry_type, state, battery_kwh_freetochange,
                battery_kwh_useable, predicted_load_kwh, price_eur_kwh, mode, reason,
                write_channel, write_latency_sec, write_outcome)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                entry["timestamp"], entry["entry_type"], entry["state"],
                entry["battery_kwh_freetochange"], entry["battery_kwh_useable"],
                entry.get("predicted_load_kwh"), entry.get("price_eur_kwh"),
                entry.get("mode"), entry.get("reason"),
                entry.get("write_channel"), entry.get("write_latency_sec"),
                entry.get("write_outcome"),
            ],
        )
        await self._db.commit()

    async def load_recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return up to `limit` log entries, newest first."""
        if not self._db:
            return []
        async with self._db.execute(
            "SELECT * FROM event_log ORDER BY id DESC LIMIT ?", [limit]
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def cleanup_event_log(self, retention_days: int) -> int:
        """Delete entries older than retention_days. Returns number of rows deleted."""
        if not self._db:
            return 0
        async with self._db.execute(
            "DELETE FROM event_log WHERE timestamp < datetime('now', ? || ' days')",
            [f"-{retention_days}"],
        ) as cur:
            deleted = cur.rowcount
        await self._db.commit()
        return deleted

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _aggregate(self, where: str, params: list) -> dict[str, float]:
        sql = f"""
            SELECT
                SUM(grid_import_kwh)      AS grid_import_kwh,
                SUM(grid_cost_eur)        AS grid_cost_eur,
                SUM(pv_used_kwh)          AS pv_used_kwh,
                SUM(pv_savings_eur)       AS pv_savings_eur,
                SUM(load_total_kwh)       AS load_total_kwh,
                SUM(load_cost_eur)        AS load_cost_eur,
                SUM(grid_charge_kwh)      AS grid_charge_kwh,
                SUM(grid_charge_cost_eur) AS grid_charge_cost_eur,
                SUM(feed_in_kwh)          AS feed_in_kwh,
                SUM(feed_in_revenue_eur)  AS feed_in_revenue_eur,
                SUM(kwh_high_rate)        AS kwh_high_rate,
                SUM(kwh_medium_rate)      AS kwh_medium_rate,
                SUM(kwh_low_rate)         AS kwh_low_rate
            FROM daily_stats WHERE {where}
        """
        async with self._db.execute(sql, params) as cur:
            row = await cur.fetchone()
            if not row:
                return {}
            return {k: (v or 0.0) for k, v in dict(row).items()}
