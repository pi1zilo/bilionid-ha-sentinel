"""Small in-container HTTP health probe."""

import os
import sys
from urllib.request import urlopen


def main() -> None:
    host = os.environ.get("HA_HOST", "0.0.0.0")
    if host == "0.0.0.0":
        host = "127.0.0.1"
    port = os.environ.get("HA_PORT", "29010")
    path = sys.argv[1] if len(sys.argv) == 2 else "/health"
    try:
        with urlopen(f"http://{host}:{port}{path}", timeout=3) as response:
            sys.exit(0 if response.status == 200 else 1)
    except OSError:
        sys.exit(1)


if __name__ == "__main__":
    main()
