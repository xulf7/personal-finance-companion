"""T07 四项回归：仅模型替身和项目内隔离 SQLite。"""

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import json
from unittest.mock import patch
from uuid import uuid4

from app import db
from app.archive import ArchiveService
from app.chat import ChatService
from app.demo import budget, summary
from app.errors import InvalidState, NotFound, ValidationError, VersionConflict
from app.llm import CheckResult
from app.storage import Store
from app.validation import SUMMARY_FIELDS
from test_storage import StorageCase


class T07FixTests(StorageCase):
    def setUp(self):
        super().setUp()
        self.initialize()
        self.store.edit_profile(budget('800'), expected_version=0)
        self.session = self.store.create_conversation('虚构四项回归')

    def turn(self, content, cid=None):
        return ChatService(self.database, model=lambda _:CheckResult('虚构助手回复', 'fake', None)).send(
            cid or self.session['id'], content, uuid4().hex)

    @staticmethod
    def model(messages):
        rows = json.loads(messages[1]['content'])['messages']
        users = [r for r in rows if r['role'] == 'user']
        content = {k: [] for k in SUMMARY_FIELDS}
        content['reasons'] = [{'text':r['content'], 'message_ids':[r['id']]} for r in users]
        candidates = []
        for r in users:
            if '现金' in r['content']:
                values = {'cash':None if '未知' in r['content'] else '0' if '为0' in r['content'] else '3000'}
            else:
                values = {'investment_budget':'950' if '950' in r['content'] else '900'}
            candidates.append({'source_message_id':r['id'], 'evidence':r['content'], 'operations':[
                {'op':'set_finances', 'currency':'CNY', 'values':values}]})
        return CheckResult(json.dumps({'summary':content, 'candidates':candidates}, ensure_ascii=False), 'fake', None)

    def archive(self, **kwargs):
        return ArchiveService(self.database, model=self.model, **kwargs)

    def batch(self):
        self.turn('把每月投资预算改为900')
        self.turn('把现金改为3000')
        result = self.archive().generate(self.session['id'])
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['candidate_count'], 2)
        return result

    def test_model_normalizes_wanyuan_and_backend_saves_base_units_without_changing_sp(self):
        for expression in ('900万元', '900 万元', '900\t万元', '900\n万元', '900，单位是万元'):
            with self.subTest(expression=expression):
                session = self.store.create_conversation('模型已明确规范化金额')
                self.turn('每月投资预算就用 '+expression, session['id'])
                self.turn('把现金改为3000', session['id'])
                def normalized(messages):
                    value = json.loads(self.model(messages).reply)
                    value['candidates'][0]['operations'][0]['values']['investment_budget'] = '9000000'
                    return CheckResult(json.dumps(value), 'fake', None)
                result = ArchiveService(self.database, model=normalized).generate(session['id'])
                self.assertEqual(result['status'], 'succeeded')
                self.assertEqual(result['candidate_count'], 2)
                changes = [self.store.get_change(i) for i in result['change_ids']]
                self.assertEqual({tuple(c['operations'][0]['values'].items()) for c in changes},
                                 {(('cash','3000.00'),), (('investment_budget','9000000.00'),)})
                self.assertEqual(len(self.store.list_summaries(session['id'])), 1)
        self.assertEqual(self.store.get_profile()['investment_budget'], '800.00')

    def test_amount_yuan_currency_zero_and_unknown(self):
        self.turn('把每月投资预算改为 900 元')
        self.turn('把现金改为0 元')
        result = self.archive().generate(self.session['id'])
        self.assertEqual(result['candidate_count'], 2)
        self.assertEqual({tuple(sorted(self.store.get_change(i)['operations'][0]['values'].items())) for i in result['change_ids']},
                         {(('cash','0.00'),), (('investment_budget','900.00'),)})
        self.store.confirm_change_batch(result['id'], result['change_ids'])
        self.turn('现金为未知')
        unknown = self.archive().generate(self.session['id'])
        self.assertEqual(unknown['candidate_count'], 1)
        self.assertIsNone(self.store.get_change(unknown['change_ids'][0])['operations'][0]['values']['cash'])
        self.store.confirm_change(unknown['change_ids'][0])
        self.assertIsNone(self.store.get_profile()['cash'])

    def test_yuan_currency_must_match_and_decimal_text_is_not_rounded(self):
        database = self.root/'usd.sqlite3'
        db.initialize(database, self.backups)
        store = Store(database)
        store.edit_profile([{'op':'set_finances','currency':'USD','values':{'investment_budget':'800'}}], expected_version=0)
        session = store.create_conversation('美元档案不接受人民币隐含换汇')
        ChatService(database, model=lambda _:CheckResult('虚构回复','fake',None)).send(session['id'],'投资预算改为900 元',uuid4().hex)
        result = ArchiveService(database, model=self.model).generate(session['id'])
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(result['candidate_count'],0)
        self.assertEqual(store.get_profile()['investment_budget'],'800.00')
        self.turn('投资预算改为900.01 元')
        def precise(messages):
            value = json.loads(self.model(messages).reply)
            value['candidates'][0]['operations'][0]['values']['investment_budget'] = '900.01'
            return CheckResult(json.dumps(value),'fake',None)
        result = ArchiveService(self.database, model=precise).generate(self.session['id'])
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(self.store.get_change(result['change_ids'][0])['operations'][0]['values']['investment_budget'],'900.01')

    def test_batch_confirmation_updates_once_and_duplicate_does_not_reapply(self):
        result = self.batch()
        confirmed = self.store.confirm_change_batch(result['id'], result['change_ids'])
        profile = self.store.get_profile()
        self.assertEqual(profile['investment_budget'], '900.00')
        self.assertEqual(profile['cash'], '3000.00')
        self.assertEqual(profile['version'], 2)
        self.assertEqual({c['applied_version'] for c in confirmed['changes']}, {2})
        self.assertEqual({c['status'] for c in confirmed['changes']}, {'confirmed'})
        self.store.confirm_change_batch(result['id'], list(reversed(result['change_ids'])))
        self.assertEqual(self.store.get_profile(), profile)
        self.store.edit_profile(budget('1000'), expected_version=2)
        again = self.store.confirm_change_batch(result['id'], result['change_ids'])
        self.assertEqual(again['applied_version'], 2)
        self.assertEqual(self.store.get_profile()['investment_budget'], '1000.00')

    def test_reject_then_confirm_remaining_and_stale_selection_rejected(self):
        result = self.batch()
        cash = next(i for i in result['change_ids'] if 'cash' in self.store.get_change(i)['operations'][0]['values'])
        self.store.reject_change(cash)
        with self.assertRaises(InvalidState): self.store.confirm_change_batch(result['id'], result['change_ids'])
        remaining = [i for i in result['change_ids'] if i != cash]
        self.store.confirm_change_batch(result['id'], remaining)
        self.assertIsNone(self.store.get_profile()['cash'])
        self.assertEqual(self.store.get_change(cash)['status'], 'rejected')
        self.assertEqual(self.store.get_profile()['investment_budget'], '900.00')

    def test_batch_real_version_conflict_and_field_conflict_require_explicit_resolution(self):
        result = self.batch()
        self.store.edit_profile(budget('850'), expected_version=1)
        with self.assertRaises(VersionConflict): self.store.confirm_change_batch(result['id'], result['change_ids'])
        self.assertTrue(all(self.store.get_change(i)['status']=='pending' for i in result['change_ids']))
        other = self.store.create_conversation('同字段冲突')
        self.turn('投资预算改为900', other['id'])
        self.turn('投资预算改为950', other['id'])
        conflict = self.archive().generate(other['id'])
        with self.assertRaises(InvalidState) as caught:
            self.store.confirm_change_batch(conflict['id'], conflict['change_ids'])
        self.assertIn('investment_budget', str(caught.exception))
        self.store.reject_change(conflict['change_ids'][0])
        self.store.confirm_change_batch(conflict['id'], conflict['change_ids'][1:])

    def test_batch_membership_full_pending_set_and_single_confirmation_protection(self):
        result = self.batch()
        with self.assertRaises(InvalidState): self.store.confirm_change_batch(result['id'], result['change_ids'][:1])
        with self.assertRaises(ValidationError): self.store.confirm_change_batch(result['id'], result['change_ids']*2)
        with self.assertRaises(ValidationError): self.store.confirm_change_batch(result['id'], ['foreign'])
        with self.assertRaises(NotFound): self.store.confirm_change_batch('missing', result['change_ids'])
        self.store.confirm_change(result['change_ids'][0])
        with self.assertRaises((InvalidState, VersionConflict)):
            self.store.confirm_change_batch(result['id'], result['change_ids'][1:])

    def test_batch_write_failure_rolls_back_profile_and_every_candidate(self):
        result = self.batch()
        before = self.store.get_profile()
        original = db.connection
        @contextmanager
        def broken(*args, **kwargs):
            with original(*args, **kwargs) as conn:
                if kwargs.get('write'):
                    conn.execute("CREATE TEMP TRIGGER fail_second BEFORE UPDATE OF status ON main.profile_changes WHEN (SELECT count(*) FROM profile_changes WHERE status='confirmed')=1 BEGIN SELECT RAISE(ABORT,'injected'); END")
                yield conn
        with patch.object(db, 'connection', broken):
            with self.assertRaises(Exception): self.store.confirm_change_batch(result['id'], result['change_ids'])
        self.assertEqual(self.store.get_profile(), before)
        self.assertTrue(all(self.store.get_change(i)['status']=='pending' for i in result['change_ids']))
        self.store.confirm_change_batch(result['id'], result['change_ids'])

    def test_partial_overlap_preserves_uncovered_reason_until_segments_cover_old_range(self):
        self.turn('投资预算改为900')
        self.turn('现金改为3000，因为应急金到位了')
        messages = self.store.list_messages(self.session['id'])
        old = self.store.save_summary(self.session['id'], messages[0]['id'], messages[3]['id'], summary(reasons=['应急金到位了']))
        self.store.save_overview(summary(reasons=['应急金到位了']), [old['id']])
        archive = self.archive(max_messages=2)
        first = archive.generate(self.session['id'])
        overview = self.store.get_overview()
        self.assertIn(old['id'], overview['summary_ids'])
        self.assertIn('应急金到位了', overview['content']['reasons'])
        second = archive.generate(self.session['id'])
        overview = self.store.get_overview()
        self.assertNotIn(old['id'], overview['summary_ids'])
        self.assertEqual(set(overview['summary_ids']), {first['summary']['id'], second['summary']['id']})
        self.assertTrue(any('应急金到位了' in r for r in overview['content']['reasons']))
        self.assertEqual(self.store.get_summary(old['id'])['content']['reasons'], ['应急金到位了'])

    def test_gap_in_successful_ranges_does_not_replace_uncovered_source(self):
        self.turn('投资预算改为900')
        self.store.add_message(self.session['id'], 'system', '旧记录中的未覆盖内容')
        self.turn('现金改为3000')
        messages = self.store.list_messages(self.session['id'])
        old = self.store.save_summary(self.session['id'], messages[0]['id'], messages[-1]['id'], summary(reasons=['未覆盖消息仍有依据']))
        self.store.save_overview(summary(reasons=['未覆盖消息仍有依据']), [old['id']])
        archive = self.archive(max_messages=2)
        archive.generate(self.session['id'])
        archive.generate(self.session['id'])
        self.assertIn(old['id'], self.store.get_overview()['summary_ids'])

    def overview_at_limit(self):
        summaries = []
        with db.connection(self.database, write=True) as conn:
            store = Store(self.database, _connection=conn)
            for i in range(100):
                session = store.create_conversation('虚构旧会话'+str(i))
                u = store.add_message(session['id'], 'user', '旧事实'+str(i))
                a = store.add_message(session['id'], 'assistant', '旧回复')
                saved = store.save_summary(session['id'], u['id'], a['id'], summary(reasons=['旧理由'+str(i)]))
                # 不等待真实时间，也不假设 Windows 时钟每次调用都递增。
                at = f'2026-01-01T00:{i//60:02}:{i%60:02}.000000Z'
                conn.execute('UPDATE summaries SET created_at=? WHERE id=?', (at, saved['id']))
                summaries.append({**saved, 'created_at':at})
            store.save_overview(summary(reasons=['旧理由99']), [s['id'] for s in summaries])
        return summaries

    def test_source_101_is_retained_oldest_evicted_but_history_kept(self):
        old = self.overview_at_limit()
        self.turn('投资预算改为900')
        result = self.archive().generate(self.session['id'])
        self.assertEqual(result['status'], 'succeeded')
        overview = self.store.get_overview()
        self.assertEqual(len(overview['summary_ids']), 100)
        self.assertIn(result['summary']['id'], overview['summary_ids'])
        self.assertNotIn(old[0]['id'], overview['summary_ids'])
        self.assertEqual(self.store.get_summary(old[0]['id'])['content']['reasons'], ['旧理由0'])

    def test_retention_ties_are_deterministic_and_current_archive_wins_clock_skew(self):
        old = self.overview_at_limit()
        at = '2026-01-01T00:00:00.000000Z'
        with db.connection(self.database, write=True) as conn:
            conn.execute('UPDATE summaries SET created_at=?', (at,))
        old = [{**s, 'created_at':at} for s in old]
        self.turn('投资预算改为900')
        with patch.object(db, 'now', return_value='2025-01-01T00:00:00.000000Z'):
            result = self.archive().generate(self.session['id'])
        overview = self.store.get_overview()
        expected = {s['id'] for s in sorted(old, key=lambda s:(s['created_at'],s['conversation_id'],s['version'],s['id']), reverse=True)[:99]}
        self.assertEqual(set(overview['summary_ids']), expected | {result['summary']['id']})
        self.assertIn('投资预算改为900', overview['content']['reasons'])

    def test_legacy_memory_limit_failure_retries_same_range_then_new_messages_continue(self):
        self.overview_at_limit()
        self.turn('投资预算改为900')
        archive = self.archive()
        # 构造原版本确实写入的失败状态，不读取开发库。
        attempt, batch = archive._claim(self.session['id'], None, None)
        failed = archive._finish(batch['row']['id'], attempt, error=('memory_limit','旧版本来源上限'))
        result = archive.generate(self.session['id'], retry_id=failed['id'])
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual((result['first_sequence'], result['last_sequence']), (1,2))
        self.turn('现金改为3000')
        new = archive.generate(self.session['id'])
        self.assertEqual(new['status'], 'succeeded')
        self.assertEqual(new['first_sequence'], 3)
        self.assertEqual(len(self.store.get_overview()['summary_ids']), 100)

    def test_concurrent_duplicate_batch_confirmations_only_advance_one_version(self):
        result = self.batch()
        def confirm():
            return Store(self.database).confirm_change_batch(result['id'], result['change_ids'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(confirm) for _ in range(2)]
            replies = [f.result(timeout=8) for f in futures]
        self.assertEqual([r['applied_version'] for r in replies], [2,2])
        self.assertEqual(self.store.get_profile()['version'], 2)

    def test_same_field_same_value_collapses_without_conflict(self):
        self.turn('投资预算改为900')
        self.turn('投资预算为900')
        result = self.archive().generate(self.session['id'])
        confirmed = self.store.confirm_change_batch(result['id'], result['change_ids'])
        self.assertEqual(confirmed['profile']['version'], 2)
        self.assertEqual(confirmed['profile']['investment_budget'], '900.00')

    def test_http_batch_lists_sources_rejects_then_confirms_and_reports_stale_version(self):
        result = self.batch()
        client = self.app.test_client()
        rows = client.get('/api/profile-changes').json['data']
        self.assertTrue(all(r['archive_id']==result['id'] for r in rows))
        self.assertEqual({r['source_message']['content'] for r in rows}, {'把每月投资预算改为900','把现金改为3000'})
        cash = next(r for r in rows if 'cash' in r['operations'][0]['values'])
        self.assertEqual(client.post('/api/profile-changes/'+cash['id']+'/reject',json={}).status_code,200)
        url = '/api/profile-change-batches/'+result['id']+'/confirm'
        self.assertEqual(client.post(url,json={'change_ids':result['change_ids']}).status_code,409)
        selected = [i for i in result['change_ids'] if i!=cash['id']]
        self.assertEqual(client.post(url,json={'change_ids':selected}).json['data']['profile']['investment_budget'], '900.00')
        self.assertEqual(client.post(url,json={'change_ids':selected}).json['data']['applied_version'],2)
        self.assertEqual(client.post(url,json={'change_ids':selected,'version':2}).status_code,400)
        self.assertEqual(client.post(url,json={}).status_code,400)
        self.turn('投资预算改为950')
        later = self.archive().generate(self.session['id'])
        self.store.edit_profile(budget('1000'),expected_version=2)
        conflict = client.post('/api/profile-change-batches/'+later['id']+'/confirm',json={'change_ids':later['change_ids']})
        self.assertEqual(conflict.status_code,409)
        self.assertEqual(conflict.json['error']['code'],'version_conflict')
