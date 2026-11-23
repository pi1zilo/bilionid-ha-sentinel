import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer
from aiogram.filters.command import CommandObject

from app.bot.handlers.alerts import alerts_view, cmd_note
from app.core.config import NodeRole, Settings
from app.ha.http_server import HAHttpServer
from app.ha.supervisor import HASupervisor
from app.monitoring.collector import MetricsCollector
from app.monitoring.events import Observation
from app.monitoring.models import ContainerInfo, ServerMetrics
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
    assert "Было: HTTP 502" in recovery["text"]
    assert "@" not in first["text"]
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


async def test_beszel_webhook_translates_cpu_and_links_recovery(tmp_path):
    settings = Settings(DATA_DIR=tmp_path, NODE_NAME="main", BESZEL_WEBHOOK_SECRET="b" * 32)
    storage = Storage(settings.db_path)
    await storage.init_db()
    collector = MetricsCollector(settings)
    service = MonitoringService(settings, storage, collector)
    server = HAHttpServer(settings, HASupervisor(settings, service), service, collector.docker)
    url = "http://beszel.example/system/56mkfavo4lfk7lk"
    headers = {"X-Monitor-Secret": settings.BESZEL_WEBHOOK_SECRET}
    down = {"title": "suslik @suslik24na7 CPU above threshold",
            "message": f"CPU averaged 96.43% for the previous 1 minute.\n\n{url}", "hub": "backup"}
    up = {"title": "suslik @suslik24na7 CPU below threshold",
          "message": f"CPU averaged 28.25% for the previous 1 minute.\n\n{url}", "hub": "backup"}
    async with TestClient(TestServer(server.app)) as client:
        assert (await client.post("/events/beszel", json=down)).status == 401
        assert (await client.post("/events/beszel", json={"title": True}, headers=headers)).status == 400
        for payload in (down, down, up, up):
            assert (await client.post("/events/beszel", json=payload, headers=headers)).status == 204
        status = {"title": "Connection to suslik is down 🔴", "message": f"Connection to suslik is down\n\n{url}"}
        assert (await client.post("/events/beszel", json=status, headers=headers)).status == 204

    row, = await storage.get_recent_incidents()
    assert row["resolved_at"]
    assert row["message"] == "Среднее за 1 мин — 96,43%. Выше порога."
    assert row["resolution"] == "Среднее за 1 мин — 28,25%. Ниже порога."
    opened = await storage.next_alert()
    assert f"Beszel · bili #{row['id']}" in opened["text"]
    assert "suslik · загрузка CPU" in opened["text"]
    assert opened["text"].count("@suslik24na7") == 1
    assert url in opened["text"]
    await storage.update_alert_delivery(opened["id"])
    recovered = await storage.next_alert()
    assert f"Beszel · bili #{row['id']}" in recovered["text"]
    assert "Вне порога" in recovered["text"]
    assert "96,43%" in recovered["text"] and "28,25%" in recovered["text"]
    assert "@suslik24na7" not in recovered["text"]
    await storage.update_alert_delivery(recovered["id"])
    assert await storage.next_alert() is None


async def test_beszel_reboot_requires_consistent_fresh_uptime_and_persists(tmp_path):
    settings = Settings(DATA_DIR=tmp_path)
    storage = Storage(settings.db_path)
    await storage.init_db()
    service = MonitoringService(settings, storage, MetricsCollector(settings))
    now = datetime.now(timezone.utc)
    server = ServerMetrics(name="suslik", entity_id="id1", beszel_hub="main")

    async def sample(seconds_ago, uptime, **updates):
        metric = server.model_copy(update={
            "uptime_sampled_at": (now - timedelta(seconds=seconds_ago)).isoformat(),
            "uptime_seconds": uptime, **updates,
        })
        await service._check_beszel_uptime(metric, "suslik")

    await sample(120, 7200)
    await sample(110, 7210)
    await sample(95, 3, beszel_hub="backup")
    await sample(90, 7220)
    assert not await storage.get_recent_incidents()
    service = MonitoringService(settings, Storage(settings.db_path), MetricsCollector(settings))
    await sample(195, 5)
    await sample(30, 20)
    await sample(30, 20)
    await sample(5, 45)
    restart, = await storage.get_recent_incidents()
    assert "признаки перезагрузки" in restart["server"]
    assert datetime.fromisoformat(restart["timestamp"]) == now - timedelta(seconds=50)
    assert not await storage.get_active_incidents()


