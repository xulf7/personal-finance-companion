"""规范化行情与当前缓存。提供器调用期间没有打开的数据库事务。"""

from datetime import date, datetime, timedelta, timezone

from . import db
from .config import Config
from .errors import SchemaError, ValidationError
from .md_provider import ASSETS, MockProvider, Provider
from .validation import decimal_text, exact_keys, text

QUOTE_FIELDS = ("asset_id", "metric", "value", "currency", "unit", "provider_id",
                "is_simulated", "data_time_kind", "data_time")


class UnsupportedAsset(ValidationError):
    pass


def utc_now():
    return datetime.now(timezone.utc)


def utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("时钟/日期时间必须明确包含时区")
    return value.astimezone(timezone.utc)


def timestamp(value):
    return utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def normalize_quote(payload, asset, provider_id, is_simulated):
    exact_keys(payload, QUOTE_FIELDS, "规范化行情")
    expected = {
        "asset_id": asset.asset_id, "metric": asset.metric, "unit": asset.unit,
        "currency": asset.currency, "provider_id": provider_id, "is_simulated": is_simulated,
    }
    if type(payload["is_simulated"]) is not bool or any(payload[k] != v for k, v in expected.items()):
        raise ValidationError("行情资产、指标、单位、币种或来源与请求不一致")
    value = decimal_text(payload["value"], places=8)
    if value is None:
        raise ValidationError("行情数值缺失，不能用零替代")
    source_time = payload["data_time"]
    try:
        if payload["data_time_kind"] == "date":
            if not isinstance(source_time, str) or date.fromisoformat(source_time).isoformat() != source_time:
                raise ValueError()
        elif payload["data_time_kind"] == "datetime":
            if not isinstance(source_time, str) or "T" not in source_time:
                raise ValueError()
            source_time = timestamp(datetime.fromisoformat(source_time.replace("Z", "+00:00")))
        else:
            raise ValueError()
    except (TypeError, ValueError) as exc:
        raise ValidationError("数据时间须为 YYYY-MM-DD 日期或包含时区的 ISO 8601 日期时间") from exc
    return {**payload, "value": value, "data_time": source_time}


