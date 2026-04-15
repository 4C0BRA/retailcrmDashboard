# coding=utf-8
"""
etl_orders.py
Синхронизация заказов RetailCRM v5 → Supabase

Зависимости:
    pip install retailcrm supabase python-dotenv

Таблицы в Supabase создаются через schema.sql (выполнить один раз).

Логика:
  1. Читаем все заказы из RetailCRM постранично (limit=100).
  2. Трансформируем каждый заказ в три плоские структуры:
       orders / order_items / order_payments
  3. Делаем UPSERT в Supabase (конфликт по PK → обновляем).
  4. Логируем прогресс и итоговую статистику.
"""

import logging
import os
import sys
from datetime import datetime, timezone

import retailcrm
from dotenv import load_dotenv
from supabase import create_client, Client

# ──────────────────────────────────────────────
# Логирование
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Переменные окружения
# ──────────────────────────────────────────────
load_dotenv()

# RetailCRM
DOMAIN  = os.getenv("DOMAIN")        # например: myshop  (без .retailcrm.ru)
API_KEY = os.getenv("API_KEY")
SITE    = os.getenv("SITE", "default")

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")   # https://xxxx.supabase.co
SUPABASE_KEY = os.getenv("SUPABASE_KEY")   # service_role key (не anon!)

# Фильтрация: тянуть заказы, обновлённые после этой даты (опционально)
# Формат ISO 8601, например "2024-01-01T00:00:00+00:00"
# Если не задано — тянем все заказы
SYNC_FROM_DATE = os.getenv("SYNC_FROM_DATE", "")

# Проверяем обязательные переменные
_required = {
    "DOMAIN": DOMAIN,
    "API_KEY": API_KEY,
    "SUPABASE_URL": SUPABASE_URL,
    "SUPABASE_KEY": SUPABASE_KEY,
}
missing = [k for k, v in _required.items() if not v]
if missing:
    log.error("Не заданы переменные окружения: %s", ", ".join(missing))
    sys.exit(1)

# ──────────────────────────────────────────────
# Клиенты
# ──────────────────────────────────────────────
crm: retailcrm.v5 = retailcrm.v5(f"https://{DOMAIN}.retailcrm.ru", API_KEY)
sb: Client        = create_client(SUPABASE_URL, SUPABASE_KEY)

log.info("RetailCRM URL : https://%s.retailcrm.ru", DOMAIN)
log.info("Supabase URL  : %s", SUPABASE_URL)

# ──────────────────────────────────────────────
# Трансформация
# ──────────────────────────────────────────────

def _str(val) -> str | None:
    """Безопасное приведение к строке."""
    return str(val) if val is not None else None


def _float(val) -> float | None:
    """Безопасное приведение к float."""
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _address(delivery: dict) -> str | None:
    """Собираем читаемый адрес из вложенного объекта delivery.address."""
    addr = delivery.get("address", {}) or {}
    parts = filter(None, [
        addr.get("city"),
        addr.get("street"),
        addr.get("building"),
        addr.get("flat"),
    ])
    result = ", ".join(parts)
    return result or addr.get("text") or None


def transform_order(raw: dict) -> dict:
    """RetailCRM order dict → плоская запись для таблицы orders."""
    delivery = raw.get("delivery", {}) or {}
    customer = raw.get("customer", {}) or {}
    custom_fields = raw.get("customFields", {}) or {}

    # Имя/фамилия: сначала ищем на уровне заказа, потом в customer
    first_name = raw.get("firstName") or customer.get("firstName")
    last_name  = raw.get("lastName")  or customer.get("lastName")
    patronymic = raw.get("patronymic") or customer.get("patronymic")
    phone      = raw.get("phone")      or customer.get("phone")
    email      = raw.get("email")      or customer.get("email")

    # Город из адреса доставки
    addr = delivery.get("address", {}) or {}
    city = addr.get("city")

    # UTM-источник из кастомных полей
    utm_source = custom_fields.get("utm_source")

    return {
        "id":               raw["id"],
        "external_id":      _str(raw.get("externalId")),
        "number":           _str(raw.get("number")),
        "status":           raw.get("status"),
        "created_at":       raw.get("createdAt"),
        "updated_at":       raw.get("updatedAt"),
        "first_name":       first_name,
        "last_name":        last_name,
        "patronymic":       patronymic,
        "phone":            phone,
        "email":            email,
        "total_summ":       _float(raw.get("totalSumm")),
        "prepay_summ":      _float(raw.get("prepaySum")),
        "delivery_type":    delivery.get("code"),
        "delivery_cost":    _float(delivery.get("cost")),
        "delivery_address": _address(delivery),
        "city":             city,
        "utm_source":       utm_source,
        "site":             raw.get("site"),
        "manager_id":       raw.get("managerId"),
        "order_method":     raw.get("orderMethod"),
        "order_type":       raw.get("orderType"),
        "synced_at":        datetime.now(timezone.utc).isoformat(),
    }


