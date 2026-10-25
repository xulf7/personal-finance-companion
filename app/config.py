"""集中定义路径；所有位置均由代码所在目录推导。"""

from pathlib import Path


class Config:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    RUNTIME_DIR = PROJECT_ROOT / "runtime"
    DATA_DIR = RUNTIME_DIR / "data"
    LOG_DIR = RUNTIME_DIR / "logs"
    IMPORT_DIR = RUNTIME_DIR / "imports"
    BACKUP_DIR = RUNTIME_DIR / "backups"
    TEST_DIR = RUNTIME_DIR / "tests"
    DATABASE_PATH = DATA_DIR / "app.sqlite3"
