"""
Unit tests for SQLite incident and HA event history.
"""

from pathlib import Path
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
