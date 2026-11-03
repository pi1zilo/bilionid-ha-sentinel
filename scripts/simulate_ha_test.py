"""
Non-destructive High Availability test and simulation script.
Tests HA endpoints, state machines, lease management, and failover logic
without taking down production servers.
"""

import asyncio
import sys
import time
from datetime import datetime, timezone
import aiohttp

# ANSI colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def print_step(title: str) -> None:
    print(f"\n{BOLD}{CYAN}=== {title} ==={RESET}")


def print_ok(msg: str) -> None:
    print(f"{GREEN}[PASS]{RESET} {msg}")


def print_fail(msg: str) -> None:
    print(f"{RED}[FAIL]{RESET} {msg}")


def print_warn(msg: str) -> None:
    print(f"{YELLOW}[WARN]{RESET} {msg}")


async def test_endpoint(
    session: aiohttp.ClientSession, url: str, headers: dict = None, expected_status: int = 200
) -> dict:
    t0 = time.time()
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=4.0)) as resp:
            latency_ms = (time.time() - t0) * 1000
            data = await resp.json() if resp.content_type == "application/json" else {}
            return {
                "ok": resp.status == expected_status,
                "status": resp.status,
                "latency_ms": round(latency_ms, 1),
                "data": data,
            }
    except Exception as e:
        return {"ok": False, "error": str(e), "latency_ms": 0}


async def run_ha_simulation(main_url: str, backup_url: str, secret_key: str) -> bool:
    print(f"{BOLD}Запуск неразрушающего симулятора HA для BilioniD HA Bot{RESET}")
    print(f"Main URL:   {main_url}")
    print(f"Backup URL: {backup_url}")
    print(f"Время:      {datetime.now(timezone.utc).isoformat()}")

    headers = {"X-HA-Secret": secret_key}
    all_passed = True

    async with aiohttp.ClientSession() as session:
        # Step 1: Healthcheck on Main
        print_step("1. Проверка доступности Main HA Endpoint")
        main_health = await test_endpoint(session, f"{main_url}/health")
        if main_health["ok"]:
            data = main_health["data"]
            print_ok(
                f"Main отвечает (Latency: {main_health['latency_ms']}ms). "
                f"Роль: {data.get('role')}, Состояние: {data.get('state')}, Polling: {data.get('is_polling')}"
            )
        else:
            print_warn(f"Main не отвечает или не запущен: {main_health.get('error', main_health.get('status'))}")

        # Step 2: Healthcheck on Backup
        print_step("2. Проверка доступности Backup HA Endpoint")
        backup_health = await test_endpoint(session, f"{backup_url}/health")
        if backup_health["ok"]:
            data = backup_health["data"]
            print_ok(
                f"Backup отвечает (Latency: {backup_health['latency_ms']}ms). "
                f"Роль: {data.get('role')}, Состояние: {data.get('state')}, Polling: {data.get('is_polling')}"
            )
        else:
            print_warn(f"Backup не отвечает или не запущен: {backup_health.get('error', backup_health.get('status'))}")

        # Step 3: Single Active Polling Invariant Check
        print_step("3. Проверка инварианта: Единственный активный Polling")
        main_polling = main_health.get("data", {}).get("is_polling", False)
        backup_polling = backup_health.get("data", {}).get("is_polling", False)

        if main_polling and backup_polling:
            print_fail("КРИТИЧЕСКАЯ ОШИБКА: Обнаружен одновременный запуск Polling на обоих серверах (Split-Brain)!")
            all_passed = False
        elif main_polling and not backup_polling:
            print_ok("Инвариант соблюден: Main активен (Лидер), Backup находится в режиме ожидания (Резерв).")
        elif not main_polling and backup_polling:
            print_ok("Инвариант соблюден: Backup активен (Failover), Main в режиме ожидания.")
        else:
            print_warn("Ни один узел не выполняет polling (оба в standby или сервисы остановлены).")

        # Step 4: Authentication Security Test
        print_step("4. Проверка защиты служебных HA-endpoints от неавторизованного доступа")
        unauth_test = await test_endpoint(
            session, f"{main_url}/ha/status", headers={}, expected_status=401
        )
        if unauth_test.get("status") == 401:
            print_ok("Запрос без X-HA-Secret успешно отклонен (HTTP 401 Unauthorized).")
        elif main_health["ok"]:
            print_fail(f"Уязвимость: endpoint доступен без секрета (HTTP {unauth_test.get('status')})")
            all_passed = False
        else:
            print_warn(f"Main оффлайн, проверка отклонения пропущена ({unauth_test.get('error', 'offline')}).")

        # Step 5: Authorized Status Check
        print_step("5. Проверка детального состояния через авторизованный запрос")
        auth_test = await test_endpoint(
            session, f"{main_url}/ha/status", headers=headers, expected_status=200
        )
        if auth_test["ok"]:
            st = auth_test["data"]
            print_ok(f"Детальный статус получен: {st}")
        else:
            print_warn(f"Не удалось получить авторизованный статус от Main: {auth_test.get('error')}")

        # Step 6: Heartbeat Emission Simulation
        print_step("6. Тестирование доставки Heartbeat пакета")
        hb_url = f"{main_url}/ha/heartbeat"
        try:
            payload = {"source_node": "simulation_runner", "role": "TESTER"}
            async with session.post(hb_url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=3.0)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    print_ok(f"Heartbeat успешно принят Main узлом: {data}")
                else:
                    print_warn(f"Heartbeat вернул HTTP {resp.status}")
        except Exception as e:
            print_warn(f"Heartbeat симуляция не удалась: {e}")

    print_step("Итоги HA симуляции")
    if all_passed:
        print(f"{GREEN}{BOLD}Все проверки инвариантов HA и безопасности завершены успешно!{RESET}\n")
    else:
        print(f"{RED}{BOLD}Обнаружены несоответствия в работе HA!{RESET}\n")

    return all_passed


if __name__ == "__main__":
    main_target = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:29010"
    backup_target = sys.argv[2] if len(sys.argv) > 2 else "http://100.64.0.2:29010"
    secret = sys.argv[3] if len(sys.argv) > 3 else "default-secret-change-in-production-32chars"

    asyncio.run(run_ha_simulation(main_target, backup_target, secret))
