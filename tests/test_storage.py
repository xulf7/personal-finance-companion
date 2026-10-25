"""使用 runtime/tests 下的独立文件数据库，不触碰开发数据。"""

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, localcontext
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from app import create_app, db
from app.config import Config
from app.demo import budget, seed_demo, summary
from app.errors import (
    InitializationRequired, InvalidState, NotFound, SchemaError, ValidationError, VersionConflict,
)
from app.storage import Store


class StorageCase(unittest.TestCase):
    def setUp(self):
        Config.TEST_DIR.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="t02-", dir=Config.TEST_DIR)
        self.root = Path(self.temp.name).resolve()
        self.assertTrue(self.root.is_relative_to(Config.TEST_DIR.resolve()))
        self.addCleanup(self.temp.cleanup)
        self.database = self.root / "data/app.sqlite3"
        self.backups = self.root / "backups"
        self.app = create_app({
            "TESTING": True, "RUNTIME_DIR": self.root, "DATABASE_PATH": self.database,
            "DATA_DIR": self.root / "data", "BACKUP_DIR": self.backups,
            "TEST_DIR": self.root / "tests", "LOG_DIR": self.root / "logs",
            "IMPORT_DIR": self.root / "imports",
        })
        self.store = Store.from_app(self.app)

    def initialize(self):
        return db.initialize(self.database, self.backups)

    def user_message(self, role="user"):
        session = self.store.create_conversation("虚构测试会话")
        message = self.store.add_message(session["id"], role, "虚构测试消息")
        return session, message

    def candidate(self, value="800", base_version=None):
        session, message = self.user_message()
        return self.store.create_change(
            session["id"], message["id"], budget(value),
            base_version=self.store.get_profile()["version"] if base_version is None else base_version,
        )


