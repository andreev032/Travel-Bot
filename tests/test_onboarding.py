import asyncio
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import psycopg2
from telegram.error import TimedOut

os.environ.setdefault("BOT_TOKEN", "123:test-only")
import bot
import onboarding


class ScheduleTests(unittest.TestCase):
    def test_all_steps_across_moscow_new_year(self):
        started = datetime(2026, 12, 31, 20, 58, tzinfo=timezone.utc)
        actual = [(key, due.astimezone(onboarding.MOSCOW).strftime("%m-%d %H:%M"))
                  for key, due in onboarding.schedule(started)]
        self.assertEqual(actual, [
            ("destiny", "12-31 23:59"), ("planning", "01-01 00:09"),
            ("start_tip", "01-01 00:58"), ("checklist", "01-01 11:00"),
            ("tools", "01-01 11:10"), ("countries", "01-02 11:00"),
            ("mytrips", "01-02 11:10"), ("country_day", "01-03 11:00"),
            ("games", "01-03 11:10"), ("partners", "01-04 11:00"),
            ("feedback", "01-06 11:00"), ("donate", "01-09 11:00"),
        ])

    def test_keyboard_layout_and_social_links(self):
        for step in onboarding.STEPS:
            rows = onboarding.keyboard(step).inline_keyboard
            self.assertEqual(len(rows), 3 if step.key == "feedback" else 2)
            self.assertEqual([b.callback_data for b in rows[0]], [f"intro:{step.key}"])
            self.assertEqual([b.text for b in rows[-1]], ["⬅️ Назад", "🏠 Главное меню"])
        links = onboarding.keyboard(onboarding.STEP_BY_KEY["feedback"]).inline_keyboard[1]
        self.assertEqual([button.url for button in links], [
            "https://t.me/like_a_local", "https://instagram.com/andreev032",
        ])


def update_for_callback(data):
    message = SimpleNamespace(reply_text=AsyncMock(return_value=SimpleNamespace(message_id=123)))
    user = SimpleNamespace(id=123, username="test", first_name="Test", is_bot=False)
    return SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=user.id, type="private"),
        effective_user=user,
        callback_query=SimpleNamespace(data=data, from_user=user, answer=AsyncMock()),
    )


class NavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_callbacks_use_existing_conversation_and_keyboard(self):
        states = {
            "destiny": bot.DESTINY_TYPING, "planning": bot.MAIN_MENU,
            "start_tip": bot.MAIN_MENU, "checklist": bot.MAIN_MENU,
            "tools": bot.MAIN_MENU, "countries": bot.MAIN_MENU,
            "mytrips": bot.MAIN_MENU, "country_day": bot.COUNTRY_OF_DAY,
            "games": bot.GAMES_MENU, "partners": bot.PARTNERS_MENU,
            "feedback": bot.SUPPORT_MENU, "donate": bot.MAIN_MENU,
        }
        with patch.object(bot, "get_user_countries", return_value=["Италия"]), \
             patch.object(bot, "get_or_create_referral_code", return_value="REF_TEST"), \
             patch.object(bot, "_add_flag_to_collection", return_value=True), \
             patch.object(bot, "_get_flag_count", return_value=1):
            for key, state in states.items():
                for action in (key, f"back:{key}", "home"):
                    with self.subTest(action=action):
                        update = update_for_callback(f"intro:{action}")
                        context = SimpleNamespace(user_data={"quiz_score": 3}, chat_data={})
                        expected = state if action == key else bot.MAIN_MENU
                        if action == "back:country_day":
                            expected = bot.GAMES_MENU
                        self.assertEqual(await bot.onboarding_callback(update, context), expected)
                        update.callback_query.answer.assert_awaited_once()
                        keyboard = update.effective_message.reply_text.call_args.kwargs["reply_markup"]
                        labels = [b.text for row in keyboard.keyboard for b in row]
                        if action == "feedback":
                            self.assertTrue(set(bot._SUPPORT_TYPES) <= set(labels))
                        if action in ("countries", "checklist"):
                            url = keyboard.keyboard[0][0].web_app.url
                            if action == "countries":
                                self.assertTrue(url.startswith(bot.WEBAPP_URL + "?d="))
                            else:
                                self.assertEqual(url, bot.CHECKLIST_URL)

    async def test_start_preserves_guide_deep_link(self):
        update = update_for_callback("intro:home")
        update.message = update.effective_message
        context = SimpleNamespace(
            user_data={"quiz_index": 4, "quiz_score": 3, "support_type": "idea"},
            chat_data={}, args=[], bot=SimpleNamespace(unpin_all_chat_messages=AsyncMock(),
                                                      delete_message=AsyncMock()),
        )
        with patch.object(bot, "record_user", new_callable=AsyncMock) as record, \
             patch.object(bot, "ONBOARDING_ENABLED", True):
            self.assertEqual(await bot.start(update, context), bot.MAIN_MENU)
            context.args = ["guide"]
            self.assertEqual(await bot.start(update, context), bot.MAIN_MENU)
            self.assertTrue(record.await_args.kwargs["enroll_onboarding"])
        self.assertEqual(context.user_data, {})
        keyboard = update.message.reply_text.call_args.kwargs["reply_markup"]
        self.assertTrue(keyboard.inline_keyboard[0][0].web_app.url.endswith("/how-to-travel-cheap/"))

    async def test_legacy_callbacks_still_answer(self):
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(bot, "get_user_countries", return_value=[]):
            for name in ("onb_mytrips", "onb_feedback", "onb_premium"):
                update = update_for_callback(name)
                await getattr(bot, name + "_cb")(update, context)
                update.callback_query.answer.assert_awaited_once()
        self.assertEqual(context.bot.send_message.await_count, 3)

