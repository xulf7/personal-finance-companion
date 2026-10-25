"""基础功能与可迁移性检查：python -B -m unittest discover -s tests -v。"""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.request import ProxyHandler, build_opener

from app import create_app
from app.config import Config


class AppTests(unittest.TestCase):
    def test_home_and_health_without_keys_or_network(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "socket.socket.connect", side_effect=AssertionError("禁止外部网络")
        ), patch("socket.create_connection", side_effect=AssertionError("禁止外部网络")):
            app = create_app()
            app.config["TESTING"] = True
            client = app.test_client()
            home = client.get("/")
            self.assertEqual(home.status_code, 200)
            self.assertEqual(home.mimetype, "text/html")
            text = home.get_data(as_text=True)
            for label in ("Personal Finance Companion", "SP", "CA Overview", "MD", "尚未实现"):
                self.assertIn(label, text)
            with client.get("/static/style.css") as style:
                self.assertEqual(style.status_code, 200)
            health = client.get("/health")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.mimetype, "application/json")
            self.assertEqual(health.get_json(), {"status": "ok"})

    def test_paths_are_under_the_code_root(self):
        root = Path(__file__).resolve().parent.parent
        config = create_app().config
        self.assertEqual(config["PROJECT_ROOT"], root)
        self.assertEqual(config["RUNTIME_DIR"], root / "runtime")
        for key, folder in (
            ("DATA_DIR", "data"), ("LOG_DIR", "logs"), ("IMPORT_DIR", "imports"),
            ("BACKUP_DIR", "backups"), ("TEST_DIR", "tests"),
        ):
            self.assertEqual(config[key], root / "runtime" / folder)
        self.assertEqual(config["DATABASE_PATH"], root / "runtime/data/app.sqlite3")
        self.assertEqual(Path(create_app().instance_path), config["RUNTIME_DIR"])


class PortabilityTests(unittest.TestCase):
    def setUp(self):
        Config.TEST_DIR.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="t01-", dir=Config.TEST_DIR)
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)

    def check_server(self, root, cwd):
        # 只分配本机回环端口，不调用外部 API；日志和临时数据留在 runtime/tests。
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.pop("PYTHONPATH", None)
        opener = build_opener(ProxyHandler({}))
        with (self.work / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, "-B", str(root / "run.py"), "--port", str(port)],
                cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 10
                while True:
                    if process.poll() is not None:
                        self.fail("入口提前退出，请检查启动代码")
                    try:
                        with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                            self.assertEqual(response.status, 200)
                            self.assertEqual(json.load(response), {"status": "ok"})
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            self.fail("等待本地服务超时")
                        time.sleep(0.05)
                with opener.open(f"http://127.0.0.1:{port}/", timeout=2) as response:
                    self.assertIn("Personal Finance Companion", response.read().decode("utf-8"))
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    def test_entry_from_project_root(self):
        self.check_server(Config.PROJECT_ROOT, Config.PROJECT_ROOT)

    def test_entry_from_different_working_directory(self):
        self.check_server(Config.PROJECT_ROOT, self.work)
        self.assertFalse((self.work / "runtime").exists())

    def test_relocated_copy_uses_its_own_paths(self):
        copy = self.work / "迁移副本 with spaces"
        copy.mkdir()
        shutil.copytree(
            Config.PROJECT_ROOT / "app", copy / "app",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        shutil.copy2(Config.PROJECT_ROOT / "run.py", copy / "run.py")
        # 错误的 .env 不应被入口自动加载，也不需要 python-dotenv。
        (copy / ".env").write_text("FLASK_DEBUG=1\n", encoding="utf-8")
        probe = """
import json
from pathlib import Path
from unittest.mock import patch
from app import create_app
root = Path(__file__).resolve().parent
with patch('sqlite3.connect', side_effect=AssertionError('不应创建数据库')):
    app = create_app()
    assert app.test_client().get('/health').json == {'status': 'ok'}
assert app.config['PROJECT_ROOT'] == root
assert Path(app.instance_path) == root / 'runtime'
assert app.config['DATABASE_PATH'] == root / 'runtime/data/app.sqlite3'
assert not (root / 'runtime').exists()
app.config['TEST_DIR'].mkdir(parents=True)
(app.config['TEST_DIR'] / 'probe.txt').write_text('local', encoding='utf-8')
print(json.dumps({'root': str(root), 'runtime': str(app.config['RUNTIME_DIR'])}))
"""
        (copy / "probe.py").write_text(probe, encoding="utf-8")
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        result = subprocess.run(
            [sys.executable, "-B", str(copy / "probe.py")], cwd=self.work,
            env=env, capture_output=True, text=True, check=True,
        )
        paths = json.loads(result.stdout)
        self.assertEqual(Path(paths["root"]), copy)
        self.assertEqual(Path(paths["runtime"]), copy / "runtime")
        self.assertEqual((copy / "runtime/tests/probe.txt").read_text(), "local")
        self.check_server(copy, self.work)
        self.assertFalse((copy / "runtime/data/app.sqlite3").exists())
        self.assertFalse((self.work / "runtime").exists())


if __name__ == "__main__":
    unittest.main()