class InitializationTests(StorageCase):
    def test_factory_health_and_uninitialized_service_do_not_create_database(self):
        with patch("sqlite3.connect", side_effect=AssertionError("不应连接数据库")):
            app = create_app({"DATABASE_PATH": self.database})
            self.assertEqual(app.test_client().get("/").status_code, 200)
            self.assertEqual(app.test_client().get("/health").json, {"status": "ok"})
        with self.assertRaisesRegex(InitializationRequired, "init-db"):
            self.store.get_profile()
        self.assertFalse(self.database.parent.exists())
        self.assertFalse(self.backups.exists())

    def test_reinitialize_preserves_data_and_connections_are_closed(self):
        self.assertEqual(self.initialize(), {"version": 1, "backup": None})
        self.store.edit_profile(budget("500"), expected_version=0)
        session, message = self.user_message()
        self.assertEqual(self.initialize(), {"version": 1, "backup": None})
        reopened = Store(self.database)
        self.assertEqual(reopened.get_profile()["investment_budget"], "500.00")
        self.assertEqual(reopened.get_message(message["id"])["conversation_id"], session["id"])
        with db.connection(self.database) as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
        self.assertFalse(self.backups.exists())

    def test_foreign_keys_are_enforced(self):
        self.initialize()
        with self.assertRaises(sqlite3.IntegrityError), db.connection(self.database, write=True) as conn:
            conn.execute("INSERT INTO overview_sources VALUES ('missing', 'missing')")

    def test_unknown_and_future_schemas_are_preserved(self):
        self.database.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.database)) as conn:
            conn.execute("CREATE TABLE precious (value TEXT)")
            conn.execute("INSERT INTO precious VALUES ('keep me')")
            conn.commit()
        original = hashlib.sha256(self.database.read_bytes()).hexdigest()
        with self.assertRaisesRegex(SchemaError, "已有表结构"):
            self.initialize()
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), original)
        with closing(sqlite3.connect(self.database)) as conn:
            conn.execute("PRAGMA user_version=99")
        with self.assertRaisesRegex(SchemaError, "高于"):
            self.initialize()
        with self.assertRaisesRegex(SchemaError, "高于"):
            self.store.get_profile()
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute("SELECT value FROM precious").fetchone()[0], "keep me")
        self.assertFalse(self.backups.exists())

    def test_schema_or_migration_history_tampering_is_rejected(self):
        self.initialize()
        with db.connection(self.database, write=True) as conn:
            conn.execute("UPDATE schema_migrations SET sha256='wrong'")
        with self.assertRaisesRegex(SchemaError, "校验和"):
            self.initialize()
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM profile").fetchone()[0], 1)

    def test_crlf_checkout_does_not_invalidate_migration_checksum(self):
        self.initialize()
        directory = self.root / "crlf-migrations"
        directory.mkdir()
        source = db.MIGRATIONS_DIR / "001_initial.sql"
        (directory / source.name).write_bytes(source.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8"))
        self.assertIsNone(db.initialize(self.database, self.backups, migrations_dir=directory)["backup"])

    def migration_directory(self, sql):
        directory = self.root / "migrations"
        directory.mkdir()
        shutil.copy2(db.MIGRATIONS_DIR / "001_initial.sql", directory / "001_initial.sql")
        (directory / "002_test.sql").write_text(sql, encoding="utf-8", newline="\n")
        return directory

    def test_upgrade_has_valid_backup_and_keeps_existing_business_data(self):
        self.initialize()
        self.store.edit_profile(budget("500"), expected_version=0)
        directory = self.migration_directory("ALTER TABLE profile ADD COLUMN note TEXT;\n")
        result = db.initialize(self.database, self.backups, migrations_dir=directory)
        self.assertEqual(result["version"], 2)
        with closing(sqlite3.connect(result["backup"])) as backup:
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(backup.execute("SELECT investment_budget FROM profile").fetchone()[0], "500.00")
            self.assertNotIn("note", [r[1] for r in backup.execute("PRAGMA table_info(profile)")])
        with patch.object(db, "MIGRATIONS_DIR", directory):
            self.assertEqual(self.store.get_profile()["investment_budget"], "500.00")
            self.assertIsNone(db.initialize(self.database, self.backups)["backup"])
        self.assertEqual(len(list(self.backups.iterdir())), 1)

    def test_failed_upgrade_rolls_back_ddl_data_and_version(self):
        self.initialize()
        self.store.edit_profile(budget("500"), expected_version=0)
        directory = self.migration_directory(
            "CREATE TABLE partial_write (value TEXT);\n"
            "UPDATE profile SET investment_budget='999.00';\n"
            "INSERT INTO nonexistent VALUES (1);\n"
        )
        with self.assertRaisesRegex(SchemaError, "回滚"):
            db.initialize(self.database, self.backups, migrations_dir=directory)
        self.assertEqual(self.store.get_profile()["investment_budget"], "500.00")
        with db.connection(self.database) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 1)
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='partial_write'").fetchone())
        with closing(sqlite3.connect(next(self.backups.iterdir()))) as backup:
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")