async def test_journal_combines_operator_note_and_authenticated_peer_incident(tmp_path):
    backup = Settings(DATA_DIR=tmp_path / "backup", NODE_ROLE=NodeRole.STANDBY, NODE_NAME="backup",
                      HA_PEER_ENABLED=True, HA_SECRET_KEY="secret")
    remote_storage = Storage(backup.db_path)
    await remote_storage.init_db()
    remote_collector = MetricsCollector(backup)
    remote = MonitoringService(backup, remote_storage, remote_collector)
    await remote.observe(Observation("HA", "main", "Бот на main", "DOWN", "Проверка HA API узла не прошла."))
    server = HAHttpServer(backup, HASupervisor(backup, remote), remote, remote_collector.docker)

    async with TestClient(TestServer(server.app)) as client:
        assert (await client.get("/ha/incidents")).status == 401
        main = Settings(DATA_DIR=tmp_path / "main", NODE_NAME="main", HA_PEER_ENABLED=True,
                        PEER_NODE_NAME="backup", PEER_TAILSCALE_IP=client.host, HA_PEER_PORT=client.port,
                        HA_SECRET_KEY="secret")
        storage = Storage(main.db_path)
        await storage.init_db()
        message = AsyncMock()
        message.from_user.username = "operator"
        message.date = datetime.now(timezone.utc)
        await cmd_note(message, CommandObject(command="note", args="suslik Перезагрузил вручную"), storage, main)
        assert "bili #1" in message.answer.await_args.args[0]
        text = await alerts_view(storage, main, MetricsCollector(main))
        assert "Оператор · bili #1" in text
        assert "@operator: Перезагрузил вручную" in text
        assert "HA · suslik #1" in text
        assert text.index("Оператор") < text.index("HA · suslik")


async def test_container_incident_closes_after_process_restart(tmp_path):
    settings = Settings(DATA_DIR=tmp_path)
    storage = Storage(settings.db_path)
    await storage.init_db()
    collector = MetricsCollector(settings)
    monitored = ContainerInfo(name="web", server="main", state="running")
    stopped = ContainerInfo(name="unused", server="main", state="exited")
    collector.get_all_containers = AsyncMock(return_value={"main": [monitored, stopped]})
    monitoring = MonitoringService(settings, storage, collector)
    await monitoring._check_containers()
    monitored.state = "exited"
    monitored.status = "Exited (137) 10 seconds ago"
    await monitoring._check_containers()
    incident, = await storage.get_active_incidents()
    assert incident["server"] == "bili · контейнер web"
    alert = await storage.next_alert()
    assert "Код выхода 137" in alert["text"]
    assert "@bilionid" in alert["text"]

    monitoring = MonitoringService(settings, Storage(settings.db_path), collector)
    collector.get_all_containers.return_value = {"main": []}
    await monitoring._check_containers()
    assert len(await storage.get_active_incidents()) == 1
    monitored.state = "running"
    collector.get_all_containers.return_value = {"main": [monitored, stopped]}
    await monitoring._check_containers()
    assert not await storage.get_active_incidents()


async def test_sentinel_resource_limits_use_incident_queue(tmp_path):
    settings = Settings(DATA_DIR=tmp_path)
    storage = Storage(settings.db_path)
    await storage.init_db()
    monitoring = MonitoringService(settings, storage, MetricsCollector(settings))
    server = ServerMetrics(name="main", cpu_percent=96, ram_percent=70, disk_percent=91)
    await monitoring._check_resource_limits(server, "main")
    warning, = await storage.get_active_incidents()
    assert warning["server"] == "bili · загрузка CPU"
    server.cpu_percent = 80
    await monitoring._check_resource_limits(server, "main")
    assert not await storage.get_active_incidents()