class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_does_not_repeat_send(self):
        telegram = SimpleNamespace(send_message=AsyncMock(side_effect=TimedOut()))
        step = onboarding.STEP_BY_KEY["destiny"]
        await onboarding.deliver(telegram, (123, step))
        telegram.send_message.assert_awaited_once()
        self.assertEqual(telegram.send_message.call_args.kwargs["text"], step.text)


@unittest.skipUnless(os.getenv("ONBOARDING_LOCAL_TEST_DSN"), "needs disposable local PostgreSQL")
class PostgresTests(unittest.TestCase):
    def setUp(self):
        self.dsn = os.environ["ONBOARDING_LOCAL_TEST_DSN"]
        self.schema = "intro_test_" + uuid.uuid4().hex
        with closing(self.connect()) as conn, conn, conn.cursor() as cur:
            cur.execute(f"CREATE SCHEMA {self.schema}")
            cur.execute("""
                CREATE TABLE users (
                    user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT,
                    first_seen TIMESTAMP DEFAULT NOW(), last_seen TIMESTAMP DEFAULT NOW()
                );
                CREATE TABLE onboarding_messages (
                    user_id BIGINT, day INT, sent_at TIMESTAMP DEFAULT NOW(), PRIMARY KEY (user_id, day)
                );
                INSERT INTO users (user_id, first_name) VALUES (1, 'Existing');
                INSERT INTO onboarding_messages VALUES (1, 14), (2, 3);
            """)
        self.addCleanup(self.drop_schema)
    def connect(self):
        return psycopg2.connect(self.dsn, options=f"-c search_path={self.schema}")

    def drop_schema(self):
        with closing(self.connect()) as conn, conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA {self.schema} CASCADE")

    def register(self, user_id, enabled=True):
        asyncio.run(bot.record_user(user_id, "test", "Test", enroll_onboarding=enabled))

    def test_atomic_registration_parallel_claim_restart_and_expiry(self):
        with patch.object(bot, "get_db_connection", self.connect):
            self.register(1)  # Existing profile.
            self.register(2)  # Historical delivery without a profile.
            self.register(3, enabled=False)
            self.register(3)
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(self.register, [4, 4]))

        steps = {step.key: day for day, step in onboarding.PENDING_STEPS.items()}
        with closing(self.connect()) as conn, conn, conn.cursor() as cur:
            cur.execute("SELECT user_id, count(*) FROM onboarding_messages WHERE day < 0 GROUP BY user_id")
            self.assertEqual(cur.fetchall(), [(4, 12)])
            cur.execute("""
                UPDATE onboarding_messages SET sent_at = (now() AT TIME ZONE 'UTC') - INTERVAL '1 minute'
                WHERE day IN (%s, %s)
            """, (steps["destiny"], steps["planning"]))
            cur.execute("""
                UPDATE onboarding_messages SET sent_at = (now() AT TIME ZONE 'UTC') - INTERVAL '6 minutes'
                WHERE day = %s
            """, (steps["start_tip"],))

        onboarding.expire(self.connect)
        with ThreadPoolExecutor(max_workers=3) as pool:
            claims = list(pool.map(lambda _: onboarding.claim(self.connect), range(3)))
        self.assertEqual({item[1].key for item in claims if item}, {"destiny", "planning"})
        self.assertEqual(claims.count(None), 1)
        # Claims survive fresh connections and an unconfirmed Telegram send.
        self.assertIsNone(onboarding.claim(self.connect))
        with closing(self.connect()) as conn, conn, conn.cursor() as cur:
            cur.execute("SELECT user_id, day FROM onboarding_messages ORDER BY user_id, day")
            rows = cur.fetchall()
            self.assertIn((1, 14), rows)
            self.assertIn((2, 3), rows)
            self.assertIn((4, steps["destiny"] - onboarding.CLAIM_OFFSET), rows)
            self.assertIn((4, steps["planning"] - onboarding.CLAIM_OFFSET), rows)
            self.assertNotIn((4, steps["start_tip"]), rows)

    def test_registration_rolls_back_when_enrollment_fails(self):
        def fail(cur, user_id):
            cur.execute("SELECT * FROM missing_table")

        with patch.object(bot, "get_db_connection", self.connect), \
             patch.object(onboarding, "enroll", side_effect=fail):
            self.register(5)
        with closing(self.connect()) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM users WHERE user_id = 5")
            self.assertEqual(cur.fetchone(), (0,))
