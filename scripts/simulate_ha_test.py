"""Read-only smoke test for two running bot nodes."""

import argparse
import asyncio
import os

import aiohttp


async def check_peer(session: aiohttp.ClientSession, url: str, secret: str) -> dict:
    url = url.rstrip("/")
    async with session.get(f"{url}/health", allow_redirects=False) as response:
        response.raise_for_status()
    path = f"{url}/ha/status"
    async with session.get(path, allow_redirects=False) as response:
        if response.status != 401:
            raise ValueError("Authenticated peer endpoint did not reject a request without the key")
    async with session.get(path, headers={"X-HA-Secret": secret}, allow_redirects=False) as response:
        response.raise_for_status()
        return await response.json()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("main")
    parser.add_argument("backup")
    args = parser.parse_args()
    secret = os.environ.get("HA_SECRET_KEY")
    if not secret:
        parser.error("set HA_SECRET_KEY in the environment")
    timeout = aiohttp.ClientTimeout(total=5)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            peers = await asyncio.gather(*(check_peer(session, url, secret) for url in (args.main, args.backup)))
    except (aiohttp.ClientError, TimeoutError, ValueError) as error:
        print(f"Peer check failed: {type(error).__name__}: {error}")
        return 1

    for peer in peers:
        print(f"{peer['node_name']}: {peer['state']}; ready={peer['polling_ready']}")
    polling = sum(bool(peer.get("is_polling")) for peer in peers)
    ready = sum(bool(peer.get("polling_ready")) for peer in peers)
    if (polling, ready) != (1, 1):
        print(f"Expected one healthy poller; polling={polling}, ready={ready}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
