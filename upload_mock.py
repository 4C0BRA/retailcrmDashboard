# coding=utf-8
"""
upload_orders.py
Загрузка заказов из mock_orders.json в RetailCRM v5
через официальный Python-клиент: pip install retailcrm python-dotenv

Как работает библиотека (из исходника):
  - client.orders_upload(orders, site) сериализует список в JSON-строку,
    кладёт в form-data поле 'orders' и шлёт POST
  - API-ключ передаётся заголовком X-API-KEY (не query-параметром!)
  - Возвращает объект Response с методами:
      .is_successful()   → bool  (HTTP < 400)
      .get_status_code() → int
      .get_response()    → dict  (распарсенный JSON)
      .get_errors()      → dict  (поле 'errors' из ответа)
      .get_error_msg()   → str   (поле 'errorMsg' из ответа)
"""

import json
import logging
import os
import sys
from pathlib import Path

import retailcrm
from dotenv import load_dotenv

# ──────────────────────────────────────────────
# Настройка логирования
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Загрузка переменных окружения из .env
# ──────────────────────────────────────────────
load_dotenv()

DOMAIN  = os.getenv("DOMAIN")        # например: demo  (без .retailcrm.ru)
API_KEY = os.getenv("API_KEY")
SITE    = os.getenv("SITE", "default")

# Проверяем обязательные переменные
missing = [name for name, val in (("DOMAIN", DOMAIN), ("API_KEY", API_KEY)) if not val]
if missing:
    log.error("Не заданы переменные окружения: %s", ", ".join(missing))
    sys.exit(1)

# Формируем полный URL магазина
CRM_URL = f"https://{DOMAIN}.retailcrm.ru"

# ──────────────────────────────────────────────
# Шаг 1. Читаем заказы из mock_orders.json
# ──────────────────────────────────────────────
ORDERS_FILE = Path(__file__).parent / "mock_orders1.json"

if not ORDERS_FILE.exists():
    log.error("Файл не найден: %s", ORDERS_FILE)
    sys.exit(1)

log.info("Читаем заказы из файла: %s", ORDERS_FILE)

with open(ORDERS_FILE, encoding="utf-8") as f:
    raw = json.load(f)

# Поддерживаем два формата файла: список [...] или объект {"orders": [...]}
if isinstance(raw, list):
    orders_list = raw
elif isinstance(raw, dict):
    orders_list = raw.get("orders", [])
else:
    log.error("Неожиданный формат файла — ожидается список или объект с ключом 'orders'")
    sys.exit(1)

if not orders_list:
    log.warning("Список заказов пуст — нечего загружать.")
    sys.exit(0)

log.info("Загружено %d заказов из файла.", len(orders_list))

# ──────────────────────────────────────────────
# Шаг 2. Создаём клиент RetailCRM v5
#
# Библиотека сама добавит:
#   - заголовок X-API-KEY: <ваш ключ>
#   - POST-тело: orders=<json-строка>&site=<site>
# ──────────────────────────────────────────────
client = retailcrm.v5(CRM_URL, API_KEY)

log.info("Клиент RetailCRM v5 создан. URL: %s", CRM_URL)

# ──────────────────────────────────────────────
# RetailCRM принимает не более 50 заказов за раз.
# Разбиваем список на батчи.
# ──────────────────────────────────────────────
BATCH_SIZE = 50

def chunked(lst: list, size: int):
    """Генератор — разбивает список на части по size элементов."""
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


# ──────────────────────────────────────────────
# Шаг 3. Отправляем батчи через orders_upload()
#
# Сигнатура метода (из исходника библиотеки):
#   client.orders_upload(orders: list, site: str | None) -> Response
# ──────────────────────────────────────────────
total_uploaded = 0
total_errors   = 0

for batch_num, batch in enumerate(chunked(orders_list, BATCH_SIZE), start=1):
    log.info("Отправляем батч #%d (%d заказов)...", batch_num, len(batch))

    try:
        response = client.orders_upload(batch, SITE)
    except Exception as exc:
        # Ловим сетевые ошибки и ошибки парсинга JSON
        log.error("Ошибка при отправке батча #%d: %s", batch_num, exc)
        continue

    # ── Проверяем HTTP-статус через Response.is_successful() ──
    if not response.is_successful():
        log.error(
            "HTTP %d при загрузке батча #%d. Тело ответа: %s",
            response.get_status_code(),
            batch_num,
            response.get_response(),
        )
        continue

    # ── Получаем тело ответа через Response.get_response() ────
    body = response.get_response()

    # ── Проверяем поле success ─────────────────────────────────
    if not body.get("success"):
        errors = response.get_errors()          # {"field": "сообщение", ...}
        log.error(
            "API вернул success=false в батче #%d. Ошибки: %s",
            batch_num,
            errors,
        )
        total_errors += len(errors)
        continue

    # ── Выводим uploadedOrders ─────────────────────────────────
    uploaded = body.get("uploadedOrders", [])
    log.info(
        "Батч #%d успешно загружен. uploadedOrders (%d шт.): %s",
        batch_num,
        len(uploaded),
        uploaded,
    )
    total_uploaded += len(uploaded)

    # ── Предупреждения API (частичные ошибки внутри батча) ─────
    api_errors = response.get_errors()          # {} если всё чисто
    if api_errors:
        log.warning(
            "Предупреждения API в батче #%d: %s",
            batch_num,
            api_errors,
        )
        total_errors += len(api_errors)

# ──────────────────────────────────────────────
# Итоговая статистика
# ──────────────────────────────────────────────
log.info("=" * 55)
log.info(
    "Итог: загружено заказов = %d, предупреждений/ошибок API = %d",
    total_uploaded,
    total_errors,
)
log.info("=" * 55)