class ProfileAndChangesTests(StorageCase):
    def setUp(self):
        super().setUp()
        self.initialize()

    def test_precise_money_quantities_unknown_and_child_versions(self):
        self.assertIsNone(self.store.get_profile()["cash"])
        profile = self.store.edit_profile([
            {"op": "set_finances", "currency": "CNY", "values": {
                "cash": Decimal("0.1") + Decimal("0.2"), "debt": 0, "income": None,
            }},
            {"op": "set_goal", "goal": {"id": "emergency", "name": "虚构储蓄目标",
             "target_amount": "12000.10", "currency": "CNY", "target_date": "2027-12-31"}},
            {"op": "set_holding", "holding": {"id": "demo", "asset_code": "FICTIONAL_ASSET",
             "quantity": "0.12345678", "unit_cost": "1.23", "currency": "USD"}},
        ], expected_version=0)
        self.assertEqual(profile["version"], 1)
        self.assertEqual(profile["cash"], "0.30")
        self.assertEqual(profile["debt"], "0.00")
        self.assertIsNone(profile["income"])
        self.assertEqual(profile["holdings"][0]["quantity"], "0.12345678")
        self.assertEqual(profile["holdings"][0]["currency"], "USD")
        child_only = self.store.edit_profile([{"op": "remove_goal", "id": "emergency"}], expected_version=1)
        self.assertEqual(child_only["version"], 2)
        self.assertNotEqual(profile["updated_at"], child_only["updated_at"])
        unchanged = self.store.edit_profile([
            {"op": "set_finances", "currency": "CNY", "values": {"cash": "0.3000"}}
        ], expected_version=2)
        self.assertEqual(unchanged, child_only)
        with localcontext() as context:
            context.prec = 6
            precise = self.store.edit_profile([
                {"op": "set_finances", "currency": "CNY", "values": {"cash": "12345678.91"}}
            ], expected_version=2)
        self.assertEqual(precise["cash"], "12345678.91")

    def test_bad_precision_types_operations_and_currency_do_not_write(self):
        for value in (0.1, True, "NaN", "-1", "0.001", "1000000000000000000", Decimal("Infinity")):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.store.edit_profile(budget(value), expected_version=0)
        for changes in ([], [{"op": "DROP TABLE profile"}], [{"op": "set_risk", "value": "unknown"}],
                        [{"op": "set_finances", "currency": "CNY", "values": {"arbitrary_column": "1"}}]):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.store.edit_profile(changes, expected_version=0)
        self.assertEqual(self.store.get_profile()["version"], 0)
        self.store.edit_profile(budget("500"), expected_version=0)
        with self.assertRaises(ValidationError):
            self.store.edit_profile([
                {"op": "set_risk", "value": "low"},
                {"op": "set_finances", "currency": "USD", "values": {"cash": "1"}},
            ], expected_version=1)
        self.assertIsNone(self.store.get_profile()["risk_preference"])

    def test_pending_confirm_and_repeat_confirm_are_idempotent(self):
        self.store.edit_profile(budget("500"), expected_version=0)
        candidate = self.candidate()
        self.assertEqual(candidate["status"], "pending")
        self.assertIsNone(candidate["processed_at"])
        self.assertEqual(self.store.get_profile()["investment_budget"], "500.00")
        confirmed = self.store.confirm_change(candidate["id"])
        self.assertEqual(confirmed["status"], "confirmed")
        self.assertEqual(confirmed["applied_version"], 2)
        self.assertIsNotNone(confirmed["processed_at"])
        self.assertEqual(self.store.get_profile()["investment_budget"], "800.00")
        self.assertEqual(self.store.confirm_change(candidate["id"]), confirmed)
        self.assertEqual(self.store.get_profile()["version"], 2)
        with self.assertRaises(InvalidState):
            self.store.reject_change(candidate["id"])

    def test_reject_does_not_change_sp_and_cannot_confirm(self):
        candidate = self.candidate()
        before = self.store.get_profile()
        rejected = self.store.reject_change(candidate["id"])
        self.assertEqual(self.store.get_profile(), before)
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(self.store.reject_change(candidate["id"]), rejected)
        self.assertEqual(len(self.store.list_changes("rejected")), 1)
        with self.assertRaises(InvalidState):
            self.store.confirm_change(candidate["id"])

    def test_stale_candidate_and_direct_edit_conflict(self):
        first, second = self.candidate("500"), self.candidate("800")
        self.store.confirm_change(first["id"])
        with self.assertRaises(VersionConflict):
            self.store.confirm_change(second["id"])
        with self.assertRaises(VersionConflict):
            self.store.edit_profile(budget("900"), expected_version=0)
        self.assertEqual(self.store.get_change(second["id"])["status"], "pending")
        self.assertEqual(self.store.get_profile()["investment_budget"], "500.00")

    def test_concurrent_confirmations_do_not_overwrite_newer_profile(self):
        first, second = self.candidate("500"), self.candidate("800")

        def confirm(change):
            try:
                return Store(self.database).confirm_change(change["id"])["status"]
            except VersionConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(confirm, (first, second)))
        self.assertCountEqual(results, ["confirmed", "conflict"])
        self.assertEqual(self.store.get_profile()["version"], 1)
        self.assertEqual(len(self.store.list_changes("pending")), 1)

    def test_failure_at_candidate_status_update_rolls_back_profile_and_version(self):
        candidate = self.candidate()
        before = self.store.get_profile()
        original_apply = self.store._apply

        def inject_failure(conn, changes, expected_version):
            result = original_apply(conn, changes, expected_version)
            conn.execute("CREATE TEMP TRIGGER fail_confirm BEFORE UPDATE ON main.profile_changes "
                         "BEGIN SELECT RAISE(ABORT, 'injected test failure'); END")
            return result

        with patch.object(self.store, "_apply", side_effect=inject_failure):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "injected"):
                self.store.confirm_change(candidate["id"])
        self.assertEqual(self.store.get_profile(), before)
        self.assertEqual(self.store.get_change(candidate["id"]), candidate)
        self.assertEqual(self.store.confirm_change(candidate["id"])["status"], "confirmed")

    def test_partial_operations_roll_back_and_source_must_be_user_in_session(self):
        session, message = self.user_message()
        candidate = self.store.create_change(session["id"], message["id"],
            budget("800") + [{"op": "remove_goal", "id": "missing"}], base_version=0)
        with self.assertRaises(NotFound):
            self.store.confirm_change(candidate["id"])
        self.assertEqual(self.store.get_profile()["version"], 0)
        self.assertIsNone(self.store.get_profile()["investment_budget"])
        other, assistant = self.user_message("assistant")
        for cid, mid in ((session["id"], assistant["id"]), (other["id"], assistant["id"]),
                         (other["id"], message["id"])):
            with self.assertRaises(ValidationError):
                self.store.create_change(cid, mid, budget("1"), base_version=0)
        with self.assertRaises(NotFound):
            self.store.create_change(session["id"], "missing", budget("1"), base_version=0)


