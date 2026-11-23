"""Backup grants short, exclusive polling leases to Main."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from functools import partial
from uuid import uuid4

import aiohttp

from app.core.config import Settings

logger = logging.getLogger("bilionid_ha.handover")

GRANT_SECONDS = 45
MAIN_SECONDS = 25
REQUEST_SECONDS = 8
CHECK_INTERVAL = 4
RECOVERY_SECONDS = 30
clock = partial(time.clock_gettime, time.CLOCK_BOOTTIME) if hasattr(time, "CLOCK_BOOTTIME") else time.monotonic


class TwoNodeHandover:
    def __init__(self, settings: Settings, switch: Callable[[bool], Awaitable[None]]):
        self.settings = settings
        self.switch = switch
        self.clock = clock
        self._lock = asyncio.Lock()
        # A previous Backup process could have issued a grant just before restarting.
        self._grant_until = self.clock() + GRANT_SECONDS
        self._startup_until = self._grant_until
        self._drain_until = 0.0
        self._main_until = 0.0
        self._session_id = uuid4().hex
        self._granted_session: str | None = None
        self._contact_session: str | None = None
        self._backup_active = False
        self._main_active = False
        self._prefer_backup = False
        self._since = None
        self._last_request = None

    def can_poll(self) -> bool:
        if self.settings.is_standby:
            return self._backup_active
        return self.clock() + REQUEST_SECONDS < self._main_until

    async def grant(self, session_id: str) -> bool:
        """Called only on Backup by the authenticated Main."""
        async with self._lock:
            now = self.clock()
            if now < max(self._startup_until, self._drain_until) or self._prefer_backup:
                return False
            if now < self._grant_until and self._granted_session != session_id:
                return False
            if (session_id != self._contact_session or self._last_request is None
                    or now - self._last_request > 3 * CHECK_INTERVAL):
                self._since = now
            self._contact_session = session_id
            self._last_request = now
            if self._backup_active:
                if now - self._since < RECOVERY_SECONDS:
                    return False
                self._backup_active = False
                # Wait for the Backup polling task to stop before granting Main any time.
                try:
                    await self.switch(False)
                except BaseException:
                    self._backup_active = True
                    raise
                self._drain_until = self.clock() + REQUEST_SECONDS
                self._grant_until = self._drain_until + 3 * CHECK_INTERVAL
                self._granted_session = session_id
                return False
            self._grant_until = self.clock() + GRANT_SECONDS
            self._granted_session = session_id
            return True

    async def prefer(self, target: str) -> None:
        if self.settings.is_primary:
            response = await self._post("/ha/preference", target=target)
            if response.get("target") != target:
                raise RuntimeError("Backup did not acknowledge the request")
            return
        async with self._lock:
            self._prefer_backup = target == "backup"
            self._since = self._last_request = None

    async def _post(self, path: str, **payload) -> dict:
        url = f"{self.settings.peer_ha_url}{path}"
        headers = {"X-HA-Secret": self.settings.HA_SECRET_KEY}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3), headers=headers) as session:
            async with session.post(url, json={"node": self.settings.NODE_NAME, **payload}, allow_redirects=False) as response:
                response.raise_for_status()
                document = await response.json()
        if not isinstance(document, dict) or document.get("node") != self.settings.PEER_NODE_NAME:
            raise ValueError("Unexpected Backup identity")
        return document

    async def _step_main(self) -> None:
        sent_at = self.clock()
        try:
            granted = (await self._post("/ha/grant", session=self._session_id)).get("granted") is True
        except (aiohttp.ClientError, TimeoutError, ValueError):
            granted = False
        if granted:
            # Measure from before dispatch. Clock offsets and delayed replies give Main no extra time.
            self._main_until = sent_at + MAIN_SECONDS
        should_poll = self.can_poll()
        if should_poll != self._main_active:
            await self.switch(should_poll)
            self._main_active = should_poll

    async def _step_backup(self) -> None:
        async with self._lock:
            if not self._backup_active and self.clock() >= self._grant_until:
                self._backup_active = True
                try:
                    await self.switch(True)
                except BaseException:
                    self._backup_active = False
                    raise

    async def run(self) -> None:
        check = self._step_main if self.settings.is_primary else self._step_backup
        while True:
            try:
                await check()
            except Exception:
                logger.exception("Polling handover check failed")
            await asyncio.sleep(CHECK_INTERVAL)
