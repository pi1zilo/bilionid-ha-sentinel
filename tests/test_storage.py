"""
Unit tests for SQLite incident and HA event history.
"""

from pathlib import Path
import sqlite3

import pytest

from app.storage.db import Storage


@pytest.mark.asyncio
async def test_ha_event_and_incident_recording(tmp_path: Path):
    db_path = tmp_path / "test_state.db"
    storage = Storage(db_path)
    await storage.init_db()

    # Record HA Event
    ev_id = await storage.record_ha_event(
        event_type="FAILOVER_TAKEOVER",
        source_node="SUSLIKVPN",
        target_node="BilioniD-factories",
        details="Test failover",
    )
    assert ev_id > 0

    events = await storage.get_recent_ha_events(limit=5)
    assert len(events) == 1
    assert events[0]["event_type"] == "FAILOVER_TAKEOVER"

    # Record Incident
    inc_id = await storage.record_incident(
        category="NETWORK",
        server="BilioniD-factories",
        message="Peer lost",
        severity="CRITICAL",
    )
    assert inc_id > 0

    active_incidents = await storage.get_active_incidents()
    assert len(active_incidents) == 1
    assert active_incidents[0]["id"] == inc_id

    # Resolve Incident
    await storage.resolve_incident(inc_id)
    active_incidents_after = await storage.get_active_incidents()
    assert len(active_incidents_after) == 0


async def test_legacy_database_keeps_incidents_when_adding_resolution(tmp_path):
    path = tmp_path / "test_state.db"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE incidents (
            id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, category TEXT NOT NULL,
            server TEXT NOT NULL, message TEXT NOT NULL, severity TEXT NOT NULL, resolved_at TEXT
        )""")
        db.execute("""INSERT INTO incidents VALUES (
            1, '2026-11-21T10:00:00+00:00', 'HA', 'main', 'HA API', 'WARNING', NULL
        )""")
    db.close()
    storage = Storage(path)
    await storage.init_db()
    await storage.init_db()
    incident, = await storage.get_recent_incidents()
    assert incident["message"] == "HA API"
    assert incident["resolution"] == ""
