"""
Message formatters for BilioniD HA Bot.
Produces clean, compact, professional Markdown reports.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.ha.supervisor import HASupervisor
from app.monitoring.models import ContainerInfo, ServerMetrics, UptimeKumaOverview


def format_server_card(s: ServerMetrics) -> str:
    """Formats a single server detailed status matching Section 9 specification."""
    lines = [
        f"🖥 **{s.name}**",
        "",
        f"Статус: {s.status}",
        "",
        "CPU:",
        f"Загрузка: {s.cpu_percent}%",
        "",
        "RAM:",
        f"Использовано: {s.ram_used_gb} / {s.ram_total_gb} GB",
        f"Загрузка: {s.ram_percent}%",
        "",
        "Диск:",
        f"Использовано: {s.disk_used_gb} / {s.disk_total_gb} GB",
        f"Свободно: {s.disk_free_gb} GB",
        "",
        "Load Average:",
        f"1m: {s.load_1m:.2f}",
        f"5m: {s.load_5m:.2f}",
        f"15m: {s.load_15m:.2f}",
        "",
        "Uptime:",
        f"{s.uptime_str}",
        "",
        "Docker:",
        f"Контейнеров: {s.docker_containers_total}",
        f"Работает: {s.docker_containers_running}",
        f"Остановлено: {s.docker_containers_stopped}",
    ]

    if s.temperature is not None:
        lines.extend(["", f"Температура: {s.temperature}°C"])

    lines.extend([
        "",
        f"Последнее обновление: {s.last_updated or datetime.now(timezone.utc).strftime('%H:%M:%S')}",
    ])

    if s.error_message:
        lines.extend(["", f"⚠️ *Примечание:* {s.error_message}"])

    return "\n".join(lines)


def format_infrastructure_status(
    servers: List[ServerMetrics],
    uptime_overview: UptimeKumaOverview,
    ha_supervisor: HASupervisor,
) -> str:
    """Formats general overview for /status command."""
    ha_info = ha_supervisor.get_ha_info()
    now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")

    lines = [
        "🌐 **ОБЩИЙ СТАТУС ИНФРАСТРУКТУРЫ**",
        f"⏱ Время проверки: `{now_str}`",
        "",
        "🔄 **Состояние HA кластера:**",
        f"• Текущий узел: `{ha_supervisor.settings.NODE_NAME}` ({ha_supervisor.role.value})",
        f"• Режим: `{ha_supervisor.state.value}`",
        f"• Активный Polling: `{'Да' if ha_supervisor.is_polling else 'Нет'}`",
        f"• Связь со вторым узлом: `{'🟢 Норма' if ha_info.get('peer_reachable') else '🔴 Недоступен'}`",
        "",
        "🖥 **Серверы:**",
    ]

    if not servers:
        lines.append("• Данные от серверов временно недоступны.")
    else:
        for s in servers:
            icon = "🟢" if s.is_online else "🔴"
            lines.append(
                f"• {icon} **{s.name}**: CPU {s.cpu_percent}% | RAM {s.ram_percent}% ({s.ram_used_gb}/{s.ram_total_gb}G) | Uptime: {s.uptime_str}"
            )

    lines.extend([
        "",
        "📊 **Мониторинг сервисов (Uptime Kuma):**",
    ])
    if uptime_overview.available:
        lines.append(
            f"• Мониторов: {uptime_overview.total_count} (🟢 Up: {uptime_overview.up_count} | 🔴 Down: {uptime_overview.down_count})"
        )
    else:
        lines.append(f"• {uptime_overview.error_message or 'Не настроен / недоступен'}")

    return "\n".join(lines)


def format_ha_status(ha_supervisor: HASupervisor, lease_info: Optional[Dict[str, Any]]) -> str:
    """Formats HA diagnostics view."""
    ha_info = ha_supervisor.get_ha_info()
    peer_health = ha_supervisor.peer_health

    role_desc = "PRIMARY (Основной)" if ha_supervisor.role.value == "PRIMARY" else "STANDBY (Резервный)"
    peer_role_desc = "STANDBY" if ha_supervisor.role.value == "PRIMARY" else "PRIMARY"

    lines = [
        "🔄 **HIGH AVAILABILITY STATUS**",
        "",
        f"📍 **Локальный узел:** `{ha_supervisor.settings.NODE_NAME}`",
        f"• Роль: `{role_desc}`",
        f"• Состояние FSM: `{ha_supervisor.state.value}`",
        f"• Telegram Polling: `{'🟢 Активен (Лидер)' if ha_supervisor.is_polling else '⚪ Остановлен (Резерв)'}`",
        f"• Uptime процесса: `{ha_supervisor.uptime_seconds // 3600}ч {(ha_supervisor.uptime_seconds % 3600) // 60}м`",
        "",
        f"🌐 **Связь с пиром ({peer_health.peer_name} — {peer_health.peer_ip}):**",
        f"• Доступность: `{'🟢 Доступен' if peer_health.is_reachable else '🔴 НЕДОСТУПЕН'}`",
        f"• HA Endpoint ({ha_supervisor.settings.HA_PORT}): `{'🟢 OK' if peer_health.ha_endpoint_ok else '🔴 Ошибка'}`",
        f"• Beszel Hub: `{'🟢 OK' if peer_health.beszel_hub_ok else '⚪ Не проверен / Ошибка'}`",
        f"• Успешных проверок подряд: `{peer_health.consecutive_successes}`",
        f"• Ошибок подряд: `{peer_health.consecutive_failures}`",
    ]

    if peer_health.last_seen_healthy:
        lines.append(f"• Последний здоровый ответ: `{peer_health.last_seen_healthy.strftime('%H:%M:%S')} UTC`")

    lines.extend([
        "",
        "🔒 **HA Lease (Блокировка лидера):**",
    ])
    if lease_info:
        lines.extend([
            f"• Текущий владелец: `{lease_info.get('holder_node')}`",
            f"• Истекает в: `{lease_info.get('expires_at', '')[:19]} UTC`",
            f"• Статус: `{'Активен' if lease_info.get('is_active') else 'Освобожден'}`",
        ])
    else:
        lines.append("• Данные lease отсутствуют в локальной БД.")

    lines.extend([
        "",
        "🛡 **Защита от Split-Brain:**",
        "• Модель: 2 узла, Heartbeat + Lease timeout + Fencing через Telegram API 409 Conflict.",
        "• При потере связи Backup перехватывает управление только при истечении lease и подтверждении выхода в Интернет.",
    ])

    return "\n".join(lines)


def format_containers_view(
    containers_by_server: Dict[str, List[ContainerInfo]],
    server_errors: Optional[Dict[str, str]] = None,
) -> str:
    """Formats Docker containers report across servers."""
    lines = ["🐳 **DOCKER КОНТЕЙНЕРЫ**", ""]
    errors = server_errors or {}

    for s_name, c_list in containers_by_server.items():
        if c_list:
            running = sum(1 for c in c_list if c.state == "running")
            lines.append(f"🖥 **{s_name}** ({running}/{len(c_list)} работают):")
            for c in c_list:
                status_icon = "🟢" if c.state == "running" else "🔴"
                health_str = f" [{c.health}]" if c.health else ""
                lines.append(f"  {status_icon} `{c.name}` ({c.image}){health_str}")
        else:
            err = errors.get(s_name)
            if err:
                lines.append(f"🖥 **{s_name}**:\n  ⚠️ {err}")
            else:
                lines.append(f"🖥 **{s_name}**:\n  _(контейнеры не запущены или узел недоступен)_")

        lines.append("")

    return "\n".join(lines).strip()


def format_beszel_view(beszel_status: Dict[str, Any]) -> str:
    """Formats Beszel Hubs health view."""
    main_st = beszel_status.get("main", {})
    backup_st = beszel_status.get("backup", {})
    active = beszel_status.get("active_hub", "Unknown")

    lines = [
        "📊 **BESZEL MONITORING HUBS**",
        "",
        f"🎯 Активный Hub для запросов: `{active}`",
        "",
        "1️⃣ **Main Beszel Hub:**",
        f"• URL: `{main_st.get('url', 'N/A')}`",
        f"• Доступность: `{'🟢 Онлайн' if main_st.get('available') else '🔴 Недоступен'}`",
    ]
    if main_st.get("latency_ms"):
        lines.append(f"• Задержка: `{main_st.get('latency_ms')} ms`")
    if main_st.get("error"):
        lines.append(f"• Ошибка: `{main_st.get('error')}`")

    lines.extend([
        "",
        "2️⃣ **Backup Beszel Hub:**",
        f"• URL: `{backup_st.get('url', 'N/A')}`",
        f"• Доступность: `{'🟢 Онлайн' if backup_st.get('available') else '🔴 Недоступен'}`",
    ])
    if backup_st.get("latency_ms"):
        lines.append(f"• Задержка: `{backup_st.get('latency_ms')} ms`")
    if backup_st.get("error"):
        lines.append(f"• Ошибка: `{backup_st.get('error')}`")

    return "\n".join(lines)


def format_uptime_kuma_view(overview: UptimeKumaOverview) -> str:
    """Formats Uptime Kuma monitors view."""
    lines = [
        "📊 **UPTIME KUMA МОНИТОРЫ**",
        "",
    ]
    if not overview.available:
        lines.append(f"⚠️ {overview.error_message or 'Uptime Kuma недоступен'}")
        return "\n".join(lines)

    lines.extend([
        f"Всего мониторов: **{overview.total_count}** (🟢 Up: {overview.up_count} | 🔴 Down: {overview.down_count})",
        "",
    ])

    for m in overview.monitors:
        icon = "🟢" if m.status == "UP" else ("🔴" if m.status == "DOWN" else "🟡")
        ping_s = f" ({m.ping_ms} ms)" if m.ping_ms is not None else ""
        lines.append(f"• {icon} **{m.name}**: `{m.status}`{ping_s}")

    return "\n".join(lines)


def format_incidents_view(ha_events: List[Dict[str, Any]], incidents: List[Dict[str, Any]]) -> str:
    """Formats recent incidents and HA failover transitions."""
    lines = [
        "🚨 **ЖУРНАЛ ИНЦИДЕНТОВ И HA ПЕРЕКЛЮЧЕНИЙ**",
        "",
    ]

    if not ha_events and not incidents:
        lines.append("Инцидентов и аварийных переключений не зафиксировано. Система стабильна. 🟢")
        return "\n".join(lines)

    if ha_events:
        lines.append("**Последние HA события:**")
        for ev in ha_events[:7]:
            ts = ev.get("timestamp", "")[:19].replace("T", " ")
            ev_type = ev.get("event_type", "UNKNOWN")
            src = ev.get("source_node", "")
            details = ev.get("details", "")
            lines.append(f"• `[{ts}]` **{ev_type}** ({src})\n  _{details}_")
        lines.append("")

    if incidents:
        lines.append("**Активные инциденты инфраструктуры:**")
        for inc in incidents[:7]:
            ts = inc.get("timestamp", "")[:19].replace("T", " ")
            sev = inc.get("severity", "WARN")
            msg = inc.get("message", "")
            srv = inc.get("server", "")
            lines.append(f"• `[{ts}]` [{sev}] {srv}: {msg}")

    return "\n".join(lines)


def format_admin_view(ha_supervisor: HASupervisor) -> str:
    """Formats safe admin/management screen."""
    return (
        "⚙️ **УПРАВЛЕНИЕ ИНФРАСТРУКТУРОЙ**\n\n"
        "🔒 **Текущий режим безопасности:** Безопасный Read-Only мониторинг.\n"
        "Выполнение произвольных shell-команд заблокировано политикой безопасности.\n\n"
        f"• Текущий активный узел: `{ha_supervisor.settings.NODE_NAME}`\n"
        f"• Роль: `{ha_supervisor.role.value}`\n\n"
        "Доступные действия:\n"
        "• Принудительное переключение HA роли\n"
        "• Диагностика и проверка связности Tailscale\n"
        "• Обновление кэша метрик"
    )
