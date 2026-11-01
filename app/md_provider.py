"""有限目录与提供器；AKShare 延迟导入，所有缓存规则由 MDService 管理。"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from numbers import Real
from typing import Protocol

from .errors import ValidationError
from .validation import decimal_text

CHINA = timezone(timedelta(hours=8))


def utc_now():
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Asset:
    asset_id: str
    display_name: str
    asset_type: str
    market: str
    code: str
    metric: str
    unit: str
    currency: str | None

    def describe(self):
        return {
            "asset_id": self.asset_id, "display_name": self.display_name,
            "asset_type": self.asset_type, "market": self.market, "code": self.code,
            "metrics": [{"metric": self.metric, "unit": self.unit, "currency": self.currency}],
        }


# 故意复用短代码，展示不能仅凭代码识别不同市场/类型的资产。
ASSETS = (
    Asset("demo:stock:DEMO_EXCHANGE:DEMO001", "虚构演示股票 DEMO001", "stock", "DEMO_EXCHANGE",
          "DEMO001", "price", "currency_per_share", "CNY"),
    Asset("demo:fund:DEMO_FUNDS:DEMO001", "虚构演示基金 DEMO001", "fund", "DEMO_FUNDS",
          "DEMO001", "nav", "currency_per_unit", "CNY"),
    Asset("demo:index:DEMO_EXCHANGE:DEMO001", "虚构演示指数 DEMO001", "index", "DEMO_EXCHANGE",
          "DEMO001", "index_level", "points", None),
)


class Provider(Protocol):
    provider_id: str
    is_simulated: bool
    assets: tuple[Asset, ...]

    def fetch(self, asset: Asset, metric: str) -> dict:
        """返回规范化字段，不负责缓存、获取时间或数据库写入。"""
        ...


class MockProvider:
    provider_id = "mock-fixed-v1"
    is_simulated = True
    assets = ASSETS

    def fetch(self, asset, metric):
        samples = {
            "stock": ("123.45678901", "datetime", "2026-01-15T07:00:00Z"),
            "fund": ("1.23456789", "date", "2026-01-14"),
            "index": ("3456.78901234", "datetime", "2026-01-15T07:00:00Z"),
        }
        if asset not in ASSETS or metric != asset.metric:
            raise ValueError("模拟提供器不支持该资产或指标")
        value, kind, source_time = samples[asset.asset_type]
        return {
            "asset_id": asset.asset_id, "metric": metric, "value": value,
            "currency": asset.currency, "unit": asset.unit,
            "provider_id": self.provider_id, "is_simulated": self.is_simulated,
            "data_time_kind": kind, "data_time": source_time,
        }


REAL_ASSETS = (
    Asset("cn:stock:SHA:600519", "贵州茅台 600519", "stock", "SHA",
          "600519", "price", "currency_per_share", "CNY"),
    Asset("cn:index:SHS:000001", "上证指数 000001", "index", "SHS",
          "000001", "index_level", "points", None),
    Asset("cn:fund:OEF:001186", "开放式基金 001186", "fund", "OEF",
          "001186", "nav", "currency_per_unit", "CNY"),
)


class AkShareProvider:
    provider_id = "akshare-real-daily-v1"
    is_simulated = False
    assets = REAL_ASSETS

    def __init__(self, *, clock=utc_now):
        self.clock = clock

    def fetch(self, asset, metric):
        if asset not in self.assets or metric != asset.metric:
            raise ValidationError("真实提供器不支持该资产或指标")
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValidationError("市场日期时钟必须包含时区")
        local = now.astimezone(CHINA)
        # 日线接口盘中可能含当日未完成的 K 线，15:00 前只选此前日期。
        close_day = local.date() if local.hour >= 15 else local.date() - timedelta(days=1)
        import akshare as ak
        # 1.20.18 三个函数均无 timeout 参数，使用其原生 HTTP 默认 (10, 60) 秒。
        # 不修改全局网络库/配置，不额外重试，不失败回退 mock。
        if asset.asset_type == "stock" and metric == "price":
            frame = ak.stock_zh_a_hist(symbol=asset.code, period="daily",
                                       start_date=(close_day - timedelta(days=45)).strftime("%Y%m%d"),
                                       end_date=close_day.strftime("%Y%m%d"), adjust="")
            value, day = latest_daily(frame, "日期", "收盘", local.date(), close_day)
        elif asset.asset_type == "index" and metric == "index_level":
            frame = ak.stock_zh_index_daily(symbol="sh" + asset.code)
            value, day = latest_daily(frame, "date", "close", local.date(), close_day)
        elif asset.asset_type == "fund" and metric == "nav":
            frame = ak.fund_open_fund_info_em(symbol=asset.code, indicator="单位净值走势")
            value, day = latest_daily(frame, "净值日期", "单位净值", local.date(), local.date())
        return {
            "asset_id": asset.asset_id, "metric": metric,
            "value": value,
            "currency": asset.currency, "unit": asset.unit,
            "provider_id": self.provider_id, "is_simulated": self.is_simulated,
            "data_time_kind": "date", "data_time": day,
        }


def upstream_decimal(value):
    """仅适配器接受上游数值类型；str(float) 不能恢复上游已损失的精度。"""
    if isinstance(value, bool) or not isinstance(value, (str, Real, Decimal)):
        raise ValidationError("上游数值缺失或类型错误")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError("上游数值无法解析") from exc
    return decimal_text(number, places=8)


def source_date(value):
    if isinstance(value, datetime):
        # pandas Timestamp 也是 datetime；只允许日期型午夜，不截断采样时刻。
        if value.hour or value.minute or value.second or value.microsecond:
            raise ValidationError("上游日线日期含非午夜时间")
        value = value.date()
    try:
        if isinstance(value, date):
            result = date.fromisoformat(value.isoformat())
        elif isinstance(value, str) and date.fromisoformat(value).isoformat() == value:
            result = date.fromisoformat(value)
        else:
            raise ValueError()
    except (TypeError, ValueError) as exc:
        raise ValidationError("上游日期无效，须为 YYYY-MM-DD") from exc
    return result


def latest_daily(frame, date_column, value_column, today, cutoff):
    """逐行校验日期/数值，按日期取最新；不靠上游排序，不跳过坏值冒充成功。"""
    if frame is None or frame.empty:
        raise ValidationError("上游返回空数据")
    if date_column not in frame.columns or value_column not in frame.columns:
        raise ValidationError("上游缺少日期或数值字段")
    rows = {}
    for row in frame.to_dict(orient="records"):
        day = source_date(row[date_column])
        value = upstream_decimal(row[value_column])
        if day > today:
            raise ValidationError("上游数据日期晚于中国市场当前日期")
        if day in rows and rows[day] != value:
            raise ValidationError("同一数据日期存在不同数值，无法确定最新记录")
        rows[day] = value
    available = [day for day in rows if day <= cutoff]
    if not available:
        raise ValidationError("上游暂无已完成日期的数据")
    latest = max(available)
    return rows[latest], latest.isoformat()


def provider_for_id(provider_id, *, clock=utc_now):
    """唯一配置映射；构造不导入 AKShare、不联网。"""
    if provider_id == MockProvider.provider_id:
        return MockProvider()
    if provider_id == AkShareProvider.provider_id:
        return AkShareProvider(clock=clock)
    raise ValidationError("未知 MD_PROVIDER_ID；仅支持 mock-fixed-v1 或 akshare-real-daily-v1")
