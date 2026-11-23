"""Plain-text Telegram views for monitoring and diagnostics."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.ha.supervisor import HASupervisor
from app.monitoring.models import ContainerInfo, ServerMetrics, UptimeKumaOverview
from app.services.monitoring import parse_event_time


def format_time(value: str | None, tz_name: str = "Europe/Moscow") -> str:
    try:
        parsed = parse_event_time(value)
    except (ValueError, TypeError, OverflowError, OSError):
        return "—"
    return parsed.astimezone(ZoneInfo(tz_name)).strftime("%d.%m %H:%M:%S %Z") if parsed else "—"


def format_server_card(server: ServerMetrics, tz_name: str = "Europe/Moscow") -> str:
    lines = [f"🖥 {server.name}", f"Статус: {server.status}", f"Источник: {server.source}"]
    if server.monitor_state not in {"DOWN", "UNKNOWN"}:
        lines += [f"CPU: {server.cpu_percent}%", f"RAM: {server.ram_percent}%", f"Диск: {server.disk_percent}%"]
        if server.ram_total_gb:
            lines.append(f"RAM: {server.ram_used_gb} / {server.ram_total_gb} ГиБ")
        if server.disk_total_gb:
            lines.append(f"Диск: {server.disk_used_gb} / {server.disk_total_gb} ГиБ")
        if server.temperature is not None:
            lines.append(f"Температура: {server.temperature} °C")
        lines.append(f"Uptime: {server.uptime_str}")
    if server.last_updated:
        lines.append(f"Последние данные: {format_time(server.last_updated, tz_name)}")
    return "\n".join(lines)


def format_infrastructure_status(
    servers: list[ServerMetrics], kuma: UptimeKumaOverview, supervisor: HASupervisor,
    delivery: dict | None = None,
) -> str:
    info = supervisor.get_ha_info()
    lines = ["🌐 Инфраструктура", f"Узел: {info['node_name']} · {info['state']}", "", "🖥 Серверы"]
    if not servers:
        lines.append("Нет данных Beszel.")
    for server in servers:
        lines.append(f"• {server.name}: {server.status}")
    lines += ["", "⏱ Uptime Kuma"]
    if kuma.available:
        lines.append(f"Всего {kuma.total_count} · UP {kuma.up_count} · DOWN {kuma.down_count}")
    else:
        lines.append(kuma.error_message or "Нет данных.")
    if delivery and (delivery["pending"] or delivery["failed"]):
        lines += ["", f"📨 Алерты в очереди: {delivery['pending']} · ошибки доставки: {delivery['failed']}"]
    return "\n".join(lines)


def format_ha_status(supervisor: HASupervisor) -> str:
    info = supervisor.get_ha_info()
    tz = supervisor.settings.ALERT_TIMEZONE
    lines = [
        "🔄 Состояние HA",
        f"Узел: {info['node_name']} · {info['role']} · {info['state']}",
        f"Telegram polling: {'активен' if info['polling_ready'] else 'не готов' if info['polling_enabled'] else 'выключен'}",
        f"Последний getUpdates: {format_time(info.get('last_poll_ok'), tz)}",
    ]
    if error := info.get("last_poll_error"):
        lines.append(f"Последняя ошибка: {error}")
    if info["peer_configured"]:
        lines += [
            "",
            f"Резервный адрес: {info['peer_name']} · {supervisor.peer_health.peer_ip}",
            f"HA API: {'доступен' if info['peer_reachable'] else 'нет связи'}",
            f"Polling второго узла: { {True: 'активен', False: 'выключен или недоступен', None: 'неизвестно'}[info['peer_polling_ready']] }",
        ]
    lines += ["", "Переключение выполняется через SSH."]
    return "\n".join(lines)


def format_containers_view(containers_by_server: dict[str, list[ContainerInfo]]) -> str:
    lines = ["🐳 Контейнеры"]
    for server, containers in containers_by_server.items():
        lines += ["", server]
        if not containers:
            lines.append("Нет данных Docker.")
        for container in containers:
            health = f" · {container.health}" if container.health else ""
            lines.append(f"• {container.name}: {container.state}{health}")
    return "\n".join(lines)[:3800]


def format_beszel_view(status: dict) -> str:
    lines = ["📊 Beszel", f"Основной источник данных: {status.get('active_hub', '—')}"]
    for key, label in (("main", "Main"), ("backup", "Backup")):
        hub = status.get(key, {})
        if hub.get("url"):
            lines.append(f"• {label}: {'доступен' if hub.get('available') else 'нет связи'}")
    return "\n".join(lines)


def format_uptime_kuma_view(overview: UptimeKumaOverview) -> str:
    if not overview.available:
        return overview.error_message or "Нет данных Uptime Kuma."
    lines = ["⏱ Uptime Kuma", f"Всего {overview.total_count} · UP {overview.up_count} · DOWN {overview.down_count}", ""]
    for monitor in overview.monitors:
        latency = f" · {monitor.ping_ms:g} мс" if monitor.ping_ms is not None and monitor.ping_ms >= 0 else ""
        tags = f" · 🏷 {', '.join(monitor.tags)}" if monitor.tags else ""
        lines.append(f"• {monitor.name}: {monitor.status}{latency}{tags}")
    return "\n".join(lines)[:3800]


def format_incidents_view(
    ha_events: list[dict], incidents: list[dict], tz_name: str = "Europe/Moscow", node_name: str = "",
) -> str:
    lines = ["🚨 Журнал инцидентов"]
    if not ha_events and not incidents:
        return f"{lines[0]}\nЗаписей пока нет."
    entries = []
    for incident in incidents:
        timestamp = format_time(incident["timestamp"], tz_name)
        closed = incident.get("resolved_at")
        origin = incident.get("node") or node_name
        reference = f" · {origin} #{incident['id']}" if origin else ""
        standalone = closed == incident["timestamp"] and incident.get("severity") == "INFO"
        state = "ℹ️" if standalone else "🟢" if closed else "🔴"
        entry = f"{state} {incident['server']}\n{incident['category']}{reference} · {timestamp}"
        if incident["message"]:
            entry += f"\n{incident['message'][:200]}"
        if closed and not standalone:
            entry += f"\nВосстановление: {format_time(closed, tz_name)}"
            if incident.get("resolution"):
                entry += f" · {incident['resolution'][:200]}"
        entries.append((incident["timestamp"], entry))
    for event in ha_events[:3]:
        label = {
            "POLLING_ENABLED": "Запуск узла с приёмом обновлений Telegram",
            "STANDBY_STARTUP": "Запуск резервного узла",
        }.get(event["event_type"], event["event_type"])
        entries.append((event["timestamp"],
            f"• {event['source_node']} · {label}\n{format_time(event['timestamp'], tz_name)}"))
    for _, entry in sorted(entries, reverse=True):
        if sum(len(line) + 1 for line in lines) + len(entry) + 2 > 3800:
            break
        lines.extend(("", entry))
    return "\n".join(lines)


def format_admin_view(supervisor: HASupervisor) -> str:
    return "⚙️ Управление\n\n" + format_ha_status(supervisor)
