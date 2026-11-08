import hashlib
import hmac
import json
import time
import uuid
from urllib.parse import urlencode
from unittest.mock import patch
import unittest
import test_payment_and_countries as fixtures
import bot


def signed(uid, age=0):
    values = {"auth_date": str(int(time.time()) - age), "user": json.dumps({"id": uid})}
    secret = hmac.new(b"WebAppData", bot.TOKEN.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, "\n".join(
        f"{key}={value}" for key, value in sorted(values.items())).encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(values)}


@unittest.skipUnless(__import__("os").getenv("ONBOARDING_LOCAL_TEST_DSN"), "local PostgreSQL required")
class RecordsTests(unittest.TestCase):
    setUp = fixtures.PostgresTests.setUp
    connect = fixtures.PostgresTests.connect
    drop_schema = fixtures.PostgresTests.drop_schema

    def test_auth_isolation_and_empty(self):
        client = bot._flask_app.test_client()
        url = "/api/records/diary"
        self.assertEqual(client.get(url).status_code, 401)
        self.assertEqual(client.get(url, headers=signed(1, 4000)).status_code, 401)
        payload = {"revision": 0, "request_id": str(uuid.uuid4()), "data": [{"title": "Trip"}]}
        self.assertEqual(client.post(url, json=payload, headers=signed(1)).status_code, 200)
        self.assertEqual(client.post(url, json=payload, headers=signed(1)).status_code, 200)
        self.assertEqual(client.get(url, headers=signed(2)).json["data"], None)
        self.assertEqual(client.get(url, headers=signed(1)).json["data"], payload["data"])
        payload["request_id"] = str(uuid.uuid4())
        self.assertEqual(client.post(url, json=payload, headers=signed(1)).status_code, 409)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE user_records SET updated_at = now() - interval '2 seconds'")
        payload.update(revision=1, data=[])
        self.assertEqual(client.post(url, json=payload, headers=signed(1)).status_code, 200)
        self.assertEqual(client.get(url, headers=signed(1)).json["data"], [])

    def test_limits_invalid_data_and_forgery(self):
        client = bot._flask_app.test_client()
        url = "/api/records/stats"
        bad = signed(1)
        bad["X-Telegram-Init-Data"] += "x"
        self.assertEqual(client.get(url, headers=bad).status_code, 401)
        payload = {"revision": 0, "request_id": str(uuid.uuid4()), "data": []}
        self.assertEqual(client.post(url, json=payload, headers=signed(1)).status_code, 400)
        with patch("user_records.MAX_BYTES", 5):
            self.assertEqual(client.post(url, json=payload, headers=signed(1)).status_code, 413)
        for _ in range(61):
            result = client.get(url, headers=signed(1))
        self.assertEqual(result.status_code, 429)

    def test_all_records_survive_restart_and_parallel_requests(self):
        import asyncio
        from concurrent.futures import ThreadPoolExecutor
        import psycopg2
        for kind, data in (("diary", [{"title": "Trip"}]), ("checklist", {"hat": True}),
                           ("stats", {"flights": 3}), ("regions", ["Москва"]), ("splitwise", {"members": []})):
            body = {"revision": 0, "request_id": str(uuid.uuid4()), "data": data}
            url = "/api/records/" + kind
            def post(_):
                return bot._flask_app.test_client().post(url, json=body, headers=signed(1)).status_code
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(list(pool.map(post, range(2))), [200, 200])
            asyncio.run(bot.init_db(None))
            asyncio.run(bot.record_user(1, "one", "Test"))
            result = bot._flask_app.test_client().get(url, headers=signed(1))
            self.assertEqual(result.json["data"], data)
            self.assertEqual(result.json["revision"], 1)
        with patch.object(bot, "get_db_connection", side_effect=psycopg2.OperationalError):
            self.assertEqual(bot._flask_app.test_client().get(
                "/api/records/diary", headers=signed(1)).status_code, 503)
