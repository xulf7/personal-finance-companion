"""服务输入的有限结构；精确数值规范化为十进制文本。"""

from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
import json
import re

from .errors import ValidationError

FINANCE_FIELDS = ("income", "expenses", "cash", "debt", "savings_budget", "investment_budget")
SUMMARY_FIELDS = ("facts", "decisions", "reasons", "plans", "confirmed", "todos", "unresolved")


def text(value, name, limit=20000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValidationError(f"{name} 必须为非空文本，最多 {limit} 字符")
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", value):
        raise ValidationError("标识只能包含字母、数字、下划线、点和连字符，长度 1–100")
    return value


def version_number(value):
    if type(value) is not int or value < 0:
        raise ValidationError("档案版本必须为非负整数")
    return value


def exact_keys(value, keys, name):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValidationError(f"{name} 字段必须为：{', '.join(keys)}")


def currency(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{3}", value):
        raise ValidationError("币种须为三个大写英文字母，如 CNY 或 USD；不自动换汇")
    return value


def decimal_text(value, places=2):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValidationError("金额/数量请使用十进制字符串、整数或 Decimal，不接受 float")
    if isinstance(value, str) and (len(value) > 100 or not re.fullmatch(r"\d+(\.\d+)?", value, re.ASCII)):
        raise ValidationError("精确数值必须是非负十进制文本")
    try:
        number = Decimal(value)
        if not number.is_finite() or number < 0 or number >= Decimal("1e18"):
            raise ValidationError("精确数值必须为非负有限值，整数部分最多 18 位")
        with localcontext() as context:
            context.prec = 40
            normalized = number.quantize(Decimal(1).scaleb(-places))
        if normalized != number:
            raise ValidationError(f"最多支持 {places} 位有效小数，不自动舍入")
    except InvalidOperation as exc:
        raise ValidationError("无法解析精确数值") from exc
    return format(normalized.copy_abs(), f".{places}f")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def summary_content(value):
    exact_keys(value, SUMMARY_FIELDS, "摘要")
    result = {}
    for key in SUMMARY_FIELDS:
        items = value[key]
        if not isinstance(items, list) or len(items) > 30:
            raise ValidationError(f"摘要 {key} 必须为最多 30 项的文本列表")
        result[key] = [text(item, key, 2000) for item in items]
    return result


def operations(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        raise ValidationError("修改操作必须为 1–100 项的列表")
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise ValidationError("每项修改必须为对象")
        op = item.get("op")
        if op == "set_finances":
            exact_keys(item, ("op", "currency", "values"), op)
            values = item["values"]
            if not isinstance(values, dict) or not values or not set(values) <= set(FINANCE_FIELDS):
                raise ValidationError("财务字段仅支持：" + ", ".join(FINANCE_FIELDS))
            result.append({"op": op, "currency": currency(item["currency"]),
                           "values": {k: decimal_text(v) for k, v in values.items()}})
        elif op == "set_risk":
            exact_keys(item, ("op", "value"), op)
            if item["value"] is not None and item["value"] not in ("low", "medium", "high"):
                raise ValidationError("风险偏好仅支持 null、low、medium、high")
            result.append(dict(item))
        elif op == "set_goal":
            exact_keys(item, ("op", "goal"), op)
            goal = item["goal"]
            exact_keys(goal, ("id", "name", "target_amount", "currency", "target_date"), "目标")
            target_date = goal["target_date"]
            if target_date is not None:
                try:
                    if date.fromisoformat(target_date).isoformat() != target_date:
                        raise ValueError()
                except (TypeError, ValueError) as exc:
                    raise ValidationError("目标日期须为 YYYY-MM-DD 或 null") from exc
            result.append({"op": op, "goal": {
                "id": identifier(goal["id"]), "name": text(goal["name"], "目标名称", 200),
                "target_amount": decimal_text(goal["target_amount"]),
                "currency": currency(goal["currency"]), "target_date": target_date,
            }})
        elif op == "set_holding":
            exact_keys(item, ("op", "holding"), op)
            holding = item["holding"]
            exact_keys(holding, ("id", "asset_code", "quantity", "unit_cost", "currency"), "持仓")
            result.append({"op": op, "holding": {
                "id": identifier(holding["id"]),
                "asset_code": text(holding["asset_code"], "资产标识", 100),
                "quantity": decimal_text(holding["quantity"], 8),
                "unit_cost": decimal_text(holding["unit_cost"]), "currency": currency(holding["currency"]),
            }})
        elif op in ("remove_goal", "remove_holding"):
            exact_keys(item, ("op", "id"), op)
            result.append({"op": op, "id": identifier(item["id"])})
        else:
            raise ValidationError("不支持的修改操作")
    return result
