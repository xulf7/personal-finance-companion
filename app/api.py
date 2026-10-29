"""薄 HTTP 适配层：结构校验和响应组织，业务规则留在已有服务。"""

from flask import Blueprint, current_app, jsonify, request
from werkzeug.exceptions import HTTPException

from .errors import (
    InitializationRequired, InvalidState, NotFound, SchemaError, StorageError,
    ValidationError, VersionConflict,
)
from .md import MDService
from .storage import Store
from .validation import exact_keys

api = Blueprint("api", __name__, url_prefix="/api")


def store():
    return Store.from_app(current_app)


def market():
    return MDService.from_app(current_app)


def body(*keys):
    if not request.is_json:
        raise ValidationError("写入请求必须使用 application/json")
    value = request.get_json()
    exact_keys(value, keys, "请求")
    return value


def response(data, status=200):
    return jsonify(data=data), status


def error(code, message, status):
    return jsonify(error={"code": code, "message": message}), status


@api.errorhandler(Exception)
def api_error(exc):
    for kind, code, status in (
        (ValidationError, "invalid_input", 400), (NotFound, "not_found", 404),
        (VersionConflict, "version_conflict", 409), (InvalidState, "invalid_state", 409),
    ):
        if isinstance(exc, kind):
            return error(code, str(exc), status)
    if isinstance(exc, InitializationRequired):
        return error("database_not_ready", "数据库尚未初始化或需要升级，请在项目目录执行 python -m flask --app app:create_app init-db。", 503)
    if isinstance(exc, SchemaError):
        return error("database_schema_error", "数据库结构或版本无法识别，请保留数据并联系维护者检查，不要删库重建。", 503)
    if isinstance(exc, StorageError):
        return error("storage_unavailable", "存储暂不可用，请联系维护者检查。", 503)
    if isinstance(exc, HTTPException):
        return http_error(exc)
    current_app.logger.exception("业务 API 执行失败")
    return error("internal_error", "操作未完成，请稍后重试或联系维护者。", 500)


def http_error(exc):
    """路由匹配阶段的 /api 404/405 也返回 JSON。"""
    if request.path != "/api" and not request.path.startswith("/api/"):
        return exc
    messages = {
        400: ("invalid_json", "请求不是有效的 JSON，请检查输入。"),
        404: ("not_found", "接口或对象不存在。"),
        405: ("method_not_allowed", "该接口不支持此请求方法。"),
        413: ("request_too_large", "请求内容过长。"),
    }
    code, message = messages.get(exc.code, ("http_error", "请求未完成。"))
    return error(code, message, exc.code or 500)


@api.after_request
def private_response(result):
    result.headers["Cache-Control"] = "no-store"
    return result


@api.get("/profile")
def get_profile():
    return response(store().get_profile())


@api.patch("/profile")
def edit_profile():
    value = body("expected_version", "changes")
    return response(store().edit_profile(value["changes"], expected_version=value["expected_version"]))


@api.get("/conversations")
def conversations():
    return response(store().list_conversations())


@api.post("/conversations")
def create_conversation():
    value = body("title")
    return response(store().create_conversation(value["title"]), 201)


@api.get("/conversations/<conversation_id>/messages")
def messages(conversation_id):
    return response(store().list_messages(conversation_id))


@api.post("/conversations/<conversation_id>/messages")
def create_message(conversation_id):
    value = body("content")
    return response(store().add_message(conversation_id, "user", value["content"]), 201)


@api.get("/conversations/<conversation_id>/summaries")
def summaries(conversation_id):
    return response(store().list_summaries(conversation_id))


@api.get("/overview")
def overview():
    return response(store().get_overview())


@api.get("/profile-changes")
def changes():
    service = store()
    rows = service.list_changes()
    for row in rows:
        row["source_conversation"] = service.get_conversation(row["conversation_id"])
        row["source_message"] = service.get_message(row["source_message_id"])
    return response(rows)


@api.post("/profile-changes/<change_id>/confirm")
def confirm_change(change_id):
    body()
    return response(store().confirm_change(change_id))


@api.post("/profile-changes/<change_id>/reject")
def reject_change(change_id):
    body()
    return response(store().reject_change(change_id))


@api.get("/market/assets")
def assets():
    return response(MDService.list_assets())


def market_result(value):
    # 提供器异常可能包含外部接口详情，浏览器只显示安全的中文提示。
    if value["error"] and value["error"]["code"] == "provider_error":
        value = {**value, "error": {"code": "provider_error", "message": "行情获取失败；已保留可用缓存，可稍后手动重试。"}}
    return response(value)


@api.get("/market/cache")
def cache():
    keys = ("asset_id", "metric")
    if set(request.args) != set(keys) or any(len(request.args.getlist(k)) != 1 for k in keys):
        raise ValidationError("查询须且仅须包含一个完整 asset_id 和一个 metric")
    return market_result(market().read_cache(request.args["asset_id"], request.args["metric"]))


@api.post("/market/refresh")
def refresh():
    value = body("asset_id", "metric")
    return market_result(market().refresh(value["asset_id"], value["metric"]))
