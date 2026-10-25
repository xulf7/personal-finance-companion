"""有限资产目录和小型提供器协议；本轮唯一实现仅返回固定虚构数据。"""

from dataclasses import dataclass
from typing import Protocol


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

    def fetch(self, asset: Asset, metric: str) -> dict:
        """返回规范化字段，不负责缓存、获取时间或数据库写入。"""
        ...


class MockProvider:
    provider_id = "mock-fixed-v1"
    is_simulated = True

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
