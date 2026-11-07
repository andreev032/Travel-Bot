import asyncio
import hashlib
import hmac
import json
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import urlencode

import psycopg2
from telegram.error import Forbidden, RetryAfter, TimedOut

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
                        self.assertEqual(context.user_data["quiz_score"], 3)
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

    async def test_start_keeps_progress_and_preserves_guide_deep_link(self):
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
        self.assertEqual(context.user_data, {"quiz_index": 4, "quiz_score": 3})
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

    async def test_returning_to_game_resumes_score_and_question(self):
        update = update_for_callback("intro:games")
        update.message = update.effective_message
        context = SimpleNamespace(user_data={}, chat_data={})
        self.assertEqual(await bot.quiz_start(update, context), bot.QUIZ_ACTIVE)
        question = context.user_data["quiz_questions"][0]
        update.message.text = question["correct"]
        await bot.quiz_handler(update, context)
        await bot.go_home(update, context)
        update.message.text = "🧠 Викторина о путешествиях"
        self.assertEqual(await bot.games_menu_handler(update, context), bot.QUIZ_ACTIVE)
        self.assertEqual(context.user_data["quiz_score"], 1)
        update.message.text = "➡️ Следующий вопрос"
        await bot.quiz_handler(update, context)
        self.assertEqual(context.user_data["quiz_index"], 1)
        self.assertEqual(context.user_data["quiz_score"], 1)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_delivery_outcomes(self):
        delivery = (123, "destiny", 1)
        for result, expected in [
            (SimpleNamespace(message_id=5), "sent"), (RetryAfter(8), "pending"),
            (Forbidden("blocked"), "failed"), (TimedOut(), "uncertain"),
        ]:
            telegram = SimpleNamespace(send_message=AsyncMock())
            if isinstance(result, Exception):
                telegram.send_message.side_effect = result
            else:
                telegram.send_message.return_value = result
            with patch.object(onboarding, "finish") as finish:
                await onboarding.deliver(telegram, None, delivery)
                self.assertEqual(finish.call_args.args[2], expected)
                telegram.send_message.assert_awaited_once()


