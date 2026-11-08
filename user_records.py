"""Authenticated, bounded snapshots for the four personal Mini Apps."""
import json
import time
from contextlib import closing
from uuid import UUID

import psycopg2
from psycopg2.extras import Json
from flask import request, jsonify

KINDS = {"diary": list, "checklist": dict, "stats": dict, "splitwise": dict, "regions": list}
MAX_BYTES = 8 * 1024 * 1024


def initialize(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_records (
            user_id BIGINT NOT NULL, kind TEXT NOT NULL,
            revision BIGINT NOT NULL DEFAULT 0, data JSONB,
            request_id UUID, updated_at TIMESTAMPTZ,
            window_at TIMESTAMPTZ NOT NULL DEFAULT now(), requests INT NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, kind)
        )
    """)


def register(app, connect, validate):
    @app.route("/api/records/<kind>", methods=["GET", "POST", "OPTIONS"])
    def records(kind):
        headers = {"Access-Control-Allow-Origin": "https://andreev032.github.io",
                   "Access-Control-Allow-Headers": "Content-Type, X-Telegram-Init-Data",
                   "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                   "Cache-Control": "no-store"}
        def respond(body, code=200):
            return jsonify(body), code, headers
        if request.method == "OPTIONS":
            return respond({}, 204)
        if kind not in KINDS:
            return respond({"error": "kind"}, 404)
        signed = request.headers.get("X-Telegram-Init-Data", "")
        if len(signed) > 16384:
            return respond({"error": "auth"}, 401)
        user = validate(signed)
        from urllib.parse import parse_qsl
        try:
            age = time.time() - int(dict(parse_qsl(signed))["auth_date"])
            if not user or not 0 <= age <= 3600:
                return respond({"error": "auth"}, 401)
            uid = int(user["id"])
            if not 0 < uid < 2**63:
                return respond({"error": "auth"}, 401)
        except (ValueError, KeyError, TypeError):
            return respond({"error": "auth"}, 401)
        # Rate limits are shared across workers and released with the transaction.
        try:
            with closing(connect()) as conn, conn, conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (uid,))
                cur.execute("""
                    INSERT INTO user_records (user_id, kind) VALUES (%s,%s)
                    ON CONFLICT DO NOTHING
                """, (uid, kind))
                cur.execute("""
                    UPDATE user_records SET
                        requests = CASE WHEN window_at < now() - interval '1 minute'
                                        THEN 1 ELSE requests + 1 END,
                        window_at = CASE WHEN window_at < now() - interval '1 minute'
                                         THEN now() ELSE window_at END
                    WHERE user_id=%s AND kind=%s RETURNING requests
                """, (uid, kind))
                if cur.fetchone()[0] > 60:
                    return respond({"error": "rate"}, 429)
                if request.method == "GET":
                    cur.execute("SELECT revision, data FROM user_records WHERE user_id=%s AND kind=%s",
                                (uid, kind))
                    row = cur.fetchone()
                    return respond({"user": str(uid), "revision": row[0] if row else 0,
                                    "data": row[1] if row else None})
                if request.content_length is None or request.content_length > MAX_BYTES:
                    return respond({"error": "size"}, 413)
                body = request.get_json(silent=True)
                if not isinstance(body, dict):
                    return respond({"error": "body"}, 400)
                try:
                    request_id = str(UUID(body["request_id"]))
                    revision = body["revision"]
                    data = body["data"]
                    if type(revision) is not int or not 0 <= revision < 2**53:
                        raise ValueError()
                    if not isinstance(data, KINDS[kind]):
                        raise ValueError()
                    json.dumps(data, allow_nan=False)
                except (KeyError, TypeError, ValueError, RecursionError):
                    return respond({"error": "body"}, 400)
                cur.execute("""
                    INSERT INTO user_records (user_id, kind) VALUES (%s,%s)
                    ON CONFLICT DO NOTHING
                """, (uid, kind))
                cur.execute("""
                    SELECT revision, request_id::text, data,
                        updated_at > now() - interval '1 second'
                    FROM user_records WHERE user_id=%s AND kind=%s FOR UPDATE
                """, (uid, kind))
                current, previous, saved, limited = cur.fetchone()
                if previous == request_id:
                    if saved != data:
                        return respond({"error": "request_id"}, 409)
                    return respond({"revision": current})
                if revision != current:
                    return respond({"error": "conflict"}, 409)
                if limited:
                    return respond({"error": "rate"}, 429)
                cur.execute("""
                    UPDATE user_records SET data=%s, revision=revision+1,
                        request_id=%s, updated_at=now()
                    WHERE user_id=%s AND kind=%s RETURNING revision
                """, (Json(data), request_id, uid, kind))
                result = cur.fetchone()[0]
            return respond({"revision": result})
        except psycopg2.Error:
            return respond({"error": "storage"}, 503)