class MDService:
    def __init__(self, database_path=Config.DATABASE_PATH, *, provider: Provider | None = None,
                 clock=utc_now, ttl_seconds=Config.MD_CACHE_TTL_SECONDS):
        self.database_path = db.local_path(database_path)
        self.provider = provider if provider is not None else MockProvider()
        self.provider_id = text(self.provider.provider_id, "提供器标识", 100)
        if type(self.provider.is_simulated) is not bool:
            raise ValidationError("提供器必须明确声明布尔型 is_simulated")
        self.is_simulated = self.provider.is_simulated
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 86400:
            raise ValidationError("缓存有效期必须为 1–86400 秒的整数")
        self.ttl_seconds = ttl_seconds
        self.clock = clock

    @classmethod
    def from_app(cls, app, **kwargs):
        kwargs.setdefault("ttl_seconds", app.config["MD_CACHE_TTL_SECONDS"])
        return cls(app.config["DATABASE_PATH"], **kwargs)

    @staticmethod
    def list_assets():
        return [asset.describe() for asset in ASSETS]

    @staticmethod
    def _asset(asset_id, metric):
        for asset in ASSETS:
            if asset.asset_id == asset_id and asset.metric == metric:
                return asset
        raise UnsupportedAsset("不支持的资产或指标；请使用 list_assets() 中的完整资产标识与指标")

    def _row(self, conn, asset):
        row = conn.execute(
            "SELECT * FROM md_cache WHERE asset_id=? AND metric=? AND provider_id=? AND is_simulated=?",
            (asset.asset_id, asset.metric, self.provider_id, int(self.is_simulated)),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["is_simulated"] = bool(result["is_simulated"])
        return result

    @staticmethod
    def _result(row, at, refresh_status, error=None):
        state = "missing"
        if row is not None:
            state = "fresh" if row["fetched_at"] <= timestamp(at) < row["expires_at"] else "stale"
        return {"data": row, "cache_status": state, "refresh_status": refresh_status, "error": error}

    def read_cache(self, asset_id, metric):
        asset = self._asset(asset_id, metric)
        at = utc(self.clock())
        with db.connection(self.database_path) as conn:
            row = self._row(conn, asset)
        return self._result(row, at, "not_requested")

    def query(self, asset_id, metric, *, force_refresh=False):
        if type(force_refresh) is not bool:
            raise ValidationError("force_refresh 必须为布尔值")
        asset = self._asset(asset_id, metric)
        started = utc(self.clock())
        with db.connection(self.database_path) as conn:
            previous = self._row(conn, asset)
        cached = self._result(previous, started, "not_needed")
        if not force_refresh and cached["cache_status"] == "fresh":
            return cached
        # 此时读连接已经关闭；即使提供器响应缓慢，也不占用写锁。
        try:
            payload = self.provider.fetch(asset, metric)
        except Exception as exc:
            return self._failure(asset, "provider_error", f"提供器获取失败：{exc}")
        try:
            quote = normalize_quote(payload, asset, self.provider_id, self.is_simulated)
        except ValidationError as exc:
            return self._failure(asset, "invalid_response", str(exc))
        fetched = utc(self.clock())
        if fetched < started:
            return self._failure(asset, "clock_error", "获取结束时间早于开始时间，未写入缓存")
        incoming = {**quote, "fetched_at": timestamp(fetched),
                    "expires_at": timestamp(fetched + timedelta(seconds=self.ttl_seconds))}
        with db.connection(self.database_path, write=True) as conn:
            current = self._row(conn, asset)
            if (current["revision"] if current else 0) != (previous["revision"] if previous else 0):
                return self._result(current, fetched, "superseded", {
                    "code": "cache_changed", "message": "获取期间其他调用已更新缓存，本次未覆盖",
                })
            if current and (quote["data_time_kind"] != current["data_time_kind"] or
                            quote["data_time"] < current["data_time"] or fetched < datetime.fromisoformat(current["fetched_at"].replace("Z", "+00:00"))):
                return self._result(current, fetched, "superseded", {
                    "code": "older_data", "message": "数据时间倒退、时间语义变化或获取时间倒退，本次未覆盖",
                })
            definition = (asset.asset_id, asset.display_name, asset.asset_type, asset.market, asset.code)
            conn.execute("INSERT INTO md_assets VALUES (?,?,?,?,?) ON CONFLICT(asset_id) DO NOTHING", definition)
            saved = conn.execute("SELECT * FROM md_assets WHERE asset_id=?", (asset.asset_id,)).fetchone()
            if tuple(saved) != definition:
                raise SchemaError("已保存的资产定义与目录不匹配，停止写入以保护数据")
            revision = (current["revision"] if current else 0) + 1
            conn.execute(
                "INSERT INTO md_cache (asset_id,metric,provider_id,is_simulated,value,currency,unit,"
                "data_time_kind,data_time,fetched_at,expires_at,revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(asset_id,metric,provider_id,is_simulated) DO UPDATE SET "
                "value=excluded.value,currency=excluded.currency,unit=excluded.unit,"
                "data_time_kind=excluded.data_time_kind,data_time=excluded.data_time,"
                "fetched_at=excluded.fetched_at,expires_at=excluded.expires_at,revision=excluded.revision",
                (asset.asset_id, metric, self.provider_id, int(self.is_simulated), incoming["value"],
                 incoming["currency"], incoming["unit"], incoming["data_time_kind"], incoming["data_time"],
                 incoming["fetched_at"], incoming["expires_at"], revision),
            )
            row = self._row(conn, asset)
        return self._result(row, fetched, "updated")

    def _failure(self, asset, code, message):
        # 失败时重读：并发调用可能已保存了更新数据；不写任何失败时间戳。
        with db.connection(self.database_path) as conn:
            row = self._row(conn, asset)
        return self._result(row, utc(self.clock()), "failed", {"code": code, "message": message})

    def refresh(self, asset_id, metric):
        return self.query(asset_id, metric, force_refresh=True)
