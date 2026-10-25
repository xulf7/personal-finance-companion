"""只在成员显式执行命令时初始化或填充。"""

import json

from .db import initialize
from .demo import seed_demo
from .errors import StorageError
from .md import MDService


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

    def market_command(refresh):
        try:
            service = MDService.from_app(app)
            results = []
            for asset in service.list_assets():
                metric = asset["metrics"][0]["metric"]
                result = (service.refresh if refresh else service.read_cache)(asset["asset_id"], metric)
                results.append({"asset": asset, **result})
            print(json.dumps(results, ensure_ascii=False, indent=2))
            if refresh and any(result["error"] for result in results):
                raise SystemExit("部分模拟行情刷新未成功，详情见 error；已有缓存仍保留。")
        except StorageError as exc:
            raise SystemExit(f"错误：{exc}") from exc

    @app.cli.command("md-refresh")
    def md_refresh_command():
        """显式刷新三类固定虚构行情；不调用真实接口。"""
        market_command(True)

    @app.cli.command("md-show")
    def md_show_command():
        """查看支持的资产和已有缓存，不调用提供器。"""
        market_command(False)
