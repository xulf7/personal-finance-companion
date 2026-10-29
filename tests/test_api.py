"""业务 HTTP 串联测试；仅访问 StorageCase 创建的隔离数据库。"""

from contextlib import closing
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from unittest.mock import patch

from app import create_app
from app.config import Config
from app.demo import budget, seed_demo, summary
from app.errors import SchemaError
from app.md import MDService
from app.md_provider import ASSETS, MockProvider
from test_md import Clock, CountingProvider
from test_storage import StorageCase


class APITests(StorageCase):
    def setUp(self):
        super().setUp()
        self.initialize()
        self.client = self.app.test_client()

    def value(self, result, status=200):
        self.assertEqual(result.status_code, status, result.get_data(as_text=True))
        self.assertEqual(result.mimetype, "application/json")
        return result.get_json()["data"]

    def test_profile_goal_holding_edits_precision_conflict_and_transaction_rollback(self):
        initial = self.value(self.client.get("/api/profile"))
        self.assertIsNone(initial["cash"])
        changes = [*budget("123.45"),
            {"op": "set_finances", "currency": "CNY", "values": {"debt": "0.00"}},
            {"op": "set_goal", "goal": {"id": "api-goal", "name": "虚构目标", "target_amount": "9000.12", "currency": "CNY", "target_date": None}},
            {"op": "set_holding", "holding": {"id": "api-holding", "asset_code": "FICTIONAL", "quantity": "1.23456789", "unit_cost": "0.01", "currency": "USD"}},
        ]
        saved = self.value(self.client.patch("/api/profile", json={"expected_version": 0, "changes": changes}))
        self.assertEqual(saved["version"], 1)
        self.assertEqual(saved["investment_budget"], "123.45")
        self.assertEqual(saved["debt"], "0.00")
        self.assertIsNone(saved["cash"])
        self.assertEqual(saved["holdings"][0]["quantity"], "1.23456789")
        conflict = self.client.patch("/api/profile", json={"expected_version": 0, "changes": budget("999")})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json["error"]["code"], "version_conflict")
        partial = self.client.patch("/api/profile", json={"expected_version": 1, "changes": budget("999") + [{"op": "remove_goal", "id": "missing"}]})
        self.assertEqual(partial.status_code, 404)
        self.assertEqual(self.value(self.client.get("/api/profile")), saved)
        removed = self.value(self.client.patch("/api/profile", json={"expected_version": 1, "changes": [{"op": "remove_goal", "id": "api-goal"}, {"op": "remove_holding", "id": "api-holding"}]}))
        self.assertEqual(removed["version"], 2)
        self.assertEqual(removed["goals"], [])
        self.assertEqual(removed["holdings"], [])

    def test_create_session_save_user_messages_reconnect_and_no_fake_replies(self):
        session = self.value(self.client.post("/api/conversations", json={"title": "虚构 API 会话"}), 201)
        url = f"/api/conversations/{session['id']}/messages"
        raw = '<img src=x onerror="alert(1)"> 同样的原文'
        for _ in range(2):
            message = self.value(self.client.post(url, json={"content": raw}), 201)
            self.assertEqual(message["role"], "user")
        other_client = self.app.test_client()
        rows = self.value(other_client.get(url))
        self.assertEqual([m["sequence"] for m in rows], [1, 2])
        self.assertEqual([m["content"] for m in rows], [raw, raw])
        self.assertEqual([m["role"] for m in rows], ["user", "user"])
        self.assertEqual(self.value(other_client.get(f"/api/conversations/{session['id']}/summaries")), [])
        self.assertEqual(self.value(other_client.get("/api/profile-changes")), [])
        for role in ("assistant", "system", "tool", "user"):
            self.assertEqual(self.client.post(url, json={"role": role, "content": "不要保存"}).status_code, 400)
        self.assertEqual(len(self.value(other_client.get(url))), 2)

    def test_strict_json_shapes_versions_and_missing_objects(self):
        for value in (None, [], "text", {}, {"title": "ok", "extra": True}, {"title": False}):
            result = self.client.post("/api/conversations", data=json.dumps(value), content_type="application/json")
            self.assertEqual(result.status_code, 400, value)
        self.assertEqual(self.client.post("/api/conversations", data="title=x").status_code, 400)
        self.assertEqual(self.client.post("/api/conversations", data="{", content_type="application/json").status_code, 400)
        for version in (True, "0", 0.0, None):
            self.assertEqual(self.client.patch("/api/profile", json={"expected_version": version, "changes": budget("1")}).status_code, 400)
        self.assertEqual(self.client.patch("/api/profile", json={"expected_version": 0, "changes": budget(1.23)}).status_code, 400)
        for path in ("/api/conversations/missing/messages", "/api/conversations/missing/summaries", "/api/not-an-endpoint"):
            result = self.client.get(path)
            self.assertEqual(result.status_code, 404)
            self.assertIn("error", result.json)
        for path in ("/api/overview", "/api/conversations/missing/summaries"):
            result = self.client.post(path, json={})
            self.assertEqual(result.status_code, 405)
            self.assertEqual(result.json["error"]["code"], "method_not_allowed")

    def test_confirm_reject_repeat_confirm_and_old_version_conflict(self):
        first, stale, rejected = self.candidate("500"), self.candidate("800"), self.candidate("900")
        candidates = self.value(self.client.get("/api/profile-changes"))
        self.assertEqual(len(candidates), 3)
        self.assertEqual(candidates[0]["source_message"]["role"], "user")
        self.assertIn("title", candidates[0]["source_conversation"])
        url = f"/api/profile-changes/{first['id']}/confirm"
        confirmed = self.value(self.client.post(url, json={}))
        self.assertEqual(self.value(self.client.post(url, json={})), confirmed)
        self.assertEqual(self.value(self.client.get("/api/profile"))["investment_budget"], "500.00")
        self.assertEqual(self.client.post(f"/api/profile-changes/{stale['id']}/confirm", json={}).status_code, 409)
        profile = self.store.get_profile()
        self.value(self.client.post(f"/api/profile-changes/{rejected['id']}/reject", json={}))
        self.assertEqual(self.store.get_profile(), profile)
        self.assertEqual(self.client.post(f"/api/profile-changes/{rejected['id']}/confirm", json={}).status_code, 409)
        self.assertEqual(self.client.post(f"/api/profile-changes/{first['id']}/reject", json={}).status_code, 409)
        self.assertEqual(self.client.post(url, json={"force": True}).status_code, 400)
        self.assertEqual(self.client.post("/api/profile-changes/missing/confirm", json={}).status_code, 404)

    def test_read_endpoints_do_not_write_or_fetch_and_summaries_are_read_only(self):
        seed_demo(self.database)
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        session = self.store.list_conversations()[0]
        with patch.object(MockProvider, "fetch", side_effect=AssertionError("GET 不应获取行情")):
            for path in ("/api/profile", "/api/conversations", f"/api/conversations/{session['id']}/messages", f"/api/conversations/{session['id']}/summaries", "/api/overview", "/api/profile-changes", "/api/market/assets"):
                self.value(self.client.get(path))
            result = self.value(self.client.get("/api/market/cache", query_string={"asset_id": ASSETS[0].asset_id, "metric": "price"}))
            self.assertIsNone(result["data"])
            self.assertEqual(result["cache_status"], "missing")
            self.assertEqual(result["refresh_status"], "not_requested")
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), before)
        self.assertEqual(self.client.post("/api/overview", json=summary()).status_code, 405)

    def test_market_status_contract_and_safe_provider_errors(self):
        provider, clock = CountingProvider(), Clock()
        service = MDService(self.database, provider=provider, clock=clock)
        asset = ASSETS[0]
        query = {"asset_id": asset.asset_id, "metric": asset.metric}
        with patch.object(MDService, "from_app", return_value=service):
            initial = self.value(self.client.get("/api/market/cache", query_string=query))
            self.assertIsNone(initial["data"])
            self.assertEqual(provider.calls, 0)
            fresh = self.value(self.client.post("/api/market/refresh", json=query))
            self.assertIsInstance(fresh["data"]["value"], str)
            self.assertTrue(fresh["data"]["is_simulated"])
            self.assertEqual(fresh["refresh_status"], "updated")
            clock.advance(300)
            stale = self.value(self.client.get("/api/market/cache", query_string=query))
            self.assertEqual(stale["cache_status"], "stale")
            self.assertEqual(provider.calls, 1)
            with patch.object(provider, "fetch", side_effect=OSError("SECRET /private/internal.sqlite3")):
                failed = self.value(self.client.post("/api/market/refresh", json=query))
                self.assertEqual(failed["data"], fresh["data"])
                self.assertEqual(failed["cache_status"], "stale")
                self.assertEqual(failed["refresh_status"], "failed")
                self.assertEqual(failed["error"]["code"], "provider_error")
                self.assertNotIn("SECRET", json.dumps(failed))
                missing = self.value(self.client.post("/api/market/refresh", json={"asset_id": ASSETS[1].asset_id, "metric": "nav"}))
                self.assertIsNone(missing["data"])
                self.assertEqual(missing["cache_status"], "missing")
        with patch.object(MDService, "refresh", return_value={**fresh, "refresh_status": "superseded", "error": {"code": "cache_changed", "message": "已有更新，未覆盖"}}):
            result = self.value(self.client.post("/api/market/refresh", json=query))
            self.assertEqual(result["refresh_status"], "superseded")
            self.assertEqual(result["data"], fresh["data"])
        for query_string in ({}, {**query, "force": "1"}, {"asset_id": "DEMO001", "metric": "price"}):
            self.assertEqual(self.client.get("/api/market/cache", query_string=query_string).status_code, 400)
        self.assertEqual(self.client.get("/api/market/refresh").status_code, 405)

    def test_unexpected_and_schema_errors_do_not_leak_internals(self):
        with patch("app.storage.Store.get_profile", side_effect=RuntimeError("SECRET /private/path")), self.assertLogs(self.app.logger, level="ERROR"):
            result = self.client.get("/api/profile")
        self.assertEqual(result.status_code, 500)
        self.assertEqual(result.json["error"]["code"], "internal_error")
        self.assertNotIn("SECRET", result.get_data(as_text=True))
        with patch("app.storage.Store.get_profile", side_effect=SchemaError("/private/path")):
            result = self.client.get("/api/profile")
        self.assertEqual(result.status_code, 503)
        self.assertNotIn("/private/path", result.get_data(as_text=True))
        self.assertNotIn("Access-Control-Allow-Origin", self.client.get("/api/profile", headers={"Origin": "https://other.example"}).headers)

    def test_relocated_api_uses_its_own_database(self):
        seed_demo(self.database)
        copy = self.root / "API copy with spaces"
        shutil.copytree(Config.PROJECT_ROOT / "app", copy / "app", ignore=shutil.ignore_patterns("__pycache__"))
        target = copy / "runtime/data/app.sqlite3"
        target.parent.mkdir(parents=True)
        shutil.copy2(self.database, target)
        (copy / "probe.py").write_text('''
from app import create_app
from app.demo import budget
client = create_app().test_client()
assert client.get('/').status_code == 200
assert client.get('/api/profile').json['data']['investment_budget'] == '800.00'
result = client.patch('/api/profile', json={'expected_version':2,'changes':budget('900')})
assert result.status_code == 200
assert client.get('/api/profile').json['data']['investment_budget'] == '900.00'
print('relocated HTTP adapter read/write verified')
''', encoding="utf-8", newline="\n")
        env = os.environ.copy(); env.pop("PYTHONPATH", None)
        result = subprocess.run([sys.executable, "-B", str(copy / "probe.py")], cwd=self.root, env=env, capture_output=True, text=True, check=True)
        self.assertIn("verified", result.stdout)
        self.assertEqual(self.store.get_profile()["investment_budget"], "800.00")


