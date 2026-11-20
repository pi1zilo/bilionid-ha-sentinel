import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from app.core.config import Settings
from app.monitoring.collector import MetricsCollector
from app.monitoring.events import Observation
from app.services.monitoring import MonitoringService
from app.storage.db import Storage


async def test_open_recover_and_retry_survive_process_restart(tmp_path):
    settings = Settings(DATA_DIR=tmp_path)
    storage = Storage(settings.db_path)
    await storage.init_db()
    service = MonitoringService(settings, storage, MetricsCollector(settings))
    now = datetime.now(timezone.utc) - timedelta(minutes=5)

    await service.observe(Observation("Kuma", "42", "Webhook Test", "UP", occurred_at=now))
    failed = Observation("Kuma", "42", "Webhook Test", "DOWN", "HTTP 502", now + timedelta(minutes=1))
    await asyncio.gather(*(service.observe(failed) for _ in range(4)))

    storage = Storage(settings.db_path)
    await storage.init_db()
    service = MonitoringService(settings, storage, MetricsCollector(settings))
    await service.observe(Observation("Kuma", "42", "Webhook Test", "UP", occurred_at=now + timedelta(minutes=4)))
    await service.observe(failed)
    assert not await storage.get_active_incidents()
    assert len(await storage.get_recent_incidents()) == 1

    first = await storage.next_alert()
    assert "HTTP 502" in first["text"]
    await storage.update_alert_delivery(first["id"], retry_delay=0)
    assert (await storage.next_alert())["attempts"] == 1
    await storage.update_alert_delivery(first["id"])
    recovery = await storage.next_alert()
    assert "3 мин" in recovery["text"]
    await storage.update_alert_delivery(recovery["id"])
    assert await storage.delivery_counts() == {"pending": 0, "failed": 0}


async def test_source_loss_does_not_close_existing_monitor_incident(tmp_path):
    settings = Settings(DATA_DIR=tmp_path)
    storage = Storage(settings.db_path)
    await storage.init_db()
    service = MonitoringService(settings, storage, MetricsCollector(settings))
    await service.accept_kuma({"monitor": {"id": 8, "name": "web"}, "heartbeat": {"status": 0}})
    await service.source_health("Kuma", "source", "Uptime Kuma", False)
    await service.source_health("Kuma", "source", "Uptime Kuma", False)
    await service.source_health("Kuma", "source", "Uptime Kuma", True)
    assert [row["server"] for row in await storage.get_active_incidents()] == ["web"]


async def test_zabbix_recovery_matches_original_event_id(tmp_path):
    settings = Settings(DATA_DIR=tmp_path)
    storage = Storage(settings.db_path)
    await storage.init_db()
    service = MonitoringService(settings, storage, MetricsCollector(settings))
    stamp = int(datetime.now(timezone.utc).timestamp()) - 60
    await service.accept_zabbix({"status": "PROBLEM", "event_id": "100", "name": "Backup job", "timestamp": stamp})
    await service.accept_zabbix({"status": "RECOVERY", "event_id": "100", "name": "Backup job", "timestamp": stamp + 15})
    incident, = await storage.get_recent_incidents()
    assert incident["category"] == "Zabbix"
    assert incident["resolved_at"]


async def test_alert_worker_uses_persisted_queue(tmp_path):
    settings = Settings(DATA_DIR=tmp_path)
    storage = Storage(settings.db_path)
    await storage.init_db()
    service = MonitoringService(settings, storage, MetricsCollector(settings))
    await service.observe(Observation("Kuma", "123", "delivered", "DOWN"))
    sent = AsyncMock()
    task = asyncio.create_task(service.deliver(sent))
    try:
        for _ in range(50):
            if (await storage.delivery_counts())["pending"] == 0:
                break
            await asyncio.sleep(0.02)
        assert (await storage.delivery_counts())["pending"] == 0
        sent.assert_awaited_once()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
