"""聊天仅可调用的三个有限工具；模型参数是数据，不执行 SQL、代码或 URL。"""

import json
from decimal import Decimal, ROUND_CEILING, localcontext

from . import db
from .config import Config
from .errors import NotFound, ValidationError
from .md import MDService
from .storage import Store
from .validation import currency, decimal_text, exact_keys, identifier, text


def json_object(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError()
            value[key] = item
        return value
    if not isinstance(raw, str) or len(raw) > 4000:
        raise ValidationError("工具参数须为最多4000字符的JSON对象")
    def reject_constant(value):
        raise ValueError()
    try:
        value = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=reject_constant)
    except (ValueError, RecursionError):
        raise ValidationError("工具参数JSON无效，不接受重复字段或非有限数值") from None
    if not isinstance(value, dict):
        raise ValidationError("工具参数必须为对象")
    return value


def failure(code, message):
    return {"ok": False, "error": {"code": code, "message": message}}


def savings_explanation(result):
    """只解释后端计算状态，不解析用户原话或筛查模型文本。"""
    if not result or not result.get("ok"):
        return None
    labels = {"target": "目标", "saved": "已存", "monthly": "月存入"}
    status = result.get("status")
    if status == "missing_input":
        missing = []
        for field in result["missing"]:
            item = result["inputs"][field]
            if item["value"] is None:
                missing.append(labels[field] + "金额")
            if item["currency"] is None:
                missing.append(labels[field] + "币种")
        explanation = "无法给出月数：" + "、".join(missing) + "未知，请补充后重新计算。"
    elif status == "currency_mismatch":
        units = "；".join(labels[field] + "：" + (item["currency"] or "未知")
                         for field, item in result["inputs"].items())
        explanation = f"无法给出月数：输入币种不一致（{units}），不进行换汇。"
    elif status == "unreachable":
        explanation = (f"无法给出月数：尚有差额 {result['gap']} {result['currency']}，"
                       "但月存入为零，按当前条件无法达成目标。")
    elif status == "reached":
        explanation = f"已达到储蓄目标：差额 {result['gap']} {result['currency']}，所需 0 个月。"
    elif status == "calculated":
        explanation = (f"差额 {result['gap']} {result['currency']}，"
                       f"按每月存入 {result['inputs']['monthly']['value']} {result['currency']}，"
                       f"所需 {result['months']} 个月（向上取整）。")
    else:
        return None
    return explanation + "\n计算口径：" + result["assumptions"]


def references(name, result):
    """固定来源注脚不让模型改写数值、单位或缓存状态；不是新增数据库状态。"""
    if name == "search_history":
        return [f"历史摘要 {r['id']}（会话 {r['conversation_id']}，版本{r['version']}，消息#{r['first_sequence']}–#{r['last_sequence']}，仅历史依据）"
                for r in result.get("records", [])]
    if name == "query_market" and result.get("data"):
        quote = result["data"]
        return [f"{result['asset']['display_name']}：{quote['value']} {result['display_unit']}；"
                f"数据时间 {quote['data_time']}；来源 {quote['provider_id']}；"
                f"{'模拟' if quote['is_simulated'] else '真实日线/正式净值'}；"
                f"缓存 {result['cache_status']}，刷新 {result['refresh_status']}；获取时间 {quote['fetched_at']}。"]
    return []


