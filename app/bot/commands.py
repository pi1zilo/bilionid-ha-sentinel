"""Telegram's command menu and the matching help message."""

COMMANDS = (
    ("start", "Главное меню"),
    ("logs", "Журналы bili и suslik"),
    ("alerts", "Сбои и восстановления"),
    ("note", "Записать действие оператора"),
    ("status", "Состояние инфраструктуры"),
    ("servers", "Список серверов"),
    ("server", "Метрики сервера"),
    ("containers", "Состояние Docker-контейнеров"),
    ("ha", "Связь узлов и приём команд Telegram"),
    ("handover", "Смена активного узла для администратора"),
    ("beszel", "Состояние хабов Beszel"),
    ("uptime", "Мониторы Uptime Kuma"),
    ("problems", "Открытые проблемы Zabbix"),
    ("refresh", "Обновить данные"),
    ("help", "Справка по командам"),
)

HELP_TEXT = "\n".join((
    "Доступные команды",
    *(f"/{name} — {description}" for name, description in COMMANDS),
    "",
    "Примеры",
    "/logs suslik 100",
    "/server bili",
    "/note suslik Перезагрузил сервер",
    "/handover suslik",
))
