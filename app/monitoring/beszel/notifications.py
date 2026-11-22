"""Parse Beszel's Shoutrrr notifications while keeping unknown formats visible."""

import re
from urllib.parse import urlsplit

from app.monitoring.events import Observation

METRICS = {
    "cpu": "загрузка CPU",
    "cpu i/o wait": "ожидание ввода-вывода CPU",
    "cpu steal time": "задержка CPU со стороны гипервизора",
    "memory": "использование памяти",
    "disk usage": "заполнение диска",
    "temperature": "температура",
    "bandwidth": "сетевой трафик",
    "gpu": "загрузка GPU",
    "1m load": "нагрузка (1 мин)",
    "5m load": "нагрузка (5 мин)",
    "15m load": "нагрузка (15 мин)",
    "battery": "заряд батареи",
}
THRESHOLD_TITLE = re.compile(
    rf"(?P<host>.+) (?P<metric>{'|'.join(map(re.escape, METRICS))}) (?P<direction>above|below) threshold",
    re.IGNORECASE,
)
AVERAGE = re.compile(
    r"(?P<descriptor>.+) averaged (?P<value>-?\d+(?:\.\d+)?)(?P<unit>.*?) for the previous (?P<minutes>\d+) minutes?\.",
    re.IGNORECASE,
)
HOST_STATUS = re.compile(r"Connection to .+ is (?:up|down)(?: [✅🔴])?", re.IGNORECASE)
SYSTEM_PATH = re.compile(r"(?:^|/)system/([a-zA-Z0-9]+)$")


def parse_threshold(title: str, message: str, hub: str) -> Observation | None:
    threshold = THRESHOLD_TITLE.fullmatch(title)
    if not threshold:
        return None
    metric = threshold["metric"].casefold()
    above = threshold["direction"].lower() == "above"
    triggered = above != (metric == "battery")
    lines = message.splitlines()
    average = AVERAGE.fullmatch(lines[0]) if lines else None
    detail = "Выше порога." if above else "Ниже порога."
    if average:
        value = average["value"].replace(".", ",")
        unit = average["unit"].replace("MB/s", "МБ/с")
        detail = f"Среднее за {average['minutes']} мин — {value}{unit}. {detail}"
        descriptor = average["descriptor"]
        if descriptor.casefold() not in METRICS and descriptor.casefold() != "disk":
            detail += f" Датчик: {descriptor}."
    elif lines:
        detail += f" {lines[0]}"

    link = ""
    entity = threshold["host"].casefold()
    for line in reversed(lines):
        if not line.startswith(("http://", "https://")) or len(line) > 400:
            continue
        path = SYSTEM_PATH.search(urlsplit(line).path)
        if path:
            link, entity = line, path[1]
            break
    return Observation(
        "Beszel", f"threshold:{hub}:{entity}:{metric}", f"{threshold['host'][:140]} · {METRICS[metric]}",
        "DOWN" if triggered else "UP", detail[:500], duration_label="Вне порога", link=link,
    )
