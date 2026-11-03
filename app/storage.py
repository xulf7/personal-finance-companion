"""单用户 SP/CA 服务。每次调用短连接；写操作在 BEGIN IMMEDIATE 事务内完成。"""

from contextlib import contextmanager
import hashlib
import json
from uuid import uuid4

from . import db
from .config import Config
from .errors import InvalidState, NotFound, ValidationError, VersionConflict
from .validation import (
    FINANCE_FIELDS, canonical, identifier, operations, summary_content, text, version_number,
)


def _one(conn, table, object_id):
    # table 仅由本模块内的固定调用点传入，所有外部值均使用占位符。
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (object_id,)).fetchone()
    if row is None:
        raise NotFound(f"{table} 对象不存在：{object_id}")
    return dict(row)


def _decoded(row, column, key):
    result = dict(row)
    result[key] = json.loads(result.pop(column))
    return result


class Store:
    def __init__(self, database_path=Config.DATABASE_PATH, *, _connection=None):
        self.database_path = db.local_path(database_path)
        self._connection = _connection  # 仅供原子演示填充复用已有事务。

    @classmethod
    def from_app(cls, app):
        return cls(app.config["DATABASE_PATH"])

    @contextmanager
    def _session(self, *, write=False):
        if self._connection is not None:
            yield self._connection
        else:
            with db.connection(self.database_path, write=write) as conn:
                yield conn

    @staticmethod
    def _profile(conn):
        result = _one(conn, "profile", 1)
        result["goals"] = [dict(r) for r in conn.execute(
            "SELECT id, name, target_amount, currency, target_date FROM goals ORDER BY id"
        )]
        result["holdings"] = [dict(r) for r in conn.execute(
            "SELECT id, asset_code, quantity, unit_cost, currency FROM holdings ORDER BY id"
        )]
        return result

    def get_profile(self):
        with self._session() as conn:
            return self._profile(conn)

    @staticmethod
    def _check_version(profile, expected):
        if profile["version"] != expected:
            raise VersionConflict(f"档案版本冲突：基于 {expected}，当前 {profile['version']}；请重新读取并确认")

    def _apply(self, conn, changes, expected_version):
        before = self._profile(conn)
        self._check_version(before, expected_version)
        for change in changes:
            op = change["op"]
            if op == "set_finances":
                profile = _one(conn, "profile", 1)
                if profile["currency"] is not None and profile["currency"] != change["currency"]:
                    raise ValidationError("基础财务币种已确定，不支持直接改币种或隐含换汇")
                values = {key: change["values"].get(key, profile[key]) for key in FINANCE_FIELDS}
                conn.execute(
                    "UPDATE profile SET currency=?, income=?, expenses=?, cash=?, debt=?, "
                    "savings_budget=?, investment_budget=? WHERE id=1",
                    (change["currency"], *(values[key] for key in FINANCE_FIELDS)),
                )
            elif op == "set_risk":
                conn.execute("UPDATE profile SET risk_preference=? WHERE id=1", (change["value"],))
            elif op == "set_goal":
                goal = change["goal"]
                conn.execute(
                    "INSERT INTO goals (id,name,target_amount,currency,target_date) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET name=excluded.name, target_amount=excluded.target_amount, "
                    "currency=excluded.currency, target_date=excluded.target_date",
                    tuple(goal[k] for k in ("id", "name", "target_amount", "currency", "target_date")),
                )
            elif op == "set_holding":
                holding = change["holding"]
                conn.execute(
                    "INSERT INTO holdings (id,asset_code,quantity,unit_cost,currency) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET asset_code=excluded.asset_code, quantity=excluded.quantity, "
                    "unit_cost=excluded.unit_cost, currency=excluded.currency",
                    tuple(holding[k] for k in ("id", "asset_code", "quantity", "unit_cost", "currency")),
                )
            elif op in ("remove_goal", "remove_holding"):
                table = "goals" if op == "remove_goal" else "holdings"
                if conn.execute(f"DELETE FROM {table} WHERE id=?", (change["id"],)).rowcount != 1:
                    raise NotFound(f"待删除的 {table} 子项不存在：{change['id']}")
        after = self._profile(conn)
        if before != after:
            conn.execute("UPDATE profile SET version=version+1, updated_at=? WHERE id=1", (db.now(),))
        return self._profile(conn)

    def edit_profile(self, changes, *, expected_version):
        """仅用于用户已在表单明确保存的修改；不用于自动提取或摘要回写。"""
        changes = operations(changes)
        expected_version = version_number(expected_version)
        with self._session(write=True) as conn:
            return self._apply(conn, changes, expected_version)

    def create_conversation(self, title):
        title = text(title, "会话标题", 200)
        with self._session(write=True) as conn:
            object_id = uuid4().hex
            conn.execute("INSERT INTO conversations VALUES (?,?,?)", (object_id, title, db.now()))
            return _one(conn, "conversations", object_id)

    def get_conversation(self, conversation_id):
        with self._session() as conn:
            return _one(conn, "conversations", identifier(conversation_id))

    def list_conversations(self):
        with self._session() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM conversations ORDER BY created_at, id")]

    def add_message(self, conversation_id, role, content, *, _chat_attempt=None):
        conversation_id = identifier(conversation_id)
        if role not in ("user", "assistant", "system", "tool"):
            raise ValidationError("消息角色须为 user、assistant、system 或 tool")
        content = text(content, "消息内容")
        with self._session(write=True) as conn:
            _one(conn, "conversations", conversation_id)
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_requests'").fetchone():
                pending = conn.execute("SELECT attempt_id FROM chat_requests WHERE conversation_id=? AND status!='succeeded'", (conversation_id,)).fetchone()
                if pending and pending["attempt_id"] != _chat_attempt:
                    raise InvalidState("当前会话有未完成聊天，请等待或明确重试原问题")
            sequence = conn.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM messages WHERE conversation_id=?", (conversation_id,)
            ).fetchone()[0]
            object_id = uuid4().hex
            conn.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)",
                         (object_id, conversation_id, sequence, role, content, db.now()))
            return _one(conn, "messages", object_id)

    def get_message(self, message_id):
        with self._session() as conn:
            return _one(conn, "messages", identifier(message_id))

    def list_messages(self, conversation_id):
        with self._session() as conn:
            _one(conn, "conversations", identifier(conversation_id))
            return [dict(row) for row in conn.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY sequence", (conversation_id,)
            )]

    def recent_messages(self, conversation_id, *, before_sequence, limit=12):
        conversation_id = identifier(conversation_id)
        if type(limit) is not int or not 0 <= limit <= 50 or type(before_sequence) is not int or before_sequence < 1:
            raise ValidationError("近期消息范围无效")
        with self._session() as conn:
            _one(conn, "conversations", conversation_id)
            rows = conn.execute("SELECT * FROM messages WHERE conversation_id=? AND sequence<? AND role IN ('user','assistant') ORDER BY sequence DESC LIMIT ?",
                                (conversation_id, before_sequence, limit)).fetchall()
            return [dict(row) for row in reversed(rows)]

    def save_summary(self, conversation_id, first_message_id, last_message_id, content):
        conversation_id = identifier(conversation_id)
        content_json = canonical(summary_content(content))
        with self._session(write=True) as conn:
            first = _one(conn, "messages", identifier(first_message_id))
            last = _one(conn, "messages", identifier(last_message_id))
            if first["conversation_id"] != conversation_id or last["conversation_id"] != conversation_id:
                raise ValidationError("摘要起止消息必须属于指定会话")
            if first["sequence"] > last["sequence"]:
                raise ValidationError("摘要消息范围起点不得晚于终点")
            args = (conversation_id, first["sequence"], last["sequence"], content_json)
            existing = conn.execute(
                "SELECT * FROM summaries WHERE conversation_id=? AND first_sequence=? "
                "AND last_sequence=? AND content_json=?", args,
            ).fetchone()
            if existing:
                return _decoded(existing, "content_json", "content")
            version = conn.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM summaries WHERE conversation_id=?", (conversation_id,)
            ).fetchone()[0]
            object_id = uuid4().hex
            conn.execute("INSERT INTO summaries VALUES (?,?,?,?,?,?,?)",
                         (object_id, conversation_id, version, args[1], args[2], content_json, db.now()))
            return _decoded(_one(conn, "summaries", object_id), "content_json", "content")

    def get_summary(self, summary_id):
        with self._session() as conn:
            return _decoded(_one(conn, "summaries", identifier(summary_id)), "content_json", "content")

    def list_summaries(self, conversation_id):
        with self._session() as conn:
            _one(conn, "conversations", identifier(conversation_id))
            return [_decoded(r, "content_json", "content") for r in conn.execute(
                "SELECT * FROM summaries WHERE conversation_id=? ORDER BY version", (conversation_id,)
            )]

    @staticmethod
    def _overview(conn, row):
        result = _decoded(row, "content_json", "content")
        result.pop("input_hash")
        result["summary_ids"] = [r[0] for r in conn.execute(
            "SELECT summary_id FROM overview_sources WHERE overview_id=? ORDER BY summary_id", (result["id"],)
        )]
        return result

    def save_overview(self, content, summary_ids):
        content = summary_content(content)
        if not isinstance(summary_ids, list) or not 1 <= len(summary_ids) <= 100:
            raise ValidationError("Overview 必须关联 1–100 个摘要标识")
        sources = sorted({identifier(i) for i in summary_ids})
        input_hash = hashlib.sha256(canonical([content, sources]).encode("utf-8")).hexdigest()
        with self._session(write=True) as conn:
            for source in sources:
                _one(conn, "summaries", source)
            existing = conn.execute("SELECT * FROM overviews WHERE input_hash=?", (input_hash,)).fetchone()
            if existing:
                return self._overview(conn, existing)
            version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM overviews").fetchone()[0]
            object_id = uuid4().hex
            conn.execute("INSERT INTO overviews VALUES (?,?,?,?,?)",
                         (object_id, version, canonical(content), input_hash, db.now()))
            conn.executemany("INSERT INTO overview_sources VALUES (?,?)", [(object_id, s) for s in sources])
            return self._overview(conn, _one(conn, "overviews", object_id))

    def get_overview(self, version=None):
        if version is not None:
            version_number(version)
        with self._session() as conn:
            if version is None:
                row = conn.execute("SELECT * FROM overviews ORDER BY version DESC LIMIT 1").fetchone()
                return self._overview(conn, row) if row else None
            row = conn.execute("SELECT * FROM overviews WHERE version=?", (version,)).fetchone()
            if row is None:
                raise NotFound(f"Overview 版本不存在：{version}")
            return self._overview(conn, row)

    def create_change(self, conversation_id, source_message_id, changes, *, base_version):
        conversation_id, source_message_id = identifier(conversation_id), identifier(source_message_id)
        changes, base_version = operations(changes), version_number(base_version)
        with self._session(write=True) as conn:
            message = _one(conn, "messages", source_message_id)
            if message["conversation_id"] != conversation_id or message["role"] != "user":
                raise ValidationError("候选来源必须为指定会话中的用户消息")
            if base_version > self._profile(conn)["version"]:
                raise ValidationError("候选不能基于未来的档案版本")
            object_id = uuid4().hex
            conn.execute(
                "INSERT INTO profile_changes (id,conversation_id,source_message_id,base_version,"
                "operations_json,status,created_at) VALUES (?,?,?,?,?,'pending',?)",
                (object_id, conversation_id, source_message_id, base_version, canonical(changes), db.now()),
            )
            return _decoded(_one(conn, "profile_changes", object_id), "operations_json", "operations")

    def get_change(self, change_id):
        with self._session() as conn:
            return _decoded(_one(conn, "profile_changes", identifier(change_id)), "operations_json", "operations")

    def list_changes(self, status=None):
        if status is not None and status not in ("pending", "confirmed", "rejected"):
            raise ValidationError("未知候选状态")
        with self._session() as conn:
            return [_decoded(r, "operations_json", "operations") for r in conn.execute(
                "SELECT * FROM profile_changes WHERE (? IS NULL OR status=?) ORDER BY created_at,id", (status, status)
            )]

    def confirm_change(self, change_id):
        with self._session(write=True) as conn:
            change = _one(conn, "profile_changes", identifier(change_id))
            if change["status"] == "confirmed":
                return _decoded(change, "operations_json", "operations")
            if change["status"] != "pending":
                raise InvalidState("已拒绝的候选不能确认，请创建新的候选")
            profile = self._apply(conn, operations(json.loads(change["operations_json"])), change["base_version"])
            conn.execute(
                "UPDATE profile_changes SET status='confirmed', processed_at=?, applied_version=? WHERE id=?",
                (db.now(), profile["version"], change_id),
            )
            return _decoded(_one(conn, "profile_changes", change_id), "operations_json", "operations")

    def change_archive_ids(self):
        """只读的归档批次关联，供候选页面分组；不改变原有单条对象语义。"""
        with self._session() as conn:
            return {r['change_id']: r['archive_id'] for r in conn.execute(
                "SELECT e.change_id,e.archive_id FROM archive_evidence e JOIN archive_requests a ON a.id=e.archive_id WHERE a.status='succeeded'")}

    def confirm_change_batch(self, archive_id, change_ids):
        """确认用户已核对的同批次全部剩余候选；一次版本校验、一次档案更新。"""
        archive_id = identifier(archive_id)
        if not isinstance(change_ids, list) or not 1 <= len(change_ids) <= 100:
            raise ValidationError("合并确认须提供1–100个候选标识")
        selected_ids = [identifier(i) for i in change_ids]
        if len(set(selected_ids)) != len(selected_ids):
            raise ValidationError("合并确认的候选标识不得重复")
        selected_ids = sorted(selected_ids)
        with self._session(write=True) as conn:
            archive = _one(conn, 'archive_requests', archive_id)
            if archive['status'] != 'succeeded':
                raise InvalidState("只能确认成功归档批次的候选")
            rows = {r['id']: dict(r) for r in conn.execute(
                "SELECT p.* FROM profile_changes p JOIN archive_evidence e ON e.change_id=p.id WHERE e.archive_id=?", (archive_id,))}
            if not set(selected_ids) <= rows.keys():
                raise ValidationError("候选不属于指定归档批次")
            chosen = [rows[i] for i in selected_ids]
            if any(r['conversation_id'] != archive['conversation_id'] for r in chosen):
                raise InvalidState("候选来源与归档会话不一致")
            def result():
                changes = [_decoded(_one(conn, 'profile_changes', i), 'operations_json', 'operations') for i in selected_ids]
                return {'archive_id': archive_id, 'applied_version': changes[0]['applied_version'],
                        'changes': changes, 'profile': self._profile(conn)}
            if all(r['status'] == 'confirmed' for r in chosen):
                if len({r['applied_version'] for r in chosen}) != 1:
                    raise InvalidState("这些候选已在不同操作中确认，请重新读取状态")
                return result()  # 即使SP后来又变化，也不会重新应用或覆盖。
            if any(r['status'] != 'pending' for r in chosen):
                raise InvalidState("候选状态已变化，请重新读取批次，先拒绝不想应用的项")
            if set(selected_ids) != {i for i, row in rows.items() if row['status'] == 'pending'}:
                raise InvalidState("请核对并确认本批次全部剩余待确认项；不想应用的项请先逐条拒绝")
            base = json.loads(archive['profile_json'])['version']
            if any(r['base_version'] != base for r in chosen):
                raise VersionConflict("同批次候选基于不同档案版本，不能合并确认")
            values, risk, risk_set, selected_currency = {}, None, False, None
            for row in chosen:
                for change in operations(json.loads(row['operations_json'])):
                    if change['op'] == 'set_finances':
                        if selected_currency is not None and selected_currency != change['currency']:
                            raise InvalidState("同批次币种冲突，请先拒绝不想应用的项；不自动换汇")
                        selected_currency = change['currency']
                        for field, value in change['values'].items():
                            if field in values and values[field] != value:
                                raise InvalidState(f"同批次字段 {field} 有不同目标值，请先拒绝冲突项，再合并确认")
                            values[field] = value
                    elif change['op'] == 'set_risk':
                        if risk_set and risk != change['value']:
                            raise InvalidState("同批次字段 risk_preference 有不同目标值，请先拒绝冲突项")
                        risk, risk_set = change['value'], True
                    else:
                        raise InvalidState("批次合并只支持归档的基础财务和风险偏好，其他操作请单条处理")
            merged = ([{'op': 'set_finances', 'currency': selected_currency, 'values': values}] if values else [])
            if risk_set:
                merged.append({'op': 'set_risk', 'value': risk})
            profile = self._apply(conn, operations(merged), base)
            at = db.now()
            conn.executemany("UPDATE profile_changes SET status='confirmed',processed_at=?,applied_version=? WHERE id=?",
                             [(at, profile['version'], i) for i in selected_ids])
            return result()

    def reject_change(self, change_id):
        with self._session(write=True) as conn:
            change = _one(conn, "profile_changes", identifier(change_id))
            if change["status"] == "confirmed":
                raise InvalidState("已确认的候选不能拒绝；需要撤回时请创建新的明确修改")
            if change["status"] == "pending":
                conn.execute("UPDATE profile_changes SET status='rejected', processed_at=? WHERE id=?", (db.now(), change_id))
            return _decoded(_one(conn, "profile_changes", change_id), "operations_json", "operations")
