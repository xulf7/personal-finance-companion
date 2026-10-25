"""明确标注的虚构开发数据；仅显式调用，整个填充过程原子执行。"""

from . import db
from .errors import InvalidState
from .storage import Store
from .validation import FINANCE_FIELDS, SUMMARY_FIELDS

SEED_NAME = "fictional-sp-ca-v1"


def budget(value):
    return [{"op": "set_finances", "currency": "CNY", "values": {"investment_budget": value}}]


def summary(**parts):
    return {key: parts.get(key, []) for key in SUMMARY_FIELDS}


def seed_demo(database_path):
    with db.connection(database_path, write=True) as conn:
        if conn.execute("SELECT 1 FROM demo_seeds WHERE name=?", (SEED_NAME,)).fetchone():
            return {"seed": SEED_NAME, "created": False}
        for table in ("goals", "holdings", "conversations", "messages", "summaries", "overviews", "profile_changes", "demo_seeds"):
            if conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
                raise InvalidState("数据库已有其他业务数据，拒绝填充虚构演示；请使用独立测试数据库")
        service = Store(database_path, _connection=conn)
        profile = service.get_profile()
        if profile["version"] != 0 or any(
            profile[key] is not None for key in (*FINANCE_FIELDS, "currency", "risk_preference")
        ):
            raise InvalidState("SP 已被编辑，拒绝填充虚构演示，以保护已有数据")
        service.edit_profile(budget("500"), expected_version=0)
        session = service.create_conversation("【虚构演示】投资预算从 500 调整至 800")
        first = service.add_message(session["id"], "user", "【虚构数据】我当前每月投资预算为 500 元人民币。")
        proposal = service.add_message(session["id"], "user", "【虚构数据】日常支出减少了，我想把投资预算从 500 调整到 800 元。")
        old = service.save_summary(session["id"], first["id"], proposal["id"], summary(
            facts=["所有记录均为虚构演示数据。"],
            confirmed=["此前每月投资预算为 CNY 500.00。"],
            plans=["提出提高到 CNY 800.00，尚待确认。"],
            reasons=["日常支出减少。"], todos=["等待用户明确确认。"],
        ))
        candidate = service.create_change(session["id"], proposal["id"], budget("800"), base_version=1)
        confirmation = service.add_message(session["id"], "user", "【虚构数据】确认将每月投资预算保存为 800 元人民币。")
        service.confirm_change(candidate["id"])
        current = service.save_summary(session["id"], first["id"], confirmation["id"], summary(
            facts=["所有记录均为虚构演示数据。"],
            decisions=["用户明确确认后将预算从 CNY 500.00 改为 CNY 800.00。"],
            reasons=["日常支出减少。"], confirmed=["当前每月投资预算为 CNY 800.00。"],
        ))
        pending_message = service.add_message(session["id"], "user", "【虚构数据】以后或许提高到 1000 元，暂时不要保存。")
        service.create_change(session["id"], pending_message["id"], budget("1000"), base_version=2)
        service.save_overview(summary(
            facts=["虚构演示；原始消息和两个摘要版本均保留。"],
            decisions=["每月投资预算从 CNY 500.00 调整为 CNY 800.00。"],
            reasons=["用户说明日常支出减少，并明确确认修改。"],
            confirmed=["当前 SP 的投资预算为 CNY 800.00。"],
            plans=["另有 CNY 1000.00 的待确认候选，不属于当前有效档案。"],
        ), [old["id"], current["id"]])
        conn.execute("INSERT INTO demo_seeds VALUES (?,?)", (SEED_NAME, db.now()))
        return {"seed": SEED_NAME, "created": True}
