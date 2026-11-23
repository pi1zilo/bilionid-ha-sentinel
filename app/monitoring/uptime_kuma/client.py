"""Read Kuma's Prometheus exporter or a configured status page."""

import json
import logging
from collections import Counter
from urllib.parse import quote

import aiohttp
from prometheus_client.parser import text_string_to_metric_families

from app.monitoring.models import UptimeKumaMonitor, UptimeKumaOverview

logger = logging.getLogger("bilionid_ha.uptime_kuma")
MONITOR_STATES = {0: "DOWN", 1: "UP", 2: "PENDING", 3: "MAINTENANCE"}
METRIC_MONITOR_LABELS = {
    "monitor_id", "monitor_name", "monitor_type", "monitor_url", "monitor_hostname", "monitor_port",
}


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
        self.status_page_slug = status_page_slug or "default"
        self.timeout = aiohttp.ClientTimeout(total=timeout_sec)
        self.auth = aiohttp.BasicAuth("", api_key) if api_key else None
        if not self.auth and username and password:
            self.auth = aiohttp.BasicAuth(username, password)

    async def get_overview(self, *, include_tags: bool = False) -> UptimeKumaOverview:
        if not self.enabled:
            return UptimeKumaOverview(
                available=False, state="DISABLED", error_message="Интеграция Uptime Kuma отключена."
            )

        reason = "Uptime Kuma не отвечает."
        slug = quote(self.status_page_slug, safe="")
        monitors = None
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            try:
                async with session.get(
                    f"{self.base_url}/metrics", auth=self.auth, allow_redirects=False
                ) as response:
                    if response.status == 200:
                        monitors = self._parse_prometheus_metrics(await response.text())
                    else:
                        reason = f"Uptime Kuma /metrics вернул HTTP {response.status}."
            except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
                logger.debug("Kuma metrics: %s", type(exc).__name__)

            from_heartbeats = monitors is None
            if from_heartbeats:
                try:
                    async with session.get(
                        f"{self.base_url}/api/status-page/heartbeat/{slug}", allow_redirects=False
                    ) as response:
                        if response.status == 200:
                            monitors = self._parse_status_page_heartbeats(await response.json())
                except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError) as exc:
                    logger.debug("Kuma status page: %s", type(exc).__name__)

            if monitors is not None:
                if include_tags and monitors:
                    await self._apply_status_page_metadata(session, slug, monitors, from_heartbeats)
                return self._overview(monitors)
        return UptimeKumaOverview(available=False, state="UNKNOWN", error_message=reason)

    async def _apply_status_page_metadata(
        self, session: aiohttp.ClientSession, slug: str,
        monitors: list[UptimeKumaMonitor], from_heartbeats: bool,
    ) -> None:
        try:
            async with session.get(f"{self.base_url}/api/status-page/{slug}", allow_redirects=False) as response:
                if response.status != 200:
                    return
                data = await response.json()
            by_id = {
                str(monitor["id"]): monitor
                for group in data["publicGroupList"]
                for monitor in group["monitorList"]
            }
            by_name = {monitor["name"]: monitor for monitor in by_id.values()}
            page_name_counts = Counter(monitor["name"] for monitor in by_id.values())
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError) as exc:
            logger.debug("Kuma status page metadata: %s", type(exc).__name__)
            return

        monitor_name_counts = Counter(monitor.name for monitor in monitors)
        for monitor in monitors:
            metadata = by_id.get(monitor.id)
            # Older Kuma exporters do not include monitor_id. Match only unambiguous names.
            if (
                not monitor.id.isdecimal()
                and monitor_name_counts[monitor.name] == page_name_counts[monitor.name] == 1
            ):
                metadata = by_name[monitor.name]
            if metadata is None:
                continue
            if from_heartbeats:
                monitor.name = metadata["name"]
            tags = metadata.get("tags")
            if isinstance(tags, list):
                monitor.tags = [
                    f"{tag['name']}: {tag['value']}" if tag.get("value") else tag["name"]
                    for tag in tags if isinstance(tag, dict) and tag.get("name")
                ]

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
                    # Kuma exports each tag as a label with comma-separated values.
                    record.tags = [
                        f"{label}: {tag_value}" if tag_value else label
                        for label, value in sample.labels.items() if label not in METRIC_MONITOR_LABELS
                        for tag_value in value.split(",")
                    ]
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
