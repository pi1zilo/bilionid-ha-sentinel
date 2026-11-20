"""Read-only Zabbix API for the current problem list."""

import aiohttp


class ZabbixClient:
    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        if self.url and not self.url.endswith("/api_jsonrpc.php"):
            self.url += "/api_jsonrpc.php"
        self.token = token

    async def get_problems(self) -> list[dict]:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "problem.get",
            "params": {
                "output": ["eventid", "name", "clock", "severity"],
                "recent": False,
                "sortfield": ["eventid"],
                "sortorder": "DESC",
                "limit": 15,
            },
        }
        timeout = aiohttp.ClientTimeout(total=5)
        headers = {"Authorization": f"Bearer {self.token}"}
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.url, json=request, headers=headers, allow_redirects=False) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)
        if data.get("error") or not isinstance(data.get("result"), list):
            raise ValueError("Zabbix did not return a problem list")
        return data["result"]
