"""
Unit tests for HA lease mechanics and SQLite storage.
"""

import asyncio
from pathlib import Path
import pytest

from app.storage.db import Storage


@pytest.mark.asyncio
async def test_lease_acquisition_and_renewal(tmp_path: Path):
    db_path = tmp_path / "test_state.db"
    storage = Storage(db_path)
    await storage.init_db()

    # 1. Main acquires lease for 1.0 second
    acquired = await storage.acquire_or_renew_lease("primary-node", duration_sec=1.0)
    assert acquired is True

    current = await storage.get_current_lease()
    assert current is not None
    assert current["holder_node"] == "primary-node"
    assert current["is_active"] == 1

    # 2. Main renews lease
    renewed = await storage.acquire_or_renew_lease("primary-node", duration_sec=1.0)
    assert renewed is True

    # 3. Backup attempts to acquire active unexpired lease -> rejected!
    backup_attempt = await storage.acquire_or_renew_lease("backup-node", duration_sec=1.0)
    assert backup_attempt is False

    # 4. Wait for lease to expire (>1.1s)
    await asyncio.sleep(1.1)

    # 5. Backup acquires expired lease -> succeeds!
    backup_acquired = await storage.acquire_or_renew_lease("backup-node", duration_sec=1.0)
    assert backup_acquired is True

    current2 = await storage.get_current_lease()
    assert current2["holder_node"] == "backup-node"


@pytest.mark.asyncio
async def test_ha_event_and_incident_recording(tmp_path: Path):
    db_path = tmp_path / "test_state.db"
    storage = Storage(db_path)
    await storage.init_db()

    # Record HA Event
    ev_id = await storage.record_ha_event(
        event_type="FAILOVER_TAKEOVER",
        source_node="backup-node",
        target_node="primary-node",
        details="Test failover",
    )
    assert ev_id > 0

    events = await storage.get_recent_ha_events(limit=5)
    assert len(events) == 1
    assert events[0]["event_type"] == "FAILOVER_TAKEOVER"

    # Record Incident
    inc_id = await storage.record_incident(
        category="NETWORK",
        server="primary-node",
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