class APINotReadyTests(StorageCase):
    def test_uninitialized_page_health_and_api_guidance_without_creation(self):
        client = self.app.test_client()
        with patch("sqlite3.connect", side_effect=AssertionError("页面/健康检查不应连接数据库")):
            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(client.get("/health").json, {"status": "ok"})
            self.assertEqual(client.get("/api/market/assets").status_code, 200)
        for path in ("/api/profile", "/api/conversations", "/api/profile-changes", "/api/overview"):
            result = client.get(path)
            self.assertEqual(result.status_code, 503)
            self.assertEqual(result.json["error"]["code"], "database_not_ready")
            self.assertIn("init-db", result.json["error"]["message"])
        self.assertFalse(self.database.exists())

    def test_upgrade_required_and_newer_database_return_safe_guidance(self):
        old = self.root / "old-migrations"; old.mkdir()
        shutil.copy2(Config.PROJECT_ROOT / "app/migrations/001_initial.sql", old / "001_initial.sql")
        from app import db
        db.initialize(self.database, self.backups, migrations_dir=old)
        self.assertEqual(self.app.test_client().get("/api/profile").json["error"]["code"], "database_not_ready")
        with closing(sqlite3.connect(self.database)) as conn:
            conn.execute("PRAGMA user_version=99")
        result = self.app.test_client().get("/api/profile")
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json["error"]["code"], "database_schema_error")
