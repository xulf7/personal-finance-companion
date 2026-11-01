"""真实适配逻辑的离线替身测试：不导入 AKShare/pandas，不请求外网。"""

from datetime import date, datetime, timezone
from decimal import Decimal
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import create_app
from app.demo import seed_demo
from app.errors import ValidationError
from app.md import MDService
from app.md_provider import (
    ASSETS, REAL_ASSETS, AkShareProvider, MockProvider, source_date, upstream_decimal,
)
from test_md import sp_ca_snapshot
from test_storage import StorageCase


class Frame:
    """只模拟适配器实际使用的 DataFrame 读接口。"""
    def __init__(self, rows):
        self.rows = rows
        self.empty = not rows
        self.columns = set(rows[0]) if rows else set()

    def to_dict(self, orient):
        assert orient == "records"
        return self.rows


def fake_ak():
    return SimpleNamespace(
        stock_zh_a_hist=Mock(return_value=Frame([
            {"日期": date(2026, 10, 30), "收盘": 1400.12},
            {"日期": "2026-10-28", "收盘": "1300.12345678"},
            {"日期": "2026-10-29", "收盘": "1350"},
        ])),
        stock_zh_index_daily=Mock(return_value=Frame([
            {"date": "2026-10-30", "close": "3456.12345678"},
            {"date": "2026-10-29", "close": "3400"},
        ])),
        fund_open_fund_info_em=Mock(return_value=Frame([
            {"净值日期": "2026-10-29", "单位净值": "1.23456789"},
            {"净值日期": "2026-10-28", "单位净值": "1.23"},
        ])),
    )


