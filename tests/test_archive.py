"""归档状态、来源、原子性和跨会话记忆；全部使用隔离数据库与模型替身。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import json
import shutil
import sqlite3
from threading import Event
from unittest.mock import Mock, patch

from app import create_app, db
from app.archive import ArchiveService, ArchiveSaveError
from app.chat import ChatService
from app.demo import budget, seed_demo, summary
from app.errors import InvalidState, NotFound, SchemaError, ValidationError, VersionConflict
from app.llm import CheckResult, LLMError
from app.storage import Store
from app.validation import SUMMARY_FIELDS
from test_storage import StorageCase


def completion(batch, *, candidate=False, plan=False, label="日常支出减少了"):
    messages = json.loads(batch[1]['content'])['messages']
    user = next(m for m in messages if m['role'] == 'user')
    content = {k: [] for k in SUMMARY_FIELDS}
    content['reasons'] = [{'text': label, 'message_ids': [user['id']]}]
    content['plans' if plan else 'decisions'] = [{'text': '历史讨论900，尚未写入SP', 'message_ids': [user['id']]}]
    candidates = [{'source_message_id': user['id'], 'evidence': user['content'], 'operations': budget('900')}] if candidate else []
    return CheckResult(json.dumps({'summary': content, 'candidates': candidates}, ensure_ascii=False), 'fake-model', {'total_tokens': 100})


class ArchiveTests(StorageCase):
    def setUp(self):
        super().setUp()
        self.initialize()
        self.store.edit_profile(budget('800'), expected_version=0)
        self.session = self.store.create_conversation('虚构归档')
        self.model = Mock(side_effect=lambda batch: completion(batch, candidate=True))
        self.clock = datetime(2026, 11, 3, tzinfo=timezone.utc)
        self.archive = ArchiveService(self.database, model=self.model, clock=lambda: self.clock)

    def turn(self, question='把每月投资预算改为900，因为日常支出减少了', conversation=None, reply='这是意向，尚未修改档案。'):
        cid = conversation or self.session['id']
        return ChatService(self.database, model=lambda _: CheckResult(reply, 'fake-chat', None)).send(cid, question, 'chat-' + cid + '-' + str(len(self.store.list_messages(cid))))

    def test_success_reconnect_idempotent_and_confirmed_sp_cross_session_memory(self):
        turn = self.turn()
        before = self.store.get_profile()
        result = self.archive.generate(self.session['id'])
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['candidate_count'], 1)
        change = self.store.get_change(result['change_ids'][0])
        self.assertEqual(change['source_message_id'], turn['user_message']['id'])
        self.assertEqual(change['conversation_id'], self.session['id'])
        self.assertEqual(self.store.get_profile(), before)
        again = ArchiveService(self.database, model=self.model).generate(self.session['id'], first_sequence=1, last_sequence=2)
        self.assertEqual(result['id'], again['id'])
        self.assertEqual(self.archive.generate(self.session['id'])['id'], result['id'])
        self.assertEqual(self.archive.generate(self.session['id'], retry_id=result['id'])['id'], result['id'])
        self.model.assert_called_once()
        self.store.confirm_change(change['id'])
        self.assertEqual(self.store.get_profile()['investment_budget'], '900.00')
        next_session = self.store.create_conversation('新会话')
        model = Mock(return_value=CheckResult('预算900，历史理由是日常支出减少', 'fake-chat', None))
        ChatService(self.database, model=model).send(next_session['id'], '预算及理由是什么', 'new-chat')
        context = json.loads(model.call_args.args[0][1]['content'].split('\n', 1)[1])
        self.assertEqual(context['current_confirmed_sp']['investment_budget'], '900.00')
        self.assertIn('日常支出减少了', context['historical_ca_overview']['content']['reasons'])
        self.assertNotIn(turn['user_message']['content'], [m['content'] for m in model.call_args.args[0][2:]])
        app = create_app({'DATABASE_PATH': self.database})
        self.assertEqual(app.test_client().get('/api/conversations/' + self.session['id'] + '/archive').json['data']['latest']['id'], result['id'])

    def test_model_omits_hypotheses_and_backend_does_not_invent_candidates(self):
        # 替身负责省略假设候选；此测试不宣称后端或真实模型已理解语义。
        self.model.side_effect = lambda batch: completion(batch, candidate=False, plan=True)
        for content in ('如果以后改成900', '如果每月投资预算改为900会怎样', '假设我的投资预算为900',
                        '我想以后把每月投资预算改为900', '不要把每月投资预算改为900'):
            with self.subTest(content=content):
                session = self.store.create_conversation(content)
                self.turn(content, session['id'])
                result = self.archive.generate(session['id'])
                self.assertEqual(result['status'], 'succeeded')
                self.assertEqual(result['candidate_count'], 0)
        self.assertEqual(self.store.list_changes(), [])
        self.assertEqual(self.store.get_profile()['investment_budget'], '800.00')

    def test_invalid_summary_fails_before_any_business_write(self):
        turn = self.turn()
        def mutate(result, operation):
            value = json.loads(result.reply)
            operation(value)
            return CheckResult(json.dumps(value), result.model, result.usage)
        modifications = [
            lambda v: v['summary']['facts'].append({'text':'伪造引用','message_ids':['missing']}),
            lambda v: v['summary'].update(extra=[]),
            lambda v: v['summary']['facts'].append({'text':'x'*501,'message_ids':[turn['user_message']['id']]}),
        ]
        for edit in modifications:
            self.model.side_effect = lambda batch, edit=edit: mutate(completion(batch, candidate=True), edit)
            pending = self.archive.status(self.session['id'])['pending']
            result = self.archive.generate(self.session['id'], retry_id=pending['id'] if pending else None)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['error']['code'], 'invalid_output')
            self.assertEqual(self.store.list_summaries(self.session['id']), [])
            self.assertIsNone(self.store.get_overview())
            self.assertEqual(self.store.list_changes(), [])

    def test_failure_explicit_retry_preserves_prior_archive_and_messages(self):
        self.turn('如果以后投资预算改为900')
        old = self.archive.generate(self.session['id'])
        messages = self.turn()
        self.model.side_effect = LLMError('timeout', 'FAKE-SECRET-RAW')
        failed = self.archive.generate(self.session['id'])
        self.assertEqual(failed['status'], 'failed')
        self.assertNotIn('FAKE-SECRET', json.dumps(failed))
        self.assertEqual(self.archive.status(self.session['id'])['latest']['id'], old['id'])
        self.assertEqual(len(self.store.list_summaries(self.session['id'])), 1)
        calls = self.model.call_count
        self.assertEqual(self.archive.generate(self.session['id'])['id'], failed['id'])
        self.assertEqual(self.model.call_count, calls)
        self.model.side_effect = lambda batch: completion(batch, candidate=True)
        retried = self.archive.generate(self.session['id'], retry_id=failed['id'])
        self.assertEqual(retried['status'], 'succeeded')
        self.assertEqual(retried['first_sequence'], messages['user_message']['sequence'])
        self.assertEqual(len(self.store.list_messages(self.session['id'])), 4)
        self.assertEqual(self.store.get_profile()['investment_budget'], '800.00')

    def test_rejection_is_not_resurrected_and_new_evidence_has_normal_version_checks(self):
        self.turn()
        result = self.archive.generate(self.session['id'])
        change_id = result['change_ids'][0]
        self.store.reject_change(change_id)
        self.archive.generate(self.session['id'], first_sequence=1, last_sequence=2)
        self.archive.generate(self.session['id'], retry_id=result['id'])
        self.assertEqual(len(self.store.list_changes()), 1)
        self.assertEqual(self.store.get_change(change_id)['status'], 'rejected')
        with self.assertRaises(InvalidState): self.store.confirm_change(change_id)
        self.turn()
        second = self.archive.generate(self.session['id'])
        self.assertEqual(second['candidate_count'], 1)
        self.store.edit_profile(budget('850'), expected_version=1)
        with self.assertRaises(VersionConflict): self.store.confirm_change(second['change_ids'][0])

    def test_complete_turn_boundaries_incremental_chunks_and_no_truncated_archive(self):
        self.turn(); self.turn(); self.turn()
        archive = ArchiveService(self.database, model=self.model, max_messages=2)
        status = archive.status(self.session['id'])
        self.assertEqual(status['next_range'], {'first_sequence':1,'last_sequence':2})
        first = archive.generate(self.session['id'], **status['next_range'])
        self.assertEqual(first['last_sequence'], 2)
        self.assertEqual(archive.generate(self.session['id'], **status['next_range'])['id'], first['id'])
        self.assertEqual(self.model.call_count, 1)
        self.assertEqual(archive.status(self.session['id'])['next_range']['first_sequence'], 3)
        with self.assertRaises(InvalidState): archive.generate(self.session['id'], first_sequence=3, last_sequence=6)
        second = archive.generate(self.session['id'])
        self.assertEqual(second['last_sequence'], 4)
        prompt = json.loads(self.model.call_args.args[0][1]['content'])
        self.assertEqual([m['sequence'] for m in prompt['messages']], [3,4])
        self.assertIn(first['summary']['id'], self.store.get_overview()['summary_ids'])
        self.assertEqual(len(self.store.get_overview()['content']['reasons']), 1)

    def test_partial_failed_or_long_turns_are_not_marked_archived(self):
        self.turn()
        ChatService(self.database, model=Mock(side_effect=LLMError('timeout','hidden'))).send(self.session['id'], '未完成', 'incomplete')
        self.assertEqual(self.archive.generate(self.session['id'])['last_sequence'], 2)
        self.assertFalse(self.archive.status(self.session['id'])['can_archive'])
        self.assertIn('完整', self.archive.status(self.session['id'])['notice'])
        session = self.store.create_conversation('超长轮次')
        self.turn('q'*1900, session['id'], reply='a'*1900)
        archive = ArchiveService(self.database, model=self.model, max_chars=2000)
        self.assertFalse(archive.status(session['id'])['can_archive'])
        with self.assertRaises(InvalidState): archive.generate(session['id'])
        self.assertEqual(self.store.list_summaries(session['id']), [])

    def test_legacy_gaps_are_excluded_from_explicit_range_not_marked_archived(self):
        self.store.add_message(self.session['id'], 'user', '旧版未配对原文')
        self.store.add_message(self.session['id'], 'system', '旧系统消息')
        self.turn()
        result = self.archive.generate(self.session['id'])
        self.assertEqual((result['first_sequence'], result['last_sequence']), (3, 4))
        messages = json.loads(self.model.call_args.args[0][1]['content'])['messages']
        self.assertEqual([m['sequence'] for m in messages], [3,4])
        self.assertEqual(len(self.store.list_messages(self.session['id'])), 4)

    def test_concurrent_claims_fencing_and_model_does_not_hold_write_lock(self):
        self.turn()
        entered, release = Event(), Event()
        def slow(messages):
            with db.connection(self.database, write=True) as conn:
                conn.execute('UPDATE profile SET updated_at=updated_at')
            entered.set()
            self.assertTrue(release.wait(8))
            return completion(messages, candidate=True, label='迟到的旧结果')
        self.model.side_effect = slow
        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(self.archive.generate, self.session['id'])
            self.assertTrue(entered.wait(8))
            running = self.archive.generate(self.session['id'])
            self.assertEqual(running['status'], 'running')
            self.assertEqual(self.model.call_count, 1)
            self.clock += timedelta(seconds=181)
            self.assertEqual(self.archive.status(self.session['id'])['pending']['status'], 'interrupted')
            self.model.side_effect = lambda batch: completion(batch, candidate=True, label='重试的新结果')
            new = self.archive.generate(self.session['id'], retry_id=running['id'])
            release.set()
            old = future.result(timeout=8)
        self.assertEqual(old['summary']['id'], new['summary']['id'])
        self.assertEqual(len(self.store.list_changes()), 1)
        self.assertEqual(len(self.store.list_summaries(self.session['id'])), 1)
        self.assertNotIn('迟到的旧结果', json.dumps(self.store.get_overview(), ensure_ascii=False))

    def test_other_conversation_commit_is_retained_when_late_result_arrives(self):
        self.turn()
        second = self.store.create_conversation('另一会话')
        self.turn(conversation=second['id'])
        entered, release = Event(), Event()
        def slow(messages):
            entered.set(); self.assertTrue(release.wait(8))
            return completion(messages, label='第一会话原因')
        self.model.side_effect = slow
        with ThreadPoolExecutor() as pool:
            future = pool.submit(self.archive.generate, self.session['id'])
            self.assertTrue(entered.wait(8))
            other = ArchiveService(self.database, model=lambda m:completion(m, label='另一会话原因')).generate(second['id'])
            release.set(); result = future.result(timeout=8)
        overview = self.store.get_overview()
        self.assertEqual(set(overview['summary_ids']), {other['summary']['id'], result['summary']['id']})
        self.assertEqual(set(overview['content']['reasons']), {'第一会话原因','另一会话原因'})

    def test_new_messages_during_model_wait_are_left_for_next_range(self):
        self.turn()
        def model(messages):
            self.turn()
            return completion(messages)
        self.model.side_effect = model
        result = self.archive.generate(self.session['id'])
        self.assertEqual(result['last_sequence'], 2)
        self.assertEqual(self.archive.status(self.session['id'])['next_range']['first_sequence'], 3)

    def test_write_failure_rolls_back_summary_overview_candidates_and_can_retry(self):
        self.turn()
        original = db.connection
        @contextmanager
        def broken(*args, **kwargs):
            with original(*args, **kwargs) as conn:
                if kwargs.get('write'):
                    conn.execute("CREATE TEMP TRIGGER fail_archive BEFORE INSERT ON main.archive_evidence BEGIN SELECT RAISE(ABORT,'FAKE-SECRET'); END")
                yield conn
        with patch.object(db, 'connection', broken):
            result = self.archive.generate(self.session['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error']['code'], 'save_failed')
        self.assertEqual(self.store.list_summaries(self.session['id']), [])
        self.assertIsNone(self.store.get_overview())
        self.assertEqual(self.store.list_changes(), [])
        self.assertEqual(self.store.get_profile()['investment_budget'], '800.00')
        self.assertEqual(self.archive.generate(self.session['id'], retry_id=result['id'])['status'], 'succeeded')

    def test_zero_unknown_and_exact_decimal_and_unchanged_values(self):
        for question, field, value in [('把现金改为0','cash','0.00'), ('现金为未知','cash',None), ('收入为123.45','income','123.45')]:
            session = self.store.create_conversation('数值')
            self.turn(question, session['id'])
            def model(batch):
                response = completion(batch, candidate=True)
                data = json.loads(response.reply)
                data['candidates'][0]['operations'][0]['values'] = {field:value}
                return CheckResult(json.dumps(data), 'fake', None)
            result = ArchiveService(self.database, model=model).generate(session['id'])
            self.assertEqual(result['status'], 'succeeded')
            self.assertEqual(result['candidate_count'], 0 if value is None else 1)
            if result['candidate_count']:
                self.assertEqual(self.store.get_change(result['change_ids'][0])['operations'][0]['values'][field], value)

    def test_manual_rejected_evidence_is_reused_not_recreated(self):
        turn = self.turn()
        previous = self.store.create_change(self.session['id'], turn['user_message']['id'], budget('900'), base_version=1)
        self.store.reject_change(previous['id'])
        result = self.archive.generate(self.session['id'])
        self.assertEqual(result['candidate_count'], 0)
        self.assertEqual(result['change_ids'], [previous['id']])
        self.assertEqual(len(self.store.list_changes()), 1)
        self.assertEqual(self.store.get_change(previous['id'])['status'], 'rejected')

    def test_risk_supported_and_invalid_model_amounts_are_skipped(self):
        self.turn('把风险偏好改为中等风险')
        def risk_model(batch):
            response = completion(batch, candidate=True)
            value = json.loads(response.reply)
            value['candidates'][0]['operations'] = [{'op':'set_risk','value':'medium'}]
            return CheckResult(json.dumps(value), 'fake', None)
        risk = ArchiveService(self.database, model=risk_model).generate(self.session['id'])
        self.assertEqual(risk['candidate_count'], 1)
        self.store.confirm_change(risk['change_ids'][0])
        self.assertEqual(self.store.get_profile()['risk_preference'], 'medium')
        for raw in ('900万', '900k', '900,000', '900USD', '900.001', 900):
            with self.subTest(raw=raw):
                session = self.store.create_conversation('模型候选金额契约')
                self.turn('这就是我的新投资安排', session['id'])
                def invalid(batch):
                    response = completion(batch, candidate=True)
                    value = json.loads(response.reply)
                    value['candidates'][0]['operations'][0]['values']['investment_budget'] = raw
                    return CheckResult(json.dumps(value), 'fake', None)
                self.model.side_effect = invalid
                result = self.archive.generate(session['id'])
                self.assertEqual(result['status'], 'succeeded')
                self.assertEqual(result['candidate_count'], 0)
        self.assertEqual(len(self.store.list_changes()), 1)

    def test_overlapping_summary_replaced_other_session_and_history_retained(self):
        turn = self.turn()
        old = self.store.save_summary(self.session['id'], turn['user_message']['id'], turn['assistant_message']['id'], summary(reasons=['旧版会话原因']))
        other = self.store.create_conversation('保留的其他会话')
        self.turn(conversation=other['id'])
        other_messages = self.store.list_messages(other['id'])
        saved = self.store.save_summary(other['id'], other_messages[0]['id'], other_messages[1]['id'], summary(reasons=['其他会话仍有用的原因']))
        self.store.save_overview(summary(reasons=['旧版会话原因','其他会话仍有用的原因']), [old['id'],saved['id']])
        current = self.archive.generate(self.session['id'])
        overview = self.store.get_overview()
        self.assertEqual(set(overview['summary_ids']), {current['summary']['id'], saved['id']})
        self.assertIn('其他会话仍有用的原因', overview['content']['reasons'])
        self.assertNotIn('旧版会话原因', overview['content']['reasons'])
        self.assertEqual(self.store.get_summary(old['id'])['content']['reasons'], ['旧版会话原因'])

    def test_profile_edit_during_fetch_leaves_old_base_candidate_conflict(self):
        self.turn()
        def model(batch):
            self.store.edit_profile(budget('850'), expected_version=1)
            return completion(batch, candidate=True)
        self.model.side_effect = model
        result = self.archive.generate(self.session['id'])
        change = self.store.get_change(result['change_ids'][0])
        self.assertEqual(change['base_version'], 1)
        self.assertEqual(self.store.get_profile()['investment_budget'], '850.00')
        with self.assertRaises(VersionConflict): self.store.confirm_change(change['id'])

    def test_malformed_json_is_safe_and_total_status_save_failure_is_not_success(self):
        self.turn()
        self.model.side_effect = None
        self.model.return_value = CheckResult('not-json FAKE-SECRET', 'fake', None)
        result = self.archive.generate(self.session['id'])
        self.assertEqual(result['error']['code'], 'invalid_output')
        self.assertNotIn('FAKE-SECRET', json.dumps(result))
        with patch.object(self.archive, '_finish', side_effect=OSError('FAKE-SECRET')):
            with self.assertRaises(ArchiveSaveError) as caught:
                self.archive.generate(self.session['id'], retry_id=result['id'])
        self.assertNotIn('FAKE-SECRET', str(caught.exception))
        self.assertEqual(self.store.list_summaries(self.session['id']), [])

    def test_http_status_generate_retry_strict_body_no_key_or_get_network(self):
        self.turn()
        client = self.app.test_client(); base = '/api/conversations/'+self.session['id']+'/archive'
        with patch('app.llm.load_config', side_effect=AssertionError('no config')), patch('app.llm.complete', side_effect=AssertionError('no model')):
            status = client.get(base)
            self.assertEqual(status.status_code, 200)
            self.assertTrue(status.json['data']['can_archive'])
            self.assertEqual(client.get('/health').status_code, 200)
        with patch.object(ArchiveService, '_model', side_effect=lambda m: completion(m, candidate=True)):
            result = client.post(base, json=status.json['data']['next_range'])
        self.assertEqual(result.json['data']['status'], 'succeeded')
        self.assertEqual(client.post(base, json={}).status_code, 400)
        self.assertEqual(client.post(base, json={'first_sequence':True,'last_sequence':2}).status_code, 400)
        self.assertEqual(client.post(base, json={'first_sequence':None,'last_sequence':None}).status_code, 400)
        self.assertEqual(client.post(base, json={'first_sequence':1,'last_sequence':2,'system':'evil'}).status_code, 400)
        self.assertEqual(client.post(base+'/missing/retry', json={}).status_code, 404)
        self.turn()
        with patch.object(ArchiveService, '_model', side_effect=RuntimeError('FAKE-SECRET')):
            with self.assertNoLogs(self.app.logger, level='ERROR'):
                failed = client.post(base, json={'first_sequence':3,'last_sequence':4})
        self.assertNotIn('FAKE-SECRET', failed.get_data(as_text=True))
        with patch.object(ArchiveService, '_model', side_effect=lambda m: completion(m)):
            retry = client.post(base+'/'+failed.json['data']['id']+'/retry', json={})
        self.assertEqual(retry.json['data']['status'], 'succeeded')


class ArchiveMigrationTests(StorageCase):
    def v3(self):
        migrations = self.root/'v3'; migrations.mkdir()
        for path in db.migration_files()[:3]: shutil.copy2(path, migrations/path.name)
        db.initialize(self.database, self.backups, migrations_dir=migrations)
        with patch.object(db, 'MIGRATIONS_DIR', migrations):
            seed_demo(self.database)
            session = self.store.create_conversation('T05已保存聊天')
            ChatService(self.database, model=lambda _:CheckResult('旧回复','fake',None)).send(session['id'],'旧问题','old-chat')
        return migrations

    def snapshot(self, path):
        with closing(sqlite3.connect(path)) as conn:
            names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='schema_migrations'")]
            return {t:conn.execute('SELECT * FROM '+t+' ORDER BY rowid').fetchall() for t in names}

    def test_real_v3_upgrade_valid_backup_retains_t05_and_repeat_and_copy(self):
        self.v3(); before = self.snapshot(self.database)
        upgraded = self.initialize()
        self.assertEqual(upgraded['version'], 4)
        self.assertEqual(self.snapshot(upgraded['backup']), before)
        after = self.snapshot(self.database)
        self.assertEqual({k:after[k] for k in before}, before)
        self.assertEqual(after['archive_requests'], [])
        self.assertIsNone(self.initialize()['backup'])
        copied = self.root/'copied.sqlite3'; shutil.copy2(self.database, copied)
        self.assertEqual(Store(copied).get_profile()['investment_budget'], '800.00')
        self.assertEqual(len(Store(copied).list_changes()), 2)

    def test_v3_failure_rolls_back_schema_data_and_keeps_valid_backup(self):
        migrations = self.v3(); before = self.snapshot(self.database)
        (migrations/'004_broken.sql').write_text("CREATE TABLE partial(value);\nUPDATE profile SET investment_budget='999';\nINSERT INTO missing VALUES(1);\n", encoding='utf8', newline='\n')
        with self.assertRaises(SchemaError): db.initialize(self.database, self.backups, migrations_dir=migrations)
        self.assertEqual(self.snapshot(self.database), before)
        self.assertEqual(self.snapshot(next(self.backups.iterdir())), before)
