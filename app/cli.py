"""只在成员显式执行命令时初始化或填充。"""

from .db import initialize
from .demo import seed_demo
from .errors import StorageError


def register_cli(app):
    @app.cli.command("init-db")
    def init_db_command():
        """创建空库，或备份后应用待执行的迁移；不清空业务数据。"""
        try:
            result = initialize(app.config["DATABASE_PATH"], app.config["BACKUP_DIR"])
        except StorageError as exc:
            raise SystemExit(f"错误：{exc}") from exc
        print(f"数据库已就绪，版本 {result['version']}。")
        if result["backup"]:
            print(f"升级前备份：{result['backup']}")

    @app.cli.command("seed-demo")
    def seed_demo_command():
        """仅向空业务库写入虚构演示数据；重复执行不新增。"""
        try:
            result = seed_demo(app.config["DATABASE_PATH"])
        except StorageError as exc:
            raise SystemExit(f"错误：{exc}") from exc
        print("已生成虚构演示：500 → 提出 800 → 确认后 800；另有待确认 1000。"
              if result["created"] else "虚构演示已存在，本次未修改任何数据。")
