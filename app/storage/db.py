"""
Asynchronous SQLite storage for HA states, leases, and incident history.
"""

import asyncio
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import aiosqlite

logger = logging.getLogger("bilionid_ha.storage")


class Storage:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = asyncio.Lock()

    async def init_db(self) -> None:
        """Initializes tables and indexes."""
        parent_dir = self.db_path.parent
        try:
            parent_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            logger.critical(
                "Permission denied creating database directory '%s': %s. "
                "Fix on host: chmod 777 ./data or chown -R 1000:1000 ./data",
                parent_dir,
                e,
            )
            raise

        try:
            async with self._lock:
                async with aiosqlite.connect(str(self.db_path)) as db:
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
                        CREATE TABLE IF NOT EXISTS ha_lease (
                            id INTEGER PRIMARY KEY CHECK (id = 1),
                            holder_node TEXT NOT NULL,
                            acquired_at TEXT NOT NULL,
                            expires_at TEXT NOT NULL,
                            is_active INTEGER NOT NULL DEFAULT 1
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
                            resolved_at TEXT
                        );
                    """)
                    await db.commit()
        except Exception as e:
            logger.critical(
                "Failed to open/initialize SQLite database at '%s': %s. "
                "Check that directory '%s' has write permissions (chmod 777 ./data or chown -R 1000:1000 ./data).",
                self.db_path,
                e,
                parent_dir,
            )
            raise

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

    async def acquire_or_renew_lease(
        self, node_name: str, duration_sec: float
    ) -> bool:
        """Acquires or renews the lease for node_name."""
        now_dt = datetime.now(timezone.utc)
        now_str = now_dt.isoformat()
        expires_dt = datetime.fromtimestamp(now_dt.timestamp() + duration_sec, tz=timezone.utc)
        expires_str = expires_dt.isoformat()

        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT holder_node, expires_at, is_active FROM ha_lease WHERE id = 1") as cursor:
                    row = await cursor.fetchone()

                if row is None:
                    # First time lease
                    await db.execute(
                        """
                        INSERT INTO ha_lease (id, holder_node, acquired_at, expires_at, is_active)
                        VALUES (1, ?, ?, ?, 1)
                        """,
                        (node_name, now_str, expires_str),
                    )
                    await db.commit()
                    return True

                current_holder = row["holder_node"]
                current_expires = datetime.fromisoformat(row["expires_at"])
                is_active = bool(row["is_active"])

                # If same holder or previous lease expired or inactive
                if current_holder == node_name or not is_active or now_dt > current_expires:
                    await db.execute(
                        """
                        UPDATE ha_lease
                        SET holder_node = ?, acquired_at = ?, expires_at = ?, is_active = 1
                        WHERE id = 1
                        """,
                        (node_name, now_str, expires_str),
                    )
                    await db.commit()
                    return True

                return False

    async def release_lease(self, node_name: str) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    UPDATE ha_lease
                    SET is_active = 0
                    WHERE id = 1 AND holder_node = ?
                    """,
                    (node_name,),
                )
                await db.commit()

    async def get_current_lease(self) -> Optional[Dict[str, Any]]:
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT holder_node, acquired_at, expires_at, is_active FROM ha_lease WHERE id = 1"
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        return dict(row)
                    return None

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
                    SELECT id, timestamp, category, server, message, severity, resolved_at
                    FROM incidents
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