class ArchiveAndDemoTests(StorageCase):
    def setUp(self):
        super().setUp()
        self.initialize()

    def test_order_summary_range_versions_sources_and_sp_isolation(self):
        session, first = self.user_message()
        last = self.store.add_message(session["id"], "assistant", "仅讨论假设，不修改 SP")
        self.assertEqual([r["sequence"] for r in self.store.list_messages(session["id"])], [1, 2])
        self.assertEqual(self.store.get_conversation(session["id"]), session)
        self.assertEqual(self.store.list_conversations(), [session])
        before = self.store.get_profile()
        content = summary(plans=["可能增加预算"], confirmed=["尚未确认任何预算修改"])
        first_summary = self.store.save_summary(session["id"], first["id"], last["id"], content)
        self.assertEqual((first_summary["first_sequence"], first_summary["last_sequence"]), (1, 2))
        self.assertEqual(self.store.save_summary(session["id"], first["id"], last["id"], content), first_summary)
        second = self.store.save_summary(session["id"], first["id"], last["id"], summary(reasons=["修订原因"]))
        self.assertEqual(second["version"], 2)
        self.assertEqual(self.store.get_summary(first_summary["id"]), first_summary)
        self.assertEqual(len(self.store.list_summaries(session["id"])), 2)
        overview = self.store.save_overview(content, [first_summary["id"], second["id"]])
        self.assertEqual(self.store.save_overview(content, [second["id"], first_summary["id"]]), overview)
        self.assertEqual(self.store.get_overview(), overview)
        newer = self.store.save_overview(summary(todos=["待核对"]), [second["id"]])
        self.assertEqual(newer["version"], 2)
        self.assertEqual(self.store.get_overview(1), overview)
        self.assertEqual(self.store.get_profile(), before)
        self.assertEqual(self.store.list_messages(session["id"]), [first, last])

    def test_invalid_summary_ranges_structure_and_overview_sources(self):
        session, first = self.user_message()
        last = self.store.add_message(session["id"], "user", "第二条")
        _, other = self.user_message()
        for start, end in ((last["id"], first["id"]), (first["id"], other["id"])):
            with self.assertRaises(ValidationError):
                self.store.save_summary(session["id"], start, end, summary())
        for invalid in ({"freeform": "any"}, summary(facts="not a list"), summary(confirmed=[123])):
            with self.assertRaises(ValidationError):
                self.store.save_summary(session["id"], first["id"], last["id"], invalid)
        with self.assertRaises(NotFound):
            self.store.save_overview(summary(), ["missing"])
        with self.assertRaises(ValidationError):
            self.store.save_overview(summary(), [])
        self.assertIsNone(self.store.get_overview())
        self.assertEqual(self.store.list_summaries(session["id"]), [])

    def test_demo_cli_repeat_and_preservation(self):
        runner = self.app.test_cli_runner()
        self.assertEqual(runner.invoke(args=["init-db"]).exit_code, 0)
        result = runner.invoke(args=["seed-demo"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("虚构", result.output)
        self.assertEqual(self.store.get_profile()["investment_budget"], "800.00")
        self.assertEqual(self.store.get_profile()["version"], 2)
        self.assertEqual(len(self.store.list_conversations()), 1)
        session = self.store.list_conversations()[0]
        self.assertEqual(len(self.store.list_messages(session["id"])), 4)
        self.assertEqual(len(self.store.list_summaries(session["id"])), 2)
        self.assertCountEqual([c["status"] for c in self.store.list_changes()], ["confirmed", "pending"])
        self.assertEqual(len(self.store.get_overview()["summary_ids"]), 2)
        with db.connection(self.database) as conn:
            before = list(conn.iterdump())
        self.assertEqual(runner.invoke(args=["seed-demo"]).exit_code, 0)
        self.initialize()
        with db.connection(self.database) as conn:
            self.assertEqual(list(conn.iterdump()), before)
        self.store.edit_profile(budget("900"), expected_version=2)
        self.assertFalse(seed_demo(self.database)["created"])
        self.assertEqual(self.store.get_profile()["investment_budget"], "900.00")

    def test_demo_refuses_existing_data_and_failure_is_atomic(self):
        with patch.object(Store, "save_overview", side_effect=RuntimeError("demo failure")):
            with self.assertRaises(RuntimeError):
                seed_demo(self.database)
        self.assertEqual(self.store.get_profile()["version"], 0)
        self.assertEqual(self.store.list_conversations(), [])
        self.store.create_conversation("必须保留的用户会话")
        result = self.app.test_cli_runner().invoke(args=["seed-demo"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("已有其他业务数据", result.output)
        self.assertEqual(len(self.store.list_conversations()), 1)
        self.assertEqual(self.store.get_profile()["version"], 0)

    def test_relocated_project_reads_and_edits_its_own_database(self):
        seed_demo(self.database)
        copy = self.root / "relocated project"
        shutil.copytree(Config.PROJECT_ROOT / "app", copy / "app",
                        ignore=shutil.ignore_patterns("__pycache__"))
        target = copy / "runtime/data/app.sqlite3"
        target.parent.mkdir(parents=True)
        shutil.copy2(self.database, target)
        (copy / "probe.py").write_text('''
import json
from pathlib import Path
from app import create_app
from app.storage import Store
from app.demo import budget
app = create_app()
root = Path(__file__).resolve().parent
assert app.config['DATABASE_PATH'] == root / 'runtime/data/app.sqlite3'
store = Store.from_app(app)
assert store.get_profile()['investment_budget'] == '800.00'
assert len(store.list_conversations()) == 1
store.edit_profile(budget('900'), expected_version=2)
print(json.dumps({'budget': store.get_profile()['investment_budget']}))
''', encoding="utf-8", newline="\n")
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        result = subprocess.run([sys.executable, "-B", str(copy / "probe.py")],
                                cwd=self.root, env=env, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["budget"], "900.00")
        self.assertEqual(self.store.get_profile()["investment_budget"], "800.00")


if __name__ == "__main__":
    unittest.main()