class ChatTools:
    def __init__(self, database_path=Config.DATABASE_PATH, *, market=None):
        self.database_path = db.local_path(database_path)
        self.store = Store(self.database_path)
        self.market = market if market is not None else MDService(self.database_path)

    @classmethod
    def from_app(cls, app):
        return cls(app.config["DATABASE_PATH"], market=MDService.from_app(app))

    def schemas(self):
        def tool(name, description, properties, required):
            return {"type": "function", "function": {"name": name, "description": description,
                "parameters": {"type": "object", "properties": properties,
                               "required": required, "additionalProperties": False}}}
        amount = {"type": ["string", "null"], "maxLength": 100,
                  "description": "币种基本单位非负十进制文本，最多2位有效小数；未知用null，零用\"0\"。不带符号、千分位或倍率。"}
        # 资产目录随应用配置变化，列目录不联网、不读数据库。
        catalog = self.market.list_assets()
        return [
            tool("search_history", "关键词字面匹配本地历史摘要，包括Overview未收录的历史。返回归档时的事实/理由及来源，不能代表当前SP。",
                 {"keyword": {"type": "string", "minLength": 1, "maxLength": 80},
                  "limit": {"type": "integer", "minimum": 1, "maximum": 5}}, ["keyword", "limit"]),
            tool("query_market", "仅查询当前目录，遵循缓存规则。资产不明确先询问，不猜短代码。回答必须注明来源、数据时间、模拟/过期状态；非实时行情。目录：" + json.dumps(catalog, ensure_ascii=False),
                 {"asset_id": {"type": "string", "enum": [a["asset_id"] for a in catalog]},
                  "metric": {"type": "string", "enum": ["price", "nav", "index_level"]}}, ["asset_id", "metric"]),
            tool("calculate_savings", "Python计算固定月存入、不计收益/通胀的储蓄目标。必须调用此工具计算月数。"
                 "每项输入用source指定user（用户明确给出的情景）、sp_goal（目标金额取已确认goal_id）、sp_savings_budget（月存入取SP储蓄预算）。"
                 "SP没有已存目标金额字段，不能把现金或投资预算当作已存款/月储蓄。缺失不猜，用user的value=null,currency=null。"
                 "模型负责理解用户金额/币种，工具验证结构和计算，不写SP。",
                 {name: {"type": "object", "properties": {
                     "source": {"type": "string", "enum": {"target": ["user", "sp_goal"],
                         "saved": ["user"], "monthly": ["user", "sp_savings_budget"]}[name]},
                     "value": amount, "currency": {"type": ["string", "null"], "maxLength": 3},
                     "goal_id": {"type": ["string", "null"], "maxLength": 100}},
                     "required": ["source", "value", "currency", "goal_id"], "additionalProperties": False}
                  for name in ("target", "saved", "monthly")}, ["target", "saved", "monthly"]),
        ]

    def execute(self, name, raw):
        if name not in ("search_history", "query_market", "calculate_savings"):
            return failure("unknown_tool", "未注册该工具，本次未执行查询或计算")
        try:
            args = json_object(raw)
            result = getattr(self, name)(args)
            if len(json.dumps(result, ensure_ascii=False)) > 6000:
                return failure("result_limit", "工具结果超出长度限制，请缩小查询范围")
            return result
        except ValidationError as exc:
            return failure("invalid_arguments", str(exc))
        except NotFound:
            return failure("not_found", "指定的已确认目标或历史对象不存在")
        except Exception:
            return failure("tool_failed", "本次工具未取得结果，请检查数据库或服务状态；未修改财务档案")

    def search_history(self, args):
        exact_keys(args, ("keyword", "limit"), "历史查询")
        keyword = text(args["keyword"], "关键词", 80).strip()
        limit = args["limit"]
        if type(limit) is not int or not 1 <= limit <= 5:
            raise ValidationError("历史查询limit须为1–5整数")
        with db.connection(self.database_path) as conn:
            # 仅匹配类别数组中的正文条目，不匹配JSON键名；先筛正文再取LIMIT。
            # instr是字面匹配，%和_不作为通配符；仅返回每个相同覆盖范围的最新版本。
            rows = conn.execute(
                "SELECT s.id,c.title FROM summaries s JOIN conversations c ON c.id=s.conversation_id "
                "WHERE EXISTS (SELECT 1 FROM json_each(s.content_json) AS category, "
                "json_each(category.value) AS entry WHERE category.type='array' AND entry.type='text' "
                "AND instr(lower(entry.value),lower(?))>0) AND NOT EXISTS "
                "(SELECT 1 FROM summaries n WHERE n.conversation_id=s.conversation_id "
                "AND n.first_sequence=s.first_sequence AND n.last_sequence=s.last_sequence AND n.version>s.version) "
                "ORDER BY s.created_at DESC,s.conversation_id DESC,s.version DESC,s.id DESC LIMIT ?",
                (keyword, limit)).fetchall()
            store = Store(self.database_path, _connection=conn)
            records = []
            for row in rows:
                item = store.get_summary(row["id"])
                # 逐条预算，保留有效JSON和来源；明确标记摘录，不伪装为完整原文。
                remaining = 500
                excerpt = {}
                categories = ("reasons", "decisions", "confirmed", "facts", "plans", "todos", "unresolved")
                categories = sorted(categories, key=lambda key: not any(keyword.casefold() in v.casefold() for v in item["content"][key]))
                for category in categories:
                    excerpt[category] = []
                    values = sorted(item["content"][category], key=lambda value: keyword.casefold() not in value.casefold())
                    for value in values[:3]:
                        if remaining <= 0:
                            break
                        start = max(0, value.casefold().find(keyword.casefold()) - 50)
                        part = value[start:start + min(200, remaining)]
                        excerpt[category].append(part)
                        remaining -= len(part)
                records.append({**{k: v for k, v in item.items() if k != "content"},
                                "conversation_title": row["title"][:80], "content_excerpt": excerpt,
                                "is_excerpt": True})
        return {"ok": True, "matched": bool(records), "records": records,
                "notice": "归档时的历史摘要摘录，不是当前状态。当前值以已确认SP为准；未确认计划不代表已生效。"}

    def query_market(self, args):
        exact_keys(args, ("asset_id", "metric"), "行情查询")
        asset_id = text(args["asset_id"], "资产标识", 100)
        metric = text(args["metric"], "指标", 30)
        # 目录和指标由MDService验证，不能让模型选择其他提供器或任意URL。
        result = self.market.query(asset_id, metric)
        if result["error"]:
            code = result["error"]["code"]
            result = {**result, "error": {"code": code if code in {
                "provider_error", "invalid_response", "rate_limit", "older_data", "cache_changed", "clock_error"} else "provider_error",
                "message": "上游限流，本次未取得新数据。" if code == "rate_limit" else "本次未取得新数据；有旧值时仅供带状态参考。"}}
        asset = next(a for a in self.market.list_assets() if a["asset_id"] == asset_id)
        definition = asset["metrics"][0]
        unit = {"currency_per_share": f"{definition['currency']}/股",
                "currency_per_unit": f"{definition['currency']}/份", "points": "点"}[definition["unit"]]
        return {"ok": result["data"] is not None, "asset": asset, **result,
                "display_unit": unit,
                "notice": "必须注明数据时间、来源、单位和模拟/过期状态；fresh仅指缓存最近获取，绝非实时行情。"}

    def calculate_savings(self, args):
        exact_keys(args, ("target", "saved", "monthly"), "储蓄计算")
        profile = self.store.get_profile()
        inputs = {}
        for field, item in args.items():
            exact_keys(item, ("source", "value", "currency", "goal_id"), "计算输入")
            source = item["source"]
            if source == "user":
                if item["goal_id"] is not None or (item["value"] is not None and not isinstance(item["value"], str)):
                    raise ValidationError("情景金额须为十进制文本或null，goal_id须为null")
                value = decimal_text(item["value"])
                unit = currency(item["currency"]) if item["currency"] is not None else None
            else:
                if item["value"] is not None or item["currency"] is not None:
                    raise ValidationError("SP来源须由后端读取，value/currency须为null")
                if source == "sp_goal" and field == "target":
                    goal_id = identifier(item["goal_id"])
                    goal = next((g for g in profile["goals"] if g["id"] == goal_id), None)
                    if goal is None:
                        raise NotFound("目标不存在")
                    value, unit = goal["target_amount"], goal["currency"]
                elif source == "sp_savings_budget" and field == "monthly" and item["goal_id"] is None:
                    value, unit = profile["savings_budget"], profile["currency"]
                else:
                    raise ValidationError("不支持该字段的SP来源；已存金额须由用户明确提供")
            inputs[field] = {"value": value, "currency": unit, "source": source,
                             "goal_id": item["goal_id"], "sp_version": profile["version"] if source != "user" else None}
        result = {"ok": True, "inputs": inputs, "gap": None, "months": None, "currency": None,
                  "assumptions": "每月固定存入；不计收益、通胀、费用及汇率；情景不写入SP。"}
        known_currencies = {i["currency"] for i in inputs.values() if i["currency"] is not None}
        if len(known_currencies) > 1:
            return {**result, "status": "currency_mismatch", "message": "输入币种不一致，不做换汇"}
        missing = [k for k in ("target", "saved") if inputs[k]["value"] is None or inputs[k]["currency"] is None]
        if missing:
            return {**result, "status": "missing_input", "missing": missing, "message": "金额或币种未知，请补充缺失输入"}
        with localcontext() as ctx:
            ctx.prec = 50
            gap = max(Decimal(inputs["target"]["value"]) - Decimal(inputs["saved"]["value"]), Decimal(0))
            if gap and (inputs["monthly"]["value"] is None or inputs["monthly"]["currency"] is None):
                return {**result, "gap": format(gap, ".2f"), "currency": inputs["target"]["currency"],
                        "status": "missing_input", "missing": ["monthly"], "message": "月存入金额或币种未知，请补充"}
            if not gap:
                return {**result, "gap": "0.00", "months": 0, "currency": inputs["target"]["currency"],
                        "status": "reached", "message": "已达到目标，无需继续存入"}
            monthly = Decimal(inputs["monthly"]["value"])
            months = int((gap / monthly).to_integral_value(rounding=ROUND_CEILING)) if gap and monthly else (0 if not gap else None)
        return {**result, "gap": format(gap, ".2f"), "months": months, "currency": inputs["target"]["currency"],
                "status": "reached" if not gap else "unreachable" if not monthly else "calculated",
                "message": "按当前条件无法达成：尚有差额且月存入为零" if gap and not monthly else "已按月数向上取整"}