def payment_fixture(payment_id, plan="month"):
    value, description = (
        ("300.00", bot.YOOKASSA_DONATION_DESCRIPTION)
        if plan == "donate" else bot.YOOKASSA_PLANS[plan]
    )
    return {
        "id": payment_id, "status": "succeeded", "paid": True, "test": False,
        "recipient": {"account_id": bot.YOOKASSA_SHOP_ID},
        "amount": {"value": value, "currency": "RUB"},
        "description": description, "metadata": {"plan": plan, "user_id": "123"},
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


class PaymentTests(unittest.TestCase):
    def setUp(self):
        self.payment_id = str(uuid.uuid4())
        self.client = bot._flask_app.test_client()
        self.addCleanup(patch.stopall)
        patch.object(bot, "YOOKASSA_WEBHOOK_SECRET", "").start()
        patch.object(bot, "YOOKASSA_SECRET_KEY", "test-only-merchant-key").start()
        self.api = patch.object(bot.requests, "get").start()
        self.apply = patch.object(bot, "_apply_yookassa_payment", return_value=None).start()

    def post(self, **object_fields):
        return self.client.post("/yookassa/webhook", json={
            "event": "payment.succeeded", "object": {"id": self.payment_id, **object_fields},
        })

    def test_api_data_controls_subscription_and_credentials_stay_on_official_host(self):
        payment = payment_fixture(self.payment_id)
        self.api.return_value = Mock(status_code=200, json=Mock(return_value=payment))
        response = self.post(metadata={"user_id": "999", "plan": "year"}, amount={"value": "0.00"})
        self.assertEqual(response.status_code, 200)
        self.api.assert_called_once_with(
            f"https://api.yookassa.ru/v3/payments/{self.payment_id}",
            auth=(bot.YOOKASSA_SHOP_ID, "test-only-merchant-key"),
            timeout=(3, 8), allow_redirects=False,
        )
        self.assertEqual(self.apply.call_args.args[:4], (self.payment_id, 123, "month", None))

    def test_invalid_provider_fields_cannot_change_database(self):
        for field, value in {
            "id": str(uuid.uuid4()), "status": "pending", "paid": False, "test": True,
            "recipient": {"account_id": "other"},
            "amount": {"value": "1.00", "currency": "RUB"},
            "description": "other purchase", "metadata": {"plan": "unknown", "user_id": "123"},
            "refunded_amount": {"value": "200.00", "currency": "RUB"},
            "captured_at": "invalid",
        }.items():
            with self.subTest(field=field):
                payment = payment_fixture(self.payment_id)
                payment[field] = value
                self.api.return_value = Mock(status_code=200, json=Mock(return_value=payment))
                self.assertEqual(self.post().status_code, 200)
        payment = payment_fixture(self.payment_id)
        payment["amount"]["currency"] = "USD"
        self.api.return_value = Mock(status_code=200, json=Mock(return_value=payment))
        self.assertEqual(self.post().status_code, 200)
        self.apply.assert_not_called()

    def test_donation_is_verified_and_never_applied_even_if_webhook_claims_subscription(self):
        payment = payment_fixture(self.payment_id, "donate")
        self.api.return_value = Mock(status_code=200, json=Mock(return_value=payment))
        for _ in range(2):
            self.assertEqual(self.post(metadata={"plan": "year"}).status_code, 200)
        self.assertEqual(self.api.call_count, 2)
        self.apply.assert_not_called()

    def test_unavailable_api_or_storage_is_retriable_without_trusting_body(self):
        self.api.side_effect = bot.requests.Timeout
        self.assertEqual(self.post(metadata={"plan": "month"}).status_code, 503)
        self.apply.assert_not_called()
        self.api.side_effect = None
        self.api.return_value = Mock(status_code=200, json=Mock(return_value=payment_fixture(self.payment_id)))
        self.apply.side_effect = RuntimeError
        self.assertEqual(self.post().status_code, 503)

    def test_invalid_id_and_missing_configured_header_are_rejected_before_lookup(self):
        self.payment_id = "../accounts"
        self.assertEqual(self.post().status_code, 400)
        self.payment_id = str(uuid.uuid4())
        with patch.object(bot, "YOOKASSA_WEBHOOK_SECRET", "header-test"):
            self.assertEqual(self.post().status_code, 403)
        self.api.assert_not_called()
        self.apply.assert_not_called()


class SecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_admin_cannot_broadcast_testpost_or_approve_queue(self):
        update = update_for_callback("pq:approve:1")
        with patch.object(bot, "ADMIN_ID", 456), patch.object(bot, "_queue_set_status") as set_status:
            await bot.broadcast_command(update, None)
            await bot.testpost_command(update, None)
            await bot.queue_callback_handler(update, None)
        set_status.assert_not_called()
        update.callback_query.answer.assert_awaited_once()

    async def test_countries_sync_rejects_empty_or_malformed_input(self):
        update = update_for_callback("intro:countries")
        update.message = update.effective_message
        context = SimpleNamespace(user_data={"back_to_mytrips": True})
        with patch.object(bot, "upsert_countries_count") as save:
            for visited in ([], ["Italy", None], "Italy", ["x\u0000"]):
                update.message.web_app_data = SimpleNamespace(data=json.dumps({
                    "source": "countries", "visited": visited,
                }))
                self.assertEqual(await bot.handle_webapp_data(update, context), bot.MAIN_MENU)
            save.assert_not_called()
            update.message.web_app_data.data = json.dumps({
                "source": "countries", "visited": ["Italy", "Italy", "France"],
            })
            await bot.handle_webapp_data(update, context)
            self.assertEqual(save.call_args.args[-2:], (2, {"Italy", "France"}))

    async def test_mini_app_signature_expires_and_cannot_be_modified(self):
        def sign(auth_date):
            fields = {"auth_date": str(auth_date), "user": '{"id":123}'}
            check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
            secret = hmac.new(b"WebAppData", bot.TOKEN.encode(), hashlib.sha256).digest()
            fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
            return urlencode(fields)

        now = int(datetime.now(timezone.utc).timestamp())
        valid = sign(now)
        self.assertEqual(bot._validate_init_data(valid), {"id": 123})
        self.assertIsNone(bot._validate_init_data(valid.replace("123", "999")))
        self.assertIsNone(bot._validate_init_data(sign(now - 86401)))
        self.assertIsNone(bot._validate_init_data(sign(now + 120)))


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
                    first_seen TIMESTAMP DEFAULT NOW(), last_seen TIMESTAMP DEFAULT NOW(),
                    is_premium BOOLEAN, premium_expires_at TIMESTAMP, subscription_type TEXT,
                    yookassa_payment_method_id TEXT, referred_by BIGINT, tokens INTEGER
                );
                CREATE TABLE onboarding_messages (
                    user_id BIGINT, day INT, PRIMARY KEY (user_id, day)
                );
                INSERT INTO users (user_id, first_name) VALUES (1, 'Existing');
                INSERT INTO onboarding_messages VALUES (1, 14), (2, 3);
            """)
        self.addCleanup(self.drop_schema)
        migrations = Path(__file__).resolve().parents[1] / "migrations"
        with closing(self.connect()) as conn, conn.cursor() as cur:
            conn.autocommit = True
            for _ in range(2):
                for migration in sorted(migrations.glob("*.sql")):
                    cur.execute(migration.read_text(encoding="utf-8"))

    def connect(self):
        return psycopg2.connect(self.dsn, options=f"-c search_path={self.schema}")

    def drop_schema(self):
        with closing(self.connect()) as conn, conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA {self.schema} CASCADE")

    def register(self, user_id, enabled=True):
        asyncio.run(bot.record_user(user_id, "test", "Test", enroll_onboarding=enabled))

    def test_atomic_registration_parallel_claim_restart_and_expiry(self):
        with patch.object(bot, "get_db_connection", self.connect):
            self.register(1)
            self.register(2)
            self.register(3, enabled=False)
            self.register(3)
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(self.register, [4, 4]))

        with closing(self.connect()) as conn, conn, conn.cursor() as cur:
            cur.execute("SELECT user_id FROM onboarding_enrollments")
            self.assertEqual(cur.fetchall(), [(4,)])
            cur.execute("SELECT count(*) FROM onboarding_deliveries")
            self.assertEqual(cur.fetchone(), (12,))
            cur.execute("SELECT count(*) FROM onboarding_messages")
            self.assertEqual(cur.fetchone(), (2,))
            cur.execute("""
                UPDATE onboarding_deliveries SET
                  due_at = NOW() - INTERVAL '1 minute', next_attempt_at = NOW()
                WHERE step_key IN ('destiny', 'planning')
            """)
            cur.execute("""
                UPDATE onboarding_deliveries SET
                  due_at = NOW() - INTERVAL '6 minutes', next_attempt_at = NOW()
                WHERE step_key = 'start_tip'
            """)
        onboarding.maintain_queue(self.connect)
        with ThreadPoolExecutor(max_workers=3) as pool:
            claims = list(pool.map(lambda _: onboarding.claim(self.connect), range(3)))
        self.assertEqual({result[1] for result in claims if result}, {"destiny", "planning"})
        self.assertEqual(claims.count(None), 1)
        sent, lost = [result for result in claims if result]
        onboarding.finish(self.connect, sent, "sent", 123)
        # A new worker sees the abandoned claim but never retransmits it.
        with closing(self.connect()) as conn, conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE onboarding_deliveries SET claimed_at = NOW() - INTERVAL '3 minutes'
                WHERE step_key = %s
            """, (lost[1],))
        onboarding.maintain_queue(self.connect)
        self.assertIsNone(onboarding.claim(self.connect))
        with closing(self.connect()) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT step_key, status FROM onboarding_deliveries
                WHERE step_key IN ('destiny', 'planning', 'start_tip')
            """)
            self.assertEqual(dict(cur.fetchall()), {
                sent[1]: "sent", lost[1]: "uncertain", "start_tip": "expired",
            })

    def test_payment_applies_once_under_concurrency_and_rejects_pre_cutover_replays(self):
        with closing(self.connect()) as conn, conn, conn.cursor() as cur:
            cur.execute("INSERT INTO users (user_id, referred_by) VALUES (123, 1)")
            cur.execute("SELECT captured_since FROM yookassa_webhook_cutover")
            cutover = cur.fetchone()[0]

        payment_id = str(uuid.uuid4())
        with patch.object(bot, "get_db_connection", self.connect):
            self.assertIsNone(bot._apply_yookassa_payment(
                str(uuid.uuid4()), 123, "month", None, cutover - timedelta(seconds=1),
            ))
            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(
                    lambda _: bot._apply_yookassa_payment(payment_id, 123, "year", None, cutover),
                    range(3),
                ))
            self.assertEqual(sum(result is not None for result in results), 1)
            retry_id = str(uuid.uuid4())
            with self.assertRaises(LookupError):
                bot._apply_yookassa_payment(retry_id, 999, "month", None, cutover)
        with closing(self.connect()) as conn, conn, conn.cursor() as cur:
            cur.execute("SELECT tokens FROM users WHERE user_id = 1")
            self.assertEqual(cur.fetchone(), (3,))
            cur.execute("SELECT count(*) FROM yookassa_applied_payments")
            self.assertEqual(cur.fetchone(), (1,))
            cur.execute("SELECT is_premium, subscription_type FROM users WHERE user_id = 123")
            self.assertEqual(cur.fetchone(), (True, "year"))

    def test_registration_rolls_back_when_enrollment_fails(self):
        def fail(cur, user_id):
            cur.execute("SELECT * FROM missing_table")

        with patch.object(bot, "get_db_connection", self.connect), \
             patch.object(onboarding, "enroll", side_effect=fail):
            self.register(5)
        with closing(self.connect()) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM users WHERE user_id = 5")
            self.assertEqual(cur.fetchone(), (0,))
