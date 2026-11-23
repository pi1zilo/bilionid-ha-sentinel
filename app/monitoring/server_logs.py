"""Bounded, read-only access to a mounted host systemd journal."""

import asyncio

DEFAULT_LOG_LINES = 50
MAX_LOG_LINES = 200
MAX_JOURNAL_BYTES = 48 * 1024
TRUNCATED = "\n[Вывод ограничен по размеру.]"
MAX_LOG_RESPONSE_BYTES = 3 * MAX_JOURNAL_BYTES + len(TRUNCATED.encode())


class JournalUnavailable(Exception):
    pass


def parse_log_lines(value: str) -> int:
    if len(value) <= 3 and value.isascii() and value.isdecimal():
        count = int(value)
        if 1 <= count <= MAX_LOG_LINES:
            return count
    raise ValueError("Invalid journal line count")


async def read_server_journal(lines: int) -> str:
    if not 1 <= lines <= MAX_LOG_LINES:
        raise ValueError("Invalid journal line count")
    try:
        process = await asyncio.create_subprocess_exec(
            "journalctl", "--directory=/host-journal", "--system", "--quiet", "--no-pager",
            "--utc", "--output=short-iso", "--reverse", f"--lines={lines}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as exc:
        raise JournalUnavailable from exc

    try:
        async with asyncio.timeout(5):
            try:
                data = await process.stdout.readexactly(MAX_JOURNAL_BYTES + 1)
            except asyncio.IncompleteReadError as exc:
                if await process.wait() != 0:
                    raise JournalUnavailable from None
                return exc.partial.decode("utf-8", errors="replace").strip()
            data = data[:MAX_JOURNAL_BYTES]
            complete_lines = data.rpartition(b"\n")[0]
            return (complete_lines or data).decode("utf-8", errors="replace").rstrip() + TRUNCATED
    except TimeoutError as exc:
        raise JournalUnavailable from exc
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.communicate()
