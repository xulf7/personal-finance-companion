"""MD 使用独立文件数据库、注入时钟和替身，不等待真实时间或访问外网。"""

from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from unittest.mock import patch

from app import create_app, db
from app.config import Config
from app.demo import seed_demo
from app.errors import InitializationRequired, SchemaError, ValidationError
from app.md import MDService, UnsupportedAsset
from app.md_provider import ASSETS, MockProvider
from test_storage import StorageCase

STOCK, FUND, INDEX = ASSETS
SP_CA_TABLES = ("profile", "goals", "holdings", "conversations", "messages", "summaries",
                "overviews", "overview_sources", "profile_changes", "demo_seeds")


def sp_ca_snapshot(path):
    with closing(sqlite3.connect(path)) as conn:
        return {table: conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in SP_CA_TABLES}


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 26, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class CountingProvider(MockProvider):
    def __init__(self):
        self.calls = 0
        self.transform = lambda quote: quote

    def fetch(self, asset, metric):
        self.calls += 1
        return self.transform(super().fetch(asset, metric))


class MDMigrationTests(StorageCase):
    def old_database(self):
        old = self.root / "old-migrations"
        old.mkdir()
        shutil.copy2(db.MIGRATIONS_DIR / "001_initial.sql", old / "001_initial.sql")
        db.initialize(self.database, self.backups, migrations_dir=old)
        with patch.object(db, "MIGRATIONS_DIR", old):
            seed_demo(self.database)
        return old

    def test_real_v1_to_v2_upgrade_preserves_all_sp_ca_and_has_valid_backup(self):
        self.old_database()
        before = sp_ca_snapshot(self.database)
        provider = CountingProvider()
        with self.assertRaisesRegex(InitializationRequired, "升级"):
            MDService(self.database, provider=provider).query(STOCK.asset_id, STOCK.metric)
        self.assertEqual(provider.calls, 0)
        result = self.initialize()
        self.assertEqual(result["version"], 2)
        self.assertEqual(sp_ca_snapshot(self.database), before)
        self.assertEqual(sp_ca_snapshot(result["backup"]), before)
        with closing(sqlite3.connect(result["backup"])) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='md_cache'").fetchone())
        self.assertEqual(self.store.get_profile()["investment_budget"], "800.00")
        self.assertEqual(self.store.get_profile()["version"], 2)
        self.assertEqual(len(self.store.list_changes("pending")), 1)
        self.assertIsNone(self.initialize()["backup"])
        self.assertEqual(len(list(self.backups.iterdir())), 1)

    def test_v1_failed_upgrade_rolls_back_and_keeps_backup(self):
        old = self.old_database()
        before = sp_ca_snapshot(self.database)
        (old / "002_broken.sql").write_text(
            "CREATE TABLE md_partial (value TEXT);\n"
            "UPDATE profile SET investment_budget='999.00';\n"
            "INSERT INTO nonexistent VALUES (1);\n", encoding="utf-8", newline="\n",
        )
        with self.assertRaisesRegex(SchemaError, "回滚"):
            db.initialize(self.database, self.backups, migrations_dir=old)
        self.assertEqual(sp_ca_snapshot(self.database), before)
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 1)
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='md_partial'").fetchone())
        backup = next(self.backups.iterdir())
        self.assertEqual(sp_ca_snapshot(backup), before)
        with closing(sqlite3.connect(backup)) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_md_import_factory_health_and_catalog_are_lazy(self):
        with patch("sqlite3.connect", side_effect=AssertionError("不得连接数据库")), patch.object(
            MockProvider, "fetch", side_effect=AssertionError("不得读取提供器")
        ):
            app = create_app({"DATABASE_PATH": self.database})
            self.assertEqual(app.test_client().get("/").status_code, 200)
            self.assertEqual(app.test_client().get("/health").json, {"status": "ok"})
            self.assertEqual(len(MDService(self.database).list_assets()), 3)
        for command in ("md-show", "md-refresh"):
            result = self.app.test_cli_runner().invoke(args=[command])
            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("init-db", result.output)
        self.assertFalse(self.database.exists())


