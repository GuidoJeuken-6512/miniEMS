"""SQLite persistence layer for miniEMS daily energy statistics.

Data survives addon restarts and updates.  All I/O is async via aiosqlite.
"""
import logging
from datetime import date, timedelta
from typing import Any

import aiosqlite

from const import DB_FILE

_LOGGER = logging.getLogger(__name__)

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
        ):
            try:
                await self._db.execute(f"ALTER TABLE daily_stats ADD COLUMN {col_def}")
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
                SUM(feed_in_revenue_eur)  AS feed_in_revenue_eur
            FROM daily_stats WHERE {where}
        """
        async with self._db.execute(sql, params) as cur:
            row = await cur.fetchone()
            if not row:
                return {}
            return {k: (v or 0.0) for k, v in dict(row).items()}