class MarketProviderTests(StorageCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"MD_PROVIDER_ID": MockProvider.provider_id})
        self.env.start()
        self.addCleanup(self.env.stop)
        super().setUp()
        self.initialize()
        self.now = datetime(2026, 11, 1, tzinfo=timezone.utc)  # 周日，允许上周五数据。
        self.provider = AkShareProvider(clock=lambda: self.now)
        self.md = MDService(self.database, provider=self.provider, clock=lambda: self.now)
        self.ak = fake_ak()
        self.modules = patch.dict(sys.modules, {"akshare": self.ak})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def test_default_and_environment_selection_and_unknown_provider(self):
        self.assertEqual(MDService.from_app(self.app).provider_id, MockProvider.provider_id)
        with patch.dict(os.environ, {"MD_PROVIDER_ID": AkShareProvider.provider_id}):
            app = create_app({"DATABASE_PATH": self.database})
            self.assertEqual(MDService.from_app(app).list_assets()[0]["asset_id"], REAL_ASSETS[0].asset_id)
            self.assertEqual(MDService(self.database).provider_id, AkShareProvider.provider_id)
        self.assertEqual(MDService.from_app(self.app).provider_id, MockProvider.provider_id)
        for bad in ("akshrae", "", None):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValidationError, "MD_PROVIDER_ID"):
                create_app({"MD_PROVIDER_ID": bad})
        with patch.dict(os.environ, {"MD_PROVIDER_ID": "typo"}), self.assertRaises(ValidationError):
            MDService(self.database)

    def test_factory_page_health_and_catalog_are_lazy_even_without_dependency(self):
        with patch.dict(sys.modules, {"akshare": None}), patch("sqlite3.connect", side_effect=AssertionError("DB")):
            app = create_app({"MD_PROVIDER_ID": AkShareProvider.provider_id, "DATABASE_PATH": self.root / "absent.sqlite3"})
            client = app.test_client()
            for path in ("/", "/health", "/api/market/assets"):
                self.assertEqual(client.get(path).status_code, 200)
            catalog = client.get("/api/market/assets").json["data"]
            self.assertEqual([a["asset_id"] for a in catalog], [a.asset_id for a in REAL_ASSETS])
            self.assertTrue(all(a["is_simulated"] is False for a in catalog))

    def test_three_assets_latest_dates_units_precision_and_reconnect(self):
        for asset, value, day in zip(REAL_ASSETS, ("1400.12000000", "3456.12345678", "1.23456789"),
                                      ("2026-10-30", "2026-10-30", "2026-10-29")):
            with self.subTest(asset=asset.asset_id):
                result = self.md.refresh(asset.asset_id, asset.metric)
                self.assertIsNone(result["error"])
                row = result["data"]
                self.assertEqual((row["value"], row["data_time"], row["data_time_kind"]), (value, day, "date"))
                self.assertEqual((row["unit"], row["currency"]), (asset.unit, asset.currency))
                self.assertEqual(row["provider_id"], AkShareProvider.provider_id)
                self.assertIs(row["is_simulated"], False)
                other = MDService(self.database, provider=self.provider, clock=lambda: self.now)
                self.assertEqual(other.read_cache(asset.asset_id, asset.metric)["data"], row)
        self.ak.stock_zh_a_hist.assert_called_once_with(symbol="600519", period="daily", start_date="20260916", end_date="20261031", adjust="")
        self.ak.stock_zh_index_daily.assert_called_once_with(symbol="sh000001")
        self.ak.fund_open_fund_info_em.assert_called_once_with(symbol="001186", indicator="单位净值走势")

    def test_cross_year_china_clock_and_completed_daily_bar(self):
        self.now = datetime(2026, 12, 31, 16, 30, tzinfo=timezone.utc)  # 中国 1 月 1 日 00:30
        self.ak.stock_zh_a_hist.return_value = Frame([{"日期": "2026-12-31", "收盘": 0}])
        row = self.md.refresh(REAL_ASSETS[0].asset_id, "price")["data"]
        self.assertEqual(row["data_time"], "2026-12-31")
        self.ak.stock_zh_a_hist.assert_called_with(symbol="600519", period="daily", start_date="20261116", end_date="20261231", adjust="")
        self.now = datetime(2027, 1, 4, 6, tzinfo=timezone.utc)  # 14:00 盘中
        self.ak.stock_zh_index_daily.return_value = Frame([
            {"date": "2027-01-04", "close": "9999"}, {"date": "2026-12-31", "close": "3000"},
        ])
        self.assertEqual(self.md.refresh(REAL_ASSETS[1].asset_id, "index_level")["data"]["value"], "3000.00000000")
        self.now = datetime(2027, 1, 4, 7, tzinfo=timezone.utc)  # 15:00
        self.assertEqual(self.md.refresh(REAL_ASSETS[1].asset_id, "index_level")["data"]["value"], "9999.00000000")

    def test_decimal_conversion_zero_high_precision_and_rejection(self):
        for value, expected in [(0, "0.00000000"), (0.0, "0.00000000"), ("1.23456789", "1.23456789"),
                                (Decimal("1.230000000"), "1.23000000"), (1.2345, "1.23450000")]:
            self.assertEqual(upstream_decimal(value), expected)
        for value in (None, True, False, "", "--", float("nan"), float("inf"), Decimal("NaN"),
                      Decimal("Infinity"), "0.123456789", 0.123456789, -1, [], object()):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                upstream_decimal(value)
        self.assertEqual(source_date(datetime(2026, 10, 30)), date(2026, 10, 30))
        for day in (None, "NaT", "2026-02-30", "20261030", "2026-10-30junk", datetime(2026, 10, 30, 12)):
            with self.subTest(day=day), self.assertRaises(ValidationError):
                source_date(day)

    def test_bad_frames_fail_without_overwriting_old_cache(self):
        asset = REAL_ASSETS[0]
        original = self.md.refresh(asset.asset_id, asset.metric)["data"]
        for frame in (None, Frame([]), Frame([{"wrong": 1}]),
                      Frame([{"日期": "NaT", "收盘": "1"}]),
                      Frame([{"日期": "2026-10-30", "收盘": None}]),
                      Frame([{"日期": "2026-10-30", "收盘": float("inf")}]),
                      Frame([{"日期": "2026-10-30", "收盘": "1.000000001"}]),
                      Frame([{"日期": "2027-01-01", "收盘": "1"}]),
                      Frame([{"日期": "2026-10-30", "收盘": "1"}, {"日期": "2026-10-30", "收盘": "2"}])):
            with self.subTest(frame=frame):
                self.ak.stock_zh_a_hist.return_value = frame
                result = self.md.refresh(asset.asset_id, asset.metric)
                self.assertEqual(result["error"]["code"], "invalid_response")
                self.assertEqual(result["data"], original)
                self.assertEqual(result["refresh_status"], "failed")

    def test_real_cache_hit_read_only_failure_missing_and_source_isolation(self):
        asset = REAL_ASSETS[0]
        original = self.md.refresh(asset.asset_id, asset.metric)["data"]
        self.ak.stock_zh_a_hist.side_effect = OSError("offline")
        self.assertEqual(self.md.query(asset.asset_id, asset.metric)["refresh_status"], "not_needed")
        with patch.dict(sys.modules, {"akshare": None}):
            self.assertEqual(self.md.read_cache(asset.asset_id, asset.metric)["data"], original)
        self.assertEqual(self.ak.stock_zh_a_hist.call_count, 1)
        self.assertEqual(self.md.refresh(asset.asset_id, asset.metric)["data"], original)
        self.ak.stock_zh_index_daily.side_effect = TimeoutError("network")
        missing = self.md.refresh(REAL_ASSETS[1].asset_id, "index_level")
        self.assertEqual((missing["data"], missing["cache_status"], missing["refresh_status"]), (None, "missing", "failed"))
        mock = MDService(self.database, provider=MockProvider())
        mockrow = mock.refresh(ASSETS[0].asset_id, "price")["data"]
        self.assertTrue(mockrow["is_simulated"])
        self.assertEqual(self.md.read_cache(asset.asset_id, asset.metric)["data"], original)
        self.assertEqual(mock.read_cache(ASSETS[0].asset_id, "price")["data"], mockrow)

    def test_injected_clock_flows_through_from_app(self):
        self.app.config["MD_PROVIDER_ID"] = AkShareProvider.provider_id
        self.now = datetime(2027, 1, 1, 9, tzinfo=timezone.utc)
        service = MDService.from_app(self.app, clock=lambda: self.now)
        self.assertIs(service.provider.clock(), self.now)
        self.assertEqual(service.list_assets()[0]["asset_id"], REAL_ASSETS[0].asset_id)
        self.assertIs(MDService.from_app(self.app, provider=MockProvider()).provider.is_simulated, True)

    def test_real_mode_http_cli_catalog_cache_refresh_and_sp_ca_unchanged(self):
        seed_demo(self.database)
        before = sp_ca_snapshot(self.database)
        self.app.config["MD_PROVIDER_ID"] = AkShareProvider.provider_id
        client = self.app.test_client()
        assets = client.get("/api/market/assets").json["data"]
        self.assertEqual([a["asset_id"] for a in assets], [a.asset_id for a in REAL_ASSETS])
        query = {"asset_id": assets[0]["asset_id"], "metric": "price"}
        self.assertEqual(client.get("/api/market/cache", query_string=query).json["data"]["cache_status"], "missing")
        self.ak.stock_zh_a_hist.assert_not_called()
        refreshed = client.post("/api/market/refresh", json=query)
        self.assertEqual(refreshed.status_code, 200)
        self.assertFalse(refreshed.json["data"]["data"]["is_simulated"])
        runner = self.app.test_cli_runner()
        result = runner.invoke(args=["md-refresh"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(all(r["data"]["is_simulated"] is False for r in json.loads(result.output)))
        with patch.dict(sys.modules, {"akshare": None}):
            shown = runner.invoke(args=["md-show"])
            self.assertEqual(shown.exit_code, 0, shown.output)
            self.assertTrue(all(r["refresh_status"] == "not_requested" for r in json.loads(shown.output)))
        self.ak.stock_zh_a_hist.side_effect = TimeoutError("upstream unavailable")
        failed = client.post("/api/market/refresh", json=query).json["data"]
        self.assertEqual(failed["refresh_status"], "failed")
        self.assertEqual(failed["data"]["value"], "1400.12000000")
        self.assertNotIn("upstream unavailable", failed["error"]["message"])
        partial = runner.invoke(args=["md-refresh"])
        self.assertNotEqual(partial.exit_code, 0)
        self.assertIn('"refresh_status": "failed"', partial.output)
        self.assertIn('"refresh_status": "updated"', partial.output)
        self.ak.stock_zh_a_hist.side_effect = None
        self.app.config["MD_PROVIDER_ID"] = MockProvider.provider_id
        self.assertTrue(client.get("/api/market/assets").json["data"][0]["is_simulated"])
        self.assertEqual(client.get("/api/market/cache", query_string=query).status_code, 400)
        self.assertEqual(runner.invoke(args=["md-refresh-real"]).exit_code, 0)
        self.assertTrue(all(r["cache_status"] == "missing" for r in json.loads(runner.invoke(args=["md-show"]).output)))
        self.assertEqual(sp_ca_snapshot(self.database), before)

    def test_unknown_asset_does_not_call_network(self):
        with self.assertRaises(ValidationError):
            self.provider.fetch(ASSETS[0], "price")
        self.ak.stock_zh_a_hist.assert_not_called()
