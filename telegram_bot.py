import os
import time
import json
import logging
import signal
import sys
from datetime import datetime, timedelta
import requests
from dotenv import load_dotenv

# ==========================================
# Настройки логирования
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("retail_orders.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ==========================================
# Глобальные переменные и конфигурация
# ==========================================
load_dotenv()

RETAIL_DOMAIN = os.getenv("RETAIL_DOMAIN") or os.getenv("DOMAIN")
RETAIL_API_KEY = os.getenv("RETAIL_API_KEY") or os.getenv("API_KEY")
SITE = os.getenv("SITE", "default")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

POLLING_INTERVAL = 60  # Секунд
THRESHOLD_AMOUNT = 50000  # Выше этой суммы отправляем уведомление
STATE_FILE = "last_check.json"

RUNNING = True

# ==========================================
# Graceful Shutdown (Обработка Ctrl+C)
# ==========================================
def handle_exit(signum, frame):
    global RUNNING
    logger.info("❌ Получен сигнал остановки. Завершаем работу graceful...")
    RUNNING = False

signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

# ==========================================
# Вспомогательные функции
# ==========================================
def get_last_check_time() -> str:
    """Возвращает время последней проверки или time.now() - 1 час, если файла нет"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "last_check" in data:
                    return data["last_check"]
        except Exception as e:
            logger.error(f"Ошибка чтения {STATE_FILE}: {e}")
    
    # Если файла нет или он поврежден, берём время 1 час назад
    fallback_time = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    return fallback_time

def save_last_check_time(time_str: str):
    """Сохраняет время последней успешной проверки"""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_check": time_str}, f)
    except Exception as e:
        logger.error(f"Ошибка сохранения состояния: {e}")

def send_telegram_message(text: str):
    """Отправка сообщения в Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        logger.info("📩 Сообщение успешно доставлено в Telegram")
    except Exception as e:
        logger.error(f"⚠️ Ошибка отправки в Telegram: {e}")

def fetch_new_orders(last_check: str):
    """Получает новые заказы из RetailCRM"""
    if not RETAIL_DOMAIN or not RETAIL_API_KEY:
        logger.error("Ключ или домен RetailCRM не настроены в .env")
        return []

    url = f"https://{RETAIL_DOMAIN}.retailcrm.ru/api/v5/orders"
    
    params = {
        "apiKey": RETAIL_API_KEY,
        "limit": 50,
        "filter[createdAtFrom]": last_check,
        # Закомментируйте строку ниже, если хотите получать все заказы, а не только 'new'
        # "filter[statusGroup]": "new" 
    }

    try:
        response = requests.get(url, params=params, timeout=15)
        
        # Обработка Rate-Limit (429)
        if response.status_code == 429:
            logger.warning("Сработал Rate Limit! Ждём 5 секунд...")
            time.sleep(5)
            return []
            
        response.raise_for_status()
        data = response.json()
        
        if not data.get("success"):
            logger.error(f"RetailCRM API вернул ошибку: {data.get('errorMsg')}")
            return []
            
        return data.get("orders", [])
        
    except requests.exceptions.JSONDecodeError:
        logger.error("Ошибка парсинга JSON (RetailCRM вернул не JSON)")
    except Exception as e:
        logger.error(f"Сетевая ошибка при обращении к RetailCRM: {e}")
        
    return []

# ==========================================
# Основной цикл (Polling)
# ==========================================
def main():
    logger.info("🚀 Запуск Telegram-уведомлений для VIP-заказов (>= 50,000₸)")
    
    while RUNNING:
        last_check = get_last_check_time()
        logger.info(f"🔍 Запрашиваем заказы от: {last_check}")
        
        # Запоминаем время НАЧАЛА запроса для следующей итерации, 
        # чтобы избежать потерь из-за времени ответа
        current_check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        orders = fetch_new_orders(last_check)
        logger.info(f"📦 Найдено заказов: {len(orders)}")
        
        latest_order_time = last_check
        
        for order in orders:
            order_id = order.get("id")
            total_summ = float(order.get("totalSumm", 0))
            created_at = order.get("createdAt")
            
            # Обновляем максимальную дату заказа в текущей пачке
            if created_at and created_at > latest_order_time:
                latest_order_time = created_at
            
            if total_summ > THRESHOLD_AMOUNT:
                first_name = order.get("firstName", "")
                last_name = order.get("lastName", "")
                phone = order.get("phone", "Не указан")
                
                msg = (
                    f"🆕 <b>Новый заказ #{order_id}</b>\n"
                    f"💰 Сумма: <b>{total_summ:,.0f} ₸</b>\n"
                    f"👤 Клиент: {first_name} {last_name}\n"
                    f"📞 Тел: {phone}\n"
                    f"🕒 Время: {created_at}"
                )
                
                logger.info(f"🚨 VIP Заказ! ID: {order_id}, Сумма: {total_summ}")
                send_telegram_message(msg)
            else:
                logger.info(f"ℹ️ Обычный заказ #{order_id} ({total_summ}₸), пропускаем.")

        # Сдвигаем время на 1 секунду вперед от последнего заказа, 
        # чтобы не захватывать его дважды
        if len(orders) > 0 and latest_order_time > last_check:
            # Парсим и добавляем 1 секунду
            try:
                dt = datetime.strptime(latest_order_time, "%Y-%m-%d %H:%M:%S")
                dt += timedelta(seconds=1)
                save_last_check_time(dt.strftime("%Y-%m-%d %H:%M:%S"))
            except Exception:
                save_last_check_time(current_check_time)
        else:
            save_last_check_time(current_check_time)

        # Спим до следующего опроса, но реагируем на Ctrl+C
        for _ in range(POLLING_INTERVAL):
            if not RUNNING:
                break
            time.sleep(1)

    logger.info("👋 Скрипт остановлен.")

if __name__ == "__main__":
    main()
