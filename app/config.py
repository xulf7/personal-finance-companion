"""集中定义路径；所有位置均由代码所在目录推导。"""

from pathlib import Path
import os


class Config:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    RUNTIME_DIR = PROJECT_ROOT / "runtime"
    DATA_DIR = RUNTIME_DIR / "data"
    LOG_DIR = RUNTIME_DIR / "logs"
    IMPORT_DIR = RUNTIME_DIR / "imports"
    BACKUP_DIR = RUNTIME_DIR / "backups"
    TEST_DIR = RUNTIME_DIR / "tests"
    DATABASE_PATH = DATA_DIR / "app.sqlite3"
    MD_CACHE_TTL_SECONDS = 300
    MD_PROVIDER_ID = "mock-fixed-v1"
    CHAT_RECENT_MESSAGES = 12
    CHAT_CONTEXT_CHARS = 16000
    CHAT_MAX_TOKENS = 1024
    CHAT_TIMEOUT_SECONDS = 60

    @staticmethod
    def provider_id():
        """每次创建应用/独立服务时读取当前进程环境，不自动加载 .env。"""
        return os.environ.get("MD_PROVIDER_ID", Config.MD_PROVIDER_ID)