def transform_items(raw: dict) -> list[dict]:
    """RetailCRM order dict → список записей для таблицы order_items."""
    result = []
    for item in raw.get("items", []) or []:
        offer = item.get("offer", {}) or {}
        result.append({
            "id":               item["id"],
            "order_id":         raw["id"],
            "offer_id":         offer.get("id"),
            "offer_external_id": _str(offer.get("externalId")),
            "offer_name":       item.get("productName") or offer.get("name"),
            "article":          offer.get("article"),
            "quantity":         _float(item.get("quantity")),
            "price":            _float(item.get("initialPrice")),
            "purchase_price":   _float(item.get("purchasePrice")),
            "discount_percent": _float(item.get("discountPercent")),
            "synced_at":        datetime.now(timezone.utc).isoformat(),
        })
    return result


def transform_payments(raw: dict) -> list[dict]:
    """RetailCRM order dict → список записей для таблицы order_payments."""
    result = []
    for payment in (raw.get("payments", {}) or {}).values():
        result.append({
            "id":       payment["id"],
            "order_id": raw["id"],
            "type":     payment.get("type"),
            "amount":   _float(payment.get("amount")),
            "status":   payment.get("status"),
            "paid_at":  payment.get("paidAt"),
            "comment":  payment.get("comment"),
            "synced_at": datetime.now(timezone.utc).isoformat(),
        })
    return result


# ──────────────────────────────────────────────
# Получение заказов из RetailCRM (пагинация)
# ──────────────────────────────────────────────
LIMIT = 100   # максимально допустимый лимит для GET /orders

def fetch_all_orders() -> list[dict]:
    """Тянет все страницы заказов из RetailCRM и возвращает плоский список."""
    all_orders: list[dict] = []
    page = 1

    # Фильтры
    filters: dict = {}
    if SYNC_FROM_DATE:
        filters["updatedAtFrom"] = SYNC_FROM_DATE
        log.info("Фильтр: updatedAtFrom = %s", SYNC_FROM_DATE)

    while True:
        log.info("RetailCRM: запрашиваем страницу %d (limit=%d)...", page, LIMIT)
        try:
            response = crm.orders(filters=filters, limit=LIMIT, page=page)
        except Exception as exc:
            log.error("Ошибка запроса к RetailCRM (стр. %d): %s", page, exc)
            break

        if not response.is_successful():
            log.error(
                "HTTP %d от RetailCRM. Ответ: %s",
                response.get_status_code(),
                response.get_response(),
            )
            break

        body   = response.get_response()
        orders = body.get("orders", [])
        pagination = body.get("pagination", {})

        all_orders.extend(orders)

        total_pages = pagination.get("totalPageCount", 1)
        log.info(
            "Получено %d заказов (стр. %d/%d, всего в CRM: %d)",
            len(orders),
            page,
            total_pages,
            pagination.get("totalCount", "?"),
        )

        if page >= total_pages:
            break
        page += 1

    return all_orders


# ──────────────────────────────────────────────
# UPSERT в Supabase
# ──────────────────────────────────────────────
BATCH = 500   # supabase-py рекомендует не более ~1000 строк за раз

def upsert_batch(table: str, rows: list[dict]) -> int:
    """
    Вставляет/обновляет строки в таблицу Supabase батчами.
    Возвращает количество успешно обработанных строк.
    """
    if not rows:
        return 0

    total_ok = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i : i + BATCH]
        try:
            result = (
                sb.table(table)
                .upsert(chunk, on_conflict="id", ignore_duplicates=True)   # игнорируем дубликаты, вставляем только новые
                .execute()
            )
            total_ok += len(result.data) if result.data else len(chunk)
            log.info(
                "  ↳ [%s] upsert батч %d–%d — ОК",
                table,
                i + 1,
                i + len(chunk),
            )
        except Exception as exc:
            log.error("  ↳ [%s] ошибка upsert батча %d–%d: %s", table, i + 1, i + len(chunk), exc)

    return total_ok


# ──────────────────────────────────────────────
# Главный поток
# ──────────────────────────────────────────────
def main() -> None:
    log.info("=" * 58)
    log.info("Старт синхронизации RetailCRM → Supabase")
    log.info("=" * 58)

    # 1. Тянем заказы
    raw_orders = fetch_all_orders()
    if not raw_orders:
        log.warning("Заказы не получены — синхронизация прервана.")
        return

    log.info("Всего получено заказов: %d", len(raw_orders))

    # 2. Трансформируем
    orders_rows:   list[dict] = []
    items_rows:    list[dict] = []
    payments_rows: list[dict] = []

    for raw in raw_orders:
        try:
            orders_rows.append(transform_order(raw))
            items_rows.extend(transform_items(raw))
            payments_rows.extend(transform_payments(raw))
        except Exception as exc:
            log.warning("Ошибка трансформации заказа id=%s: %s", raw.get("id"), exc)

    log.info(
        "Трансформировано: orders=%d, items=%d, payments=%d",
        len(orders_rows),
        len(items_rows),
        len(payments_rows),
    )

    # 3. UPSERT в Supabase
    # Порядок важен: сначала родительская таблица orders,
    # потом дочерние (FK-ограничения)
    log.info("Записываем в Supabase...")

    ok_orders   = upsert_batch("orders",        orders_rows)
    ok_items    = upsert_batch("order_items",   items_rows)
    ok_payments = upsert_batch("order_payments", payments_rows)

    # 4. Итог
    log.info("=" * 58)
    log.info("Готово!")
    log.info("  orders        : %d строк", ok_orders)
    log.info("  order_items   : %d строк", ok_items)
    log.info("  order_payments: %d строк", ok_payments)
    log.info("=" * 58)


if __name__ == "__main__":
    main()