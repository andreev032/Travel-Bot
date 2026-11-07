"""Versioned onboarding schedule and durable Telegram delivery."""
import asyncio
import logging
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

MOSCOW = ZoneInfo("Europe/Moscow")
FRESHNESS = timedelta(minutes=5)
POLL_SECONDS = 5
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Step:
    key: str
    day: int
    minute: int
    button: str
    text: str


STEPS = (
    Step("destiny", 1, 1, "🌍 Страна по судьбе", (
        "🌍 Не знаешь, куда отправиться в путешествие?\n"
        "\n"
        "Попробуй «Страна по судьбе» — узнай, какую страну тебе предложит бот!"
    )),
    Step("planning", 1, 11, "🧭 Планирование", (
        "🧭 Планируешь путешествие?\n"
        "\n"
        "В разделе «Планирование» ты можешь:\n"
        "\n"
        "🌍 Подобрать страну\n"
        "🎲 Испытать судьбу\n"
        "☀️ Узнать, куда лучше ехать по сезону\n"
        "✈️ Найти идеи для поездки\n"
        "📖 Получить советы новичку\n"
        "💡 Узнать полезное о путешествиях"
    )),
    Step("start_tip", 1, 60, "🏠 Главное меню", (
        "🔄 Бот постоянно развивается!\n"
        "\n"
        "Здесь регулярно появляются новые функции и полезные возможности для путешествий.\n"
        "\n"
        "Если бот завис или перестал отвечать, нажми «Меню» внизу экрана, затем выбери «Start» — бот снова откроет главное меню.\n"
        "\n"
        "Не забывай иногда заглядывать сюда — впереди ещё много интересного! 🌍"
    )),
    Step("checklist", 2, 0, "🧳 Чек-лист сборов", (
        "🧳 Собираешься в путешествие?\n"
        "\n"
        "В боте есть удобный чек-лист сборов, который поможет ничего не забыть и взять с собой всё самое необходимое!\n"
        "\n"
        "Отмечай собранные вещи и спокойно готовься к поездке. ✈️"
    )),
    Step("tools", 2, 10, "🛠 Инструменты", (
        "🛠 Ещё больше полезного для путешествий!\n"
        "\n"
        "В разделе «Инструменты» ты найдёшь:\n"
        "\n"
        "🗣 Переводчик\n"
        "💱 Конвертер валют\n"
        "🕒 Разницу во времени\n"
        "🗺 Карту мира\n"
        "🧳 Чек-лист сборов\n"
        "\n"
        "Всё, что может пригодиться в путешествии, всегда под рукой! 🌍"
    )),
    Step("countries", 3, 0, "🌎 Мои страны", (
        "🌎 А сколько стран ты уже посетил?\n"
        "\n"
        "Отмечай страны, в которых побывал, собирай свою карту путешествий и следи за личной статистикой!\n"
        "\n"
        "Интересно, сколько стран у тебя уже в коллекции? ✈️"
    )),
    Step("mytrips", 3, 10, "🌍 Мои путешествия", (
        "🌍 Сохраняй историю своих путешествий!\n"
        "\n"
        "В разделе «Мои путешествия» ты можешь:\n"
        "\n"
        "🗺 Отмечать посещённые страны и регионы России\n"
        "🏛 Собирать достопримечательности\n"
        "🏆 Участвовать в рейтинге путешественников\n"
        "📊 Следить за статистикой и считать расстояния\n"
        "📖 Вести дневник путешествий\n"
        "🌊 Отмечать посещённые океаны, моря и реки\n"
        "\n"
        "А ещё здесь есть путеводители, авторские туры и помощь с визами! ✈️"
    )),
    Step("country_day", 4, 0, "🎮 Страна дня", (
        "🎮 А ты готов открыть новую страну?\n"
        "\n"
        "Попробуй «Страну дня»! 🌍\n"
        "\n"
        "Каждый день открывай новую страну, узнавай интересные факты и пополняй свою коллекцию флагов! 🚩"
    )),
    Step("games", 4, 10, "🎮 Игры", (
        "🎮 Хочешь скоротать время в дороге?\n"
        "\n"
        "Загляни в раздел «Игры»! Здесь тебя ждут:\n"
        "\n"
        "🧠 Викторина — проверь свои знания о мире\n"
        "🌍 Угадай, где я — попробуй узнать место\n"
        "🧩 Найди пару — тренируй память\n"
        "🚩 Страна дня — открывай страны и собирай флаги\n"
        "\n"
        "Играй, узнавай новое и открывай мир! 🌎"
    )),
    Step("partners", 5, 0, "🤝 Партнёры", (
        "✈️ Планируешь следующее путешествие?\n"
        "\n"
        "В разделе «Партнёры» собраны полезные сервисы для организации поездки:\n"
        "\n"
        "✈️ Авиабилеты\n"
        "🏨 Отели\n"
        "🛡 Страховки\n"
        "📱 eSIM для интернета за границей\n"
        "🗺 Экскурсии\n"
        "🚢 Круизы\n"
        "\n"
        "Всё необходимое для путешествия в одном месте! 🌍"
    )),
    Step("feedback", 7, 0, "💬 Оставить отзыв", (
        "💬 Ну как тебе бот?\n"
        "\n"
        "Уже успел попробовать его возможности? Расскажи, что понравилось, а что можно улучшить!\n"
        "\n"
        "Твой отзыв поможет сделать бот ещё полезнее для путешественников. 🌍\n"
        "\n"
        "А ещё подписывайся на мои соцсети — там делюсь путешествиями, полезными советами и интересными местами! ✈️"
    )),
    Step("donate", 10, 0, "❤️ Поддержать проект", (
        "❤️ Спасибо, что путешествуешь с нами!\n"
        "\n"
        "«Как местный» — это больше, чем просто бот. Я создаю экосистему, которая помогает путешествовать, открывать новые места и находить единомышленников по всему миру. 🌍\n"
        "\n"
        "Впереди — новые путеводители, авторские туры, полезные сервисы, собственное приложение и даже социальная сеть для путешественников!\n"
        "\n"
        "Бот остаётся полностью бесплатным, и я постоянно работаю над его улучшением.\n"
        "\n"
        "Если проект оказался тебе полезен, ты можешь поддержать его добровольным пожертвованием.\n"
        "\n"
        "Даже небольшая поддержка помогает воплощать большие идеи! ❤️"
    )),
)
STEP_BY_KEY = {step.key: step for step in STEPS}

