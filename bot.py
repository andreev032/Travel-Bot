import os
import re
import json
import random as _random
import logging
import asyncio
import traceback
import urllib.request
import urllib.parse
import sqlite3
import threading
import requests
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from posts import CHANNEL_POSTS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN            = "8701321387:AAHwb_WkmrimPtInwDftv8jb0d03gTkogqA"
# CHANNEL_ID     = -1002079377291   # основной канал — вернуть после проверки
CHANNEL_ID       = -1003580791059   # ВРЕМЕННО: тестовый канал для проверки расписания
TEST_CHANNEL_ID  = -1003580791059   # тестовый канал — команда /testpost
MOSCOW_TZ        = ZoneInfo("Europe/Moscow")
# ── Хранилище статистики пользователей (PostgreSQL → SQLite → JSON) ─
_db_backend: str = "none"          # "postgres" | "sqlite" | "json"
_db_conn    = None                 # psycopg2 or sqlite3 connection
_db_lock    = threading.Lock()
_DATA_DIR   = "/app/data"
_SQLITE_PATH = os.path.join(_DATA_DIR, "users.db")
_JSON_PATH   = os.path.join(_DATA_DIR, "users.json")

# ── helpers ──────────────────────────────────────────────────────────

def _ensure_data_dir() -> bool:
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        return True
    except OSError as e:
        logger.warning("Не удалось создать %s: %s", _DATA_DIR, e)
        return False

def _try_postgres() -> bool:
    global _db_conn, _db_backend
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        logger.warning("DATABASE_URL отсутствует в os.environ")
        return False
    logger.info("DATABASE_URL найден: %.30s…", raw)
    try:
        conn = psycopg2.connect(raw, connect_timeout=5)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id    BIGINT PRIMARY KEY,
                    username   TEXT,
                    first_name TEXT,
                    first_seen TIMESTAMP DEFAULT NOW(),
                    last_seen  TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_countries (
                    user_id        BIGINT PRIMARY KEY,
                    username       TEXT,
                    first_name     TEXT,
                    countries_count INTEGER DEFAULT 0,
                    updated_at     TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_flags (
                    user_id        BIGINT,
                    country_code   TEXT,
                    collected_date TEXT,
                    PRIMARY KEY (user_id, country_code)
                )
            """)
        _db_conn    = conn
        _db_backend = "postgres"
        # Логируем количество строк чтобы убедиться что данные сохранились
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM users")
                n_users = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM user_countries")
                n_countries = cur.fetchone()[0]
            logger.info("Бэкенд: PostgreSQL ✓ | users=%d, user_countries=%d", n_users, n_countries)
        except Exception:
            logger.info("Бэкенд: PostgreSQL ✓")
        return True
    except Exception as e:
        logger.error("PostgreSQL недоступен: %s: %s", type(e).__name__, e)
        logger.error(traceback.format_exc())
        return False

def _check_volume() -> None:
    """Диагностика Railway Volume — вызывается при каждом старте."""
    logger.info("── Диагностика /app/data ──────────────────────────")
    logger.info("DB путь : %s", _SQLITE_PATH)

    # Существование и размер файла — главный признак того, сохранился ли Volume
    db_exists = os.path.exists(_SQLITE_PATH)
    db_size   = os.path.getsize(_SQLITE_PATH) if db_exists else 0
    if db_exists:
        logger.info("users.db : СУЩЕСТВУЕТ, размер %d байт", db_size)
    else:
        logger.warning("users.db : НЕ СУЩЕСТВУЕТ (новый деплой без Volume или Volume не подключён)")

    # Проверяем запись в /app/data (Railway Volume должен это уметь)
    test_path = os.path.join(_DATA_DIR, "test.txt")
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(test_path, "w", encoding="utf-8") as f:
            f.write("railway-volume-ok")
        with open(test_path, "r", encoding="utf-8") as f:
            content = f.read()
        logger.info("Запись /app/data : ✓")
    except Exception as e:
        logger.error("Запись /app/data FAILED: %s: %s", type(e).__name__, e)

    # Список файлов в /app/data
    try:
        files = os.listdir(_DATA_DIR)
        logger.info("Файлы в /app/data : %s", files)
    except Exception as e:
        logger.error("os.listdir(%s) FAILED: %s", _DATA_DIR, e)

    logger.info("───────────────────────────────────────────────────")

def _try_sqlite() -> bool:
    global _db_conn, _db_backend
    if not _ensure_data_dir():
        return False
    try:
        conn = sqlite3.connect(_SQLITE_PATH, check_same_thread=False)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id    INTEGER PRIMARY KEY,
                username   TEXT,
                first_name TEXT,
                first_seen TEXT DEFAULT (datetime('now')),
                last_seen  TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_countries (
                user_id         INTEGER PRIMARY KEY,
                username        TEXT,
                first_name      TEXT,
                countries_count INTEGER DEFAULT 0,
                updated_at      TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_flags (
                user_id        INTEGER,
                country_code   TEXT,
                collected_date TEXT,
                PRIMARY KEY (user_id, country_code)
            )
        """)
        conn.commit()
        _db_conn    = conn
        _db_backend = "sqlite"
        # Логируем количество строк чтобы убедиться что данные сохранились
        try:
            n_users     = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            n_countries = conn.execute("SELECT COUNT(*) FROM user_countries").fetchone()[0]
            logger.info("Бэкенд: SQLite ✓  | users=%d, user_countries=%d | путь: %s",
                        n_users, n_countries, _SQLITE_PATH)
        except Exception:
            logger.info("Бэкенд: SQLite (%s) ✓", _SQLITE_PATH)
        return True
    except Exception as e:
        logger.error("SQLite недоступен: %s: %s", type(e).__name__, e)
        logger.error(traceback.format_exc())
        return False

def _init_json() -> None:
    global _db_backend
    _ensure_data_dir()
    _db_backend = "json"
    logger.info("Бэкенд: JSON (%s)", _JSON_PATH)

def _load_json() -> dict:
    try:
        with open(_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_json(data: dict) -> None:
    tmp = _JSON_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _JSON_PATH)

# ── public API ───────────────────────────────────────────────────────

async def init_db(app) -> None:
    """Пробует PostgreSQL, затем SQLite, затем JSON."""
    _check_volume()
    if _try_postgres():
        return
    logger.warning("Переключаемся на SQLite…")
    if _try_sqlite():
        return
    logger.warning("Переключаемся на JSON…")
    _init_json()

async def record_user(user_id: int, username: str | None, first_name: str | None) -> None:
    """Сохраняет/обновляет пользователя в активном бэкенде."""
    try:
        if _db_backend == "postgres":
            with _db_lock:
                with _db_conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO users (user_id, username, first_name)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (user_id) DO UPDATE
                            SET last_seen  = NOW(),
                                username   = EXCLUDED.username,
                                first_name = EXCLUDED.first_name
                    """, (user_id, username, first_name))
        elif _db_backend == "sqlite":
            now = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S")
            with _db_lock:
                _db_conn.execute("""
                    INSERT INTO users (user_id, username, first_name, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (user_id) DO UPDATE
                        SET last_seen  = excluded.last_seen,
                            username   = excluded.username,
                            first_name = excluded.first_name
                """, (user_id, username, first_name, now, now))
                _db_conn.commit()
        elif _db_backend == "json":
            now = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S")
            with _db_lock:
                data = _load_json()
                key  = str(user_id)
                if key not in data:
                    data[key] = {"username": username or "", "first_name": first_name or "",
                                 "first_seen": now, "last_seen": now}
                else:
                    data[key].update({"username": username or "", "first_name": first_name or "",
                                      "last_seen": now})
                _save_json(data)
        else:
            return
        logger.info("record_user: user_id=%s сохранён [%s] ✓", user_id, _db_backend)
    except Exception as e:
        logger.error("record_user: ошибка user_id=%s [%s]: %s: %s",
                     user_id, _db_backend, type(e).__name__, e)

def upsert_countries_count(user_id: int, username: str | None,
                           first_name: str | None, count: int) -> None:
    """Сохраняет количество отмеченных стран пользователя в user_countries."""
    try:
        if _db_backend == "postgres":
            with _db_lock:
                with _db_conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO user_countries (user_id, username, first_name, countries_count, updated_at)
                        VALUES (%s, %s, %s, %s, NOW())
                        ON CONFLICT (user_id) DO UPDATE SET
                            username        = EXCLUDED.username,
                            first_name      = EXCLUDED.first_name,
                            countries_count = EXCLUDED.countries_count,
                            updated_at      = NOW()
                    """, (user_id, username, first_name, count))
        elif _db_backend == "sqlite":
            now = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S")
            with _db_lock:
                _db_conn.execute("""
                    INSERT INTO user_countries (user_id, username, first_name, countries_count, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        username        = excluded.username,
                        first_name      = excluded.first_name,
                        countries_count = excluded.countries_count,
                        updated_at      = excluded.updated_at
                """, (user_id, username, first_name, count, now))
                _db_conn.commit()
        logger.info("upsert_countries_count: user_id=%s count=%s ✓", user_id, count)
    except Exception as e:
        logger.error("upsert_countries_count: ошибка user_id=%s: %s: %s", user_id, type(e).__name__, e)


def get_countries_rating(user_id: int) -> tuple[list[dict], int]:
    """Возвращает (топ-30 список, позиция текущего пользователя).
    Каждый элемент: {"name": str, "count": int}.
    Позиция — 1-based, 0 если пользователь не в рейтинге.
    """
    try:
        if _db_backend in ("postgres", "sqlite"):
            with _db_lock:
                if _db_backend == "postgres":
                    with _db_conn.cursor() as cur:
                        cur.execute("""
                            SELECT user_id, first_name, username, countries_count
                            FROM user_countries
                            WHERE countries_count > 0
                            ORDER BY countries_count DESC, updated_at ASC
                        """)
                        rows = cur.fetchall()
                else:
                    cur = _db_conn.execute("""
                        SELECT user_id, first_name, username, countries_count
                        FROM user_countries
                        WHERE countries_count > 0
                        ORDER BY countries_count DESC, updated_at ASC
                    """)
                    rows = cur.fetchall()

            top30, my_pos, my_count = [], 0, 0
            for pos, (uid, fname, uname, cnt) in enumerate(rows, 1):
                name = fname or (f"@{uname}" if uname else f"id{uid}")
                if pos <= 30:
                    top30.append({"name": name, "count": cnt})
                if uid == user_id:
                    my_pos, my_count = pos, cnt
            return top30, my_pos, my_count
    except Exception as e:
        logger.error("get_countries_rating: %s: %s", type(e).__name__, e)
    return [], 0, 0


def _get_stats() -> dict:
    """Возвращает dict(total, new_7, new_30, active_today, since) из активного бэкенда."""
    if _db_backend == "postgres":
        with _db_lock:
            with _db_conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM users")
                total = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM users WHERE first_seen >= NOW() - INTERVAL '7 days'")
                new_7 = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM users WHERE first_seen >= NOW() - INTERVAL '30 days'")
                new_30 = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM users WHERE last_seen >= CURRENT_DATE")
                active_today = cur.fetchone()[0]
                cur.execute("SELECT MIN(first_seen) FROM users")
                row = cur.fetchone()[0]
                since = row.strftime("%d.%m.%Y") if row else "нет данных"
    elif _db_backend == "sqlite":
        with _db_lock:
            cur = _db_conn.execute("SELECT COUNT(*) FROM users")
            total = cur.fetchone()[0]
            cur = _db_conn.execute(
                "SELECT COUNT(*) FROM users WHERE first_seen >= datetime('now', '-7 days')")
            new_7 = cur.fetchone()[0]
            cur = _db_conn.execute(
                "SELECT COUNT(*) FROM users WHERE first_seen >= datetime('now', '-30 days')")
            new_30 = cur.fetchone()[0]
            cur = _db_conn.execute(
                "SELECT COUNT(*) FROM users WHERE last_seen >= date('now')")
            active_today = cur.fetchone()[0]
            cur = _db_conn.execute("SELECT MIN(first_seen) FROM users")
            row = cur.fetchone()[0]
            if row:
                y, m, d = row[:10].split("-")
                since = f"{d}.{m}.{y}"
            else:
                since = "нет данных"
    elif _db_backend == "json":
        data  = _load_json()
        total = len(data)
        now   = datetime.now(MOSCOW_TZ)
        cut7  = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        cut30 = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        today = now.strftime("%Y-%m-%d")
        new_7        = sum(1 for u in data.values() if u.get("first_seen", "") >= cut7)
        new_30       = sum(1 for u in data.values() if u.get("first_seen", "") >= cut30)
        active_today = sum(1 for u in data.values() if u.get("last_seen", "").startswith(today))
        all_first = [u["first_seen"][:10] for u in data.values() if u.get("first_seen")]
        if all_first:
            y, m, d = min(all_first).split("-")
            since = f"{d}.{m}.{y}"
        else:
            since = "нет данных"
    else:
        total = new_7 = new_30 = active_today = 0
        since = "нет данных"
    return {"total": total, "new_7": new_7, "new_30": new_30, "active_today": active_today,
            "since": since}

# ── Персистентный индекс поста (сохраняется между перезапусками) ─
POST_INDEX_FILE = os.path.join(os.path.dirname(__file__), "post_index.json")

def _load_post_index() -> int:
    """Читает сохранённый индекс из post_index.json; возвращает 0 если файл не существует."""
    try:
        if os.path.exists(POST_INDEX_FILE):
            with open(POST_INDEX_FILE, "r", encoding="utf-8") as f:
                return int(json.load(f).get("index", 0))
    except (ValueError, KeyError, OSError, json.JSONDecodeError):
        pass
    return 0

def _save_post_index(idx: int) -> None:
    """Атомарно сохраняет индекс через временный файл."""
    tmp = POST_INDEX_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"index": idx}, f)
    os.replace(tmp, POST_INDEX_FILE)

# Счётчик текущего поста — перебираем по кругу; восстанавливается после перезапуска
_post_index = _load_post_index()

MAIN_MENU, ANSWERING, HELP_MENU, HELP_TOPIC, TRANSLATING, VISA_MENU, VISA_CATEGORY, \
    MOVIES_MENU, MOVIES_REGION, MOVIES_LIST, INCOMPATIBLE_MENU, INCOMPATIBLE_TOPIC, \
    DRONE_MENU, DRONE_SECTION, SEASON_MENU, SEASON_REGION, \
    LOUNGE_MENU, LOUNGE_SECTION, \
    SUPPORT_MENU, SUPPORT_TYPING, \
    CRUISE_MENU, CRUISE_SECTION, \
    WONDERS_MENU, WONDERS_SEVEN_MENU, WONDERS_SECTION, UNESCO_MENU, UNESCO_REGION, \
    PARTNERS_MENU, \
    TOURS_MENU, TOURS_TYPING, \
    DESTINY_TYPING, \
    QUIZ_ACTIVE, \
    GAMES_MENU, GUESS_ACTIVE, PAIR_ACTIVE, \
    COUNTRY_OF_DAY, \
    SHOP_MENU, SHOP_TYPING = range(38)

# Замени на реальный HTTPS-URL после деплоя webapp/index.html
WEBAPP_URL      = "https://andreev032.github.io/Travel-Bot/"
MAP_URL         = "https://andreev032.github.io/Travel-Bot/map.html"
CHECKLIST_URL   = "https://andreev032.github.io/Travel-Bot/checklist.html"
STATS_URL       = "https://andreev032.github.io/Travel-Bot/stats.html"
CURRENCY_URL    = "https://andreev032.github.io/Travel-Bot/currency.html"
SPLITWISE_URL   = "https://andreev032.github.io/Travel-Bot/splitwise.html"
TIMEZONE_URL    = "https://andreev032.github.io/Travel-Bot/timezone.html"
ATTRACTIONS_URL = "https://andreev032.github.io/Travel-Bot/attractions.html"
RUSSIA_URL      = "https://andreev032.github.io/Travel-Bot/russia.html"
DIARY_URL       = "https://andreev032.github.io/Travel-Bot/diary.html"
DISTANCE_URL    = "https://andreev032.github.io/Travel-Bot/distance.html"
CHANNEL_URL     = "https://t.me/like_a_local"


HOME_BTN    = "🏠 Главное меню"
CHANNEL_BTN = "📢 Наш канал"
SHOP_BTN    = "🛒 Магазин"
ADMIN_ID    = 462171750       # доступ к /stats и служебным командам


def get_main_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🧭 Планирование"),    KeyboardButton("🛠 Инструменты")],
            [KeyboardButton("🗺 Мои путешествия"), KeyboardButton("📚 Знания")],
            [KeyboardButton("🎮 Игры"),             KeyboardButton("✈️ Услуги")],
            [KeyboardButton("🤝 Партнёры"),         KeyboardButton(SHOP_BTN)],
            [KeyboardButton("⭐ Премиум"),           KeyboardButton("🆘 Поддержка")],
            [KeyboardButton(CHANNEL_BTN)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def get_folder_planning_kb():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🌍 Подобрать страну"),       KeyboardButton("🔮 Страна по судьбе")],
            [KeyboardButton("🌤 Сезоны путешествий"),     KeyboardButton("🛂 Визы")],
            [KeyboardButton("⛔ Несовместимые страны"),    KeyboardButton("✅ Чеклист", web_app=WebAppInfo(url=CHECKLIST_URL))],
            [KeyboardButton("◀️ Назад"),                   KeyboardButton(HOME_BTN)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def get_folder_tools_kb():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🔤 Переводчик"),
             KeyboardButton("💱 Конвертер валют", web_app=WebAppInfo(url=CURRENCY_URL))],
            [KeyboardButton("🕐 Разница во времени", web_app=WebAppInfo(url=TIMEZONE_URL)),
             KeyboardButton("💰 Общий счёт",         web_app=WebAppInfo(url=SPLITWISE_URL))],
            [KeyboardButton("🗺 Карта мира",          web_app=WebAppInfo(url=MAP_URL))],
            [KeyboardButton("◀️ Назад"),              KeyboardButton(HOME_BTN)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def get_folder_mytrips_kb():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🗺 Мои страны",                  web_app=WebAppInfo(url=WEBAPP_URL)),
             KeyboardButton("🏆 Рейтинг путешественников")],
            [KeyboardButton("🇷🇺 Путешествия по России",      web_app=WebAppInfo(url=RUSSIA_URL)),
             KeyboardButton("🏛 Мои достопримечательности",   web_app=WebAppInfo(url=ATTRACTIONS_URL))],
            [KeyboardButton("📊 Моя статистика",              web_app=WebAppInfo(url=STATS_URL)),
             KeyboardButton("📏 Калькулятор расстояний",      web_app=WebAppInfo(url=DISTANCE_URL))],
            [KeyboardButton("📖 Дневник путешественника",     web_app=WebAppInfo(url=DIARY_URL))],
            [KeyboardButton("◀️ Назад"),                      KeyboardButton(HOME_BTN)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def get_folder_knowledge_kb():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📖 Инструкция для новичка"), KeyboardButton("🚁 Дроны")],
            [KeyboardButton("🛋 Лаунджи аэропортов"),     KeyboardButton("🚢 Круизы")],
            [KeyboardButton("🎬 Фильмы о путешествиях"),  KeyboardButton("🏛 Чудеса и наследие")],
            [KeyboardButton("◀️ Назад"),                   KeyboardButton(HOME_BTN)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def get_folder_services_kb():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📚 Путеводители"),  KeyboardButton("✈️ Авторские туры")],
            [KeyboardButton("🛃 Оформить визу")],
            [KeyboardButton("◀️ Назад"),          KeyboardButton(HOME_BTN)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def show_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает топ-30 путешественников по количеству стран."""
    user = update.effective_user
    top30, my_pos, my_count = get_countries_rating(user.id)

    if not top30:
        await update.message.reply_text(
            "🏆 *Рейтинг путешественников*\n\n"
            "Рейтинг пока пуст. Отмечай страны в «Мои страны» чтобы появиться в рейтинге! 🌍",
            parse_mode="Markdown",
            reply_markup=get_folder_mytrips_kb(),
        )
        return MAIN_MENU

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = ["🏆 *Рейтинг путешественников*\n"]
    for i, entry in enumerate(top30, 1):
        medal = medals.get(i, f"{i}.")
        lines.append(f"{medal} {entry['name']} — {entry['count']} стран")

    if my_pos:
        lines.append(f"\n*Твоё место:* #{my_pos} с {my_count} странами")
    else:
        lines.append("\nТебя пока нет в рейтинге")

    lines.append("\nОтмечай страны в «Мои страны» чтобы подняться в рейтинге! 🌍")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=get_folder_mytrips_kb(),
    )
    return MAIN_MENU


async def go_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "🏠 Главное меню:",
        reply_markup=get_main_keyboard(),
    )
    return MAIN_MENU


# ── 🔮 Страна по судьбе — нумерология ───────────────────────────────────────

_DESTINY_MAP = {
    1: {
        "meaning": "Лидерство, независимость, первопроходцы",
        "main":    ("🇺🇸 США", "Страна, построенная первопроходцами — бесконечные горизонты, свобода выбора, Гранд-Каньон и право идти своим путём. Здесь рождаются лидеры."),
        "extra":   ["🇮🇸 Исландия — край земли для тех, кто не боится неизведанного", "🇦🇺 Австралия — континент смелых и независимых духом", "🇳🇴 Норвегия — страна викингов и первооткрывателей морей"],
    },
    2: {
        "meaning": "Гармония, красота, баланс",
        "main":    ("🇯🇵 Япония", "Страна совершенного баланса — сакура и технологии, чайная церемония и неон Токио, вабисаби и точность. Красота здесь — философия жизни."),
        "extra":   ["🇨🇭 Швейцария — идеальная гармония Альп, озёр и часового механизма", "🇲🇻 Мальдивы — абсолютный покой над бирюзовой лагуной", "🇹🇭 Таиланд — улыбки, храмы и баланс между суетой и покоем"],
    },
    3: {
        "meaning": "Творчество, общение, искусство",
        "main":    ("🇮🇹 Италия", "Родина Возрождения — Микеланджело, Да Винчи, пицца и опера. Каждый город — музей, каждый ужин — произведение искусства."),
        "extra":   ["🇫🇷 Франция — мода, импрессионизм и высокая гастрономия", "🇧🇷 Бразилия — карнавал, самба и неудержимый творческий взрыв", "🇪🇸 Испания — Гауди, фламенко и уличная жизнь как перформанс"],
    },
    4: {
        "meaning": "Стабильность, история, традиции",
        "main":    ("🇩🇪 Германия", "Страна порядка и тысячелетней истории — замки Баварии, Берлин, Октоберфест и немецкая точность, проверенная веками."),
        "extra":   ["🇨🇳 Китай — 5000 лет непрерывной цивилизации и Великая стена", "🇪🇬 Египет — пирамиды и фараоны, пережившие тысячелетия", "🇬🇷 Греция — колыбель демократии, философии и западной цивилизации"],
    },
    5: {
        "meaning": "Свобода, приключения, перемены",
        "main":    ("🇹🇭 Таиланд", "Страна бесконечных перемен — каждый остров другой, каждый базар новый, и всё это в движении: сегодня джунгли, завтра море."),
        "extra":   ["🇲🇦 Марокко — сук, пустыня Сахара и берберские деревни в горах", "🇨🇴 Колумбия — вечная весна, кофе и карибский азарт", "🇻🇳 Вьетнам — от рисовых полей севера до пляжей юга на одном дыхании"],
    },
    6: {
        "meaning": "Уют, семья, природа",
        "main":    ("🇦🇹 Австрия", "Страна уюта и семейных ценностей — венские кафе с штруделем, Штраус, рождественские рынки и Альпы за каждым окном."),
        "extra":   ["🇳🇿 Новая Зеландия — самое спокойное и безопасное место южного полушария", "🇬🇷 Греция — средиземноморское тепло, гостеприимство и застолья до рассвета", "🇨🇭 Швейцария — природа и порядок как синонимы уюта"],
    },
    7: {
        "meaning": "Духовность, тайны, мудрость",
        "main":    ("🇮🇳 Индия", "Страна духовных практик и вечных вопросов — Варанаси, Тадж-Махал, аюрведа и мокша. Место, откуда возвращаются другими людьми."),
        "extra":   ["🇵🇪 Перу — Мачу-Пикчу и нераскрытые тайны цивилизации инков", "🇳🇵 Непал — Гималаи, буддийские монастыри и путь к себе", "🇰🇭 Камбоджа — Ангкор Ват и мистика затерянных храмов в джунглях"],
    },
    8: {
        "meaning": "Сила, достаток, амбиции",
        "main":    ("🇦🇪 ОАЭ", "Страна амбиций без границ — Бурдж-Халифа, острова построенные в море, и воля превратить пустыню в мировой центр роскоши и бизнеса."),
        "extra":   ["🇸🇬 Сингапур — самый эффективный город-государство на планете", "🇭🇰 Гонконг — финансовая мощь и небоскрёбы над морем", "🇰🇷 Южная Корея — K-pop, Samsung и экономическое чудо за 50 лет", "🇨🇳 Китай — вторая экономика мира и великие инфраструктурные проекты"],
    },
    9: {
        "meaning": "Путешествия, гуманизм, открытость миру",
        "main":    ("🇪🇸 Испания", "Страна открытых людей — Гауди, фламенко, Камино-де-Сантьяго и палитра культур: кастильцы, каталонцы, баски под одним солнцем."),
        "extra":   ["🇦🇷 Аргентина — от ледников Патагонии до танго Буэнос-Айреса", "🇿🇦 ЮАР — мыс Доброй Надежды и радуга 11 официальных народов", "🇵🇹 Португалия — фаду, атлантический горизонт и открытость к миру"],
    },
    11: {
        "meaning": "Мастер-число: интуиция, вдохновение",
        "main":    ("🇮🇪 Ирландия", "Страна кельтской интуиции — скалы Мохер, туманные холмы, пабы с живой музыкой и ощущение, что каждый камень здесь что-то помнит."),
        "extra":   ["🇮🇸 Исландия — земля, где природа буквально говорит с тобой через гейзеры и северное сияние", "🇳🇴 Норвегия — фьорды и полярное сияние как медитация в движении"],
    },
    22: {
        "meaning": "Мастер-число: великие свершения, масштаб",
        "main":    ("🇨🇳 Китай", "Страна великих свершений — Великая стена видна из космоса, космическая программа, 5000 лет цивилизации и скачок в будущее за одно поколение."),
        "extra":   ["🇪🇬 Египет — монументальность пирамид, построенных без современных технологий", "🇮🇳 Индия — цивилизация масштаба, давшая миру математику, шахматы и йогу", "🇵🇪 Перу — Мачу-Пикчу и инженерный гений инков на высоте 2400 м"],
    },
    33: {
        "meaning": "Мастер-число: мудрость, служение людям",
        "main":    ("🇮🇳 Индия", "Страна высшего служения — Ганди, мать Тереза, ашрамы Ришикеша. Здесь тысячелетиями учат тому, что смысл жизни — в отдаче, а не в накоплении."),
        "extra":   ["🏔 Тибет / 🇳🇵 Непал — крыша мира, где монахи живут ради просветления других", "🇯🇵 Япония — бусидо, мастерство ремесленника и служение красоте как долг", "🇱🇰 Шри-Ланка — буддийские храмы, чайные плантации и простота как мудрость"],
    },
}

def _calc_destiny(dob: str) -> int | None:
    """
    Принимает строку ДД.ММ.ГГГГ, возвращает число судьбы (1–9, 11, 22, 33)
    или None если формат неверный.
    """
    try:
        digits = [int(c) for c in dob if c.isdigit()]
        if len(digits) != 8:
            return None
        # Проверяем что дата реальная
        d, m, y = int(dob[:2]), int(dob[3:5]), int(dob[6:])
        datetime(y, m, d)  # бросит ValueError если дата невалидна
    except (ValueError, IndexError):
        return None

    n = sum(digits)
    while n > 9 and n not in (11, 22, 33):
        n = sum(int(c) for c in str(n))
    return n


async def destiny_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вход в раздел 🔮 Страна по судьбе."""
    await update.message.reply_text(
        "🔮 *Страна по судьбе*\n\n"
        "Введи дату рождения в формате *ДД.ММ.ГГГГ*\n"
        "_(например: 14.04.1990)_\n\n"
        "Я посчитаю твоё число судьбы и найду страну, созданную специально для тебя ✨",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            [["◀️ Назад", HOME_BTN]], resize_keyboard=True
        ),
    )
    return DESTINY_TYPING


async def destiny_typing_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает дату рождения и выдаёт результат."""
    text = update.message.text.strip()
    if text == HOME_BTN:
        return await go_home(update, context)
    if text == "◀️ Назад":
        return await show_folder_planning(update, context)

    number = _calc_destiny(text)
    if number is None:
        await update.message.reply_text(
            "⚠️ Не могу распознать дату. Введи в формате *ДД.ММ.ГГГГ*, например: *14.04.1990*",
            parse_mode="Markdown",
        )
        return DESTINY_TYPING

    info = _DESTINY_MAP[number]
    main_country, main_reason = info["main"]
    extra_lines = "\n".join(f"✨ {c}" for c in info["extra"])

    result = (
        f"🔮 *Твоё число судьбы: {number}*\n"
        f"_{info['meaning']}_\n\n"
        f"🌟 *Твоя страна: {main_country}*\n"
        f"{main_reason}\n\n"
        f"*Также подойдут:*\n{extra_lines}"
    )
    await update.message.reply_text(
        result,
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            [["🔮 Другая дата"], ["◀️ Назад", HOME_BTN]], resize_keyboard=True
        ),
    )
    return DESTINY_TYPING


# ── 🧠 Викторина о путешествиях ─────────────────────────────────────────────

_QUIZ_QUESTIONS = [
    {
        "q": "Какая страна самая большая в мире?",
        "options": ["а) Китай", "б) Россия", "в) США", "г) Канада"],
        "correct": "б) Россия",
        "explanation": "Россия занимает около 17,1 млн км² — больше любой другой страны.",
    },
    {
        "q": "В какой стране находится Мачу-Пикчу?",
        "options": ["а) Бразилия", "б) Колумбия", "в) Перу", "г) Чили"],
        "correct": "в) Перу",
        "explanation": "Мачу-Пикчу — древний город инков в Андах, Перу.",
    },
    {
        "q": "Сколько стран в мире?",
        "options": ["а) 157", "б) 175", "в) 201", "г) 210"],
        "correct": "в) 201",
        "explanation": "В нашем боте 201: 195 стран ООН (193 члена + 2 наблюдателя) + 6 особых территорий.",
    },
    {
        "q": "В какой стране самое большое население?",
        "options": ["а) Индия", "б) Китай", "в) США", "г) Индонезия"],
        "correct": "а) Индия",
        "explanation": "С 2023 года Индия обогнала Китай и стала самой населённой страной.",
    },
    {
        "q": "Где находится Эйфелева башня?",
        "options": ["а) Лондон", "б) Рим", "в) Берлин", "г) Париж"],
        "correct": "г) Париж",
        "explanation": "Эйфелева башня построена в Париже в 1889 году к Всемирной выставке.",
    },
    {
        "q": "Какая река самая длинная в мире?",
        "options": ["а) Амазонка", "б) Нил", "в) Янцзы", "г) Миссисипи"],
        "correct": "б) Нил",
        "explanation": "Нил (около 6 650 км) считается самой длинной рекой мира.",
    },
    {
        "q": "В какой стране находится Ангкор-Ват?",
        "options": ["а) Таиланд", "б) Вьетнам", "в) Камбоджа", "г) Лаос"],
        "correct": "в) Камбоджа",
        "explanation": "Ангкор-Ват — крупнейший храмовый комплекс мира, построен в XII веке в Камбодже.",
    },
    {
        "q": "Какой океан самый большой?",
        "options": ["а) Атлантический", "б) Индийский", "в) Северный Ледовитый", "г) Тихий"],
        "correct": "г) Тихий",
        "explanation": "Тихий океан занимает около 165 млн км² — больше всей суши вместе взятой.",
    },
    {
        "q": "В какой стране находится Петра?",
        "options": ["а) Израиль", "б) Иордания", "в) Египет", "г) Ливан"],
        "correct": "б) Иордания",
        "explanation": "Петра — древний город, вырубленный в скалах, одно из 7 чудес света, находится в Иордании.",
    },
    {
        "q": "Какая гора самая высокая?",
        "options": ["а) К2", "б) Эверест", "в) Килиманджаро", "г) Монблан"],
        "correct": "б) Эверест",
        "explanation": "Эверест (8 849 м) — высочайшая вершина Земли на границе Непала и Китая.",
    },
    {
        "q": "В какой стране изобрели пиццу?",
        "options": ["а) Греция", "б) Испания", "в) Италия", "г) Франция"],
        "correct": "в) Италия",
        "explanation": "Пицца родом из Неаполя, Италия. Пицца маргарита создана в 1889 году.",
    },
    {
        "q": "Сколько часовых поясов в России?",
        "options": ["а) 9", "б) 11", "в) 13", "г) 15"],
        "correct": "б) 11",
        "explanation": "Россия охватывает 11 часовых поясов — от UTC+2 до UTC+12.",
    },
    {
        "q": "Какая страна имеет больше всего островов?",
        "options": ["а) Филиппины", "б) Индонезия", "в) Норвегия", "г) Швеция"],
        "correct": "г) Швеция",
        "explanation": "Швеция насчитывает около 220 000 островов — больше, чем любая другая страна.",
    },
    {
        "q": "В какой стране находится Колизей?",
        "options": ["а) Греция", "б) Испания", "в) Италия", "г) Португалия"],
        "correct": "в) Италия",
        "explanation": "Колизей — античный амфитеатр в Риме, построен в 70–80 году н.э.",
    },
    {
        "q": "Какой город самый населённый в мире?",
        "options": ["а) Пекин", "б) Шанхай", "в) Токио", "г) Дели"],
        "correct": "в) Токио",
        "explanation": "Токийская агломерация — крупнейшая в мире с населением около 37 млн человек.",
    },
    {
        "q": "В какой стране находится Тадж-Махал?",
        "options": ["а) Пакистан", "б) Бангладеш", "в) Индия", "г) Непал"],
        "correct": "в) Индия",
        "explanation": "Тадж-Махал — мавзолей в Агре, Индия, построен императором Шах-Джаханом.",
    },
    {
        "q": "Какая страна производит больше всего чая?",
        "options": ["а) Индия", "б) Китай", "в) Шри-Ланка", "г) Япония"],
        "correct": "б) Китай",
        "explanation": "Китай производит около 3 млн тонн чая в год — более половины мирового производства.",
    },
    {
        "q": "В какой стране находится Саграда Фамилия?",
        "options": ["а) Португалия", "б) Италия", "в) Франция", "г) Испания"],
        "correct": "г) Испания",
        "explanation": "Саграда Фамилия — базилика в Барселоне, строится с 1882 года по проекту Гауди.",
    },
    {
        "q": "Какое море самое солёное?",
        "options": ["а) Красное", "б) Мёртвое", "в) Каспийское", "г) Средиземное"],
        "correct": "б) Мёртвое",
        "explanation": "Солёность Мёртвого моря — около 34%, что в 10 раз выше обычного океана.",
    },
    {
        "q": "В какой стране находится Большой Барьерный риф?",
        "options": ["а) Новая Зеландия", "б) Индонезия", "в) Австралия", "г) Филиппины"],
        "correct": "в) Австралия",
        "explanation": "Большой Барьерный риф у берегов Австралии — крупнейшая коралловая система мира.",
    },
    {
        "q": "Какая страна имеет самую длинную береговую линию?",
        "options": ["а) Россия", "б) Норвегия", "в) Канада", "г) Австралия"],
        "correct": "в) Канада",
        "explanation": "Береговая линия Канады составляет около 202 080 км — первое место в мире.",
    },
    {
        "q": "В какой стране находится Стоунхендж?",
        "options": ["а) Ирландия", "б) Шотландия", "в) Англия", "г) Уэльс"],
        "correct": "в) Англия",
        "explanation": "Стоунхендж — доисторический мегалитический комплекс в графстве Уилтшир, Англия.",
    },
    {
        "q": "Какой континент самый маленький?",
        "options": ["а) Европа", "б) Австралия", "в) Антарктида", "г) Южная Америка"],
        "correct": "б) Австралия",
        "explanation": "Австралия — наименьший материк, занимающий около 7,7 млн км².",
    },
    {
        "q": "В какой стране находится Килиманджаро?",
        "options": ["а) Кения", "б) Танзания", "в) Эфиопия", "г) Уганда"],
        "correct": "б) Танзания",
        "explanation": "Килиманджаро (5 895 м) — высочайшая вершина Африки, расположена в Танзании.",
    },
    {
        "q": "Какая страна имеет больше всего языков?",
        "options": ["а) Индия", "б) Китай", "в) Папуа Новая Гвинея", "г) Россия"],
        "correct": "в) Папуа Новая Гвинея",
        "explanation": "В Папуа Новой Гвинее говорят более чем на 800 языках — абсолютный мировой рекорд.",
    },
    {
        "q": "В какой стране находится Ниагарский водопад?",
        "options": ["а) Только в США", "б) Только в Канаде", "в) На границе США и Канады", "г) В Мексике"],
        "correct": "в) На границе США и Канады",
        "explanation": "Ниагарский водопад находится на границе штата Нью-Йорк (США) и провинции Онтарио (Канада).",
    },
    {
        "q": "Какой город является самым высокогорным столичным в мире?",
        "options": ["а) Лхаса", "б) Куско", "в) Ла-Пас", "г) Кито"],
        "correct": "в) Ла-Пас",
        "explanation": "Ла-Пас (Боливия) расположен на высоте около 3 640 м — самая высокогорная столица мира.",
    },
    {
        "q": "В какой стране находится Помпеи?",
        "options": ["а) Греция", "б) Хорватия", "в) Италия", "г) Испания"],
        "correct": "в) Италия",
        "explanation": "Помпеи — древний римский город у подножия Везувия, погибший при извержении 79 года н.э.",
    },
    {
        "q": "Какая страна имеет наибольшее количество объектов ЮНЕСКО?",
        "options": ["а) Франция", "б) Германия", "в) Китай", "г) Италия"],
        "correct": "г) Италия",
        "explanation": "Италия занимает первое место по числу объектов Всемирного наследия ЮНЕСКО.",
    },
    {
        "q": "В какой стране находится Дворец Потала?",
        "options": ["а) Япония", "б) Монголия", "в) Тибет/Китай", "г) Непал"],
        "correct": "в) Тибет/Китай",
        "explanation": "Дворец Потала в Лхасе (Тибет, Китай) — зимняя резиденция Далай-ламы, объект ЮНЕСКО.",
    },
    # ── 🍜 Угадай блюдо ──────────────────────────────────────────────
    {
        "q": "🍜 Том ям — суп с кокосовым молоком и острыми специями. Из какой страны это блюдо?",
        "options": ["а) Вьетнам", "б) Индия", "в) Таиланд", "г) Китай"],
        "correct": "в) Таиланд",
        "explanation": "Том ям — визитная карточка тайской кухни, острый суп с лемонграссом, кафирским лаймом и грибами.",
    },
    {
        "q": "🍜 Пад тай — жареная рисовая лапша с креветками и арахисом. Из какой страны?",
        "options": ["а) Китай", "б) Таиланд", "в) Япония", "г) Корея"],
        "correct": "б) Таиланд",
        "explanation": "Пад тай — одно из самых популярных блюд Таиланда, жареная лапша с яйцом, тофу и соусом тамаринд.",
    },
    {
        "q": "🍜 Фо — ароматный суп с рисовой лапшой и говядиной. Из какой страны это блюдо?",
        "options": ["а) Таиланд", "б) Камбоджа", "в) Китай", "г) Вьетнам"],
        "correct": "г) Вьетнам",
        "explanation": "Фо бо — национальный суп Вьетнама, готовится на медленно томлёном бульоне с пряностями.",
    },
    {
        "q": "🍜 Хумус — паста из нутa с кунжутной пастой. Кухня какой страны/региона?",
        "options": ["а) Иран", "б) Ирак", "в) Израиль/Ливан", "г) Египет"],
        "correct": "в) Израиль/Ливан",
        "explanation": "Хумус — традиционное блюдо Ближнего Востока, особенно популярен в Израиле и Ливане.",
    },
    {
        "q": "🍜 Тапас — маленькие закуски к напиткам. Из какой страны эта традиция?",
        "options": ["а) Португалия", "б) Италия", "в) Греция", "г) Испания"],
        "correct": "г) Испания",
        "explanation": "Тапас — испанская традиция подавать небольшие закуски к вину или пиву, особенно в барах.",
    },
    {
        "q": "🍜 Суши — рис с рыбой или морепродуктами. Из какой страны это блюдо?",
        "options": ["а) Китай", "б) Корея", "в) Япония", "г) Вьетнам"],
        "correct": "в) Япония",
        "explanation": "Суши зародились в Японии как способ хранения рыбы в ферментированном рисе.",
    },
    {
        "q": "🍜 Хачапури — лепёшка с сыром и яйцом. Из какой страны это блюдо?",
        "options": ["а) Армения", "б) Азербайджан", "в) Грузия", "г) Турция"],
        "correct": "в) Грузия",
        "explanation": "Хачапури — национальное блюдо Грузии, хлеб, запечённый с сыром сулугуни.",
    },
    {
        "q": "🍜 Кускус — крупа из пшеницы с овощами и мясом. Из какой страны это блюдо?",
        "options": ["а) Тунис", "б) Ливия", "в) Алжир", "г) Марокко"],
        "correct": "г) Марокко",
        "explanation": "Кускус — символ марокканской кухни, традиционно подаётся по пятницам с мясом и овощами.",
    },
    {
        "q": "🍜 Бигос — тушёная капуста с мясом и колбасой. Из какой страны это блюдо?",
        "options": ["а) Чехия", "б) Румыния", "в) Венгрия", "г) Польша"],
        "correct": "г) Польша",
        "explanation": "Бигос — национальное польское блюдо, «охотничье рагу» из квашеной капусты с мясом.",
    },
    {
        "q": "🍜 Гуляш — густой суп-рагу с паприкой и говядиной. Из какой страны это блюдо?",
        "options": ["а) Австрия", "б) Чехия", "в) Словакия", "г) Венгрия"],
        "correct": "г) Венгрия",
        "explanation": "Гуляш — блюдо венгерских пастухов, национальный символ Венгрии.",
    },
    # ── 💰 Угадай валюту ─────────────────────────────────────────────
    {
        "q": "💰 Как называется валюта Таиланда?",
        "options": ["а) Донг", "б) Кип", "в) Риель", "г) Бат"],
        "correct": "г) Бат",
        "explanation": "Тайский бат (THB) — валюта Таиланда. 1 бат = 100 сатангов.",
    },
    {
        "q": "💰 Как называется валюта Вьетнама?",
        "options": ["а) Бат", "б) Риель", "в) Донг", "г) Кип"],
        "correct": "в) Донг",
        "explanation": "Вьетнамский донг (VND) — одна из самых дешёвых валют мира по номиналу.",
    },
    {
        "q": "💰 Как называется валюта ОАЭ?",
        "options": ["а) Риял", "б) Динар", "в) Лира", "г) Дирхам"],
        "correct": "г) Дирхам",
        "explanation": "Дирхам ОАЭ (AED) привязан к доллару США с 1997 года.",
    },
    {
        "q": "💰 Как называется валюта Венгрии?",
        "options": ["а) Злотый", "б) Крона", "в) Форинт", "г) Лей"],
        "correct": "в) Форинт",
        "explanation": "Венгерский форинт (HUF) — Венгрия не входит в еврозону.",
    },
    {
        "q": "💰 Как называется валюта Польши?",
        "options": ["а) Форинт", "б) Крона", "в) Лев", "г) Злотый"],
        "correct": "г) Злотый",
        "explanation": "Польский злотый (PLN) — Польша также не входит в еврозону.",
    },
    {
        "q": "💰 Как называется валюта Грузии?",
        "options": ["а) Драм", "б) Манат", "в) Лари", "г) Сом"],
        "correct": "в) Лари",
        "explanation": "Грузинский лари (GEL) — введён в 1995 году, заменив купон.",
    },
    {
        "q": "💰 Как называется валюта Армении?",
        "options": ["а) Лари", "б) Манат", "в) Сом", "г) Драм"],
        "correct": "г) Драм",
        "explanation": "Армянский драм (AMD) — введён в 1993 году после распада СССР.",
    },
    {
        "q": "💰 Как называется валюта Камбоджи?",
        "options": ["а) Кип", "б) Донг", "в) Риель", "г) Бат"],
        "correct": "в) Риель",
        "explanation": "Камбоджийский риель (KHR) — в стране широко используется и доллар США.",
    },
    {
        "q": "💰 Как называется валюта Лаоса?",
        "options": ["а) Донг", "б) Бат", "в) Риель", "г) Кип"],
        "correct": "г) Кип",
        "explanation": "Лаосский кип (LAK) — одна из наименее известных валют Юго-Восточной Азии.",
    },
    {
        "q": "💰 Как называется валюта Кубы?",
        "options": ["а) Боливар", "б) Соль", "в) Реал", "г) Песо"],
        "correct": "г) Песо",
        "explanation": "Кубинское песо (CUP) — национальная валюта Кубы.",
    },
    # ── 🌍 Угадай страну по описанию ────────────────────────────────
    {
        "q": "🌍 Страна тысячи улыбок: буддизм, слоны, острова с белым песком. Что это за страна?",
        "options": ["а) Вьетнам", "б) Индонезия", "в) Таиланд", "г) Камбоджа"],
        "correct": "в) Таиланд",
        "explanation": "Таиланд называют «Страной тысячи улыбок» — тайцы славятся приветливостью и гостеприимством.",
    },
    {
        "q": "🌍 Страна восходящего солнца: суши, сакура, горы Фудзи. Что это за страна?",
        "options": ["а) Китай", "б) Корея", "в) Тайвань", "г) Япония"],
        "correct": "г) Япония",
        "explanation": "Япония — «Страна восходящего солнца», так называли её соседи-китайцы, с востока которых она расположена.",
    },
    {
        "q": "🌍 Страна фараонов: пирамиды, сфинкс, великий Нил. Что это за страна?",
        "options": ["а) Судан", "б) Марокко", "в) Египет", "г) Ливия"],
        "correct": "в) Египет",
        "explanation": "Египет — колыбель одной из древнейших цивилизаций, страна пирамид и Нила.",
    },
    {
        "q": "🌍 Страна кофе: родина человечества, кофейная церемония, Великая Рифтовая долина. Что это?",
        "options": ["а) Кения", "б) Руанда", "в) Танзания", "г) Эфиопия"],
        "correct": "г) Эфиопия",
        "explanation": "Эфиопия — родина кофе и, по мнению учёных, место появления первых людей (Australopithecus).",
    },
    {
        "q": "🌍 Страна кленового листа: медведи, хоккей, Ниагара. Что это за страна?",
        "options": ["а) США", "б) Финляндия", "в) Швеция", "г) Канада"],
        "correct": "г) Канада",
        "explanation": "Кленовый лист — национальный символ Канады, изображён на флаге страны.",
    },
    {
        "q": "🌍 Страна тюльпанов, мельниц и велосипедов. Что это за страна?",
        "options": ["а) Бельгия", "б) Дания", "в) Германия", "г) Нидерланды"],
        "correct": "г) Нидерланды",
        "explanation": "Нидерланды — мировой лидер по экспорту тюльпанов, страна с 23 млн велосипедов на 17 млн жителей.",
    },
    {
        "q": "🌍 Страна кенгуру, коал и Большого Барьерного рифа. Что это за страна?",
        "options": ["а) Новая Зеландия", "б) Австралия", "в) Папуа Новая Гвинея", "г) Индонезия"],
        "correct": "б) Австралия",
        "explanation": "Австралия — материк-страна с уникальной фауной: кенгуру, коалы, вомбаты, утконосы.",
    },
    {
        "q": "🌍 Страна танго, лучших стейков и Патагонии. Что это за страна?",
        "options": ["а) Чили", "б) Уругвай", "в) Аргентина", "г) Бразилия"],
        "correct": "в) Аргентина",
        "explanation": "Аргентина — родина танго, страна с огромными пампасами и загадочной Патагонией на юге.",
    },
    {
        "q": "🌍 Страна фьордов, северного сияния и викингов. Что это за страна?",
        "options": ["а) Исландия", "б) Финляндия", "в) Швеция", "г) Норвегия"],
        "correct": "г) Норвегия",
        "explanation": "Норвегия — страна с самыми красивыми фьордами, родина викингов и место лучшего северного сияния.",
    },
    {
        "q": "🌍 Страна самбы, карнавала и великой Амазонки. Что это за страна?",
        "options": ["а) Колумбия", "б) Венесуэла", "в) Перу", "г) Бразилия"],
        "correct": "г) Бразилия",
        "explanation": "Бразилия — крупнейшая страна Латинской Америки, родина самбы и карнавала в Рио-де-Жанейро.",
    },
]


def _quiz_question_kb(options: list[str]) -> ReplyKeyboardMarkup:
    """Клавиатура с 4 вариантами ответа (2×2) + Завершить + Назад."""
    return ReplyKeyboardMarkup(
        [
            [options[0], options[1]],
            [options[2], options[3]],
            ["🏁 Завершить", "◀️ Назад"],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _quiz_next_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["➡️ Следующий вопрос"], ["🏁 Завершить", "◀️ Назад"]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _quiz_finish_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["🔄 Начать заново"], [HOME_BTN]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def quiz_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало викторины — перемешиваем вопросы и показываем первый."""
    questions = _QUIZ_QUESTIONS.copy()
    _random.shuffle(questions)
    context.user_data["quiz_questions"] = questions
    context.user_data["quiz_index"] = 0
    context.user_data["quiz_score"] = 0
    context.user_data["quiz_awaiting_next"] = False
    return await _quiz_show_question(update, context)


async def _quiz_show_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud = context.user_data
    idx = ud["quiz_index"]
    q = ud["quiz_questions"][idx]
    ud["quiz_awaiting_next"] = False
    await update.message.reply_text(
        f"🧠 *Вопрос {idx + 1}*\n\n{q['q']}",
        parse_mode="Markdown",
        reply_markup=_quiz_question_kb(q["options"]),
    )
    return QUIZ_ACTIVE


async def quiz_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    ud = context.user_data

    if text == "◀️ Назад":
        return await show_games_menu(update, context)

    if text == "🏁 Завершить":
        return await _quiz_show_finish(update, context)

    awaiting_next = ud.get("quiz_awaiting_next", False)

    # Пользователь нажал "Следующий вопрос" или "Начать заново"
    if awaiting_next:
        if text == "🔄 Начать заново":
            return await quiz_start(update, context)
        if text == HOME_BTN:
            return await go_home(update, context)
        if text == "➡️ Следующий вопрос":
            ud["quiz_index"] += 1
            return await _quiz_show_question(update, context)
        # Любой другой текст — повторяем подсказку
        return QUIZ_ACTIVE

    # Пользователь отвечает на вопрос
    idx = ud.get("quiz_index", 0)
    questions = ud.get("quiz_questions", [])
    if not questions:
        return await quiz_start(update, context)

    q = questions[idx]
    is_correct = text == q["correct"]
    if is_correct:
        ud["quiz_score"] = ud.get("quiz_score", 0) + 1
        verdict = "✅ Правильно!"
    else:
        verdict = f"❌ Неверно. Правильный ответ: *{q['correct']}*"

    score = ud["quiz_score"]
    total = len(questions)
    is_last = (idx + 1) >= total

    if is_last:
        ud["quiz_awaiting_next"] = True
        await update.message.reply_text(
            f"{verdict}\n_{q['explanation']}_\n\n"
            f"🏁 *Викторина завершена!*\n\n"
            f"✅ Правильных ответов: *{score} из {total}*\n\n"
            f"Хочешь попробовать ещё раз?",
            parse_mode="Markdown",
            reply_markup=_quiz_finish_kb(),
        )
        return QUIZ_ACTIVE
    else:
        ud["quiz_awaiting_next"] = True
        await update.message.reply_text(
            f"{verdict}\n_{q['explanation']}_",
            parse_mode="Markdown",
            reply_markup=_quiz_next_kb(),
        )
        return QUIZ_ACTIVE


async def _quiz_show_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Досрочное завершение викторины по кнопке 🏁 Завершить."""
    ud = context.user_data
    score = ud.get("quiz_score", 0)
    # quiz_index = индекс текущего вопроса (ещё не отвечен).
    # Если quiz_awaiting_next=True — пользователь уже ответил на этот вопрос,
    # поэтому отвеченных вопросов на 1 больше.
    answered = ud.get("quiz_index", 0) + (1 if ud.get("quiz_awaiting_next", False) else 0)
    ud["quiz_awaiting_next"] = True
    await update.message.reply_text(
        f"🏁 *Викторина завершена!*\n\n"
        f"✅ Правильных ответов: *{score} из {answered}*\n\n"
        f"Хочешь попробовать ещё раз?",
        parse_mode="Markdown",
        reply_markup=_quiz_finish_kb(),
    )
    return QUIZ_ACTIVE


## ── 🎯 Угадай где я? ────────────────────────────────────────────────────────

_GUESS_RIDDLES = [
    {
        "riddle": "Здесь стоит башня, которая немного наклонена. Каждый год миллионы туристов фотографируются рядом с ней.",
        "answers": ["пиза", "pisa", "италия", "italy"],
        "correct": "Пиза, Италия",
        "fact": "Падающая башня Пизы наклонена на ~3.97° из-за мягкого грунта с одной стороны. Строительство длилось почти 200 лет.",
    },
    {
        "riddle": "Розовый город, вырубленный прямо в скале и потерянный для мира на 500 лет. Попасть сюда можно только пешком через узкое ущелье.",
        "answers": ["петра", "petra", "иордания", "jordan"],
        "correct": "Петра, Иордания",
        "fact": "Петра — столица древнего набатейского царства. О ней «забыли» в Европе и заново открыли в 1812 году швейцарский исследователь Буркхардт.",
    },
    {
        "riddle": "Самое маленькое государство в мире — всего 44 гектара. Оно полностью окружено одной страной и имеет собственную армию из 110 человек.",
        "answers": ["ватикан", "vatican"],
        "correct": "Ватикан",
        "fact": "Ватикан — государство-анклав внутри Рима. Здесь живут около 800 человек и расположен крупнейший христианский собор мира — Святого Петра.",
    },
    {
        "riddle": "Город на воде, где вместо улиц — каналы, вместо машин — лодки. Здесь нет ни одного автомобиля.",
        "answers": ["венеция", "venice", "venezia", "италия"],
        "correct": "Венеция, Италия",
        "fact": "Венеция стоит на 118 маленьких островах, соединённых 400 мостами. Город медленно погружается в лагуну примерно на 1–2 мм в год.",
    },
    {
        "riddle": "Синий город в горах, где все стены домов покрашены в один и тот же цвет. Местные говорят, что синий цвет отпугивает комаров.",
        "answers": ["шефшауэн", "шефшауэнь", "chefchaouen", "шауэн", "марокко", "morocco"],
        "correct": "Шефшауэн, Марокко",
        "fact": "Традиция красить стены в синий цвет появилась в 1930-х годах среди еврейских беженцев. Сегодня это один из самых фотографируемых городов мира.",
    },
    {
        "riddle": "На этих островах пляжи розового цвета — из-за смеси белого кварца и красных кораллов. Острова находятся посреди Атлантического океана.",
        "answers": ["бермуды", "bermuda", "bermudas"],
        "correct": "Бермуды",
        "fact": "Розовый цвет пляжам Бермуд придают раздробленные панцири морских существ — фораминифер. Острова также известны «Бермудским треугольником».",
    },
    {
        "riddle": "В этом норвежском городе солнце не заходит за горизонт почти два месяца летом. Зато зимой здесь два месяца полярной ночи.",
        "answers": ["тромсё", "тромсо", "tromsø", "tromso", "норвегия", "norway"],
        "correct": "Тромсё, Норвегия",
        "fact": "Тромсё называют «Воротами в Арктику». Это один из лучших городов мира для наблюдения за северным сиянием — с сентября по март.",
    },
    {
        "riddle": "В этой стране овец в шесть раз больше, чем людей. Здесь снимали трилогию «Властелин колец» и страна считается одной из самых безопасных в мире.",
        "answers": ["новая зеландия", "new zealand", "нз", "nz"],
        "correct": "Новая Зеландия",
        "fact": "В Новой Зеландии около 6 миллионов овец на 5 миллионов человек. Страна была последней крупной землёй, заселённой людьми — маори прибыли сюда лишь около 1300 года.",
    },
    {
        "riddle": "На этой магнитной горе машины, поставленные на нейтральную передачу, кажется, катятся вверх. На самом деле это оптический обман рельефа.",
        "answers": ["магнитная гора", "magnetic hill", "австралия", "australia", "мончтон", "moncton", "канада", "canada"],
        "correct": "Магнитная гора (Magnetic Hill), Канада",
        "fact": "Самая знаменитая «магнитная гора» находится в Монктоне, Канада. Подобные оптические иллюзии есть и в других странах — в Австралии, Индии, США.",
    },
    {
        "riddle": "Этот город стоит одновременно на двух континентах — в Европе и в Азии. Его разделяет пролив, через который перекинуты мосты.",
        "answers": ["стамбул", "istanbul", "турция", "turkey", "константинополь"],
        "correct": "Стамбул, Турция",
        "fact": "Стамбул — единственный город в мире, расположенный на двух континентах. Босфор делит его на европейскую и азиатскую части. Бывшие названия — Константинополь и Византий.",
    },
    {
        "riddle": "Это самое высокогорное судоходное озеро в мире — на высоте 3812 метров. Две страны делят его между собой.",
        "answers": ["титикака", "titicaca", "перу", "peru", "боливия", "bolivia"],
        "correct": "Озеро Титикака, Перу/Боливия",
        "fact": "На озере Титикака живут индейцы урос — на плавучих островах из тростника тотора. Озеро считается колыбелью цивилизации инков.",
    },
    {
        "riddle": "Эта страна занимает первое место в мире по числу вулканов. Здесь более 130 активных вулканов — больше, чем где-либо ещё.",
        "answers": ["индонезия", "indonesia"],
        "correct": "Индонезия",
        "fact": "В Индонезии 127 активных вулканов. Это часть Тихоокеанского «Огненного кольца». Самое известное извержение — Кракатау в 1883 году — было слышно за 5000 км.",
    },
    {
        "riddle": "В этой стране нет ни одной постоянной реки — только сухие русла, которые наполняются водой лишь после редких дождей.",
        "answers": ["саудовская аравия", "saudi arabia", "саудовская", "аравия", "сауди"],
        "correct": "Саудовская Аравия",
        "fact": "В Саудовской Аравии нет рек с постоянным течением. Пресную воду получают главным образом из опреснения морской воды — страна лидирует в мире по этому показателю.",
    },
    {
        "riddle": "В этом норвежском посёлке на Шпицбергене официально запрещено умирать — если человек серьёзно заболел, его должны эвакуировать.",
        "answers": ["лонгйир", "лонгьир", "longyearbyen", "норвегия", "norway", "шпицберген", "svalbard"],
        "correct": "Лонгйир, Норвегия (Шпицберген)",
        "fact": "Тела не разлагаются в вечной мерзлоте Шпицбергена, поэтому умерших хоронить здесь запрещено с 1950 года. Живых тоже стараются эвакуировать заранее.",
    },
    {
        "riddle": "Этот знаменитый водопад в национальном парке исчезает каждую зиму — воды в горных ручьях слишком мало, чтобы питать его.",
        "answers": ["йосемити", "yosemite", "сша", "usa", "калифорния", "california"],
        "correct": "Водопад Йосемити, США",
        "fact": "Йосемитский водопад — один из самых высоких в Северной Америке (739 м). Зимой он почти полностью пересыхает, а весной в период таяния снегов превращается в мощный поток.",
    },
    {
        "riddle": "На этом острове в Кении есть деревня, где живут только женщины. Мужчинам вход запрещён — это правило действует с 1990 года.",
        "answers": ["умодже", "umoja", "кения", "kenya"],
        "correct": "Деревня Умодже, Кения",
        "fact": "Умодже основали женщины, пережившие насилие. Деревня стала символом женской независимости. Жительницы зарабатывают продажей украшений туристам.",
    },
    {
        "riddle": "В этой знаменитой точке у Рейнского водопада сходятся три страны. Ты можешь стоять в одной стране и одновременно касаться двух других.",
        "answers": ["германия", "germany", "франция", "france", "швейцария", "switzerland", "базель", "basel", "три страны", "тройная граница"],
        "correct": "Точка трёх стран — Германия, Франция, Швейцария (у Базеля)",
        "fact": "Dreiländereck (Угол трёх стран) у Базеля — место, где сходятся границы Германии, Франции и Швейцарии. Здесь установлен памятный обелиск прямо на берегу Рейна.",
    },
    {
        "riddle": "В этой стране можно увидеть огромные острова целиком из стекловидной соли. В сезон дождей они превращаются в идеальное зеркало, отражающее небо.",
        "answers": ["боливия", "bolivia", "уюни", "uyuni"],
        "correct": "Солончак Уюни, Боливия",
        "fact": "Уюни — крупнейший солончак в мире (10 582 км²). Слой соли достигает 10 метров. Здесь сосредоточено около 50–70% мировых запасов лития.",
    },
    {
        "riddle": "Этот город запрещён для автомобилей — ни одной машины, только лодки, пешеходы и велосипеды. Тем не менее здесь живут постоянные жители.",
        "answers": ["венеция", "venice", "venezia", "италия", "italy"],
        "correct": "Венеция, Италия",
        "fact": "В Венеции нет ни одной дороги для автомобилей. Транспорт — вапоретто (водные автобусы), гондолы и частные лодки. Городу угрожает постепенное затопление.",
    },
    {
        "riddle": "Это самое глубокое озеро в мире — глубина достигает 1642 метров. В нём содержится около 20% всей пресной воды на поверхности Земли.",
        "answers": ["байкал", "baikal", "россия", "russia"],
        "correct": "Озеро Байкал, Россия",
        "fact": "Байкал — древнейшее озеро на Земле (25–30 миллионов лет). Здесь обитает около 3700 видов животных и растений, две трети из которых не встречаются нигде больше.",
    },
    # ── ещё 30 загадок ──────────────────────────────────────────────────────
    {
        "riddle": "В этой стране монахи в оранжевых одеждах встречаются буквально на каждом шагу. Почти всё население исповедует буддизм, а в каждом городе есть золотые пагоды.",
        "answers": ["таиланд", "thailand"],
        "correct": "Таиланд",
        "fact": "В Таиланде около 40 000 буддийских храмов. По традиции каждый мужчина хотя бы раз в жизни должен стать монахом на короткий срок.",
    },
    {
        "riddle": "В этом городе стоит самое высокое здание в мире — 828 метров. Небоскрёбы здесь выросли из песка за 50 лет.",
        "answers": ["дубай", "dubai", "оаэ", "uae", "эмираты"],
        "correct": "Дубай, ОАЭ",
        "fact": "Бурдж-Халифа в Дубае (828 м) — самое высокое здание в мире с 2010 года. Строительство заняло 6 лет. На вершине башни ветер так силён, что температура ощущается на 5–8°C холоднее.",
    },
    {
        "riddle": "На этом острове растут деревья, похожие на перевёрнутые зонтики. Такие деревья больше нигде в мире не встречаются. Остров называют «Галапагосами Индийского океана».",
        "answers": ["сокотра", "socotra", "йемен", "yemen"],
        "correct": "Сокотра, Йемен",
        "fact": "На Сокотре растёт драконово дерево с зонтикообразной кроной. Из-за изоляции острова около 37% растений не встречаются больше нигде на Земле. Остров входит в список Всемирного наследия ЮНЕСКО.",
    },
    {
        "riddle": "В этой стране запрещено продавать и жевать жевательную резинку. За нарушение — штраф. Страна считается одной из самых чистых в мире.",
        "answers": ["сингапур", "singapore"],
        "correct": "Сингапур",
        "fact": "Запрет на жвачку в Сингапуре действует с 1992 года — из-за загрязнения тротуаров и поломок дверей метро. Исключение сделано лишь для терапевтической жвачки по рецепту врача.",
    },
    {
        "riddle": "В этом водоёме вода настолько солёная, что человек не может утонуть. Грязь с его берегов считается целебной для кожи. Это самое низкое место на суше.",
        "answers": ["мёртвое море", "мертвое море", "dead sea", "израиль", "israel", "иордания", "jordan"],
        "correct": "Мёртвое море, Израиль/Иордания",
        "fact": "Мёртвое море находится на 430 м ниже уровня Мирового океана. Солёность воды достигает 34% — в 10 раз выше, чем в океане. Водоём медленно пересыхает: уровень воды падает на 1 м в год.",
    },
    {
        "riddle": "В этом городе каждый год проходит самый большой карнавал в мире. Несколько дней весь город танцует самбу, а самбодром вмещает 90 000 зрителей.",
        "answers": ["рио", "rio", "рио-де-жанейро", "rio de janeiro", "бразилия", "brazil"],
        "correct": "Рио-де-Жанейро, Бразилия",
        "fact": "Карнавал в Рио собирает более 2 миллионов человек в день. Школы самбы готовятся к нему целый год. Карнавал проводится за 40 дней до Пасхи.",
    },
    {
        "riddle": "В этой стране коровы считаются священными животными и свободно ходят по улицам городов. Никто не имеет права их прогнать или обидеть.",
        "answers": ["индия", "india"],
        "correct": "Индия",
        "fact": "В Индии насчитывается около 305 миллионов коров — больше, чем в любой другой стране. В большинстве штатов их забой запрещён законом. Корова в индуизме — символ изобилия.",
    },
    {
        "riddle": "Здесь находится самый большой коралловый риф в мире — его длина превышает 2300 км и он виден из космоса.",
        "answers": ["австралия", "australia", "большой барьерный риф", "great barrier reef"],
        "correct": "Большой Барьерный риф, Австралия",
        "fact": "Большой Барьерный риф — крупнейшая живая структура на Земле. Здесь обитает более 1500 видов рыб и 4000 видов моллюсков. Риф занесён в список Всемирного наследия ЮНЕСКО.",
    },
    {
        "riddle": "В этом городе подземная железная дорога работает с 1863 года — она старейшая в мире. Местные называют её просто «Труба».",
        "answers": ["лондон", "london", "великобритания", "uk", "england", "англия"],
        "correct": "Лондон, Великобритания",
        "fact": "Лондонское метро — первое в мире, открытое в 1863 году. Сегодня его длина — 402 км. Ежегодно метро перевозит более 1 миллиарда пассажиров.",
    },
    {
        "riddle": "Эта река меняет цвет в зависимости от сезона — она становится красной, жёлтой и зелёной из-за водорослей. Её называют «самой красивой рекой в мире».",
        "answers": ["каньо кристалес", "cano cristales", "колумбия", "colombia"],
        "correct": "Каньо Кристалес, Колумбия",
        "fact": "Каньо Кристалес цветёт с июля по ноябрь. Цвет создаёт уникальное растение Macarenia clavigera. Долгие годы река была закрыта для туристов из-за действий партизан.",
    },
    {
        "riddle": "Именно в этой стране тысячи лет назад придумали игру, которую сегодня знает весь мир — шахматы.",
        "answers": ["индия", "india"],
        "correct": "Индия",
        "fact": "Шахматы изобрели в Индии около VI века нашей эры — игра называлась «чатуранга». Через Персию она попала в арабский мир, а затем в Европу.",
    },
    {
        "riddle": "В этом городе каждую ночь с мая по ноябрь разводят огромные мосты над рекой — чтобы пропустить корабли. Белые ночи здесь длятся несколько недель.",
        "answers": ["санкт-петербург", "питер", "петербург", "saint petersburg", "spb", "россия", "russia"],
        "correct": "Санкт-Петербург, Россия",
        "fact": "В Санкт-Петербурге 342 моста — больше, чем в Венеции. Разводные мосты поднимают ежедневно с апреля по ноябрь. Самый известный — Дворцовый мост на Неве.",
    },
    {
        "riddle": "Здесь находится крупнейшая в мире Долина гейзеров — около 160 гейзеров на площади 6 км². Добраться сюда можно только на вертолёте.",
        "answers": ["камчатка", "kamchatka", "россия", "russia"],
        "correct": "Камчатка, Россия",
        "fact": "Долина гейзеров на Камчатке открыта в 1941 году. В 2007 году часть долины была уничтожена оползнем, но гейзеры восстановились. Камчатка — одно из самых активных вулканических мест планеты.",
    },
    {
        "riddle": "В этой стране нет ни одного светофора — ни в столице, ни в каком-либо другом городе. Вместо этого движение регулируют полицейские.",
        "answers": ["бутан", "bhutan"],
        "correct": "Бутан",
        "fact": "Бутан — единственная страна в мире без светофоров. Единственный светофор, установленный в столице Тхимпху в 1990-х, убрали через несколько дней — жители сочли его безличным.",
    },
    {
        "riddle": "В этом городе похоронен великий композитор Вольфганг Амадей Моцарт. Здесь же находится его дом-музей и знаменитые оперные залы.",
        "answers": ["вена", "vienna", "wien", "австрия", "austria"],
        "correct": "Вена, Австрия",
        "fact": "Моцарт похоронен в Вене в 1791 году в возрасте 35 лет. Место его захоронения точно неизвестно — он был погребён в общей могиле. Вена считается мировой столицей классической музыки.",
    },
    {
        "riddle": "Часть сцен знаменитой саги «Звёздные войны» снималась именно здесь — в пустыне, которая стала планетой Татуин. Каменные деревни здесь похожи на другой мир.",
        "answers": ["тунис", "tunisia", "африка", "africa"],
        "correct": "Тунис",
        "fact": "Деревня Матмата в Тунисе, где живут люди в подземных домах, стала планетой Татуин в «Звёздных войнах». Режиссёр Джордж Лукас выбрал Тунис за неземной пейзаж.",
    },
    {
        "riddle": "Эта древняя практика гармонизации тела и разума зародилась здесь тысячи лет назад. Сегодня её практикуют сотни миллионов людей по всему миру.",
        "answers": ["индия", "india"],
        "correct": "Индия",
        "fact": "Йога зародилась в Индии более 5000 лет назад. Слово «йога» на санскрите означает «единение» или «союз». В 2014 году ООН объявила 21 июня Международным днём йоги.",
    },
    {
        "riddle": "В этом крошечном государстве каждые два года проходят самые знаменитые городские гонки «Формула-1». Трасса петляет по узким улицам прямо вдоль моря.",
        "answers": ["монако", "monaco"],
        "correct": "Монако",
        "fact": "Гран-при Монако — одна из старейших гонок Ф-1 (с 1929 года). Трасса протяжённостью 3.34 км проходит по улицам города. Это единственная гонка, где болиды едут медленнее из-за крутых поворотов.",
    },
    {
        "riddle": "Здесь находится крупнейший буддийский храм в мире — огромная пирамида из камня, украшенная сотнями ступ и тысячами рельефов.",
        "answers": ["боробудур", "borobudur", "индонезия", "indonesia"],
        "correct": "Боробудур, Индонезия",
        "fact": "Боробудур построен в IX веке. Храм содержит 2672 рельефных панели и 504 статуи Будды. После извержения вулкана он был заброшен и скрыт под слоем пепла — вновь открыт в 1814 году.",
    },
    {
        "riddle": "На горе над этим городом стоит огромная статуя Христа с распростёртыми руками. Её высота — 38 метров, а видна она практически из любой точки города.",
        "answers": ["рио", "rio", "рио-де-жанейро", "rio de janeiro", "бразилия", "brazil"],
        "correct": "Рио-де-Жанейро, Бразилия",
        "fact": "Статуя Христа-Искупителя на горе Корковаду открыта в 1931 году. Она входит в список семи новых чудес света. Статуя весит 635 тонн и является символом не только Рио, но и всей Бразилии.",
    },
    {
        "riddle": "В этом месте на западном берегу реки расположены гробницы египетских фараонов, в том числе гробница Тутанхамона. Туристы называют это место «Долиной».",
        "answers": ["луксор", "luxor", "долина царей", "valley of the kings", "египет", "egypt"],
        "correct": "Долина царей, Луксор, Египет",
        "fact": "В Долине царей найдено более 63 гробниц фараонов. Гробницу Тутанхамона обнаружил Говард Картер в 1922 году — это была одна из немногих нетронутых гробниц.",
    },
    {
        "riddle": "Именно в этой скандинавской стране придумали разноцветные пластиковые кирпичики, из которых сегодня строят всё на свете. Штаб-квартира компании до сих пор здесь.",
        "answers": ["дания", "denmark", "lego", "лего"],
        "correct": "Дания",
        "fact": "LEGO основана в 1932 году датским плотником Оле Кирком Кристиансеном. Название — сокращение от датского «leg godt» («играй хорошо»). Ежегодно производится 36 миллиардов деталей LEGO.",
    },
    {
        "riddle": "В этом испанском городке каждый август проходит битва — тысячи людей бросают друг в друга перезревшими помидорами. Всё ради веселья.",
        "answers": ["буньоль", "буньол", "bunol", "испания", "spain", "томатина", "tomatina"],
        "correct": "Буньоль, Испания",
        "fact": "Томатина проводится в последнюю среду августа. В 2013 году число участников ограничили до 20 000 человек. На фестиваль уходит около 150 тонн помидоров.",
    },
    {
        "riddle": "Национальный вид спорта этой страны — единоборство, где богатыри в набедренных повязках стараются вытолкнуть соперника за пределы круга.",
        "answers": ["япония", "japan", "сумо", "sumo"],
        "correct": "Япония",
        "fact": "Сумо — один из древнейших видов спорта Японии, известный более 1500 лет. Профессиональные борцы (рикиси) следуют строгому кодексу поведения. Соревнования проводятся шесть раз в год.",
    },
    {
        "riddle": "В этом городе находится один из самых узнаваемых оперных театров мира — белое здание в форме парусов прямо на берегу залива.",
        "answers": ["сидней", "sydney", "австралия", "australia"],
        "correct": "Сидней, Австралия",
        "fact": "Сиднейский оперный театр построен в 1973 году по проекту датского архитектора Йорна Утзона. Его крыши состоят из 1 056 006 керамических плиток. Здание входит в список Всемирного наследия ЮНЕСКО.",
    },
    {
        "riddle": "Здесь находится самый биологически разнообразный тропический лес на планете. Он занимает большую часть страны и называется «лёгкими Земли».",
        "answers": ["амазония", "amazon", "бразилия", "brazil", "амазонка"],
        "correct": "Амазония, Бразилия",
        "fact": "В Амазонии обитает около 10% всех видов живых организмов на Земле. Лес производит 20% мирового кислорода. Река Амазонка — крупнейшая в мире по объёму воды.",
    },
    {
        "riddle": "Именно в этой стране монах по имени Периньон в XVII веке случайно создал игристый напиток, который стал символом праздника во всём мире.",
        "answers": ["франция", "france", "шампань", "champagne"],
        "correct": "Франция",
        "fact": "Шампанское изобретено в регионе Шампань во Франции около 1697 года. По закону ЕС только игристое вино из этого региона может называться «шампанским». Остальное — просто игристое вино.",
    },
    {
        "riddle": "В этой стране белые медведи живут в дикой природе и иногда заходят прямо в посёлки. Здесь самая большая популяция этих хищников в мире.",
        "answers": ["канада", "canada", "черчилль", "churchill"],
        "correct": "Канада (Черчилль, провинция Манитоба)",
        "fact": "Городок Черчилль в Канаде называют «столицей белых медведей». Каждую осень медведи проходят через город по пути к замерзающему заливу. Жители оставляют машины незапертыми — на случай если нужно спрятаться от медведя.",
    },
    {
        "riddle": "Эта скандинавская страна выдала больше нобелевских лауреатов на душу населения, чем любая другая. Здесь же хранится оригинальная нобелевская медаль.",
        "answers": ["швеция", "sweden"],
        "correct": "Швеция",
        "fact": "Нобелевская премия учреждена по завещанию шведского изобретателя Альфреда Нобеля в 1895 году. Церемония вручения проходит в Стокгольме 10 декабря — в день смерти Нобеля.",
    },
]

_random_guess = __import__("random")


def _check_guess_answer(user_input: str, riddle: dict) -> bool:
    """Возвращает True если ответ засчитан (без учёта регистра, частичное совпадение)."""
    user = user_input.strip().lower()
    if not user:
        return False
    for ans in riddle["answers"]:
        ans_l = ans.lower()
        if ans_l in user or user in ans_l:
            return True
    return False


def _guess_question_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["🏁 Завершить", "◀️ Назад"]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def _guess_next_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["➡️ Следующий вопрос"], ["🏁 Завершить", "◀️ Назад"]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _guess_finish_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["🔄 Начать заново"], [HOME_BTN]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def guess_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    riddles = _GUESS_RIDDLES.copy()
    _random_guess.shuffle(riddles)
    context.user_data["guess_riddles"]      = riddles
    context.user_data["guess_index"]        = 0
    context.user_data["guess_score"]        = 0
    context.user_data["guess_awaiting_next"] = False
    await _guess_show_question(update, context)
    return GUESS_ACTIVE


async def _guess_show_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud  = context.user_data
    idx = ud["guess_index"]
    riddles = ud["guess_riddles"]
    total   = len(riddles)
    q = riddles[idx]
    await update.message.reply_text(
        f"🎯 *Вопрос {idx + 1}*\n\n{q['riddle']}\n\n_Напиши страну или город:_",
        parse_mode="Markdown",
        reply_markup=_guess_question_kb(),
    )


async def _guess_show_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud      = context.user_data
    score   = ud.get("guess_score", 0)
    answered = ud.get("guess_index", 0) + (1 if ud.get("guess_awaiting_next") else 0)
    total   = len(ud.get("guess_riddles", _GUESS_RIDDLES))
    if score == total:
        emoji = "🏆"
    elif score >= total * 0.7:
        emoji = "🥇"
    elif score >= total * 0.4:
        emoji = "🥈"
    else:
        emoji = "🌍"
    await update.message.reply_text(
        f"🏁 *Игра завершена!*\n\n"
        f"{emoji} Угадано: *{score} из {answered}*\n\n"
        f"Хочешь сыграть ещё раз?",
        parse_mode="Markdown",
        reply_markup=_guess_finish_kb(),
    )
    return GUESS_ACTIVE


async def guess_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    ud   = context.user_data

    if text == "◀️ Назад":
        return await show_games_menu(update, context)

    if text == "🏁 Завершить":
        return await _guess_show_finish(update, context)

    awaiting_next = ud.get("guess_awaiting_next", False)

    if awaiting_next:
        if text == "🔄 Начать заново":
            return await guess_start(update, context)
        if text == HOME_BTN:
            return await go_home(update, context)
        if text == "➡️ Следующий вопрос":
            ud["guess_index"] += 1
            ud["guess_awaiting_next"] = False
            riddles = ud.get("guess_riddles", [])
            if ud["guess_index"] >= len(riddles):
                return await _guess_show_finish(update, context)
            await _guess_show_question(update, context)
            return GUESS_ACTIVE
        return GUESS_ACTIVE

    riddles = ud.get("guess_riddles", [])
    if not riddles:
        return await guess_start(update, context)

    idx = ud.get("guess_index", 0)
    q   = riddles[idx]

    correct = _check_guess_answer(text, q)
    if correct:
        ud["guess_score"] = ud.get("guess_score", 0) + 1
        verdict = "✅ *Правильно!*"
    else:
        verdict = f"❌ *Не угадал.* Правильный ответ: *{q['correct']}*"

    is_last = (idx + 1) >= len(riddles)
    ud["guess_awaiting_next"] = True

    if is_last:
        score = ud["guess_score"]
        total = len(riddles)
        await update.message.reply_text(
            f"{verdict}\n\n_{q['fact']}_\n\n"
            f"🏁 *Игра завершена!*\n\n"
            f"{'🏆' if score == total else '🌍'} Угадано: *{score} из {total}*\n\n"
            f"Хочешь сыграть ещё раз?",
            parse_mode="Markdown",
            reply_markup=_guess_finish_kb(),
        )
    else:
        await update.message.reply_text(
            f"{verdict}\n\n_{q['fact']}_",
            parse_mode="Markdown",
            reply_markup=_guess_next_kb(),
        )
    return GUESS_ACTIVE


## ── 🤝 Найди пару ────────────────────────────────────────────────────────────

_PAIR_CAPITALS = [
    {"country": "Франция",    "answer": "Париж"},
    {"country": "Япония",     "answer": "Токио"},
    {"country": "Бразилия",   "answer": "Бразилиа"},
    {"country": "Австралия",  "answer": "Канберра"},
    {"country": "Египет",     "answer": "Каир"},
    {"country": "Таиланд",    "answer": "Бангкок"},
    {"country": "Аргентина",  "answer": "Буэнос-Айрес"},
    {"country": "Индия",      "answer": "Нью-Дели"},
    {"country": "Китай",      "answer": "Пекин"},
    {"country": "Марокко",    "answer": "Рабат"},
    {"country": "Грузия",     "answer": "Тбилиси"},
    {"country": "Армения",    "answer": "Ереван"},
    {"country": "Куба",       "answer": "Гавана"},
    {"country": "Иордания",   "answer": "Амман"},
    {"country": "ОАЭ",        "answer": "Абу-Даби"},
    {"country": "Португалия", "answer": "Лиссабон"},
    {"country": "Греция",     "answer": "Афины"},
    {"country": "Нидерланды", "answer": "Амстердам"},
    {"country": "Норвегия",   "answer": "Осло"},
    {"country": "Швеция",     "answer": "Стокгольм"},
    {"country": "Финляндия",  "answer": "Хельсинки"},
    {"country": "Чехия",      "answer": "Прага"},
    {"country": "Венгрия",    "answer": "Будапешт"},
    {"country": "Польша",     "answer": "Варшава"},
    {"country": "Австрия",    "answer": "Вена"},
]

_PAIR_CURRENCIES = [
    {"country": "Таиланд",              "answer": "Бат"},
    {"country": "Япония",               "answer": "Йена"},
    {"country": "Индия",                "answer": "Рупия"},
    {"country": "Грузия",               "answer": "Лари"},
    {"country": "Армения",              "answer": "Драм"},
    {"country": "Венгрия",              "answer": "Форинт"},
    {"country": "Польша",               "answer": "Злотый"},
    {"country": "Куба",                 "answer": "Песо"},
    {"country": "Вьетнам",              "answer": "Донг"},
    {"country": "Камбоджа",             "answer": "Риель"},
    {"country": "Великобритания",        "answer": "Фунт"},
    {"country": "Швейцария",            "answer": "Франк"},
    {"country": "Норвегия",             "answer": "Крона"},
    {"country": "Израиль",              "answer": "Шекель"},
    {"country": "ОАЭ",                  "answer": "Дирхам"},
    {"country": "Египет",               "answer": "Фунт египетский"},
    {"country": "Марокко",              "answer": "Дирхам марокканский"},
    {"country": "Сингапур",             "answer": "Сингапурский доллар"},
    {"country": "Австралия",            "answer": "Австралийский доллар"},
]

_PAIR_DISHES = [
    {"country": "Италия",      "answer": "Паста"},
    {"country": "Япония",      "answer": "Суши"},
    {"country": "Таиланд",     "answer": "Том ям"},
    {"country": "Грузия",      "answer": "Хачапури"},
    {"country": "Испания",     "answer": "Паэлья"},
    {"country": "Марокко",     "answer": "Кускус"},
    {"country": "Вьетнам",     "answer": "Фо бо"},
    {"country": "Индия",       "answer": "Карри"},
    {"country": "Венгрия",     "answer": "Гуляш"},
    {"country": "Франция",     "answer": "Круассан"},
    {"country": "Греция",      "answer": "Мусака"},
    {"country": "Турция",      "answer": "Кебаб"},
    {"country": "Япония",      "answer": "Рамен"},
    {"country": "Корея",       "answer": "Кимчи"},
    {"country": "Китай",       "answer": "Димсам"},
    {"country": "Мексика",     "answer": "Тако"},
    {"country": "Аргентина",   "answer": "Асадо"},
    {"country": "Ливан",       "answer": "Хумус"},
    {"country": "Израиль",     "answer": "Фалафель"},
    {"country": "Индонезия",   "answer": "Наси горенг"},
]

_PAIR_LANDMARKS = [
    {"country": "Франция",        "answer": "Эйфелева башня"},
    {"country": "Египет",         "answer": "Пирамиды Гизы"},
    {"country": "Китай",          "answer": "Великая стена"},
    {"country": "Индия",          "answer": "Тадж-Махал"},
    {"country": "Перу",           "answer": "Мачу-Пикчу"},
    {"country": "Иордания",       "answer": "Петра"},
    {"country": "Италия",         "answer": "Колизей"},
    {"country": "Греция",         "answer": "Акрополь"},
    {"country": "Камбоджа",       "answer": "Ангкор-Ват"},
    {"country": "ОАЭ",            "answer": "Бурдж Халифа"},
    {"country": "Испания",        "answer": "Саграда Фамилия"},
    {"country": "Бразилия",       "answer": "Статуя Христа"},
    {"country": "Австралия",      "answer": "Сиднейская опера"},
    {"country": "США",            "answer": "Статуя Свободы"},
    {"country": "Великобритания", "answer": "Биг-Бен"},
    {"country": "Россия",         "answer": "Красная площадь"},
    {"country": "Япония",         "answer": "Фудзияма"},
    {"country": "Греция",         "answer": "Санторини"},
    {"country": "Турция",         "answer": "Каппадокия"},
    {"country": "Индонезия",      "answer": "Храм Боробудур"},
]

_PAIR_CATEGORIES = {
    "столица":            ("🏛 Столица",             _PAIR_CAPITALS),
    "валюта":             ("💰 Валюта",              _PAIR_CURRENCIES),
    "блюдо":              ("🍽 Национальное блюдо",  _PAIR_DISHES),
    "достопримечательность": ("🗺 Достопримечательность", _PAIR_LANDMARKS),
}

_rnd_pair = __import__("random")


def _pair_build_questions() -> list[dict]:
    """Собирает все вопросы из всех категорий, перемешивает."""
    questions = []
    for cat_key, (cat_label, items) in _PAIR_CATEGORIES.items():
        for item in items:
            questions.append({
                "cat_key":   cat_key,
                "cat_label": cat_label,
                "country":   item["country"],
                "answer":    item["answer"],
                "pool":      [x["answer"] for x in items],  # all answers in category
            })
    _rnd_pair.shuffle(questions)
    return questions


def _pair_make_options(q: dict) -> list[str]:
    """Возвращает 4 варианта ответа (1 правильный + 3 случайных неправильных)."""
    wrong = [a for a in q["pool"] if a != q["answer"]]
    _rnd_pair.shuffle(wrong)
    options = [q["answer"]] + wrong[:3]
    _rnd_pair.shuffle(options)
    return options


def _pair_question_kb(options: list[str]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [options[0], options[1]],
            [options[2], options[3]],
            ["🏁 Завершить"],
            ["◀️ Назад", HOME_BTN],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _pair_next_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["➡️ Следующий вопрос"], ["🏁 Завершить"], ["◀️ Назад", HOME_BTN]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _pair_finish_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["🔄 Начать заново"], [HOME_BTN]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def pair_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    questions = _pair_build_questions()
    context.user_data["pair_questions"]      = questions
    context.user_data["pair_index"]          = 0
    context.user_data["pair_score"]          = 0
    context.user_data["pair_awaiting_next"]  = False
    context.user_data["pair_options"]        = []
    await _pair_show_question(update, context)
    return PAIR_ACTIVE


async def _pair_show_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud  = context.user_data
    idx = ud["pair_index"]
    q   = ud["pair_questions"][idx]
    options = _pair_make_options(q)
    ud["pair_options"] = options
    await update.message.reply_text(
        f"🤝 *{q['cat_label']}*\n\n🌍 *{q['country']}* — это…?",
        parse_mode="Markdown",
        reply_markup=_pair_question_kb(options),
    )


async def _pair_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud      = context.user_data
    score   = ud.get("pair_score", 0)
    answered = ud.get("pair_index", 0) + (1 if ud.get("pair_awaiting_next") else 0)
    if score == answered:
        emoji = "🏆"
    elif score >= answered * 0.7:
        emoji = "🥇"
    elif score >= answered * 0.4:
        emoji = "🥈"
    else:
        emoji = "📚"
    await update.message.reply_text(
        f"🏁 *Игра завершена!*\n\n"
        f"{emoji} Правильных: *{score} из {answered}*\n\n"
        f"Сыграть ещё раз?",
        parse_mode="Markdown",
        reply_markup=_pair_finish_kb(),
    )
    return PAIR_ACTIVE


async def pair_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    ud   = context.user_data

    if text == HOME_BTN:
        return await go_home(update, context)

    if text == "◀️ Назад":
        return await show_games_menu(update, context)

    if text == "🏁 Завершить":
        return await _pair_finish(update, context)

    awaiting = ud.get("pair_awaiting_next", False)

    if awaiting:
        if text == "🔄 Начать заново":
            return await pair_start(update, context)
        if text == "➡️ Следующий вопрос":
            ud["pair_index"] += 1
            ud["pair_awaiting_next"] = False
            questions = ud.get("pair_questions", [])
            if ud["pair_index"] >= len(questions):
                return await _pair_finish(update, context)
            await _pair_show_question(update, context)
            return PAIR_ACTIVE
        return PAIR_ACTIVE

    questions = ud.get("pair_questions", [])
    if not questions:
        return await pair_start(update, context)

    idx     = ud.get("pair_index", 0)
    q       = questions[idx]
    options = ud.get("pair_options", [])

    # Only accept one of the 4 displayed options as an answer
    if text not in options:
        await _pair_show_question(update, context)
        return PAIR_ACTIVE

    correct = (text == q["answer"])
    if correct:
        ud["pair_score"] = ud.get("pair_score", 0) + 1
        verdict = "✅ *Правильно!*"
    else:
        verdict = f"❌ Неверно. Правильный ответ: *{q['answer']}*"

    ud["pair_awaiting_next"] = True
    is_last = (idx + 1) >= len(questions)

    if is_last:
        score    = ud["pair_score"]
        answered = idx + 1
        emoji    = "🏆" if score == answered else ("🥇" if score >= answered * 0.7 else "📚")
        await update.message.reply_text(
            f"{verdict}\n\n"
            f"🏁 *Игра завершена!*\n\n"
            f"{emoji} Правильных: *{score} из {answered}*\n\n"
            f"Сыграть ещё раз?",
            parse_mode="Markdown",
            reply_markup=_pair_finish_kb(),
        )
    else:
        await update.message.reply_text(
            verdict,
            parse_mode="Markdown",
            reply_markup=_pair_next_kb(),
        )
    return PAIR_ACTIVE


async def show_folder_planning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вернуться в папку 🧭 Планирование."""
    context.user_data.clear()
    await update.message.reply_text(
        "🧭 *Планирование*\n\nВыбери раздел:",
        parse_mode="Markdown",
        reply_markup=get_folder_planning_kb(),
    )
    return MAIN_MENU


async def show_folder_knowledge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вернуться в папку 📚 Знания."""
    context.user_data.clear()
    await update.message.reply_text(
        "📚 *Знания*\n\nВыбери раздел:",
        parse_mode="Markdown",
        reply_markup=get_folder_knowledge_kb(),
    )
    return MAIN_MENU


## ── 🌍 Страна дня ────────────────────────────────────────────────────────────

_COUNTRIES_OF_DAY = [
    {"code": "AF", "flag": "🇦🇫", "name": "Афганистан",         "capital": "Кабул",          "region": "Азия",          "desc": "Страна с богатейшей историей — перекрёсток цивилизаций. Здесь находятся древние буддийские памятники и величественный Гиндукуш.", "fact": "В Афганистане растёт самый крупный гранат в мире."},
    {"code": "AL", "flag": "🇦🇱", "name": "Албания",             "capital": "Тирана",          "region": "Европа",        "desc": "Балканская жемчужина с нетронутой природой, синими берегами Адриатики и старинными замками. Одна из самых дешёвых стран Европы.", "fact": "Кивок головой в Албании означает «нет», а покачивание — «да»."},
    {"code": "DZ", "flag": "🇩🇿", "name": "Алжир",               "capital": "Алжир",           "region": "Африка",        "desc": "Крупнейшая страна Африки. Большую часть занимает Сахара с потрясающими барханами и древними наскальными рисунками.", "fact": "Алжир — самая большая страна Африки по площади."},
    {"code": "AD", "flag": "🇦🇩", "name": "Андорра",             "capital": "Андорра-ла-Велья","region": "Европа",        "desc": "Крошечное горное княжество между Францией и Испанией. Рай для лыжников и шопинга без НДС.", "fact": "В Андорре нет аэропорта и армии, но один из самых высоких уровней жизни в мире."},
    {"code": "AO", "flag": "🇦🇴", "name": "Ангола",              "capital": "Луанда",          "region": "Африка",        "desc": "Атлантическое побережье, водопады Каландула и нетронутые национальные парки. Страна стремительно развивается после окончания гражданской войны.", "fact": "Водопады Каландула в Анголе — одни из крупнейших в Африке."},
    {"code": "AG", "flag": "🇦🇬", "name": "Антигуа и Барбуда",   "capital": "Сент-Джонс",      "region": "Карибы",        "desc": "365 пляжей — по одному на каждый день года. Карибский рай с кристально чистой водой и яхтенными гаванями.", "fact": "На Антигуа ровно 365 пляжей — по одному на каждый день года."},
    {"code": "AR", "flag": "🇦🇷", "name": "Аргентина",           "capital": "Буэнос-Айрес",    "region": "Южная Америка", "desc": "Страна танго, асадо и бескрайних пампасов. От ледников Патагонии до тропических водопадов Игуасу — контрасты поражают.", "fact": "Аргентина — восьмая по площади страна в мире."},
    {"code": "AM", "flag": "🇦🇲", "name": "Армения",             "capital": "Ереван",          "region": "Азия",          "desc": "Одна из старейших христианских стран мира с монастырями в горах, коньяком и гостеприимством.", "fact": "Армения — первая страна, принявшая христианство как государственную религию в 301 году."},
    {"code": "AU", "flag": "🇦🇺", "name": "Австралия",           "capital": "Канберра",        "region": "Океания",       "desc": "Континент-страна с уникальной фауной, Большим барьерным рифом и красными пустынями Аутбэка.", "fact": "В Австралии обитает 21 из 25 самых ядовитых змей в мире."},
    {"code": "AT", "flag": "🇦🇹", "name": "Австрия",             "capital": "Вена",            "region": "Европа",        "desc": "Страна Моцарта, венских балов и Альп. Вена три года подряд признаётся лучшим городом мира для жизни.", "fact": "В Австрии находится самая старая в мире зоологическая станция — венский зоопарк Тиргартен Шёнбрунн (1752 г.)."},
    {"code": "AZ", "flag": "🇦🇿", "name": "Азербайджан",         "capital": "Баку",            "region": "Азия",          "desc": "Страна огней: вечные газовые факелы в пустыне, современный Баку с небоскрёбами и древние крепости.", "fact": "Азербайджан называют «страной огней» — здесь тысячи лет горят естественные газовые факелы."},
    {"code": "BS", "flag": "🇧🇸", "name": "Багамы",              "capital": "Нассау",          "region": "Карибы",        "desc": "700 островов с бирюзовой водой, белым песком и самым большим в мире подводным пещерным лабиринтом.", "fact": "На Багамах находится Голубая дыра Дина — самая глубокая морская голубая дыра в мире (202 м)."},
    {"code": "BH", "flag": "🇧🇭", "name": "Бахрейн",             "capital": "Манама",          "region": "Азия",          "desc": "Островное королевство в Персидском заливе с богатой историей добычи жемчуга и современными небоскрёбами.", "fact": "Бахрейн — единственная островная страна в Арабском мире."},
    {"code": "BD", "flag": "🇧🇩", "name": "Бангладеш",           "capital": "Дакка",           "region": "Азия",          "desc": "Страна рек и дельт, руины великих империй и Сундарбан — крупнейший мангровый лес в мире.", "fact": "В Бангладеш самая высокая плотность населения среди крупных стран мира."},
    {"code": "BB", "flag": "🇧🇧", "name": "Барбадос",            "capital": "Бриджтаун",       "region": "Карибы",        "desc": "Родина Рианны и рома Mount Gay — старейшего рома в мире. Коралловые рифы и живой карибский дух.", "fact": "Барбадос — самая восточная страна Карибского бассейна."},
    {"code": "BY", "flag": "🇧🇾", "name": "Беларусь",            "capital": "Минск",           "region": "Европа",        "desc": "Страна пущ, замков и партизанской истории. Беловежская пуща — последний первобытный лес Европы.", "fact": "В Беларуси находится Беловежская пуща — старейший заповедник в мире (XIV век)."},
    {"code": "BE", "flag": "🇧🇪", "name": "Бельгия",             "capital": "Брюссель",        "region": "Европа",        "desc": "Родина шоколада, вафель, пива и картофеля фри. Медиевальные города Брюгге и Гент — словно ожившие сказки.", "fact": "В Бельгии производится более 220 000 тонн шоколада в год."},
    {"code": "BZ", "flag": "🇧🇿", "name": "Белиз",               "capital": "Бельмопан",       "region": "Центральная Америка", "desc": "Единственная центральноамериканская страна с английским языком. Барьерный риф Белиза — второй по величине в мире.", "fact": "Великая голубая дыра у берегов Белиза — одно из лучших мест для дайвинга на планете."},
    {"code": "BJ", "flag": "🇧🇯", "name": "Бенин",               "capital": "Порто-Ново",      "region": "Африка",        "desc": "Родина культа вуду и королевства Дагомея. Уникальная живая история Западной Африки.", "fact": "Бенин — родина религии вуду, которая распространилась в Карибы через работорговлю."},
    {"code": "BT", "flag": "🇧🇹", "name": "Бутан",               "capital": "Тхимпху",         "region": "Азия",          "desc": "Королевство счастья в Гималаях. Единственная страна, измеряющая ВВС — Валовое Внутреннее Счастье.", "fact": "Бутан — единственная страна в мире с отрицательным углеродным балансом."},
    {"code": "BO", "flag": "🇧🇴", "name": "Боливия",             "capital": "Сукре",           "region": "Южная Америка", "desc": "Страна двух столиц, солончака Уюни и озера Титикака. Самое высокогорное судоходное озеро в мире.", "fact": "Солончак Уюни в Боливии — крупнейший в мире и содержит половину мировых запасов лития."},
    {"code": "BA", "flag": "🇧🇦", "name": "Босния и Герцеговина","capital": "Сараево",         "region": "Европа",        "desc": "Сараево — город, где встречаются Восток и Запад. Мостар со старым мостом и горные курорты Олимпиады-84.", "fact": "Сараево было первым городом в Европе, где появился трамвай (1885 год)."},
    {"code": "BW", "flag": "🇧🇼", "name": "Ботсвана",            "capital": "Габороне",        "region": "Африка",        "desc": "Лучшее сафари в Африке: дельта Окаванго, пустыня Калахари и огромные стада слонов.", "fact": "В Ботсване самая высокая концентрация слонов в мире — более 130 000 особей."},
    {"code": "BR", "flag": "🇧🇷", "name": "Бразилия",            "capital": "Бразилиа",        "region": "Южная Америка", "desc": "Страна карнавала, Амазонии и пляжа Копакабана. Крупнейшая страна Южной Америки с бешеной энергией жизни.", "fact": "Бразилия — единственная страна в Южной Америке, где говорят по-португальски."},
    {"code": "BN", "flag": "🇧🇳", "name": "Бруней",              "capital": "Бандар-Сери-Бегаван","region": "Азия",       "desc": "Крошечный нефтяной султанат на Борнео. Один из богатейших монархов мира и огромная мечеть Омара Али.", "fact": "В Брунее запрещён алкоголь, но страна входит в топ самых богатых по ВВП на душу населения."},
    {"code": "BG", "flag": "🇧🇬", "name": "Болгария",            "capital": "София",           "region": "Европа",        "desc": "Страна роз, Чёрного моря и фракийских золотых сокровищ. Один из самых доступных курортов Европы.", "fact": "Болгария — поставщик 70% мирового производства розового масла."},
    {"code": "BF", "flag": "🇧🇫", "name": "Буркина-Фасо",        "capital": "Уагадугу",        "region": "Африка",        "desc": "«Страна честных людей» — так переводится её название. Богатейшая культура масок и традиционных фестивалей.", "fact": "Уагадугу — одна из немногих столиц мира с таким необычным и звучным названием."},
    {"code": "BI", "flag": "🇧🇮", "name": "Бурунди",             "capital": "Гитега",          "region": "Африка",        "desc": "Маленькая горная страна у озера Танганьика — второго по глубине озера в мире.", "fact": "Озеро Танганьика у берегов Бурунди содержит 17% мировых запасов пресной воды."},
    {"code": "CV", "flag": "🇨🇻", "name": "Кабо-Верде",          "capital": "Прая",            "region": "Африка",        "desc": "Архипелаг вулканических островов у берегов Африки с постоянным бризом и музыкой морна.", "fact": "Кабо-Верде был необитаем до прихода португальцев в XV веке."},
    {"code": "KH", "flag": "🇰🇭", "name": "Камбоджа",            "capital": "Пномпень",        "region": "Азия",          "desc": "Родина Ангкор-Вата — крупнейшего религиозного сооружения в мире. Страна возрождается после трагедии.", "fact": "Ангкор-Ват изображён на флаге Камбоджи — единственный в мире флаг со зданием."},
    {"code": "CM", "flag": "🇨🇲", "name": "Камерун",             "capital": "Яунде",           "region": "Африка",        "desc": "«Африка в миниатюре»: от пляжей до саванны, от экватора до вулкана. Необыкновенное биоразнообразие.", "fact": "Камерун называют «Африкой в миниатюре» — здесь есть все климатические зоны континента."},
    {"code": "CA", "flag": "🇨🇦", "name": "Канада",              "capital": "Оттава",          "region": "Северная Америка", "desc": "Вторая по площади страна мира с Ниагарским водопадом, Скалистыми горами и кленовым сиропом.", "fact": "В Канаде больше озёр, чем во всех остальных странах мира вместе взятых."},
    {"code": "CF", "flag": "🇨🇫", "name": "ЦАР",                 "capital": "Банги",           "region": "Африка",        "desc": "Непроходимые леса, реки с бегемотами и горилл. Одна из самых нетронутых природных стран Африки.", "fact": "ЦАР входит в число наименее посещаемых туристами стран мира."},
    {"code": "TD", "flag": "🇹🇩", "name": "Чад",                 "capital": "Нджамена",        "region": "Африка",        "desc": "Сердце Африки: от Сахары на севере до тропических лесов на юге. Озеро Чад некогда было четвёртым по величине озером мира.", "fact": "Озеро Чад за 50 лет уменьшилось на 90% из-за климатических изменений."},
    {"code": "CL", "flag": "🇨🇱", "name": "Чили",                "capital": "Сантьяго",        "region": "Южная Америка", "desc": "Самая длинная страна мира: от пустыни Атакама до ледников Патагонии. Вулканы, острров Пасхи и вино.", "fact": "Чили — самая длинная страна в мире: 4300 км с севера на юг."},
    {"code": "CN", "flag": "🇨🇳", "name": "Китай",               "capital": "Пекин",           "region": "Азия",          "desc": "Четыре тысячи лет истории, Великая стена, терракотовая армия и мегаполисы будущего в одной стране.", "fact": "Великая Китайская стена — самое протяжённое сооружение в мире (более 21 000 км)."},
    {"code": "CO", "flag": "🇨🇴", "name": "Колумбия",            "capital": "Богота",          "region": "Южная Америка", "desc": "Страна магического реализма: Картахена, кофейный пояс, Амазония и Анды в одном флаконе.", "fact": "Колумбия — единственная страна Южной Америки с выходом к двум океанам."},
    {"code": "KM", "flag": "🇰🇲", "name": "Коморы",              "capital": "Морони",          "region": "Африка",        "desc": "Вулканический архипелаг в Индийском океане с уникальной флорой и нетронутыми пляжами.", "fact": "Коморы — один из крупнейших мировых производителей иланг-иланга для духов."},
    {"code": "CG", "flag": "🇨🇬", "name": "Конго",               "capital": "Браззавиль",      "region": "Африка",        "desc": "На берегах великой реки Конго — тропические леса, редкие гориллы и живая традиционная культура.", "fact": "Река Конго — самая глубокая река в мире (более 220 метров глубины)."},
    {"code": "CD", "flag": "🇨🇩", "name": "ДР Конго",            "capital": "Киншаса",         "region": "Африка",        "desc": "Крупнейшая страна Африки южнее Сахары. Река Конго, тропические леса Итури и горные гориллы Вирунги.", "fact": "ДР Конго обладает крупнейшими в мире запасами кобальта — более 70% мировых резервов."},
    {"code": "CR", "flag": "🇨🇷", "name": "Коста-Рика",          "capital": "Сан-Хосе",        "region": "Центральная Америка", "desc": "Страна «Пура Вида»: без армии, с 25% территории под заповедниками и лучшим биоразнообразием.", "fact": "Коста-Рика на 100% обеспечивает себя возобновляемой энергией более 300 дней в году."},
    {"code": "HR", "flag": "🇭🇷", "name": "Хорватия",            "capital": "Загреб",          "region": "Европа",        "desc": "Лазурная Адриатика, 1000 островов, Дубровник-«жемчужина Адриатики» и Плитвицкие озёра.", "fact": "В Хорватии изобрели галстук — слово cravat происходит от слова «хорват»."},
    {"code": "CI", "flag": "🇨🇮", "name": "Кот-д'Ивуар",         "capital": "Ямусукро",        "region": "Африка",        "desc": "Крупнейшая экономика Западной Африки и мировая столица какао. Абиджан — финансовый центр региона.", "fact": "Кот-д'Ивуар производит около 40% всего мирового какао."},
    {"code": "CU", "flag": "🇨🇺", "name": "Куба",                "capital": "Гавана",          "region": "Карибы",        "desc": "Остров застывшего времени: ретро-автомобили, сигары, сальса и пляжи Варадеро.", "fact": "На Кубе эксплуатируется самая большая в мире коллекция автомобилей 1950-х годов."},
    {"code": "CY", "flag": "🇨🇾", "name": "Кипр",                "capital": "Никосия",         "region": "Европа",        "desc": "Остров Афродиты в Средиземном море с античными руинами, виноградниками и тёплым морем 10 месяцев в году.", "fact": "Кипр — единственная страна ЕС, столица которой (Никосия) до сих пор разделена."},
    {"code": "CZ", "flag": "🇨🇿", "name": "Чехия",               "capital": "Прага",           "region": "Европа",        "desc": "Прага — город сотни шпилей. Замки, пивные традиции и стеклянные шедевры богемского хрусталя.", "fact": "Чехия — мировой лидер по потреблению пива на душу населения."},
    {"code": "DK", "flag": "🇩🇰", "name": "Дания",               "capital": "Копенгаген",      "region": "Европа",        "desc": "Родина Андерсена, Lego и хюгге. Копенгаген — самый велосипедный и счастливый город мира.", "fact": "Датский флаг Даннеброг — старейший государственный флаг в мире (1219 год)."},
    {"code": "DJ", "flag": "🇩🇯", "name": "Джибути",             "capital": "Джибути",         "region": "Африка",        "desc": "Крошечная страна на Африканском роге с солёным озером Ассаль — самой низкой точкой Африки.", "fact": "Озеро Ассаль в Джибути — самое солёное озеро в мире после Мёртвого моря."},
    {"code": "DM", "flag": "🇩🇲", "name": "Доминика",            "capital": "Розо",            "region": "Карибы",        "desc": "«Природный остров» Карибов: вулканические горы, кипящее озеро и попугаи сисеру.", "fact": "На Доминике есть кипящее озеро — второе по величине в мире."},
    {"code": "DO", "flag": "🇩🇴", "name": "Доминикана",          "capital": "Санто-Доминго",   "region": "Карибы",        "desc": "Первый город Нового Света, пальмы Пунта-Каны и горы Кордильера-Сентраль.", "fact": "Санто-Доминго — старейший постоянно заселённый европейский город в Западном полушарии."},
    {"code": "EC", "flag": "🇪🇨", "name": "Эквадор",             "capital": "Кито",            "region": "Южная Америка", "desc": "Галапагосы, Амазония, Анды и тихоокеанский берег — всё в одной маленькой стране.", "fact": "Эквадор назван по линии экватора, которая пересекает страну."},
    {"code": "EG", "flag": "🇪🇬", "name": "Египет",              "capital": "Каир",            "region": "Африка",        "desc": "Семь тысяч лет цивилизации: пирамиды Гизы, Луксор и нырялка в Красном море.", "fact": "Древние египтяне изобрели бумагу, чернила, замок и ключ."},
    {"code": "SV", "flag": "🇸🇻", "name": "Сальвадор",           "capital": "Сан-Сальвадор",   "region": "Центральная Америка", "desc": "Самая маленькая страна материковой Америки с вулканами-серфингом и кофе высшего качества.", "fact": "Сальвадор — первая страна в мире, принявшая биткоин как официальное платёжное средство."},
    {"code": "GQ", "flag": "🇬🇶", "name": "Экваториальная Гвинея","capital": "Малабо",         "region": "Африка",        "desc": "Единственная испаноязычная страна Африки. Нефтяное богатство соседствует с первозданными тропическими лесами.", "fact": "Малабо — единственная в мире столица, расположенная на острове, не соединённом с материком."},
    {"code": "ER", "flag": "🇪🇷", "name": "Эритрея",             "capital": "Асмэра",          "region": "Африка",        "desc": "Столица Асмэра с итальянской архитектурой — живой музей арт-деко на Африканском роге.", "fact": "Асмэра включена в список Всемирного наследия ЮНЕСКО как выдающийся пример модернистского города."},
    {"code": "EE", "flag": "🇪🇪", "name": "Эстония",             "capital": "Таллин",          "region": "Европа",        "desc": "Самая цифровая страна мира: электронное гражданство, средневековый Таллин и сосновые леса.", "fact": "В Эстонии впервые в мире введено электронное резидентство для нерезидентов."},
    {"code": "SZ", "flag": "🇸🇿", "name": "Эсватини",            "capital": "Мбабане",         "region": "Африка",        "desc": "Одна из последних абсолютных монархий в мире, окружённая ЮАР и Мозамбиком.", "fact": "Эсватини (бывший Свазиленд) — одна из трёх оставшихся абсолютных монархий в мире."},
    {"code": "ET", "flag": "🇪🇹", "name": "Эфиопия",             "capital": "Аддис-Абеба",     "region": "Африка",        "desc": "Родина кофе, православных монастырей на скалах и уникального календаря с 13 месяцами.", "fact": "Эфиопия — родина кофе: дикий кофе был обнаружен в регионе Каффа."},
    {"code": "FJ", "flag": "🇫🇯", "name": "Фиджи",               "capital": "Сува",            "region": "Океания",       "desc": "333 острова с коралловыми рифами, дружелюбными местными жителями и главным в мире производством мягких кораллов.", "fact": "Фиджи известно как «мягкокоралловая столица мира»."},
    {"code": "FI", "flag": "🇫🇮", "name": "Финляндия",           "capital": "Хельсинки",       "region": "Европа",        "desc": "Страна тысячи озёр, северного сияния, сауны и родина Деда Мороза (Санта-Клауса).", "fact": "В Финляндии на каждого жителя приходится более двух сауны."},
    {"code": "FR", "flag": "🇫🇷", "name": "Франция",             "capital": "Париж",           "region": "Европа",        "desc": "Самая посещаемая страна мира: Эйфелева башня, Лувр, Прованс и лучшая кухня планеты.", "fact": "Франция — самая посещаемая страна мира: более 90 миллионов туристов ежегодно."},
    {"code": "GA", "flag": "🇬🇦", "name": "Габон",               "capital": "Либревиль",       "region": "Африка",        "desc": "85% территории покрыто джунглями. Страна мандрилов, горилл и нетронутых атлантических пляжей.", "fact": "В Габоне 88% территории покрыто тропическими лесами."},
    {"code": "GM", "flag": "🇬🇲", "name": "Гамбия",              "capital": "Банжул",          "region": "Африка",        "desc": "Самая маленькая страна материковой Африки — тонкая полоска вдоль реки Гамбия.", "fact": "Гамбия — самая маленькая страна на африканском материке."},
    {"code": "GE", "flag": "🇬🇪", "name": "Грузия",              "capital": "Тбилиси",         "region": "Азия",          "desc": "Родина вина, хачапури и невероятного гостеприимства. Кавказские горы, пещерные города и Батуми.", "fact": "Грузия — родина виноделия: здесь обнаружены следы вина возрастом 8000 лет."},
    {"code": "DE", "flag": "🇩🇪", "name": "Германия",            "capital": "Берлин",          "region": "Европа",        "desc": "Берлин, Бавария, Октоберфест и 1500 видов пива. Страна автобанов, замков и Баха.", "fact": "В Германии более 1500 различных сортов пива и около 1300 пивоварен."},
    {"code": "GH", "flag": "🇬🇭", "name": "Гана",                "capital": "Аккра",           "region": "Африка",        "desc": "«Звезда Африки» — первая страна Африки южнее Сахары получившая независимость. Крупнейший в мире производитель какао.", "fact": "Гана — второй по величине производитель какао в мире."},
    {"code": "GR", "flag": "🇬🇷", "name": "Греция",              "capital": "Афины",           "region": "Европа",        "desc": "Колыбель западной цивилизации. Акрополь, Санторини, оливковое масло и море, которое никогда не надоедает.", "fact": "Греция имеет самую длинную береговую линию в Европе — более 13 000 км."},
    {"code": "GD", "flag": "🇬🇩", "name": "Гренада",             "capital": "Сент-Джорджес",   "region": "Карибы",        "desc": "«Остров специй»: мускатный орех, корица, гвоздика и шоколадные фермы с туром для туристов.", "fact": "Гренада — крупнейший в мире производитель мускатного ореха."},
    {"code": "GT", "flag": "🇬🇹", "name": "Гватемала",           "capital": "Гватемала-Сити",  "region": "Центральная Америка", "desc": "Сердце мира майя: Тикаль, колониальная Антигуа и вулкан Акатенанго с видом на лавовые реки.", "fact": "В Гватемале более 30 вулканов, три из которых активны постоянно."},
    {"code": "GN", "flag": "🇬🇳", "name": "Гвинея",              "capital": "Конакри",         "region": "Африка",        "desc": "«Водонапорная башня Африки» — отсюда вытекают крупнейшие реки континента.", "fact": "Из Гвинеи берут начало реки Нигер, Сенегал и Гамбия."},
    {"code": "GW", "flag": "🇬🇼", "name": "Гвинея-Бисау",        "capital": "Бисау",           "region": "Африка",        "desc": "Архипелаг Биджогос с нетронутыми мангровыми островами и морскими черепахами.", "fact": "Архипелаг Биджагош в Гвинее-Бисау — биосферный резерват ЮНЕСКО."},
    {"code": "GY", "flag": "🇬🇾", "name": "Гайана",              "capital": "Джорджтаун",      "region": "Южная Америка", "desc": "Водопад Кайетур — один из мощнейших в мире. Единственная англоязычная страна Южной Америки.", "fact": "Водопад Кайетур в Гайане в 4 раза выше Ниагарского и в 5 раз мощнее."},
    {"code": "HT", "flag": "🇭🇹", "name": "Гаити",               "capital": "Порт-о-Пренс",   "region": "Карибы",        "desc": "Первая чёрная республика мира, добившаяся независимости через революцию рабов в 1804 году.", "fact": "Гаити — первая страна в Западном полушарии, отменившая рабство."},
    {"code": "HN", "flag": "🇭🇳", "name": "Гондурас",            "capital": "Тегусигальпа",    "region": "Центральная Америка", "desc": "Руины Копан — самого художественно богатого города майя. Карибские рифы острова Роатан.", "fact": "Копан в Гондурасе — наиболее художественно изощрённый из всех городов майя."},
    {"code": "HU", "flag": "🇭🇺", "name": "Венгрия",             "capital": "Будапешт",        "region": "Европа",        "desc": "Будапешт — «Жемчужина Дуная» с термальными банями, Art Nouveau архитектурой и паприкой.", "fact": "Будапешт — мировая столица термальных ванн: в городе более 100 горячих источников."},
    {"code": "IS", "flag": "🇮🇸", "name": "Исландия",            "capital": "Рейкьявик",       "region": "Европа",        "desc": "Страна огня и льда: гейзеры, вулканы, Голубая лагуна и северное сияние.", "fact": "В Исландии нет комаров — единственная страна в мире без этих насекомых."},
    {"code": "IN", "flag": "🇮🇳", "name": "Индия",               "capital": "Нью-Дели",        "region": "Азия",          "desc": "Тадж-Махал, специи, Ганг и 1,4 миллиарда историй. Страна, которая меняет сознание.", "fact": "Индия — крупнейшая демократия в мире и родина шахмат."},
    {"code": "ID", "flag": "🇮🇩", "name": "Индонезия",           "capital": "Джакарта",        "region": "Азия",          "desc": "17 000 островов, Бали, Комодо с драконами, Борнео и Ява. Четвёртая по населению страна мира.", "fact": "Индонезия состоит из более чем 17 000 островов — больше, чем любая другая страна."},
    {"code": "IR", "flag": "🇮🇷", "name": "Иран",                "capital": "Тегеран",         "region": "Азия",          "desc": "Персия: Исфахан с куполами мечетей, Персеполь, шафран и невероятное гостеприимство.", "fact": "Иран — один из старейших мировых цивилизационных центров с историей более 7000 лет."},
    {"code": "IQ", "flag": "🇮🇶", "name": "Ирак",                "capital": "Багдад",          "region": "Азия",          "desc": "Колыбель цивилизации: Месопотамия, Вавилон, Ур и тысячелетние традиции.", "fact": "Ирак — родина письменности: здесь возникла древнейшая система письма — шумерская клинопись."},
    {"code": "IE", "flag": "🇮🇪", "name": "Ирландия",            "capital": "Дублин",          "region": "Европа",        "desc": "Зелёный остров с кельтскими легендами, утёсами Мохер, пабами и Гиннессом.", "fact": "Ирландия — единственная страна ЕС, где гэльский язык является первым официальным."},
    {"code": "IL", "flag": "🇮🇱", "name": "Израиль",             "capital": "Иерусалим",       "region": "Азия",          "desc": "Иерусалим — святой город трёх религий. Мёртвое море, Масада и современный Тель-Авив.", "fact": "Израиль — единственная страна в мире, основанная заново после почти 2000-летнего перерыва."},
    {"code": "IT", "flag": "🇮🇹", "name": "Италия",              "capital": "Рим",             "region": "Европа",        "desc": "Рим, Флоренция, Венеция, Амальфи — бесконечный музей под открытым небом с лучшей едой.", "fact": "В Италии больше объектов ЮНЕСКО, чем в любой другой стране мира (58 объектов)."},
    {"code": "JM", "flag": "🇯🇲", "name": "Ямайка",              "capital": "Кингстон",        "region": "Карибы",        "desc": "Родина регги, Боба Марли и самых быстрых людей на Земле. Blue Mountains и Дюнны-Фолс.", "fact": "Ямайка — родина регги-музыки и родина самых быстрых бегунов в мире."},
    {"code": "JP", "flag": "🇯🇵", "name": "Япония",              "capital": "Токио",           "region": "Азия",          "desc": "Фудзи, сакура, суши и технологии. Страна, где традиции и инновации существуют вместе.", "fact": "В Японии более 6800 островов, а токийское метро — самое пунктуальное в мире."},
    {"code": "JO", "flag": "🇯🇴", "name": "Иордания",            "capital": "Амман",           "region": "Азия",          "desc": "Петра — «розовый город» в скалах, Мёртвое море, пустыня Вади-Рам и доброжелательные люди.", "fact": "Мёртвое море у берегов Иордании — самая низкая точка суши на Земле (-430 м)."},
    {"code": "KZ", "flag": "🇰🇿", "name": "Казахстан",           "capital": "Астана",          "region": "Азия",          "desc": "Девятая по площади страна мира: бескрайние степи, горы Алматы, яблочная столица и Байконур.", "fact": "Казахстан — крупнейшая в мире страна, не имеющая выхода к морю."},
    {"code": "KE", "flag": "🇰🇪", "name": "Кения",               "capital": "Найроби",         "region": "Африка",        "desc": "Масаи Мара — лучшее сафари на планете. Гора Килиманджаро и марафонские традиции.", "fact": "Кения — родина кроссового бега: кенийские спортсмены выиграли большинство мировых марафонов."},
    {"code": "KI", "flag": "🇰🇮", "name": "Кирибати",            "capital": "Южная Тарава",    "region": "Океания",       "desc": "Единственная страна, расположенная сразу во всех четырёх полушариях Земли.", "fact": "Кирибати — единственная страна в мире, находящаяся во всех четырёх полушариях одновременно."},
    {"code": "KP", "flag": "🇰🇵", "name": "КНДР",                "capital": "Пхеньян",         "region": "Азия",          "desc": "Одна из самых закрытых стран мира. Пхеньян с монументальной архитектурой и горы Пэктусан.", "fact": "КНДР — единственная страна в мире, сохранившая коммунизм в первоначальной форме."},
    {"code": "KW", "flag": "🇰🇼", "name": "Кувейт",              "capital": "Эль-Кувейт",      "region": "Азия",          "desc": "Нефтяное государство Персидского залива с современными небоскрёбами и традиционными рынками.", "fact": "Кувейт обладает 6% мировых запасов нефти при населении всего 4 миллиона человек."},
    {"code": "KG", "flag": "🇰🇬", "name": "Кыргызстан",          "capital": "Бишкек",          "region": "Азия",          "desc": "Горная страна с озером Иссык-Куль, юртами кочевников и эпосом «Манас» — длиннейшим в мире.", "fact": "Эпос «Манас» кыргызского народа в 20 раз длиннее «Илиады» и «Одиссеи» вместе взятых."},
    {"code": "LA", "flag": "🇱🇦", "name": "Лаос",                "capital": "Вьентьян",        "region": "Азия",          "desc": "Единственная страна Юго-Восточной Азии без моря: Меконг, буддийские пагоды и водопады Куанг Си.", "fact": "Лаос — самая разбомбленная страна в мире: за войну во Вьетнаме сброшено больше бомб, чем на всю Европу в WWII."},
    {"code": "LV", "flag": "🇱🇻", "name": "Латвия",              "capital": "Рига",            "region": "Европа",        "desc": "Рига с крупнейшим в мире кварталом Art Nouveau и янтарное побережье Балтики.", "fact": "Рига — крупнейший центр архитектуры Art Nouveau в мире: более 800 зданий в этом стиле."},
    {"code": "LB", "flag": "🇱🇧", "name": "Ливан",               "capital": "Бейрут",          "region": "Азия",          "desc": "«Ближневосточный Париж»: Бейрут, руины Баальбека, кедровые леса и лучшая кухня региона.", "fact": "Кедр Ливана изображён на государственном флаге страны."},
    {"code": "LS", "flag": "🇱🇸", "name": "Лесото",              "capital": "Масеру",          "region": "Африка",        "desc": "Единственная страна в мире, полностью расположенная выше 1000 метров над уровнем моря.", "fact": "Лесото — самая высокогорная страна в мире, полностью окружённая ЮАР."},
    {"code": "LR", "flag": "🇱🇷", "name": "Либерия",             "capital": "Монровия",        "region": "Африка",        "desc": "Основана освобождёнными американскими рабами в 1822 году. Первая республика Африки.", "fact": "Либерия — первая независимая республика в Африке, основанная в 1847 году."},
    {"code": "LY", "flag": "🇱🇾", "name": "Ливия",               "capital": "Триполи",         "region": "Африка",        "desc": "Античные руины Лептис-Магны и Сабраты — лучшие в Средиземноморье, плюс дюны Сахары.", "fact": "Лептис-Магна в Ливии — один из наиболее хорошо сохранившихся римских городов в мире."},
    {"code": "LI", "flag": "🇱🇮", "name": "Лихтенштейн",         "capital": "Вадуц",           "region": "Европа",        "desc": "Одна из двух двойных стран-анклавов в мире. Маленькое, но богатейшее государство в Альпах.", "fact": "Лихтенштейн — одна из двух стран мира, окружённых двумя государствами без выхода к морю."},
    {"code": "LT", "flag": "🇱🇹", "name": "Литва",               "capital": "Вильнюс",         "region": "Европа",        "desc": "Вильнюс — барочная столица с самым большим в ЕС старым городом и тянутым янтарём.", "fact": "Литва последней из европейских стран приняла христианство (1387 год)."},
    {"code": "LU", "flag": "🇱🇺", "name": "Люксембург",          "capital": "Люксембург",      "region": "Европа",        "desc": "Самый богатый народ Европы в стране замков, банков и штаб-квартир ЕС.", "fact": "Люксембург имеет самый высокий ВВП на душу населения среди стран ЕС."},
    {"code": "MG", "flag": "🇲🇬", "name": "Мадагаскар",          "capital": "Антананариву",    "region": "Африка",        "desc": "Восьмой континент: 90% флоры и фауны — эндемики. Лемуры, баобабы и неземные пейзажи.", "fact": "На Мадагаскаре обитает 90% видов флоры и фауны, не встречающихся нигде в мире."},
    {"code": "MW", "flag": "🇲🇼", "name": "Малави",              "capital": "Лилонгве",        "region": "Африка",        "desc": "«Тёплое сердце Африки»: озеро Малави — «Африканское море» с сотнями видов цихлид.", "fact": "Озеро Малави содержит больше видов рыб, чем любое другое озеро на Земле."},
    {"code": "MY", "flag": "🇲🇾", "name": "Малайзия",            "capital": "Куала-Лумпур",    "region": "Азия",          "desc": "Башни Петронас, острова Борнео с орангутангами, дайвинг на Сипадане и уличная еда.", "fact": "Башни Петронас в Куала-Лумпуре были самыми высокими зданиями мира с 1998 по 2004 год."},
    {"code": "MV", "flag": "🇲🇻", "name": "Мальдивы",            "capital": "Мале",            "region": "Азия",          "desc": "Самая плоская страна мира: кристальная вода, белый коралловый песок и бунгало на сваях.", "fact": "Мальдивы — самая низкорасположенная страна в мире: средняя высота над уровнем моря — 1,5 м."},
    {"code": "ML", "flag": "🇲🇱", "name": "Мали",                "capital": "Бамако",          "region": "Африка",        "desc": "Тимбукту — легендарный город золота и соли, сердце исламского учёного мира Средних веков.", "fact": "Тимбукту в Мали был центром исламской учёности в XIV–XVI веках с крупнейшими библиотеками мира."},
    {"code": "MT", "flag": "🇲🇹", "name": "Мальта",              "capital": "Валлетта",        "region": "Европа",        "desc": "Самая маленькая столица ЕС с мегалитическими храмами старше Стоунхенджа и Египетских пирамид.", "fact": "Мегалитические храмы Мальты — старейшие отдельно стоящие каменные постройки в мире (3600–2500 до н.э.)."},
    {"code": "MH", "flag": "🇲🇭", "name": "Маршалловы острова",  "capital": "Маджуро",         "region": "Океания",       "desc": "Атоллы в Тихом океане с ядерным прошлым: здесь США испытывали ядерное оружие.", "fact": "На атолле Бикини (Маршалловы острова) было проведено 23 ядерных испытания."},
    {"code": "MR", "flag": "🇲🇷", "name": "Мавритания",          "capital": "Нуакшот",         "region": "Африка",        "desc": "Сахара и Атлантика: старый город Шингетти и каменные пустыни с розовыми озёрами.", "fact": "Мавритания отменила рабство последней в мире — лишь в 1981 году."},
    {"code": "MU", "flag": "🇲🇺", "name": "Маврикий",            "capital": "Порт-Луи",        "region": "Африка",        "desc": "Тропический рай в Индийском океане — родина вымершего дронта (додо) и лагуны с подводными водопадами.", "fact": "Дронт (додо) был эндемиком Маврикия и вымер в XVII веке после появления европейцев."},
    {"code": "MX", "flag": "🇲🇽", "name": "Мексика",             "capital": "Мехико",          "region": "Северная Америка", "desc": "Пирамиды ацтеков, Юкатан с сенотами, Оахака с мескалем и Тихоокеанские пляжи.", "fact": "Мексика ввела шоколад, ваниль, помидоры, авокадо и перец чили в мировую кухню."},
    {"code": "FM", "flag": "🇫🇲", "name": "Микронезия",          "capital": "Паликир",         "region": "Океания",       "desc": "600 островов в западной части Тихого океана с руинами Нан-Мадола — «Тихоокеанской Венецией».", "fact": "Нан-Мадол на Понпеи — единственный в мире древний город, построенный на коралловых рифах."},
    {"code": "MD", "flag": "🇲🇩", "name": "Молдова",             "capital": "Кишинёв",         "region": "Европа",        "desc": "Родина гагаузов, молдавского вина и самого большого в мире подземного винного погреба.", "fact": "В Молдове находится Милештий Мичь — крупнейший в мире подземный погреб с 2 миллионами бутылок вина."},
    {"code": "MC", "flag": "🇲🇨", "name": "Монако",              "capital": "Монако",          "region": "Европа",        "desc": "Самое маленькое государство после Ватикана. Казино Монте-Карло, Гран-при Формулы-1 и яхты.", "fact": "Монако — самая плотнонаселённая страна мира: более 26 000 чел/км²."},
    {"code": "MN", "flag": "🇲🇳", "name": "Монголия",            "capital": "Улан-Батор",      "region": "Азия",          "desc": "Бескрайние степи, пустыня Гоби, юрты номадов и наследие Чингисхана.", "fact": "Монголия — страна с наименьшей плотностью населения в мире: около 2 чел/км²."},
    {"code": "ME", "flag": "🇲🇪", "name": "Черногория",          "capital": "Подгорица",       "region": "Европа",        "desc": "Бока-Которская бухта — красивейшая в Европе. Горы, Адриатика и средневековые города.", "fact": "Которская бухта — самый южный фьорд в Европе."},
    {"code": "MA", "flag": "🇲🇦", "name": "Марокко",             "capital": "Рабат",           "region": "Африка",        "desc": "Марракеш, Фес, Сахара, горы Атлас и Атлантическое побережье — всё в одной стране.", "fact": "Университет Аль-Карауин в Фесе (859 г.) — старейший непрерывно действующий университет в мире."},
    {"code": "MZ", "flag": "🇲🇿", "name": "Мозамбик",            "capital": "Мапуто",          "region": "Африка",        "desc": "2500 км береговой линии, остров Мозамбик — объект ЮНЕСКО и лучшие коралловые рифы Индийского океана.", "fact": "Остров Мозамбик дал название всей стране."},
    {"code": "MM", "flag": "🇲🇲", "name": "Мьянма",              "capital": "Нейпьидо",        "region": "Азия",          "desc": "Баган с тысячами буддийских пагод, Инле с плавучими деревнями и рубиновые шахты Могок.", "fact": "В Багане (Мьянма) сосредоточено более 2000 буддийских храмов и пагод."},
    {"code": "NA", "flag": "🇳🇦", "name": "Намибия",             "capital": "Виндхук",         "region": "Африка",        "desc": "Красные дюны Соссусфлей, призрачный Колманскоп и пустыня Намиб — старейшая на Земле.", "fact": "Пустыня Намиб — старейшая пустыня в мире: ей более 55 миллионов лет."},
    {"code": "NR", "flag": "🇳🇷", "name": "Науру",               "capital": "Ярен",            "region": "Океания",       "desc": "Самая маленькая островная страна в мире. Некогда самая богатая страна на Земле благодаря фосфатам.", "fact": "Науру — самая маленькая островная страна мира (21 км²)."},
    {"code": "NP", "flag": "🇳🇵", "name": "Непал",               "capital": "Катманду",        "region": "Азия",          "desc": "Родина Будды и 8 из 10 высочайших вершин мира, включая Эверест. Треккинг как образ жизни.", "fact": "В Непале находятся 8 из 10 самых высоких гор мира, включая Эверест (8849 м)."},
    {"code": "NL", "flag": "🇳🇱", "name": "Нидерланды",          "capital": "Амстердам",       "region": "Европа",        "desc": "Страна тюльпанов, ветряных мельниц, каналов и Рембрандта. Амстердам — велосипедная столица мира.", "fact": "В Нидерландах велосипедов больше, чем жителей."},
    {"code": "NZ", "flag": "🇳🇿", "name": "Новая Зеландия",      "capital": "Веллингтон",      "region": "Океания",       "desc": "Родина хоббитов, маори и самого чистого воздуха на планете. Фьорды, вулканы и киви.", "fact": "Новая Зеландия — первая страна в мире, предоставившая женщинам право голосовать (1893 год)."},
    {"code": "NI", "flag": "🇳🇮", "name": "Никарагуа",           "capital": "Манагуа",         "region": "Центральная Америка", "desc": "Сёрфинг на вулканическом пепле Серро-Негро, колониальная Гранада и озеро Никарагуа.", "fact": "В Никарагуа можно кататься на сёрфе с вулканического склона — это называется «вулканический сёрфинг»."},
    {"code": "NE", "flag": "🇳🇪", "name": "Нигер",               "capital": "Ниамей",          "region": "Африка",        "desc": "80% территории — Сахара. Страна туарегов, верблюдов и солёных каньонов.", "fact": "Нигер — самая большая страна Западной Африки."},
    {"code": "NG", "flag": "🇳🇬", "name": "Нигерия",             "capital": "Абуджа",          "region": "Африка",        "desc": "Самая населённая страна Африки — «гигант Африки». Нигерийское кино (Нолливуд) — второе в мире.", "fact": "Нигерия — крупнейшая экономика Африки и самая населённая страна континента."},
    {"code": "NO", "flag": "🇳🇴", "name": "Норвегия",            "capital": "Осло",            "region": "Европа",        "desc": "Фьорды, северное сияние, тролли и самый высокий уровень жизни. Страна, открывшая нефть в Северном море.", "fact": "Норвегия имеет крупнейший суверенный фонд благосостояния в мире."},
    {"code": "OM", "flag": "🇴🇲", "name": "Оман",                "capital": "Маскат",          "region": "Азия",          "desc": "Самое безопасное арабское государство: пустыня Руб-эль-Хали, фьорды Мусандам и горы Джабаль-Ахдар.", "fact": "Оман — одна из старейших независимых наций в арабском мире с историей более 5000 лет."},
    {"code": "PK", "flag": "🇵🇰", "name": "Пакистан",            "capital": "Исламабад",       "region": "Азия",          "desc": "К2 — второй пик мира, шоссе Каракорум и древняя цивилизация Мохенджо-Даро.", "fact": "В Пакистане находятся пять из четырнадцати восьмитысячников мира."},
    {"code": "PW", "flag": "🇵🇼", "name": "Палау",               "capital": "Нгерулмуд",       "region": "Океания",       "desc": "Лучшее место для дайвинга на планете: Озеро медуз, акулы и коралловые сады.", "fact": "Палау первой в мире создало национальное морское святилище (2009 год)."},
    {"code": "PS", "flag": "🇵🇸", "name": "Палестина",           "capital": "Рамалла",         "region": "Азия",          "desc": "Земля трёх мировых религий: Вифлеем, Иерихон — один из древнейших городов мира, и Мёртвое море.", "fact": "Иерихон в Палестине — один из старейших непрерывно населённых городов мира (более 10 000 лет)."},
    {"code": "PA", "flag": "🇵🇦", "name": "Панама",              "capital": "Панама-Сити",     "region": "Центральная Америка", "desc": "Панамский канал — чудо инженерии. Единственное место, где можно увидеть закат над Тихим океаном, стоя лицом на восток.", "fact": "Панамский канал сокращает морской путь из Атлантики в Тихий океан на 15 000 км."},
    {"code": "PG", "flag": "🇵🇬", "name": "Папуа Новая Гвинея",  "capital": "Порт-Морсби",     "region": "Океания",       "desc": "850 языков — больше, чем в любой другой стране. Нетронутые джунгли и традиционные племена.", "fact": "В Папуа Новой Гвинее говорят на более 850 языках — это больше, чем в любой другой стране."},
    {"code": "PY", "flag": "🇵🇾", "name": "Парагвай",            "capital": "Асунсьон",        "region": "Южная Америка", "desc": "Двуязычная страна (испанский и гуарани), иезуитские миссии ЮНЕСКО и водопады Игуасу.", "fact": "Парагвай — единственная страна в мире с двусторонним флагом (разные стороны имеют разный рисунок)."},
    {"code": "PE", "flag": "🇵🇪", "name": "Перу",                "capital": "Лима",            "region": "Южная Америка", "desc": "Мачу-Пикчу, озеро Титикака, кухня признана лучшей в мире и Амазония.", "fact": "Кухня Перу трижды признавалась лучшей в мире по версии World Travel Awards."},
    {"code": "PH", "flag": "🇵🇭", "name": "Филиппины",           "capital": "Манила",          "region": "Азия",          "desc": "7107 островов, Шоколадные холмы, тарсиеры и лучший в Азии дайвинг в Туббатаха.", "fact": "Шоппинг-центры Филиппин — одни из крупнейших в мире: SM Mall of Asia — шестой по величине."},
    {"code": "PL", "flag": "🇵🇱", "name": "Польша",              "capital": "Варшава",         "region": "Европа",        "desc": "Краков с Вавелем, соляная шахта Величка, Беловежская пуща и самое дешёвое пиво в ЕС.", "fact": "Польша является крупнейшим производителем яблок в Европе."},
    {"code": "PT", "flag": "🇵🇹", "name": "Португалия",          "capital": "Лиссабон",        "region": "Европа",        "desc": "Лиссабон на семи холмах, фаду, пастель-де-ната и Атлантические волны Назаре.", "fact": "Португалия — самая западная точка континентальной Европы."},
    {"code": "QA", "flag": "🇶🇦", "name": "Катар",               "capital": "Доха",            "region": "Азия",          "desc": "Из рыбацкой деревни в мировую столицу: Доха, «Перл», Мусей современного искусства.", "fact": "Катар имеет самый высокий ВВП на душу населения в мире."},
    {"code": "RO", "flag": "🇷🇴", "name": "Румыния",             "capital": "Бухарест",        "region": "Европа",        "desc": "Трансильвания с замком Дракулы, Карпаты, дельта Дуная — крупнейший биосферный резерват Европы.", "fact": "Дельта Дуная в Румынии — крупнейшее место гнездования птиц в Европе."},
    {"code": "RU", "flag": "🇷🇺", "name": "Россия",              "capital": "Москва",          "region": "Европа/Азия",   "desc": "Крупнейшая страна мира: Байкал, Красная площадь, Транссиб и 11 часовых поясов.", "fact": "Россия — самая большая страна в мире: её площадь больше поверхности Плутона."},
    {"code": "RW", "flag": "🇷🇼", "name": "Руанда",              "capital": "Кигали",          "region": "Африка",        "desc": "«Страна тысячи холмов» — самое чистое государство Африки и лучшее место для наблюдения горилл.", "fact": "Руанда — одна из чистейших стран в мире: пластиковые пакеты здесь запрещены с 2008 года."},
    {"code": "KN", "flag": "🇰🇳", "name": "Сент-Китс и Невис",   "capital": "Бастер",          "region": "Карибы",        "desc": "Первая британская колония в Карибах с вулканом, сахарными плантациями и дождевыми лесами.", "fact": "Сент-Китс и Невис — наименьшая страна в Западном полушарии."},
    {"code": "LC", "flag": "🇱🇨", "name": "Сент-Люсия",          "capital": "Кастри",          "region": "Карибы",        "desc": "Два пика Питон и шоколадные плантации. Карибский остров, дважды менявший флаг (Франция/Британия).", "fact": "На Сент-Люсии родилось больше нобелевских лауреатов на душу населения, чем в любой другой стране."},
    {"code": "VC", "flag": "🇻🇨", "name": "Сент-Винсент и Гренадины","capital": "Кингстаун",  "region": "Карибы",        "desc": "Пиратские острова Карибского моря: здесь снимали «Пираты Карибского моря».", "fact": "Острова Гренадины — место съёмок фильмов о Джеке Воробье."},
    {"code": "WS", "flag": "🇼🇸", "name": "Самоа",               "capital": "Апиа",            "region": "Океания",       "desc": "«Сердце Полинезии»: водопады, коралловые рифы и традиционные деревни фа'а Самоа.", "fact": "Самоа в 2011 году перепрыгнуло через линию перемены дат, «потеряв» один день."},
    {"code": "SM", "flag": "🇸🇲", "name": "Сан-Марино",          "capital": "Сан-Марино",      "region": "Европа",        "desc": "Самая старая республика в мире (301 год н.э.), полностью окружённая Италией.", "fact": "Сан-Марино — самая старая конституционная республика в мире, основана в 301 году."},
    {"code": "ST", "flag": "🇸🇹", "name": "Сан-Томе и Принсипи", "capital": "Сан-Томе",        "region": "Африка",        "desc": "Затерянные острова в Атлантике с какао-плантациями, тропическими лесами и пляжами без туристов.", "fact": "Сан-Томе и Принсипи — второй по величине производитель какао на душу населения в мире."},
    {"code": "SA", "flag": "🇸🇦", "name": "Саудовская Аравия",   "capital": "Эр-Рияд",         "region": "Азия",          "desc": "Мекка и Медина — священные города ислама. Аль-Ула с набатейскими руинами и пустыня Руб-эль-Хали.", "fact": "В Саудовской Аравии нет ни одной реки."},
    {"code": "SN", "flag": "🇸🇳", "name": "Сенегал",             "capital": "Дакар",           "region": "Африка",        "desc": "Самая западная точка Африки: остров Горе с историей работорговли, Дакар и Розовое озеро.", "fact": "Розовое озеро Ретба в Сенегале розового цвета из-за бактерий и высокой солёности."},
    {"code": "RS", "flag": "🇷🇸", "name": "Сербия",              "capital": "Белград",         "region": "Европа",        "desc": "Белград — место слияния Дуная и Савы. Монастыри Фрушка-горы и крепость Калемегдан.", "fact": "Белград — один из старейших городов Европы: более 7000 лет непрерывного заселения."},
    {"code": "SC", "flag": "🇸🇨", "name": "Сейшелы",             "capital": "Виктория",        "region": "Африка",        "desc": "115 гранитных и коралловых островов в Индийском океане. Праслен с пальмовым лесом Валле-де-Мэ.", "fact": "Сейшелы — единственная в мире страна, где растут пальмы коко-де-мер с самыми большими семенами."},
    {"code": "SL", "flag": "🇸🇱", "name": "Сьерра-Леоне",        "capital": "Фритаун",         "region": "Африка",        "desc": "«Лев-гора»: природные пляжи класса «лучшие в Африке» и восстанавливающаяся природа.", "fact": "Название «Сьерра-Леоне» означает «Лев-гора» — так португальцы назвали прибрежные горы."},
    {"code": "SG", "flag": "🇸🇬", "name": "Сингапур",            "capital": "Сингапур",        "region": "Азия",          "desc": "Город-государство: Marina Bay Sands, сады у залива и лучший аэропорт в мире.", "fact": "Сингапур входит в тройку самых дорогих городов мира и при этом имеет один из низших уровней преступности."},
    {"code": "SK", "flag": "🇸🇰", "name": "Словакия",            "capital": "Братислава",      "region": "Европа",        "desc": "Татры, пещеры Словацкого карста и Братислава — столица в 60 км от Вены.", "fact": "В Словакии находится крупнейшая в Центральной Европе пещерная система."},
    {"code": "SI", "flag": "🇸🇮", "name": "Словения",            "capital": "Любляна",         "region": "Европа",        "desc": "Озеро Блед с замком на скале — самая красивая открытка Европы. Пещеры Постойна.", "fact": "Словения — одна из самых лесистых стран Европы: 60% территории покрыто лесами."},
    {"code": "SB", "flag": "🇸🇧", "name": "Соломоновы острова",  "capital": "Хониара",         "region": "Океания",       "desc": "Место жестоких боёв Второй мировой и нетронутых рифов Тихого океана.", "fact": "На дне у Соломоновых островов покоятся десятки военных кораблей WWII — дайверский рай."},
    {"code": "SO", "flag": "🇸🇴", "name": "Сомали",              "capital": "Могадишо",        "region": "Африка",        "desc": "Самая длинная береговая линия в Африке (3025 км) и древние торговые связи с Аравией.", "fact": "У Сомали самая длинная береговая линия среди стран материковой Африки."},
    {"code": "ZA", "flag": "🇿🇦", "name": "ЮАР",                 "capital": "Претория",        "region": "Африка",        "desc": "Три столицы, Столовая гора, Крюгер-парк и путь Нельсона Манделы.", "fact": "ЮАР — единственная страна в мире, добровольно отказавшаяся от ядерного оружия."},
    {"code": "KR", "flag": "🇰🇷", "name": "Южная Корея",         "capital": "Сеул",            "region": "Азия",          "desc": "Родина K-pop, самсунга, кимчи и технологического чуда. Сеул — один из самых технологичных городов планеты.", "fact": "Южная Корея имеет самую высокую скорость интернета в мире."},
    {"code": "SS", "flag": "🇸🇸", "name": "Южный Судан",         "capital": "Джуба",           "region": "Африка",        "desc": "Самая молодая страна мира (2011 год). Национальный парк Бома — второй в мире по числу мигрирующих животных.", "fact": "Южный Судан — самое молодое независимое государство в мире (провозглашено в 2011 году)."},
    {"code": "ES", "flag": "🇪🇸", "name": "Испания",             "capital": "Мадрид",          "region": "Европа",        "desc": "Фламенко, Гауди, Сиеста, Камино де Сантьяго и лучшая хамон в мире.", "fact": "Испания — третья по посещаемости страна в мире после Франции и США."},
    {"code": "LK", "flag": "🇱🇰", "name": "Шри-Ланка",           "capital": "Коломбо",         "region": "Азия",          "desc": "«Жемчужина Индийского океана»: чайные плантации, слоны, руины Сигирии и серфинг.", "fact": "На Шри-Ланке находится самый большой в мире чайный аукцион."},
    {"code": "SD", "flag": "🇸🇩", "name": "Судан",               "capital": "Хартум",          "region": "Африка",        "desc": "Больше пирамид, чем в Египте: Мероэ, Нубийские пирамиды и Нил у слияния Белого и Голубого.", "fact": "В Судане больше пирамид, чем в Египте: около 200 нубийских пирамид."},
    {"code": "SR", "flag": "🇸🇷", "name": "Суринам",             "capital": "Парамарибо",      "region": "Южная Америка", "desc": "Самая маленькая страна Южной Америки с нетронутой Амазонией и креольской культурой.", "fact": "Суринам — самая малонаселённая страна Южной Америки."},
    {"code": "SE", "flag": "🇸🇪", "name": "Швеция",              "capital": "Стокгольм",       "region": "Европа",        "desc": "ABBA, IKEA, Вольво, Нобелевская премия и архипелаг из 30 000 островов у Стокгольма.", "fact": "Швеция — родина Нобелевской премии, учреждённой Альфредом Нобелем в 1895 году."},
    {"code": "CH", "flag": "🇨🇭", "name": "Швейцария",           "capital": "Берн",            "region": "Европа",        "desc": "Альпы, шоколад, часы и Женевское озеро. Страна с 4 государственными языками и вечным нейтралитетом.", "fact": "Швейцария сохраняет нейтралитет с 1815 года и не участвовала ни в одной войне."},
    {"code": "SY", "flag": "🇸🇾", "name": "Сирия",               "capital": "Дамаск",          "region": "Азия",          "desc": "Дамаск — один из древнейших непрерывно обитаемых городов мира. Пальмира и крепость крестоносцев Крак де Шевалье.", "fact": "Дамаск — один из старейших непрерывно населённых городов мира, обитаемый уже около 11 000 лет."},
    {"code": "TW", "flag": "🇹🇼", "name": "Тайвань",             "capital": "Тайбэй",          "region": "Азия",          "desc": "Тайваньская кухня, пузырьковый чай, горячие источники и ночные рынки.", "fact": "Тайвань — крупнейший производитель полупроводниковых чипов в мире."},
    {"code": "TJ", "flag": "🇹🇯", "name": "Таджикистан",         "capital": "Душанбе",         "region": "Азия",          "desc": "93% территории — горы. Памир — «Крыша мира» с озёрами, ледниками и Шёлковым путём.", "fact": "Таджикистан — самая горная страна в мире: 93% территории занимают горы."},
    {"code": "TZ", "flag": "🇹🇿", "name": "Танзания",            "capital": "Додома",          "region": "Африка",        "desc": "Килиманджаро, Серенгети, Занзибар и остров Мафия — главные сокровища Восточной Африки.", "fact": "Килиманджаро в Танзании — самый высокий отдельно стоящий вулкан и высочайшая точка Африки."},
    {"code": "TH", "flag": "🇹🇭", "name": "Таиланд",             "capital": "Бангкок",         "region": "Азия",          "desc": "Храмы Чиангмая, пляжи Краби, тук-туки и самый вкусный стрит-фуд в мире.", "fact": "В Таиланде запрещено наступать на деньги — это оскорбление короля, чьё изображение на них."},
    {"code": "TL", "flag": "🇹🇱", "name": "Восточный Тимор",     "capital": "Дили",            "region": "Азия",          "desc": "Одна из молодейших стран мира (2002). Нетронутые рифы и традиционные ткани тайс.", "fact": "Восточный Тимор обрёл независимость в 2002 году — одно из новейших государств мира."},
    {"code": "TG", "flag": "🇹🇬", "name": "Того",                "capital": "Ломе",            "region": "Африка",        "desc": "Рынок Аконде — один из крупнейших вуду-рынков в мире. Водопады Кпалиме и саванны севера.", "fact": "В Ломе (Того) находится крупнейший в мире рынок фетишей для ритуалов вуду."},
    {"code": "TO", "flag": "🇹🇴", "name": "Тонга",               "capital": "Нукуалофа",       "region": "Океания",       "desc": "Единственная полинезийская монархия. Плавание с горбатыми китами и флагам.", "fact": "Тонга — единственная монархия в Океании, существующая с незапамятных времён."},
    {"code": "TT", "flag": "🇹🇹", "name": "Тринидад и Тобаго",   "capital": "Порт-оф-Спейн",  "region": "Карибы",        "desc": "Родина стил-пэна, карнавала Тринидада и нефтяного «пузыря» природного асфальта.", "fact": "Тринидадский карнавал — один из самых известных в мире, вдохновивший Рио-де-Жанейро."},
    {"code": "TN", "flag": "🇹🇳", "name": "Тунис",               "capital": "Тунис",           "region": "Африка",        "desc": "Карфаген, Сахара, Матмата — пещерные дома из «Звёздных войн» и средиземноморские курорты.", "fact": "В Тунисе снимались сцены на планете Татуин из «Звёздных войн»."},
    {"code": "TR", "flag": "🇹🇷", "name": "Турция",              "capital": "Анкара",          "region": "Азия/Европа",   "desc": "Каппадокия, воздушные шары, Памуккале, Стамбул на двух континентах и турецкий завтрак.", "fact": "Стамбул — единственный в мире город, расположенный на двух континентах одновременно."},
    {"code": "TM", "flag": "🇹🇲", "name": "Туркменистан",        "capital": "Ашхабад",         "region": "Азия",          "desc": "Ворота Ада — горящий газовый кратер в пустыне. Город белого мрамора Ашхабад.", "fact": "В Туркменистане горит «Врата ада» — газовый кратер, пылающий с 1971 года."},
    {"code": "TV", "flag": "🇹🇻", "name": "Тувалу",              "capital": "Фунафути",        "region": "Океания",       "desc": "Четвёртое по площади государство в мире. Атоллы уходят под воду из-за потепления.", "fact": "Тувалу зарабатывает значительные деньги на продаже доменного имени .tv."},
    {"code": "UG", "flag": "🇺🇬", "name": "Уганда",              "capital": "Кампала",         "region": "Африка",        "desc": "«Жемчужина Африки»: гориллы Бвинди, исток Нила и озеро Виктория.", "fact": "Уганда — одна из немногих стран мира, где можно наблюдать горных горилл в дикой природе."},
    {"code": "UA", "flag": "🇺🇦", "name": "Украина",             "capital": "Киев",            "region": "Европа",        "desc": "Карпаты, Одесса, Киево-Печерская лавра и крупнейшая страна целиком в Европе.", "fact": "Украина — крупнейшая страна, целиком расположенная в Европе."},
    {"code": "AE", "flag": "🇦🇪", "name": "ОАЭ",                 "capital": "Абу-Даби",        "region": "Азия",          "desc": "Дубай — город рекордов: Бурдж-Халифа, самый большой ТЦ и остров Пальма.", "fact": "Бурдж-Халифа в ОАЭ — самое высокое здание в мире (828 м)."},
    {"code": "GB", "flag": "🇬🇧", "name": "Великобритания",      "capital": "Лондон",          "region": "Европа",        "desc": "Биг-Бен, Тауэрский мост, Шекспир, Битлз и Стоунхендж. Колыбель промышленной революции.", "fact": "В Лондоне метро открылось в 1863 году — это старейший метрополитен в мире."},
    {"code": "US", "flag": "🇺🇸", "name": "США",                 "capital": "Вашингтон",       "region": "Северная Америка", "desc": "Гранд-Каньон, Нью-Йорк, Йеллоустоун и Голливуд. Страна, определившая XX век.", "fact": "США — третья по площади и третья по численности населения страна мира."},
    {"code": "UY", "flag": "🇺🇾", "name": "Уругвай",             "capital": "Монтевидео",      "region": "Южная Америка", "desc": "Колония-дель-Сакраменто, пляжи Пунта-дель-Эсте и самый прогрессивный закон о конопле.", "fact": "Уругвай — первая в мире страна, полностью легализовавшая марихуану (2013 год)."},
    {"code": "UZ", "flag": "🇺🇿", "name": "Узбекистан",          "capital": "Ташкент",         "region": "Азия",          "desc": "Самарканд, Бухара, Хива — живой Шёлковый путь с куполами мечетей и медресе.", "fact": "Регистан в Самарканде — самая красивая площадь в мире по мнению многих путешественников."},
    {"code": "VU", "flag": "🇻🇺", "name": "Вануату",             "capital": "Порт-Вила",       "region": "Океания",       "desc": "80 островов с действующими вулканами, кастомной культурой и прыжками с лиан (land diving).", "fact": "Бунги-джампинг произошёл от ритуала прыжков с башни на Вануату."},
    {"code": "VA", "flag": "🇻🇦", "name": "Ватикан",             "capital": "Ватикан",         "region": "Европа",        "desc": "Наименьшее государство мира: Площадь Святого Петра, Сикстинская капелла и музеи Ватикана.", "fact": "Ватикан — самое маленькое государство в мире по площади (0,44 км²)."},
    {"code": "VE", "flag": "🇻🇪", "name": "Венесуэла",           "capital": "Каракас",         "region": "Южная Америка", "desc": "Водопад Анхель — самый высокий в мире (979 м). Тепуи — столовые горы и потерянный мир.", "fact": "Водопад Анхель в Венесуэле — самый высокий незапруженный водопад в мире (979 м)."},
    {"code": "VN", "flag": "🇻🇳", "name": "Вьетнам",             "capital": "Ханой",           "region": "Азия",          "desc": "Бухта Халонг, Хойан, уличная еда Ханоя, Хошимин и рисовые террасы Сапа.", "fact": "Вьетнам — второй в мире экспортёр кофе после Бразилии."},
    {"code": "YE", "flag": "🇾🇪", "name": "Йемен",               "capital": "Сана",            "region": "Азия",          "desc": "Сана — город из сказок «1001 ночи», остров Сокотра с драконовыми деревьями.", "fact": "Остров Сокотра у берегов Йемена называют «Галапагосами Индийского океана» за уникальную флору."},
    {"code": "ZM", "flag": "🇿🇲", "name": "Замбия",              "capital": "Лусака",          "region": "Африка",        "desc": "Водопад Виктория — «Дым который гремит». Долина Луангва — одно из лучших сафари Африки.", "fact": "Водопад Виктория на границе Замбии и Зимбабве — крупнейший в мире по площади водной завесы."},
    {"code": "ZW", "flag": "🇿🇼", "name": "Зимбабве",            "capital": "Хараре",          "region": "Африка",        "desc": "Руины Большого Зимбабве, водопад Виктория, Национальный парк Хванге со слонами.", "fact": "Зимбабве в 2008 году выпустила банкноту в 100 триллионов долларов из-за гиперинфляции."},
    {"code": "MK", "flag": "🇲🇰", "name": "Северная Македония",  "capital": "Скопье",          "region": "Европа",        "desc": "Охридское озеро — «Балканский Иерусалим» с более чем 365 церквями, каньон Матка и родина Матери Терезы.", "fact": "Охрид входит в список Всемирного наследия ЮНЕСКО как один из древнейших человеческих поселений в Европе."},
    # ── 6 особых территорий (не члены ООН, но важны для путешественников: отдельные визы, штампы) ──
    {"code": "HK", "flag": "🇭🇰", "name": "Гонконг",              "capital": "Гонконг",         "region": "Азия",          "desc": "Специальный административный район Китая: небоскрёбы Виктория-Харбор, дим-сам и Пик Виктория.", "fact": "Гонконг имеет одну из самых высоких концентраций небоскрёбов в мире — более 9000 высотных зданий."},
    {"code": "MO", "flag": "🇲🇴", "name": "Макао",                "capital": "Макао",           "region": "Азия",          "desc": "Специальный административный район Китая: казино, португальское наследие и руины собора Святого Павла.", "fact": "Макао — крупнейший игорный центр мира, с оборотом казино в несколько раз больше Лас-Вегаса."},
    {"code": "XK", "flag": "🇽🇰", "name": "Косово",               "capital": "Приштина",        "region": "Европа",        "desc": "Частично признанное государство на Балканах: монастыри ЮНЕСКО, водопад Мирушка и каньон Руговы.", "fact": "Косово — одна из самых молодых стран Европы, провозгласило независимость в 2008 году."},
    {"code": "PMR", "flag": "🏴", "name": "Приднестровье",         "capital": "Тирасполь",       "region": "Европа",        "desc": "Непризнанное государство между Молдовой и Украиной: советская эстетика, крепость Бендеры и собственная валюта.", "fact": "Приднестровье — единственное место в мире, где на государственном гербе сохранились серп и молот."},
    {"code": "EH", "flag": "🇪🇭", "name": "Западная Сахара",      "capital": "Эль-Аюн",         "region": "Африка",        "desc": "Спорная территория на северо-западе Африки: пустыня Сахара, оазисы и кочевые племена сахрави.", "fact": "Западная Сахара — одна из самых малонаселённых территорий в мире, плотность около 2 человек на км²."},
]


def _get_country_of_day() -> dict:
    """Возвращает страну дня — одинакова для всех пользователей в течение суток."""
    day_of_year = datetime.now(MOSCOW_TZ).timetuple().tm_yday
    return _COUNTRIES_OF_DAY[day_of_year % len(_COUNTRIES_OF_DAY)]


def _add_flag_to_collection(user_id: int, country_code: str) -> bool:
    """Добавляет флаг в коллекцию. Возвращает True если флаг новый."""
    today = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")
    try:
        if _db_backend == "postgres":
            with _db_lock:
                with _db_conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO user_flags (user_id, country_code, collected_date)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (user_id, country_code) DO NOTHING
                    """, (user_id, country_code, today))
                    return cur.rowcount > 0
        elif _db_backend == "sqlite":
            with _db_lock:
                cur = _db_conn.execute("""
                    INSERT OR IGNORE INTO user_flags (user_id, country_code, collected_date)
                    VALUES (?, ?, ?)
                """, (user_id, country_code, today))
                _db_conn.commit()
                return cur.rowcount > 0
    except Exception as e:
        logger.error("_add_flag_to_collection error: %s", e)
    return False


def _get_flag_count(user_id: int) -> int:
    """Возвращает количество флагов пользователя."""
    try:
        if _db_backend == "postgres":
            with _db_lock:
                with _db_conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM user_flags WHERE user_id = %s", (user_id,))
                    return cur.fetchone()[0]
        elif _db_backend == "sqlite":
            with _db_lock:
                return _db_conn.execute(
                    "SELECT COUNT(*) FROM user_flags WHERE user_id = ?", (user_id,)
                ).fetchone()[0]
    except Exception as e:
        logger.error("_get_flag_count error: %s", e)
    return 0


def _get_user_flags(user_id: int) -> list[str]:
    """Возвращает список кодов стран в коллекции пользователя."""
    try:
        if _db_backend == "postgres":
            with _db_lock:
                with _db_conn.cursor() as cur:
                    cur.execute(
                        "SELECT country_code FROM user_flags WHERE user_id = %s ORDER BY collected_date",
                        (user_id,)
                    )
                    return [r[0] for r in cur.fetchall()]
        elif _db_backend == "sqlite":
            with _db_lock:
                rows = _db_conn.execute(
                    "SELECT country_code FROM user_flags WHERE user_id = ? ORDER BY collected_date",
                    (user_id,)
                ).fetchall()
                return [r[0] for r in rows]
    except Exception as e:
        logger.error("_get_user_flags error: %s", e)
    return []


def _get_flag_top() -> list[tuple]:
    """Возвращает топ-10 коллекционеров: (first_name, count)."""
    try:
        if _db_backend == "postgres":
            with _db_lock:
                with _db_conn.cursor() as cur:
                    cur.execute("""
                        SELECT u.first_name, COUNT(f.country_code) AS cnt
                        FROM user_flags f
                        JOIN users u ON u.user_id = f.user_id
                        GROUP BY u.user_id, u.first_name
                        ORDER BY cnt DESC
                        LIMIT 10
                    """)
                    return cur.fetchall()
        elif _db_backend == "sqlite":
            with _db_lock:
                return _db_conn.execute("""
                    SELECT u.first_name, COUNT(f.country_code) AS cnt
                    FROM user_flags f
                    JOIN users u ON u.user_id = f.user_id
                    GROUP BY u.user_id, u.first_name
                    ORDER BY cnt DESC
                    LIMIT 10
                """).fetchall()
    except Exception as e:
        logger.error("_get_flag_top error: %s", e)
    return []


def _cod_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["🏆 Моя коллекция"],
            ["◀️ Назад", HOME_BTN],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def country_of_day_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    country = _get_country_of_day()
    user_id = update.effective_user.id
    is_new  = _add_flag_to_collection(user_id, country["code"])
    count   = _get_flag_count(user_id)
    flag_line = "✅ Флаг добавлен в твою коллекцию!" if is_new else "📌 Ты уже получил этот флаг сегодня."
    text = (
        f"🌍 *Страна дня — {country['name']}*\n\n"
        f"{country['flag']} {country['name']}\n"
        f"🏙 Столица: {country['capital']}\n"
        f"🌐 Регион: {country['region']}\n\n"
        f"📖 {country['desc']}\n\n"
        f"💡 Интересный факт: {country['fact']}\n\n"
        f"{flag_line}\n"
        f"🏳 Собрано флагов: *{count}* из 201\n\n"
        f"Возвращайся завтра за новым флагом! 🎒"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=_cod_kb())
    return COUNTRY_OF_DAY


async def country_of_day_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == HOME_BTN:
        return await go_home(update, context)
    if text == "◀️ Назад":
        return await show_games_menu(update, context)
    if text == "🏆 Моя коллекция":
        user_id = update.effective_user.id
        codes   = _get_user_flags(user_id)
        count   = len(codes)
        code_set = {c["code"] for c in _COUNTRIES_OF_DAY}
        flags_line = " ".join(
            c["flag"] for c in _COUNTRIES_OF_DAY if c["code"] in set(codes)
        ) or "—"
        top = _get_flag_top()
        top_lines = "\n".join(
            f"{i+1}. {name or 'Путешественник'} — {cnt} 🏳"
            for i, (name, cnt) in enumerate(top)
        ) or "Пока никто не собрал флаги."
        msg = (
            f"🏆 *Твоя коллекция флагов*\n\n"
            f"Собрано: *{count}* из 201\n\n"
            f"{flags_line}\n\n"
            f"📊 *Топ коллекционеров:*\n{top_lines}"
        )
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=_cod_kb())
        return COUNTRY_OF_DAY
    return await country_of_day_start(update, context)


def _games_kb():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🧠 Викторина о путешествиях")],
            [KeyboardButton("🎯 Угадай где я?")],
            [KeyboardButton("🤝 Найди пару")],
            [KeyboardButton("🌍 Страна дня")],
            [KeyboardButton("◀️ Назад"), KeyboardButton(HOME_BTN)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def show_games_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подменю 🎮 Игры."""
    await update.message.reply_text(
        "🎮 *Игры*\n\nВыбери игру:",
        parse_mode="Markdown",
        reply_markup=_games_kb(),
    )
    return GAMES_MENU


async def games_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "◀️ Назад":
        return await go_home(update, context)
    if text == "🧠 Викторина о путешествиях":
        return await quiz_start(update, context)
    if text == "🎯 Угадай где я?":
        return await guess_start(update, context)
    if text == "🤝 Найди пару":
        return await pair_start(update, context)
    if text == "🌍 Страна дня":
        return await country_of_day_start(update, context)
    return await show_games_menu(update, context)


async def show_folder_tools(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вернуться в папку 🛠 Инструменты."""
    context.user_data.clear()
    await update.message.reply_text(
        "🛠 *Инструменты*\n\nВыбери раздел:",
        parse_mode="Markdown",
        reply_markup=get_folder_tools_kb(),
    )
    return MAIN_MENU


HELP_TOPICS = {
    "✈️ Что делать в аэропорту": (
        "✈️ *Что делать в аэропорту*\n\n"
        "1. Приезжай за 2–3 часа до вылета (3 часа — международные рейсы).\n"
        "2. Найди стойку регистрации своей авиакомпании по табло DEPARTURES.\n"
        "3. Сдай багаж и получи посадочный талон (boarding pass).\n"
        "4. Досмотр: сними ремень и куртку, достань ноутбук и жидкости в пакете 1л.\n"
        "5. Найди свой гейт (Gate) заранее — он указан на посадочном.\n"
        "6. Следи за табло — гейт может измениться в последний момент.\n"
        "7. В дьюти-фри лимиты: 1л алкоголя, 200 сигарет — при въезде в большинство стран.\n"
        "8. Зарядись и скачай офлайн-карты пока есть Wi-Fi в аэропорту.\n\n"
        "💡 *Совет:* Скачай приложение авиакомпании — push-уведомления о гейте придут быстрее табло."
    ),
    "🛃 Паспортный контроль и таможня": (
        "🛃 *Паспортный контроль и таможня*\n\n"
        "*Паспортный контроль:*\n"
        "— На выезде из России: отдай загранпаспорт, получи штамп, иди на посадку.\n"
        "— На въезде: стань в очередь Foreigners / All Passports.\n"
        "— Цель поездки — Tourism, срок — сколько дней, где живёшь — отель.\n"
        "— Держи под рукой бронь отеля и обратный билет.\n"
        "— Говори уверенно и кратко — пограничники реагируют на нервозность.\n\n"
        "*Таможня:*\n"
        "— Декларируй наличные свыше $10 000 (или эквивалент) — обязательно везде.\n"
        "— Алкоголь и табак сверх нормы страны назначения — тоже декларируй.\n"
        "— Не вози мясо, молоко и яйца — во многих странах запрещено на ввоз.\n"
        "— Рецептурные лекарства (особенно с кодеином) — возьми справку от врача.\n"
        "— Вейпы и электронные сигареты запрещены в Таиланде, ОАЭ, Сингапуре.\n\n"
        "💡 *Совет:* ОАЭ, Сингапур и Япония — самые строгие таможни. Изучи правила заранее."
    ),
    "🚕 Как найти такси/транспорт": (
        "🚕 *Как найти такси и транспорт*\n\n"
        "В аэропорту:\n"
        "— Не соглашайся с «частниками», которые сами подходят — это дорого и рискованно.\n"
        "— Ищи официальные стойки такси (Taxi / Official Taxi) в зоне прилёта.\n"
        "— Лучший вариант — приложения: Uber, Bolt, Grab (Азия), inDrive, Яндекс Go.\n"
        "— Всегда договаривайся о цене или включай счётчик ДО посадки.\n\n"
        "Общественный транспорт:\n"
        "— Автобус или метро до центра обычно в 5–10 раз дешевле такси.\n"
        "— Маршрут и расписание — Google Maps, работает офлайн.\n"
        "— Транспортная карта (Oyster, Navigo, Octopus) выгоднее разовых билетов.\n\n"
        "Аренда авто:\n"
        "— Нужны права + международное водительское удостоверение (МВУ) для ряда стран.\n"
        "— Проверяй страховку: CDW и Third Party обязательны.\n\n"
        "💡 *Совет:* Скачай Google Maps офлайн-карту ещё дома — без интернета покажет маршрут."
    ),
    "🪪 Что такое e-visa и как оформить": (
        "🪪 *Что такое e-visa*\n\n"
        "E-visa — электронная виза, получаешь онлайн без похода в консульство.\n\n"
        "Как оформить:\n"
        "1. Найди официальный сайт страны (ищи «e-visa + название страны»).\n"
        "2. Заполни анкету: ФИО латиницей, данные паспорта, цель визита.\n"
        "3. Загрузи фото (обычно 3.5×4.5 см, белый фон) и скан паспорта.\n"
        "4. Оплати сбор картой — обычно $20–60.\n"
        "5. Жди одобрения на email — от нескольких часов до 7 дней.\n"
        "6. Распечатай или сохрани на телефоне — покажешь на границе.\n\n"
        "Страны с e-visa для россиян:\n"
        "— Индия, Египет, Иордания, Шри-Ланка, Вьетнам (бесплатно), Оман, Катар.\n\n"
        "Осторожно:\n"
        "— Пользуйся ТОЛЬКО официальными сайтами — есть мошеннические копии.\n"
        "— Посредники берут $50–200 за то, что ты сделаешь сам за 20 минут.\n\n"
        "💡 *Совет:* Оформляй за 2–3 недели до поездки — бывают задержки одобрения."
    ),
    "🎟 Как найти дешёвые авиабилеты": (
        "🎟 *Как найти дешёвые авиабилеты*\n\n"
        "Где искать:\n"
        "— Aviasales, Skyscanner, Google Flights — сравнивают сразу все авиакомпании.\n"
        "— Сайт самой авиакомпании — иногда там дешевле, без комиссии агрегатора.\n\n"
        "Когда покупать:\n"
        "— За 1.5–3 месяца до вылета — обычно оптимальная цена.\n"
        "— Вторник и среда — часто самые дешёвые дни для перелёта.\n"
        "— Ранние утренние и поздние ночные рейсы дешевле дневных.\n\n"
        "Лайфхаки:\n"
        "— Режим инкогнито в браузере — цены не растут от повторных поисков.\n"
        "— Гибкие даты ±3 дня могут сэкономить 30–50% стоимости.\n"
        "— Иногда два отдельных билета (туда и обратно разными а/к) дешевле стыковочного.\n"
        "— Подпишись на уведомления о снижении цены в Aviasales.\n\n"
        "💡 *Совет:* Аэропорты-хабы (Стамбул, Дубай, Доха) дают самые дешёвые стыковки."
    ),
    "🏨 Как выбрать жильё (отель, хостел, Airbnb)": (
        "🏨 *Как выбрать жильё*\n\n"
        "Отель:\n"
        "— Надёжно, есть ресепшн и сервис круглосуточно.\n"
        "— Booking.com — читай отзывы, смотри дату последнего и оценку «Расположение».\n"
        "— Бронируй с бесплатной отменой — можно изменить планы без штрафа.\n\n"
        "Хостел:\n"
        "— Дешевле в 2–5 раз, хорошо для одиночных путешественников.\n"
        "— Общие комнаты на 4–12 человек, есть и приватные комнаты.\n"
        "— Hostelworld — лучший сайт для поиска, читай отзывы о чистоте.\n\n"
        "Airbnb / апартаменты:\n"
        "— Своя кухня, больше пространства, атмосфера «как дома».\n"
        "— Читай правила отмены ДО оплаты — невозвратные броней много.\n"
        "— Superhost-статус хозяина — признак надёжности.\n\n"
        "💡 *Совет:* Первую ночь бронируй заранее — искать жильё с багажом после перелёта тяжело."
    ),
    "🛡 Страховка — зачем и как оформить": (
        "🛡 *Страховка для путешествий*\n\n"
        "Зачем нужна:\n"
        "— Лечение за рубежом стоит дорого: $100–500 за один визит к врачу.\n"
        "— Страховка покрывает больницу, скорую, эвакуацию домой при болезни.\n"
        "— Для Шенгена, США и ряда других стран — обязательное условие визы.\n\n"
        "Где оформить:\n"
        "— Cherehapa.ru, Сравни.ру — сравнивают цены всех страховщиков сразу.\n"
        "— Стоимость: от 300–500 ₽ в неделю для популярных направлений.\n\n"
        "На что смотреть:\n"
        "— Страховая сумма: минимум $50 000, для США — от $100 000.\n"
        "— Покрытие активного отдыха — если планируешь трекинг, дайвинг, лыжи.\n"
        "— Франшиза $0 — не придётся платить из своего кармана при обращении.\n"
        "— Покрытие отмены поездки — если оформить сразу после покупки билетов.\n\n"
        "💡 *Совет:* Сохрани номер горячей линии страховой в телефоне — при страховом случае звони сразу."
    ),
    "💳 Деньги — карты, наличные, обмен валюты": (
        "💳 *Деньги в поездке*\n\n"
        "Карты:\n"
        "— Российские Visa/MC не работают в большинстве стран из-за санкций.\n"
        "— Используй карты UnionPay (Газпромбанк, Россельхозбанк) — принимают шире.\n"
        "— Карта Мир работает в Турции, Армении, Беларуси, Казахстане, ОАЭ.\n"
        "— Сообщи банку о поездке заранее — иначе могут заблокировать операции.\n\n"
        "Наличные:\n"
        "— Всегда имей запас наличными — рынки, такси, мелкие расходы.\n"
        "— Снимай в банкоматах местных банков — курс лучше, чем в обменниках.\n"
        "— Не соглашайся на «конвертацию в рублях» в зарубежном банкомате.\n\n"
        "Обмен:\n"
        "— Не меняй в аэропорту — курс хуже на 10–20%.\n"
        "— Лучший курс — в банках или сертифицированных обменниках в городе.\n\n"
        "💡 *Совет:* Раздели деньги: часть в кошельке, часть в отеле, немного в запасном кармане."
    ),
    "📱 Симкарта за рубежом": (
        "📱 *Симкарта за рубежом — как не остаться без связи*\n\n"
        "Варианты:\n"
        "— *Роуминг* — удобно, но дорого. Подключи пакет у оператора заранее.\n"
        "— *Местная симкарта* — купи в аэропорту по прилёту. Дёшево, быстрый интернет.\n"
        "— *eSIM* — виртуальная симка без физической карты. Airalo, Holafly, Yesim.\n\n"
        "Как выбрать:\n"
        "— eSIM — лучший вариант: покупаешь онлайн, активируешь за 5 минут дома.\n"
        "— Местная симка — если телефон залочен или не поддерживает eSIM.\n"
        "— Роуминг — только если едешь на 1–2 дня и трафик нужен минимально.\n\n"
        "Подготовься до отъезда:\n"
        "— Скачай офлайн-карты Google Maps или Maps.me.\n"
        "— Сохрани важные адреса, номера телефонов, маршруты.\n"
        "— Проверь: поддерживает ли твой телефон eSIM (большинство с 2020 года).\n\n"
        "💡 *Совет:* Карта Airalo на 7 дней для Таиланда — около $5. Роуминг МТС — от $10/день."
    ),
    "🔒 Безопасность в путешествии": (
        "🔒 *Безопасность в путешествии*\n\n"
        "До поездки:\n"
        "— Сделай копии всех документов — храни отдельно от оригиналов и в облаке.\n"
        "— Запиши номера: страховой горячей линии, посольства России, банков.\n"
        "— Не публикуй маршрут и даты отъезда в соцсетях заранее.\n\n"
        "На месте:\n"
        "— Не носи все деньги в одном месте — часть оставляй в отеле/сейфе.\n"
        "— Сумка спереди, рюкзак застёгнут — особенно в толпе и транспорте.\n"
        "— Не подключайся к незащищённому Wi-Fi без VPN.\n"
        "— Используй сейф в номере для паспорта и крупных сумм.\n\n"
        "Опасные ситуации:\n"
        "— Отвлекающие манёвры (что-то пролили, просят помочь) — классика карманников.\n"
        "— Нелегальное такси — никогда не садись.\n"
        "— В незнакомом районе ночью — придерживайся освещённых улиц.\n\n"
        "💡 *Совет:* Приложение «Зарубежный помощник» МИД РФ — экстренные контакты посольств."
    ),
    "🆘 Что делать если потерял паспорт": (
        "🆘 *Потерял паспорт за рубежом — что делать*\n\n"
        "Шаг 1: Не паникуй — это решаемо.\n\n"
        "Шаг 2: Обратись в местную полицию.\n"
        "— Получи справку об утере (Police Report / Loss Report).\n"
        "— Без неё не выдадут документы и не выплатит страховая.\n\n"
        "Шаг 3: Свяжись с посольством или консульством России.\n"
        "— Контакты: сайт МИД РФ или приложение «Зарубежный помощник».\n"
        "— Выдадут Свидетельство на возвращение (СНВ) — временный документ для отлёта.\n\n"
        "Шаг 4: Уведоми страховую компанию.\n"
        "— Большинство полисов покрывают расходы на оформление документов.\n\n"
        "Шаг 5: Сообщи в отель — помогут с переводом, транспортом, связью.\n\n"
        "Шаг 6: Заблокируй банковские карты если они тоже пропали.\n\n"
        "💡 *Совет:* Заранее отправь скан паспорта себе на email — ускорит восстановление в разы."
    ),
    "💊 Аптечка путешественника": (
        "💊 *Аптечка путешественника*\n\n"
        "Базовый набор:\n"
        "— Ибупрофен или парацетамол — обезболивающее и жаропонижающее.\n"
        "— Лоперамид (имодиум) — от диареи, первая помощь при расстройстве.\n"
        "— Регидрон — восстановление водно-солевого баланса при отравлении.\n"
        "— Цетиризин или лоратадин — антигистаминное, от аллергии и укусов.\n"
        "— Пластырь (разных размеров) и стерильные салфетки.\n"
        "— Хлоргексидин или спиртовые салфетки — антисептик.\n\n"
        "По направлению:\n"
        "— Азия/Африка/Латинская Америка: таблетки от малярии (по назначению врача).\n"
        "— Горы (от 2500 м): ацетазоламид от горной болезни — нужен рецепт.\n"
        "— Жаркие страны: крем SPF 50+ и репеллент с DEET от насекомых.\n\n"
        "Важно:\n"
        "— Личные лекарства — с запасом на 3–5 дней сверх поездки.\n"
        "— Рецептурные препараты — в оригинальной упаковке с рецептом врача.\n\n"
        "💡 *Совет:* Сфотографируй упаковки своих лекарств — по фото легче найти аналог за рубежом."
    ),
    "📱 Приложения для путешественников — топ-10": (
        "📱 *Топ-10 приложений для путешественника*\n\n"
        "Навигация:\n"
        "— *Google Maps* — скачай офлайн-карту нужного города дома, до отъезда.\n"
        "— *Maps.me* — детальные офлайн-карты, работает в горах и без сети.\n\n"
        "Транспорт и жильё:\n"
        "— *Aviasales* — поиск дешёвых авиабилетов, уведомления о снижении цен.\n"
        "— *Booking.com* — отели с бесплатной отменой, огромная база отзывов.\n"
        "— *Airbnb* — апартаменты и нестандартное жильё по всему миру.\n\n"
        "Деньги и связь:\n"
        "— *Wise* — выгодный обмен валюты и международные переводы.\n"
        "— *XE Currency* — конвертер валют с офлайн-режимом.\n"
        "— *Airalo* — покупка eSIM для интернета в любой стране от $5.\n\n"
        "Разное:\n"
        "— *Google Translate* — переводчик с камерой: наводи на меню или знаки.\n"
        "— *TripIt* — автоматически собирает маршрут из писем на почте.\n\n"
        "💡 *Совет:* Скачай карты и переводы офлайн дома — в роуминге интернет дорогой или недоступен."
    ),
    "🕵️ Мошенники и что делать если обокрали": (
        "🕵️ *Мошенники и кражи в путешествии*\n\n"
        "*Классические схемы мошенников:*\n"
        "— «Что-то пролили» — пока помогают, второй чистит карманы.\n"
        "— «Бесплатный подарок» — браслет, цветок — потом агрессивно требуют деньги.\n"
        "— «Фото за деньги» — человек в костюме сам встаёт рядом, потом выставляет счёт.\n"
        "— «Закрыт главный вход» — «помощник» ведёт в магазин друга вместо музея.\n"
        "— «Такси без счётчика» — всегда договаривайся о цене ДО посадки.\n\n"
        "*Как защититься:*\n"
        "— Сумку носи спереди, не доставай телефон в толпе без необходимости.\n"
        "— Чувствуешь давление — молча уходи. Вежливость здесь не обязательна.\n\n"
        "*Если всё же обокрали:*\n"
        "— Карту — заблокируй немедленно через приложение банка.\n"
        "— Телефон — войди на find.google.com или appleid.apple.com и заблокируй удалённо.\n"
        "— Обратись в полицию за справкой об утере — нужна для страховой и посольства.\n"
        "— Сообщи страховой в течение 24 часов — без справки из полиции не выплатят.\n\n"
        "💡 *Совет:* Запиши номер горячей линии банка в заметки — без телефона в приложение не войдёшь."
    ),
    "🚫 Запреты и табу в разных странах": (
        "🚫 *Запреты и табу в разных странах*\n\n"
        "Еда и напитки:\n"
        "— 🇸🇬 Сингапур: жвачка запрещена к ввозу, штраф за жевание в метро.\n"
        "— 🇦🇪 ОАЭ, 🇸🇦 Саудовская Аравия: алкоголь строго ограничен или запрещён.\n"
        "— 🇹🇭 Таиланд: нельзя есть и пить в буддийских храмах.\n\n"
        "Одежда:\n"
        "— Мечети везде: закрытые плечи и колени, женщинам — платок.\n"
        "— 🇮🇷 Иран: женщинам хиджаб обязателен на улице.\n"
        "— 🇮🇩 Бали: в храм без саронга не пускают — дают напрокат у входа.\n\n"
        "Поведение:\n"
        "— 🇯🇵 Япония: не говори громко в транспорте, не ешь на ходу.\n"
        "— 🇹🇭 Таиланд: не касайся головы другого человека — она священна.\n"
        "— 🇨🇳 Китай: не втыкай палочки вертикально в рис — символ похорон.\n\n"
        "Фото:\n"
        "— Нельзя фотографировать военные объекты, аэропорты, полицейских — везде.\n"
        "— 🇲🇾 Малайзия, 🇱🇰 Шри-Ланка: селфи спиной к статуе Будды — штраф.\n\n"
        "💡 *Совет:* Погугли «что нельзя делать в [страна]» — 10 минут сэкономят тебе штраф или скандал."
    ),
    "💰 Чаевые по странам": (
        "💰 *Чаевые по странам — где обязательно, где не принято*\n\n"
        "Обязательно:\n"
        "— 🇺🇸 США, 🇨🇦 Канада: 15–20% в ресторане, $1–2 бармену, $2–5 такси.\n"
        "— 🇲🇽 Мексика: 10–15% в кафе и ресторанах.\n"
        "— 🇪🇬 Египет, 🇲🇦 Марокко: чаевые ожидаются везде, включая любую помощь.\n\n"
        "Приветствуется:\n"
        "— 🇩🇪 Германия, 🇦🇹 Австрия: округли счёт вверх или оставь 5–10%.\n"
        "— 🇬🇧 Великобритания: 10–12.5% если не включено (проверь строку service charge).\n"
        "— 🇹🇷 Турция: 10% в ресторанах, мелочь экскурсоводам и горничным.\n"
        "— 🇹🇭 Таиланд: 20–50 бат в кафе — не обязательно, но приятно персоналу.\n\n"
        "Не принято:\n"
        "— 🇯🇵 Япония: чаевые — табу, могут вернуть со смущением.\n"
        "— 🇨🇳 Китай: в большинстве заведений не ожидаются.\n"
        "— 🇸🇬 Сингапур: сервисный сбор 10% уже в счёте — отдельно не нужно.\n"
        "— 🇦🇺 Австралия: не обязательно, официанты получают высокую зарплату.\n\n"
        "💡 *Совет:* Есть «service charge» или «gratuity» в счёте — чаевые уже включены, доплачивать не надо."
    ),
    # sentinel — opens baggage submenu (see BAGGAGE_SUBTOPICS below)
    "🧳 Багаж и ручная кладь": None,
}

## ── Baggage subtopics ────────────────────────────────────────────────────────

BAGGAGE_SUBTOPICS = {
    "📏 Размеры и вес": (
        "📏 *Размеры и вес багажа — актуально 2025–2026*\n\n"
        "*Ручная кладь (стандарт IATA):*\n"
        "— Размер: 55×40×20 см (с колёсами и ручками)\n"
        "— Вес: 8–10 кг у большинства авиакомпаний\n\n"
        "*Зарегистрированный багаж:*\n"
        "— Линейные размеры: до 158 см (сумма трёх сторон)\n"
        "— Вес: 20–23 кг (эконом), 32 кг (бизнес)\n\n"
        "*Таблица авиакомпаний:*\n"
        "✈️ *Аэрофлот* — ручная кладь 55×40×25 см / 10 кг, багаж 23 кг\n"
        "✈️ *S7 Airlines* — 55×40×20 см / 10 кг, багаж 23 кг\n"
        "✈️ *Победа* — 36×30×27 см / 10 кг бесплатно; большая — доплата\n"
        "✈️ *Turkish Airlines* — 55×40×23 см / 8 кг, багаж 20–23 кг\n"
        "✈️ *Emirates* — 55×38×20 см / нет лимита веса (до 7 кг рек.), багаж 25 кг\n"
        "✈️ *FlyDubai* — 55×38×20 см / 7 кг, багаж 20–23 кг (по тарифу)\n"
        "✈️ *Air Arabia* — 55×40×20 см / 10 кг, багаж 20–25 кг\n"
        "✈️ *Thai Airways* — 56×45×25 см / 7 кг, багаж 20–30 кг\n"
        "✈️ *Vietnam Airlines* — 56×36×23 см / 10 кг, багаж 23 кг\n\n"
        "⚠️ Лоукостеры часто меняют нормы — проверяй на сайте а/к перед вылетом.\n\n"
        "💡 *Совет:* Взвесь чемодан дома — на стойке каждый кг сверх нормы стоит €15–30."
    ),
    "✅ Что можно провозить": (
        "✅ *Что можно провозить — правила 2025–2026*\n\n"
        "*В ручной клади:*\n"
        "— Жидкости: до 100 мл в одном флаконе, всё в прозрачном пакете 1 л (zip-lock)\n"
        "— Электроника: ноутбук, планшет, телефон — достань при досмотре\n"
        "— Power bank: до 100 Вт·ч без ограничений; 100–160 Вт·ч — с разрешения а/к\n"
        "— Лекарства: с рецептом или справкой врача на языке страны\n"
        "— Детское питание и молоко для грудничков — в разумном количестве\n"
        "— Зонт, книги, одежда, небольшие сувениры\n\n"
        "*В зарегистрированном багаже:*\n"
        "— Жидкости любого объёма (шампунь, крем и пр.)\n"
        "— Острые предметы: ножи, ножницы, инструменты\n"
        "— Алкоголь: до 5 литров, крепостью не более 70° (140 proof)\n"
        "— Аэрозоли: до 500 мл/500 г на один баллончик, суммарно до 2 кг\n"
        "— Литий-ионные батареи: только в ручной клади — в багаж нельзя!\n\n"
        "💡 *Совет:* Сними скриншот правил своей а/к — на стойке не будет времени гуглить."
    ),
    "🚫 Что запрещено": (
        "🚫 *Что запрещено в самолёте — актуально 2025–2026*\n\n"
        "*Полностью под запретом (ни в ручной клади, ни в багаже):*\n"
        "— Взрывчатые вещества и имитации\n"
        "— Огнестрельное оружие без официального разрешения а/к и властей\n"
        "— Радиоактивные, легковоспламеняющиеся вещества\n"
        "— Хлор, кислоты, отбеливатель\n"
        "— Сухой лёд более 2.5 кг\n\n"
        "*В ручной клади запрещено:*\n"
        "— Ножи, кинжалы, многофункциональные инструменты с лезвием\n"
        "— Ножницы с лезвием длиннее 6 см\n"
        "— Жидкости в ёмкостях более 100 мл (даже наполовину пустые)\n"
        "— Зажигалки в ряде стран (проверь отдельно)\n"
        "— Power bank более 160 Вт·ч\n\n"
        "*Ограничения на ввоз по странам:*\n"
        "— 🇦🇺 Австралия: строгий запрет на продукты — мясо, яйца, свежие фрукты\n"
        "— 🇸🇬 Сингапур: жвачка, вейпы, электронные сигареты — штраф до $10 000\n"
        "— 🇹🇭 Таиланд: вейпы запрещены, конфискация и штраф\n"
        "— 🇦🇪 ОАЭ: определённые лекарства (кодеин) — только с рецептом\n"
        "— 🇺🇸 США: свежие растения и почва — запрещены\n\n"
        "💡 *Совет:* Забытый нож в ручной клади — конфискация. Перед упаковкой проверь карманы рюкзака."
    ),
    "💡 Лайфхаки": (
        "💡 *Лайфхаки про багаж — 2025–2026*\n\n"
        "*Как избежать доплаты за перевес:*\n"
        "— Взвесь дома. Норма — 23 кг? Ставь цель 21 кг — весы на стойке строже.\n"
        "— Наденей тяжёлые вещи (куртка, кроссовки) перед регистрацией.\n"
        "— Переложи в ручную кладь то, что влезет.\n\n"
        "*Как правильно упаковать чемодан:*\n"
        "— Тяжёлое (обувь, книги) — ближе к колёсам, лёгкое — сверху.\n"
        "— Одежду скручивай в рулоны — меньше морщин и больше места.\n"
        "— Обувь в пакеты — защита от грязи и запаха.\n\n"
        "*Если багаж потеряли:*\n"
        "— Сразу у стойки Lost & Found (до выхода из зоны прилёта) подай PIR-форму.\n"
        "— Срок поиска: 5–21 день. Если не нашли — компенсация по Монреальской конвенции.\n"
        "— Сохрани чек регистрации багажа и квитанции за вынужденные покупки.\n\n"
        "*Страховка багажа:*\n"
        "— Входит в большинство комплексных туристических страховок.\n"
        "— Покрывает утерю, повреждение, задержку свыше 6 часов.\n\n"
        "*Компенсация за задержку:*\n"
        "— Авиакомпания обязана выдать набор первой необходимости или деньги.\n"
        "— Храни чеки — по Монреальской конвенции возмещают до ~1600 USD.\n\n"
        "*Хрупкие вещи:*\n"
        "— Оберни пузырчатой плёнкой и помести в центр чемодана.\n"
        "— Наклей стикер «Fragile» — грузчики всё равно бросают, но чуть реже.\n\n"
        "*Замки и пломбы:*\n"
        "— Используй замки TSA (открываются досмотром без поломки).\n"
        "— Пластиковые стяжки — заметишь вскрытие сразу.\n\n"
        "💡 *Совет:* Сфотографируй содержимое чемодана перед сдачей — ускорит компенсацию."
    ),
}

_BAGGAGE_MENU_KB = ReplyKeyboardMarkup(
    [[btn] for btn in BAGGAGE_SUBTOPICS] + [["◀️ Назад", HOME_BTN]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

MYMEMORY_URL = "https://api.mymemory.translated.net/get?q={q}&langpair={lp}"

VISAS = {
    "✅ Без визы": (
        "✅ *Без визы для граждан России*\n\n"
        "🇦🇿 Азербайджан — 90 дней\n"
        "🇦🇱 Албания — 90 дней\n"
        "🇩🇿 Алжир — 90 дней\n"
        "🇦🇩 Андорра — 90 дней\n"
        "🇦🇬 Антигуа и Барбуда — 30 дней\n"
        "🇦🇷 Аргентина — 90 дней\n"
        "🇦🇲 Армения — без ограничений\n"
        "🇧🇸 Багамские острова — 30 дней\n"
        "🇧🇧 Барбадос — 90 дней\n"
        "🇧🇾 Беларусь — без ограничений\n"
        "🇧🇿 Белиз — 30 дней\n"
        "🇧🇴 Боливия — 90 дней\n"
        "🇧🇦 Босния и Герцеговина — 30 дней\n"
        "🇧🇼 Ботсвана — 90 дней\n"
        "🇧🇷 Бразилия — 90 дней\n"
        "🇧🇳 Бруней — 14 дней\n"
        "🇻🇺 Вануату — 30 дней\n"
        "🇻🇪 Венесуэла — 90 дней\n"
        "🇻🇳 Вьетнам — 45 дней\n"
        "🇬🇾 Гайана — 30 дней\n"
        "🇬🇹 Гватемала — 90 дней\n"
        "🇭🇰 Гонконг — 14 дней\n"
        "🇭🇳 Гондурас — 90 дней\n"
        "🇬🇩 Гренада — 30 дней\n"
        "🇬🇪 Грузия — 365 дней\n"
        "🇩🇴 Доминиканская Республика — 30 дней\n"
        "🇮🇱 Израиль — 90 дней\n"
        "🇮🇩 Индонезия — 30 дней\n"
        "🇰🇿 Казахстан — 30 дней\n"
        "🇨🇳 Китай — 15 дней\n"
        "🇨🇴 Колумбия — 90 дней\n"
        "🇨🇷 Коста-Рика — 90 дней\n"
        "🇨🇺 Куба — 30 дней\n"
        "🇰🇬 Кыргызстан — без ограничений\n"
        "🇲🇴 Макао — 30 дней\n"
        "🇲🇺 Маврикий — 60 дней\n"
        "🇲🇾 Малайзия — 30 дней\n"
        "🇲🇻 Мальдивы — 30 дней\n"
        "🇲🇦 Марокко — 90 дней\n"
        "🇲🇽 Мексика — 180 дней\n"
        "🇲🇩 Молдова — 90 дней\n"
        "🇲🇳 Монголия — 30 дней\n"
        "🇳🇦 Намибия — 90 дней\n"
        "🇳🇮 Никарагуа — 90 дней\n"
        "🇦🇪 ОАЭ — 90 дней\n"
        "🇵🇦 Панама — 90 дней\n"
        "🇵🇾 Парагвай — 90 дней\n"
        "🇵🇪 Перу — 90 дней\n"
        "🇸🇻 Сальвадор — 90 дней\n"
        "🇲🇰 Северная Македония — 90 дней\n"
        "🇸🇨 Сейшелы — 30 дней\n"
        "🇷🇸 Сербия — 30 дней\n"
        "🇹🇯 Таджикистан — 30 дней\n"
        "🇹🇭 Таиланд — 30 дней\n"
        "🇹🇹 Тринидад и Тобаго — 90 дней\n"
        "🇹🇳 Тунис — 30 дней\n"
        "🇹🇷 Турция — 60 дней\n"
        "🇺🇿 Узбекистан — 30 дней\n"
        "🇺🇾 Уругвай — 90 дней\n"
        "🇫🇯 Фиджи — 120 дней\n"
        "🇵🇭 Филиппины — 30 дней\n"
        "🇲🇪 Черногория — 30 дней\n"
        "🇨🇱 Чили — 90 дней\n"
        "🇪🇨 Эквадор — 90 дней\n"
        "🇰🇷 Южная Корея — 60 дней\n"
        "🇯🇲 Ямайка — 30 дней\n\n"
        "⚠️ Визовые правила меняются — проверяй актуальную информацию на сайте посольства перед поездкой."
    ),
    "📱 Электронная виза": (
        "📱 *Электронная виза для граждан России*\n\n"
        "🇦🇴 Ангола — 30 дней\n"
        "🇧🇭 Бахрейн — 14 дней\n"
        "🇧🇯 Бенин — 30 дней\n"
        "🇧🇫 Буркина-Фасо — 30 дней\n"
        "🇹🇱 Восточный Тимор — 30 дней\n"
        "🇬🇦 Габон — 30 дней\n"
        "🇬🇲 Гамбия — 30 дней\n"
        "🇬🇭 Гана — 30 дней\n"
        "🇬🇳 Гвинея — 30 дней\n"
        "🇩🇯 Джибути — 31 день\n"
        "🇪🇬 Египет — 30 дней\n"
        "🇿🇲 Замбия — 30 дней\n"
        "🇿🇼 Зимбабве — 30 дней\n"
        "🇮🇳 Индия — 30 дней\n"
        "🇯🇴 Иордания — 30 дней\n"
        "🇮🇷 Иран — 30 дней\n"
        "🇨🇻 Кабо-Верде — 30 дней\n"
        "🇰🇭 Камбоджа — 30 дней\n"
        "🇶🇦 Катар — 30 дней\n"
        "🇰🇪 Кения — 30 дней\n"
        "🇰🇲 Коморские острова — 30 дней\n"
        "🇨🇮 Кот-д'Ивуар — 30 дней\n"
        "🇰🇼 Кувейт — 90 дней\n"
        "🇱🇦 Лаос — 30 дней\n"
        "🇲🇷 Мавритания — 30 дней\n"
        "🇲🇬 Мадагаскар — 30 дней\n"
        "🇲🇼 Малави — 30 дней\n"
        "🇲🇿 Мозамбик — 30 дней\n"
        "🇲🇲 Мьянма — 28 дней\n"
        "🇳🇵 Непал — 30 дней\n"
        "🇳🇬 Нигерия — 30 дней\n"
        "🇴🇲 Оман — 30 дней\n"
        "🇵🇰 Пакистан — 30 дней\n"
        "🇵🇬 Папуа Новая Гвинея — 60 дней\n"
        "🇷🇼 Руанда — 30 дней\n"
        "🇼🇸 Самоа — 60 дней\n"
        "🇸🇹 Сан-Томе и Принсипи — 30 дней\n"
        "🇸🇦 Саудовская Аравия — 90 дней\n"
        "🇸🇳 Сенегал — 90 дней\n"
        "🇸🇩 Судан — 30 дней\n"
        "🇸🇱 Сьерра-Леоне — 30 дней\n"
        "🇹🇿 Танзания — 30 дней\n"
        "🇹🇬 Того — 7 дней\n"
        "🇺🇬 Уганда — 30 дней\n"
        "🇬🇶 Экваториальная Гвинея — 30 дней\n"
        "🇱🇰 Шри-Ланка — 30 дней\n"
        "🇪🇹 Эфиопия — 30 дней\n\n"
        "⚠️ Визовые правила меняются — проверяй актуальную информацию на сайте посольства перед поездкой."
    ),
    "💡 Полезная информация": (
        "💡 *Полезная информация о визах*\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📋 *Как правильно подать документы*\n"
        "• Готовь полный пакет с запасом — лучше принести лишнее, чем не хватит\n"
        "• Все копии делай с оригиналов, не с копий\n"
        "• Фото строго по требованиям консульства — размер, фон, без очков\n"
        "• Бронь жилья и билетов делай только после одобрения визы (или возвратную)\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🚫 *Частые причины отказа и как избежать*\n"
        "• Недостаточное финансирование — покажи выписку с остатком от $50-100/день\n"
        "• Нет обратного билета — всегда прикладывай подтверждение возврата\n"
        "• Неполный пакет документов — проверяй список на сайте посольства дважды\n"
        "• Несоответствие маршрута и жилья — всё должно совпадать логически\n"
        "• Плохая визовая история — откажи в Шенгене дважды? Сложнее следующий раз\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🏥 *Сколько стоит страховка для визы*\n"
        "• Шенген: от 500₽ за 2 недели (Ингосстрах, АльфаСтрахование, Тинькофф)\n"
        "• Минимальное покрытие: €30 000 — это требование визы\n"
        "• Берёшь страховку с покрытием €50 000+ → повышает доверие консульства\n"
        "• Онлайн-страховки (Cherehapa, Страховка.ру) часто дешевле прямых\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🇪🇺 *Шенгенская виза — особенности*\n"
        "• 26 стран зоны Шенген — одна виза на все\n"
        "• Однократная: 1 въезд, действует до 90 дней в полугодии\n"
        "• Многократная (мультивиза): несколько въездов в течение срока действия\n"
        "• Подавать нужно в консульство страны с наибольшим сроком пребывания\n"
        "• Срок ожидания: 5-15 рабочих дней, в сезон — до 30\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🔁 *Как получить мультивизу*\n"
        "• Покажи историю предыдущих шенгенских виз с хорошим использованием\n"
        "• Не нарушай срок пребывания — это ключевой показатель для консульства\n"
        "• Финансовая стабильность: справка с работы + выписки счёта\n"
        "• Некоторые консульства дают мульти сразу (Финляндия, Германия, Испания)\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🚪 *Правило первого въезда в Шенген*\n"
        "• Первый въезд должен быть в страну, выдавшую визу\n"
        "• Нарушение = риск аннулирования визы прямо на границе\n"
        "• Исключение: страна наибольшего пребывания уже посещена в этой поездке\n"
        "• Маршрут в анкете и реальный должны совпадать\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "❌ *Что делать если отказали*\n"
        "• Получи официальный отказ с указанием причины\n"
        "• Исправь причину и подай повторно с пояснительным письмом\n"
        "• Попробуй другое консульство Шенгена (законно, если нет предыдущих виз)\n"
        "• Обжалуй решение — в большинстве стран есть апелляционная процедура\n"
        "• Возьми помощь визового агентства — они знают нюансы\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "💰 *Финансовое подтверждение — сколько денег показать*\n"
        "• Шенген: от €50-100 в день на человека (зависит от страны)\n"
        "• Германия: €45/день, Франция: €65/день, Испания: €100/день\n"
        "• США: нет чёткой суммы, важна стабильность дохода и остатков\n"
        "• Выписка должна быть свежей — не старше 1-3 месяцев\n"
        "• Спонсорское письмо от родственников тоже принимается\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🏨 *Бронь отеля vs реальное жильё*\n"
        "• Для визы нужна бронь — она может быть возвратной или без предоплаты\n"
        "• Booking.com: бронируй с бесплатной отменой — консульству хватает\n"
        "• Если живёшь у друзей/родных — нужно приглашение и регистрация\n"
        "• Airbnb: скриншот брони с датами и адресом принимают везде\n"
        "• Бронь должна совпадать с датами визы по заявлению\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📅 *Сроки подачи документов*\n"
        "• Шенген: не ранее чем за 6 месяцев и не позже чем за 15 дней до поездки\n"
        "• США (туристическая B1/B2): запись на собеседование — за 2-6 месяцев\n"
        "• Великобритания: минимум за 3 недели, лучше за 6-8 недель\n"
        "• Летний сезон — пик записей, подавай заранее\n"
        "• Срочное оформление: доступно во многих консульствах за доп. плату\n\n"
        "⚠️ _Требования меняются — всегда проверяй на официальном сайте посольства_"
    ),
    "📋 Нужна виза": (
        "📋 *Нужна виза для граждан России*\n\n"
        "🇦🇺 Австралия (туристическая subclass 600)\n"
        "🇦🇹 Австрия (Шенген)\n"
        "🇦🇫 Афганистан (въездная виза)\n"
        "🇧🇩 Бангладеш (туристическая)\n"
        "🇧🇪 Бельгия (Шенген)\n"
        "🇧🇬 Болгария (национальная D)\n"
        "🇧🇮 Бурунди (туристическая)\n"
        "🇧🇹 Бутан (туристическая)\n"
        "🇻🇦 Ватикан (Шенген — через Италию)\n"
        "🇬🇧 Великобритания (Standard Visitor)\n"
        "🇭🇺 Венгрия (Шенген)\n"
        "🇭🇹 Гаити (туристическая)\n"
        "🇬🇼 Гвинея-Бисау (туристическая)\n"
        "🇩🇪 Германия (Шенген)\n"
        "🇬🇷 Греция (Шенген)\n"
        "🇩🇰 Дания (Шенген)\n"
        "🇩🇲 Доминика (туристическая)\n"
        "🇨🇩 ДР Конго (туристическая)\n"
        "🇮🇶 Ирак (въездная виза)\n"
        "🇮🇪 Ирландия (туристическая)\n"
        "🇮🇸 Исландия (Шенген)\n"
        "🇪🇸 Испания (Шенген)\n"
        "🇮🇹 Италия (Шенген)\n"
        "🇾🇪 Йемен (въездная виза)\n"
        "🇨🇲 Камерун (туристическая)\n"
        "🇨🇦 Канада (Visitor Visa)\n"
        "🇨🇾 Кипр (национальная C/D)\n"
        "🇰🇮 Кирибати (въездная виза)\n"
        "🇨🇬 Конго (туристическая)\n"
        "🇽🇰 Косово (национальная)\n"
        "🇱🇻 Латвия (Шенген)\n"
        "🇱🇸 Лесото (туристическая)\n"
        "🇱🇷 Либерия (туристическая)\n"
        "🇱🇧 Ливан (туристическая)\n"
        "🇱🇾 Ливия (въездная виза)\n"
        "🇱🇹 Литва (Шенген)\n"
        "🇱🇮 Лихтенштейн (Шенген)\n"
        "🇱🇺 Люксембург (Шенген)\n"
        "🇲🇱 Мали (туристическая)\n"
        "🇲🇹 Мальта (Шенген)\n"
        "🇲🇭 Маршалловы острова (въездная виза)\n"
        "🇫🇲 Микронезия (туристическая)\n"
        "🇲🇨 Монако (Шенген — через Францию)\n"
        "🇳🇷 Науру (въездная виза)\n"
        "🇳🇪 Нигер (туристическая)\n"
        "🇳🇱 Нидерланды (Шенген)\n"
        "🇳🇿 Новая Зеландия (Visitor Visa)\n"
        "🇳🇴 Норвегия (Шенген)\n"
        "🇵🇼 Палау (въездная виза)\n"
        "🇵🇸 Палестина (въездное разрешение)\n"
        "🇵🇱 Польша (Шенген)\n"
        "🇵🇹 Португалия (Шенген)\n"
        "🇷🇴 Румыния (национальная D)\n"
        "🇸🇲 Сан-Марино (Шенген — через Италию)\n"
        "🇰🇵 Северная Корея (туристическая)\n"
        "🇻🇨 Сент-Винсент и Гренадины (туристическая)\n"
        "🇰🇳 Сент-Китс и Невис (туристическая)\n"
        "🇱🇨 Сент-Люсия (туристическая)\n"
        "🇸🇬 Сингапур (туристическая)\n"
        "🇸🇾 Сирия (туристическая)\n"
        "🇸🇰 Словакия (Шенген)\n"
        "🇸🇮 Словения (Шенген)\n"
        "🇸🇧 Соломоновы острова (въездная виза)\n"
        "🇸🇴 Сомали (въездная виза)\n"
        "🇺🇸 США (туристическая B1/B2)\n"
        "🇸🇷 Суринам (туристическая)\n"
        "🇹🇼 Тайвань (въездная виза)\n"
        "🇹🇴 Тонга (туристическая)\n"
        "🇹🇻 Тувалу (въездная виза)\n"
        "🇹🇲 Туркменистан (туристическая)\n"
        "🇺🇦 Украина (въездная виза)\n"
        "🇫🇮 Финляндия (Шенген)\n"
        "🇫🇷 Франция (Шенген)\n"
        "🇭🇷 Хорватия (Шенген)\n"
        "🇨🇫 ЦАР (туристическая)\n"
        "🇹🇩 Чад (туристическая)\n"
        "🇨🇿 Чехия (Шенген)\n"
        "🇨🇭 Швейцария (Шенген)\n"
        "🇸🇪 Швеция (Шенген)\n"
        "🇪🇷 Эритрея (туристическая)\n"
        "🇸🇿 Эсватини (туристическая)\n"
        "🇪🇪 Эстония (Шенген)\n"
        "🇿🇦 ЮАР (туристическая)\n"
        "🇸🇸 Южный Судан (туристическая)\n"
        "🇯🇵 Япония (туристическая)\n\n"
        "⚠️ Визовая ситуация меняется — уточняй актуальную информацию "
        "на сайте посольства. Актуально на 2025–2026."
    ),
}


async def translate_text(text: str) -> tuple:
    """Return (translated, src_lang, dst_lang). Uses MyMemory API."""
    has_cyrillic = any('\u0400' <= ch <= '\u04ff' for ch in text)
    src, dst = ('ru', 'en') if has_cyrillic else ('en', 'ru')
    url = MYMEMORY_URL.format(
        q=urllib.parse.quote(text, safe=''),
        lp=f"{src}|{dst}",
    )
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(
        None, lambda: urllib.request.urlopen(url, timeout=8).read()
    )
    data = json.loads(raw)
    translated = data['responseData']['translatedText']
    return translated, src, dst

QUESTIONS = [
    {"id": "company", "text": "Привет! Я твой travel-помощник 🌍\n\nС кем планируешь путешествие?", "opts": ["Один", "С партнёром", "С друзьями", "С семьёй и детьми"]},
    {"id": "nature", "text": "Что больше притягивает в путешествии?", "opts": ["Море и пляжи", "Горы и природа", "Горы и море", "Города и культура", "Джунгли и экзотика"]},
    {"id": "budget", "text": "Бюджет на человека (перелёт + отель + еда)?", "opts": ["До 50 000 ₽", "50–100 000 ₽", "100–200 000 ₽", "Без ограничений"]},
    {"id": "passport", "text": "Есть загранпаспорт?", "opts": ["Да, биометрический", "Да, обычный", "Нет загранпаспорта"]},
    {"id": "duration", "text": "Сколько дней планируешь?", "opts": ["До 7 дней", "1–2 недели", "2–4 недели", "Больше месяца"]},
    {"id": "climate", "text": "Какой климат предпочитаешь?", "opts": ["Жара +30 и выше", "Тепло +20–28", "Прохладно +10–18", "Не важно"]},
    {"id": "food", "text": "Как относишься к экзотической еде?", "opts": ["Пробую всё подряд", "Осторожно, но пробую", "Предпочитаю привычное", "Только европейская кухня"]},
    {"id": "vibe", "text": "Что главное в поездке?", "opts": ["Полный отдых и пляж", "Культура и история", "Достопримечательности и UNESCO", "Гастрономия и рынки", "Экстрим и активность"]},
    {"id": "accommodation", "text": "Где предпочитаешь жить?", "opts": ["Отель 4–5 звёзд", "Отель 2–3 звезды", "Хостел или гестхаус", "Апартаменты"]},
    {"id": "experience", "text": "Опыт самостоятельных путешествий?", "opts": ["Первый раз за рубеж", "Иногда езжу", "Опытный путешественник", "Постоянно в дороге"]},
    {"id": "visa", "text": "Готов оформлять визу?", "opts": ["Да, любую", "Только e-visa онлайн", "Только безвизовые страны", "Не знаю как это делать"]},
    {"id": "activity", "text": "Любимое занятие в поездке?", "opts": ["Пляж и купание", "Экскурсии и музеи", "Гулять куда глаза глядят", "Шоппинг и рынки", "Трекинг и природа"]},
    {"id": "transport", "text": "Как передвигаешься внутри страны?", "opts": ["Аренда авто или мото", "Общественный транспорт", "Такси и трансферы", "Пешком"]},
    {"id": "language", "text": "Знаешь иностранные языки?", "opts": ["Английский хорошо", "Английский базово", "Только русский", "Несколько языков"]},
    {"id": "goal", "text": "Последний вопрос! Главная цель поездки?", "opts": ["Полностью отключиться", "Увидеть максимум мест", "Погрузиться в культуру", "Совместить работу и отдых"]},
]

DESTINATIONS = [
    {"country": "Таиланд", "city": "Бангкок", "flag": "🇹🇭", "why": "Идеальный баланс пляжей, культуры, уличной еды и доступных цен.", "highlight": "Буддийские храмы, ночные рынки и острова с бирюзовой водой", "best_time": "Ноябрь – февраль", "budget": "от 2 500 ₽/день", "tip": "Покупай симку в аэропорту сразу — интернет дешёвый и быстрый везде", "visa": "Безвизово 30 дней", "tags": {"nature": ["Море и пляжи", "Джунгли и экзотика", "Горы и море"], "climate": ["Жара +30 и выше", "Тепло +20–28"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Полный отдых и пляж", "Гастрономия и рынки", "Культура и история"]}},
    {"country": "Вьетнам", "city": "Ханой", "flag": "🇻🇳", "why": "Разнообразие на любой вкус: мегаполисы, рисовые поля, пляжи и вкуснейшая уличная еда.", "highlight": "Бухта Халонг, фонарики Хойана и фо за 50 рублей", "best_time": "Февраль – апрель", "budget": "от 2 000 ₽/день", "tip": "Торгуйся везде — первая цена для туристов завышена в 2–3 раза", "visa": "E-visa онлайн, 90 дней", "tags": {"nature": ["Море и пляжи", "Горы и природа", "Горы и море"], "climate": ["Жара +30 и выше", "Тепло +20–28"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Культура и история", "Гастрономия и рынки", "Достопримечательности и UNESCO"]}},
    {"country": "Камбоджа", "city": "Сием Рип", "flag": "🇰🇭", "why": "Ангкор Ват — одно из величайших чудес света и древняя цивилизация вокруг.", "highlight": "Ангкор Ват на рассвете, плавучие деревни и закаты", "best_time": "Ноябрь – март", "budget": "от 1 800 ₽/день", "tip": "Вставай в 4 утра чтобы встретить рассвет в Ангкор Вате — незабываемо", "visa": "E-visa 30 долларов", "tags": {"nature": ["Джунгли и экзотика"], "climate": ["Жара +30 и выше"], "budget": ["До 50 000 ₽"], "vibe": ["Достопримечательности и UNESCO", "Культура и история"]}},
    {"country": "Лаос", "city": "Луанг Прабанг", "flag": "🇱🇦", "why": "Самая медленная и душевная страна Азии — монахи, водопады и полная тишина.", "highlight": "Рассвет с монахами, водопад Куанг Си и берега Меконга", "best_time": "Ноябрь – февраль", "budget": "от 1 500 ₽/день", "tip": "Вставай в 5:30 чтобы увидеть церемонию подношения еды монахам — это незабываемо", "visa": "E-visa онлайн", "tags": {"nature": ["Горы и природа", "Джунгли и экзотика"], "climate": ["Жара +30 и выше", "Тепло +20–28"], "budget": ["До 50 000 ₽"], "vibe": ["Культура и история", "Полный отдых и пляж"]}},
    {"country": "Малайзия", "city": "Куала-Лумпур", "flag": "🇲🇾", "why": "Мультикультурная страна с башнями Петронас, джунглями и лучшей уличной едой Азии.", "highlight": "Башни Петронас, острова Perhentian и чайные плантации Cameron Highlands", "best_time": "Март – октябрь", "budget": "от 2 500 ₽/день", "tip": "Возьми карту Touch n Go — работает везде в транспорте и экономит время", "visa": "Безвизово 30 дней", "tags": {"nature": ["Джунгли и экзотика", "Море и пляжи"], "climate": ["Жара +30 и выше"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Гастрономия и рынки", "Культура и история"]}},
    {"country": "Сингапур", "city": "Сингапур", "flag": "🇸🇬", "why": "Самый чистый и безопасный город Азии — архитектура, еда и шоппинг.", "highlight": "Gardens by the Bay, Марина Бэй Сэндс и хокер-центры", "best_time": "Февраль – апрель", "budget": "от 7 000 ₽/день", "tip": "Ешь в хокер-центрах — еда дешевле и вкуснее ресторанов в разы", "visa": "Безвизово 30 дней", "tags": {"nature": ["Города и культура"], "climate": ["Жара +30 и выше"], "budget": ["100–200 000 ₽", "Без ограничений"], "vibe": ["Культура и история", "Гастрономия и рынки", "Достопримечательности и UNESCO"]}},
    {"country": "Филиппины", "city": "Манила", "flag": "🇵🇭", "why": "Более 7000 островов с бирюзовой водой, кораллами и белым песком.", "highlight": "Острова Палаван, Боракай и дайвинг", "best_time": "Декабрь – май", "budget": "от 2 500 ₽/день", "tip": "Летай внутренними рейсами Cebu Pacific — дёшево между островами", "visa": "Безвизово 30 дней", "tags": {"nature": ["Море и пляжи", "Джунгли и экзотика", "Горы и море"], "climate": ["Жара +30 и выше"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Полный отдых и пляж", "Экстрим и активность"]}},
    {"country": "Гонконг", "city": "Гонконг", "flag": "🇭🇰", "why": "Нью-Йорк Азии — небоскрёбы, трамваи, уличная еда и невероятные виды.", "highlight": "Вид с Пик Трам, ночной рынок Монгкок и димсам с утра", "best_time": "Октябрь – декабрь", "budget": "от 6 000 ₽/день", "tip": "Карта Octopus — покупай сразу в аэропорту, работает везде", "visa": "Безвизово 14 дней", "tags": {"nature": ["Города и культура"], "climate": ["Тепло +20–28", "Жара +30 и выше"], "budget": ["50–100 000 ₽", "100–200 000 ₽"], "vibe": ["Гастрономия и рынки", "Культура и история"]}},
    {"country": "Макао", "city": "Макао", "flag": "🇲🇴", "why": "Смесь португальской архитектуры и азиатского азарта — уникальное место в мире.", "highlight": "Руины Сан-Паулу, казино и португальская кухня в Азии", "best_time": "Октябрь – декабрь", "budget": "от 4 000 ₽/день", "tip": "Из Гонконга добирайся на скоростном пароме — всего 1 час", "visa": "Безвизово 30 дней", "tags": {"nature": ["Города и культура"], "climate": ["Тепло +20–28"], "budget": ["50–100 000 ₽", "100–200 000 ₽"], "vibe": ["Культура и история", "Достопримечательности и UNESCO"]}},
    {"country": "Китай", "city": "Пекин", "flag": "🇨🇳", "why": "Великая стена, запретный город и 5000 лет истории — масштаб которому нет равных.", "highlight": "Великая Китайская стена, Запретный город и сады Сучжоу", "best_time": "Апрель – май, сентябрь – октябрь", "budget": "от 3 500 ₽/день", "tip": "Скачай VPN до въезда — без него нет Google, Instagram и WhatsApp", "visa": "Виза или 144-часовой транзит", "tags": {"nature": ["Горы и природа", "Города и культура"], "climate": ["Тепло +20–28"], "budget": ["50–100 000 ₽"], "vibe": ["Достопримечательности и UNESCO", "Культура и история"]}},
    {"country": "Южная Корея", "city": "Сеул", "flag": "🇰🇷", "why": "K-pop, уличная еда, хайтек и древние дворцы в одном флаконе.", "highlight": "Дворец Кёнбоккун, рынок Намдэмун и острова Чеджу", "best_time": "Март – май, сентябрь – ноябрь", "budget": "от 4 000 ₽/день", "tip": "Транспортная карта T-money — обязательна, метро и автобусы очень удобные", "visa": "Безвизово 30 дней", "tags": {"nature": ["Города и культура", "Горы и природа"], "climate": ["Тепло +20–28", "Прохладно +10–18"], "budget": ["50–100 000 ₽"], "vibe": ["Культура и история", "Гастрономия и рынки"]}},
    {"country": "Япония", "city": "Токио", "flag": "🇯🇵", "why": "Уникальное сочетание древней культуры и ультрасовременного города.", "highlight": "Фудзи, суши, сакура и технологии будущего", "best_time": "Март – май, октябрь – ноябрь", "budget": "от 6 000 ₽/день", "tip": "Купи JR Pass до въезда в Японию — сэкономишь на синкансэнах", "visa": "Безвизово 90 дней", "tags": {"nature": ["Горы и природа", "Города и культура"], "climate": ["Тепло +20–28", "Прохладно +10–18"], "budget": ["100–200 000 ₽", "Без ограничений"], "vibe": ["Достопримечательности и UNESCO", "Культура и история", "Гастрономия и рынки"]}},
    {"country": "Турция", "city": "Стамбул", "flag": "🇹🇷", "why": "Два континента, тысячелетняя история, море и отличная кухня по доступным ценам.", "highlight": "Голубая мечеть, Каппадокия и Средиземноморское побережье", "best_time": "Апрель – июнь, сентябрь – ноябрь", "budget": "от 3 000 ₽/день", "tip": "Стамбульская карта выгоднее разовых билетов — купи сразу", "visa": "Безвизово 60 дней", "tags": {"nature": ["Море и пляжи", "Города и культура", "Горы и море"], "climate": ["Тепло +20–28", "Жара +30 и выше"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Достопримечательности и UNESCO", "Культура и история", "Гастрономия и рынки"]}},
    {"country": "Египет", "city": "Каир", "flag": "🇪🇬", "why": "Пирамиды, Красное море и 7000 лет истории — одна из самых древних цивилизаций.", "highlight": "Пирамиды Гизы, Луксор и дайвинг в Шарм-эль-Шейхе", "best_time": "Октябрь – апрель", "budget": "от 2 500 ₽/день", "tip": "Торгуйся везде и всегда — это норма культуры, не грубость", "visa": "E-visa онлайн или по прилёту", "tags": {"nature": ["Море и пляжи", "Горы и море"], "climate": ["Жара +30 и выше"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Достопримечательности и UNESCO", "Культура и история", "Полный отдых и пляж"]}},
    {"country": "Марокко", "city": "Марракеш", "flag": "🇲🇦", "why": "Медины, пустыня Сахара, синий город Шефшауэн и невероятная еда.", "highlight": "Площадь Джемаа эль-Фна, пустыня Мерзуга и синий город Шефшауэн", "best_time": "Март – май, сентябрь – ноябрь", "budget": "от 2 500 ₽/день", "tip": "Найми местного гида на 1 день в медине — без него легко потеряться и переплатить", "visa": "Безвизово 90 дней", "tags": {"nature": ["Горы и природа", "Джунгли и экзотика"], "climate": ["Тепло +20–28", "Жара +30 и выше"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Культура и история", "Гастрономия и рынки", "Достопримечательности и UNESCO"]}},
    {"country": "Тунис", "city": "Тунис", "flag": "🇹🇳", "why": "Средиземноморские пляжи, Карфаген, Сахара и самая доступная Африка.", "highlight": "Руины Карфагена, пустыня Сахара и медина Туниса", "best_time": "Апрель – июнь, сентябрь – октябрь", "budget": "от 2 000 ₽/день", "tip": "Возьми машину напрокат — страна маленькая, за неделю можно объехать всё", "visa": "Безвизово 90 дней", "tags": {"nature": ["Море и пляжи", "Горы и море"], "climate": ["Жара +30 и выше", "Тепло +20–28"], "budget": ["До 50 000 ₽"], "vibe": ["Достопримечательности и UNESCO", "Культура и история", "Полный отдых и пляж"]}},
    {"country": "Иордания", "city": "Амман", "flag": "🇯🇴", "why": "Петра, Мёртвое море, Вади Рам — концентрация чудес света на маленькой территории.", "highlight": "Петра, пустыня Вади Рам и купание в Мёртвом море", "best_time": "Март – май, сентябрь – ноябрь", "budget": "от 4 000 ₽/день", "tip": "Jordan Pass — покупай онлайн до поездки, включает визу и вход в Петру", "visa": "Jordan Pass или виза по прилёту", "tags": {"nature": ["Горы и природа"], "climate": ["Тепло +20–28", "Жара +30 и выше"], "budget": ["50–100 000 ₽"], "vibe": ["Достопримечательности и UNESCO", "Культура и история", "Экстрим и активность"]}},
    {"country": "ОАЭ", "city": "Дубай", "flag": "🇦🇪", "why": "Роскошь, небоскрёбы, пустыня и шоппинг — город будущего в пустыне.", "highlight": "Бурдж Халифа, сафари в пустыне и золотой рынок", "best_time": "Ноябрь – март", "budget": "от 8 000 ₽/день", "tip": "Метро в Дубае отличное — не трать деньги на такси для перемещений по городу", "visa": "Безвизово 30 дней", "tags": {"nature": ["Города и культура"], "climate": ["Жара +30 и выше"], "budget": ["100–200 000 ₽", "Без ограничений"], "vibe": ["Достопримечательности и UNESCO", "Шоппинг и рынки"]}},
    {"country": "Оман", "city": "Маскат", "flag": "🇴🇲", "why": "Самая безопасная и красивая страна Ближнего Востока — фьорды, пустыня и гостеприимство.", "highlight": "Фьорды Мусандам, пустыня Вахиба и форты Низва", "best_time": "Октябрь – март", "budget": "от 5 000 ₽/день", "tip": "Арендуй внедорожник — без него половину страны не увидишь", "visa": "E-visa онлайн", "tags": {"nature": ["Горы и природа", "Море и пляжи", "Горы и море"], "climate": ["Жара +30 и выше", "Тепло +20–28"], "budget": ["50–100 000 ₽", "100–200 000 ₽"], "vibe": ["Экстрим и активность", "Культура и история"]}},
    {"country": "Армения", "city": "Ереван", "flag": "🇦🇲", "why": "Древнейшая христианская страна с монастырями, горами и коньяком.", "highlight": "Монастырь Гегард, гора Арарат и рынок Вернисаж", "best_time": "Май – октябрь", "budget": "от 1 800 ₽/день", "tip": "Попробуй коньяк на заводе Арарат — экскурсия с дегустацией стоит копейки", "visa": "Безвизово", "tags": {"nature": ["Горы и природа"], "climate": ["Тепло +20–28", "Прохладно +10–18"], "budget": ["До 50 000 ₽"], "vibe": ["Культура и история", "Достопримечательности и UNESCO", "Гастрономия и рынки"]}},
    {"country": "Грузия", "city": "Тбилиси", "flag": "🇬🇪", "why": "Безвизово, дёшево, вкусно и невероятно красиво — горы, вино и гостеприимство.", "highlight": "Кавказские горы, хачапури, вино из квеври и старый Тбилиси", "best_time": "Май – июнь, сентябрь – октябрь", "budget": "от 2 000 ₽/день", "tip": "Возьми машину — общественного транспорта в горах почти нет", "visa": "Безвизово 365 дней", "tags": {"nature": ["Горы и природа", "Горы и море"], "climate": ["Тепло +20–28", "Прохладно +10–18"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Культура и история", "Гастрономия и рынки", "Достопримечательности и UNESCO"]}},
    {"country": "Кыргызстан", "city": "Бишкек", "flag": "🇰🇬", "why": "Нетронутая природа, горы, юрты и самый дикий и красивый Тянь-Шань.", "highlight": "Озеро Иссык-Куль, ущелье Барскоон и кочевая культура", "best_time": "Июнь – сентябрь", "budget": "от 1 500 ₽/день", "tip": "Ночуй в юрте у местных — это дёшево и даёт настоящее погружение в культуру", "visa": "Безвизово 60 дней", "tags": {"nature": ["Горы и природа", "Горы и море"], "climate": ["Тепло +20–28", "Прохладно +10–18"], "budget": ["До 50 000 ₽"], "vibe": ["Экстрим и активность", "Культура и история"]}},
    {"country": "Куба", "city": "Гавана", "flag": "🇨🇺", "why": "Машины 50-х, сальса, ром и Карибское море — планета в параллельном измерении.", "highlight": "Старая Гавана, Варадеро и плантации табака Виньялес", "best_time": "Декабрь – апрель", "budget": "от 4 000 ₽/день", "tip": "Бери наличные — карты почти нигде не работают, банкоматов мало", "visa": "Туристическая карта", "tags": {"nature": ["Море и пляжи", "Горы и море"], "climate": ["Жара +30 и выше"], "budget": ["50–100 000 ₽"], "vibe": ["Культура и история", "Полный отдых и пляж", "Гастрономия и рынки"]}},
    {"country": "Испания", "city": "Барселона", "flag": "🇪🇸", "why": "Гауди, фламенко, тапас и лучший климат Европы — страна для всех.", "highlight": "Саграда Фамилия, Альгамбра и пляжи Коста Бравы", "best_time": "Май – июнь, сентябрь – октябрь", "budget": "от 5 000 ₽/день", "tip": "Меню дня в обед — полноценный обед за 10-12 евро даже в хорошем ресторане", "visa": "Шенген", "tags": {"nature": ["Море и пляжи", "Города и культура", "Горы и море"], "climate": ["Тепло +20–28", "Жара +30 и выше"], "budget": ["50–100 000 ₽", "100–200 000 ₽"], "vibe": ["Достопримечательности и UNESCO", "Культура и история", "Гастрономия и рынки"]}},
    {"country": "Италия", "city": "Рим", "flag": "🇮🇹", "why": "Колизей, Ватикан, пицца и паста — страна где история на каждом углу.", "highlight": "Колизей, Венеция, Амальфитанское побережье и Помпеи", "best_time": "Апрель – июнь, сентябрь – октябрь", "budget": "от 6 000 ₽/день", "tip": "Бронируй Колизей и Ватикан онлайн заранее — живые очереди на 3–4 часа", "visa": "Шенген", "tags": {"nature": ["Море и пляжи", "Города и культура", "Горы и море"], "climate": ["Тепло +20–28", "Жара +30 и выше"], "budget": ["50–100 000 ₽", "100–200 000 ₽"], "vibe": ["Достопримечательности и UNESCO", "Культура и история", "Гастрономия и рынки"]}},
    {"country": "Ватикан", "city": "Ватикан", "flag": "🇻🇦", "why": "Самое маленькое государство мира — Сикстинская капелла и собор Святого Петра.", "highlight": "Сикстинская капелла, Ватиканские музеи и площадь Святого Петра", "best_time": "Апрель – июнь, сентябрь – октябрь", "budget": "от 5 000 ₽/день", "tip": "Бронируй музеи за 2–3 недели — билеты раскупаются мгновенно", "visa": "Шенген", "tags": {"nature": ["Города и культура"], "climate": ["Тепло +20–28"], "budget": ["50–100 000 ₽", "100–200 000 ₽"], "vibe": ["Достопримечательности и UNESCO", "Культура и история"]}},
    {"country": "Франция", "city": "Париж", "flag": "🇫🇷", "why": "Эйфелева башня, Лувр, круассаны и лучшее вино — классика которая не надоедает.", "highlight": "Эйфелева башня, Лувр, замки Луары и Лазурный берег", "best_time": "Апрель – июнь, сентябрь – октябрь", "budget": "от 8 000 ₽/день", "tip": "Museum Pass окупается за 2–3 музея — бери сразу на 4 дня", "visa": "Шенген", "tags": {"nature": ["Города и культура", "Море и пляжи"], "climate": ["Тепло +20–28", "Прохладно +10–18"], "budget": ["100–200 000 ₽", "Без ограничений"], "vibe": ["Достопримечательности и UNESCO", "Культура и история", "Гастрономия и рынки"]}},
    {"country": "Германия", "city": "Берлин", "flag": "🇩🇪", "why": "История, пиво, замки Баварии и самый живой арт-андеграунд Европы.", "highlight": "Замок Нойшванштайн, Берлинская стена и Кёльнский собор", "best_time": "Май – сентябрь", "budget": "от 6 000 ₽/день", "tip": "Покупай день-тикет на транспорт — неограниченные поездки дешевле разовых", "visa": "Шенген", "tags": {"nature": ["Города и культура", "Горы и природа"], "climate": ["Тепло +20–28", "Прохладно +10–18"], "budget": ["50–100 000 ₽", "100–200 000 ₽"], "vibe": ["Достопримечательности и UNESCO", "Культура и история"]}},
    {"country": "Австрия", "city": "Вена", "flag": "🇦🇹", "why": "Венская опера, Альпы, Захер-торт и самая элегантная столица Европы.", "highlight": "Дворец Шёнбрунн, Венская опера и Альпы в Зальцбурге", "best_time": "Апрель – октябрь", "budget": "от 7 000 ₽/день", "tip": "Венская карта — транспорт + скидки на музеи, окупается за 1 день", "visa": "Шенген", "tags": {"nature": ["Горы и природа", "Города и культура"], "climate": ["Тепло +20–28", "Прохладно +10–18"], "budget": ["100–200 000 ₽"], "vibe": ["Достопримечательности и UNESCO", "Культура и история"]}},
    {"country": "Чехия", "city": "Прага", "flag": "🇨🇿", "why": "Самая сказочная столица Европы — готика, пиво и средневековые улочки.", "highlight": "Пражский Град, Карлов мост и Чешский Крумлов", "best_time": "Апрель – июнь, сентябрь – октябрь", "budget": "от 4 500 ₽/день", "tip": "Не меняй деньги на Вацлавской площади — курс грабительский, ищи обменники без комиссии", "visa": "Шенген", "tags": {"nature": ["Города и культура"], "climate": ["Тепло +20–28", "Прохладно +10–18"], "budget": ["50–100 000 ₽"], "vibe": ["Достопримечательности и UNESCO", "Культура и история", "Гастрономия и рынки"]}},
    {"country": "Венгрия", "city": "Будапешт", "flag": "🇭🇺", "why": "Будапешт — одна из красивейших столиц Европы с термальными банями и венгерской кухней.", "highlight": "Парламент, термальные бани Сечени и рыбацкий бастион", "best_time": "Апрель – июнь, сентябрь – октябрь", "budget": "от 4 000 ₽/день", "tip": "Термальные бани — обязательно, лучше идти в будний день утром", "visa": "Шенген", "tags": {"nature": ["Города и культура"], "climate": ["Тепло +20–28", "Прохладно +10–18"], "budget": ["50–100 000 ₽"], "vibe": ["Достопримечательности и UNESCO", "Культура и история", "Гастрономия и рынки"]}},
    {"country": "Польша", "city": "Варшава", "flag": "🇵🇱", "why": "Краков, Освенцим, Вроцлав и самая недооценённая страна Европы.", "highlight": "Старый город Кракова, соляные шахты Велички и замок Мальборк", "best_time": "Май – сентябрь", "budget": "от 3 500 ₽/день", "tip": "Краков интереснее Варшавы — обязательно приедь туда хотя бы на 2 дня", "visa": "Шенген", "tags": {"nature": ["Города и культура"], "climate": ["Тепло +20–28", "Прохладно +10–18"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Достопримечательности и UNESCO", "Культура и история"]}},
    {"country": "Словакия", "city": "Братислава", "flag": "🇸🇰", "why": "Маленькая и уютная столица рядом с Веной — замки, горы и дешевле соседей.", "highlight": "Братиславский замок, Высокие Татры и средневековый центр", "best_time": "Май – сентябрь", "budget": "от 3 500 ₽/день", "tip": "Из Братиславы до Вены всего час на автобусе — удобно совместить", "visa": "Шенген", "tags": {"nature": ["Горы и природа", "Города и культура"], "climate": ["Тепло +20–28", "Прохладно +10–18"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Культура и история", "Экстрим и активность"]}},
    {"country": "Бельгия", "city": "Брюссель", "flag": "🇧🇪", "why": "Шоколад, вафли, пиво и самые красивые площади Европы.", "highlight": "Гран Плас, Брюгге и бельгийский шоколад", "best_time": "Май – сентябрь", "budget": "от 7 000 ₽/день", "tip": "Брюгге красивее Брюсселя — обязательно съезди на день", "visa": "Шенген", "tags": {"nature": ["Города и культура"], "climate": ["Тепло +20–28", "Прохладно +10–18"], "budget": ["100–200 000 ₽"], "vibe": ["Достопримечательности и UNESCO", "Культура и история", "Гастрономия и рынки"]}},
    {"country": "Нидерланды", "city": "Амстердам", "flag": "🇳🇱", "why": "Каналы, тюльпаны, велосипеды и Рембрандт — страна которую хочется возвращать снова.", "highlight": "Каналы Амстердама, поля тюльпанов и музей Ван Гога", "best_time": "Апрель – май, сентябрь", "budget": "от 8 000 ₽/день", "tip": "Берёшь велосипед в аренду — это самый удобный транспорт по городу", "visa": "Шенген", "tags": {"nature": ["Города и культура"], "climate": ["Тепло +20–28", "Прохладно +10–18"], "budget": ["100–200 000 ₽"], "vibe": ["Достопримечательности и UNESCO", "Культура и история"]}},
    {"country": "Болгария", "city": "София", "flag": "🇧🇬", "why": "Самая доступная страна ЕС — горы, море, розовые поля и отличная кухня.", "highlight": "Рильский монастырь, Созополь и долина роз", "best_time": "Июнь – сентябрь", "budget": "от 3 000 ₽/день", "tip": "Аренда авто обязательна — общественный транспорт между городами медленный", "visa": "Шенген", "tags": {"nature": ["Море и пляжи", "Горы и природа", "Горы и море"], "climate": ["Тепло +20–28", "Жара +30 и выше"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Достопримечательности и UNESCO", "Полный отдых и пляж", "Культура и история"]}},
    {"country": "Кипр", "city": "Лимассол", "flag": "🇨🇾", "why": "Средиземноморский остров с античными храмами, горами и лучшим вином региона.", "highlight": "Замок Колосси, Пафос и горы Троодос", "best_time": "Апрель – июнь, сентябрь – ноябрь", "budget": "от 5 000 ₽/день", "tip": "Берёшь машину — остров маленький, за неделю объедешь всё", "visa": "Шенген", "tags": {"nature": ["Море и пляжи", "Горы и море"], "climate": ["Тепло +20–28", "Жара +30 и выше"], "budget": ["50–100 000 ₽"], "vibe": ["Полный отдых и пляж", "Культура и история", "Достопримечательности и UNESCO"]}},
    {"country": "Монако", "city": "Монте-Карло", "flag": "🇲🇨", "why": "Самое маленькое богатое государство — казино, яхты и Формула-1.", "highlight": "Казино Монте-Карло, дворец Гримальди и трасса Ф-1", "best_time": "Апрель – октябрь", "budget": "от 15 000 ₽/день", "tip": "Жить лучше в Ницце и ездить в Монако на день — жильё дешевле в разы", "visa": "Шенген", "tags": {"nature": ["Море и пляжи", "Города и культура"], "climate": ["Тепло +20–28", "Жара +30 и выше"], "budget": ["Без ограничений"], "vibe": ["Достопримечательности и UNESCO", "Культура и история"]}},
    {"country": "Албания", "city": "Тирана", "flag": "🇦🇱", "why": "Самая неизведанная страна Европы — дикие пляжи, горы и невероятное гостеприимство.", "highlight": "Ривьера Ионического моря, замок Гирокастры и озеро Охрид", "best_time": "Июнь – сентябрь", "budget": "от 2 500 ₽/день", "tip": "Наличные в лей — карты принимают не везде, банкоматы есть только в городах", "visa": "Безвизово", "tags": {"nature": ["Море и пляжи", "Горы и природа", "Горы и море"], "climate": ["Тепло +20–28", "Жара +30 и выше"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Полный отдых и пляж", "Культура и история"]}},
    {"country": "Молдавия", "city": "Кишинёв", "flag": "🇲🇩", "why": "Самая недооценённая винная страна мира — погреба Милешть Мичь и тихая провинция.", "highlight": "Винные погреба Крикова, монастыри Орхея и Старый Орхей", "best_time": "Май – октябрь", "budget": "от 1 500 ₽/день", "tip": "Экскурсия в подземные винные погреба Крикова — одно из чудес Молдавии", "visa": "Безвизово", "tags": {"nature": ["Города и культура"], "climate": ["Тепло +20–28"], "budget": ["До 50 000 ₽"], "vibe": ["Культура и история", "Гастрономия и рынки"]}},
    {"country": "Беларусь", "city": "Минск", "flag": "🇧🇾", "why": "Советская архитектура, чистые улицы, Беловежская пуща и очень гостеприимные люди.", "highlight": "Беловежская пуща, замок Мир и проспект Независимости", "best_time": "Май – сентябрь", "budget": "от 2 000 ₽/день", "tip": "Безвизовый въезд через аэропорт Минск-2 — но только прямым рейсом", "visa": "Безвизово через аэропорт 30 дней", "tags": {"nature": ["Горы и природа", "Города и культура"], "climate": ["Тепло +20–28", "Прохладно +10–18"], "budget": ["До 50 000 ₽"], "vibe": ["Культура и история", "Достопримечательности и UNESCO"]}},
    {"country": "Россия", "city": "Санкт-Петербург", "flag": "🇷🇺", "why": "Эрмитаж, белые ночи, Байкал и Камчатка — страна которую невозможно объехать за одну жизнь.", "highlight": "Эрмитаж, озеро Байкал, Камчатка и золотое кольцо", "best_time": "Июнь – август", "budget": "от 2 000 ₽/день", "tip": "Белые ночи в Питере в июне — разводные мосты в 1–3 ночи это незабываемо", "visa": "Для граждан России", "tags": {"nature": ["Горы и природа", "Города и культура"], "climate": ["Тепло +20–28", "Прохладно +10–18"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Достопримечательности и UNESCO", "Культура и история", "Экстрим и активность"]}},
    {"country": "Бали", "city": "Денпасар", "flag": "🇮🇩", "why": "Духовная атмосфера, рисовые террасы, сёрфинг и незабываемые закаты.", "highlight": "Храм Танах Лот, вулкан Батур и рисовые поля Тегаллаланг", "best_time": "Апрель – октябрь", "budget": "от 2 500 ₽/день", "tip": "Арендуй скутер — единственный нормальный способ передвижения по острову", "visa": "Безвизово 30 дней", "tags": {"nature": ["Море и пляжи", "Горы и природа", "Горы и море"], "climate": ["Жара +30 и выше"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Полный отдых и пляж", "Культура и история", "Экстрим и активность"]}},
    # ── Европа ────────────────────────────────────────────────────────
    {"country": "Греция", "city": "Афины", "flag": "🇬🇷", "why": "Античные руины, белоснежные острова, оливковое масло и тёплое Средиземноморье.", "highlight": "Акрополь, острова Санторини и Крит, мегаполис Афины", "best_time": "Май – июнь, сентябрь – октябрь", "budget": "от 5 000 ₽/день", "tip": "Острова лучше посещать в мае или сентябре — народу меньше, а море уже тёплое", "visa": "Нужна шенгенская виза", "tags": {"nature": ["Море и пляжи", "Горы и море"], "climate": ["Жара +30 и выше", "Тепло +20–28"], "budget": ["50–100 000 ₽", "100–150 000 ₽"], "vibe": ["Полный отдых и пляж", "Культура и история", "Достопримечательности и UNESCO"]}},
    {"country": "Португалия", "city": "Лиссабон", "flag": "🇵🇹", "why": "Самобытная культура фаду, атлантические пляжи, лучший пасталь де ната в мире и мягкий климат.", "highlight": "Лиссабон с трамваями, Синтра, Порту и сёрфинг на Алгарве", "best_time": "Апрель – июнь, сентябрь – октябрь", "budget": "от 5 500 ₽/день", "tip": "Лиссабон холмистый — купи дневной проездной на трамваи и фуникулёры", "visa": "Нужна шенгенская виза", "tags": {"nature": ["Море и пляжи"], "climate": ["Тепло +20–28", "Умеренный +10–20"], "budget": ["50–100 000 ₽", "100–150 000 ₽"], "vibe": ["Культура и история", "Гастрономия и рынки", "Полный отдых и пляж"]}},
    {"country": "Хорватия", "city": "Загреб", "flag": "🇭🇷", "why": "Лазурная Адриатика, средневековые города-крепости и заповедник Плитвицкие озёра.", "highlight": "Дубровник, Сплит, Плитвице и 1000 островов", "best_time": "Июнь – сентябрь", "budget": "от 6 000 ₽/день", "tip": "Дубровник в июле переполнен — приезжай в июне или сентябре", "visa": "Нужна шенгенская виза", "tags": {"nature": ["Море и пляжи", "Горы и природа"], "climate": ["Жара +30 и выше", "Тепло +20–28"], "budget": ["50–100 000 ₽", "100–150 000 ₽"], "vibe": ["Полный отдых и пляж", "Культура и история", "Достопримечательности и UNESCO"]}},
    {"country": "Черногория", "city": "Подгорица", "flag": "🇲🇪", "why": "Адриатика и горы в одном флаконе — и всё это без шенгена и дешевле Хорватии.", "highlight": "Которская бухта, горы Дурмитор и пляжи Будвы", "best_time": "Июнь – сентябрь", "budget": "от 4 000 ₽/день", "tip": "Черногория принимает россиян без визы до 30 дней — отличный вариант для Адриатики", "visa": "Безвизово 30 дней", "tags": {"nature": ["Море и пляжи", "Горы и природа", "Горы и море"], "climate": ["Жара +30 и выше", "Тепло +20–28"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Полный отдых и пляж", "Культура и история", "Экстрим и активность"]}},
    {"country": "Сербия", "city": "Белград", "flag": "🇷🇸", "why": "Балканская душа, кафаны с живой музыкой, крепость Калемегдан и безвизовый въезд.", "highlight": "Белград — ночная жизнь, крепость и Скадарлия; Нови-Сад", "best_time": "Апрель – октябрь", "budget": "от 3 000 ₽/день", "tip": "Обменивай рубли на динары в городских обменниках — курс лучше чем в банках", "visa": "Безвизово 30 дней", "tags": {"nature": ["Горы и природа"], "climate": ["Тепло +20–28", "Умеренный +10–20"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Культура и история", "Гастрономия и рынки", "Шоппинг и развлечения"]}},
    {"country": "Румыния", "city": "Бухарест", "flag": "🇷🇴", "why": "Замок Дракулы, Трансильванские горы, средневековые города и одна из самых дешёвых стран ЕС.", "highlight": "Замок Бран, Сигишоара, Синая и Карпаты", "best_time": "Май – сентябрь", "budget": "от 3 500 ₽/день", "tip": "Возьми напрокат авто — расстояния большие, а общественный транспорт медленный", "visa": "Нужна шенгенская виза", "tags": {"nature": ["Горы и природа"], "climate": ["Тепло +20–28", "Умеренный +10–20"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Культура и история", "Достопримечательности и UNESCO", "Экстрим и активность"]}},
    {"country": "Словения", "city": "Любляна", "flag": "🇸🇮", "why": "Самая зелёная страна Европы — озеро Блед, Триглав и уютная Любляна.", "highlight": "Озеро Блед, пещеры Постойна, нацпарк Триглав", "best_time": "Июнь – сентябрь", "budget": "от 6 500 ₽/день", "tip": "Карточка Slovenia Green позволяет бесплатно ездить на автобусах до Бледа", "visa": "Нужна шенгенская виза", "tags": {"nature": ["Горы и природа", "Горы и море"], "climate": ["Тепло +20–28", "Умеренный +10–20"], "budget": ["50–100 000 ₽", "100–150 000 ₽"], "vibe": ["Культура и история", "Экстрим и активность", "Романтика и пары"]}},
    {"country": "Северная Македония", "city": "Скопье", "flag": "🇲🇰", "why": "Озеро Охрид — жемчужина Балкан, вкуснейшая кухня и безвизовый въезд.", "highlight": "Охрид — старый город и озеро, Скопье, нацпарк Маврово", "best_time": "Май – октябрь", "budget": "от 2 500 ₽/день", "tip": "Охрид внесён в список UNESCO — гуляй по старому городу рано утром до толпы туристов", "visa": "Безвизово 90 дней", "tags": {"nature": ["Горы и природа"], "climate": ["Тепло +20–28", "Умеренный +10–20"], "budget": ["До 50 000 ₽"], "vibe": ["Культура и история", "Достопримечательности и UNESCO", "Гастрономия и рынки"]}},
    {"country": "Босния и Герцеговина", "city": "Сараево", "flag": "🇧🇦", "why": "Мосты Мостара, баня-лукская кухня и история на каждом шагу — самобытная страна без туристских толп.", "highlight": "Мостар с мостом Стари-Мост, Сараево, водопады Крки", "best_time": "Май – октябрь", "budget": "от 2 500 ₽/день", "tip": "Баня-Лука и Мостар удобнее посещать из Сараево — автобусы ходят регулярно", "visa": "Безвизово 90 дней", "tags": {"nature": ["Горы и природа"], "climate": ["Тепло +20–28", "Умеренный +10–20"], "budget": ["До 50 000 ₽"], "vibe": ["Культура и история", "Достопримечательности и UNESCO"]}},
    {"country": "Эстония", "city": "Таллин", "flag": "🇪🇪", "why": "Лучший средневековый старый город Балтии, цифровое государство и близость к природе.", "highlight": "Старый Таллин — UNESCO, острова Сааремаа и Хийумаа", "best_time": "Июнь – август", "budget": "от 6 000 ₽/день", "tip": "Карта Tallinn Card даёт бесплатный транспорт и вход в музеи — считай сколько сэкономишь", "visa": "Нужна шенгенская виза", "tags": {"nature": ["Горы и природа"], "climate": ["Умеренный +10–20", "Холодно до +10"], "budget": ["50–100 000 ₽", "100–150 000 ₽"], "vibe": ["Культура и история", "Достопримечательности и UNESCO"]}},
    {"country": "Латвия", "city": "Рига", "flag": "🇱🇻", "why": "Рига — столица арт-нуво, янтарный берег Юрмалы и вкуснейший ржаной хлеб.", "highlight": "Старая Рига, Юрмала, замки Сигулды", "best_time": "Июнь – август", "budget": "от 5 500 ₽/день", "tip": "Рынок Центральный рынок Риги — крупнейший в Европе, обязателен к посещению", "visa": "Нужна шенгенская виза", "tags": {"nature": ["Горы и природа"], "climate": ["Умеренный +10–20", "Холодно до +10"], "budget": ["50–100 000 ₽", "100–150 000 ₽"], "vibe": ["Культура и история", "Гастрономия и рынки"]}},
    {"country": "Литва", "city": "Вильнюс", "flag": "🇱🇹", "why": "Самый большой средневековый старый город в Восточной Европе и Куршская коса.", "highlight": "Вильнюс UNESCO, Куршская коса, Тракай", "best_time": "Июнь – август", "budget": "от 5 000 ₽/день", "tip": "Из Вильнюса легко доехать до Куршской косы — бронируй жильё заранее в сезон", "visa": "Нужна шенгенская виза", "tags": {"nature": ["Горы и природа"], "climate": ["Умеренный +10–20", "Холодно до +10"], "budget": ["50–100 000 ₽"], "vibe": ["Культура и история", "Достопримечательности и UNESCO"]}},
    {"country": "Финляндия", "city": "Хельсинки", "flag": "🇫🇮", "why": "Северное сияние, тысячи озёр, лучшая в мире сауна и Санта в Лапландии.", "highlight": "Лапландия и северное сияние, острова Хельсинки, дизайн", "best_time": "Декабрь – март (сияние), июнь – август (природа)", "budget": "от 9 000 ₽/день", "tip": "Финская сауна — обязательный ритуал, общественные сауны в Хельсинки открыты для всех", "visa": "Нужна шенгенская виза", "tags": {"nature": ["Горы и природа"], "climate": ["Умеренный +10–20", "Холодно до +10"], "budget": ["100–150 000 ₽"], "vibe": ["Экстрим и активность", "Культура и история", "Романтика и пары"]}},
    {"country": "Швеция", "city": "Стокгольм", "flag": "🇸🇪", "why": "Дизайн, ABBA, острова-шхеры, IKEA и самый красивый метрополитен в мире.", "highlight": "Стокгольм — Гамла Стан и острова, Абба-музей, Готланд", "best_time": "Июнь – август", "budget": "от 9 000 ₽/день", "tip": "Стокгольмский метро — настоящая галерея, каждая станция оформлена по-своему", "visa": "Нужна шенгенская виза", "tags": {"nature": ["Горы и природа"], "climate": ["Умеренный +10–20", "Холодно до +10"], "budget": ["100–150 000 ₽"], "vibe": ["Культура и история", "Шоппинг и развлечения"]}},
    {"country": "Норвегия", "city": "Осло", "flag": "🇳🇴", "why": "Фьорды, тролли, северное сияние и самый высокий уровень жизни — природа здесь монументальная.", "highlight": "Гейрангерфьорд, Флом, Берген и мыс Нордкап", "best_time": "Июнь – август (фьорды), декабрь – март (сияние)", "budget": "от 12 000 ₽/день", "tip": "Norway in a Nutshell — готовый маршрут по фьордам поездом+паромом+автобусом", "visa": "Нужна шенгенская виза", "tags": {"nature": ["Горы и природа"], "climate": ["Умеренный +10–20", "Холодно до +10"], "budget": ["100–150 000 ₽"], "vibe": ["Экстрим и активность", "Достопримечательности и UNESCO", "Романтика и пары"]}},
    {"country": "Дания", "city": "Копенгаген", "flag": "🇩🇰", "why": "Родина LEGO и «Гадкого утёнка», велосипедная столица мира и лучшие рестораны Европы.", "highlight": "Копенгаген — Русалочка, Тиволи, Нюхавн, Христиания", "best_time": "Май – сентябрь", "budget": "от 10 000 ₽/день", "tip": "Арендуй велосипед — датчане передвигаются только так, и ты сразу станешь местным", "visa": "Нужна шенгенская виза", "tags": {"nature": ["Горы и природа"], "climate": ["Умеренный +10–20"], "budget": ["100–150 000 ₽"], "vibe": ["Культура и история", "Гастрономия и рынки", "Шоппинг и развлечения"]}},
    {"country": "Исландия", "city": "Рейкьявик", "flag": "🇮🇸", "why": "Гейзеры, вулканы, ледники, северное сияние и купание в горячих источниках под звёздным небом.", "highlight": "Голубая лагуна, Золотое кольцо, ледник Ватнайёкюдль", "best_time": "Июнь – август (природа), ноябрь – февраль (сияние)", "budget": "от 12 000 ₽/день", "tip": "Арендуй авто и едь в объезд острова по кольцевой дороге — минимум 7–10 дней", "visa": "Нужна шенгенская виза", "tags": {"nature": ["Горы и природа"], "climate": ["Холодно до +10"], "budget": ["100–150 000 ₽"], "vibe": ["Экстрим и активность", "Романтика и пары", "Достопримечательности и UNESCO"]}},
    {"country": "Швейцария", "city": "Берн", "flag": "🇨🇭", "why": "Альпы, шоколад, сыр, часы и самые красивые виды с горных вершин Европы.", "highlight": "Юнгфрауйох, Лугано, Женева, Интерлакен", "best_time": "Июнь – сентябрь (горы), декабрь – март (лыжи)", "budget": "от 14 000 ₽/день", "tip": "Swiss Travel Pass — единый проездной на поезда, автобусы и большинство подъёмников", "visa": "Нужна шенгенская виза", "tags": {"nature": ["Горы и природа"], "climate": ["Умеренный +10–20", "Холодно до +10"], "budget": ["100–150 000 ₽"], "vibe": ["Экстрим и активность", "Романтика и пары", "Шоппинг и развлечения"]}},
    {"country": "Ирландия", "city": "Дублин", "flag": "🇮🇪", "why": "Зелёные скалы, пабы с живой музыкой, касл-отели и лучший виски в мире.", "highlight": "Скалы Мохер, Кольцо Керри, Дублинский замок", "best_time": "Май – сентябрь", "budget": "от 8 000 ₽/день", "tip": "Арендуй авто — правый руль и левостороннее движение, будь внимателен в первые часы", "visa": "Нужна виза Великобритании или Ирландии", "tags": {"nature": ["Горы и природа"], "climate": ["Умеренный +10–20", "Холодно до +10"], "budget": ["100–150 000 ₽"], "vibe": ["Культура и история", "Гастрономия и рынки"]}},
    {"country": "Мальта", "city": "Валлетта", "flag": "🇲🇹", "why": "Самый маленький остров с историей цивилизаций, лазурная вода Голубой лагуны и вечное лето.", "highlight": "Валлетта UNESCO, Голубая лагуна, Мдина, Азурное окно", "best_time": "Май – октябрь", "budget": "от 5 500 ₽/день", "tip": "Остров Гозо тише и дешевле — паром 25 минут от Мальты", "visa": "Нужна шенгенская виза", "tags": {"nature": ["Море и пляжи"], "climate": ["Жара +30 и выше", "Тепло +20–28"], "budget": ["50–100 000 ₽", "100–150 000 ₽"], "vibe": ["Полный отдых и пляж", "Культура и история", "Достопримечательности и UNESCO"]}},
    {"country": "Люксембург", "city": "Люксембург", "flag": "🇱🇺", "why": "Крошечное богатейшее герцогство с замками, ущельями и бесплатным транспортом по всей стране.", "highlight": "Замки Вианден и Буршайд, ущелье Мюллерталь", "best_time": "Май – сентябрь", "budget": "от 9 000 ₽/день", "tip": "Транспорт в Люксембурге бесплатный для всех — пользуйся автобусами и поездами без билета", "visa": "Нужна шенгенская виза", "tags": {"nature": ["Горы и природа"], "climate": ["Умеренный +10–20"], "budget": ["100–150 000 ₽"], "vibe": ["Культура и история", "Достопримечательности и UNESCO"]}},
    {"country": "Андорра", "city": "Андорра-ла-Велья", "flag": "🇦🇩", "why": "Горнолыжный рай, беспошлинный шоппинг и жизнь между Францией и Испанией.", "highlight": "Горнолыжный курорт Грандвалира, шоппинг Авеню Меричель", "best_time": "Декабрь – март (лыжи), июль – август (хайкинг)", "budget": "от 6 000 ₽/день", "tip": "Бензин, алкоголь и электроника здесь заметно дешевле — европейцы приезжают специально за покупками", "visa": "Нужна шенгенская виза", "tags": {"nature": ["Горы и природа"], "climate": ["Умеренный +10–20", "Холодно до +10"], "budget": ["50–100 000 ₽", "100–150 000 ₽"], "vibe": ["Экстрим и активность", "Шоппинг и развлечения"]}},
    {"country": "Сан-Марино", "city": "Сан-Марино", "flag": "🇸🇲", "why": "Самая древняя республика мира — на вершине горы Монте-Титано, в окружении Италии.", "highlight": "Три башни Гуаита, Честа и Монтале, панорама Апеннин", "best_time": "Апрель – октябрь", "budget": "от 6 000 ₽/день", "tip": "Посещай как однодневную поездку из Римини — всё государство обойдёшь за полдня", "visa": "Нужна шенгенская виза", "tags": {"nature": ["Горы и природа"], "climate": ["Тепло +20–28", "Умеренный +10–20"], "budget": ["50–100 000 ₽", "100–150 000 ₽"], "vibe": ["Культура и история", "Достопримечательности и UNESCO"]}},
    {"country": "Лихтенштейн", "city": "Вадуц", "flag": "🇱🇮", "why": "Одна из самых маленьких и богатых стран мира — альпийские луга, замок на скале и штамп в паспорт за €3.", "highlight": "Замок Вадуц, маршруты по Альпам, деревня Трисен", "best_time": "Июнь – сентябрь", "budget": "от 10 000 ₽/день", "tip": "Проставь уникальный штамп в паспорт в туристическом центре — памятный сувенир за 3 евро", "visa": "Нужна шенгенская виза", "tags": {"nature": ["Горы и природа"], "climate": ["Умеренный +10–20"], "budget": ["100–150 000 ₽"], "vibe": ["Культура и история", "Экстрим и активность"]}},
    # ── Азия ──────────────────────────────────────────────────────────
    {"country": "Индия", "city": "Дели", "flag": "🇮🇳", "why": "Тадж-Махал, специи, красочные фестивали, аюрведа и невероятный контраст культур и эпох.", "highlight": "Тадж-Махал, Варанаси, Джайпур, Гоа, Керала", "best_time": "Октябрь – март", "budget": "от 2 000 ₽/день", "tip": "Пей только бутилированную воду и ешь в местах где видишь очередь из местных — знак качества", "visa": "E-visa онлайн, 30–90 дней", "tags": {"nature": ["Горы и природа", "Море и пляжи", "Джунгли и экзотика"], "climate": ["Жара +30 и выше", "Тепло +20–28"], "budget": ["До 50 000 ₽"], "vibe": ["Культура и история", "Достопримечательности и UNESCO", "Гастрономия и рынки"]}},
    {"country": "Непал", "city": "Катманду", "flag": "🇳🇵", "why": "Эверест, треккинг среди гималайских гигантов, буддийские монастыри и самые добрые люди в мире.", "highlight": "Трек к базовому лагерю Эвереста, Аннапурна, Катманду", "best_time": "Март – май, октябрь – ноябрь", "budget": "от 2 000 ₽/день", "tip": "Разрешение TIMS и нацпарковый пермит обязательны для трекинга — оформляй в Катманду заранее", "visa": "Visa on arrival 15–90 дней", "tags": {"nature": ["Горы и природа"], "climate": ["Тепло +20–28", "Умеренный +10–20", "Холодно до +10"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Экстрим и активность", "Культура и история", "Достопримечательности и UNESCO"]}},
    {"country": "Шри-Ланка", "city": "Коломбо", "flag": "🇱🇰", "why": "Чайные плантации, слоны, руины древних царств и незаезженные пляжи Индийского океана.", "highlight": "Скала Сигирия, поезд в Элла, храм Зуба Будды в Канди", "best_time": "Декабрь – апрель (запад), май – сентябрь (восток)", "budget": "от 3 000 ₽/день", "tip": "Поезд Канди–Элла — один из красивейших маршрутов в мире, бронируй 1-й класс заранее", "visa": "ETA онлайн, 30 дней", "tags": {"nature": ["Море и пляжи", "Горы и природа", "Джунгли и экзотика"], "climate": ["Жара +30 и выше"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Полный отдых и пляж", "Культура и история", "Достопримечательности и UNESCO"]}},
    {"country": "Мальдивы", "city": "Мале", "flag": "🇲🇻", "why": "Самые прозрачные воды в мире, бунгало над лагуной и рифы с мантами и китовыми акулами.", "highlight": "Бунгало overwater, снорклинг с мантами, закаты", "best_time": "Ноябрь – апрель", "budget": "от 15 000 ₽/день", "tip": "Local islands — атоллы с дешёвым жильём и местной жизнью, альтернатива дорогим резортам", "visa": "Безвизово 30 дней", "tags": {"nature": ["Море и пляжи"], "climate": ["Жара +30 и выше"], "budget": ["100–150 000 ₽"], "vibe": ["Полный отдых и пляж", "Романтика и пары"]}},
    {"country": "Мьянма", "city": "Янгон", "flag": "🇲🇲", "why": "Тысячи золотых пагод Багана, плавучие деревни озера Инле и незаезженность — туристов почти нет.", "highlight": "Баган — поля пагод, озеро Инле, Шведагон в Янгоне", "best_time": "Ноябрь – февраль", "budget": "от 2 500 ₽/день", "tip": "Проверяй актуальную обстановку перед поездкой — политическая ситуация нестабильна", "visa": "E-visa онлайн, 28 дней", "tags": {"nature": ["Горы и природа", "Джунгли и экзотика"], "climate": ["Жара +30 и выше", "Тепло +20–28"], "budget": ["До 50 000 ₽"], "vibe": ["Культура и история", "Достопримечательности и UNESCO"]}},
    {"country": "Монголия", "city": "Улан-Батор", "flag": "🇲🇳", "why": "Бескрайние степи, жизнь в юрте, лошади и жизнь кочевников практически не изменилась за тысячи лет.", "highlight": "Гоби, пустыня Гоби, Хубсугул, степи Хустай с лошадьми Пржевальского", "best_time": "Июнь – сентябрь", "budget": "от 3 500 ₽/день", "tip": "Наздак — фестиваль в июле с борьбой, стрельбой и скачками — лучшее время для визита", "visa": "Безвизово 30 дней", "tags": {"nature": ["Горы и природа", "Пустыня и степи"], "climate": ["Тепло +20–28", "Умеренный +10–20"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Экстрим и активность", "Культура и история"]}},
    {"country": "Бутан", "city": "Тхимпху", "flag": "🇧🇹", "why": "Страна счастья в Гималаях — монастырь Гнездо тигра, архерия и полное отсутствие массового туризма.", "highlight": "Монастырь Такцанг (Тигриное гнездо), Пунакха Дзонг", "best_time": "Март – май, сентябрь – ноябрь", "budget": "от 18 000 ₽/день", "tip": "Въезд только через туроператора — обязателен минимальный суточный платёж SDF $100 с человека", "visa": "Только через тур, минимальный суточный взнос", "tags": {"nature": ["Горы и природа"], "climate": ["Умеренный +10–20", "Холодно до +10"], "budget": ["100–150 000 ₽"], "vibe": ["Культура и история", "Экстрим и активность"]}},
    {"country": "Узбекистан", "city": "Ташкент", "flag": "🇺🇿", "why": "Великий шёлковый путь — Самарканд, Бухара, Хива и плов как смысл жизни.", "highlight": "Самарканд — Регистан, Бухара и Хива UNESCO", "best_time": "Март – май, сентябрь – ноябрь", "budget": "от 2 000 ₽/день", "tip": "Везде принимают российские карты МИР — снимай наличные сумы по хорошему курсу", "visa": "Безвизово 30 дней", "tags": {"nature": ["Пустыня и степи"], "climate": ["Жара +30 и выше", "Тепло +20–28"], "budget": ["До 50 000 ₽"], "vibe": ["Культура и история", "Достопримечательности и UNESCO", "Гастрономия и рынки"]}},
    {"country": "Казахстан", "city": "Алматы", "flag": "🇰🇿", "why": "Горы Тянь-Шаня над Алматы, степи, Байконур и самый молодой мегаполис Центральной Азии — Астана.", "highlight": "Алматы — горы Шымбулак, Чарынский каньон, Астана", "best_time": "Май – сентябрь", "budget": "от 3 000 ₽/день", "tip": "Карты МИР работают везде — и снять наличные тенге и расплачиваться безналом", "visa": "Безвизово 30 дней", "tags": {"nature": ["Горы и природа", "Пустыня и степи"], "climate": ["Тепло +20–28", "Умеренный +10–20"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Экстрим и активность", "Культура и история"]}},
    {"country": "Азербайджан", "city": "Баку", "flag": "🇦🇿", "why": "Огненная страна — Пламенные башни Баку, древний Ичери-Шехер и грязевые вулканы.", "highlight": "Старый Баку UNESCO, Гобустан, Шеки, горы Большого Кавказа", "best_time": "Апрель – июнь, сентябрь – октябрь", "budget": "от 3 500 ₽/день", "tip": "Такси через приложения Bolt или Uber — намного дешевле и удобнее уличных", "visa": "E-visa онлайн, 30 дней", "tags": {"nature": ["Горы и природа"], "climate": ["Тепло +20–28", "Умеренный +10–20"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Культура и история", "Гастрономия и рынки", "Достопримечательности и UNESCO"]}},
    {"country": "Иран", "city": "Тегеран", "flag": "🇮🇷", "why": "Персидская империя — Исфахан, Шираз, Персеполис и одни из самых гостеприимных людей в мире.", "highlight": "Исфахан — площадь Накш-э Джахан UNESCO, Персеполис, Шираз", "best_time": "Март – май, сентябрь – ноябрь", "budget": "от 1 500 ₽/день", "tip": "Карты не работают — меняй доллары или евро наличными в обменниках по прилёте", "visa": "Visa on arrival 30 дней (не для граждан США/Великобритании/Канады)", "tags": {"nature": ["Горы и природа", "Пустыня и степи"], "climate": ["Тепло +20–28", "Умеренный +10–20"], "budget": ["До 50 000 ₽"], "vibe": ["Культура и история", "Достопримечательности и UNESCO"]}},
    {"country": "Пакистан", "city": "Исламабад", "flag": "🇵🇰", "why": "K2, Каракорумское шоссе — одна из лучших горных дорог мира и нетронутая природа Гиндукуша.", "highlight": "Гилгит-Балтистан, дорога Каракорум, долина Хунза", "best_time": "Апрель – октябрь", "budget": "от 2 000 ₽/день", "tip": "Оформляй электронную визу заранее через сайт NADRA — обработка занимает до 3 рабочих дней", "visa": "E-visa онлайн, 30–90 дней", "tags": {"nature": ["Горы и природа"], "climate": ["Тепло +20–28", "Умеренный +10–20"], "budget": ["До 50 000 ₽"], "vibe": ["Экстрим и активность", "Культура и история"]}},
    # ── Африка ────────────────────────────────────────────────────────
    {"country": "Кения", "city": "Найроби", "flag": "🇰🇪", "why": "Великая миграция гну, Килиманджаро на горизонте, жирафы на фоне Найроби и сафари мирового класса.", "highlight": "Масаи-Мара — Великая миграция, озеро Накуру, Амбосели", "best_time": "Июль – октябрь (миграция), январь – март", "budget": "от 7 000 ₽/день", "tip": "Сафари в Масаи-Мара бронируй заранее — в период миграции места расхватывают за год", "visa": "E-visa онлайн, 90 дней — $51", "tags": {"nature": ["Джунгли и экзотика", "Пустыня и степи"], "climate": ["Жара +30 и выше", "Тепло +20–28"], "budget": ["50–100 000 ₽", "100–150 000 ₽"], "vibe": ["Экстрим и активность", "Достопримечательности и UNESCO"]}},
    {"country": "Танзания", "city": "Дар-эс-Салам", "flag": "🇹🇿", "why": "Серенгети, Килиманджаро, Занзибар и нетронутые пляжи острова Маафия — Африка в одной стране.", "highlight": "Серенгети, кратер Нгоронгоро, восхождение на Килиманджаро", "best_time": "Июнь – октябрь, январь – февраль", "budget": "от 8 000 ₽/день", "tip": "Занзибар отлично совмещается с сафари — перелёт 30 минут из Дар-эс-Салам", "visa": "E-visa онлайн, 90 дней — $50", "tags": {"nature": ["Джунгли и экзотика", "Пустыня и степи", "Море и пляжи"], "climate": ["Жара +30 и выше"], "budget": ["50–100 000 ₽", "100–150 000 ₽"], "vibe": ["Экстрим и активность", "Достопримечательности и UNESCO", "Полный отдых и пляж"]}},
    {"country": "Занзибар", "city": "Занзибар-сити", "flag": "🇹🇿", "why": "Белоснежные пляжи, бирюзовый океан, Стоун-Таун и аромат пряностей — жемчужина Индийского океана.", "highlight": "Стоун-Таун UNESCO, пляж Нунгви, коралловый риф Менаи", "best_time": "Июнь – октябрь, декабрь – февраль", "budget": "от 5 000 ₽/день", "tip": "Договаривайся о ценах в местных ресторанах заранее — в туристических зонах завышают в 2–3 раза", "visa": "Включена в визу Танзании, $50", "tags": {"nature": ["Море и пляжи"], "climate": ["Жара +30 и выше"], "budget": ["50–100 000 ₽"], "vibe": ["Полный отдых и пляж", "Романтика и пары", "Культура и история"]}},
    {"country": "ЮАР", "city": "Кейптаун", "flag": "🇿🇦", "why": "Мыс Доброй Надежды, сафари Крюгера, виноградники Стелленбоша и многомиллионный Йоханнесбург.", "highlight": "Кейптаун — Столовая гора, Крюгер-парк, Сад маршрутов", "best_time": "Октябрь – апрель (Кейптаун), май – сентябрь (сафари)", "budget": "от 5 000 ₽/день", "tip": "Перемещайся только на авто — общественный транспорт небезопасен вне туристических зон", "visa": "Безвизово 30 дней", "tags": {"nature": ["Джунгли и экзотика", "Горы и природа", "Море и пляжи"], "climate": ["Тепло +20–28"], "budget": ["50–100 000 ₽", "100–150 000 ₽"], "vibe": ["Экстрим и активность", "Культура и история", "Гастрономия и рынки"]}},
    {"country": "Намибия", "city": "Виндхук", "flag": "🇳🇦", "why": "Красные дюны Сосусвлей, мёртвые деревья Дедвлея и самая малонаселённая страна Африки.", "highlight": "Сосусвлей, каньон Фиш-Ривер, берег скелетов, Этоша", "best_time": "Май – октябрь", "budget": "от 6 000 ₽/день", "tip": "Без авто 4x4 не обойтись — большинство достопримечательностей труднодоступны на обычной машине", "visa": "Безвизово 90 дней", "tags": {"nature": ["Пустыня и степи", "Горы и природа"], "climate": ["Тепло +20–28"], "budget": ["50–100 000 ₽", "100–150 000 ₽"], "vibe": ["Экстрим и активность", "Достопримечательности и UNESCO"]}},
    {"country": "Эфиопия", "city": "Аддис-Абеба", "flag": "🇪🇹", "why": "Колыбель человечества — долина Афар, монолитные церкви Лалибэлы и кофе на родине кофе.", "highlight": "Лалибэла UNESCO, долина Омо, Симиенские горы", "best_time": "Октябрь – февраль", "budget": "от 2 500 ₽/день", "tip": "Церемония кофе — национальный ритуал, не отказывайся от приглашения местных", "visa": "E-visa онлайн, 30 дней", "tags": {"nature": ["Горы и природа", "Пустыня и степи"], "climate": ["Тепло +20–28"], "budget": ["До 50 000 ₽"], "vibe": ["Культура и история", "Достопримечательности и UNESCO"]}},
    {"country": "Зимбабве", "city": "Харари", "flag": "🇿🇼", "why": "Водопад Виктория — одно из семи природных чудес света, Великий Зимбабве и сафари Хванге.", "highlight": "Водопад Виктория, Хванге, руины Большого Зимбабве", "best_time": "Апрель – октябрь", "budget": "от 5 000 ₽/день", "tip": "Виктория-Фолс удобно совместить с Замбией — мост через реку Замбези и пешая прогулка к водопаду", "visa": "Visa on arrival или KAZA Univisa $50", "tags": {"nature": ["Джунгли и экзотика", "Горы и природа"], "climate": ["Тепло +20–28"], "budget": ["50–100 000 ₽"], "vibe": ["Экстрим и активность", "Достопримечательности и UNESCO"]}},
    {"country": "Сенегал", "city": "Дакар", "flag": "🇸🇳", "why": "Остров Горе — символ работорговли, розовое озеро Ретба и живая музыка мбалакс на каждом углу.", "highlight": "Остров Горе UNESCO, озеро Ретба, дельта Синэ-Салум", "best_time": "Ноябрь – май", "budget": "от 3 000 ₽/день", "tip": "Торгуйся на рынках — первая цена завышена втрое, улыбка и настойчивость творят чудеса", "visa": "Безвизово 90 дней", "tags": {"nature": ["Море и пляжи", "Джунгли и экзотика"], "climate": ["Жара +30 и выше"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Культура и история", "Гастрономия и рынки"]}},
    {"country": "Гана", "city": "Аккра", "flag": "🇬🇭", "why": "Самая стабильная демократия Западной Африки — замки работорговли, ганский шоколад и клобэ.", "highlight": "Замки Кейп-Кост UNESCO, нацпарк Какум, Аккра", "best_time": "Ноябрь – март", "budget": "от 3 500 ₽/день", "tip": "Гана — отличная точка входа в Западную Африку: безопасно, говорят по-английски", "visa": "E-visa онлайн, 60 дней — $50", "tags": {"nature": ["Джунгли и экзотика"], "climate": ["Жара +30 и выше"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Культура и история", "Достопримечательности и UNESCO"]}},
    # ── Америка ───────────────────────────────────────────────────────
    {"country": "Мексика", "city": "Мехико", "flag": "🇲🇽", "why": "Пирамиды майя, гастрономия UNESCO, пляжи Тихого и Атлантического океанов и сенотес Юкатана.", "highlight": "Чичен-Ица, Тулум, Мехико — музей Фриды Кало, Канкун", "best_time": "Декабрь – апрель", "budget": "от 4 000 ₽/день", "tip": "Мехико — один из лучших гастрономических городов планеты, ешь всё на уличных рынках", "visa": "Безвизово 180 дней", "tags": {"nature": ["Море и пляжи", "Джунгли и экзотика"], "climate": ["Жара +30 и выше", "Тепло +20–28"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Культура и история", "Полный отдых и пляж", "Гастрономия и рынки"]}},
    {"country": "США", "city": "Нью-Йорк", "flag": "🇺🇸", "why": "Большое яблоко, Гранд-Каньон, Йосемити, Лас-Вегас — страна на любой вкус от мегаполисов до дикой природы.", "highlight": "Нью-Йорк, Гранд-Каньон, Йосемити, Майами, Лас-Вегас", "best_time": "Апрель – июнь, сентябрь – ноябрь", "budget": "от 10 000 ₽/день", "tip": "Арендуй авто для национальных парков — общественный транспорт туда не ходит", "visa": "Нужна виза США (B1/B2)", "tags": {"nature": ["Горы и природа", "Море и пляжи", "Пустыня и степи"], "climate": ["Тепло +20–28", "Умеренный +10–20"], "budget": ["100–150 000 ₽"], "vibe": ["Шоппинг и развлечения", "Культура и история", "Достопримечательности и UNESCO"]}},
    {"country": "Канада", "city": "Торонто", "flag": "🇨🇦", "why": "Ниагарский водопад, скалистые горы Банфа, северное сияние и самые дружелюбные люди в мире.", "highlight": "Банф, Ниагарский водопад, Ванкувер, Квебек", "best_time": "Июнь – сентябрь (горы), декабрь – март (лыжи)", "budget": "от 10 000 ₽/день", "tip": "Canada ETA оформляется за $7 онлайн за несколько минут — не путай с визой в США", "visa": "Нужна виза Канады", "tags": {"nature": ["Горы и природа"], "climate": ["Тепло +20–28", "Умеренный +10–20", "Холодно до +10"], "budget": ["100–150 000 ₽"], "vibe": ["Экстрим и активность", "Достопримечательности и UNESCO"]}},
    {"country": "Колумбия", "city": "Богота", "flag": "🇨🇴", "why": "Медельин — город вечной весны, Картахена — карибский жемчуг и лучший в мире кофе в Кофейном регионе.", "highlight": "Картахена, Медельин, Кофейный регион — Salento", "best_time": "Декабрь – март, июль – август", "budget": "от 3 500 ₽/день", "tip": "Медельин сильно изменился — теперь это безопасный и очень интересный город для путешественников", "visa": "Безвизово 90 дней", "tags": {"nature": ["Горы и природа", "Море и пляжи", "Джунгли и экзотика"], "climate": ["Тепло +20–28"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Культура и история", "Гастрономия и рынки", "Полный отдых и пляж"]}},
    {"country": "Перу", "city": "Лима", "flag": "🇵🇪", "why": "Мачу-Пикчу — одно из новых семи чудес света, следы цивилизации инков и лучшая кухня Латинской Америки.", "highlight": "Мачу-Пикчу, трек Инка-Трейл, Куско, Перуанская Амазония", "best_time": "Май – октябрь", "budget": "от 4 000 ₽/день", "tip": "Билеты на Инка-Трейл ограничены — 500 человек в день, бронируй за 6 месяцев", "visa": "Безвизово 90 дней", "tags": {"nature": ["Горы и природа", "Джунгли и экзотика"], "climate": ["Тепло +20–28", "Умеренный +10–20"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Культура и история", "Достопримечательности и UNESCO", "Экстрим и активность"]}},
    {"country": "Аргентина", "city": "Буэнос-Айрес", "flag": "🇦🇷", "why": "Паtagonia, ледник Перито-Морено, Буэнос-Айрес с танго и лучшее мясо в мире.", "highlight": "Перито-Морено, Патагония, Буэнос-Айрес, Игуасу", "best_time": "Ноябрь – март (юг), апрель – ноябрь (север)", "budget": "от 4 500 ₽/день", "tip": "Водопады Игуасу лучше смотреть с аргентинской стороны — обходная дорожка над самыми мощными каскадами", "visa": "Безвизово 90 дней", "tags": {"nature": ["Горы и природа"], "climate": ["Тепло +20–28", "Умеренный +10–20"], "budget": ["50–100 000 ₽"], "vibe": ["Культура и история", "Гастрономия и рынки", "Экстрим и активность"]}},
    {"country": "Бразилия", "city": "Рио-де-Жанейро", "flag": "🇧🇷", "why": "Карнавал, Иисус-Искупитель, Амазонка и Копакабана — самая эмоциональная страна планеты.", "highlight": "Рио — Иисус, Копакабана; Амазонка; Водопады Игуасу", "best_time": "Апрель – июнь, август – октябрь", "budget": "от 5 000 ₽/день", "tip": "В Рио не носи украшения и дорогую технику на улице — соблюдай разумную осторожность", "visa": "Безвизово 90 дней", "tags": {"nature": ["Море и пляжи", "Джунгли и экзотика", "Горы и природа"], "climate": ["Жара +30 и выше", "Тепло +20–28"], "budget": ["50–100 000 ₽"], "vibe": ["Культура и история", "Полный отдых и пляж", "Шоппинг и развлечения"]}},
    {"country": "Чили", "city": "Сантьяго", "flag": "🇨🇱", "why": "Атакама — самая сухая пустыня в мире, Торрес-дель-Пайне и вулканы Патагонии.", "highlight": "Атакама, Торрес-дель-Пайне, остров Пасхи, Вальпараисо", "best_time": "Ноябрь – март", "budget": "от 5 500 ₽/день", "tip": "Атакама — самое звёздное небо на планете, записывайся на ночную астроэкскурсию заранее", "visa": "Безвизово 90 дней", "tags": {"nature": ["Горы и природа", "Пустыня и степи"], "climate": ["Тепло +20–28", "Умеренный +10–20"], "budget": ["50–100 000 ₽", "100–150 000 ₽"], "vibe": ["Экстрим и активность", "Достопримечательности и UNESCO"]}},
    {"country": "Эквадор", "city": "Кито", "flag": "🇪🇨", "why": "Галапагосы, вулкан Котопахи и возможность стоять одновременно в двух полушариях.", "highlight": "Галапагосские острова, вулкан Котопахи, Амазония", "best_time": "Декабрь – май", "budget": "от 4 000 ₽/день", "tip": "Галапагосы — дорого, но оправданно: тур от $100/день, бронируй за несколько месяцев", "visa": "Безвизово 90 дней", "tags": {"nature": ["Горы и природа", "Море и пляжи", "Джунгли и экзотика"], "climate": ["Тепло +20–28"], "budget": ["50–100 000 ₽", "100–150 000 ₽"], "vibe": ["Экстрим и активность", "Достопримечательности и UNESCO"]}},
    {"country": "Боливия", "city": "Ла-Пас", "flag": "🇧🇴", "why": "Солончак Уюни — зеркало мира, самый высокогорный город планеты и озеро Титикака.", "highlight": "Солончак Уюни, озеро Титикака, Ла-Пас", "best_time": "Май – октябрь (сухой сезон)", "budget": "от 2 500 ₽/день", "tip": "Уюни в сезон дождей (декабрь–март) — вода создаёт идеальный зеркальный эффект на солончаке", "visa": "Безвизово 90 дней", "tags": {"nature": ["Горы и природа", "Пустыня и степи"], "climate": ["Умеренный +10–20", "Холодно до +10"], "budget": ["До 50 000 ₽"], "vibe": ["Экстрим и активность", "Достопримечательности и UNESCO"]}},
    {"country": "Коста-Рика", "city": "Сан-Хосе", "flag": "🇨🇷", "why": "Биоразнообразие мирового уровня — 5% всей фауны планеты, вулканы и серфинг на двух океанах.", "highlight": "Нацпарк Мануэль Антонио, вулкан Ареналь, Тортугеро", "best_time": "Декабрь – апрель", "budget": "от 6 000 ₽/день", "tip": "Арендуй авто 4x4 — большинство дорог к паркам грунтовые и размытые в сезон дождей", "visa": "Безвизово 90 дней", "tags": {"nature": ["Горы и природа", "Море и пляжи", "Джунгли и экзотика"], "climate": ["Жара +30 и выше", "Тепло +20–28"], "budget": ["50–100 000 ₽", "100–150 000 ₽"], "vibe": ["Экстрим и активность", "Полный отдых и пляж"]}},
    {"country": "Панама", "city": "Панама-сити", "flag": "🇵🇦", "why": "Панамский канал — инженерное чудо, острова Сан-Блас и мост между двумя океанами.", "highlight": "Панамский канал, острова Сан-Блас, Бокас-дель-Торо", "best_time": "Декабрь – апрель", "budget": "от 5 000 ₽/день", "tip": "Острова Сан-Блас — самостоятельное государство куна, попасть туда можно только с разрешения", "visa": "Безвизово 90 дней", "tags": {"nature": ["Море и пляжи", "Джунгли и экзотика"], "climate": ["Жара +30 и выше"], "budget": ["50–100 000 ₽"], "vibe": ["Культура и история", "Полный отдых и пляж"]}},
    {"country": "Доминиканская Республика", "city": "Санто-Доминго", "flag": "🇩🇴", "why": "Лучший «всё включено» Карибского бассейна, пляжи Пунта-Каны и старейший колониальный город Америки.", "highlight": "Пунта-Кана, Санто-Доминго UNESCO, Самана с китами", "best_time": "Декабрь – апрель", "budget": "от 6 000 ₽/день", "tip": "Декабрь–март — сезон наблюдения за горбатыми китами в заливе Самана, незабываемое зрелище", "visa": "Туристическая карта $10 при въезде", "tags": {"nature": ["Море и пляжи"], "climate": ["Жара +30 и выше"], "budget": ["50–100 000 ₽", "100–150 000 ₽"], "vibe": ["Полный отдых и пляж", "Романтика и пары", "Семья с детьми"]}},
    # ── Океания ───────────────────────────────────────────────────────
    {"country": "Австралия", "city": "Сидней", "flag": "🇦🇺", "why": "Большой Барьерный риф, Улуру, кенгуру, сиднейская опера и самый большой остров-континент.", "highlight": "Большой Барьерный риф, Улуру, Сидней, Тасмания", "best_time": "Сентябрь – ноябрь (юг), апрель – октябрь (север)", "budget": "от 11 000 ₽/день", "tip": "Driving licence обменивается без экзамена — арендуй авто и объезжай вдоль побережья", "visa": "Нужна виза Австралии", "tags": {"nature": ["Море и пляжи", "Горы и природа", "Пустыня и степи"], "climate": ["Жара +30 и выше", "Тепло +20–28"], "budget": ["100–150 000 ₽"], "vibe": ["Полный отдых и пляж", "Экстрим и активность", "Достопримечательности и UNESCO"]}},
    {"country": "Новая Зеландия", "city": "Окленд", "flag": "🇳🇿", "why": "Страна Средиземья, фьорды Милфорд-Саунда, гейзеры и самые добрые люди в южном полушарии.", "highlight": "Милфорд-Саунд, Роторуа, Квинстаун, трек Tongariro", "best_time": "Декабрь – февраль (лето ЮП), март – май (осень)", "budget": "от 10 000 ₽/день", "tip": "Campervan — лучший способ путешествовать: нет проблем с жильём, гибкий маршрут", "visa": "Нужна виза Новой Зеландии", "tags": {"nature": ["Горы и природа", "Море и пляжи"], "climate": ["Тепло +20–28", "Умеренный +10–20"], "budget": ["100–150 000 ₽"], "vibe": ["Экстрим и активность", "Достопримечательности и UNESCO", "Романтика и пары"]}},
    {"country": "Фиджи", "city": "Сува", "flag": "🇫🇯", "why": "Коралловые рифы, 333 острова, деревенские буэ и «Була!» — самая дружелюбная страна Тихого океана.", "highlight": "Острова Ясава, снорклинг в Soft Coral Capital of the World", "best_time": "Июль – сентябрь", "budget": "от 8 000 ₽/день", "tip": "Sevusevu — принеси кава-корень в деревню и тебя встретят как почётного гостя", "visa": "Безвизово 120 дней", "tags": {"nature": ["Море и пляжи"], "climate": ["Жара +30 и выше"], "budget": ["100–150 000 ₽"], "vibe": ["Полный отдых и пляж", "Романтика и пары"]}},
]


_INTERNAL_PASSPORT_COUNTRIES = {"Беларусь", "Казахстан", "Кыргызстан", "Армения"}


def _visa_is_free(dest: dict) -> bool:
    return dest.get("visa", "").startswith("Безвизово")


def _visa_is_evisa(dest: dict) -> bool:
    v = dest.get("visa", "")
    return "E-visa" in v or "ETA" in v or "eTA" in v


def score_destination(dest, answers):
    s = 0
    tags = dest.get("tags", {})
    if answers.get("nature")  in tags.get("nature",  []): s += 3
    if answers.get("climate") in tags.get("climate", []): s += 2
    if answers.get("budget")  in tags.get("budget",  []): s += 2
    if answers.get("vibe")    in tags.get("vibe",    []): s += 3
    if answers.get("activity") == "Трекинг и природа" and answers.get("nature") in ["Горы и природа", "Горы и море"]: s += 1
    if answers.get("activity") == "Пляж и купание"    and answers.get("nature") in ["Море и пляжи",   "Горы и море"]: s += 1
    return s


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user = update.effective_user
    await record_user(user.id, user.username, user.first_name)
    await update.message.reply_text(
        "✈️ Привет! Я твой travel-помощник «Как местный» 🎒\n"
        "Всё что нужно для путешествия — в одном месте:\n\n"
        "🌍 Подберу идеальную страну под твои желания\n"
        "🛂 Визы для 201 страны — без визы, электронная, нужна\n"
        "🗺 Отмечай страны где побывал и смотри статистику\n"
        "🌤 Когда лучше ехать в каждую страну\n"
        "🔤 Переводчик и конвертер валют всегда под рукой\n"
        "🛋 Как попасть в аэропортовый лаундж бесплатно\n"
        "⛔ Куда не пустят со штампом другой страны\n"
        "📖 Полная инструкция для первой самостоятельной поездки\n\n"
        "Выбери раздел 👇",
        reply_markup=get_main_keyboard(),
    )
    return MAIN_MENU


async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    # ◀️ Назад из подменю папок → главное меню
    if text == "◀️ Назад":
        return await go_home(update, context)

    # ── Folder buttons ──────────────────────────────────────────────────────
    if text == "🧭 Планирование":
        await update.message.reply_text(
            "🧭 *Планирование*\n\nВыбери раздел:",
            parse_mode="Markdown",
            reply_markup=get_folder_planning_kb(),
        )
        return MAIN_MENU
    elif text == "🛠 Инструменты":
        await update.message.reply_text(
            "🛠 *Инструменты*\n\nВыбери раздел:",
            parse_mode="Markdown",
            reply_markup=get_folder_tools_kb(),
        )
        return MAIN_MENU
    elif text == "🗺 Мои путешествия":
        await update.message.reply_text(
            "🗺 *Мои путешествия*\n\nВыбери раздел:",
            parse_mode="Markdown",
            reply_markup=get_folder_mytrips_kb(),
        )
        return MAIN_MENU
    elif text == "🏆 Рейтинг путешественников":
        return await show_rating(update, context)
    elif text == "📚 Знания":
        await update.message.reply_text(
            "📚 *Знания*\n\nВыбери раздел:",
            parse_mode="Markdown",
            reply_markup=get_folder_knowledge_kb(),
        )
        return MAIN_MENU
    elif text == "✈️ Услуги":
        await update.message.reply_text(
            "✈️ *Услуги*\n\nВыбери раздел:",
            parse_mode="Markdown",
            reply_markup=get_folder_services_kb(),
        )
        return MAIN_MENU

    # ── Feature buttons ──────────────────────────────────────────────────────
    elif text == "🌍 Подобрать страну":
        context.user_data["answers"] = {}
        context.user_data["step"] = 0
        q = QUESTIONS[0]
        keyboard = [[opt] for opt in q["opts"]] + [[HOME_BTN]]
        await update.message.reply_text(
            q["text"],
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        )
        return ANSWERING
    elif text == "🔮 Страна по судьбе":
        return await destiny_start(update, context)
    elif text == "🎮 Игры":
        return await show_games_menu(update, context)
    elif text == "📖 Инструкция для новичка":
        return await show_help_menu(update, context)
    elif text == "🔤 Переводчик":
        return await start_translator(update, context)
    elif text == "🛂 Визы":
        return await show_visa_menu(update, context)
    elif text == "🎬 Фильмы о путешествиях":
        return await show_movies_menu(update, context)
    elif text == INCOMPATIBLE_BTN:
        return await show_incompatible_menu(update, context)
    elif text == "🚁 Дроны":
        return await drone_menu_handler(update, context)
    elif text == "🌤 Сезоны путешествий":
        return await season_menu_handler(update, context)
    elif text == "🛋 Лаунджи аэропортов":
        return await lounge_menu_handler(update, context)
    elif text == "📚 Путеводители":
        await update.message.reply_text(
            "🚧 В разработке — скоро появится!",
            reply_markup=get_folder_services_kb(),
        )
        return MAIN_MENU
    elif text == "🛃 Оформить визу":
        await update.message.reply_text(
            "🛃 *Оформить визу*\n\n"
            "Помогаем с оформлением виз в любую страну мира 🌍\n"
            "Опытный специалист подготовит документы и сопроводит весь процесс.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✉️ Написать специалисту", url="https://t.me/Maksim1387")],
            ]),
        )
        await update.message.reply_text("🏠 Главное меню:", reply_markup=get_main_keyboard())
        return MAIN_MENU
    elif text == "✈️ Авторские туры":
        return await show_tours_menu(update, context)
    elif text == "🚢 Круизы":
        return await cruise_menu_handler(update, context)
    elif text == "🏛 Чудеса и наследие":
        return await show_wonders_menu(update, context)
    elif text == "⭐ Премиум":
        await update.message.reply_text(
            "⭐ *Как местный Премиум*\n\n"
            "🚧 В разработке — скоро появится!",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([["◀️ Назад", HOME_BTN]], resize_keyboard=True),
        )
        return MAIN_MENU
    elif text == "🤝 Партнёры":
        return await show_partners_menu(update, context)
    elif text == "🆘 Поддержка":
        return await show_support_menu(update, context)
    elif text == CHANNEL_BTN:
        inline_kb = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Перейти в канал", url=CHANNEL_URL)]])
        await update.message.reply_text(
            "Подписывайся на наш канал — там лайфхаки, маршруты и вдохновение для путешествий 🌍✈️",
            reply_markup=inline_kb,
        )
        await update.message.reply_text("Главное меню:", reply_markup=get_main_keyboard())
        return MAIN_MENU
    elif text == SHOP_BTN:
        return await show_shop_menu(update, context)
    else:
        await update.message.reply_text(
            "Выбери один из вариантов 👇",
            reply_markup=get_main_keyboard(),
        )
        return MAIN_MENU


async def show_help_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[topic] for topic in HELP_TOPICS.keys()] + [["◀️ Назад", HOME_BTN]]
    await update.message.reply_text(
        "📖 *Инструкция для новичка*\n\nВыбери тему:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return HELP_MENU


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await show_help_menu(update, context)


async def help_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "◀️ Назад":
        return await show_folder_knowledge(update, context)
    if text in HELP_TOPICS:
        # Baggage topic — show submenu instead of plain text
        if text == "🧳 Багаж и ручная кладь":
            await update.message.reply_text(
                "🧳 *Багаж и ручная кладь*\n\nВыбери раздел:",
                parse_mode="Markdown",
                reply_markup=_BAGGAGE_MENU_KB,
            )
            # depth=1: on the baggage submenu (list of subtopics)
            context.user_data["baggage_depth"] = 1
            return HELP_TOPIC
        keyboard = [["◀️ Назад в меню", HOME_BTN]]
        await update.message.reply_text(
            HELP_TOPICS[text],
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        )
        return HELP_TOPIC
    return await show_help_menu(update, context)


async def help_topic_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    baggage_depth = context.user_data.get("baggage_depth", 0)

    # Baggage subtopic selected → depth 2 (reading subtopic content)
    if text in BAGGAGE_SUBTOPICS:
        context.user_data["baggage_depth"] = 2
        await update.message.reply_text(
            BAGGAGE_SUBTOPICS[text],
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(
                [["◀️ Назад", HOME_BTN]], resize_keyboard=True, one_time_keyboard=True
            ),
        )
        return HELP_TOPIC

    if text == "◀️ Назад":
        if baggage_depth == 2:
            # Back from subtopic content → baggage submenu
            context.user_data["baggage_depth"] = 1
            await update.message.reply_text(
                "🧳 *Багаж и ручная кладь*\n\nВыбери раздел:",
                parse_mode="Markdown",
                reply_markup=_BAGGAGE_MENU_KB,
            )
            return HELP_TOPIC
        else:
            # Back from baggage submenu (depth 1) or any other HELP_TOPIC screen
            # → return to 📖 Инструкция для новичка
            context.user_data.pop("baggage_depth", None)
            return await show_help_menu(update, context)

    context.user_data.pop("baggage_depth", None)
    if text == "◀️ Назад в меню":
        return await show_help_menu(update, context)
    return await show_help_menu(update, context)


async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("step", 0)
    answers = context.user_data.get("answers", {})
    text = update.message.text

    valid_opts = QUESTIONS[step]["opts"]
    if text not in valid_opts:
        keyboard = [[opt] for opt in valid_opts] + [[HOME_BTN]]
        await update.message.reply_text(
            "Выбери один из вариантов 👇",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        )
        return ANSWERING

    answers[QUESTIONS[step]["id"]] = text
    context.user_data["answers"] = answers
    step += 1
    context.user_data["step"] = step

    if step < len(QUESTIONS):
        q = QUESTIONS[step]
        keyboard = [[opt] for opt in q["opts"]] + [[HOME_BTN]]
        progress = f"Вопрос {step + 1} из {len(QUESTIONS)}\n\n"
        await update.message.reply_text(
            progress + q["text"],
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        )
        return ANSWERING
    else:
        return await show_result(update, context)


async def show_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answers = context.user_data.get("answers", {})
    await update.message.reply_text("Анализирую твои ответы... 🔍", reply_markup=ReplyKeyboardRemove())

    visa_answer  = answers.get("visa", "")
    passport_ans = answers.get("passport", "")

    # Hard filters — applied before scoring so only eligible countries remain
    pool = DESTINATIONS[:]
    if passport_ans == "Нет загранпаспорта":
        pool = [d for d in pool if d["country"] in _INTERNAL_PASSPORT_COUNTRIES]
    elif visa_answer == "Только безвизовые страны":
        pool = [d for d in pool if _visa_is_free(d)]
    elif visa_answer == "Только e-visa онлайн":
        pool = [d for d in pool if _visa_is_evisa(d)]

    if not pool:
        await update.message.reply_text(
            "😔 По твоим критериям не нашлось подходящих стран. Попробуй изменить фильтры.",
            reply_markup=get_main_keyboard(),
        )
        return MAIN_MENU

    scored = sorted(pool, key=lambda d: score_destination(d, answers), reverse=True)
    rec = scored[0]
    alt = scored[1] if len(scored) > 1 else None

    result = (
        f"{rec['flag']} *Твоё идеальное направление — {rec['country']}*\n\n"
        f"🏙 *Старт из:* {rec['city']}\n"
        f"💡 *Почему:* {rec['why']}\n"
        f"✨ *Главная фишка:* {rec['highlight']}\n"
        f"📅 *Лучшее время:* {rec['best_time']}\n"
        f"💰 *Бюджет:* {rec['budget']}\n"
        f"🛂 *Виза:* {rec['visa']}\n"
        f"🎯 *Совет эксперта:* {rec['tip']}"
    )
    if alt:
        result += f"\n\nТакже подойдёт: {alt['flag']} {alt['country']}"

    await update.message.reply_text(result, parse_mode="Markdown", reply_markup=get_main_keyboard())
    return MAIN_MENU


async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        data = json.loads(update.message.web_app_data.data)
    except (json.JSONDecodeError, AttributeError):
        data = {}

    source = data.get("source", "")

    # ── 🗺 Мои страны (index.html) ──────────────────────────────────────────
    if source == "countries":
        count = data.get("count", len(data.get("visited", [])))
        total = data.get("total", 201)
        user = update.effective_user
        upsert_countries_count(user.id, user.username, user.first_name, count)
        await update.message.reply_text(
            f"✅ Список стран сохранён! Посещено: *{count}* стран из {total}",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(),
        )
        return MAIN_MENU

    # ── 🇷🇺 Путешествия по России (russia.html) ──────────────────────────────
    if source == "regions":
        count = data.get("count", len(data.get("visited", [])))
        total = data.get("total", 89)
        await update.message.reply_text(
            f"✅ Список регионов сохранён! Посещено: *{count}* регионов из {total}",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(),
        )
        return MAIN_MENU

    # ── Splitwise WebApp ─────────────────────────────────────────────────────
    if data.get("type") == "splitwise_export":
        await update.message.reply_text("✅ Данные сохранены!", reply_markup=get_main_keyboard())
        return MAIN_MENU

    # ── Устаревший формат index.html (visited: [...]) ────────────────────────
    countries = data.get("countries", data.get("visited", []))
    if countries:
        count = len(countries)
        await update.message.reply_text(
            f"✅ Список стран сохранён! Посещено: *{count}* стран из 201",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(),
        )
        return MAIN_MENU

    await update.message.reply_text("✅ Данные сохранены!", reply_markup=get_main_keyboard())
    return MAIN_MENU


async def start_translator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔤 *Переводчик*\n\n"
        "Напиши слово или фразу — переведу автоматически:\n"
        "🇷🇺 русский → 🇬🇧 английский\n"
        "🇬🇧 английский → 🇷🇺 русский\n\n"
        "_Язык определяется автоматически по наличию кириллицы._",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([["◀️ Назад", HOME_BTN]], resize_keyboard=True, one_time_keyboard=False),
    )
    return TRANSLATING


async def handle_translation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "◀️ Назад":
        return await show_folder_tools(update, context)
    if not text:
        return TRANSLATING

    await update.message.reply_chat_action("typing")

    try:
        translated, src, dst = await translate_text(text)
        flag = {"ru": "🇷🇺", "en": "🇬🇧"}
        lang_name = {"ru": "Русский", "en": "Английский"}
        reply = (
            f"{flag[src]} *{lang_name[src]}* → {flag[dst]} *{lang_name[dst]}*\n\n"
            f"*Оригинал:* {text}\n"
            f"*Перевод:* {translated}"
        )
    except Exception:
        reply = "⚠️ Не удалось получить перевод. Проверь соединение и попробуй ещё раз."

    await update.message.reply_text(
        reply,
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            [["🔤 Перевести ещё"], [HOME_BTN]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )
    return TRANSLATING


_VISA_MAIN_KB = [
    ["✅ Без визы"],
    ["📱 Электронная виза"],
    ["📋 Нужна виза"],
    ["💡 Полезная информация"],
    ["🚧 Оформить визу"],
    ["◀️ Назад", HOME_BTN],
]


async def show_visa_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛂 *Визы для граждан России*\n\nВыбери категорию:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(_VISA_MAIN_KB, resize_keyboard=True, one_time_keyboard=True),
    )
    return VISA_MENU


async def visa_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "◀️ Назад":
        return await show_folder_planning(update, context)
    if text == "🚧 Оформить визу":
        await update.message.reply_text(
            "🛃 *Оформить визу*\n\n"
            "Помогаем с оформлением виз в любую страну мира 🌍\n"
            "Опытный специалист подготовит документы и сопроводит весь процесс.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✉️ Написать специалисту", url="https://t.me/Maksim1387")],
            ]),
        )
        await update.message.reply_text(
            "🏠 Главное меню:",
            reply_markup=ReplyKeyboardMarkup(_VISA_MAIN_KB, resize_keyboard=True),
        )
        return VISA_MENU
    if text in VISAS:
        content = VISAS[text]
        back_kb = ReplyKeyboardMarkup([["◀️ Назад", HOME_BTN]], resize_keyboard=True, one_time_keyboard=True)
        if len(content) > 4000:
            chunks = [content[i:i+4000] for i in range(0, len(content), 4000)]
            for chunk in chunks[:-1]:
                await update.message.reply_text(chunk, parse_mode="Markdown")
            await update.message.reply_text(chunks[-1], parse_mode="Markdown", reply_markup=back_kb)
        else:
            await update.message.reply_text(content, parse_mode="Markdown", reply_markup=back_kb)
        return VISA_CATEGORY
    return await show_visa_menu(update, context)


async def visa_category_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "◀️ Назад":
        return await show_visa_menu(update, context)
    return await show_visa_menu(update, context)


## ── CRUISES ──────────────────────────────────────────────────────────────────

CRUISE_BTNS = [
    "🌊 Что такое круиз",
    "🚢 Крупнейшие лайнеры мира",
    "🌍 Популярные маршруты",
    "💰 Цены и экономия",
    "🏆 Топ круизных компаний",
    "💡 Лайфхаки",
]

CRUISE_DATA: dict[str, str] = {

"🌊 Что такое круиз": (
    "🌊 *Что такое круиз — гид для новичков*\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🛳 *Как устроен круиз*\n"
    "Круиз — это путешествие на большом пассажирском лайнере с остановками в разных портах. "
    "Лайнер — это плавучий отель: на борту рестораны, бассейны, театры, спа и казино. "
    "Каждый день ты просыпаешься в новом городе или стране.\n\n"
    "✅ *Что входит в стоимость каюты*\n"
    "• Проживание в каюте\n"
    "• Основное питание (завтрак, обед, ужин в основных ресторанах)\n"
    "• Большинство развлечений на борту: шоу, бассейны, спортзал, детские клубы\n"
    "• Переезд между портами — лайнер везёт тебя сам\n"
    "• Круглосуточный доступ к закускам и кофе (в базовых точках)\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🛏 *Типы кают*\n\n"
    "🪟 *Внутренняя каюта* — без окон\n"
    "Самая дешёвая. Подходит тем, кто проводит на борту минимум времени. "
    "Иллюминатора нет, но каюта уютная и тихая.\n"
    "💰 Цена: от $50–80/ночь на человека\n\n"
    "🌊 *С иллюминатором* — небольшое фиксированное окно\n"
    "Видно море, есть естественный свет. Хороший баланс цена/качество.\n"
    "💰 Цена: от $70–110/ночь на человека\n\n"
    "🏖 *С балконом* — самая популярная\n"
    "Личный балкон с видом на море. Можно завтракать на воздухе, наблюдать закаты. "
    "Рекомендуется для первого круиза.\n"
    "💰 Цена: от $100–180/ночь на человека\n\n"
    "👑 *Люкс / Suite*\n"
    "Гостиная + спальня + большой балкон. Часто включает личного дворецкого, "
    "приоритетную посадку, ужины с капитаном, эксклюзивные зоны на палубе.\n"
    "💰 Цена: от $300–500+/ночь на человека\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "❌ *Что НЕ входит в стоимость*\n"
    "• 🍸 Алкоголь и premium напитки — отдельно или пакет ~$70–100/день\n"
    "• 🏝 Береговые экскурсии — от $30 до $200+ за экскурсию\n"
    "• 💆 Спа и массаж — от $80 за сеанс\n"
    "• 🌐 Интернет — от $15–30/день или пакет на весь рейс\n"
    "• 💰 Чаевые (gratuities) — $16–20/ночь с человека, обычно добавляются автоматически\n"
    "• 🍽 Рестораны specialty dining — от $30–50 за вечер\n"
    "• 📸 Профессиональные фото, магазины, казино\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🗺 *Как выбрать каюту*\n"
    "• Мидшип (середина) — меньше качки, идеально при морской болезни\n"
    "• Нижние палубы — меньше качки, ближе к воде\n"
    "• Избегай кают под дискотекой, рядом с машинным отделением\n"
    "• Каюты у лифта — шумно в любое время суток\n"
    "• Проверяй deck plan на сайте лайнера перед бронированием\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🌊 *Морская болезнь — как справиться*\n"
    "• Выбирай крупные лайнеры (6 000+ пассажиров) — они устойчивее\n"
    "• Каюта в середине корабля (мидшип), ниже ватерлинии\n"
    "• Препарат: драмина, скополамин (пластырь за ухо), имбирные таблетки\n"
    "• На борту: точки акупрессуры на запястье — браслеты Sea-Band\n"
    "• Выходи на открытую палубу, смотри на горизонт\n"
    "• Ешь небольшими порциями, избегай жирного и алкоголя\n\n"
    "⚠️ _Актуально на 2025–2026. Цены меняются — проверяй на официальных сайтах._"
),

"🚢 Крупнейшие лайнеры мира": (
    "🚢 *Крупнейшие лайнеры мира 2025–2026*\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🥇 *Icon of the Seas — Royal Caribbean*\n"
    "📏 Длина: 365 м | 20 палуб | 7 600 пассажиров\n"
    "🌊 Самый большой круизный лайнер в мире (с 2024)\n"
    "✨ На борту: 7 тематических районов, крупнейший аквапарк в море, "
    "20+ ресторанов, казино, ледовый каток, скалодром\n"
    "🗺 Маршруты: Карибский бассейн из Майами\n"
    "💰 Цены от: $800–1 200/чел за 7 ночей (внутренняя каюта)\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🥈 *Wonder of the Seas — Royal Caribbean*\n"
    "📏 Длина: 362 м | 18 палуб | 6 988 пассажиров\n"
    "✨ 8 тематических районов, самый длинный горка-слайд на корабле, "
    "Ultimate Abyss (10-палубная горка), Central Park с живыми деревьями\n"
    "🗺 Маршруты: Карибский бассейн, Средиземноморье\n"
    "💰 Цены от: $750–1 100/чел за 7 ночей\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🥉 *Symphony of the Seas — Royal Caribbean*\n"
    "📏 Длина: 362 м | 18 палуб | 6 680 пассажиров\n"
    "✨ 20 ресторанов, лазерный лабиринт, FlowRider (сёрфинг-симулятор), "
    "2 300 м² спа-центр, Bionic Bar (бар с роботами)\n"
    "🗺 Маршруты: Средиземноморье, Карибский бассейн\n"
    "💰 Цены от: $700–1 050/чел за 7 ночей\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "4️⃣ *MSC World Europa — MSC Cruises*\n"
    "📏 Длина: 333 м | 22 палубы | 6 762 пассажира\n"
    "✨ Первый в мире лайнер на сжиженном природном газе. "
    "Аквапарк, боулинг, ледовый каток, 13 ресторанов, казино\n"
    "🗺 Маршруты: Средиземноморье, Персидский залив (ОАЭ, Катар)\n"
    "💰 Цены от: $600–900/чел за 7 ночей\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "5️⃣ *Utopia of the Seas — Royal Caribbean* (2024)\n"
    "📏 6 000+ пассажиров\n"
    "✨ Сосредоточен на коротких 3–5-дневных маршрутах, новое поколение развлечений\n"
    "🗺 Маршруты: Багамы и Карибский бассейн\n"
    "💰 Цены от: $400–700/чел за 3–4 ночи\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "💡 *Сравнение размеров*\n"
    "Icon of the Seas ≈ 5 Титаников по водоизмещению\n"
    "Вмещает больше людей, чем многие маленькие города\n\n"
    "⚠️ _Актуально на 2025–2026. Цены меняются — проверяй на официальных сайтах._"
),

"🌍 Популярные маршруты": (
    "🌍 *Популярные круизные маршруты 2025–2026*\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🇮🇹🇬🇷🇭🇷 *Средиземноморье*\n"
    "🏖 Порты: Барселона, Рим (Чивитавеккья), Неаполь, Афины (Пирей), "
    "Дубровник, Венеция, Марсель, Мальта\n"
    "📅 Лучший сезон: апрель–октябрь (пик — июнь–август)\n"
    "⏱ Длительность: 7–14 дней\n"
    "💰 Цены: от $600–900/чел за 7 ночей\n"
    "💡 Совет: май и сентябрь — лучший баланс погоды и цены, меньше толп\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🌴🏝 *Карибский бассейн*\n"
    "🏖 Острова: Ямайка, Багамы, Кайман, Пуэрто-Рико, Сен-Мартен, Барбадос, Кюрасао\n"
    "📅 Лучший сезон: декабрь–апрель (сухой сезон, нет ураганов)\n"
    "⏱ Длительность: 3–14 дней\n"
    "💰 Цены: от $500–800/чел за 7 ночей\n"
    "💡 Совет: Восточные Карибы — более уединённые острова; "
    "Западные — Мексика и Белиз\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🇳🇴🇮🇸 *Северная Европа и фьорды*\n"
    "🏖 Порты: Берген, Флом, Гейрангер, Осло, Копенгаген, "
    "Стокгольм, Таллин, Санкт-Петербург (сейчас ограничен)\n"
    "📅 Лучший сезон: май–сентябрь\n"
    "⏱ Длительность: 7–14 дней\n"
    "💰 Цены: от $700–1 100/чел за 7 ночей\n"
    "💡 Совет: июнь–июль — белые ночи в Норвегии, незабываемое зрелище\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🇯🇵🇸🇬🇭🇰 *Азия*\n"
    "🏖 Маршруты: Сингапур → Бангкок → Вьетнам; Токио → Осака → Нагасаки; "
    "Сингапур → Бали → Австралия\n"
    "📅 Лучший сезон: ноябрь–март\n"
    "⏱ Длительность: 7–21 день\n"
    "💰 Цены: от $700–1 200/чел за 7 ночей\n"
    "💡 Совет: Япония осенью (октябрь–ноябрь) — сезон листьев момидзи, "
    "один из красивейших маршрутов\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🌎 *Кругосветные круизы*\n"
    "🗺 Маршрут: обычно начинается в США или Великобритании, "
    "охватывает 30–50 стран за 70–120 дней\n"
    "📅 Лучший сезон: январь–апрель (Северное полушарие уходит от зимы)\n"
    "⏱ Длительность: 70–180 дней\n"
    "💰 Цены: от $10 000 до $150 000+ на человека\n"
    "💡 Самые известные: Cunard Queen Mary 2, Princess World Cruise\n\n"
    "⚠️ _Актуально на 2025–2026. Цены меняются — проверяй на официальных сайтах._"
),

"💰 Цены и экономия": (
    "💰 *Цены и экономия на круизах 2025–2026*\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "💳 *Средние цены по типам кают (7 ночей, на человека)*\n"
    "🪟 Внутренняя каюта: $500–900\n"
    "🌊 С иллюминатором: $700–1 200\n"
    "🏖 С балконом: $900–1 800\n"
    "👑 Люкс / Suite: $2 500–8 000+\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "📅 *Когда бронировать выгоднее*\n\n"
    "🐦 *Early Bird (за 6–12 месяцев)*\n"
    "• Скидки 20–40% от стандартной цены\n"
    "• Лучший выбор кают\n"
    "• Часто включают бонусы: бесплатный алкогольный пакет, чаевые, Wi-Fi\n"
    "• Лучшая стратегия для лета и школьных каникул\n\n"
    "⏰ *Last Minute (за 4–8 недель)*\n"
    "• Скидки до 50–70% на оставшиеся каюты\n"
    "• Меньший выбор — часто только внутренние каюты\n"
    "• Риск: нет нужных дат или маршрутов\n"
    "• Подходит гибким путешественникам без детей\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🍽 *Всё включено vs базовый тариф*\n"
    "• Базовый: каюта + питание в основных ресторанах\n"
    "• All Inclusive (у MSC, Norwegian, Virgin): +напитки, чаевые, Wi-Fi\n"
    "• Часто выгоднее взять пакет сразу: экономия $200–400 за круиз\n"
    "• Norwegian: пакет Free at Sea — один из лучших\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🔍 *Скрытые расходы — о чём не предупреждают*\n"
    "• Чаевые: $16–20/ночь с человека — обычно добавляются автоматически\n"
    "• Портовые сборы: $100–300 за круиз (указываются отдельно)\n"
    "• Перелёт до порта отправления — часто дороже самого круиза\n"
    "• Отель за день до/после круиза\n"
    "• Экскурсии в портах: $30–200+ за экскурсию\n"
    "• Алкоголь: без пакета $8–15 за коктейль\n"
    "• Wi-Fi: $15–30/день или $100–200 за рейс\n"
    "• Specialty рестораны: $30–60 за вечер\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🌐 *Лучшие сайты для бронирования*\n"
    "• cruiseline.com — сравнение цен всех компаний\n"
    "• expedia.com/cruises — удобный поиск с фильтрами\n"
    "• royalcaribbean.com — прямая продажа RCL\n"
    "• msccruises.com — прямая продажа MSC\n"
    "• ncl.com — Norwegian, пакеты Free at Sea\n"
    "• costacruises.com — Costa, много маршрутов из Европы\n"
    "💡 Прямое бронирование = лучший сервис при изменениях и отменах\n\n"
    "⚠️ _Актуально на 2025–2026. Цены меняются — проверяй на официальных сайтах._"
),

"🏆 Топ круизных компаний": (
    "🏆 *Топ круизных компаний 2025–2026*\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "👑 *Royal Caribbean International*\n"
    "🎯 Аудитория: семьи, молодёжь, любители развлечений\n"
    "💰 Сегмент: средний и выше среднего\n"
    "✨ Особенности: самые инновационные лайнеры мира (Icon, Wonder), "
    "максимальные развлечения на борту, аквапарки, скалодромы, сёрфинг\n"
    "🗺 Маршруты: Карибы, Средиземноморье, Европа, Азия, Аляска\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🌍 *MSC Cruises*\n"
    "🎯 Аудитория: европейцы, семьи, ценители атмосферы\n"
    "💰 Сегмент: бюджетный и средний\n"
    "✨ Особенности: хороший выбор из Европы, много маршрутов "
    "по Средиземноморью, сильная кухня, программа лояльности Voyagers Club\n"
    "🗺 Маршруты: Средиземноморье, Карибы, ОАЭ, Бразилия\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🎉 *Norwegian Cruise Line (NCL)*\n"
    "🎯 Аудитория: взрослые, пары, любители свободы\n"
    "💰 Сегмент: средний\n"
    "✨ Особенности: Freestyle Cruising — никакого дресс-кода и расписания, "
    "пакет Free at Sea (напитки + Wi-Fi + экскурсии), собственный остров "
    "Great Stirrup Cay\n"
    "🗺 Маршруты: Карибы, Европа, Аляска, Гавайи\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🌞 *Costa Cruises*\n"
    "🎯 Аудитория: итальянская атмосфера, европейцы, семьи\n"
    "💰 Сегмент: бюджетный\n"
    "✨ Особенности: итальянский стиль и кухня, много маршрутов "
    "из Генуи и Барселоны, доступные цены, активная вечерняя программа\n"
    "🗺 Маршруты: Средиземноморье, Канарские острова, Дубай\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "⭐ *Celebrity Cruises*\n"
    "🎯 Аудитория: взрослые, пары, гурманы\n"
    "💰 Сегмент: премиум\n"
    "✨ Особенности: современный элегантный дизайн, ресторанная кухня "
    "от именитых шеф-поваров, программа Always Included (напитки + чаевые + Wi-Fi)\n"
    "🗺 Маршруты: Европа, Карибы, Азия, Галапагосы\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🎊 *Carnival Cruise Line*\n"
    "🎯 Аудитория: молодёжь, компании друзей, бюджетные путешественники\n"
    "💰 Сегмент: бюджетный и средний\n"
    "✨ Особенности: Fun Ships — самые весёлые и шумные лайнеры, "
    "короткие 3–5-дневные маршруты, много акций и скидок\n"
    "🗺 Маршруты: Карибы, Мексика, Багамы, Бермуды\n\n"
    "⚠️ _Актуально на 2025–2026. Цены меняются — проверяй на официальных сайтах._"
),

"💡 Лайфхаки": (
    "💡 *Лайфхаки для круизных путешествий*\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "💸 *Как сэкономить на круизе*\n"
    "• Бронируй за 6–12 месяцев — early bird даёт скидку 20–40%\n"
    "• Выбирай отправление в будни, а не выходные — дешевле\n"
    "• Рейсы repositioning — когда лайнер меняет регион: скидки 50–70%\n"
    "• Карибы дешевле Средиземноморья при том же уровне комфорта\n"
    "• Покупай алкогольный пакет ДО посадки — обычно дешевле на 15–20%\n"
    "• Экскурсии в портах: бронируй самостоятельно, не через лайнер — "
    "в 1.5–2 раза дешевле (проверяй Viator, GetYourGuide)\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🧳 *Что брать с собой*\n"
    "• Морской/дорожный адаптер (на корабле обычно US-розетки)\n"
    "• Удлинитель без функции питания — на большинстве лайнеров разрешён\n"
    "• Таблетки от морской болезни (даже если не страдаешь — на всякий случай)\n"
    "• Магниты для крепления вещей к стальным стенам каюты\n"
    "• Ночник/фонарик — внутренние каюты абсолютно тёмные\n"
    "• Гидрофляга — напитки дорогие, а вода на борту бесплатная\n"
    "• Полотенца-прищепки — для шезлонгов на палубе (зарезервировать место)\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "📋 *Правила посадки и документы*\n"
    "• Онлайн-регистрация (check-in) — открывается за 30–90 дней, сделай сразу\n"
    "• Приезжай в порт за 2–3 часа до отплытия\n"
    "• Документы: загранпаспорт (срок 6 мес+), круизный билет (e-ticket), "
    "медицинская страховка, кредитная карта для открытия бортового счёта\n"
    "• Sea Pass Card (ключ-карта) = удостоверение личности на борту + оплата\n"
    "• Алкоголь с собой: обычно разрешено 1–2 бутылки вина при посадке\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🌐 *Интернет на борту*\n"
    "• Стандарт: $15–30/день или $100–200 за весь рейс\n"
    "• Royal Caribbean, NCL: пакеты от $20/день с безлимитом\n"
    "• Starlink уже на многих лайнерах — скорость стала значительно лучше\n"
    "• Альтернатива: покупай местную SIM-карту в каждом порту\n"
    "• В портах: ищи бесплатный Wi-Fi в кафе на берегу\n"
    "• Telegram и мессенджеры работают в большинстве пакетов\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "💰 *Чаевые — сколько и кому*\n"
    "• Большинство лайнеров добавляют автоматически: $16–20/ночь с человека\n"
    "• Это покрывает: стюарда каюты, официантов, вспомогательный персонал\n"
    "• Дополнительно: барменам $1–2 за коктейль, в спа — 15–18%\n"
    "• Можно скорректировать на борту на стойке Guest Services\n"
    "• Norwegian и Celebrity включают чаевые в пакет — удобнее\n\n"
    "⚠️ _Актуально на 2025–2026. Цены меняются — проверяй на официальных сайтах._"
),

}


# ═══════════════════════════════════════════════════════════════
#  🏛 ЧУДЕСА И НАСЛЕДИЕ — данные
# ═══════════════════════════════════════════════════════════════

# Главное меню раздела — 2 кнопки
WONDERS_BTNS = [
    "🌟 7 чудес света",
    "🗺 Наследие ЮНЕСКО по регионам",
]

# Подменю 7 чудес — каждое отдельной кнопкой
WONDERS_SEVEN_BTNS = [
    "🇨🇳 Великая Китайская стена",
    "🇮🇳 Тадж-Махал",
    "🇧🇷 Статуя Христа Искупителя",
    "🇲🇽 Пирамида Чичен-Ица",
    "🇵🇪 Мачу-Пикчу",
    "🇯🇴 Петра",
    "🇮🇹 Колизей",
]

UNESCO_REGION_BTNS = [
    "🌏 Азия",
    "🌍 Европа",
    "🌍 Ближний Восток и Африка",
    "🌎 Америка",
    "🏔 СНГ и Кавказ",
]

WONDERS_DATA: dict[str, str] = {

"🇨🇳 Великая Китайская стена": (
    "🇨🇳 *Великая Китайская стена*\n\n"
    "📍 *Страна:* Китай\n"
    "📅 *Строительство:* VII в. до н. э. — XVII в. н. э. (более 2 000 лет)\n\n"
    "📖 *История*\n"
    "Стена строилась несколькими династиями — Цинь, Хань, Мин — как защита от кочевников с севера. "
    "Общая длина всех участков превышает 21 000 км. Наиболее сохранившиеся секции относятся к эпохе Мин (XIV–XVII вв.).\n\n"
    "✨ *Интересные факты*\n"
    "• Легенда о том, что стену видно из космоса — миф: невооружённым глазом она не различима с орбиты\n"
    "• Строительство унесло жизни сотен тысяч рабочих — их останки замуровывали прямо в стену\n"
    "• Ширина стены позволяла проехать пяти лошадям в ряд\n"
    "• ЮНЕСКО внесло объект в список наследия в 1987 году\n\n"
    "🚗 *Как добраться*\n"
    "Из Пекина: автобус № 877 до Бадалина (1,5 ч) или такси/тур (~500 ¥). "
    "Мутяньюй — живописнее и менее многолюдно, удобнее с гидом или на такси.\n\n"
    "🌤 *Лучшее время*\n"
    "Апрель–май и сентябрь–октябрь — мягкая погода, нет летнего пекла. "
    "Зимой пустынно и красиво, но холодно (до −15 °C).\n\n"
    "🎟 *Цена входа*\n"
    "Бадалин: 40–65 ¥ (≈ 500–900 ₽) в зависимости от сезона.\n"
    "Мутяньюй: 54–65 ¥ + подъёмник 100 ¥.\n\n"
    "⚠️ _Данные актуальны на 2025–2026 г._"
),

"🇮🇳 Тадж-Махал": (
    "🇮🇳 *Тадж-Махал*\n\n"
    "📍 *Страна:* Индия, г. Агра\n"
    "📅 *Построен:* 1632–1653 гг.\n\n"
    "📖 *История*\n"
    "Мавзолей возведён императором Шах-Джаханом в память о любимой жене Мумтаз-Махал, "
    "умершей при родах. Над проектом работали более 20 000 мастеров из Индии, Персии и Средней Азии. "
    "ЮНЕСКО — 1983 г.\n\n"
    "✨ *Интересные факты*\n"
    "• Белый мрамор меняет цвет в зависимости от освещения: розовый на рассвете, золотистый в сумерках\n"
    "• Четыре минарета намеренно наклонены наружу — чтобы при землетрясении упали не на мавзолей\n"
    "• Строительство обошлось в 32 млн рупий — по ценам XVII века\n"
    "• Шах-Джахан провёл последние годы жизни в заточении в Агрском форте, видя Тадж-Махал из окна\n\n"
    "🚗 *Как добраться*\n"
    "Из Дели: скоростной поезд Gatimaan Express (1 ч 40 мин, ~700–1200 ₹). "
    "Такси или тур из Дели — 4–5 часов. Вход с восточных и западных ворот.\n\n"
    "🌤 *Лучшее время*\n"
    "Октябрь–март — прохладно и сухо. Летом (+40 °C) и в муссон (июль–сентябрь) — тяжело.\n\n"
    "🎟 *Цена входа*\n"
    "Иностранцы: 1 100 ₹ (≈ 1 300 ₽). Вход на закате дороже.\n"
    "Пятница — только для мусульман на намаз, туристы не допускаются.\n\n"
    "⚠️ _Данные актуальны на 2025–2026 г._"
),

"🇧🇷 Статуя Христа Искупителя": (
    "🇧🇷 *Статуя Христа Искупителя*\n\n"
    "📍 *Страна:* Бразилия, г. Рио-де-Жанейро\n"
    "📅 *Построена:* 1922–1931 гг.\n\n"
    "📖 *История*\n"
    "Монумент на горе Корковаду (710 м) стал символом Рио и Бразилии. "
    "Статуя высотой 30 м стоит на 8-метровом постаменте. Автор проекта — французский скульптор Поль Ландовски. "
    "Торжественное открытие состоялось 12 октября 1931 г. ЮНЕСКО — 2012 г.\n\n"
    "✨ *Интересные факты*\n"
    "• Каждый год молния бьёт в статую несколько раз — повреждения регулярно реставрируют\n"
    "• Размах рук — 28 метров\n"
    "• Статуя сложена из мыльного камня — стеатита, привезённого из Швеции\n"
    "• Одна из самых посещаемых достопримечательностей мира — ~2 млн туристов в год\n\n"
    "🚗 *Как добраться*\n"
    "Зубчатый поезд Trem do Corcovado от ст. Cosme Velho (20 мин). "
    "Можно на микроавтобусе Van или такси. Билет включает подъём.\n\n"
    "🌤 *Лучшее время*\n"
    "Апрель–октябрь — сухой сезон, меньше облаков. Приезжай ранним утром — до толп туристов.\n\n"
    "🎟 *Цена входа*\n"
    "Поезд + вход: ~85–100 BRL (≈ 1 500–1 800 ₽). "
    "Ван или такси — дешевле, но медленнее.\n\n"
    "⚠️ _Данные актуальны на 2025–2026 г._"
),

"🇲🇽 Пирамида Чичен-Ица": (
    "🇲🇽 *Чичен-Ица — пирамида Кукулькана*\n\n"
    "📍 *Страна:* Мексика, штат Юкатан\n"
    "📅 *Построена:* IX–XII вв. н. э.\n\n"
    "📖 *История*\n"
    "Чичен-Ица — один из крупнейших городов цивилизации майя. "
    "Пирамида Эль-Кастильо (Кукулькан) высотой 30 м — астрономический календарь в камне: "
    "каждая из четырёх сторон имеет 91 ступень + верхняя платформа = 365 дней. "
    "ЮНЕСКО — 1988 г.\n\n"
    "✨ *Интересные факты*\n"
    "• В дни равноденствий (март и сентябрь) тень на пирамиде образует «спускающегося змея» Кукулькана\n"
    "• Подъём на пирамиду запрещён с 2006 г. из соображений сохранности\n"
    "• На площадке у пирамиды эффект эха имитирует пение птицы кетцаль\n"
    "• Рядом — Священный сенот, куда майя бросали жертвоприношения\n\n"
    "🚗 *Как добраться*\n"
    "Из Канкуна: автобус ADO (2,5 ч, ~200–250 MXN) или тур из отеля. "
    "Из Мериды: 1,5 ч на автобусе или такси.\n\n"
    "🌤 *Лучшее время*\n"
    "Ноябрь–март — нет дождей и жары. Равноденствие 20–21 марта и 22–23 сентября: огромные толпы, но зрелищно.\n\n"
    "🎟 *Цена входа*\n"
    "Федеральный + штатный сбор: ~571–714 MXN (≈ 2 700–3 400 ₽). "
    "Аудиогид — дополнительно.\n\n"
    "⚠️ _Данные актуальны на 2025–2026 г._"
),

"🇵🇪 Мачу-Пикчу": (
    "🇵🇪 *Мачу-Пикчу*\n\n"
    "📍 *Страна:* Перу, регион Куско\n"
    "📅 *Построен:* XV в. (около 1450 г.)\n\n"
    "📖 *История*\n"
    "«Затерянный город инков» на высоте 2 430 м над уровнем моря. "
    "Предположительно — летняя резиденция инкского императора Пачакутека. "
    "Испанцы так и не нашли город, он оставался в запустении до 1911 г., "
    "когда его обнаружил американский историк Хайрам Бингем. "
    "ЮНЕСКО — 1983 г.\n\n"
    "✨ *Интересные факты*\n"
    "• Постройки сложены без цемента — камни подогнаны с точностью до миллиметра\n"
    "• До сих пор неизвестно точное предназначение города — ритуальный центр или резиденция?\n"
    "• Лама — не просто декор, они живут на территории руин и щиплют траву\n"
    "• В сутки разрешено не более 5 600 посетителей — броней заранее!\n\n"
    "🚗 *Как добраться*\n"
    "Куско → Агуас-Кальентес: поезд PeruRail или Inca Rail (3,5 ч, от $50). "
    "Агуас-Кальентес → Мачу-Пикчу: автобус (25 мин, $12 туда-обратно) или пешком 1,5 ч по тропе.\n\n"
    "🌤 *Лучшее время*\n"
    "Апрель–октябрь — сухой сезон, чистое небо. "
    "Июнь–август — пик туристов, бронируй за 2–3 месяца.\n\n"
    "🎟 *Цена входа*\n"
    "От $25 (только руины) до $52 (с подъёмом на Солнечные ворота или Хуайну-Пикчу). "
    "Обязательна онлайн-бронь: machupicchu.gob.pe\n\n"
    "⚠️ _Данные актуальны на 2025–2026 г._"
),

"🇯🇴 Петра": (
    "🇯🇴 *Петра*\n\n"
    "📍 *Страна:* Иордания, южный Ваади-Муса\n"
    "📅 *Основана:* IV в. до н. э. (набатейское царство)\n\n"
    "📖 *История*\n"
    "«Розовый город» — столица набатейского царства, вырубленная в розовых скалах. "
    "Набатеи контролировали торговые пути между Аравией, Египтом и Средиземноморьем. "
    "Город был потерян для западного мира на столетия и вновь открыт швейцарским путешественником "
    "Иоганном Буркхардтом в 1812 г. ЮНЕСКО — 1985 г.\n\n"
    "✨ *Интересные факты*\n"
    "• «Казна» (Эль-Хазне) — главный фасад, высечена в скале на 40 м в высоту\n"
    "• Набатеи создали сложную систему водопроводов, собиравшую дождевую воду в пустыне\n"
    "• Около 70 бедуинских семей исторически жили прямо в пещерах Петры — до 1985 г.\n"
    "• Снималась в «Индиана Джонс и последний крестовый поход» (1989)\n\n"
    "🚗 *Как добраться*\n"
    "Из Аммана: автобус JETT (3 ч, ~10 JOD) или такси/аренда авто. "
    "Из Акабы: 1,5 ч на такси. "
    "Ближайший город — Ваади-Муса, там все отели.\n\n"
    "🌤 *Лучшее время*\n"
    "Март–май и сентябрь–ноябрь — комфортная температура (+20–28 °C). "
    "Летом жара до +40 °C, зимой возможны дожди и холод.\n\n"
    "🎟 *Цена входа*\n"
    "1 день: 50 JOD (≈ 7 000 ₽). 2 дня: 55 JOD. 3 дня: 60 JOD. "
    "При проживании в иорданских отелях — скидки через Jordan Pass.\n\n"
    "⚠️ _Данные актуальны на 2025–2026 г._"
),

"🇮🇹 Колизей": (
    "🇮🇹 *Колизей (Амфитеатр Флавиев)*\n\n"
    "📍 *Страна:* Италия, Рим\n"
    "📅 *Построен:* 72–80 гг. н. э.\n\n"
    "📖 *История*\n"
    "Крупнейший амфитеатр античного мира: вмещал от 50 000 до 80 000 зрителей. "
    "Здесь проходили гладиаторские бои, травля животных и морские сражения (наумахии). "
    "После падения Рима использовался как крепость, жильё и каменоломня. "
    "ЮНЕСКО — 1980 г.\n\n"
    "✨ *Интересные факты*\n"
    "• Строительство длилось менее 10 лет — рекорд для такого масштаба\n"
    "• Оригинальное название — «Амфитеатр Флавиев»; «Колизей» — от лат. colosseus (огромный)\n"
    "• Система люков и подъёмников позволяла выпускать животных прямо на арену\n"
    "• За 500 лет активного использования здесь погибло около 400 000 человек и 1 млн животных\n\n"
    "🚗 *Как добраться*\n"
    "Метро линия B, ст. Colosseo. Автобусы 51, 75, 85, 87. "
    "Пешком от Форума Романум — 5 минут.\n\n"
    "🌤 *Лучшее время*\n"
    "Апрель–июнь и сентябрь–октябрь. Летом (июль–август) очереди огромны и жарко. "
    "Приходи к открытию (9:00) или за 2 ч до закрытия.\n\n"
    "🎟 *Цена входа*\n"
    "Колизей + Форум + Палатин: 18–22 € (≈ 1 800–2 200 ₽). "
    "Обязательна онлайн-бронь на coopculture.it — живая очередь занимает 2–3 ч.\n\n"
    "⚠️ _Данные актуальны на 2025–2026 г._"
),

}

# ── ЮНЕСКО по регионам ──────────────────────────────────────────
UNESCO_DATA: dict[str, str] = {

"🌏 Азия": (
    "🌏 *Наследие ЮНЕСКО — Азия*\n"
    "_Топ-15 самых известных объектов_\n\n"
    "1️⃣ 🇨🇳 *Великая Китайская стена* (1987)\nЗащитное сооружение длиной 21 000 км, VII в. до н. э. — XVII в.\n\n"
    "2️⃣ 🇨🇳 *Запретный город, Пекин* (1987)\nИмператорский дворцовый комплекс — 980 зданий, 1406–1420 гг.\n\n"
    "3️⃣ 🇮🇳 *Тадж-Махал, Агра* (1983)\nМраморный мавзолей XVII в., символ вечной любви.\n\n"
    "4️⃣ 🇮🇳 *Храмы Кхаджурахо* (1986)\nСредневековые индуистские и джайнские храмы с эротической скульптурой, X–XI вв.\n\n"
    "5️⃣ 🇯🇵 *Исторические памятники Киото* (1994)\n17 объектов: храмы, святилища и замок Нидзё, VIII–XVII вв.\n\n"
    "6️⃣ 🇯🇵 *Гора Фудзи* (2013)\nСвящённая вулканическая гора (3 776 м) — культурный символ Японии.\n\n"
    "7️⃣ 🇰🇭 *Ангкор-Ват, Камбоджа* (1992)\nГигантский храмовый комплекс кхмерской империи, IX–XV вв.\n\n"
    "8️⃣ 🇻🇳 *Залив Халонг* (1994)\nБолее 1 600 известняковых островов и гротов в Тонкинском заливе.\n\n"
    "9️⃣ 🇮🇩 *Боробудур, Индонезия* (1991)\nКрупнейший буддийский храм в мире, VIII–IX вв.\n\n"
    "🔟 🇳🇵 *Национальные парки Непала* (1979)\nЧитван и Сагарматха — дикая природа у подножия Гималаев.\n\n"
    "1️⃣1️⃣ 🇵🇰 *Мохенджо-Даро* (1980)\nДревний город цивилизации долины Инда, ~2500 до н. э.\n\n"
    "1️⃣2️⃣ 🇹🇭 *Исторический город Аюттхая* (1991)\nСтолица Сиамского королевства, 1350–1767 гг.\n\n"
    "1️⃣3️⃣ 🇨🇳 *Карстовый ландшафт Южного Китая* (2007)\nУникальные башнеобразные скалы провинций Гуйлинь, Юньнань, Гуйчжоу.\n\n"
    "1️⃣4️⃣ 🇱🇰 *Старинный Сигирия, Шри-Ланка* (1982)\nСкальная крепость V в. на 200-метровой базальтовой глыбе.\n\n"
    "1️⃣5️⃣ 🇧🇹 *Долина Паро, Бутан*\nМонастырь Такцанг («Тигриное гнездо») на скале 900 м — эмблема страны.\n\n"
    "⚠️ _Данные актуальны на 2025–2026 г._"
),

"🌍 Европа": (
    "🌍 *Наследие ЮНЕСКО — Европа*\n"
    "_Топ-15 самых известных объектов_\n\n"
    "1️⃣ 🇮🇹 *Исторический центр Рима* (1980)\nКолизей, Форум, Пантеон, Ватикан — 3 000 лет истории.\n\n"
    "2️⃣ 🇬🇷 *Акрополь Афин* (1987)\nПарфенон и ансамбль храмов V в. до н. э. на священной скале.\n\n"
    "3️⃣ 🇪🇸 *Сагра́да Фамилия и работы Гауди, Барселона* (1984/2005)\nМодернистские шедевры Антонио Гауди, XIX–XX вв.\n\n"
    "4️⃣ 🇫🇷 *Берега Сены в Париже* (1991)\nОт Лувра до Нотр-Дама — исторический облик французской столицы.\n\n"
    "5️⃣ 🇨🇿 *Исторический центр Праги* (1992)\nЗамок, Карлов мост, Старый город — средневековый ансамбль.\n\n"
    "6️⃣ 🇦🇹 *Исторический центр Вены* (2001)\nИмперская архитектура Хофбурга, Шёнбрунн, опера.\n\n"
    "7️⃣ 🇵🇱 *Исторический центр Кракова* (1978)\nОдин из первых объектов ЮНЕСКО: Вавель, Рыночная площадь, Казимеж.\n\n"
    "8️⃣ 🇬🇧 *Стоунхендж* (1986)\nМегалитический монумент ~3000 до н. э. — загадка для учёных.\n\n"
    "9️⃣ 🇮🇸 *Тингвеллир, Исландия* (2004)\nМесто образования первого парламента Европы (930 г.) и разлом тектонических плит.\n\n"
    "🔟 🇳🇴 *Фьорды Западной Норвегии* (2005)\nГейрангер и Нэрёй — самые живописные фьорды планеты.\n\n"
    "1️⃣1️⃣ 🇭🇷 *Старый город Дубровника* (1979)\nСредневековые стены, барочные дворцы и «жемчужина Адриатики».\n\n"
    "1️⃣2️⃣ 🇵🇹 *Исторический центр Синтры* (1995)\nРомантические дворцы в горах под Лиссабоном.\n\n"
    "1️⃣3️⃣ 🇸🇰 *Пещеры Словацкого и Аггтелекского карста* (1995)\nГроты с уникальными сталактитами на границе Словакии и Венгрии.\n\n"
    "1️⃣4️⃣ 🇸🇮 *Пещера Шкоцян, Словения* (1986)\nПодземный каньон с рекой — один из крупнейших в мире.\n\n"
    "1️⃣5️⃣ 🇲🇹 *Мегалитические храмы Мальты* (1980)\nДревнейшие в мире отдельно стоящие постройки, ~3600–2500 до н. э.\n\n"
    "⚠️ _Данные актуальны на 2025–2026 г._"
),

"🌍 Ближний Восток и Африка": (
    "🌍 *Наследие ЮНЕСКО — Ближний Восток и Африка*\n"
    "_Топ-15 самых известных объектов_\n\n"
    "1️⃣ 🇯🇴 *Петра, Иордания* (1985)\nНабатейский «Розовый город» в скалах, IV в. до н. э.\n\n"
    "2️⃣ 🇪🇬 *Мемфис и некрополи — пирамиды Гизы* (1979)\nПирамиды и Сфинкс — единственное из 7 древних чудес, сохранившееся.\n\n"
    "3️⃣ 🇸🇦 *Аль-Хиджр (Мадаин-Салих)* (2008)\nНабатейские гробницы I в. до н. э. — I в. н. э., первый объект ЮНЕСКО в Саудовской Аравии.\n\n"
    "4️⃣ 🇮🇷 *Персеполь, Иран* (1979)\nЦеремониальная столица Персидской империи, V в. до н. э.\n\n"
    "5️⃣ 🇮🇶 *Вавилон* (2019)\nДревний город шумеров и Навуходоносора — колыбель цивилизации.\n\n"
    "6️⃣ 🇮🇱 *Старый город Иерусалим* (1981)\nСвятыни трёх религий: Храмовая гора, Гроб Господень, Западная стена.\n\n"
    "7️⃣ 🇲🇦 *Медина Феса, Марокко* (1981)\nКрупнейший в мире средневековый пешеходный город.\n\n"
    "8️⃣ 🇹🇳 *Карфаген, Тунис* (1979)\nРуины великой финикийской цивилизации и римских бань.\n\n"
    "9️⃣ 🇪🇹 *Скальные церкви Лалибэлы* (1978)\nДвенадцать монолитных храмов XII в., высеченных в скале.\n\n"
    "🔟 🇹🇿 *Нгоронгоро, Танзания* (1979)\nКалдера потухшего вулкана — крупнейший «зоопарк» дикой природы.\n\n"
    "1️⃣1️⃣ 🇹🇿 *Национальный парк Серенгети* (1981)\nВеликая миграция 1,5 млн гну — одно из величайших природных зрелищ планеты.\n\n"
    "1️⃣2️⃣ 🇿🇼 *Большой Зимбабве* (1986)\nЗагадочные каменные руины столицы государства Мономотапа, XI–XV вв.\n\n"
    "1️⃣3️⃣ 🇿🇦 *Стол-Маунтин и Мыс Доброй Надежды* (2004)\nУникальный биосферный заповедник с 9 000 видов растений.\n\n"
    "1️⃣4️⃣ 🇴🇲 *Крепости Омана* (1987–1994)\nАфладж, Бахла и Эз-Назва — образцы арабской фортификации.\n\n"
    "1️⃣5️⃣ 🇾🇪 *Старый Сана, Йемен* (1986)\nОдин из древнейших городов мира с башенными глинобитными домами.\n\n"
    "⚠️ _Данные актуальны на 2025–2026 г._"
),

"🌎 Америка": (
    "🌎 *Наследие ЮНЕСКО — Америка*\n"
    "_Топ-15 самых известных объектов_\n\n"
    "1️⃣ 🇵🇪 *Мачу-Пикчу* (1983)\nЗатерянный город инков на высоте 2 430 м.\n\n"
    "2️⃣ 🇲🇽 *Чичен-Ица* (1988)\nГлавный город майя с пирамидой Кукулькана.\n\n"
    "3️⃣ 🇧🇷 *Бразилиа* (1987)\nПлановая столица XX в. — шедевр архитекторов Нимейера и Коста.\n\n"
    "4️⃣ 🇦🇷 *Водопады Игуасу* (1984)\nСистема из 275 водопадов на границе Аргентины и Бразилии — шире Ниагары.\n\n"
    "5️⃣ 🇺🇸 *Гранд-Каньон* (1979)\nКаньон глубиной 1,8 км, вырезанный рекой Колорадо за 5–6 млн лет.\n\n"
    "6️⃣ 🇺🇸 *Национальный парк Йеллоустон* (1978)\nПервый нацпарк мира: гейзер Олд-Фейтфул, бизоны, супервулкан.\n\n"
    "7️⃣ 🇨🇴 *Исторический центр Картахены* (1984)\nЛучше всего сохранившийся колониальный город Латинской Америки.\n\n"
    "8️⃣ 🇧🇴 *Серро-Рико и Потоси* (1987)\nСеребряный рудник XVI в., давший богатство испанской короне.\n\n"
    "9️⃣ 🇨🇱 *Остров Пасхи (Рапа-Нуи)* (1995)\nЗагадочные каменные статуи Моаи — 887 монолитов полинезийской культуры.\n\n"
    "🔟 🇬🇹 *Тикаль, Гватемала* (1979)\nМегаполис майя в джунглях — храмы высотой до 65 м.\n\n"
    "1️⃣1️⃣ 🇵🇦 *Национальный парк Дарьен* (1981)\nБиосферный заповедник с уникальным биоразнообразием Центральной Америки.\n\n"
    "1️⃣2️⃣ 🇨🇦 *Скалистые горы Канады* (1984)\nБанф, Джаспер, Йохо — ледники, бирюзовые озёра, 50 горных вершин >3 000 м.\n\n"
    "1️⃣3️⃣ 🇪🇨 *Галапагосские острова* (1978)\nЛаборатория эволюции Дарвина — гигантские черепахи, морские игуаны.\n\n"
    "1️⃣4️⃣ 🇲🇽 *Исторический центр Оахаки и Монте-Альбан* (1987)\nСтолица сапотеков и шикарная колониальная архитектура.\n\n"
    "1️⃣5️⃣ 🇨🇺 *Старая Гавана* (1982)\nКолониальный центр с крепостью Морро — дух кубинской истории.\n\n"
    "⚠️ _Данные актуальны на 2025–2026 г._"
),

"🏔 СНГ и Кавказ": (
    "🏔 *Наследие ЮНЕСКО — СНГ и Кавказ*\n"
    "_Топ-15 самых известных объектов_\n\n"
    "1️⃣ 🇷🇺 *Исторический центр Санкт-Петербурга* (1990)\nЭрмитаж, Петропавловская крепость, дворцы — 36 объектов.\n\n"
    "2️⃣ 🇷🇺 *Московский Кремль и Красная площадь* (1990)\nСимвол России, резиденция власти с XIV в.\n\n"
    "3️⃣ 🇺🇿 *Исторический центр Самарканда* (2001)\nМавзолей Тамерлана Гур-Эмир, площадь Регистан, медресе XV–XVII вв.\n\n"
    "4️⃣ 🇺🇿 *Ичан-Кала (Старая Хива)* (1990)\nСредневековый «город-музей» — первый объект ЮНЕСКО в Средней Азии.\n\n"
    "5️⃣ 🇺🇿 *Исторический центр Бухары* (1993)\nМечети, медресе и мавзолеи эпохи расцвета Шёлкового пути.\n\n"
    "6️⃣ 🇬🇪 *Исторические памятники Мцхеты* (1994)\nДревняя столица Грузии с кафедральным собором Светицховели V в.\n\n"
    "7️⃣ 🇦🇲 *Монастырь Гегард и Верхняя Азатская долина* (2000)\nСредневековый монастырский комплекс IV–XIII вв. в скальном ущелье.\n\n"
    "8️⃣ 🇦🇿 *Старый город Баку (Ичери-Шехер)* (2000)\nДевичья башня XII в. и дворец Ширваншахов — сердце древнего Баку.\n\n"
    "9️⃣ 🇰🇿 *Мавзолей Ходжи Ахмеда Ясави* (2003)\nТимуридский мавзолей XIV в. в Туркестане — место поклонения паломников.\n\n"
    "🔟 🇺🇦 *Собор Святой Софии и Киево-Печерская лавра* (1990)\nШедевр византийской архитектуры XI в. и лабиринты пещерного монастыря.\n\n"
    "1️⃣1️⃣ 🇷🇺 *Озеро Байкал* (1996)\nГлубочайшее озеро планеты (1 642 м) — 20% мировых запасов пресной воды.\n\n"
    "1️⃣2️⃣ 🇷🇺 *Вулканы Камчатки* (1996)\nКрупнейший в мире действующий вулканический пояс — 29 активных вулканов.\n\n"
    "1️⃣3️⃣ 🇷🇺 *Золотые горы Алтая* (1998)\nДевственная природа Горного Алтая, снежные барсы и скифские курганы.\n\n"
    "1️⃣4️⃣ 🇹🇲 *Древний Мерв* (1999)\nАнтичный оазис Шёлкового пути — столица государства сельджуков XI–XII вв.\n\n"
    "1️⃣5️⃣ 🇰🇬 *Западный Тянь-Шань* (2016)\nГорная экосистема с уникальным биоразнообразием на границе Кыргызстана, Казахстана и Узбекистана.\n\n"
    "⚠️ _Данные актуальны на 2025–2026 г._"
),

}


# ═══════════════════════════════════════════════════════════════
#  🏛 ЧУДЕСА И НАСЛЕДИЕ — обработчики
# ═══════════════════════════════════════════════════════════════

async def show_wonders_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Уровень 1 — главное меню раздела: 2 кнопки."""
    keyboard = ReplyKeyboardMarkup(
        [[btn] for btn in WONDERS_BTNS] + [["◀️ Назад", HOME_BTN]],
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "🏛 *Чудеса и наследие*\n\nВыбери раздел:",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    return WONDERS_MENU


async def show_wonders_seven_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Уровень 2 — список 7 чудес света (каждое отдельной кнопкой)."""
    keyboard = ReplyKeyboardMarkup(
        [[btn] for btn in WONDERS_SEVEN_BTNS] + [["◀️ Назад", HOME_BTN]],
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "🌟 *7 новых чудес света*\n\nВыбери чудо:",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    return WONDERS_SEVEN_MENU


async def wonders_main_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Уровень 1 — обрабатывает нажатия в главном меню Чудеса и наследие."""
    text = update.message.text
    if text == HOME_BTN:
        return await go_home(update, context)
    if text == "◀️ Назад":
        return await show_folder_knowledge(update, context)
    if text == "🌟 7 чудес света":
        return await show_wonders_seven_menu(update, context)
    if text == "🗺 Наследие ЮНЕСКО по регионам":
        keyboard = ReplyKeyboardMarkup(
            [[btn] for btn in UNESCO_REGION_BTNS] + [["◀️ Назад", HOME_BTN]],
            resize_keyboard=True,
        )
        await update.message.reply_text(
            "🗺 *Наследие ЮНЕСКО — выбери регион:*",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        return UNESCO_MENU
    return await show_wonders_menu(update, context)


async def wonders_seven_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Уровень 2 — выбор конкретного чуда из 7."""
    text = update.message.text
    if text == HOME_BTN:
        return await go_home(update, context)
    if text == "◀️ Назад":
        return await show_wonders_menu(update, context)

    content = WONDERS_DATA.get(text)
    if not content:
        return await show_wonders_seven_menu(update, context)

    back_kb = ReplyKeyboardMarkup([["◀️ Назад", HOME_BTN]], resize_keyboard=True)
    if len(content) > 4000:
        chunks = [content[i:i+4000] for i in range(0, len(content), 4000)]
        for chunk in chunks[:-1]:
            await update.message.reply_text(chunk, parse_mode="Markdown")
        await update.message.reply_text(chunks[-1], parse_mode="Markdown", reply_markup=back_kb)
    else:
        await update.message.reply_text(content, parse_mode="Markdown", reply_markup=back_kb)
    return WONDERS_SECTION


async def wonders_section_back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Уровень 3 — из детального описания чуда назад в список 7 чудес."""
    text = update.message.text
    if text == HOME_BTN:
        return await go_home(update, context)
    # Любой текст (включая ◀️ Назад) → возврат к списку 7 чудес
    return await show_wonders_seven_menu(update, context)


async def unesco_region_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Уровень 2 — выбор региона ЮНЕСКО."""
    text = update.message.text
    if text == HOME_BTN:
        return await go_home(update, context)
    if text == "◀️ Назад":
        return await show_wonders_menu(update, context)

    content = UNESCO_DATA.get(text)
    if not content:
        keyboard = ReplyKeyboardMarkup(
            [[btn] for btn in UNESCO_REGION_BTNS] + [["◀️ Назад", HOME_BTN]],
            resize_keyboard=True,
        )
        await update.message.reply_text(
            "🗺 *Наследие ЮНЕСКО — выбери регион:*",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        return UNESCO_MENU

    back_kb = ReplyKeyboardMarkup([["◀️ Назад", HOME_BTN]], resize_keyboard=True)
    if len(content) > 4000:
        chunks = [content[i:i+4000] for i in range(0, len(content), 4000)]
        for chunk in chunks[:-1]:
            await update.message.reply_text(chunk, parse_mode="Markdown")
        await update.message.reply_text(chunks[-1], parse_mode="Markdown", reply_markup=back_kb)
    else:
        await update.message.reply_text(content, parse_mode="Markdown", reply_markup=back_kb)
    return UNESCO_REGION


async def cruise_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главное меню раздела круизов."""
    context.user_data["cruise_depth"] = "menu"
    keyboard = ReplyKeyboardMarkup(
        [[btn] for btn in CRUISE_BTNS] + [["◀️ Назад", HOME_BTN]],
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "🚢 *Круизы*\n\nВыбери раздел:",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    return CRUISE_MENU


async def cruise_section_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает контент выбранного раздела круизов."""
    text = update.message.text
    if text == HOME_BTN:
        return await go_home(update, context)
    if text == "◀️ Назад":
        if context.user_data.get("cruise_depth") == "menu":
            return await show_folder_knowledge(update, context)
        return await cruise_menu_handler(update, context)
    content = CRUISE_DATA.get(text)
    if not content:
        return await cruise_menu_handler(update, context)
    context.user_data["cruise_depth"] = "section"
    back_kb = ReplyKeyboardMarkup([["◀️ Назад", HOME_BTN]], resize_keyboard=True, one_time_keyboard=True)
    if len(content) > 4000:
        chunks = [content[i:i+4000] for i in range(0, len(content), 4000)]
        for chunk in chunks[:-1]:
            await update.message.reply_text(chunk, parse_mode="Markdown")
        await update.message.reply_text(chunks[-1], parse_mode="Markdown", reply_markup=back_kb)
    else:
        await update.message.reply_text(content, parse_mode="Markdown", reply_markup=back_kb)
    return CRUISE_SECTION


## ── SUPPORT ─────────────────────────────────────────────────────────────────

_SUPPORT_TYPES = {
    "✍️ Написать нам":       "Общее обращение",
    "🐛 Сообщить об ошибке": "Ошибка в боте",
    "💡 Предложить идею":    "Предложение",
}

_SUPPORT_KB = ReplyKeyboardMarkup(
    [[btn] for btn in _SUPPORT_TYPES] + [["◀️ Назад"], [HOME_BTN]],
    resize_keyboard=True,
    one_time_keyboard=True,
)


async def show_support_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆘 *Поддержка*\n\n"
        "⚠️ Если бот не реагирует — зайди в меню и нажми /start для перезагрузки. "
        "Бот находится в активной разработке, мы постоянно добавляем новые функции и обновления.\n\n"
        "Чем можем помочь?",
        parse_mode="Markdown",
        reply_markup=_SUPPORT_KB,
    )
    return SUPPORT_MENU


async def support_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == HOME_BTN:
        return await go_home(update, context)
    if text == "◀️ Назад":
        return await go_home(update, context)
    if text in _SUPPORT_TYPES:
        context.user_data["support_type"] = _SUPPORT_TYPES[text]
        await update.message.reply_text(
            "✏️ Напиши своё сообщение — мы обязательно его прочитаем:",
            reply_markup=ReplyKeyboardMarkup([["◀️ Назад"], [HOME_BTN]], resize_keyboard=True),
        )
        return SUPPORT_TYPING
    return await show_support_menu(update, context)


async def support_typing_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == HOME_BTN:
        return await go_home(update, context)
    if text == "◀️ Назад":
        return await show_support_menu(update, context)

    user = update.effective_user
    support_type = context.user_data.get("support_type", "Не указан")
    name = user.full_name or "Без имени"
    username_str = f"@{user.username}" if user.username else "нет username"

    logger.info(
        "support: user_id=%s (%s) тип=%r текст=%r → отправляем ADMIN_ID=%s",
        user.id, username_str, support_type, text[:120], ADMIN_ID,
    )

    # Без parse_mode — пользовательский текст может содержать спецсимволы Markdown
    admin_text = (
        f"🆘 Обращение в поддержку\n\n"
        f"👤 Имя: {name}\n"
        f"🔗 Username: {username_str}\n"
        f"🆔 Telegram ID: {user.id}\n"
        f"📋 Тип: {support_type}\n\n"
        f"💬 Сообщение:\n{text}"
    )
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text)
        logger.info("support: доставлено ADMIN_ID=%s ✓", ADMIN_ID)
    except Exception as e:
        logger.error(
            "support: НЕ ДОСТАВЛЕНО ADMIN_ID=%s — %s: %s",
            ADMIN_ID, type(e).__name__, e,
        )
        logger.error(traceback.format_exc())

    await update.message.reply_text(
        "✅ Сообщение отправлено! Мы ответим в ближайшее время.",
        reply_markup=get_main_keyboard(),
    )
    context.user_data.pop("support_type", None)
    return MAIN_MENU


## ── TOURS ────────────────────────────────────────────────────────────────────

_TOURS_TYPES = {
    "🤝 Сотрудничество": "Сотрудничество по турам",
    "✈️ Хочу в тур":     "Хочу в тур",
}

_TOURS_KB = ReplyKeyboardMarkup(
    [[btn] for btn in _TOURS_TYPES] + [["◀️ Назад"], [HOME_BTN]],
    resize_keyboard=True,
    one_time_keyboard=True,
)


async def show_tours_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✈️ *Авторские туры*\n\n"
        "🚧 В разработке — скоро появится!\n"
        "Пока готовы к сотрудничеству и рады ответить на вопросы 👇",
        parse_mode="Markdown",
        reply_markup=_TOURS_KB,
    )
    return TOURS_MENU


async def tours_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == HOME_BTN:
        return await go_home(update, context)
    if text == "◀️ Назад":
        return await go_home(update, context)
    if text in _TOURS_TYPES:
        context.user_data["tours_type"] = _TOURS_TYPES[text]
        await update.message.reply_text(
            "✏️ Напиши своё сообщение — мы обязательно его прочитаем:",
            reply_markup=ReplyKeyboardMarkup([["◀️ Назад"], [HOME_BTN]], resize_keyboard=True),
        )
        return TOURS_TYPING
    return await show_tours_menu(update, context)


async def tours_typing_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == HOME_BTN:
        return await go_home(update, context)
    if text == "◀️ Назад":
        return await show_tours_menu(update, context)

    user = update.effective_user
    tours_type = context.user_data.get("tours_type", "Не указан")
    name = user.full_name or "Без имени"
    username_str = f"@{user.username}" if user.username else "нет username"

    logger.info(
        "tours: user_id=%s (%s) тип=%r текст=%r → отправляем ADMIN_ID=%s",
        user.id, username_str, tours_type, text[:120], ADMIN_ID,
    )

    # Без parse_mode — пользовательский текст может содержать спецсимволы Markdown
    admin_text = (
        f"✈️ Авторские туры — новое обращение\n\n"
        f"👤 Имя: {name}\n"
        f"🔗 Username: {username_str}\n"
        f"🆔 Telegram ID: {user.id}\n"
        f"📋 Тип: {tours_type}\n\n"
        f"💬 Сообщение:\n{text}"
    )
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text)
        logger.info("tours: доставлено ADMIN_ID=%s ✓", ADMIN_ID)
    except Exception as e:
        logger.error(
            "tours: НЕ ДОСТАВЛЕНО ADMIN_ID=%s — %s: %s",
            ADMIN_ID, type(e).__name__, e,
        )
        logger.error(traceback.format_exc())

    await update.message.reply_text(
        "✅ Сообщение отправлено! Мы свяжемся с вами в ближайшее время.",
        reply_markup=get_main_keyboard(),
    )
    context.user_data.pop("tours_type", None)
    return MAIN_MENU


## ── SHOP ─────────────────────────────────────────────────────────────────────

_SHOP_KB = ReplyKeyboardMarkup(
    [["🤝 Сотрудничество"], ["◀️ Назад"], [HOME_BTN]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

_SHOP_TEXT = (
    "🛒 *Магазин «Как местный»*\n\n"
    "🚧 В разработке — скоро появится!\n\n"
    "Здесь будут уникальные вещи для путешественников:\n"
    "🎨 Работы художников и дизайнеров\n"
    "🧵 Изделия ручной работы\n"
    "🗺 Антиквариат и винтаж\n"
    "✈️ Всё в теме путешествий и культур мира\n\n"
    "Готовы к сотрудничеству! 👇"
)


async def show_shop_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        _SHOP_TEXT,
        parse_mode="Markdown",
        reply_markup=_SHOP_KB,
    )
    return SHOP_MENU


async def shop_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == HOME_BTN:
        return await go_home(update, context)
    if text == "◀️ Назад":
        return await go_home(update, context)
    if text == "🤝 Сотрудничество":
        context.user_data["shop_type"] = "Сотрудничество: Магазин"
        await update.message.reply_text(
            "✏️ Напиши своё сообщение — мы обязательно его прочитаем:",
            reply_markup=ReplyKeyboardMarkup([["◀️ Назад"], [HOME_BTN]], resize_keyboard=True),
        )
        return SHOP_TYPING
    return await show_shop_menu(update, context)


async def shop_typing_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == HOME_BTN:
        return await go_home(update, context)
    if text == "◀️ Назад":
        return await show_shop_menu(update, context)

    user = update.effective_user
    shop_type = context.user_data.get("shop_type", "Сотрудничество: Магазин")
    name = user.full_name or "Без имени"
    username_str = f"@{user.username}" if user.username else "нет username"

    logger.info(
        "shop: user_id=%s (%s) тип=%r текст=%r → отправляем ADMIN_ID=%s",
        user.id, username_str, shop_type, text[:120], ADMIN_ID,
    )

    admin_text = (
        f"🛒 Магазин — новое обращение\n\n"
        f"👤 Имя: {name}\n"
        f"🔗 Username: {username_str}\n"
        f"🆔 Telegram ID: {user.id}\n"
        f"📋 Тип: {shop_type}\n\n"
        f"💬 Сообщение:\n{text}"
    )
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text)
        logger.info("shop: доставлено ADMIN_ID=%s ✓", ADMIN_ID)
    except Exception as e:
        logger.error(
            "shop: НЕ ДОСТАВЛЕНО ADMIN_ID=%s — %s: %s",
            ADMIN_ID, type(e).__name__, e,
        )
        logger.error(traceback.format_exc())

    await update.message.reply_text(
        "✅ Сообщение отправлено! Мы свяжемся с вами в ближайшее время.",
        reply_markup=get_main_keyboard(),
    )
    context.user_data.pop("shop_type", None)
    return MAIN_MENU


## ── MOVIES ──────────────────────────────────────────────────────────────────

MOVIES_LIST_DATA = {
    "🌏 Фильмы про путешествия": (
        "🌏 *Фильмы про путешествия — топ-20*\n\n"
        "🎬 *В диких условиях* (2007) ⭐ 8.1\n"
        "📝 Выпускник бросает всё и едет автостопом на Аляску\n\n"
        "🎬 *Мотоциклетные дневники* (2004) ⭐ 7.8\n"
        "📝 Молодой Че Гевара путешествует по Латинской Америке на мотоцикле\n\n"
        "🎬 *До рассвета* (1995) ⭐ 8.1\n"
        "📝 Двое незнакомцев проводят одну ночь в Вене — начало культовой трилогии\n\n"
        "🎬 *Амели* (2001) ⭐ 8.3\n"
        "📝 Мечтательница из Парижа тайно меняет жизни окружающих к лучшему\n\n"
        "🎬 *Римские каникулы* (1953) ⭐ 8.0\n"
        "📝 Принцесса сбегает от протокола и влюбляется в журналиста в Риме\n\n"
        "🎬 *Потерянный в переводе* (2003) ⭐ 7.7\n"
        "📝 Два одиноких американца случайно встречаются в токийском отеле\n\n"
        "🎬 *Полночь в Париже* (2011) ⭐ 7.7\n"
        "📝 Писатель переносится в Париж 1920-х — встречает Хемингуэя и Пикассо\n\n"
        "🎬 *Залечь на дно в Брюгге* (2008) ⭐ 7.9\n"
        "📝 Два наёмных убийцы прячутся в средневековом бельгийском городке\n\n"
        "🎬 *127 часов* (2010) ⭐ 7.6\n"
        "📝 Альпинист застрял под камнем в каньоне Юты — реальная история выживания\n\n"
        "🎬 *Дикая* (2014) ⭐ 7.1\n"
        "📝 Женщина в одиночку проходит 1700 км по Тихоокеанскому горному маршруту\n\n"
        "🎬 *Путь* (2010) ⭐ 7.6\n"
        "📝 Отец проходит паломнический путь Камино де Сантьяго вместо погибшего сына\n\n"
        "🎬 *Пляж* (2000) ⭐ 6.7\n"
        "📝 Американец ищет секретный идеальный пляж в Таиланде\n\n"
        "🎬 *Тайная жизнь Уолтера Митти* (2013) ⭐ 7.3\n"
        "📝 Офисный клерк впервые в жизни отправляется в реальное приключение\n\n"
        "🎬 *Кочевница* (2020) ⭐ 7.3\n"
        "📝 Женщина после потери дома путешествует по Америке в фургоне\n\n"
        "🎬 *Миллионер из трущоб* (2008) ⭐ 8.0\n"
        "📝 Парень из трущоб Мумбая попадает на шоу «Кто хочет стать миллионером»\n\n"
        "🎬 *Ешь, молись, люби* (2010) ⭐ 5.8\n"
        "📝 После развода женщина едет в Италию, Индию и на Бали — искать себя\n\n"
        "🎬 *Под солнцем Тосканы* (2003) ⭐ 6.5\n"
        "📝 Американка покупает виллу в Италии и начинает жизнь заново\n\n"
        "🎬 *Неделимая земля* (2013) ⭐ 7.2\n"
        "📝 Британка проходит 1700 км по пустыне Австралии с верблюдами\n\n"
        "🎬 *Вавилон* (2006) ⭐ 7.5\n"
        "📝 Четыре истории, связанные одним выстрелом — Марокко, Япония, Мексика\n\n"
        "🎬 *Из Африки* (1985) ⭐ 7.1\n"
        "📝 История любви датчанки на фоне кенийских просторов, 7 «Оскаров»\n\n"
    ),
    "🎒 Самостоятельные путешественники": (
        "🎒 *Фильмы про самостоятельных путешественников — топ-10*\n\n"
        "🎬 *В диких условиях* (2007) ⭐ 8.1\n"
        "📝 Выпускник колледжа уходит в дикую природу Аляски в одиночку\n\n"
        "🎬 *Мотоциклетные дневники* (2004) ⭐ 7.8\n"
        "📝 Два друга на разваливающемся мотоцикле — путешествие, изменившее историю\n\n"
        "🎬 *127 часов* (2010) ⭐ 7.6\n"
        "📝 Один в каньоне, рука зажата камнем — реальная история Арона Ралстона\n\n"
        "🎬 *Дикая* (2014) ⭐ 7.1\n"
        "📝 Черил Стрэйд идёт 1700 км одна, чтобы найти себя после потерь\n\n"
        "🎬 *Неделимая земля* (2013) ⭐ 7.2\n"
        "📝 Робин Дэвидсон пересекает австралийскую пустыню с верблюдами и собакой\n\n"
        "🎬 *Тайная жизнь Уолтера Митти* (2013) ⭐ 7.3\n"
        "📝 Клерк из журнала LIFE впервые выходит за пределы офиса — в Гренландию и Гималаи\n\n"
        "🎬 *Путь* (2010) ⭐ 7.6\n"
        "📝 800 км пешком через Испанию — паломничество, меняющее взгляд на жизнь\n\n"
        "🎬 *Кочевница* (2020) ⭐ 7.3\n"
        "📝 Современная кочевница живёт в фургоне и работает на сезонных работах по США\n\n"
        "🎬 *Марсианин* (2015) ⭐ 8.0\n"
        "📝 Самый одинокий путешественник в истории — выживание на Марсе в одиночку\n\n"
        "🎬 *Ешь, молись, люби* (2010) ⭐ 5.8\n"
        "📝 Год в одиночестве по трём странам в поисках баланса — вдохновляет миллионы\n\n"
    ),
    "🌍 Документалки про мир": (
        "🌍 *Документалки про мир — топ-10 на YouTube/стримингах*\n\n"
        "🎬 *Наша планета* (2019) ⭐ 9.3\n"
        "📝 Дэвид Аттенборо об исчезающей красоте нашей планеты\n\n"
        "🎬 *Планета Земля II* (2016) ⭐ 9.5\n"
        "📝 BBC: острова, горы, джунгли, пустыни, города — лучшая природная съёмка в мире\n\n"
        "🎬 *Части неизвестного* с Бурденом (2013–2018) ⭐ 9.0\n"
        "📝 Энтони Бурден едет в самые неожиданные места мира ради еды и культуры\n\n"
        "🎬 *Свободный одиночный восход* (2018) ⭐ 8.2\n"
        "📝 Алекс Хоннольд лезет на Эль-Капитан без страховки — снято вживую\n\n"
        "🎬 *Спуск на Землю* с Заком Эфроном (2020)\n"
        "📝 Актёр путешествует по миру в поисках устойчивого образа жизни\n\n"
        "🎬 *Дикая Россия* (2008)\n"
        "📝 Шесть серий о нетронутой природе России — тайга, Камчатка, Байкал\n\n"
        "🎬 *Голубая планета II* (2017) ⭐ 9.3\n"
        "📝 Глубины океанов, которые до этого никто не видел — BBC на максимуме\n\n"
        "🎬 *Куба и революция* (разные авторы)\n"
        "📝 Подборка коротких документалок о жизни на Кубе сегодня\n\n"
        "🎬 *Вокруг света за 80 дней* с Майклом Пэйлином (1989)\n"
        "📝 Монти Пайтон повторяет маршрут Филеаса Фогга — смешно и познавательно\n\n"
        "🎬 *Совершенный Планетарий* (2021)\n"
        "📝 Дэвид Аттенборо о том, каким мог бы быть мир — призыв к действию\n\n"
    ),
}

MOVIES_REGIONS_DATA = {
    "🌏 Азия": (
        "🌏 *Фильмы — Азия*\n\n"
        "🎬 *Любовное настроение* (2000) ⭐ 8.1\n"
        "📝 Гонконг 1960-х: два соседа подозревают измену супругов — визуальный шедевр\n\n"
        "🎬 *Потерянный в переводе* (2003) ⭐ 7.7\n"
        "📝 Токио глазами двух потерянных американцев — ночные огни, капсульный бар\n\n"
        "🎬 *Пляж* (2000) ⭐ 6.7\n"
        "📝 Таиланд, острова и поиск идеального места — с Ди Каприо\n\n"
        "🎬 *Миллионер из трущоб* (2008) ⭐ 8.0\n"
        "📝 Мумбай от трущоб до телестудии — Индия во всей своей яркости\n\n"
        "🎬 *Ешь, молись, люби* (2010) ⭐ 5.8\n"
        "📝 Бали как место духовного поиска — рисовые поля, храмы, любовь\n\n"
    ),
    "🇪🇺 Европа": (
        "🇪🇺 *Фильмы — Европа*\n\n"
        "🎬 *Амели* (2001) ⭐ 8.3\n"
        "📝 Монмартр, кафе, рынки — самый парижский фильм всех времён\n\n"
        "🎬 *До рассвета* (1995) ⭐ 8.1\n"
        "📝 Вена за одну ночь: трамваи, книжные магазины, мосты над Дунаем\n\n"
        "🎬 *Римские каникулы* (1953) ⭐ 8.0\n"
        "📝 Рим как герой фильма — Колизей, фонтан Треви, весёлый хаос улиц\n\n"
        "🎬 *Залечь на дно в Брюгге* (2008) ⭐ 7.9\n"
        "📝 Средневековые каналы, колокольни и мрачный юмор в сердце Бельгии\n\n"
        "🎬 *Полночь в Париже* (2011) ⭐ 7.7\n"
        "📝 Ночной Париж как машина времени — Монпарнас, Сена, джаз\n\n"
    ),
    "🌎 Америка": (
        "🌎 *Фильмы — Америка*\n\n"
        "🎬 *В диких условиях* (2007) ⭐ 8.1\n"
        "📝 США от Атланты до Аляски — автостоп, фермы, горы и море\n\n"
        "🎬 *Мотоциклетные дневники* (2004) ⭐ 7.8\n"
        "📝 От Аргентины до Венесуэлы — Латинская Америка на двух колёсах\n\n"
        "🎬 *Дикая* (2014) ⭐ 7.1\n"
        "📝 Тихоокеанский маршрут PCT: пустыни, леса, Сьерра-Невада\n\n"
        "🎬 *И твою маму тоже* (2001) ⭐ 7.6\n"
        "📝 Дорожное путешествие двух парней и зрелой женщины по Мексике\n\n"
        "🎬 *Кочевница* (2020) ⭐ 7.3\n"
        "📝 Великие равнины, национальные парки, дороги Среднего Запада США\n\n"
    ),
    "🌍 Африка": (
        "🌍 *Фильмы — Африка*\n\n"
        "🎬 *Из Африки* (1985) ⭐ 7.1\n"
        "📝 Кенийские саванны и рассветы над Килиманджаро — 7 «Оскаров»\n\n"
        "🎬 *Английский пациент* (1996) ⭐ 7.4\n"
        "📝 Пустыня Сахара, тунисские пещеры и тайна сгоревшего человека\n\n"
        "🎬 *Кровавый алмаз* (2006) ⭐ 8.0\n"
        "📝 Сьерра-Леоне, гражданская война и погоня за редким бриллиантом\n\n"
        "🎬 *Последний король Шотландии* (2006) ⭐ 7.6\n"
        "📝 Шотландский врач попадает в ближний круг диктатора Иди Амина в Уганде\n\n"
        "🎬 *Преданный садовник* (2005) ⭐ 7.4\n"
        "📝 Кения: дипломат расследует гибель жены и вскрывает фармацевтический заговор\n\n"
    ),
}

## ── Film locations by country ────────────────────────────────────────────────

MOVIES_LOCATIONS_DATA = {
    "🇪🇸 Испания": (
        "🇪🇸 *Испания в кино*\n\n"
        "🎬 *Парфюмер* (2006) — Барселона\n"
        "📍 Барселонский рынок Бокерия — именно здесь начинается история Гренуя. "
        "Старый город (Готический квартал), улицы Раваль. "
        "✅ Можно погулять по тем же улочкам и зайти на рынок.\n\n"
        "🎬 *Вики Кристина Барселона* (2008) — Барселона\n"
        "📍 Парк Гуэль, Casa Milà (Ла Педрера), Сагrada Família, галерея Центр Борн. "
        "✅ Все локации открыты для туристов — классический маршрут по Гауди.\n\n"
        "🎬 *Всё о моей матери* (1999) — Барселона\n"
        "📍 Театр Виктория, больница Санта-Крус, улица Параллель, район Побленоу. "
        "✅ Фанаты Альмодовара делают пешеходные туры по этим местам.\n\n"
        "🎬 *Ешь, молись, люби* (2010) — Барселона (сцены в Испании)\n"
        "📍 Рынок Бокерия, набережная Барселонеты, таверны Готического квартала. "
        "✅ Отличный маршрут для гастро-туризма."
    ),
    "🇮🇹 Италия": (
        "🇮🇹 *Италия в кино*\n\n"
        "🎬 *Римские каникулы* (1953) — Рим\n"
        "📍 Фонтан Треви (сцена с монеткой), Испанская лестница, Уста истины (Bocca della Verità), "
        "площадь Венеции, Пантеон. ✅ Все локации в пешей доступности — классический маршрут по центру Рима.\n\n"
        "🎬 *Крёстный отец* (1972) — Сицилия\n"
        "📍 Деревня Саваока близ Корлеоне, городок Форца д'Агро (сцена свадьбы), "
        "Таормина, Палермо. ✅ Существуют туры «По следам Корлеоне» на Сицилии.\n\n"
        "🎬 *Под солнцем Тосканы* (2003) — Тоскана\n"
        "📍 Кортона (главная локация), виноградники Кьянти, Монтепульчано, Сиена. "
        "✅ Вилла Bramasole в Кортоне существует — можно пройти мимо.\n\n"
        "🎬 *Туристка / The Tourist* (2010) — Венеция\n"
        "📍 Гранд-канал, отель «Даниэли», площадь Сан-Марко, мост Риальто, Casino di Venezia. "
        "✅ Все локации открыты — Венеция сама по себе кино."
    ),
    "🇫🇷 Франция": (
        "🇫🇷 *Франция в кино*\n\n"
        "🎬 *Амели* (2001) — Париж, Монмартр\n"
        "📍 Кафе «Des 2 Moulins» (18 ар-т, rue Lepic 15) — главное место съёмок, "
        "Сакре-Кёр, Мулен Руж, продуктовые лавки Монмартра. "
        "✅ Кафе работает, там даже есть меню «в стиле Амели».\n\n"
        "🎬 *Полночь в Париже* (2011) — Париж\n"
        "📍 Мост Александра III (ночные сцены), площадь Контрэскарп, Версаль, "
        "Музей Родена, отель «Ле Мёрис». ✅ Все открыты, Версаль — отдельный день.\n\n"
        "🎬 *Код да Винчи* (2006) — Париж и окрестности\n"
        "📍 Лувр (пирамида, зал Денон), церковь Сен-Сюльпис, замок Виллет, "
        "аббатство Лис в Барбизоне. ✅ Лувр и Сен-Сюльпис открыты для всех.\n\n"
        "🎬 *Эмили в Париже* (сериал, 2020–) — Париж\n"
        "📍 Площадь Пале-Руаяль, сады Тюильри, мост Биракина, район Маре, "
        "brasserie Hôtel de la Lune (пр. Монж). ✅ Туры по локациям очень популярны."
    ),
    "🇬🇧 Великобритания": (
        "🇬🇧 *Великобритания в кино*\n\n"
        "🎬 *Гарри Поттер* (серия) — Лондон, Шотландия, Оксфорд\n"
        "📍 Лондон: Гарри Поттер Студия (Лёвстонд), вокзал Кингс-Кросс (платформа 9¾), "
        "Лидэнхолл-маркет (Косой переулок). Шотландия: виадук Гленфиннан, замок Аулдеарн. "
        "Оксфорд: трапезная Крайст-Чёрч (= Большой зал Хогвартса). "
        "✅ Студия — отдельный тур на полдня, платформа 9¾ бесплатна.\n\n"
        "🎬 *Шерлок Холмс* (сериал BBC, 2010–) — Лондон\n"
        "📍 Бейкер-стрит 187 (музей Холмса), Сент-Барт госпиталь, "
        "Тейт Модерн, Гринвич. ✅ Музей Холмса — популярная точка, очереди.\n\n"
        "🎬 *Ноттинг Хилл* (1999) — Лондон\n"
        "📍 Портобелло-роуд (рынок), The Notting Hill Bookshop (прообраз магазина), "
        "сквер Хемпстед. ✅ Книжный магазин работает, рынок по субботам.\n\n"
        "🎬 *Бриджит Джонс* (2001) — Лондон\n"
        "📍 Боро-маркет, квартира на Боу-стрит, паб «The Globe», Пикадилли. "
        "✅ Боро-маркет — одно из лучших мест для гастро-прогулки."
    ),
    "🇳🇿 Новая Зеландия": (
        "🇳🇿 *Новая Зеландия в кино*\n\n"
        "🎬 *Властелин колец* / *Хоббит* — Новая Зеландия\n"
        "📍 Хоббитон (Матамата, Северный остров) — единственная сохранённая декорация, "
        "36 нор хоббитов, «Зелёный Дракон». Гора Тонгариро (= Роковая гора Мордора). "
        "Квинстаун (долины Ривенделла). Долина Феи (Маунт-Кук). "
        "✅ Хоббитон — официальный тур, билеты нужно бронировать заранее. "
        "Тонгариро Alpine Crossing — лучший однодневный трек в NZ."
    ),
    "🇯🇴 Иордания": (
        "🇯🇴 *Иордания в кино*\n\n"
        "🎬 *Марсианин* (2015) — пустыня Вади Рам\n"
        "📍 Вади Рам (Wadi Rum) — красная марсианская пустыня на юге Иордании. "
        "Скалы Джебель-Умм-Ишрин, лагеря бедуинов. "
        "✅ Ночёвка в пустыне в прозрачном шатаре с видом на звёзды — незабываемо. "
        "Туры из Акабы или Петры.\n\n"
        "🎬 *Индиана Джонс и Последний крестовый поход* (1989) — Петра\n"
        "📍 Сокровищница (Al-Khazneh) — финальные сцены с Граалем. "
        "Каньон Сик — пешеходный проход к Сокровищнице. "
        "✅ Петра — объект ЮНЕСКО, открыта ежедневно. "
        "Лучшее время — раннее утро до туристов."
    ),
    "🇮🇳 Индия": (
        "🇮🇳 *Индия в кино*\n\n"
        "🎬 *Миллионер из трущоб* (2008) — Мумбаи\n"
        "📍 Трущобы Дхарави (можно в тур), вокзал Чхатрапати Шиваджи (финальный танец), "
        "туристический район Колаба, Ворота Индии. "
        "✅ Вокзал — действующий, грандиозная викторианская архитектура. "
        "Туры по Дхарави существуют и популярны.\n\n"
        "🎬 *Ешь, молись, люби* (2010) — Индия (Джайпур / Ашрам)\n"
        "📍 Ашрам в Гудуре (Андхра-Прадеш), розовый Джайпур, Варанаси (дух «поиска себя»). "
        "✅ Варанаси — одно из сильнейших духовных мест планеты, "
        "рассвет на Ганге изменит восприятие мира."
    ),
    "🇹🇭 Таиланд": (
        "🇹🇭 *Таиланд в кино*\n\n"
        "🎬 *Пляж* (2000) — Ко Пхи-Пхи Лей\n"
        "📍 Залив Майя-Бэй на острове Ко Пхи-Пхи Лей — тот самый «секретный пляж». "
        "✅ После съёмок пляж был закрыт на реставрацию (2018–2022), сейчас работает "
        "с ограниченным числом посетителей. Добраться — паром с Пхукета или Краби.\n\n"
        "🎬 *Кикбоксер* (1989) — Бангкок и Таиланд\n"
        "📍 Храм Ват Пхо (Бангкок), Паттайя, боксёрские залуды Муай-тай. "
        "✅ Ват Пхо с лежащим Буддой — обязательная остановка в Бангкоке.\n\n"
        "🎬 *Без чувств / The Hangover Part II* (2011) — Бангкок\n"
        "📍 Королевский дворец, Ват Арун (Храм рассвета), клуб RCA, "
        "рынок Патпонг. ✅ Ват Арун с видом через реку Чаопрайя — красивейший закат."
    ),
    "🇰🇷 Южная Корея": (
        "🇰🇷 *Южная Корея в кино*\n\n"
        "🎬 *Паразиты* (2019) — Сеул\n"
        "📍 Район Мапов-гу (богатый дом Парков снят в павильоне, но архитектура — Сеул), "
        "полуподвальный квартал Донджак-гу. ✅ Существует официальный тур «Паразиты» — "
        "проводит Seoul City Tour Bus. Район Хонде — атмосфера богемной Кореи.\n\n"
        "🎬 *Поезд в Пусан* (2016) — Пусан / Сеул\n"
        "📍 Вокзал KTX Пусан, пляж Хэундэ, рыбный рынок Чагальчи. "
        "✅ Пусан — второй город Кореи с потрясающей уличной едой. "
        "Поезд KTX Сеул–Пусан занимает 2,5 часа — отличный мини-трип."
    ),
    "🇯🇵 Япония": (
        "🇯🇵 *Япония в кино*\n\n"
        "🎬 *Трудности перевода* (2003) — Токио\n"
        "📍 Отель Park Hyatt Tokyo (бар New York Bar на 52 этаже — та самая сцена), "
        "Shinjuku перекрёсток, Shibuya, Токийская башня. "
        "✅ Бар Park Hyatt работает — дорого, но атмосфера из фильма 100%. "
        "Shibuya Scramble — самый оживлённый перекрёсток мира.\n\n"
        "🎬 *Убить Билла* (2003) — Токио\n"
        "📍 Квартал Синдзюку, суши-бар «Blue Leaves» (собирательный образ), "
        "традиционные рёканы Японии. ✅ Синдзюку-Golden Gai — лабиринт баров в 6 переулках.\n\n"
        "🎬 *Мемуары гейши* (2005) — Киото\n"
        "📍 Район Гион (квартал гейш), улочка Нинэндзака, чайные домики. "
        "✅ Гион — живой квартал гейш; рано утром можно встретить майко."
    ),
    "🇲🇦 Марокко": (
        "🇲🇦 *Марокко в кино*\n\n"
        "🎬 *Вавилон* (2006) — Марокко (Тазарин и пустыня)\n"
        "📍 Деревня Тазарин (провинция Загора), пустыня Сахара около Мерзуги, "
        "Марракеш. ✅ Тур из Марракеша в пустыню Сахара через Уарзазат — "
        "одна из лучших поездок в Северной Африке.\n\n"
        "🎬 *Гладиатор* (2000) — Уарзазат\n"
        "📍 Студии Atlas Studios в Уарзазате (крупнейшие киностудии Африки) — "
        "здесь снимали сцены Рима и Зимбабве. ✅ Atlas Studios открыты для туристов. "
        "Рядом крепость Айт-Бен-Хаду — объект ЮНЕСКО, фигурирует в «Игре Престолов»."
    ),
    "🇨🇺 Куба": (
        "🇨🇺 *Куба в кино*\n\n"
        "🎬 *Крёстный отец 2* (1974) — Гавана\n"
        "📍 Отель Nacional de Cuba (тот самый отель, где встречаются мафиози), "
        "Малекон (набережная), Старая Гавана. "
        "✅ Отель Nacional работает — коктейль у бассейна с видом на море "
        "стоит того, чтобы зайти даже не будучи гостем.\n\n"
        "🎬 *Семь дней в Гаване* (2012) — Гавана\n"
        "📍 Вся Гавана: Старый город, бары на Обиспо, Малекон, Тринидад. "
        "✅ Куба сама по себе — живое кино. Ретро-автомобили, яркая музыка, "
        "коктейль мохито в La Bodeguita del Medio — баре Хемингуэя."
    ),
    "🇦🇺 Австралия": (
        "🇦🇺 *Австралия в кино*\n\n"
        "🎬 *Крокодил Данди* (1986) — Сидней и Северная территория\n"
        "📍 Сидней: Харбор-Бридж, Опера, Кингс-Кросс. "
        "Арнем-Ленд (Северная территория) — земля аборигенов, прообраз «буша» Данди. "
        "✅ Харбор-Бридж Climb — один из топ-активити в Австралии.\n\n"
        "🎬 *Австралия* (2008, Баз Лурман) — Кимберли / Северная территория\n"
        "📍 Кимберли (Западная Австралия) — гигантские просторы и красные скалы, "
        "Дарвин. ✅ Кимберли — одно из самых диких и красивых мест на Земле, "
        "сезон — апрель–октябрь.\n\n"
        "🎬 *Mad Max: Дорога ярости* (2015) — Намибия снималась как Австралия\n"
        "📍 Фактически снимали в Намибии (пустыня Намиб), но атмосфера — "
        "австралийский аутбэк. ✅ Намибия: Соссусфлей — одни из красивейших дюн мира."
    ),
    "🇺🇸 США": (
        "🇺🇸 *США в кино*\n\n"
        "🗽 *Нью-Йорк*\n"
        "🎬 Центральный парк (Один дома 2, Elf, Дьявол носит Prada) — "
        "прогулка по парку. "
        "🎬 Бруклинский мост (Крамер против Крамера, Годзилла) — пешеходная прогулка бесплатно. "
        "🎬 Флэтайрон-билдинг (Человек-Паук) — Бродвей и 5-я авеню. "
        "✅ «Кино-тур по Нью-Йорку» — официальный тур от On Location Tours.\n\n"
        "🎞 *Лос-Анджелес*\n"
        "🎬 Голливуд-Булевар (Ла-Ла Лэнд, Сансет Бульвар) — Аллея Звёзд, Grauman's Chinese Theatre. "
        "🎬 Гриффит обсерватория (Бунтарь без причины, Ла-Ла Лэнд) — вид на весь LA. "
        "✅ Туры по студиям Universal, Warner Bros — интерактивные бэкстейдж-туры.\n\n"
        "🏙 *Чикаго*\n"
        "🎬 Тёмный рыцарь (мост Чикаго-Ривер), Одинокий дома (дом семьи Маккалистеров — "
        "пригород Виннетка), Blues Brothers (Дворец Дейли). "
        "✅ Chicago Architecture Boat Tour — лучший способ увидеть город как в кино."
    ),
}

MOVIES_LOCATIONS_BTNS = list(MOVIES_LOCATIONS_DATA.keys())

MOVIES_MENU_BTNS = ["🌏 Фильмы про путешествия", "🎒 Самостоятельные путешественники",
                    "🌍 Документалки про мир", "🗺 Фильмы по странам",
                    "🎥 Локации из фильмов"]
MOVIES_REGION_BTNS = list(MOVIES_REGIONS_DATA.keys())


async def show_movies_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[b] for b in MOVIES_MENU_BTNS] + [["◀️ Назад", HOME_BTN]]
    await update.message.reply_text(
        "🎬 *Фильмы для путешественников*\n\nВыбери категорию:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True),
    )
    return MOVIES_MENU


async def movies_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "◀️ Назад":
        return await show_folder_knowledge(update, context)
    if text in MOVIES_LIST_DATA:
        context.user_data["movies_back"] = "menu"
        await update.message.reply_text(
            MOVIES_LIST_DATA[text], parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([["◀️ Назад", HOME_BTN]], resize_keyboard=True, one_time_keyboard=True),
        )
        return MOVIES_LIST
    if text == "🗺 Фильмы по странам":
        context.user_data["movies_mode"] = "regions"
        keyboard = [[b] for b in MOVIES_REGION_BTNS] + [["◀️ Назад", HOME_BTN]]
        await update.message.reply_text(
            "🗺 *Фильмы по странам*\n\nВыбери регион:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True),
        )
        return MOVIES_REGION
    if text == "🎥 Локации из фильмов":
        context.user_data["movies_mode"] = "locations"
        keyboard = [[b] for b in MOVIES_LOCATIONS_BTNS] + [["◀️ Назад", HOME_BTN]]
        await update.message.reply_text(
            "🎥 *Фильмы и их локации*\n\nВыбери страну, чтобы узнать где снимали:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True),
        )
        return MOVIES_REGION
    return await show_movies_menu(update, context)


async def movies_region_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    mode = context.user_data.get("movies_mode", "regions")
    if text == "◀️ Назад":
        context.user_data.pop("movies_mode", None)
        return await show_movies_menu(update, context)
    if mode == "locations" and text in MOVIES_LOCATIONS_DATA:
        context.user_data["movies_back"] = "locations"
        await update.message.reply_text(
            MOVIES_LOCATIONS_DATA[text], parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([["◀️ Назад", HOME_BTN]], resize_keyboard=True, one_time_keyboard=True),
        )
        return MOVIES_LIST
    if mode == "locations":
        keyboard = [[b] for b in MOVIES_LOCATIONS_BTNS] + [["◀️ Назад", HOME_BTN]]
        await update.message.reply_text(
            "🎥 *Фильмы и их локации*\n\nВыбери страну, чтобы узнать где снимали:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True),
        )
        return MOVIES_REGION
    if text in MOVIES_REGIONS_DATA:
        context.user_data["movies_back"] = "region"
        await update.message.reply_text(
            MOVIES_REGIONS_DATA[text], parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([["◀️ Назад", HOME_BTN]], resize_keyboard=True, one_time_keyboard=True),
        )
        return MOVIES_LIST
    keyboard = [[b] for b in MOVIES_REGION_BTNS] + [["◀️ Назад", HOME_BTN]]
    await update.message.reply_text(
        "🗺 *Фильмы по странам*\n\nВыбери регион:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True),
    )
    return MOVIES_REGION


async def movies_list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "◀️ Назад":
        back = context.user_data.get("movies_back")
        if back == "region":
            keyboard = [[b] for b in MOVIES_REGION_BTNS] + [["◀️ Назад", HOME_BTN]]
            await update.message.reply_text(
                "🗺 *Фильмы по странам*\n\nВыбери регион:",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True),
            )
            return MOVIES_REGION
        if back == "locations":
            context.user_data["movies_mode"] = "locations"
            keyboard = [[b] for b in MOVIES_LOCATIONS_BTNS] + [["◀️ Назад", HOME_BTN]]
            await update.message.reply_text(
                "🎥 *Фильмы и их локации*\n\nВыбери страну, чтобы узнать где снимали:",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True),
            )
            return MOVIES_REGION
        return await show_movies_menu(update, context)
    return await show_movies_menu(update, context)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("До встречи! Напиши /start чтобы начать заново.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /menu — всегда возвращает в главное меню."""
    context.user_data.clear()
    await update.message.reply_text(
        "🏠 Главное меню:",
        reply_markup=get_main_keyboard(),
    )
    return MAIN_MENU


## ── INCOMPATIBLE COUNTRIES ───────────────────────────────────────────────────

INCOMPATIBLE_BTN = "⛔ Несовместимые страны"

INCOMPATIBLE_CATEGORIES = {
    "🇮🇱 Израиль и арабский мир": (
        "🇮🇱 *Израиль и арабский мир*\n\n"
        "*Кто с кем несовместим:*\n"
        "Израильские штампы в паспорте закрывают въезд в ряд арабских стран. "
        "Наиболее жёсткая позиция в 2025 году у Сирии, Ирака, Ливана и Йемена — "
        "въезд с израильскими отметками де-факто или де-юре запрещён.\n\n"
        "*Что грозит:*\n"
        "— Отказ во въезде на границе\n"
        "— Задержание и депортация\n"
        "— В Ливане и Сирии возможно уголовное преследование\n\n"
        "*Как избежать проблем:*\n"
        "— Израиль с 2013 года не ставит штампы в паспорт — только на отдельный "
        "листок, который можно выбросить\n"
        "— Попроси пограничника не ставить штамп прямо при въезде\n"
        "— При въезде в ОАЭ, Бахрейн, Марокко и Иорданию израильские штампы "
        "теперь официально не проблема (нормализация отношений с 2020–2023 гг.)\n"
        "— Египет и Турция нейтральны — принимают всех\n"
        "— Если планируешь обе зоны: сначала арабские страны, потом Израиль\n\n"
        "⚠️ _Ситуация меняется — уточняй в посольстве перед поездкой. Актуально на 2025–2026_"
    ),
    "🇨🇾 Кипр и Северный Кипр": (
        "🇨🇾 *Кипр и Северный Кипр*\n\n"
        "*Кто с кем несовместим:*\n"
        "Северный Кипр признан только Турцией. Въезд туда через турецкую часть "
        "(аэропорт Эрджан или порт Газимагуса) формально является нарушением "
        "законодательства Республики Кипр и ЕС.\n\n"
        "*Что грозит:*\n"
        "— При въезде на юг (Республика Кипр) через Северный Кипр могут отказать "
        "во въезде и задержать\n"
        "— Турецкий въездной штамп Северного Кипра технически означает "
        "незаконный въезд в ЕС\n"
        "— Штраф или запрет на въезд в Кипр на несколько лет\n\n"
        "*Как избежать проблем:*\n"
        "— Всегда въезжай через официальные КПП Республики Кипр "
        "(аэропорты Ларнаки и Пафоса)\n"
        "— Пересечь с юга на север через КПП в Никосии — законно и безопасно\n"
        "— Обратно — только через те же КПП на юг, не через Эрджан\n\n"
        "⚠️ _Ситуация меняется — уточняй в посольстве перед поездкой. Актуально на 2025–2026_"
    ),
    "🇦🇲 Армения и Азербайджан": (
        "🇦🇲 *Армения и Азербайджан*\n\n"
        "*Кто с кем несовместим:*\n"
        "После войны 2020 года и окончательного перехода Карабаха под контроль "
        "Азербайджана в 2023 году между странами нет дипломатических отношений. "
        "Прямой переход границы невозможен.\n\n"
        "*Что грозит:*\n"
        "— Штамп Армении в паспорте: въезд в Азербайджан запрещён, "
        "возможна депортация и занесение в чёрный список\n"
        "— Штамп Азербайджана: въезд в Армению формально возможен, "
        "но пограничники могут задержать для проверки\n"
        "— Посещение Нагорного Карабаха через армянскую сторону (до 2023) "
        "в базах Азербайджана — основание для пожизненного запрета въезда\n\n"
        "*Как избежать проблем:*\n"
        "— Посещай страны с разными паспортами, если есть возможность\n"
        "— Армения штампы не ставит гражданам многих стран — уточни заранее\n"
        "— Между странами нет прямых рейсов — летай через Тбилиси, Стамбул или Москву\n\n"
        "⚠️ _Ситуация меняется — уточняй в посольстве перед поездкой. Актуально на 2025–2026_"
    ),
    "🇷🇸 Сербия и Косово": (
        "🇷🇸 *Сербия и Косово*\n\n"
        "*Кто с кем несовместим:*\n"
        "Сербия не признаёт независимость Косово и считает его своей территорией. "
        "Въезд в Косово через сербскую границу — проблема при последующем въезде в Сербию.\n\n"
        "*Что грозит:*\n"
        "— Въезд в Косово напрямую из третьей страны (через Северную Македонию, "
        "Черногорию, Албанию) — штамп Косово ставит Сербия в чёрный список\n"
        "— Въезд в Сербию со штампом Косово может привести к допросу, "
        "задержанию или отказу во въезде\n"
        "— Российским гражданам въезд в Косово запрещён с 2022 года\n\n"
        "*Как избежать проблем:*\n"
        "— Гражданам РФ: въезд в Косово закрыт, лучше не пытаться\n"
        "— Другим: если едешь и в Косово, и в Сербию — сначала Сербия, "
        "потом Косово (обратно в Сербию лучше не возвращаться)\n"
        "— Косово не ставит штамп в паспорт при выезде через ряд КПП — уточни заранее\n\n"
        "⚠️ _Ситуация меняется — уточняй в посольстве перед поездкой. Актуально на 2025–2026_"
    ),
    "🇹🇼 Тайвань и Китай": (
        "🇹🇼 *Тайвань и Китай*\n\n"
        "*Кто с кем несовместим:*\n"
        "КНР считает Тайвань своей провинцией. Напряжённость в 2025 году остаётся "
        "высокой. Прямые контакты ограничены, но туризм формально возможен.\n\n"
        "*Что грозит:*\n"
        "— Въезд в Тайвань не создаёт проблем при въезде в КНР — у Тайваня "
        "отдельный паспорт (ROC), а штамп не ставится в большинстве случаев\n"
        "— Публичная критика КПК или поддержка независимости Тайваня на территории "
        "КНР — уголовное преследование\n"
        "— Гражданам КНР: въезд на Тайвань ограничен и требует специального разрешения\n"
        "— Транзит через Гонконг со спорными материалами, книгами, флагами — риск\n\n"
        "*Как избежать проблем:*\n"
        "— Удали со смартфона перед въездом в КНР: VPN-приложения, материалы о "
        "Тайване, Тибете, Синьцзяне\n"
        "— Пользуйся отдельной SIM-картой в Китае\n"
        "— Штамп Тайваня в паспорте технически не проблема для въезда в КНР\n\n"
        "⚠️ _Ситуация меняется — уточняй в посольстве перед поездкой. Актуально на 2025–2026_"
    ),
    "🇰🇵 Северная и Южная Корея": (
        "🇰🇵 *Северная и Южная Корея*\n\n"
        "*Кто с кем несовместим:*\n"
        "Технически страны всё ещё в состоянии войны (перемирие 1953 года). "
        "Граница закрыта наглухо. В 2025 году туризм в КНДР для большинства "
        "иностранцев фактически закрыт.\n\n"
        "*Что грозит:*\n"
        "— Гражданам США въезд в КНДР запрещён американским законом\n"
        "— Штамп КНДР создаёт проблемы при въезде в Южную Корею — "
        "обязательный допрос спецслужбами\n"
        "— Гражданам Южной Кореи въезд в КНДР запрещён под угрозой уголовного "
        "преследования дома\n"
        "— Любая несанкционированная встреча с гражданами КНДР за рубежом — риск\n\n"
        "*Как избежать проблем:*\n"
        "— Просто не езди в КНДР в 2025–2026: туризм закрыт для большинства\n"
        "— Если в будущем откроется — оформляй строго через разрешённые турагентства\n"
        "— Демилитаризованная зона (DMZ) доступна с южнокорейской стороны\n\n"
        "⚠️ _Ситуация меняется — уточняй в посольстве перед поездкой. Актуально на 2025–2026_"
    ),
    "🇲🇦 Марокко и Алжир": (
        "🇲🇦 *Марокко и Алжир*\n\n"
        "*Кто с кем несовместим:*\n"
        "Сухопутная граница между странами закрыта с 1994 года и остаётся закрытой "
        "в 2025 году. Дипломатические отношения разорваны в 2021 году.\n\n"
        "*Что грозит:*\n"
        "— Попытка пересечь сухопутную границу: задержание с обеих сторон\n"
        "— Штамп Марокко не закрывает въезд в Алжир (и наоборот) — "
        "летать можно через третьи страны\n"
        "— Марокканским гражданам въезд в Алжир крайне затруднён и наоборот\n\n"
        "*Как избежать проблем:*\n"
        "— Летай через Париж, Мадрид, Стамбул или Каир\n"
        "— Сухопутный маршрут Марокко→Алжир→Тунис в 2025 году невозможен\n"
        "— Западная Сахара: контролируется Марокко, статус спорный — "
        "штампы оттуда могут вызвать вопросы в Алжире\n\n"
        "⚠️ _Ситуация меняется — уточняй в посольстве перед поездкой. Актуально на 2025–2026_"
    ),
    "🇮🇳 Индия и Пакистан": (
        "🇮🇳 *Индия и Пакистан*\n\n"
        "*Кто с кем несовместим:*\n"
        "После обострения 2019 года (авиаудары) и в 2025 году отношения остаются "
        "крайне напряжёнными. Прямые перелёты практически отсутствуют, "
        "граница закрыта для туристов.\n\n"
        "*Что грозит:*\n"
        "— Штамп Пакистана: Индия может отказать во въезде, особенно через "
        "КПП Вагах (и так закрыт)\n"
        "— Штамп Индии: Пакистан крайне насторожённо относится к туристам, "
        "бывавшим в Индии — допрос, возможен отказ\n"
        "— Посещение Кашмира (индийская сторона) с пакистанским штампом — "
        "повышенный контроль спецслужб\n\n"
        "*Как избежать проблем:*\n"
        "— Летай через Дубай, Доху или Стамбул\n"
        "— Не посещай обе страны в одной поездке\n"
        "— Пакистанская виза сложна для граждан многих стран — оформляй заранее\n"
        "— В Пакистане зарегистрируйся в отеле: FRRO-регистрация обязательна\n\n"
        "⚠️ _Ситуация меняется — уточняй в посольстве перед поездкой. Актуально на 2025–2026_"
    ),
    "🇬🇷 Греция и Турция": (
        "🇬🇷 *Греция и Турция*\n\n"
        "*Кто с кем несовместим:*\n"
        "Официально обе страны — члены НАТО и туристически открыты. Но острые "
        "территориальные споры (Эгейское море, Кипр) периодически обостряются. "
        "В 2025 году напряжённость умеренная, туризм работает нормально.\n\n"
        "*Что грозит:*\n"
        "— Прямой туристической несовместимости нет — штампы обеих стран "
        "не создают проблем\n"
        "— Риск для яхтсменов и дайверов: спорные воды Эгейского моря — "
        "задержание турецкими ВМС при пересечении спорных координат\n"
        "— Острова вблизи турецкого берега (Родос, Кос, Лесбос): в кризисы "
        "возможно ограничение паромного сообщения\n\n"
        "*Как избежать проблем:*\n"
        "— Туристам: никаких ограничений, езди спокойно\n"
        "— Яхтсменам: точно соблюдай морские границы, регистрируйся в портах\n"
        "— Следи за новостями при обострении кризисов\n\n"
        "⚠️ _Ситуация меняется — уточняй в посольстве перед поездкой. Актуально на 2025–2026_"
    ),
    "🇷🇺 Россия и Украина": (
        "🇷🇺 *Россия и Украина — 2025*\n\n"
        "*Общая ситуация:*\n"
        "Война продолжается. Прямого авиа- и железнодорожного сообщения между "
        "странами нет с 2022 года. Въезд крайне ограничен с обеих сторон.\n\n"
        "*Для граждан России:*\n"
        "— Въезд на территорию Украины официально запрещён российским законом "
        "без специального разрешения ФСБ\n"
        "— Украина закрыла въезд для граждан РФ мужского пола 18–60 лет\n"
        "— Женщины и дети из РФ въезжают на Украину в индивидуальном порядке "
        "через третьи страны (Польша, Молдова, Румыния) — возможен отказ\n"
        "— Оккупированные территории (ЛДНР, Херсонская, Запорожская обл., Крым): "
        "въезд туда с украинской стороны означает незаконное пересечение границы "
        "по украинскому законодательству — уголовная ответственность\n"
        "— Крым: въезд через территорию РФ нелегален по украинскому праву, "
        "при освобождении территорий возможно уголовное преследование\n\n"
        "*Для граждан Украины:*\n"
        "— Въезд в Россию официально возможен, но крайне рискован:\n"
        "  мобилизация, фильтрационные проверки, задержания\n"
        "— Мужчины 18–60 лет особенно уязвимы\n"
        "— Украинский паспорт и телефон проверяются на границе\n\n"
        "*Оккупированные территории — отдельные риски:*\n"
        "— Зоны активных боёв: Донецкая, Запорожская, Херсонская области — "
        "жизнеугрожающая опасность для любых иностранцев\n"
        "— Иностранные журналисты и волонтёры: требуется аккредитация, "
        "без неё — задержание любой из сторон\n\n"
        "*Третьим странам:*\n"
        "— Штамп РФ не закрывает въезд на Украину формально, но вызывает вопросы\n"
        "— Украинский штамп не создаёт проблем в России для туристов третьих стран\n\n"
        "⚠️ _Ситуация меняется — уточняй в посольстве перед поездкой. Актуально на 2025–2026_"
    ),
    "🌍 Другие конфликтные пары": (
        "🌍 *Другие конфликтные пары — 2025*\n\n"
        "*🇸🇩 Судан и Южный Судан*\n"
        "Гражданская война в Судане с 2023 года — страна фактически разделена. "
        "Въезд крайне опасен. Южный Судан — отдельное государство с нестабильной "
        "ситуацией. Штампы не пересекаются, но оба региона опасны.\n\n"
        "*🇪🇹 Эфиопия и Эритрея*\n"
        "Граница закрыта большую часть времени с 1998 года. Мирный договор 2018 "
        "не привёл к реальному открытию. В 2025 году сухопутный переход закрыт.\n\n"
        "*🇻🇪 Венесуэла и Колумбия/Гайана*\n"
        "Граница с Колумбией периодически закрывается. Кризис с Гайаной "
        "из-за региона Эссекибо обострился в 2023–2024 гг. Туристам: "
        "избегай приграничных зон.\n\n"
        "*🇸🇾 Сирия и соседи*\n"
        "После падения режима Асада в конце 2024 года — нестабильность. "
        "Турецко-сирийская граница частично закрыта. Израиль нанёс удары "
        "по военной инфраструктуре Сирии в 2024–2025 гг. "
        "Туристический въезд: только через официальные КПП, крайне осторожно.\n\n"
        "*🇱🇾 Ливия*\n"
        "Страна де-факто разделена между востоком и западом. "
        "Штамп Ливии — настороженность в ряде стран. Туризм в 2025 году "
        "практически недоступен из-за безопасности.\n\n"
        "*🇷🇺 Россия и страны Балтии/Польша*\n"
        "Сухопутные границы работают, но с ограничениями. "
        "Граница РФ с Финляндией закрыта с 2023 года по инициативе финской стороны. "
        "Эстония, Латвия, Литва ограничили въезд граждан РФ: "
        "только по гуманитарным основаниям. Польша пропускает по отдельным категориям.\n\n"
        "⚠️ _Ситуация меняется — уточняй в посольстве перед поездкой. Актуально на 2025–2026_"
    ),
}

INCOMPATIBLE_MENU_BTN = list(INCOMPATIBLE_CATEGORIES.keys())


async def show_incompatible_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[btn] for btn in INCOMPATIBLE_MENU_BTN] + [["◀️ Назад"], [HOME_BTN]]
    await update.message.reply_text(
        "⛔ *Несовместимые страны*\n\n"
        "Выбери пару стран, между которыми могут возникнуть проблемы при путешествии:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True),
    )
    return INCOMPATIBLE_MENU


async def incompatible_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == HOME_BTN:
        return await go_home(update, context)
    if text == "◀️ Назад":
        return await show_folder_planning(update, context)
    if text in INCOMPATIBLE_CATEGORIES:
        content = INCOMPATIBLE_CATEGORIES[text]
        keyboard = [["◀️ Назад к категориям"], [HOME_BTN]]
        await update.message.reply_text(
            content,
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True),
        )
        context.user_data["incompatible_topic"] = text
        return INCOMPATIBLE_TOPIC
    return await show_incompatible_menu(update, context)


async def incompatible_topic_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == HOME_BTN:
        return await go_home(update, context)
    return await show_incompatible_menu(update, context)


## ── DRONE DATA ───────────────────────────────────────────────────────────────

DRONE_REGION_BTNS = ["🌍 Европа", "🌏 Азия и СНГ", "🌎 Америка", "🌍 Африка", "🌊 Океания"]

DRONE_DATA = {
    "✅ Можно летать": {
        "🌍 Европа": (
            "✅ *Можно летать — Европа*\n\n"
            "🇦🇹 *Австрия* — макс. 120 м, онлайн-регистрация\n"
            "Сайт: austro.control.at — €30/год | Штраф: до €22 000\n\n"
            "🇦🇱 *Албания* — макс. 120 м, онлайн-регистрация\n"
            "Сайт: aac.gov.al | Штраф: до 500 000 ALL\n\n"
            "🇦🇩 *Андорра* — макс. 120 м, правила EASA\n"
            "Штраф: до €3 000\n\n"
            "🇧🇪 *Бельгия* — макс. 120 м\n"
            "Сайт: droneguide.eu — бесплатно | Штраф: до €300\n\n"
            "🇧🇬 *Болгария* — макс. 120 м\n"
            "Сайт: caa.bg | Штраф: до 50 000 BGN\n\n"
            "🇧🇦 *Босния и Герцеговина* — макс. 120 м\n"
            "Сайт: bhdca.gov.ba | Штраф: до 15 000 BAM\n\n"
            "🇬🇧 *Великобритания* — макс. 120 м\n"
            "Сайт: register-drones.service.gov.uk — £9/год | Штраф: до £2 500\n\n"
            "🇭🇺 *Венгрия* — макс. 120 м\n"
            "Сайт: kozkozl.hu | Штраф: до HUF 500 000\n\n"
            "🇬🇷 *Греция* — макс. 120 м, запрет над Акрополем\n"
            "Сайт: hcaa.gr — бесплатно | Штраф: до €300 000\n\n"
            "🇩🇰 *Дания* — макс. 120 м\n"
            "Сайт: droneluftrum.dk — бесплатно | Штраф: до 10 000 DKK\n\n"
            "🇮🇸 *Исландия* — макс. 120 м\n"
            "Сайт: icetra.is — бесплатно | Штраф: до ISK 500 000\n\n"
            "🇪🇸 *Испания* — макс. 120 м\n"
            "Сайт: enaire.es — бесплатно | Штраф: до €225 000\n\n"
            "🇮🇹 *Италия* — макс. 120 м, запрет над историч. центрами\n"
            "Сайт: d-flight.it — €35 | Штраф: до €50 000\n\n"
            "🇱🇻 *Латвия* — макс. 120 м\n"
            "Сайт: caa.lv — €25 | Штраф: до €1 000\n\n"
            "🇱🇮 *Лихтенштейн* — правила EASA\n"
            "Штраф: до CHF 5 000\n\n"
            "🇱🇹 *Литва* — макс. 120 м\n"
            "Сайт: cregs.lt — бесплатно | Штраф: до €3 000\n\n"
            "🇱🇺 *Люксембург* — макс. 120 м\n"
            "Сайт: laa.lu | Штраф: до €500\n\n"
            "🇲🇹 *Мальта* — макс. 120 м\n"
            "Сайт: caa.com.mt — €20 | Штраф: до €50 000\n\n"
            "🇲🇩 *Молдова* — регистрация в CAMC\n"
            "Штраф: до MDL 10 000\n\n"
            "🇲🇨 *Монако* — правила EASA/Франции\n"
            "Штраф: до €50 000\n\n"
            "🇳🇱 *Нидерланды* — макс. 120 м\n"
            "Сайт: rdi.nl — бесплатно | Штраф: до €900\n\n"
            "🇳🇴 *Норвегия* — макс. 120 м\n"
            "Сайт: luftfartstilsynet.no — бесплатно | Штраф: до NOK 30 000\n\n"
            "🇵🇱 *Польша* — макс. 120 м\n"
            "Сайт: droneradar.eu — €8 | Штраф: до PLN 30 000\n\n"
            "🇵🇹 *Португалия* — макс. 120 м\n"
            "Сайт: anac.pt — бесплатно | Штраф: до €3 740\n\n"
            "🇷🇴 *Румыния* — макс. 120 м\n"
            "Сайт: caa.ro — бесплатно | Штраф: до RON 10 000\n\n"
            "🇸🇲 *Сан-Марино* — правила EASA/Италии\n"
            "Штраф: до €50 000\n\n"
            "🇲🇰 *Северная Македония* — макс. 120 м\n"
            "Сайт: caa.mk | Штраф: до MKD 100 000\n\n"
            "🇷🇸 *Сербия* — макс. 120 м\n"
            "Сайт: caa.rs | Штраф: до RSD 200 000\n\n"
            "🇸🇰 *Словакия* — макс. 120 м\n"
            "Сайт: caa.sk — бесплатно | Штраф: до €300\n\n"
            "🇸🇮 *Словения* — макс. 120 м\n"
            "Сайт: caa.si | Штраф: до €8 000\n\n"
            "🇺🇦 *Украина* — макс. 120 м (военные зоны — полный запрет)\n"
            "Сайт: avia.gov.ua | Штраф: до UAH 51 000\n\n"
            "🇫🇮 *Финляндия* — макс. 120 м\n"
            "Сайт: traficom.fi — €30 | Штраф: до €2 000\n\n"
            "🇫🇷 *Франция* — макс. 120 м, запрет над Парижем\n"
            "Сайт: alphatango.aero — бесплатно | Штраф: до €15 000\n\n"
            "🇭🇷 *Хорватия* — макс. 120 м\n"
            "Сайт: ccaa.hr — бесплатно | Штраф: до €660\n\n"
            "🇲🇪 *Черногория* — макс. 120 м\n"
            "Сайт: caa.me | Штраф: до €5 000\n\n"
            "🇨🇿 *Чехия* — макс. 120 м\n"
            "Сайт: caa.cz — бесплатно | Штраф: до 100 000 CZK\n\n"
            "🇨🇭 *Швейцария* — макс. 120 м\n"
            "Сайт: bazl.admin.ch — CHF 70 | Штраф: до CHF 20 000\n\n"
            "🇸🇪 *Швеция* — макс. 120 м\n"
            "Сайт: transportstyrelsen.se — SEK 600 | Штраф: до SEK 150 000\n\n"
            "🇪🇪 *Эстония* — макс. 120 м\n"
            "Сайт: ecaa.ee — бесплатно | Штраф: до €1 200\n\n"
            "🇮🇪 *Ирландия* — макс. 120 м\n"
            "Сайт: iaa.ie — €30 | Штраф: до €50 000\n\n"
            "⚠️ Правила меняются — проверяй на сайте авиационного ведомства страны перед поездкой"
        ),
        "🌏 Азия и СНГ": (
            "✅ *Можно летать — Азия и СНГ*\n\n"
            "🇦🇿 *Азербайджан* — макс. 100 м\n"
            "Сайт: mga.gov.az | Штраф: до 5 000 AZN\n\n"
            "🇦🇲 *Армения* — макс. 120 м\n"
            "Регистрация в GCAA | Штраф: до 500 000 AMD\n\n"
            "🇧🇩 *Бангладеш* — регистрация CAAB\n"
            "Сайт: caab.gov.bd | Штраф: до 200 000 BDT\n\n"
            "🇧🇹 *Бутан* — с разрешения Министерства туризма\n"
            "Штраф: конфискация + штраф\n\n"
            "🇻🇳 *Вьетнам* — для иностранцев фактически нужно спецразрешение\n"
            "Штраф: до VND 40 000 000\n\n"
            "🇬🇪 *Грузия* — макс. 100 м (запрет в зонах конфликтов)\n"
            "Сайт: gcaa.gov.ge — бесплатно | Штраф: до GEL 500\n\n"
            "🇮🇱 *Израиль* — регистрация CAAI\n"
            "Сайт: caa.gov.il — ₪300 | Штраф: до ₪100 000\n\n"
            "🇯🇴 *Иордания* — регистрация CARC\n"
            "Сайт: carc.gov.jo — JD25 | Штраф: до JD50 000\n\n"
            "🇰🇿 *Казахстан* — макс. 150 м\n"
            "Сайт: caak.kz | Штраф: до 50 МРП\n\n"
            "🇰🇬 *Кыргызстан* — регистрация в SCAA\n"
            "Штраф: до 50 000 KGS\n\n"
            "🇱🇦 *Лаос* — регистрация в LNCA\n"
            "Штраф: до 10 000 000 LAK\n\n"
            "🇲🇻 *Мальдивы* — вне охраняемых зон\n"
            "Сайт: caac.gov.mv | Штраф: до MVR 100 000\n\n"
            "🇲🇳 *Монголия* — регистрация CAAC\n"
            "Сайт: mcaa.gov.mn | Штраф: до MNT 5 000 000\n\n"
            "🇴🇲 *Оман* — регистрация CAAM\n"
            "Сайт: caa.gov.om — OMR 20 | Штраф: до OMR 10 000\n\n"
            "🇸🇦 *Саудовская Аравия* — регистрация GACA (запрет над Меккой и Мединой)\n"
            "Сайт: gaca.gov.sa — SAR 200 | Штраф: до SAR 100 000\n\n"
            "🇹🇭 *Таиланд* — регистрация NBTC\n"
            "Сайт: ntbc.or.th — THB 500 | Штраф: до THB 40 000 или тюрьма 1 год\n\n"
            "🇹🇱 *Тимор-Лесте* — регистрация в AACTL\n"
            "Штраф: до $5 000\n\n"
            "🇹🇲 *Туркменистан* — нужно спецразрешение\n"
            "Штраф: конфискация\n\n"
            "🇹🇷 *Турция* — макс. 120 м\n"
            "Сайт: shgm.gov.tr — ₺500 | Штраф: до ₺30 000\n\n"
            "🇺🇿 *Узбекистан* — регистрация в Госкомитете ГА\n"
            "Штраф: до 100 базовых величин\n\n"
            "🇵🇭 *Филиппины* — регистрация CAB\n"
            "Сайт: caab.gov.ph — ₱1 600 | Штраф: до ₱2 000 000\n\n"
            "🇱🇰 *Шри-Ланка* — регистрация CAASL\n"
            "Сайт: caa.lk — LKR 10 000 | Штраф: до LKR 500 000\n\n"
            "🇯🇵 *Япония* — макс. 150 м, регистрация обязательна\n"
            "Сайт: drone.mlit.go.jp | Штраф: до ¥500 000\n\n"
            "⚠️ Правила меняются — проверяй на сайте авиационного ведомства страны перед поездкой"
        ),
        "🌎 Америка": (
            "✅ *Можно летать — Америка*\n\n"
            "🇦🇷 *Аргентина* — макс. 120 м\n"
            "Сайт: anac.gob.ar — бесплатно | Штраф: до 300 000 ARS\n\n"
            "🇧🇸 *Багамы* — макс. 300 футов\n"
            "Регистрация в CAAB | Штраф: до $10 000\n\n"
            "🇧🇧 *Барбадос* — регистрация в GCAA\n"
            "Штраф: до $50 000 BBD\n\n"
            "🇧🇿 *Белиз* — регистрация в DGCA\n"
            "Штраф: до $10 000 BZD\n\n"
            "🇧🇴 *Боливия* — макс. 150 м\n"
            "Регистрация в DGAC | Штраф: до 100 000 BOB\n\n"
            "🇧🇷 *Бразилия* — макс. 120 м\n"
            "Сайт: anac.gov.br — бесплатно | Штраф: до R$50 000\n\n"
            "🇻🇪 *Венесуэла* — регистрация в INAC\n"
            "Сайт: inac.gob.ve | Штраф: до BsD 10 000\n\n"
            "🇬🇹 *Гватемала* — регистрация в DGAC\n"
            "Штраф: до Q10 000\n\n"
            "🇬🇾 *Гайана* — регистрация в GCAA\n"
            "Штраф: до G$100 000\n\n"
            "🇭🇹 *Гаити* — регистрация в AAN\n"
            "Штраф: до HTG 100 000\n\n"
            "🇭🇳 *Гондурас* — регистрация в AHAC\n"
            "Штраф: до L100 000\n\n"
            "🇬🇩 *Гренада* — регистрация в ECCAA\n"
            "Штраф: до $10 000 XCD\n\n"
            "🇩🇲 *Доминика* — регистрация в ECCAA\n"
            "Штраф: до $10 000 XCD\n\n"
            "🇩🇴 *Доминиканская Республика* — регистрация в IDAC\n"
            "Сайт: idac.gov.do | Штраф: до 500 000 DOP\n\n"
            "🇨🇦 *Канада* — макс. 122 м\n"
            "Сайт: tc.canada.ca — $5 CAD | Штраф: до $3 000 CAD\n\n"
            "🇲🇽 *Мексика* — макс. 120 м\n"
            "Сайт: afac.gob.mx — бесплатно | Штраф: до $4 000 USD\n\n"
            "🇳🇮 *Никарагуа* — регистрация в INAC\n"
            "Штраф: до C$50 000\n\n"
            "🇵🇦 *Панама* — регистрация в AAC\n"
            "Сайт: aac.gob.pa | Штраф: до $10 000\n\n"
            "🇵🇾 *Парагвай* — регистрация в DINAC\n"
            "Сайт: dinac.gov.py | Штраф: до ₲50 000 000\n\n"
            "🇵🇪 *Перу* — регистрация в DGAC\n"
            "Сайт: dgac.gob.pe | Штраф: до S/50 000\n\n"
            "🇸🇻 *Сальвадор* — регистрация в AAC\n"
            "Штраф: до $5 000\n\n"
            "🇸🇷 *Суринам* — регистрация в CAD\n"
            "Штраф: до SRD 10 000\n\n"
            "🇹🇹 *Тринидад и Тобаго* — регистрация в TTCAA\n"
            "Сайт: ttcaa.com — TT$1 000 | Штраф: до TT$100 000\n\n"
            "🇨🇱 *Чили* — макс. 130 м\n"
            "Сайт: dgac.gob.cl — бесплатно | Штраф: до 60 UTM\n\n"
            "🇪🇨 *Эквадор* — макс. 120 м\n"
            "Сайт: dgac.gob.ec | Штраф: до $5 000\n\n"
            "🇯🇲 *Ямайка* — регистрация в JCAA\n"
            "Сайт: jcaa.gov.jm | Штраф: до J$5 000 000\n\n"
            "⚠️ Правила меняются — проверяй на сайте авиационного ведомства страны перед поездкой"
        ),
        "🌍 Африка": (
            "✅ *Можно летать — Африка*\n\n"
            "🇩🇿 *Алжир* — макс. 100 м, регистрация\n"
            "Штраф: до 500 000 DZD\n\n"
            "🇦🇴 *Ангола* — регистрация в INAVIC\n"
            "Штраф: до $2 000\n\n"
            "🇧🇯 *Бенин* — регистрация в ANAC\n"
            "Штраф: до 5 000 000 XOF\n\n"
            "🇧🇼 *Ботсвана* — регистрация в CAAB\n"
            "Штраф: до $10 000 BWP\n\n"
            "🇧🇫 *Буркина-Фасо* — регистрация в ANAC\n"
            "Штраф: до 10 000 000 XOF\n\n"
            "🇧🇮 *Бурунди* — регистрация в ACRB\n"
            "Штраф: до $5 000\n\n"
            "🇬🇦 *Габон* — регистрация в ANAC\n"
            "Штраф: до 5 000 000 XAF\n\n"
            "🇬🇲 *Гамбия* — регистрация в GCAA\n"
            "Штраф: до D100 000\n\n"
            "🇬🇭 *Гана* — макс. 400 футов\n"
            "Сайт: gcaa.gov.gh — $50 | Штраф: до GHS 20 000\n\n"
            "🇬🇳 *Гвинея* — регистрация в ANAC\n"
            "Штраф: до GNF 10 000 000\n\n"
            "🇬🇼 *Гвинея-Бисау* — регистрация в AAAC\n"
            "Штраф: до $5 000\n\n"
            "🇩🇯 *Джибути* — регистрация в ADDS\n"
            "Штраф: до $5 000\n\n"
            "🇨🇩 *ДР Конго* — регистрация в CAA\n"
            "Штраф: до $10 000\n\n"
            "🇿🇲 *Замбия* — регистрация в DCAA\n"
            "Сайт: dcaa.gov.zm — K500 | Штраф: до K100 000\n\n"
            "🇿🇼 *Зимбабве* — регистрация в CAAZ\n"
            "Сайт: caaz.co.zw — $150 | Штраф: до $200 000\n\n"
            "🇨🇻 *Кабо-Верде* — регистрация в IACV\n"
            "Штраф: до €5 000\n\n"
            "🇨🇲 *Камерун* — регистрация в CCAA\n"
            "Штраф: до 5 000 000 XAF\n\n"
            "🇰🇪 *Кения* — регистрация в KCAA\n"
            "Сайт: kcaa.or.ke — KES 5 000 | Штраф: до KES 1 000 000\n\n"
            "🇨🇬 *Конго* — регистрация в ANAC\n"
            "Штраф: до CDF 5 000 000\n\n"
            "🇱🇸 *Лесото* — регистрация в DCSL\n"
            "Штраф: до M10 000\n\n"
            "🇱🇷 *Либерия* — регистрация в LCAA\n"
            "Штраф: до $5 000\n\n"
            "🇲🇬 *Мадагаскар* — регистрация в AAIM\n"
            "Штраф: до MGA 5 000 000\n\n"
            "🇲🇼 *Малави* — регистрация в DCA\n"
            "Сайт: dca.gov.mw | Штраф: до MWK 5 000 000\n\n"
            "🇲🇱 *Мали* — регистрация в ANAC\n"
            "Штраф: до XOF 10 000 000\n\n"
            "🇲🇦 *Марокко* — регистрация в RACAM\n"
            "Сайт: racam.ma — MAD 500 | Штраф: до MAD 100 000\n\n"
            "🇲🇺 *Маврикий* — регистрация в ADSU\n"
            "Сайт: adsu.govmu.org — бесплатно | Штраф: до Rs 200 000\n\n"
            "🇲🇷 *Мавритания* — регистрация в ANAC\n"
            "Штраф: до MRU 100 000\n\n"
            "🇲🇿 *Мозамбик* — регистрация в IACM\n"
            "Штраф: до MZN 500 000\n\n"
            "🇳🇦 *Намибия* — регистрация в CAAN\n"
            "Штраф: до N$100 000\n\n"
            "🇳🇪 *Нигер* — регистрация в ANAC\n"
            "Штраф: до XOF 10 000 000\n\n"
            "🇳🇬 *Нигерия* — регистрация в NCAA\n"
            "Сайт: ncaa.gov.ng — N150 000/год | Штраф: до N2 000 000\n\n"
            "🇷🇼 *Руанда* — регистрация в RCAA\n"
            "Сайт: rcaa.gov.rw — $150 | Штраф: до RWF 5 000 000\n\n"
            "🇸🇹 *Сан-Томе и Принсипи* — регистрация в AAASTP\n"
            "Штраф: до STN 10 000\n\n"
            "🇸🇳 *Сенегал* — регистрация в ANACIM\n"
            "Штраф: до XOF 10 000 000\n\n"
            "🇸🇩 *Судан* — регистрация в SCAA\n"
            "Штраф: до SDG 100 000\n\n"
            "🇸🇱 *Сьерра-Леоне* — регистрация в SLCAA\n"
            "Штраф: до SLL 5 000 000\n\n"
            "🇹🇿 *Танзания* — регистрация в TCAA\n"
            "Сайт: tcaa.go.tz — $50 | Штраф: до TZS 20 000 000\n\n"
            "🇹🇬 *Того* — регистрация в ANAC\n"
            "Штраф: до XOF 5 000 000\n\n"
            "🇺🇬 *Уганда* — регистрация в UCAA\n"
            "Сайт: caa.go.ug — $200 | Штраф: до UGX 10 000 000\n\n"
            "🇪🇷 *Эритрея* — регистрация в CAAE\n"
            "Штраф: до $10 000\n\n"
            "🇸🇿 *Эсватини* — регистрация в COTA\n"
            "Штраф: до E10 000\n\n"
            "🇿🇦 *ЮАР* — макс. 122 м\n"
            "Сайт: sacaa.org.za — R1 200 | Штраф: до R50 000 или тюрьма\n\n"
            "🇸🇸 *Южный Судан* — регистрация в SSCA\n"
            "Штраф: до $10 000\n\n"
            "⚠️ Правила меняются — проверяй на сайте авиационного ведомства страны перед поездкой"
        ),
        "🌊 Океания": (
            "✅ *Можно летать — Океания*\n\n"
            "🇦🇺 *Австралия* — макс. 120 м, визуальный контроль обязателен\n"
            "Сайт: casa.gov.au/drones — бесплатно\n"
            "Запрет: над людьми, аэропортами, нацпарками без разрешения\n"
            "Штраф: до 11 000 AUD\n\n"
            "🇻🇺 *Вануату* — регистрация в Vanuatu CAA\n"
            "Штраф: до VT 500 000\n\n"
            "🇳🇿 *Новая Зеландия* — макс. 120 м\n"
            "Сайт: aviation.govt.nz — бесплатно | Штраф: до NZ$20 000\n\n"
            "🇵🇬 *Папуа Новая Гвинея* — регистрация в CASAPNG\n"
            "Штраф: до K10 000\n\n"
            "🇸🇧 *Соломоновы острова* — регистрация в CAASI\n"
            "Штраф: до SI$10 000\n\n"
            "⚠️ Правила меняются — проверяй на сайте авиационного ведомства страны перед поездкой"
        ),
    },
    "📋 Нужно разрешение": {
        "🌍 Европа": (
            "📋 *Нужно разрешение — Европа*\n\n"
            "В большинстве стран Европы достаточно регистрации — они указаны в разделе ✅.\n"
            "Специального разрешения заранее в Европе обычно не требуется.\n\n"
            "⚠️ Исключения — зоны ограничений:\n"
            "— Над военными объектами, тюрьмами и стратегическими объектами — везде запрещено\n"
            "— Над государственными резиденциями — нужно спецразрешение\n"
            "— В контролируемом воздушном пространстве (CTR) — нужен файлплан\n\n"
            "⚠️ Правила меняются — проверяй на сайте авиационного ведомства страны перед поездкой"
        ),
        "🌏 Азия и СНГ": (
            "📋 *Нужно разрешение — Азия и СНГ*\n\n"
            "🇦🇪 *ОАЭ*\n"
            "Что оформить: разрешение GCAA + регистрация дрона\n"
            "Орган: GCAA (dcaa.gov.ae) | Стоимость: AED 300–500\n"
            "Срок: 5–10 рабочих дней | Штраф: до AED 20 000 + тюрьма\n"
            "Инструкция: зарегистрируйся → загрузи данные дрона → оплати → получи разрешение → декларируй в аэропорту\n\n"
            "🇧🇭 *Бахрейн*\n"
            "Орган: BCAA (caa.gov.bh) | Стоимость: BHD 50\n"
            "Срок: 10 рабочих дней | Штраф: до BHD 10 000\n"
            "Инструкция: подай заявку на caa.gov.bh → укажи маршрут и цель → оплати → получи по email\n\n"
            "🇨🇳 *Китай*\n"
            "Орган: CAAC (caac.gov.cn) | Стоимость: бесплатно (регистрация)\n"
            "Штраф: до ¥500 000 + уголовная ответственность\n"
            "Инструкция: зарегистрируйся на ucas.caac.gov.cn → проверяй зоны в UTMISS → в нацпарках нужно отдельное разрешение → дроны DJI автоблокируют запрещённые зоны\n\n"
            "🇮🇳 *Индия*\n"
            "Орган: DGCA (digitalsky.dgca.gov.in) | Стоимость: бесплатно (до 2 кг)\n"
            "Штраф: до ₹1 000 000 + тюрьма до 2 лет\n"
            "Инструкция: зарегистрируйся → дроны до 250 г (Nano) без регистрации → летать только в Green зонах → проверяй Digital Sky app\n\n"
            "🇮🇩 *Индонезия*\n"
            "Орган: DGCA (hubud.dephub.go.id) | Стоимость: IDR 350 000\n"
            "Срок: 5–14 дней | Штраф: до IDR 3 000 000 000\n"
            "Инструкция: зарегистрируй на e-registrasi.dephub.go.id → для Бали: разрешение + Управление нацпарка → над Borobudur и Prambanan — абсолютный запрет\n\n"
            "🇮🇷 *Иран*\n"
            "Орган: CAO (cao.ir) | Стоимость: $50–200\n"
            "Срок: несколько недель | Штраф: конфискация + тюрьма\n\n"
            "🇮🇶 *Ирак*\n"
            "Орган: ICAA | Стоимость: уточняй в посольстве\n"
            "Штраф: конфискация + тюрьма\n\n"
            "🇰🇼 *Кувейт*\n"
            "Орган: DGCA (dgca.gov.kw) | Стоимость: KWD 20–100\n"
            "Срок: 2 недели | Штраф: конфискация + до KWD 50 000\n"
            "Инструкция: подай заявление → укажи модель, серийный номер, зону → получи письменное разрешение → имей его при себе\n\n"
            "🇱🇧 *Ливан*\n"
            "Орган: DGCA (dgca.gov.lb) | Штраф: конфискация + штраф\n\n"
            "🇲🇾 *Малайзия*\n"
            "Орган: CAAM (caam.gov.my) | Стоимость: MYR 100–500\n"
            "Срок: 3–5 дней | Штраф: до MYR 50 000\n"
            "Инструкция: зарегистрируй на portal.caam.gov.my → дроны до 250 г без регистрации → над Куала-Лумпуром (Petronas, KLIA) — спецразрешение → Борнео — разрешение военного ведомства\n\n"
            "🇲🇻 *Мальдивы*\n"
            "Орган: Ministry of Tourism (tourism.gov.mv) | Стоимость: $100\n"
            "Срок: 5 рабочих дней | Штраф: до MVR 100 000\n\n"
            "🇲🇲 *Мьянма*\n"
            "Орган: DGCA Myanmar | Срок: 2–4 недели | Штраф: конфискация + тюрьма\n"
            "Инструкция: запрос через посольство или туроператора → предоставь паспорт и характеристики дрона → особые ограничения у буддийских святынь\n\n"
            "🇳🇵 *Непал*\n"
            "Орган: CAAN (caanepal.org.np) | Стоимость: $50–250\n"
            "Срок: 1–3 недели | Штраф: до NPR 500 000\n"
            "Инструкция: подай заявку → для нацпарков нужен DNPWC + CAAN → Everest area — $250 → без разрешения конфискуют на КПП\n\n"
            "🇵🇰 *Пакистан*\n"
            "Орган: CAA (caapakistan.com.pk) | Стоимость: PKR 10 000\n"
            "Срок: 3–4 недели | Штраф: конфискация + тюрьма\n\n"
            "🇶🇦 *Катар*\n"
            "Орган: QCAA (qcaa.gov.qa) | Стоимость: QAR 100–500\n"
            "Срок: 5–10 дней | Штраф: до QAR 200 000\n"
            "Инструкция: зарегистрируйся на portal.qcaa.gov.qa → подай заявку за 10 дней → в Fan Zones и Lusail — отдельные ограничения\n\n"
            "🇸🇾 *Сирия*\n"
            "Фактически запрещено | Штраф: конфискация + арест\n\n"
            "🇹🇯 *Таджикистан*\n"
            "Орган: SCAA (khi.tj) | Стоимость: уточняй в посольстве\n"
            "Штраф: конфискация\n\n"
            "🇾🇪 *Йемен*\n"
            "Зона конфликта — фактически запрещено\n"
            "Штраф: конфискация + арест\n\n"
            "⚠️ Правила меняются — проверяй на сайте авиационного ведомства страны перед поездкой"
        ),
        "🌎 Америка": (
            "📋 *Нужно разрешение — Америка*\n\n"
            "🇨🇺 *Куба*\n"
            "Что оформить: разрешение IACC + таможенная декларация\n"
            "Орган: IACC (iacc.cu) | Стоимость: $100–200\n"
            "Срок: 30 дней | Штраф: конфискация + штраф до CUP 10 000\n"
            "Инструкция: отправь заявление в IACC за месяц → задекларируй дрон на таможне → съёмка военных объектов и портов — абсолютный запрет\n\n"
            "🇺🇸 *США*\n"
            "Что оформить: регистрация FAA (faa.gov/uas)\n"
            "Стоимость: $5 | Макс. высота: 400 футов (120 м)\n"
            "Штраф: до $27 500 (гражданский) или $250 000 (уголовный)\n"
            "Инструкция: зарегистрируйся на faa.gov/uas → проверяй зоны в B4UFLY или AirMap → запрет над Вашингтоном, военными объектами, нацпарками\n\n"
            "В большинстве других стран Южной и Центральной Америки достаточно регистрации (см. раздел ✅).\n\n"
            "⚠️ Правила меняются — проверяй на сайте авиационного ведомства страны перед поездкой"
        ),
        "🌍 Африка": (
            "📋 *Нужно разрешение — Африка*\n\n"
            "🇪🇬 *Египет*\n"
            "Что оформить: разрешение ECAA + таможенная декларация\n"
            "Орган: ECAA (ecaa.gov.eg) | Стоимость: EGP 1 000–5 000\n"
            "Срок: 2–4 недели | Штраф: конфискация + до 3 лет тюрьмы\n"
            "Инструкция: подай заявку в ECAA заранее → задекларируй дрон на таможне → без разрешения конфискуют в аэропорту → для съёмки пирамид нужно отдельное разрешение Ministry of Antiquities\n\n"
            "🇹🇿 *Танзания (Занзибар)*\n"
            "Для Занзибара — отдельное разрешение правительства Занзибара\n"
            "Орган: ZAC (zac.go.tz) | Стоимость: $200 | Срок: 2 недели\n\n"
            "В остальных африканских странах обычно достаточно регистрации — см. раздел ✅.\n\n"
            "⚠️ Правила меняются — проверяй на сайте авиационного ведомства страны перед поездкой"
        ),
        "🌊 Океания": (
            "📋 *Нужно разрешение — Океания*\n\n"
            "В Австралии и Новой Зеландии достаточно регистрации (см. раздел ✅).\n\n"
            "Для коммерческих полётов в большинстве стран Океании требуется лицензия оператора.\n\n"
            "🇫🇯 *Фиджи* — регистрация в CAAF\n"
            "Штраф: до FJD 50 000\n\n"
            "🇼🇸 *Самоа* — регистрация в CAA\n"
            "Штраф: уточняй у авиационного ведомства\n\n"
            "⚠️ Правила меняются — проверяй на сайте авиационного ведомства страны перед поездкой"
        ),
    },
    "🚫 Запрещено": {
        "🌍 Европа": (
            "🚫 *Запрещено — Европа*\n\n"
            "🇻🇦 *Ватикан*\n"
            "Полный запрет над всей территорией\n"
            "Штраф: немедленное задержание (Италия/Ватикан)\n\n"
            "🌍 *Запретные зоны во всех странах Европы:*\n"
            "— Военные базы и объекты\n"
            "— Атомные станции\n"
            "— Государственные резиденции и дворцы\n"
            "— Контролируемое воздушное пространство без разрешения\n"
            "— Нацпарки (во многих странах — с ограничениями)\n\n"
            "⚠️ Правила меняются — проверяй на сайте авиационного ведомства страны перед поездкой"
        ),
        "🌏 Азия и СНГ": (
            "🚫 *Запрещено — Азия и СНГ*\n\n"
            "🇰🇵 *КНДР*\n"
            "Абсолютный запрет | Наказание: арест + уголовное преследование\n\n"
            "🇮🇷 *Иран* (без разрешения)\n"
            "Ввоз без разрешения = конфискация\n"
            "Наказание: конфискация + тюрьма до нескольких лет\n\n"
            "🇮🇶 *Ирак* (для туристов)\n"
            "Запрещено без специального разрешения\n"
            "Наказание: конфискация + задержание\n\n"
            "🇸🇾 *Сирия*\n"
            "Полный запрет | Наказание: конфискация + немедленный арест\n\n"
            "🇾🇪 *Йемен*\n"
            "Абсолютный запрет — зона вооружённого конфликта\n"
            "Наказание: конфискация + арест\n\n"
            "🇸🇦 *Саудовская Аравия* (Мекка и Медина)\n"
            "Абсолютный запрет над священными городами\n"
            "Наказание: конфискация + тюрьма\n\n"
            "🇹🇯 *Таджикистан* (без разрешения)\n"
            "Конфискация на границе | Штраф: до 50 000 TJS\n\n"
            "🇲🇲 *Мьянма* (без разрешения)\n"
            "Конфискация при въезде | Наказание: тюрьма до 3 лет\n\n"
            "🇵🇰 *Пакистан* (без разрешения)\n"
            "Конфискация + уголовное преследование | Штраф: тюрьма до 2 лет\n\n"
            "🇸🇬 *Сингапур*\n"
            "Полёты запрещены без разрешения CAAS\n"
            "Сайт: caas.gov.sg | Штраф: до S$20 000 или тюрьма 12 месяцев\n\n"
            "🇧🇳 *Бруней*\n"
            "Полёты запрещены без разрешения CABD\n"
            "Штраф: до BND 100 000\n\n"
            "🇰🇼 *Кувейт* (без разрешения)\n"
            "Полёт без разрешения = немедленная конфискация\n"
            "Штраф: до KWD 50 000\n\n"
            "🇹🇭 *Таиланд* (запретные зоны)\n"
            "Запрет над нацпарками и пляжами без разрешения\n"
            "Штраф: до THB 40 000 или тюрьма 1 год\n\n"
            "🇳🇵 *Непал* (нацпарки без разрешения)\n"
            "Запрет в нацпарках без разрешения CAAN + DNPWC\n"
            "Штраф: до NPR 500 000\n\n"
            "⚠️ Правила меняются — проверяй на сайте авиационного ведомства страны перед поездкой"
        ),
        "🌎 Америка": (
            "🚫 *Запрещено — Америка*\n\n"
            "🇨🇺 *Куба* (фактически)\n"
            "Ввоз дрона без разрешения = конфискация на таможне\n"
            "Штраф: конфискация + штраф до CUP 10 000\n\n"
            "🇧🇧 *Барбадос* (охраняемые зоны)\n"
            "Запрет в прибрежных охраняемых зонах и заповедниках\n"
            "Штраф: до $50 000 BBD + депортация\n\n"
            "🌎 *Запретные зоны во всех странах Америки:*\n"
            "— Зоны аэропортов (5–8 км)\n"
            "— Военные базы и объекты\n"
            "— Национальные парки (США — полный запрет; остальные — с разрешения)\n"
            "— Государственные резиденции\n\n"
            "⚠️ Правила меняются — проверяй на сайте авиационного ведомства страны перед поездкой"
        ),
        "🌍 Африка": (
            "🚫 *Запрещено — Африка*\n\n"
            "🇪🇹 *Эфиопия*\n"
            "Полный запрет на ввоз и использование дронов\n"
            "Наказание: конфискация на таможне + штраф + возможно задержание\n\n"
            "🇲🇦 *Марокко* (зоны запрета)\n"
            "Запрет над Королевскими дворцами, военными базами, частью Сахары\n"
            "Штраф: до MAD 100 000 + тюрьма\n\n"
            "🇸🇩 *Судан* (без разрешения)\n"
            "Конфискация + штраф | Возможна тюрьма\n\n"
            "🇲🇿 *Мозамбик* (без регистрации)\n"
            "Конфискация | Штраф: до MZN 500 000\n\n"
            "🌍 *Запретные зоны во всех странах Африки:*\n"
            "— Военные объекты\n"
            "— Государственные резиденции и президентские дворцы\n"
            "— Зоны конфликтов\n"
            "— Нацпарки (во многих странах — нужно отдельное разрешение)\n\n"
            "⚠️ Правила меняются — проверяй на сайте авиационного ведомства страны перед поездкой"
        ),
        "🌊 Океания": (
            "🚫 *Запрещено — Океания*\n\n"
            "В Австралии и Новой Зеландии полёты разрешены с регистрацией.\n"
            "Специфических стран с полным запретом в Океании нет.\n\n"
            "🌊 *Запретные зоны во всех странах Океании:*\n"
            "— Военные объекты\n"
            "— Аэропорты и подходы к ним\n"
            "— Нацпарки и морские заповедники\n"
            "— Коренные территории (требуется разрешение общины)\n\n"
            "⚠️ Правила меняются — проверяй на сайте авиационного ведомства страны перед поездкой"
        ),
    },
}

DRONE_RULES_TEXT = """⚠️ *Общие правила полётов дронов везде*

Эти правила действуют в большинстве стран мира независимо от местного законодательства.

📏 *Высота*
— Максимум 120 м (400 футов) над землёй
— В Японии и ряде стран — до 150 м
— Вблизи аэропортов высота строго ограничена

👁 *Визуальный контроль (VLOS)*
— Дрон всегда должен быть в зоне прямой видимости
— Полёты «за горизонт» (BVLOS) требуют специальных разрешений
— В сумерках и ночью — запрещено в большинстве стран

🚫 *Что НЕЛЬЗЯ нигде:*
— Летать над скоплением людей (митинги, концерты, пляжи)
— Летать ближе 5-8 км от аэропортов без разрешения
— Летать над военными объектами, тюрьмами, ядерными станциями
— Летать над государственными резиденциями и дворцами
— Летать в национальных парках без специального разрешения
— Нарушать приватность людей (съёмка через окна, во дворах)
— Перевозить дроны в багаже с заряженными аккумуляторами

✈️ *Перелёт с дроном*
— Аккумуляторы LiPo — только в ручной клади
— На одного пассажира обычно: до 2 запасных батарей ≤100 Вт·ч без ограничений, 101-160 Вт·ч — с разрешения авиакомпании
— Дрон в ручной клади или зарегистрированном багаже (без батарей)
— Уточняй правила конкретной авиакомпании

📱 *Приложения для проверки зон*
— DJI Fly Safe Map: fly.dji.com/mobile-sdk-doc/flysafe
— AirMap: airmap.com
— Алтик (Россия и СНГ): uavportal.ru
— UAV Forecast: uavforecast.com

📋 *Что брать с собой на полёт*
— Документ на право полёта (регистрация / разрешение)
— Паспорт / ID
— Полис страхования ответственности (требуется в ЕС)
— Серийный номер дрона и чек о покупке

🌍 *Особые случаи*
— *Дроны DJI*: встроенная геозащита — автоматически блокируют запрещённые зоны
— *Конкурсные/FPV дроны*: отдельные правила, обычно нужен клуб или лицензия
— *Коммерческие полёты*: требуют лицензию пилота дрона в большинстве стран ЕС

🔗 *Полезные ресурсы*
— ICAO: icao.int/safety/UA
— Европа: easa.europa.eu/domains/drones
— США: faa.gov/uas

⚠️ Правила меняются — проверяй на сайте авиационного ведомства страны перед поездкой"""



async def drone_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главное меню раздела дронов — 4 категории."""
    keyboard = ReplyKeyboardMarkup([
        ["✅ Можно летать", "📋 Нужно разрешение"],
        ["🚫 Запрещено", "⚠️ Общие правила везде"],
        ["◀️ Назад", HOME_BTN],
    ], resize_keyboard=True)
    await update.message.reply_text(
        "🚁 *Дроны в путешествиях*\n\nВыбери категорию:",
        parse_mode="Markdown", reply_markup=keyboard
    )
    return DRONE_MENU


async def drone_category_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает выбор категории дронов — показывает подменю регионов."""
    text = update.message.text
    if text == HOME_BTN:
        return await go_home(update, context)
    if text == "◀️ Назад":
        return await show_folder_knowledge(update, context)

    # Общие правила — отвечаем сразу без регионального подменю
    if text == "⚠️ Общие правила везде":
        keyboard = ReplyKeyboardMarkup([
            ["✅ Можно летать", "📋 Нужно разрешение"],
            ["🚫 Запрещено", "⚠️ Общие правила везде"],
            ["◀️ Назад", HOME_BTN],
        ], resize_keyboard=True)
        await update.message.reply_text(DRONE_RULES_TEXT, parse_mode="Markdown", reply_markup=keyboard)
        return DRONE_MENU

    if text not in DRONE_DATA:
        return await drone_menu_handler(update, context)

    # Сохраняем категорию и показываем регионы
    context.user_data["drone_category"] = text
    keyboard = ReplyKeyboardMarkup(
        [[r] for r in DRONE_REGION_BTNS] + [["◀️ Назад", HOME_BTN]],
        resize_keyboard=True
    )
    await update.message.reply_text(
        f"🚁 *{text}*\n\nВыбери регион:",
        parse_mode="Markdown", reply_markup=keyboard
    )
    return DRONE_SECTION


async def drone_region_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает контент по выбранному региону."""
    text = update.message.text
    if text == HOME_BTN:
        return await go_home(update, context)
    if text == "◀️ Назад":
        return await drone_menu_handler(update, context)

    category = context.user_data.get("drone_category")
    if not category or text not in DRONE_REGION_BTNS:
        return await drone_menu_handler(update, context)

    content = DRONE_DATA.get(category, {}).get(text, "")
    if not content:
        content = f"Данные для региона {text} временно недоступны."

    back_keyboard = ReplyKeyboardMarkup(
        [[r] for r in DRONE_REGION_BTNS] + [["◀️ Назад", HOME_BTN]],
        resize_keyboard=True
    )
    await update.message.reply_text(content, parse_mode="Markdown", reply_markup=back_keyboard)
    return DRONE_SECTION


## ── SEASONS ──────────────────────────────────────────────────────────────────

SEASON_REGION_BTNS = [
    "🌏 Азия",
    "🌍 Ближний Восток и Африка",
    "🌍 Европа",
    "🌎 Америка",
    "🏔 СНГ и Кавказ",
]

SEASON_DATA: dict[str, str] = {
    "🌏 Азия": (
        "🌏 *Азия — лучшее время для поездки*\n\n"
        "🇹🇭 *Таиланд*\n"
        "✅ Ноябрь–февраль: сухо, +28°C, идеально\n"
        "🟡 Март–май: жарко до +38°C, толп меньше\n"
        "❌ Июнь–октябрь: сезон дождей, цены вдвое ниже\n\n"
        "🇻🇳 *Вьетнам*\n"
        "✅ Декабрь–март (юг), май–август (север): сухо, +27°C\n"
        "🟡 Апрель–май (юг): жарко, влажно\n"
        "❌ Октябрь–ноябрь (центр): тайфуны и ливни\n\n"
        "🇮🇩 *Бали*\n"
        "✅ Апрель–октябрь: сухой сезон, +30°C, сёрфинг\n"
        "🟡 Октябрь–ноябрь: начало дождей, всё ещё хорошо\n"
        "❌ Декабрь–март: сезон дождей, но зелень и водопады\n\n"
        "🇰🇭 *Камбоджа*\n"
        "✅ Ноябрь–март: прохладно, +26°C, низкая влажность\n"
        "🟡 Апрель–май: сильная жара до +40°C\n"
        "❌ Июнь–октябрь: муссоны, дороги могут затапливать\n\n"
        "🇯🇵 *Япония*\n"
        "✅ Март–май (сакура), октябрь–ноябрь (осень): +18°C, красота\n"
        "🟡 Июнь–август: жарко и влажно, но много фестивалей\n"
        "❌ Июль–август: тайфуны на юге, влажность 90%\n\n"
        "🇨🇳 *Китай*\n"
        "✅ Апрель–май и сентябрь–октябрь: +20°C, меньше толп\n"
        "🟡 Март и ноябрь: прохладно, хорошая видимость\n"
        "❌ Июль–август: жара, смог, туристический пик\n\n"
        "🇰🇷 *Корея*\n"
        "✅ Апрель–май (цветение), октябрь (осень): +17°C, живописно\n"
        "🟡 Сентябрь: комфортно, меньше туристов\n"
        "❌ Июль–август: муссонные дожди и жара\n\n"
        "🇸🇬 *Сингапур*\n"
        "✅ Февраль–апрель: чуть меньше дождей, +30°C\n"
        "🟡 Май–сентябрь: жарко, короткие ливни\n"
        "❌ Ноябрь–январь: сезон дождей, но ненадолго\n\n"
        "🇲🇾 *Малайзия*\n"
        "✅ Март–октябрь (западное побережье): сухо, +30°C\n"
        "🟡 Октябрь–ноябрь: переходный период\n"
        "❌ Ноябрь–февраль (восточный берег): муссон\n\n"
        "🇵🇭 *Филиппины*\n"
        "✅ Декабрь–май: сухо, +29°C, пляжи идеальны\n"
        "🟡 Апрель–май: жарко до +35°C, но без дождей\n"
        "❌ Июнь–ноябрь: тайфунный сезон, риск для части островов\n\n"
        "🇱🇦 *Лаос*\n"
        "✅ Ноябрь–февраль: прохладно, +25°C, сухой сезон\n"
        "🟡 Март–апрель: жарко, +35°C, дымка от пожаров\n"
        "❌ Май–октябрь: муссоны, часть дорог непроходима\n\n"
        "🇮🇳 *Индия*\n"
        "✅ Октябрь–март: +25°C, сухо, идеально для севера и Гоа\n"
        "🟡 Апрель: ещё терпимо на юге\n"
        "❌ Май–сентябрь: жара до +45°C и муссоны\n\n"
        "🇳🇵 *Непал*\n"
        "✅ Октябрь–ноябрь и март–май: чистое небо, треккинг\n"
        "🟡 Декабрь–февраль: холодно в горах, Катманду комфортен\n"
        "❌ Июнь–сентябрь: муссоны, тропы скользкие\n\n"
        "🇱🇰 *Шри-Ланка*\n"
        "✅ Декабрь–март (запад/юг), июль–сентябрь (восток): солнечно\n"
        "🟡 Апрель–май: межсезонье, всё ещё можно\n"
        "❌ Май–август (запад): юго-западный муссон\n\n"
        "🇲🇻 *Мальдивы*\n"
        "✅ Ноябрь–апрель: сухо, +30°C, прозрачная вода\n"
        "🟡 Май: начало дождей, цены ниже, всё ещё красиво\n"
        "❌ Июнь–октябрь: муссон, волны, скидки до 50%\n\n"
        "⚠️ _Даты приблизительны — уточняй прогноз перед поездкой_"
    ),

    "🌍 Ближний Восток и Африка": (
        "🌍 *Ближний Восток и Африка — лучшее время*\n\n"
        "🇦🇪 *ОАЭ (Дубай)*\n"
        "✅ Октябрь–апрель: +24°C, пляжи, мероприятия\n"
        "🟡 Сентябрь и май: жарко, но терпимо вечером\n"
        "❌ Июнь–август: +45°C, на улицу не выйти\n\n"
        "🇪🇬 *Египет*\n"
        "✅ Октябрь–апрель: +22°C, море и экскурсии\n"
        "🟡 Май и сентябрь: жарко, туристов меньше\n"
        "❌ Июнь–август: +40°C в Каире, сильный зной\n\n"
        "🇲🇦 *Марокко*\n"
        "✅ Март–май и сентябрь–ноябрь: +22°C, идеально\n"
        "🟡 Февраль и декабрь: прохладно, зато дёшево\n"
        "❌ Июль–август: пустыня +50°C, Марракеш невыносим\n\n"
        "🇹🇳 *Тунис*\n"
        "✅ Апрель–июнь и сентябрь–октябрь: +25°C, пляжи\n"
        "🟡 Март и ноябрь: свежо, но море ещё прохладное\n"
        "❌ Июль–август: +40°C, массовый туризм\n\n"
        "🇯🇴 *Иордания*\n"
        "✅ Март–май и сентябрь–ноябрь: +22°C, Петра комфортна\n"
        "🟡 Декабрь–февраль: прохладно, иногда дождь\n"
        "❌ Июнь–август: +40°C в пустыне Вади Рам\n\n"
        "🇮🇱 *Израиль*\n"
        "✅ Март–май и октябрь–ноябрь: +24°C, без толп\n"
        "🟡 Сентябрь: ещё тепло, пляжный сезон\n"
        "❌ Июль–август: пик туризма, жара и очереди\n\n"
        "🇴🇲 *Оман*\n"
        "✅ Октябрь–апрель: +28°C, пустыня и вади доступны\n"
        "🟡 Сентябрь: горячо, но туристов мало\n"
        "❌ Май–август: +45°C, прибрежный хамсин\n\n"
        "🇹🇿 *Занзибар*\n"
        "✅ Июль–октябрь и январь–февраль: сухо, +28°C\n"
        "🟡 Ноябрь–декабрь: короткий дождливый сезон\n"
        "❌ Март–июнь: сезон «больших дождей», море мутное\n\n"
        "🇿🇦 *ЮАР*\n"
        "✅ Май–сентябрь (зима): +20°C, сафари, нет малярийных комаров\n"
        "🟡 Октябрь и апрель: переходный период, хорошая погода\n"
        "❌ Ноябрь–март: жара, дожди в саванне, хуже для сафари\n\n"
        "🇰🇪 *Кения*\n"
        "✅ Июль–октябрь: миграция GNU, сухо, идеально для сафари\n"
        "🟡 Январь–февраль: жарко, но сухо, хорошая видимость\n"
        "❌ Апрель–июнь: сезон дождей, дороги непроходимы\n\n"
        "⚠️ _Даты приблизительны — уточняй прогноз перед поездкой_"
    ),

    "🌍 Европа": (
        "🌍 *Европа — лучшее время для поездки*\n\n"
        "🇪🇸 *Испания*\n"
        "✅ Апрель–июнь и сентябрь–октябрь: +22°C, без толп\n"
        "🟡 Март и ноябрь: прохладно, зато дёшево\n"
        "❌ Июль–август: +38°C, Барселона переполнена\n\n"
        "🇮🇹 *Италия*\n"
        "✅ Апрель–июнь и сентябрь: +22°C, всё открыто\n"
        "🟡 Октябрь–ноябрь: прохладно, Рим и Флоренция тихие\n"
        "❌ Июль–август: жара, Рим и Венеция — туристический ад\n\n"
        "🇫🇷 *Франция*\n"
        "✅ Май–июнь и сентябрь: +20°C, Париж без очередей\n"
        "🟡 Апрель и октябрь: переменная погода, меньше туристов\n"
        "❌ Июль–август: пик туризма, всё дорого и шумно\n\n"
        "🇬🇷 *Греция*\n"
        "✅ Май–июнь и сентябрь–октябрь: +25°C, море тёплое\n"
        "🟡 Апрель: прохладно, но острова просыпаются\n"
        "❌ Июль–август: +35°C, Санторини переполнен\n\n"
        "🇹🇷 *Турция*\n"
        "✅ Апрель–май и сентябрь–октябрь: +25°C, идеально\n"
        "🟡 Март и ноябрь: прохладно, но Стамбул хорош круглый год\n"
        "❌ Июль–август: +40°C на побережье, толпы\n\n"
        "🇭🇷 *Хорватия*\n"
        "✅ Июнь и сентябрь: +27°C, море тёплое, без толп\n"
        "🟡 Май и октябрь: прохладно, Дубровник доступен\n"
        "❌ Июль–август: +35°C, Дубровник — туристический пик\n\n"
        "🇲🇪 *Черногория*\n"
        "✅ Июнь и сентябрь: +28°C, Которский залив сказочен\n"
        "🟡 Май и октябрь: свежо, горы доступны\n"
        "❌ Июль–август: пик, цены в 2 раза выше\n\n"
        "🇵🇹 *Португалия*\n"
        "✅ Май–июнь и сентябрь–октябрь: +24°C, Лиссабон без жары\n"
        "🟡 Март–апрель: цветение, прохладно, но красиво\n"
        "❌ Июль–август: жара +38°C, Алгарве переполнен\n\n"
        "🇨🇿 *Чехия*\n"
        "✅ Май–сентябрь: +22°C, пиво на открытых терассах\n"
        "🟡 Март–апрель: прохладно, Прага без толп\n"
        "❌ Декабрь: холодно, но рождественские ярмарки чудесны\n\n"
        "🇭🇺 *Венгрия*\n"
        "✅ Апрель–октябрь: +22°C, Будапешт расцветает\n"
        "🟡 Март и ноябрь: прохладно, дёшево\n"
        "❌ Январь–февраль: холодно, но термальные купальни работают\n\n"
        "🇦🇹 *Австрия*\n"
        "✅ Декабрь–март (лыжи) и июнь–август (горы): идеально\n"
        "🟡 Апрель–май: межсезонье, Вена хороша всегда\n"
        "❌ Ноябрь: слякоть, закрыты горные курорты\n\n"
        "🇩🇪 *Германия*\n"
        "✅ Май–сентябрь: +22°C, пивные фестивали, пешие маршруты\n"
        "🟡 Апрель и октябрь: прохладно, Берлин хорош всегда\n"
        "❌ Ноябрь–февраль: холодно, серо, но Рождество волшебно\n\n"
        "🇳🇱 *Нидерланды*\n"
        "✅ Апрель–май: тюльпаны, +16°C, Кёкенхоф\n"
        "🟡 Июнь–август: тепло, каналы и велосипеды\n"
        "❌ Октябрь–март: дожди, серое небо, но Амстердам живёт\n\n"
        "🇸🇪🇳🇴🇩🇰 *Скандинавия*\n"
        "✅ Июнь–август: белые ночи, +20°C, фьорды\n"
        "🟡 Сентябрь: начало сезона северного сияния\n"
        "❌ Ноябрь–февраль: полярная ночь, но северное сияние!\n\n"
        "⚠️ _Даты приблизительны — уточняй прогноз перед поездкой_"
    ),

    "🌎 Америка": (
        "🌎 *Америка — лучшее время для поездки*\n\n"
        "🇨🇺 *Куба*\n"
        "✅ Декабрь–апрель: +28°C, сухо, карнавалы\n"
        "🟡 Ноябрь и май: переходный период, тепло\n"
        "❌ Июнь–октябрь: ураганный сезон, сильные дожди\n\n"
        "🇲🇽 *Мексика*\n"
        "✅ Декабрь–апрель: +28°C, Канкун и Юкатан идеальны\n"
        "🟡 Май и ноябрь: жарко на побережье, в центре комфортно\n"
        "❌ Июнь–октябрь: ураганный сезон на Карибах\n\n"
        "🇩🇴 *Доминикана*\n"
        "✅ Декабрь–март: +28°C, пик сезона, море спокойное\n"
        "🟡 Апрель–май: тепло, дешевле, без толп\n"
        "❌ Август–октябрь: ураганный сезон, риски\n\n"
        "🇨🇴 *Колумбия*\n"
        "✅ Декабрь–март и июль–август: сухо, +26°C\n"
        "🟡 Апрель и ноябрь: дожди, но недолго и локально\n"
        "❌ Май–июнь и октябрь: сезон дождей в Андах\n\n"
        "🇵🇪 *Перу*\n"
        "✅ Май–октябрь: сухой сезон, Мачу-Пикчу без туманов\n"
        "🟡 Апрель и ноябрь: переходный, маршруты ещё открыты\n"
        "❌ Ноябрь–март: сезон дождей в джунглях и горах\n\n"
        "🇧🇷 *Бразилия*\n"
        "✅ Июнь–сентябрь: сухо, +28°C, рио-карнавал в феврале\n"
        "🟡 Март–май: влажно, но цены ниже\n"
        "❌ Декабрь–февраль: ливни в Амазонии, наводнения\n\n"
        "🇦🇷 *Аргентина*\n"
        "✅ Октябрь–декабрь и март–апрель: +22°C, Патагония открыта\n"
        "🟡 Май и сентябрь: прохладно, Буэнос-Айрес хорош\n"
        "❌ Июнь–август: зима, Патагония закрыта из-за снега\n\n"
        "🇺🇸 *США*\n"
        "✅ Май–сентябрь (большинство штатов): +25°C, парки открыты\n"
        "🟡 Апрель и октябрь: прохладно, меньше людей в парках\n"
        "❌ Июль–август во Флориде: жара +38°C и ураганы\n\n"
        "⚠️ _Даты приблизительны — уточняй прогноз перед поездкой_"
    ),

    "🏔 СНГ и Кавказ": (
        "🏔 *СНГ и Кавказ — лучшее время для поездки*\n\n"
        "🇬🇪 *Грузия*\n"
        "✅ Май–июнь и сентябрь–октябрь: +24°C, горы и вино\n"
        "🟡 Март–апрель: цветение, прохладно, дёшево\n"
        "❌ Ноябрь–февраль: холодно в горах, Тбилиси уютен\n\n"
        "🇦🇲 *Армения*\n"
        "✅ Май–июнь и сентябрь–октябрь: +22°C, монастыри и горы\n"
        "🟡 Апрель: цветение абрикосов, прохладно\n"
        "❌ Декабрь–февраль: холодно, горные перевалы закрыты\n\n"
        "🇦🇿 *Азербайджан*\n"
        "✅ Апрель–июнь и сентябрь–октябрь: +24°C, Баку и горы\n"
        "🟡 Март и ноябрь: переходный, прохладно\n"
        "❌ Июль–август: +38°C в Баку, летом лучше в горах\n\n"
        "🇰🇬 *Кыргызстан*\n"
        "✅ Июнь–сентябрь: +25°C, Иссык-Куль, джайлоо\n"
        "🟡 Май и октябрь: прохладно, горные треки ещё открыты\n"
        "❌ Ноябрь–апрель: суровая зима, перевалы закрыты\n\n"
        "🇺🇿 *Узбекистан*\n"
        "✅ Март–май и сентябрь–ноябрь: +24°C, Самарканд и Хива\n"
        "🟡 Февраль: прохладно, цветёт миндаль\n"
        "❌ Июнь–август: +42°C в Бухаре, крайне жарко\n\n"
        "⚠️ _Даты приблизительны — уточняй прогноз перед поездкой_"
    ),
}


async def season_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главное меню раздела сезонов — выбор региона."""
    context.user_data["season_depth"] = "menu"
    keyboard = ReplyKeyboardMarkup(
        [[btn] for btn in SEASON_REGION_BTNS] + [["◀️ Назад", HOME_BTN]],
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "🌤 *Сезоны путешествий*\n\nВыбери регион — покажу лучшее время для каждой страны:",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    return SEASON_MENU


async def season_region_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает сезонный контент для выбранного региона."""
    text = update.message.text
    if text == HOME_BTN:
        return await go_home(update, context)
    if text == "◀️ Назад":
        if context.user_data.get("season_depth") == "menu":
            return await show_folder_planning(update, context)
        return await season_menu_handler(update, context)
    content = SEASON_DATA.get(text)
    if not content:
        return await season_menu_handler(update, context)
    context.user_data["season_depth"] = "region"
    back_keyboard = ReplyKeyboardMarkup(
        [["◀️ Назад", HOME_BTN]],
        resize_keyboard=True,
    )
    await update.message.reply_text(content, parse_mode="Markdown", reply_markup=back_keyboard)
    return SEASON_REGION


## ── LOUNGES ──────────────────────────────────────────────────────────────────

LOUNGE_BTNS = [
    "📱 Как попасть в лаундж",
    "🇷🇺 Лаунджи Москвы",
    "🌍 Лучшие лаунджи мира",
    "💡 Лайфхаки",
]

LOUNGE_DATA: dict[str, str] = {

"📱 Как попасть в лаундж": (
    "📱 *Как попасть в лаундж аэропорта*\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "💳 *Банковские карты*\n\n"
    "🏦 *Tinkoff Premium / Black*\n"
    "До 2 бесплатных визитов в месяц по Priority Pass. Карта от 1 990₽/мес или бесплатно при остатке от 3 млн₽.\n\n"
    "🏦 *Альфа-Банк Alfa Premium*\n"
    "Безлимитный Priority Pass + 1 гость бесплатно. Карта от 5 000₽/мес.\n\n"
    "🏦 *СберПремьер / СберПервый*\n"
    "СберПремьер — 4 визита/год, СберПервый — безлимит по Lounge Key. Пакет от 2 499₽/мес.\n\n"
    "🏦 *ВТБ Прайм*\n"
    "Безлимитный Priority Pass. Пакет от 5 000₽/мес.\n\n"
    "🏦 *Райффайзен Premium*\n"
    "2 визита/мес по Priority Pass. Карта от 3 000₽/мес.\n\n"
    "🏦 *Газпромбанк Премиум*\n"
    "До 4 визитов/квартал по DragonPass. От 2 000₽/мес.\n\n"
    "🏦 *Открытие Premium*\n"
    "2 бесплатных визита/мес + скидка 50% на дополнительные.\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🌐 *Priority Pass*\n"
    "Доступ в 1 400+ лаунджей в 148 странах.\n"
    "• Standard: $99/год + $35 за визит\n"
    "• Standard Plus: $329/год — 10 бесплатных визитов\n"
    "• Prestige: $469/год — безлимит\n"
    "Купить: prioritypass.com или через банк.\n\n"
    "🐉 *DragonPass*\n"
    "Аналог Priority Pass, акцент на Азию и Россию.\n"
    "700+ лаунджей. Разовый визит от $25-35.\n"
    "Часто выгоднее PP для путешествий в Китай и СНГ.\n\n"
    "🔑 *Lounge Key*\n"
    "1 000+ лаунджей, входит в ряд банковских пакетов.\n"
    "Разовый визит от $25-30. Сайт: loungekey.com\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "✈️ *Бизнес-класс и статусные карты*\n"
    "Билет бизнес-класса → автоматический доступ в лаундж авиакомпании.\n"
    "Статус Gold/Platinum в программах лояльности (Аэрофлот Бонус, Miles&Smiles и др.) → бесплатный вход.\n\n"
    "💵 *Платный вход*\n"
    "Москва (SVO/DME): 2 000–4 000₽\n"
    "Дубай, Сингапур: $30–50\n"
    "Бангкок, Стамбул: $20–35\n"
    "Обычно включает: еда, напитки, Wi-Fi, часто душ.\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "📱 *Приложения для поиска лаунджей*\n\n"
    "🔍 *LoungeBuddy* (iOS/Android)\n"
    "Лучшее приложение. Показывает все лаунджи, фото, отзывы, часы работы и доступность по твоей карте. Покупка разового доступа прямо в приложении.\n\n"
    "🔍 *Priority Pass App*\n"
    "Официальное приложение PP. Список лаунджей, проверка баланса визитов, цифровая карта PP.\n\n"
    "🔍 *Every Lounge*\n"
    "Агрегатор лаунджей с удобным поиском по аэропорту. Показывает цены на разовый вход и доступность по картам.\n\n"
    "🔍 *Miles on Air*\n"
    "Удобен для отслеживания миль и поиска лаунджей, доступных по программам лояльности авиакомпаний.\n\n"
    "🔍 *Trip.com*\n"
    "Кроме отелей и авиабилетов — продажа разового доступа в лаунджи по всему миру. Часто дешевле официального сайта.\n\n"
    "⚠️ _Условия и цены меняются — уточняй на сайте лаунджа_"
),

"🇷🇺 Лаунджи Москвы": (
    "🇷🇺 *Лаунджи московских аэропортов*\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "✈️ *Шереметьево (SVO)*\n\n"
    "🏛 *Аэрофлот Бизнес Лаундж*\n"
    "📍 Терминал B (внутренние) и D/E/F (международные)\n"
    "🕐 Круглосуточно\n"
    "✅ Горячее питание, алкоголь, душевые кабины, Wi-Fi, детская зона\n"
    "💳 Бизнес-класс Аэрофлота, Gold/Platinum статус\n"
    "⭐ Рейтинг: 4.2/5\n\n"
    "🏛 *No.1 Traveller Lounge*\n"
    "📍 Терминал D, зона вылета\n"
    "🕐 05:00–00:00\n"
    "✅ Шведский стол, открытый бар, Wi-Fi, душ, игровая зона\n"
    "💳 Priority Pass, DragonPass, платно (~3 500₽)\n"
    "⭐ Рейтинг: 4.4/5\n\n"
    "🏛 *Sky Lounge SVO*\n"
    "📍 Терминал F, международный\n"
    "🕐 06:00–23:00\n"
    "✅ Горячие блюда, бар, Wi-Fi, пресса\n"
    "💳 Priority Pass, Lounge Key\n"
    "⭐ Рейтинг: 3.9/5\n\n"
    "🏛 *Meridian Lounge (Turkish Airlines)*\n"
    "📍 Терминал D\n"
    "🕐 По расписанию рейсов TK\n"
    "✅ Турецкая кухня, чай, Wi-Fi\n"
    "💳 Бизнес TK, Miles&Smiles Elite\n"
    "⭐ Рейтинг: 4.0/5\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "✈️ *Домодедово (DME)*\n\n"
    "🏛 *Galaktika Lounge*\n"
    "📍 Центральное здание, 3 этаж, зона вылета\n"
    "🕐 Круглосуточно\n"
    "✅ Горячее, алкоголь, Wi-Fi, душ, спа-кресла\n"
    "💳 Priority Pass, DragonPass, платно (~2 500₽)\n"
    "⭐ Рейтинг: 4.3/5\n\n"
    "🏛 *Dnata Lounge DME*\n"
    "📍 Терминал, зона международных вылетов\n"
    "🕐 24/7\n"
    "✅ Фуршет, бар, Wi-Fi, тихая зона\n"
    "💳 Priority Pass, Lounge Key\n"
    "⭐ Рейтинг: 3.8/5\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "✈️ *Внуково (VKO)*\n\n"
    "🏛 *VIP Lounge Внуково*\n"
    "📍 Терминал A, 2 этаж\n"
    "🕐 24/7\n"
    "✅ Питание, алкоголь, Wi-Fi, душевые, детская\n"
    "💳 Priority Pass, DragonPass, платно (~2 000₽)\n"
    "⭐ Рейтинг: 4.0/5\n\n"
    "🏛 *Utair Lounge*\n"
    "📍 Терминал A, зона вылета\n"
    "🕐 По расписанию\n"
    "✅ Снеки, горячие напитки, Wi-Fi\n"
    "💳 Бизнес Utair, пакеты банков\n"
    "⭐ Рейтинг: 3.7/5\n\n"
    "⚠️ _Время работы и условия могут меняться_"
),

"🌍 Лучшие лаунджи мира": (
    "🌍 *Топ-10 лучших лаунджей мира*\n\n"
    "🥇 *1. 🇸🇬 Сингапур Changi — Singapore Airlines SilverKris*\n"
    "📍 Терминал 3, SIN\n"
    "Признан лучшим в мире 10+ лет подряд. Личные комнаты-сьюты, горячая кухня 4 кухонь мира, бассейн на крыше (T1), спа. Только бизнес/первый класс SQ.\n\n"
    "🥈 *2. 🇦🇪 ОАЭ Дубай — Emirates First Class Lounge*\n"
    "📍 Терминал 3, DXB\n"
    "Бар с живым барменом, спа и массаж, горячий душ, изысканная кухня. Только первый класс Emirates.\n\n"
    "🥉 *3. 🇶🇦 Катар Доха — Qatar Airways Al Mourjan*\n"
    "📍 Хамад Аэропорт, DOH\n"
    "Крупнейший лаундж в мире (10 000 м²). Ресторан с à la carte меню, бары, спа, душевые, тихие зоны, детский уголок.\n\n"
    "4️⃣ *🇭🇰 Гонконг — Cathay Pacific The Pier*\n"
    "📍 HKG, Терминал 1\n"
    "Кабины для сна, ресторан с видом на перрон, полноценный спа-центр с ваннами.\n\n"
    "5️⃣ *🇯🇵 Япония Токио — ANA Suite Lounge*\n"
    "📍 HND / NRT\n"
    "Традиционная японская кухня, тихие зоны, безупречный сервис. Один из лучших лаунджей Азии. Только первый/бизнес ANA.\n\n"
    "6️⃣ *🇹🇭 Таиланд Бангкок — Thai Airways Royal Orchid*\n"
    "📍 Suvarnabhumi BKK\n"
    "Живая тайская кухня, просторные зоны отдыха, спа-процедуры. Только бизнес/первый класс Thai Airways.\n\n"
    "7️⃣ *🇩🇪 Германия Франкфурт — Lufthansa First Class Terminal*\n"
    "📍 FRA — отдельное здание!\n"
    "Собственный терминал только для первого класса LH. Трансфер на Porsche Cayenne, личный повар, спа, комнаты сна.\n\n"
    "8️⃣ *🇬🇧 Великобритания Лондон — British Airways Concorde Room*\n"
    "📍 Heathrow T5, LHR\n"
    "Легендарный лаундж. Личные кабины с дверями, на-борту кухня, спа, тихая зона. Только Club Suite / First class BA.\n\n"
    "9️⃣ *🇫🇮 Финляндия Хельсинки — Finnair Lounge*\n"
    "📍 Helsinki HEL\n"
    "Скандинавский дизайн, сауна, финская кухня, тихая и уютная атмосфера. Один из лучших лаунджей Европы.\n\n"
    "🔟 *🇺🇸 США Нью-Йорк — Delta One Lounge*\n"
    "📍 JFK, Терминал 4\n"
    "Современный лаундж Delta для бизнес-класса. Ресторанная кухня, коктейль-бар, спа, тихие зоны и рабочие места.\n\n"
    "⚠️ _Большинство топ-лаунджей — только бизнес/первый класс._\n"
    "_Уточняй доступность по своей карте в LoungeBuddy или PP App._"
),

"💡 Лайфхаки": (
    "💡 *Лайфхаки про лаунджи аэропортов*\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🆓 *Как попасть бесплатно*\n\n"
    "1️⃣ Оформи карту с Priority Pass — многие банки дают 2-4 бесплатных визита/мес\n"
    "2️⃣ Используй ошибочные тарифы бизнес-класса — они периодически появляются\n"
    "3️⃣ Апгрейд через мили — если нет денег на бизнес-класс\n"
    "4️⃣ Покупай билет ребёнку до 2 лет в бизнес — сам идёшь в лаундж\n"
    "5️⃣ Карты Amex Platinum (международная) — безлимитный Centurion Lounge\n"
    "6️⃣ Статус Gold в Skyteam/Star Alliance/Oneworld — вход во многие лаунджи\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "⏰ *Лучшее время для визита*\n\n"
    "• Приходи за 2-3 часа до рейса — можно нормально поесть и принять душ\n"
    "• Избегай 07:00-09:00 и 17:00-19:00 — пиковые часы, всё занято\n"
    "• В будние дни тише, чем в выходные\n"
    "• Транзитные пассажиры приходят волнами — следи за расписанием\n"
    "• За 30 минут до посадки лаундж пустеет — можно взять свободное место\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🎒 *Что взять с собой обязательно*\n\n"
    "✅ Карту Priority Pass / DragonPass (физическую или в приложении)\n"
    "✅ Банковскую карту с доступом — иногда просят предъявить\n"
    "✅ Посадочный талон — без него не пустят\n"
    "✅ Паспорт — на международных рейсах\n"
    "✅ Зарядник — розеток много, можно зарядить всё\n"
    "✅ Пустой желудок 😄 — еда обычно хорошая и бесплатная\n\n"
    "⚠️ _Условия могут меняться — всегда проверяй актуальный список в PP App_"
),
}


async def lounge_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главное меню раздела лаунджей."""
    context.user_data["lounge_depth"] = "menu"
    keyboard = ReplyKeyboardMarkup(
        [[btn] for btn in LOUNGE_BTNS] + [["◀️ Назад", HOME_BTN]],
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "🛋 *Лаунджи аэропортов*\n\nВыбери раздел:",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    return LOUNGE_MENU


async def lounge_section_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает контент выбранного раздела."""
    text = update.message.text
    if text == HOME_BTN:
        return await go_home(update, context)
    if text == "◀️ Назад":
        if context.user_data.get("lounge_depth") == "menu":
            return await show_folder_knowledge(update, context)
        return await lounge_menu_handler(update, context)
    content = LOUNGE_DATA.get(text)
    if not content:
        return await lounge_menu_handler(update, context)
    context.user_data["lounge_depth"] = "section"
    back_keyboard = ReplyKeyboardMarkup(
        [["◀️ Назад", HOME_BTN]],
        resize_keyboard=True,
    )
    # Split long messages if needed
    if len(content) > 4000:
        parts = []
        current = ""
        for line in content.split("\n"):
            if len(current) + len(line) + 1 > 4000:
                parts.append(current)
                current = line
            else:
                current = current + "\n" + line if current else line
        if current:
            parts.append(current)
        for i, part in enumerate(parts):
            kb = back_keyboard if i == len(parts) - 1 else None
            await update.message.reply_text(part, parse_mode="Markdown", reply_markup=kb)
    else:
        await update.message.reply_text(content, parse_mode="Markdown", reply_markup=back_keyboard)
    return LOUNGE_SECTION


## ── AUTOPOST ─────────────────────────────────────────────────────────────────

CHANNEL_SIGNATURE = f"\n\n🎒 [Как местный]({CHANNEL_URL}) | [Подписаться]({CHANNEL_URL})"


def _strip_hashtags(text: str) -> str:
    """Remove all #hashtag tokens from text (including leading whitespace)."""
    return re.sub(r'\s*#\w+', '', text).strip()


def _download_photo_sync(photo_url: str) -> bytes | None:
    """Blocking download of photo bytes via requests. Run in executor."""
    logger.info(f"download: [1/3] начинаем GET-запрос | URL={photo_url}")
    try:
        resp = requests.get(
            photo_url,
            timeout=15,
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        size = len(resp.content)
        ctype = resp.headers.get("content-type", "?")
        logger.info(
            f"download: [2/3] ответ получен | HTTP {resp.status_code}"
            f" | {size} байт | content-type={ctype} | URL={photo_url}"
        )
        if resp.status_code == 200:
            logger.info(f"download: [3/3] ✅ байты готовы ({size} байт)")
            return resp.content
        logger.warning(
            f"download: [3/3] ❌ статус {resp.status_code} — возвращаем None"
        )
    except requests.exceptions.Timeout:
        logger.error(
            f"download: ❌ Timeout (>15 сек) | URL={photo_url}\n{traceback.format_exc()}"
        )
    except requests.exceptions.ConnectionError as e:
        logger.error(
            f"download: ❌ ConnectionError — {e} | URL={photo_url}\n{traceback.format_exc()}"
        )
    except Exception as e:
        logger.error(
            f"download: ❌ {type(e).__name__}: {e} | URL={photo_url}\n{traceback.format_exc()}"
        )
    return None


async def _send_post(bot, post: dict, label: str, chat_id: int | None = None) -> tuple[bool, str]:
    """Send a post dict {"keyword": ..., "text": ...} with photo + signature.

    Fallback chain:
      1. download photo bytes → send_photo(bytes) + Markdown caption
      2. download photo bytes → send_photo(bytes) + plain caption
      3. send_message + Markdown (with signature)
      4. send_message + plain text
    Returns (success: bool, detail: str) — detail describes what was sent or the error.
    """
    target  = chat_id if chat_id is not None else CHANNEL_ID
    text    = _strip_hashtags(post["text"])
    keyword = post.get("keyword", "travel")
    signed  = text + CHANNEL_SIGNATURE

    # ── Шаг 0: диагностика входных данных ──────────────────────────────────
    photo_url = post.get("photo_url")
    logger.info(
        f"{label}: ── СТАРТ _send_post ──"
        f" keyword='{keyword}' | target_chat={target}"
        f" | text_len={len(text)} | photo_url={'✅ ' + photo_url if photo_url else '❌ нет'}"
    )

    errors: list[str] = []
    photo_bytes: bytes | None = None

    if photo_url:
        # ── Шаг 1: скачиваем байты ─────────────────────────────────────────
        logger.info(f"{label}: [шаг 1] запускаем _download_photo_sync в executor...")
        loop = asyncio.get_event_loop()
        photo_bytes = await loop.run_in_executor(None, _download_photo_sync, photo_url)

        if photo_bytes:
            logger.info(
                f"{label}: [шаг 1] ✅ фото скачано — {len(photo_bytes)} байт"
                f" ({len(photo_bytes) / 1024:.1f} КБ)"
            )

            # ── Шаг 2: send_photo(bytes) + Markdown ────────────────────────
            logger.info(f"{label}: [шаг 2] send_photo(bytes) + Markdown caption...")
            try:
                await bot.send_photo(
                    chat_id=target, photo=photo_bytes,
                    caption=signed, parse_mode="Markdown",
                )
                logger.info(f"{label}: [шаг 2] ✅ УСПЕХ — фото(bytes) + Markdown")
                return True, "✅ фото(bytes) + Markdown caption"
            except Exception as e1:
                err = f"{type(e1).__name__}: {e1}"
                logger.error(
                    f"{label}: [шаг 2] ❌ {err}\n{traceback.format_exc()}"
                )
                errors.append(f"bytes+Markdown: {err}")

            # ── Шаг 3: send_photo(bytes) + plain ───────────────────────────
            logger.info(f"{label}: [шаг 3] send_photo(bytes) + plain caption...")
            try:
                await bot.send_photo(chat_id=target, photo=photo_bytes, caption=text)
                logger.info(f"{label}: [шаг 3] ✅ УСПЕХ — фото(bytes) + plain")
                return True, "✅ фото(bytes) + plain caption"
            except Exception as e2:
                err = f"{type(e2).__name__}: {e2}"
                logger.error(
                    f"{label}: [шаг 3] ❌ {err}\n{traceback.format_exc()}"
                )
                errors.append(f"bytes+plain: {err}")
        else:
            err = f"download вернул None | URL={photo_url}"
            logger.error(f"{label}: [шаг 1] ❌ {err}")
            errors.append(f"download: {err}")
    else:
        logger.info(f"{label}: photo_url не задан — пропускаем скачивание")

    # ── Шаг 4: send_message + Markdown ─────────────────────────────────────
    logger.info(f"{label}: [шаг 4] send_message + Markdown (только текст)...")
    try:
        await bot.send_message(
            chat_id=target, text=signed, parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        logger.info(f"{label}: [шаг 4] ✅ УСПЕХ — текст + Markdown")
        return True, "✅ текст + Markdown (фото не отправилось)"
    except Exception as e3:
        err = f"{type(e3).__name__}: {e3}"
        logger.error(
            f"{label}: [шаг 4] ❌ {err}\n{traceback.format_exc()}"
        )
        errors.append(f"текст+Markdown: {err}")

    # ── Шаг 5: send_message plain ───────────────────────────────────────────
    logger.info(f"{label}: [шаг 5] send_message plain text...")
    try:
        await bot.send_message(
            chat_id=target, text=text, disable_web_page_preview=True,
        )
        logger.info(f"{label}: [шаг 5] ✅ УСПЕХ — plain text")
        return True, "✅ plain текст (все остальные попытки провалились)"
    except Exception as e4:
        err = f"{type(e4).__name__}: {e4}"
        logger.error(
            f"{label}: [шаг 5] ❌ {err}\n{traceback.format_exc()}"
        )
        errors.append(f"plain: {err}")

    detail = "❌ все попытки провалились:\n" + "\n".join(f"  • {e}" for e in errors)
    logger.error(f"{label}: ── ФИНАЛ: {detail}")
    return False, detail


async def scheduler(bot) -> None:
    """Infinite loop: sends next post at 10:00 and 16:00 MSK."""
    global _post_index
    sent_keys: set[str] = set()
    tick = 0
    logger.info(f"Планировщик запущен (10:00 и 16:00 МСК) | CHANNEL_ID={CHANNEL_ID} | постов={len(CHANNEL_POSTS)}")
    while True:
        try:
            now  = datetime.now(MOSCOW_TZ)
            hhmm = now.strftime("%H:%M")
            day  = now.strftime("%Y-%m-%d")
            key  = f"{day}-{hhmm}"

            # Heartbeat every ~2 hours to confirm task is alive
            if tick % 240 == 0:
                logger.info(f"Scheduler heartbeat: {now.strftime('%Y-%m-%d %H:%M')} МСК | индекс={_post_index}")
            tick += 1

            if hhmm in ("10:00", "16:00") and key not in sent_keys:
                sent_keys.add(key)
                idx  = _post_index % len(CHANNEL_POSTS)
                post = CHANNEL_POSTS[idx]
                _post_index += 1
                _save_post_index(_post_index)
                logger.info(
                    f"Автопост #{_post_index} (пост {idx+1}/{len(CHANNEL_POSTS)})"
                    f" keyword='{post.get('keyword')}': отправка в {CHANNEL_ID}"
                )
                ok, detail = await _send_post(bot, post, f"Автопост #{_post_index}")
                logger.info(f"Автопост #{_post_index}: результат — {detail}")

        except asyncio.CancelledError:
            logger.info("Планировщик остановлен (CancelledError)")
            raise
        except Exception as e:
            logger.error(f"Необработанная ошибка в планировщике: {type(e).__name__}: {e}")

        await asyncio.sleep(30)


def _scheduler_done_cb(task: asyncio.Task) -> None:
    """Called when scheduler task ends — logs any crash so it's not silent."""
    if task.cancelled():
        logger.info("Задача планировщика отменена (штатно при выключении бота)")
    elif task.exception() is not None:
        logger.error(f"Задача планировщика завершилась с ошибкой: {task.exception()!r}")
    else:
        logger.warning("Задача планировщика завершилась без ошибки (неожиданно)")


# ═══════════════════════════════════════════════════════════════
#  🤝 ПАРТНЁРЫ
# ═══════════════════════════════════════════════════════════════

_PARTNERS_KB = ReplyKeyboardMarkup(
    [
        ["🇬🇧 Школа английского Skyeng"],
        ["◀️ Назад", HOME_BTN],
    ],
    resize_keyboard=True,
)

_SKYENG_TEXT = (
    "🇬🇧 *Skyeng — онлайн школа английского языка*\n\n"
    "Skyeng — одна из крупнейших онлайн школ английского языка в России. "
    "Основана в 2012 году, более 2 миллионов учеников прошли обучение.\n\n"
    "*Как устроено:*\n"
    "• Занятия один на один с преподавателем по видеосвязи\n"
    "• Собственная платформа с интерактивными упражнениями\n"
    "• Более 1000 преподавателей — носители языка и сертифицированные педагоги\n"
    "• Уроки в удобное время — утром, днём или вечером\n"
    "• Уровни от нуля до продвинутого\n\n"
    "*Почему пригодится путешественнику:*\n"
    "✈️ Объяснишься в аэропорту и отеле\n"
    "🍽 Сделаешь заказ в ресторане без переводчика\n"
    "🗺 Поймёшь указатели и навигацию\n"
    "🤝 Познакомишься с местными жителями\n"
    "💼 Откроет двери в международные направления\n\n"
    "🎁 Специально для подписчиков «Как местный»"
)


async def show_partners_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню раздела Партнёры."""
    await update.message.reply_text(
        "🤝 *Партнёры*\n\nВыбери партнёра:",
        parse_mode="Markdown",
        reply_markup=_PARTNERS_KB,
    )
    return PARTNERS_MENU


async def partners_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатия в меню Партнёры."""
    text = update.message.text
    if text == HOME_BTN:
        return await go_home(update, context)
    if text == "◀️ Назад":
        return await go_home(update, context)
    if text == "🇬🇧 Школа английского Skyeng":
        inline_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "🎁 Получить бонус",
                url="https://skyeng.ru/referral/?source_type=referral"
                    "&utm_source=referral&inviterHash=4d5449314d54597a4e44553d",
            )
        ]])
        await update.message.reply_text(
            _SKYENG_TEXT,
            parse_mode="Markdown",
            reply_markup=inline_kb,
            disable_web_page_preview=True,
        )
        back_kb = ReplyKeyboardMarkup([["◀️ Назад", HOME_BTN]], resize_keyboard=True)
        await update.message.reply_text("Навигация:", reply_markup=back_kb)
        return PARTNERS_MENU
    # Неизвестная кнопка — вернуть меню
    return await show_partners_menu(update, context)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Статистика пользователей — только для администратора."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Нет доступа.")
        return

    if _db_backend == "none":
        await update.message.reply_text("⛔ Хранилище недоступно.")
        return

    try:
        s = _get_stats()
    except Exception as e:
        logger.error("stats_command: %s: %s", type(e).__name__, e)
        await update.message.reply_text("⛔ Ошибка при получении статистики.")
        return

    text = (
        "📊 *Статистика «Как местный»*\n\n"
        f"👥 Всего пользователей: *{s['total']}*\n"
        f"✅ Активных сегодня: *{s['active_today']}*\n"
        f"🆕 За 7 дней: *{s['new_7']}*\n"
        f"📆 За 30 дней: *{s['new_30']}*\n\n"
        f"📌 Статистика ведётся с {s['since']}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def testpost_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Diagnostic: check TEST channel access, admin rights, then send next post there."""
    global _post_index

    diag: list[str] = ["🔍 *Диагностика автопостинга*\n"]

    # 1. posts.py import check
    diag.append(f"📦 `posts.py`: {len(CHANNEL_POSTS)} постов загружено")
    diag.append(f"📍 Индекс: {_post_index} → следующий пост #{_post_index % len(CHANNEL_POSTS) + 1}")

    # 2. Bot identity
    try:
        me = await context.bot.get_me()
        diag.append(f"🤖 Бот: @{me.username} (id=`{me.id}`)")
    except Exception as e:
        diag.append(f"🤖 get\\_me() ошибка: `{type(e).__name__}: {e}`")

    # 3. Test channel access
    diag.append(f"🧪 TEST\\_CHANNEL\\_ID: `{TEST_CHANNEL_ID}`")
    try:
        chat = await context.bot.get_chat(TEST_CHANNEL_ID)
        title = chat.title or "—"
        uname = f"@{chat.username}" if chat.username else "(нет username)"
        diag.append(f"🧪 Тестовый канал: *{title}* {uname}")
    except Exception as e:
        diag.append(f"🧪 get\\_chat() ошибка: `{type(e).__name__}: {e}`")
        diag.append("⛔ Тестовый канал недоступен")
        await update.message.reply_text("\n".join(diag), parse_mode="Markdown")
        return

    # 4. Admin rights in test channel
    try:
        me = await context.bot.get_me()
        member = await context.bot.get_chat_member(TEST_CHANNEL_ID, me.id)
        status = member.status
        can_post = getattr(member, "can_post_messages", None)
        diag.append(f"🔑 Статус в тестовом канале: `{status}`")
        if can_post is not None:
            diag.append(f"🔑 can\\_post\\_messages: `{can_post}`")
        if status not in ("administrator", "creator"):
            diag.append("⚠️ Бот не является администратором тестового канала!")
    except Exception as e:
        diag.append(f"🔑 get\\_chat\\_member() ошибка: `{type(e).__name__}: {e}`")

    # 5. Post preview + photo_url info
    idx       = _post_index % len(CHANNEL_POSTS)
    post      = CHANNEL_POSTS[idx]
    post_text = post["text"]
    keyword   = post.get("keyword", "—")
    photo_url = post.get("photo_url", "")
    preview   = post_text[:120].replace("*", "").replace("_", "").replace("`", "")
    diag.append(f"\n📝 Пост #{idx + 1} (первые 120 симв.):\n{preview}…")
    diag.append(f"🔑 Keyword: `{keyword}`")
    if photo_url:
        diag.append(f"🖼 Фото: ✅ задан\n`{photo_url}`")
    else:
        diag.append("🖼 Фото: ❌ photo\\_url не задан — пост отправится без фото")

    await update.message.reply_text("\n".join(diag), parse_mode="Markdown")

    # 6. Attempt send → TEST_CHANNEL_ID only
    _post_index += 1
    _save_post_index(_post_index)
    label = f"/testpost #{_post_index}"
    logger.info(f"{label}: отправка поста {idx+1} (keyword='{keyword}') в тестовый канал {TEST_CHANNEL_ID}")

    ok, detail = await _send_post(context.bot, post, label, chat_id=TEST_CHANNEL_ID)

    result_lines = [
        f"{'✅' if ok else '❌'} Пост #{_post_index} — {detail}",
    ]
    if photo_url:
        result_lines.append(f"🖼 URL фото: `{photo_url}`")
    if not ok:
        result_lines.append("💡 Убедись что бот — администратор канала с правом публикации")
    await update.message.reply_text("\n".join(result_lines), parse_mode="Markdown")


async def post_init(app: Application) -> None:
    """Called by PTB after initialize() — set bot commands, verify channel, launch scheduler."""
    # Register menu commands (visible via the '/' button next to the clip icon)
    await app.bot.set_my_commands([
        BotCommand("start", "Главное меню"),
    ])
    logger.info("Команды бота зарегистрированы ✓")

    # Init PostgreSQL
    await init_db(app)

    # Startup channel check
    try:
        me   = await app.bot.get_me()
        chat = await app.bot.get_chat(CHANNEL_ID)
        logger.info(f"Бот: @{me.username} | Канал: {chat.title} (id={CHANNEL_ID}) — доступен ✓")
    except Exception as e:
        logger.error(f"Стартовая проверка канала: {type(e).__name__}: {e}")

    # Автопостинг временно отключён
    # task = asyncio.create_task(scheduler(app.bot))
    # task.add_done_callback(_scheduler_done_cb)
    # logger.info("Задача планировщика создана и запущена")
    logger.info("Автопостинг отключён — /testpost доступен вручную")


def main():
    app = Application.builder().token(TOKEN).post_init(post_init).build()

    home = MessageHandler(filters.Regex(f"^{HOME_BTN}$"), go_home)

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("menu", menu_command),
            CommandHandler("help", help_command),
        ],
        states={
            MAIN_MENU: [
                MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data),
                MessageHandler(filters.TEXT & ~filters.COMMAND, main_menu_handler),
            ],
            ANSWERING: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_answer),
            ],
            HELP_MENU: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, help_menu_handler),
            ],
            HELP_TOPIC: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, help_topic_handler),
            ],
            TRANSLATING: [
                home,
                MessageHandler(filters.Regex(r"^🔤 Перевести ещё$"), start_translator),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_translation),
            ],
            VISA_MENU: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, visa_menu_handler),
            ],
            VISA_CATEGORY: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, visa_category_handler),
            ],
            MOVIES_MENU: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, movies_menu_handler),
            ],
            MOVIES_REGION: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, movies_region_handler),
            ],
            MOVIES_LIST: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, movies_list_handler),
            ],
            INCOMPATIBLE_MENU: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, incompatible_menu_handler),
            ],
            INCOMPATIBLE_TOPIC: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, incompatible_topic_handler),
            ],
            DRONE_MENU: [
                home,
                MessageHandler(filters.Regex("^(✅ Можно летать|📋 Нужно разрешение|🚫 Запрещено|⚠️ Общие правила везде|◀️ Назад)$"), drone_category_handler),
            ],
            DRONE_SECTION: [
                home,
                MessageHandler(filters.Regex("^(🌍 Европа|🌏 Азия и СНГ|🌎 Америка|🌍 Африка|🌊 Океания|◀️ Назад)$"), drone_region_handler),
            ],
            SEASON_MENU: [
                home,
                MessageHandler(filters.Regex("^(🌏 Азия|🌍 Ближний Восток и Африка|🌍 Европа|🌎 Америка|🏔 СНГ и Кавказ|◀️ Назад)$"), season_region_handler),
            ],
            SEASON_REGION: [
                home,
                MessageHandler(filters.Regex("^(◀️ Назад)$"), season_region_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, season_region_handler),
            ],
            LOUNGE_MENU: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, lounge_section_handler),
            ],
            LOUNGE_SECTION: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, lounge_section_handler),
            ],
            SUPPORT_MENU: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, support_menu_handler),
            ],
            SUPPORT_TYPING: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, support_typing_handler),
            ],
            CRUISE_MENU: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, cruise_section_handler),
            ],
            CRUISE_SECTION: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, cruise_section_handler),
            ],
            WONDERS_MENU: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, wonders_main_handler),
            ],
            WONDERS_SEVEN_MENU: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, wonders_seven_handler),
            ],
            WONDERS_SECTION: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, wonders_section_back_handler),
            ],
            UNESCO_MENU: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, unesco_region_handler),
            ],
            UNESCO_REGION: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, unesco_region_handler),
            ],
            PARTNERS_MENU: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, partners_menu_handler),
            ],
            TOURS_MENU: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, tours_menu_handler),
            ],
            TOURS_TYPING: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, tours_typing_handler),
            ],
            DESTINY_TYPING: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, destiny_typing_handler),
            ],
            QUIZ_ACTIVE: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_handler),
            ],
            GAMES_MENU: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, games_menu_handler),
            ],
            GUESS_ACTIVE: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, guess_handler),
            ],
            PAIR_ACTIVE: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, pair_handler),
            ],
            COUNTRY_OF_DAY: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, country_of_day_handler),
            ],
            SHOP_MENU: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, shop_menu_handler),
            ],
            SHOP_TYPING: [
                home,
                MessageHandler(filters.TEXT & ~filters.COMMAND, shop_typing_handler),
            ],
        },
        fallbacks=[
            home,
            CommandHandler("start", start),
            CommandHandler("menu", menu_command),
            CommandHandler("cancel", cancel),
        ],
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("testpost", testpost_command))
    app.add_handler(CommandHandler("stats",    stats_command))
    app.add_handler(CommandHandler("menu",     menu_command))

    logger.info("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
