"""
Asynchronous SQLite storage for HA states, leases, and incident history.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import aiosqlite

from app.monitoring.events import Observation


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


class Storage:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = asyncio.Lock()

    async def init_db(self) -> None:
        """Initializes tables and indexes."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("BEGIN IMMEDIATE")
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS ha_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        source_node TEXT NOT NULL,
                        target_node TEXT,
                        details TEXT
                    );
                """)
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS incidents (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        category TEXT NOT NULL,
                        server TEXT NOT NULL,
                        message TEXT NOT NULL,
                        severity TEXT NOT NULL DEFAULT 'WARNING',
                        resolved_at TEXT,
                        resolution TEXT NOT NULL DEFAULT ''
                    );
                """)
                async with db.execute("PRAGMA table_info(incidents)") as cursor:
                    columns = {row[1] for row in await cursor.fetchall()}
                if "resolution" not in columns:
                    await db.execute("ALTER TABLE incidents ADD COLUMN resolution TEXT NOT NULL DEFAULT ''")
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS observations (
                        source TEXT NOT NULL,
                        entity_id TEXT NOT NULL,
                        last_at TEXT NOT NULL,
                        incident_id INTEGER REFERENCES incidents(id),
                        PRIMARY KEY (source, entity_id)
                    )
                """)
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS alert_queue (
                        id INTEGER PRIMARY KEY,
                        incident_id INTEGER NOT NULL REFERENCES incidents(id),
                        kind TEXT NOT NULL CHECK(kind IN ('DOWN', 'UP')),
                        text TEXT NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        retry_at TEXT NOT NULL,
                        delivered_at TEXT,
                        failed_at TEXT,
                        UNIQUE(incident_id, kind)
                    )
                """)
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS beszel_uptime (
                        host_id TEXT PRIMARY KEY,
                        feed TEXT NOT NULL,
                        sampled_at TEXT NOT NULL,
                        boot_at TEXT NOT NULL
                    );
                """)
                await db.execute("""
                    CREATE INDEX IF NOT EXISTS alert_queue_pending
                    ON alert_queue(retry_at, id)
                    WHERE delivered_at IS NULL AND failed_at IS NULL
                """)
                await db.execute("CREATE INDEX IF NOT EXISTS incidents_recent ON incidents(timestamp DESC, id DESC)")
                await db.commit()

    async def apply_observation(self, observation: Observation, render) -> None:
        """Apply one ordered observation and enqueue its transition atomically."""
        seen = observation.observed_at or datetime.now(timezone.utc)
        event_at = observation.occurred_at or seen
        timestamp = utc_text(event_at)
        async with self._lock, aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            key = (observation.source, observation.entity_id)
            async with db.execute(
                "SELECT last_at, incident_id FROM observations WHERE source = ? AND entity_id = ?", key
            ) as cursor:
                state = await cursor.fetchone()
            if state is not None and timestamp <= state["last_at"]:
                return

            open_id = state["incident_id"] if state is not None else None
            if observation.state == "DOWN" and open_id is None:
                cursor = await db.execute(
                    """INSERT INTO incidents (timestamp, category, server, message, severity)
                    VALUES (?, ?, ?, ?, ?)""",
                    (timestamp, observation.source, observation.name, observation.detail, observation.severity),
                )
                open_id = cursor.lastrowid
                await db.execute(
                    "INSERT INTO alert_queue (incident_id, kind, text, retry_at) VALUES (?, 'DOWN', ?, ?)",
                    (open_id, render(observation, event_at, open_id), utc_text(seen)),
                )
            elif observation.state == "UP" and open_id is not None:
                async with db.execute("SELECT timestamp, message FROM incidents WHERE id = ?", (open_id,)) as cursor:
                    row = await cursor.fetchone()
                await db.execute(
                    "UPDATE incidents SET resolved_at = ?, resolution = ? WHERE id = ?",
                    (timestamp, observation.detail, open_id),
                )
                started = datetime.fromisoformat(row["timestamp"])
                await db.execute(
                    "INSERT INTO alert_queue (incident_id, kind, text, retry_at) VALUES (?, 'UP', ?, ?)",
                    (open_id, render(observation, event_at, open_id, started, row["message"]), utc_text(seen)),
                )
                open_id = None

            await db.execute(
                """INSERT INTO observations (source, entity_id, last_at, incident_id) VALUES (?, ?, ?, ?)
                ON CONFLICT (source, entity_id) DO UPDATE
                SET last_at = excluded.last_at, incident_id = excluded.incident_id""",
                (*key, timestamp, open_id),
            )
            await db.commit()

    async def record_event(self, category: str, server: str, message: str, at: datetime, render=None) -> int:
        """Keep a standalone event in the journal and optionally enqueue its notification."""
        async with self._lock, aiosqlite.connect(self.db_path) as db:
            event_id = await self._insert_event(db, category, server, message, at, render)
            await db.commit()
            return event_id

    async def _insert_event(self, db, category: str, server: str, message: str, at: datetime, render) -> int:
        timestamp = utc_text(at)
        cursor = await db.execute(
            """INSERT INTO incidents (timestamp, category, server, message, severity, resolved_at)
            VALUES (?, ?, ?, ?, 'INFO', ?)""",
            (timestamp, category, server, message, timestamp),
        )
        event_id = cursor.lastrowid
        if render is not None:
            await db.execute(
                "INSERT INTO alert_queue (incident_id, kind, text, retry_at) VALUES (?, 'DOWN', ?, ?)",
                (event_id, render(event_id), utc_text(datetime.now(timezone.utc))),
            )
        return event_id

    async def apply_beszel_uptime(
        self, host_id: str, feed: str, sample: datetime, boot: datetime,
        name: str, detail: str, render,
    ) -> None:
        """Record a restart and its new baseline together so it survives process restarts."""
        sampled_at, boot_at = utc_text(sample), utc_text(boot)
        async with self._lock, aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT feed, sampled_at, boot_at FROM beszel_uptime WHERE host_id = ?", (host_id,)
            ) as cursor:
                previous = await cursor.fetchone()
            if previous:
                old_feed, old_sample, old_boot = previous
                if sampled_at <= old_sample:
                    return
                # Compare samples from the same hub and allow for jitter in the estimate.
                if (
                    feed == old_feed and boot_at >= old_sample
                    and boot - datetime.fromisoformat(old_boot) > timedelta(seconds=30)
                ):
                    await self._insert_event(db, "Beszel", name, detail, boot, render)
            await db.execute(
                """INSERT INTO beszel_uptime (host_id, feed, sampled_at, boot_at) VALUES (?, ?, ?, ?)
                ON CONFLICT (host_id) DO UPDATE
                SET feed = excluded.feed, sampled_at = excluded.sampled_at, boot_at = excluded.boot_at""",
                (host_id, feed, sampled_at, boot_at),
            )
            await db.commit()

    async def next_alert(self) -> Optional[Dict[str, Any]]:
        async with self._lock, aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT id, text, attempts FROM alert_queue AS alert
                WHERE alert.delivered_at IS NULL AND alert.failed_at IS NULL AND alert.retry_at <= ?
                AND NOT EXISTS (
                    SELECT 1 FROM alert_queue AS earlier WHERE earlier.incident_id = alert.incident_id
                    AND earlier.id < alert.id AND earlier.delivered_at IS NULL AND earlier.failed_at IS NULL
                )
                ORDER BY alert.id LIMIT 1
            """, (utc_text(datetime.now(timezone.utc)),)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def open_observation_ids(self, source: str) -> set[str]:
        async with self._lock, aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT entity_id FROM observations WHERE source = ? AND incident_id IS NOT NULL", (source,)
            ) as cursor:
                return {row[0] for row in await cursor.fetchall()}

    async def update_alert_delivery(
        self, alert_id: int, *, retry_delay: float | None = None, permanent_failure: bool = False
    ) -> None:
        now = datetime.now(timezone.utc)
        delivered_at = utc_text(now) if retry_delay is None and not permanent_failure else None
        failed_at = utc_text(now) if permanent_failure else None
        retry_at = utc_text(now + timedelta(seconds=retry_delay or 0))
        async with self._lock, aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE alert_queue SET attempts = attempts + 1, delivered_at = ?, failed_at = ?, retry_at = ?
                WHERE id = ?
            """, (delivered_at, failed_at, retry_at, alert_id))
            await db.commit()

    async def delivery_counts(self) -> dict[str, int]:
        async with self._lock, aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT COUNT(*) FILTER (WHERE delivered_at IS NULL AND failed_at IS NULL),
                       COUNT(*) FILTER (WHERE failed_at IS NOT NULL)
                FROM alert_queue
            """) as cursor:
                pending, failed = await cursor.fetchone()
                return {"pending": pending, "failed": failed}

    async def record_ha_event(
        self,
        event_type: str,
        source_node: str,
        target_node: Optional[str] = None,
        details: Optional[str] = None,
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    """
                    INSERT INTO ha_events (timestamp, event_type, source_node, target_node, details)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (now, event_type, source_node, target_node, details),
                )
                await db.commit()
                return cursor.lastrowid or 0

    async def get_recent_ha_events(self, limit: int = 15) -> List[Dict[str, Any]]:
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    """
                    SELECT id, timestamp, event_type, source_node, target_node, details
                    FROM ha_events
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]

    async def record_incident(
        self,
        category: str,
        server: str,
        message: str,
        severity: str = "WARNING",
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    """
                    INSERT INTO incidents (timestamp, category, server, message, severity)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (now, category, server, message, severity),
                )
                await db.commit()
                return cursor.lastrowid or 0

    async def resolve_incident(self, incident_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "UPDATE incidents SET resolved_at = ? WHERE id = ?",
                    (now, incident_id),
                )
                await db.commit()

    async def get_active_incidents(self) -> List[Dict[str, Any]]:
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    """
                    SELECT id, timestamp, category, server, message, severity
                    FROM incidents
                    WHERE resolved_at IS NULL
                    ORDER BY id DESC
                    """
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]

    async def get_recent_incidents(self, limit: int = 10) -> List[Dict[str, Any]]:
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    """
                    SELECT id, timestamp, category, server, message, severity, resolved_at, resolution
                    FROM incidents
                    ORDER BY timestamp DESC, id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