# Legacy sends use nonnegative days. Reserve stable IDs for the 12 new steps.
# The pending row stores its due time in UTC, and the claimed row its claim time.
PENDING_STEPS = {-38501 - index: step for index, step in enumerate(STEPS)}
CLAIM_OFFSET = 100


def schedule(started_at):
    if started_at.utcoffset() is None:
        raise ValueError("Onboarding start must have a timezone")
    started_at = started_at.astimezone(timezone.utc)
    local_date = started_at.astimezone(MOSCOW).date()
    for step in STEPS:
        if step.day == 1:
            due_at = started_at + timedelta(minutes=step.minute)
        else:
            day = local_date + timedelta(days=step.day - 1)
            due_at = datetime.combine(day, time(11, step.minute), MOSCOW)
        yield step.key, due_at.astimezone(timezone.utc)


def keyboard(step):
    rows = [[InlineKeyboardButton(step.button, callback_data=f"intro:{step.key}")]]
    if step.key == "feedback":
        rows.append([
            InlineKeyboardButton("📲 Telegram-канал", url="https://t.me/like_a_local"),
            InlineKeyboardButton("📸 Instagram", url="https://instagram.com/andreev032"),
        ])
    rows.append([
        InlineKeyboardButton("⬅️ Назад", callback_data=f"intro:back:{step.key}"),
        InlineKeyboardButton("🏠 Главное меню", callback_data="intro:home"),
    ])
    return InlineKeyboardMarkup(rows)


def enroll(cur, user_id):
    """Queue the steps in the transaction that inserts the user."""
    cur.execute(
        "SELECT now() WHERE NOT EXISTS (SELECT 1 FROM onboarding_messages WHERE user_id = %s)",
        (user_id,),
    )
    row = cur.fetchone()
    if row:
        due_by_key = dict(schedule(row[0]))
        cur.executemany(
            """
            INSERT INTO onboarding_messages (user_id, day, sent_at)
            VALUES (%s, %s, %s AT TIME ZONE 'UTC')
            """,
            [(user_id, day, due_by_key[step.key]) for day, step in PENDING_STEPS.items()],
        )


def expire(connect):
    with closing(connect()) as conn, conn, conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM onboarding_messages
            WHERE day BETWEEN %s AND %s
              AND sent_at <= (now() AT TIME ZONE 'UTC') - %s
            """,
            (min(PENDING_STEPS), max(PENDING_STEPS), FRESHNESS),
        )


def claim(connect):
    """Commit a permanent claim before calling Telegram, even across restarts."""
    with closing(connect()) as conn, conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH next_message AS (
                SELECT user_id, day
                FROM onboarding_messages
                WHERE day BETWEEN %s AND %s
                  AND sent_at <= now() AT TIME ZONE 'UTC'
                  AND sent_at > (now() AT TIME ZONE 'UTC') - %s
                ORDER BY sent_at, user_id, day
                LIMIT 1 FOR UPDATE SKIP LOCKED
            )
            UPDATE onboarding_messages AS message
            SET day = message.day - %s, sent_at = now() AT TIME ZONE 'UTC'
            FROM next_message
            WHERE (message.user_id, message.day) = (next_message.user_id, next_message.day)
            RETURNING next_message.user_id, next_message.day
            """,
            (min(PENDING_STEPS), max(PENDING_STEPS), FRESHNESS, CLAIM_OFFSET),
        )
        row = cur.fetchone()
        return (row[0], PENDING_STEPS[row[1]]) if row else None


async def deliver(bot, delivery):
    user_id, step = delivery
    try:
        await asyncio.wait_for(
            bot.send_message(chat_id=user_id, text=step.text, reply_markup=keyboard(step)),
            timeout=30,
        )
    except Exception as error:
        # Telegram may have accepted the message even when the request failed.
        logger.warning("Onboarding step %s was not confirmed: %s", step.key, type(error).__name__)


async def run(bot, connect):
    while True:
        try:
            await asyncio.to_thread(expire, connect)
            for _ in range(50):
                delivery = await asyncio.to_thread(claim, connect)
                if delivery is None:
                    break
                await deliver(bot, delivery)
        except Exception as error:
            logger.error("Onboarding scheduler: %s", type(error).__name__)
        await asyncio.sleep(POLL_SECONDS)
