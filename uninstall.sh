#!/usr/bin/env bash
# ==============================================================================
# BilioniD HA Sentinel — Скрипт безопасного удаления
# ==============================================================================
# Удаляет ТОЛЬКО компоненты bilionid-ha-sentinel, не затрагивая другие сервисы
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${BOLD}${RED}"
echo "======================================================================"
echo "          ⚠️  УДАЛЕНИЕ BILIONID HA SENTINEL                            "
echo "======================================================================"
echo -e "${NC}"
echo "Данный скрипт остановит и удалит контейнер bilionid-ha-sentinel."
echo "Сторонние проекты (Beszel, Uptime Kuma, Portainer и др.) НЕ будут затронуты."
echo ""

read -r -p "Вы уверены, что хотите остановить и удалить bilionid-ha-sentinel? [y/N]: " CONFIRM
if [[ ! "$CONFIRM" =~ ^[Yy] ]]; then
    echo -e "${CYAN}Операция отменена пользователем.${NC}"
    exit 0
fi

echo -e "\n${CYAN}[1/4] Остановка и удаление контейнеров Docker Compose...${NC}"
docker compose down --remove-orphans || true

echo -e "\n${CYAN}[2/4] Удаление локального Docker-образа...${NC}"
read -r -p "Удалить Docker-образ bilionid-ha-sentinel:latest? [y/N]: " REMOVE_IMG
if [[ "$REMOVE_IMG" =~ ^[Yy] ]]; then
    docker rmi bilionid-ha-sentinel:latest 2>/dev/null || true
    echo -e "${GREEN}[OK] Docker-образ удален.${NC}"
fi

echo -e "\n${CYAN}[3/4] Обработка файлов конфигурации и базы данных...${NC}"
read -r -p "Создать резервную копию .env и данных перед удалением? [Y/n]: " DO_BACKUP
if [[ ! "$DO_BACKUP" =~ ^[Nn] ]]; then
    BACKUP_FILE="bilionid-sentinel-uninstall-backup-$(date +%Y%m%d_%H%M%S).tar.gz"
    tar -czf "$BACKUP_FILE" .env data/ 2>/dev/null || true
    echo -e "${GREEN}[OK] Резервная копия сохранена в: $BACKUP_FILE${NC}"
fi

read -r -p "Удалить файл .env и директорию data/? [y/N]: " REMOVE_DATA
if [[ "$REMOVE_DATA" =~ ^[Yy] ]]; then
    rm -rf data/ .env
    echo -e "${GREEN}[OK] Конфигурация и данные удалены.${NC}"
else
    echo -e "${YELLOW}[SKIP] Файлы .env и data/ сохранены.${NC}"
fi

echo -e "\n${GREEN}======================================================================"
echo -e "  ✅ Удаление bilionid-ha-sentinel завершено."
echo -e "======================================================================${NC}"
