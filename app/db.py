"""显式迁移与短连接管理；导入和创建 Flask 应用均不连接数据库。"""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from .config import Config
from .errors import InitializationRequired, SchemaError, StorageError

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
INIT_HINT = "数据库尚未初始化，请执行 python -m flask --app app:create_app init-db"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def local_path(value):
    path = Path(value)
    if not path.is_absolute():
        path = Config.PROJECT_ROOT / path
    path = path.resolve()
    if not path.is_relative_to(Config.PROJECT_ROOT.resolve()):
        raise StorageError("持久化路径必须位于项目文件夹内")
    return path


def migration_files(directory=None):
    files = sorted((directory or MIGRATIONS_DIR).glob("*.sql"))
    for version, path in enumerate(files, 1):
        if not re.fullmatch(rf"{version:03d}_[a-z0-9_]+\.sql", path.name):
            raise SchemaError("迁移文件必须从 001 开始连续编号，如 001_initial.sql")
    if not files:
        raise SchemaError("未找到 SQL 迁移文件")
    return files


def _checksum(file):
    # Git 在不同平台可能转换换行；只忽略 CRLF/LF 差异，不忽略内容修改。
    return hashlib.sha256(file.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def _open(path, *, create=False):
    connection = sqlite3.connect(
        f"{path.as_uri()}?mode={'rwc' if create else 'rw'}", uri=True,
        timeout=5, isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _schema(connection):
    return {
        (row["type"], row["name"]): " ".join((row["sql"] or "").split())
        for row in connection.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        )
    }


def _execute_sql(connection, sql):
    # 不用 executescript：它会隐式提交已有事务，破坏迁移失败时的回滚。
    statement = ""
    for line in sql.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise SchemaError("SQL 迁移文件末尾存在未完成语句")


def _validate(connection, files):
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version > len(files):
        raise SchemaError(f"数据库版本 {version} 高于代码支持版本 {len(files)}，不能降级")
    if version == 0:
        if _schema(connection):
            raise SchemaError("发现无版本记录的已有表结构；已保留数据，请人工核对后编写迁移")
        return version
    with closing(sqlite3.connect(":memory:")) as expected:
        expected.row_factory = sqlite3.Row
        expected.execute("PRAGMA foreign_keys = ON")
        for file in files[:version]:
            _execute_sql(expected, file.read_text(encoding="utf-8"))
        if _schema(connection) != _schema(expected):
            raise SchemaError("已有表结构与版本记录不匹配；已保留数据，请人工核对")
    history = connection.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall()
    wanted = [(i, p.name, _checksum(p))
              for i, p in enumerate(files[:version], 1)]
    if [(r["version"], r["filename"], r["sha256"]) for r in history] != wanted:
        raise SchemaError("迁移记录或 SQL 校验和不匹配；不要修改已应用的迁移文件")
    if connection.execute("PRAGMA foreign_key_check").fetchone():
        raise SchemaError("已有数据库含无效外键，已保留数据，请人工检查")
    return version


@contextmanager
def connection(database_path=Config.DATABASE_PATH, *, write=False):
    path = local_path(database_path)
    if not path.is_file():
        raise InitializationRequired(INIT_HINT)
    try:
        conn = _open(path)
    except sqlite3.DatabaseError as exc:
        raise SchemaError(f"无法读取数据库：{exc}") from exc
    try:
        conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        version = _validate(conn, migration_files())
        if version == 0:
            raise InitializationRequired(INIT_HINT)
        if version < len(migration_files()):
            raise InitializationRequired("数据库需要升级；" + INIT_HINT)
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize(database_path=Config.DATABASE_PATH, backup_dir=Config.BACKUP_DIR, *, migrations_dir=None):
    """返回版本与本次备份路径；遇到未知结构绝不删库重建。"""
    path, backups = local_path(database_path), local_path(backup_dir)
    files = migration_files(migrations_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    try:
        with closing(_open(path, create=True)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = _validate(conn, files)
                if current == len(files):
                    conn.commit()
                    return {"version": current, "backup": None}
                if current:
                    backups.mkdir(parents=True, exist_ok=True)
                    backup = backups / f"{path.stem}-v{current}-{uuid4().hex}.sqlite3"
                    # 独立连接读取已提交快照；主连接已持写锁，防止并发写入。
                    with closing(_open(path)) as source, closing(_open(backup, create=True)) as target:
                        source.backup(target)
                        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise SchemaError("升级前备份完整性检查失败，停止迁移")
                for version in range(current + 1, len(files) + 1):
                    file = files[version - 1]
                    _execute_sql(conn, file.read_text(encoding="utf-8"))
                    conn.execute(
                        "INSERT INTO schema_migrations VALUES (?, ?, ?, ?)",
                        (version, file.name, _checksum(file), now()),
                    )
                    conn.execute(f"PRAGMA user_version = {version}")
                _validate(conn, files)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
    except (sqlite3.DatabaseError, SchemaError) as exc:
        detail = f"；升级前备份：{backup}" if backup else ""
        raise SchemaError(f"初始化/迁移失败，事务已回滚：{exc}{detail}") from exc
    return {"version": len(files), "backup": backup}
