"""Read Kuma's Prometheus exporter or a configured status page."""

import json
import logging
from urllib.parse import quote

import aiohttp
from prometheus_client.parser import text_string_to_metric_families

from app.monitoring.models import UptimeKumaMonitor, UptimeKumaOverview

logger = logging.getLogger("bilionid_ha.uptime_kuma")
MONITOR_STATES = {0: "DOWN", 1: "UP", 2: "PENDING", 3: "MAINTENANCE"}


class UptimeKumaClient:
    def __init__(
        self,
        base_url: str = "http://100.68.106.100:29000",
        api_key: str | None = None,
        username: str | None = None,
        password: str | None = None,
        enabled: bool = True,
        timeout_sec: float = 4.0,
        status_page_slug: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self.enabled = enabled
        self.status_page_slug = status_page_slug
        self.timeout = aiohttp.ClientTimeout(total=timeout_sec)
        self.auth = aiohttp.BasicAuth("", api_key) if api_key else None
        if not self.auth and username and password:
            self.auth = aiohttp.BasicAuth(username, password)

    async def get_overview(self) -> UptimeKumaOverview:
        if not self.enabled:
            return UptimeKumaOverview(
                available=False, state="DISABLED", error_message="Интеграция Uptime Kuma отключена."
            )

        reason = "Uptime Kuma не отвечает."
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            try:
                async with session.get(
                    f"{self.base_url}/metrics", auth=self.auth, allow_redirects=False
                ) as response:
                    if response.status == 200:
                        return self._overview(self._parse_prometheus_metrics(await response.text()))
                    reason = f"Uptime Kuma /metrics вернул HTTP {response.status}."
            except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
                logger.debug("Kuma metrics: %s", type(exc).__name__)

            if self.status_page_slug:
                slug = quote(self.status_page_slug, safe="")
                try:
                    async with session.get(
                        f"{self.base_url}/api/status-page/heartbeat/{slug}", allow_redirects=False
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            return self._overview(self._parse_status_page_heartbeats(data))
                except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError) as exc:
                    logger.debug("Kuma status page: %s", type(exc).__name__)
        return UptimeKumaOverview(available=False, state="UNKNOWN", error_message=reason)

    @staticmethod
    def _overview(monitors: list[UptimeKumaMonitor]) -> UptimeKumaOverview:
        return UptimeKumaOverview(
            monitors=monitors,
            total_count=len(monitors),
            up_count=sum(m.status == "UP" for m in monitors),
            down_count=sum(m.status == "DOWN" for m in monitors),
            state="UP",
        )

    @staticmethod
    def _parse_prometheus_metrics(body: str) -> list[UptimeKumaMonitor]:
        monitors: dict[str, UptimeKumaMonitor] = {}
        for family in text_string_to_metric_families(body):
            for sample in family.samples:
                if sample.name not in {"monitor_status", "monitor_response_time"}:
                    continue
                name = sample.labels.get("monitor_name")
                if not name:
                    continue
                key = json.dumps(sample.labels, sort_keys=True, ensure_ascii=False)
                monitor_id = sample.labels.get("monitor_id") or key
                record = monitors.setdefault(
                    key,
                    UptimeKumaMonitor(
                        id=monitor_id, name=name, url=sample.labels.get("monitor_url") or None
                    ),
                )
                if sample.name == "monitor_status":
                    record.status = MONITOR_STATES.get(sample.value, "UNKNOWN")
                elif float("-inf") < sample.value < float("inf"):
                    record.ping_ms = round(sample.value, 1)
        # Kuma's exporter uses the same labels for both series. Some old snapshots
        # have name-only latency; attach it only if that name resolves uniquely.
        by_name: dict[str, list[UptimeKumaMonitor]] = {}
        for record in monitors.values():
            if record.status != "UNKNOWN":
                by_name.setdefault(record.name, []).append(record)
        for record in monitors.values():
            matches = by_name.get(record.name, ())
            if record.status == "UNKNOWN" and record.ping_ms is not None and len(matches) == 1:
                if matches[0].ping_ms is None:
                    matches[0].ping_ms = record.ping_ms
        return [record for record in monitors.values() if record.status != "UNKNOWN"]

    @staticmethod
    def _parse_status_page_heartbeats(data: dict) -> list[UptimeKumaMonitor]:
        monitors = []
        for monitor_id, beats in data["heartbeatList"].items():
            if not beats:
                continue
            last = beats[-1]
            monitors.append(UptimeKumaMonitor(
                id=str(monitor_id),
                name=f"Monitor #{monitor_id}",
                status=MONITOR_STATES.get(last.get("status"), "UNKNOWN"),
                ping_ms=last.get("ping"),
                msg=last.get("msg"),
                last_check=last.get("time"),
            ))
        return monitors
