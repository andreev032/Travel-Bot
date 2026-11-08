import asyncio
import json
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import psycopg2

os.environ.setdefault("BOT_TOKEN", "123:test-only")
import bot


def payment_fixture(plan="month", amount="200.00"):
    payment_id = str(uuid.uuid4())
    metadata = {"plan": plan}
    if plan != "donate":
        metadata["user_id"] = "1"
    payload = {"event": "payment.succeeded", "object": {
        "id": payment_id, "amount": {"value": amount, "currency": "RUB"},
        "metadata": metadata.copy(),
    }}
    payment = SimpleNamespace(
        id=payment_id, status="succeeded", metadata=metadata,
        captured_at=datetime.now(timezone.utc) + timedelta(seconds=2),
        amount=SimpleNamespace(value=amount, currency="RUB"),
        recipient=SimpleNamespace(account_id=bot.YOOKASSA_SHOP_ID), payment_method=None,
    )
    return payload, payment


class WebhookTests(unittest.TestCase):
    def test_forged_notifications_cannot_write(self):
        for field, value in (
            ("id", str(uuid.uuid4())), ("status", "pending"),
            ("metadata", {"plan": "year", "user_id": "1"}),
            ("amount", SimpleNamespace(value="201.00", currency="RUB")),
            ("amount", SimpleNamespace(value="200.00", currency="USD")),
            ("recipient", SimpleNamespace(account_id="other")),
        ):
            with self.subTest(field=field):
                payload, payment = payment_fixture()
                setattr(payment, field, value)
                with patch.object(bot, "YOOKASSA_WEBHOOK_SECRET", ""), \
                     patch.object(bot.YKPayment, "find_one", return_value=payment), \
                     patch.object(bot, "apply_yookassa_payment") as apply:
                    response = bot._flask_app.test_client().post("/yookassa/webhook", json=payload)
                self.assertEqual(response.status_code, 400)
                apply.assert_not_called()

    def test_yookassa_api_outage_returns_retryable_response(self):
        payload, _ = payment_fixture()
        with patch.object(bot, "YOOKASSA_WEBHOOK_SECRET", ""), \
             patch.object(bot.YKPayment, "find_one", side_effect=TimeoutError), \
             patch.object(bot, "apply_yookassa_payment") as apply:
            response = bot._flask_app.test_client().post("/yookassa/webhook", json=payload)
        self.assertEqual(response.status_code, 503)
        apply.assert_not_called()


def country_update(indices, revision=0, source="countries_v2"):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=1, username="one", first_name="Test"),
        message=SimpleNamespace(reply_text=AsyncMock(), web_app_data=SimpleNamespace(
            data=json.dumps({"source": source, "revision": revision, "indices": indices}),
        )),
    )


@unittest.skipUnless(os.getenv("ONBOARDING_LOCAL_TEST_DSN"), "needs disposable local PostgreSQL")
class PostgresTests(unittest.TestCase):
    def setUp(self):
        self.dsn = os.environ["ONBOARDING_LOCAL_TEST_DSN"]
        self.schema = "regression_test_" + uuid.uuid4().hex
        with closing(self.connect()) as conn, conn, conn.cursor() as cur:
            cur.execute(f"CREATE SCHEMA {self.schema}")
        self.addCleanup(self.drop_schema)
        patcher = patch.object(bot, "get_db_connection", self.connect)
        patcher.start()
        self.addCleanup(patcher.stop)
        asyncio.run(bot.init_db(None))
        with closing(self.connect()) as conn, conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users (user_id, referred_by) VALUES (1, 2), (2, NULL);
                INSERT INTO user_country_marks (user_id, country_name) VALUES (1, 'Италия');
            """)

    def connect(self):
        return psycopg2.connect(self.dsn, options=f"-c search_path={self.schema}")

    def drop_schema(self):
        with closing(self.connect()) as conn, conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA {self.schema} CASCADE")

    def test_two_devices_and_explicit_empty_list(self):
        self.assertEqual(bot.get_country_snapshot(1), (0, ["Италия"]))
        first = country_update([bot.COUNTRY_NAMES.index("Франция")])
        second = country_update([bot.COUNTRY_NAMES.index("Германия")])
        asyncio.run(bot.handle_webapp_data(first, None))
        asyncio.run(bot.handle_webapp_data(second, None))
        self.assertIn("сохранён", first.message.reply_text.call_args.args[0])
        self.assertIn("уже изменился", second.message.reply_text.call_args.args[0])
        self.assertEqual(bot.get_country_snapshot(1), (1, ["Франция"]))
        old_client = country_update([], source="countries")
        asyncio.run(bot.handle_webapp_data(old_client, None))
        self.assertIn("не сохранён", old_client.message.reply_text.call_args.args[0])
        self.assertEqual(bot.get_country_snapshot(1), (1, ["Франция"]))
        empty = country_update([], revision=1)
        asyncio.run(bot.handle_webapp_data(empty, None))
        self.assertIn("Посещено: 0", empty.message.reply_text.call_args.args[0])
        self.assertEqual(bot.get_country_snapshot(1), (2, []))
        with closing(self.connect()) as conn, conn.cursor() as cur:
            cur.execute("SELECT countries_count FROM user_countries WHERE user_id = 1")
            self.assertEqual(cur.fetchone(), (0,))

    def test_write_failure_rolls_back_and_does_not_confirm(self):
        with closing(self.connect()) as conn, conn, conn.cursor() as cur:
            cur.execute("ALTER TABLE user_countries ADD CHECK (countries_count = 0)")
        update = country_update([bot.COUNTRY_NAMES.index("Франция")])
        asyncio.run(bot.handle_webapp_data(update, None))
        self.assertIn("Не удалось сохранить", update.message.reply_text.call_args.args[0])
        self.assertEqual(bot.get_country_snapshot(1), (0, ["Италия"]))

    def test_parallel_duplicate_payment_donation_and_historical_payment(self):
        payload, payment = payment_fixture()
        donation, donation_payment = payment_fixture("donate", "100.00")
        historical, historical_payment = payment_fixture()
        historical_payment.captured_at -= timedelta(days=1)
        payments = {item.id: item for item in (payment, donation_payment, historical_payment)}

        def post(body):
            return bot._flask_app.test_client().post("/yookassa/webhook", json=body).status_code

        with patch.object(bot, "YOOKASSA_WEBHOOK_SECRET", ""), \
             patch.object(bot.YKPayment, "find_one", side_effect=payments.__getitem__):
            with ThreadPoolExecutor(max_workers=3) as pool:
                self.assertEqual(list(pool.map(post, [payload] * 3)), [200] * 3)
            self.assertEqual(post(donation), 200)
            self.assertEqual(post(donation), 200)
            self.assertEqual(post(historical), 200)

        with closing(self.connect()) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM processed_yookassa_payments")
            self.assertEqual(cur.fetchone(), (2,))
            cur.execute("SELECT subscription_type FROM users WHERE user_id = 1")
            self.assertEqual(cur.fetchone(), ("month",))
            cur.execute("SELECT tokens FROM users WHERE user_id = 2")
            self.assertEqual(cur.fetchone(), (1,))