class MDCacheTests(StorageCase):
    def setUp(self):
        super().setUp()
        self.initialize()
        self.clock = Clock()
        self.provider = CountingProvider()
        self.md = MDService(self.database, provider=self.provider, clock=self.clock)

    def test_three_types_units_precision_fixed_source_times_and_reconnect(self):
        for asset in ASSETS:
            with self.subTest(asset=asset.asset_type):
                result = self.md.query(asset.asset_id, asset.metric)
                row = result["data"]
                self.assertEqual(result["refresh_status"], "updated")
                self.assertTrue(row["is_simulated"])
                self.assertEqual(row["provider_id"], "mock-fixed-v1")
                self.assertEqual(row["currency"], asset.currency)
                self.assertEqual(row["unit"], asset.unit)
                self.assertEqual(len(row["value"].split(".")[1]), 8)
                other = MDService(self.database, clock=self.clock)
                self.assertEqual(other.read_cache(asset.asset_id, asset.metric)["data"], row)
        old = self.md.read_cache(FUND.asset_id, FUND.metric)["data"]
        self.assertEqual(old["data_time_kind"], "date")
        self.assertEqual(old["data_time"], "2026-01-14")
        self.assertIsNone(self.md.read_cache(INDEX.asset_id, INDEX.metric)["data"]["currency"])
        self.clock.advance(400)
        new = self.md.refresh(FUND.asset_id, FUND.metric)["data"]
        self.assertEqual(new["data_time"], old["data_time"])
        self.assertNotEqual(new["fetched_at"], old["fetched_at"])
        self.assertEqual(new["value"], "1.23456789")

    def test_cache_hits_expiry_boundary_force_and_read_only(self):
        self.assertEqual(self.md.read_cache(STOCK.asset_id, STOCK.metric)["cache_status"], "missing")
        self.assertEqual(self.provider.calls, 0)
        first = self.md.query(STOCK.asset_id, STOCK.metric)
        self.assertEqual(first["data"]["expires_at"], "2026-10-26T00:05:00.000000Z")
        self.clock.advance(299)
        self.assertEqual(self.md.query(STOCK.asset_id, STOCK.metric)["refresh_status"], "not_needed")
        self.assertEqual(self.provider.calls, 1)
        self.clock.advance(1)
        self.assertEqual(self.md.read_cache(STOCK.asset_id, STOCK.metric)["cache_status"], "stale")
        self.assertEqual(self.provider.calls, 1)
        self.assertEqual(self.md.query(STOCK.asset_id, STOCK.metric)["data"]["revision"], 2)
        self.assertEqual(self.provider.calls, 2)
        self.assertEqual(self.md.refresh(STOCK.asset_id, STOCK.metric)["data"]["revision"], 3)
        self.assertEqual(self.provider.calls, 3)

    def test_failure_returns_fresh_or_stale_cache_and_no_fake_success(self):
        row = self.md.query(STOCK.asset_id, STOCK.metric)["data"]
        with patch.object(self.provider, "fetch", side_effect=OSError("虚构提供器不可用")):
            failed = self.md.refresh(STOCK.asset_id, STOCK.metric)
            self.assertEqual(failed["data"], row)
            self.assertEqual(failed["cache_status"], "fresh")
            self.assertEqual(failed["refresh_status"], "failed")
            self.assertEqual(failed["error"]["code"], "provider_error")
            self.clock.advance(300)
            stale = self.md.query(STOCK.asset_id, STOCK.metric)
            self.assertEqual(stale["cache_status"], "stale")
            self.assertEqual(stale["data"], row)
            missing = self.md.query(FUND.asset_id, FUND.metric)
            self.assertIsNone(missing["data"])
            self.assertEqual(missing["cache_status"], "missing")
            self.assertEqual(missing["refresh_status"], "failed")
        self.assertEqual(self.md.read_cache(STOCK.asset_id, STOCK.metric)["data"], row)

    def test_invalid_payload_never_overwrites_old_cache(self):
        row = self.md.query(STOCK.asset_id, STOCK.metric)["data"]
        invalid = [
            {"value": None}, {"value": 1.2}, {"value": True}, {"value": "NaN"},
            {"value": Decimal("Infinity")}, {"value": "1.123456789"}, {"value": "-1"},
            {"currency": "USD"}, {"unit": "points"}, {"provider_id": "unknown"},
            {"is_simulated": 1}, {"is_simulated": False}, {"asset_id": FUND.asset_id},
            {"metric": "nav"}, {"data_time": "2026-01-15T07:00:00"},
            {"data_time_kind": "date", "data_time": "20260115"},
        ]
        self.clock.advance(1)
        for changes in invalid:
            with self.subTest(changes=changes):
                self.provider.transform = lambda quote: {**quote, **changes}
                result = self.md.refresh(STOCK.asset_id, STOCK.metric)
                self.assertEqual(result["error"]["code"], "invalid_response")
                self.assertEqual(result["data"], row)
        for transform in (lambda q: {}, lambda q: {k: v for k, v in q.items() if k != "value"}, lambda q: []):
            self.provider.transform = transform
            self.assertEqual(self.md.refresh(STOCK.asset_id, STOCK.metric)["data"], row)

    def test_missing_value_is_unavailable_but_explicit_zero_is_valid(self):
        self.provider.transform = lambda quote: {**quote, "value": None}
        unavailable = self.md.query(STOCK.asset_id, STOCK.metric)
        self.assertIsNone(unavailable["data"])
        self.assertEqual(unavailable["error"]["code"], "invalid_response")
        with db.connection(self.database) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM md_cache").fetchone()[0], 0)
        self.provider.transform = lambda quote: {**quote, "value": "0.00000000"}
        self.assertEqual(self.md.query(STOCK.asset_id, STOCK.metric)["data"]["value"], "0.00000000")

    def test_unsupported_ids_metrics_and_invalid_configuration(self):
        for asset_id, metric in (("DEMO001", "price"), ("unknown", "price"), (STOCK.asset_id, "nav")):
            with self.assertRaises(UnsupportedAsset):
                self.md.query(asset_id, metric)
        self.assertEqual(self.provider.calls, 0)
        for ttl in (0, -1, True, 1.5, 86401):
            with self.assertRaises(ValidationError):
                MDService(self.database, ttl_seconds=ttl)
        configured = MDService.from_app(create_app({"DATABASE_PATH": self.database, "MD_CACHE_TTL_SECONDS": 1}), clock=self.clock)
        result = configured.query(STOCK.asset_id, STOCK.metric)
        self.assertEqual(result["data"]["expires_at"], "2026-10-26T00:00:01.000000Z")
        with self.assertRaises(ValidationError):
            MDService(self.database, clock=lambda: datetime(2026, 10, 26)).read_cache(STOCK.asset_id, STOCK.metric)

    def test_provider_and_simulation_flag_are_isolated_cache_dimensions(self):
        self.md.query(STOCK.asset_id, STOCK.metric)
        other = CountingProvider()
        other.provider_id = "other-test-provider"
        other.transform = lambda q: {**q, "value": "222.22222222"}
        different = MDService(self.database, provider=other, clock=self.clock)
        self.assertIsNone(different.read_cache(STOCK.asset_id, STOCK.metric)["data"])
        different.query(STOCK.asset_id, STOCK.metric)
        non_mock_flag = CountingProvider()  # 仅测试隔离契约，不调用真实接口。
        non_mock_flag.is_simulated = False
        non_mock_flag.transform = lambda q: {**q, "value": "333.33333333"}
        flag_service = MDService(self.database, provider=non_mock_flag, clock=self.clock)
        self.assertIsNone(flag_service.read_cache(STOCK.asset_id, STOCK.metric)["data"])
        self.assertEqual(flag_service.query(STOCK.asset_id, STOCK.metric)["data"]["value"], "333.33333333")
        self.assertEqual(different.read_cache(STOCK.asset_id, STOCK.metric)["data"]["value"], "222.22222222")
        self.assertEqual(self.md.read_cache(STOCK.asset_id, STOCK.metric)["data"]["value"], "123.45678901")
        with db.connection(self.database) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM md_cache").fetchone()[0], 3)

    def test_old_source_time_or_changed_date_semantics_do_not_overwrite(self):
        row = self.md.query(STOCK.asset_id, STOCK.metric)["data"]
        self.clock.advance(20)
        for changes in ({"data_time": "2026-01-14T00:00:00Z"},
                        {"data_time_kind": "date", "data_time": "2026-01-16"}):
            self.provider.transform = lambda q: {**q, **changes}
            result = self.md.refresh(STOCK.asset_id, STOCK.metric)
            self.assertEqual(result["error"]["code"], "older_data")
            self.assertEqual(result["data"], row)

    def test_overlapping_fetch_cannot_overwrite_first_completed_update(self):
        self.md.query(STOCK.asset_id, STOCK.metric)
        newer_provider = CountingProvider()
        newer_provider.transform = lambda q: {**q, "value": "999.12345678", "data_time": "2026-01-16T07:00:00Z"}
        newer = MDService(self.database, provider=newer_provider, clock=self.clock)

        def overlapping_fetch(quote):
            self.clock.advance(1)
            newer.refresh(STOCK.asset_id, STOCK.metric)
            return quote

        self.provider.transform = overlapping_fetch
        late = self.md.refresh(STOCK.asset_id, STOCK.metric)
        self.assertEqual(late["refresh_status"], "superseded")
        self.assertEqual(late["error"]["code"], "cache_changed")
        self.assertEqual(late["data"]["value"], "999.12345678")
        self.assertEqual(late["data"]["revision"], 2)

    def test_provider_runs_without_database_lock_and_ttl_starts_after_fetch(self):
        def fetch_with_database_access(quote):
            with db.connection(self.database, write=True) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM md_cache").fetchone()[0], 0)
            self.clock.advance(200)
            return {**quote, "data_time": "2026-01-15T15:00:00+08:00"}

        self.provider.transform = fetch_with_database_access
        result = self.md.query(STOCK.asset_id, STOCK.metric)
        self.assertEqual(result["data"]["data_time"], "2026-01-15T07:00:00.000000Z")
        self.assertEqual(result["data"]["fetched_at"], "2026-10-26T00:03:20.000000Z")
        self.assertEqual(result["data"]["expires_at"], "2026-10-26T00:08:20.000000Z")

    def test_clock_rollback_does_not_mark_a_refresh_successful(self):
        row = self.md.query(STOCK.asset_id, STOCK.metric)["data"]
        self.clock.advance(10)

        def clock_rolls_back(quote):
            self.clock.advance(-20)
            return quote

        self.provider.transform = clock_rolls_back
        failed = self.md.refresh(STOCK.asset_id, STOCK.metric)
        self.assertEqual(failed["error"]["code"], "clock_error")
        self.assertEqual(failed["data"], row)
        self.assertEqual(failed["cache_status"], "stale")
        self.provider.transform = lambda q: q
        rejected = self.md.refresh(STOCK.asset_id, STOCK.metric)
        self.assertEqual(rejected["error"]["code"], "older_data")
        self.assertEqual(rejected["data"], row)

    def test_failure_re_reads_a_concurrent_success_instead_of_returning_older_snapshot(self):
        self.md.query(STOCK.asset_id, STOCK.metric)
        newer_provider = CountingProvider()
        newer_provider.transform = lambda q: {**q, "value": "777.12345678"}
        newer = MDService(self.database, provider=newer_provider, clock=self.clock)

        def fails_after_other_update(quote):
            self.clock.advance(1)
            newer.refresh(STOCK.asset_id, STOCK.metric)
            raise OSError("旧请求失败")

        self.provider.transform = fails_after_other_update
        result = self.md.refresh(STOCK.asset_id, STOCK.metric)
        self.assertEqual(result["refresh_status"], "failed")
        self.assertEqual(result["error"]["code"], "provider_error")
        self.assertEqual(result["data"]["value"], "777.12345678")
        self.assertEqual(result["data"]["revision"], 2)

    def test_write_failure_rolls_back_asset_and_quote_together(self):
        original = db.connection

        @contextmanager
        def failing_connection(*args, **kwargs):
            with original(*args, **kwargs) as conn:
                if kwargs.get("write"):
                    conn.execute("CREATE TEMP TRIGGER fail_md BEFORE INSERT ON main.md_cache "
                                 "BEGIN SELECT RAISE(ABORT, 'injected MD failure'); END")
                yield conn

        with patch.object(db, "connection", failing_connection):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "injected MD"):
                self.md.query(STOCK.asset_id, STOCK.metric)
        with db.connection(self.database) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM md_assets").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM md_cache").fetchone()[0], 0)

    def test_md_does_not_change_sp_ca_or_seed_demo_behavior(self):
        seed_demo(self.database)
        before = sp_ca_snapshot(self.database)
        for asset in ASSETS:
            self.md.query(asset.asset_id, asset.metric)
            self.md.read_cache(asset.asset_id, asset.metric)
        self.assertFalse(seed_demo(self.database)["created"])
        self.assertEqual(sp_ca_snapshot(self.database), before)
        self.assertIsNone(self.initialize()["backup"])
        self.assertEqual(sp_ca_snapshot(self.database), before)

    def test_md_only_data_does_not_prevent_original_sp_ca_demo_seed(self):
        self.md.query(STOCK.asset_id, STOCK.metric)
        self.assertTrue(seed_demo(self.database)["created"])
        self.assertEqual(self.store.get_profile()["investment_budget"], "800.00")
        self.assertEqual(self.md.read_cache(STOCK.asset_id, STOCK.metric)["data"]["revision"], 1)

    def test_cli_show_is_read_only_refresh_is_explicit_and_reinitialize_keeps_cache(self):
        runner = self.app.test_cli_runner()
        with patch.object(MockProvider, "fetch", side_effect=AssertionError("md-show 不应获取行情")):
            result = runner.invoke(args=["md-show"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(all(r["cache_status"] == "missing" for r in json.loads(result.output)))
        refreshed = runner.invoke(args=["md-refresh"])
        self.assertEqual(refreshed.exit_code, 0, refreshed.output)
        self.assertEqual(len(json.loads(refreshed.output)), 3)
        with db.connection(self.database) as conn:
            before = list(conn.iterdump())
        self.initialize()
        shown = runner.invoke(args=["md-show"])
        self.assertEqual(shown.exit_code, 0, shown.output)
        rows = json.loads(shown.output)
        self.assertTrue(all(r["refresh_status"] == "not_requested" for r in rows))
        self.assertTrue(all(r["data"]["is_simulated"] for r in rows))
        with db.connection(self.database) as conn:
            self.assertEqual(list(conn.iterdump()), before)

    def test_whole_copy_reads_and_refreshes_its_own_database(self):
        self.md.query(STOCK.asset_id, STOCK.metric)
        copy = self.root / "MD copy with spaces"
        shutil.copytree(Config.PROJECT_ROOT / "app", copy / "app", ignore=shutil.ignore_patterns("__pycache__"))
        target = copy / "runtime/data/app.sqlite3"
        target.parent.mkdir(parents=True)
        shutil.copy2(self.database, target)
        (copy / "probe.py").write_text('''
from pathlib import Path
from datetime import datetime, timezone
from app import create_app
from app.md import MDService
from app.md_provider import ASSETS
app = create_app()
assert app.config['DATABASE_PATH'] == Path(__file__).resolve().parent / 'runtime/data/app.sqlite3'
md = MDService.from_app(app, clock=lambda: datetime(2026,10,26,tzinfo=timezone.utc))
stock = ASSETS[0]
assert md.read_cache(stock.asset_id, stock.metric)['data']['value'] == '123.45678901'
assert md.refresh(stock.asset_id, stock.metric)['data']['revision'] == 2
print('relocated MD database verified')
''', encoding="utf-8", newline="\n")
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        result = subprocess.run([sys.executable, "-B", str(copy / "probe.py")], cwd=self.root,
                                env=env, capture_output=True, text=True, check=True)
        self.assertIn("verified", result.stdout)
        self.assertEqual(self.md.read_cache(STOCK.asset_id, STOCK.metric)["data"]["revision"], 1)
