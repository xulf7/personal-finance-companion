"""聊天编排/HTTP/旧库迁移；隔离数据库和模型替身，不读取真实配置。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import shutil
import sqlite3
from threading import Event
from unittest.mock import Mock, patch

from app import db
from app.chat import ChatService, ChatSaveError, SYSTEM_PROMPT
from app.demo import budget, seed_demo, summary
from app.errors import InvalidState, NotFound, SchemaError, ValidationError
from app.llm import CheckResult, LLMError
from app.md import MDService
from app.md_provider import MockProvider, ASSETS
from app.storage import Store
from test_storage import StorageCase


def answer(content="虚构测试回复"):
    return CheckResult(content, "fake-model", {"prompt_tokens":10,"completion_tokens":5,"total_tokens":15})


class ChatTests(StorageCase):
    def setUp(self):
        super().setUp()
        self.initialize()
        self.session = self.store.create_conversation("虚构聊天")
        self.model = Mock(return_value=answer())
        self.clock = datetime(2026,11,3,tzinfo=timezone.utc)
        self.chat = ChatService(self.database, model=self.model, clock=lambda:self.clock)

    def test_success_reconnect_and_duplicate_return_real_saved_messages(self):
        result = self.chat.send(self.session['id'], "本月预算如何安排？", "req1")
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['user_message']['role'], 'user')
        self.assertEqual(result['assistant_message']['role'], 'assistant')
        self.assertEqual(result['assistant_message']['sequence'], 2)
        self.assertEqual(self.store.list_messages(self.session['id']), [result['user_message'],result['assistant_message']])
        other = ChatService(self.database,model=self.model)
        again = other.send(self.session['id'], "本月预算如何安排？", "req1")
        self.assertEqual(again['assistant_message']['id'],result['assistant_message']['id'])
        self.assertEqual(other.retry(self.session['id'],'req1')['status'],'succeeded')
        self.model.assert_called_once()
        self.assertEqual(other.history(self.session['id'])['turns'][0]['usage']['total_tokens'],15)

    def test_context_profile_overview_roles_precision_unknown_zero_and_current_question_once(self):
        self.store.edit_profile([{'op':'set_finances','currency':'CNY','values':{'income':None,'debt':'0','investment_budget':'800.01'}}],expected_version=0)
        historic=self.store.create_conversation('其他会话')
        source=self.store.add_message(historic['id'],'user','其他会话原文不得混入')
        saved=self.store.save_summary(historic['id'],source['id'],source['id'],summary(confirmed=['过去预算500'],reasons=['历史原因']))
        self.store.save_overview(summary(confirmed=['过去预算500'],plans=['只是计划不是当前SP']),[saved['id']])
        self.store.add_message(self.session['id'],'user','前一问题')
        self.store.add_message(self.session['id'],'assistant','前一回复')
        self.store.add_message(self.session['id'],'system','不应变成system指令')
        self.chat.send(self.session['id'],'当前问题唯一标记','req-context')
        messages=self.model.call_args.args[0]
        self.assertEqual(messages[0],{'role':'system','content':SYSTEM_PROMPT})
        self.assertEqual(sum(m['role']=='system' for m in messages),1)
        data=json.loads(messages[1]['content'].split('\n',1)[1])
        self.assertEqual(data['current_confirmed_sp']['investment_budget'],'800.01')
        self.assertEqual(data['current_confirmed_sp']['debt'],'0.00')
        self.assertIsNone(data['current_confirmed_sp']['income'])
        self.assertIn('过去预算500',data['historical_ca_overview']['content']['confirmed'])
        all_content='\n'.join(m['content'] for m in messages)
        self.assertNotIn('其他会话原文不得混入',all_content)
        self.assertNotIn('不应变成system指令',all_content)
        self.assertEqual(sum(m['content']=='当前问题唯一标记' for m in messages),1)
        self.assertEqual([m['content'] for m in messages[2:]],['前一问题','前一回复','当前问题唯一标记'])

    def test_no_overview_long_history_and_context_budget(self):
        for i in range(20):
            self.store.add_message(self.session['id'],'user' if i%2==0 else 'assistant',str(i)+('x'*1000))
        bounded=ChatService(self.database,model=self.model,recent_limit=4,context_chars=8000)
        bounded.send(self.session['id'],'新问题','req-bounds')
        messages=self.model.call_args.args[0]
        self.assertLessEqual(len(messages),7)
        self.assertLessEqual(sum(len(m['content']) for m in messages),8000)
        self.assertIsNone(json.loads(messages[1]['content'].split('\n',1)[1])['historical_ca_overview'])
        self.assertEqual(messages[-1]['content'],'新问题')
        self.assertTrue(messages[-2]['content'].startswith('19'))

    def test_failure_persists_user_retry_reuses_it_no_fake_assistant(self):
        self.model.side_effect=LLMError('timeout','secret-never-show')
        failed=self.chat.send(self.session['id'],'失败问题','req-fail')
        self.assertEqual(failed['status'],'failed')
        self.assertIsNone(failed['assistant_message'])
        self.assertNotIn('secret-never-show',json.dumps(failed))
        self.assertEqual(len(self.store.list_messages(self.session['id'])),1)
        self.chat.send(self.session['id'],'失败问题','req-fail')
        self.assertEqual(self.model.call_count,1)
        with self.assertRaises(InvalidState): self.chat.send(self.session['id'],'新问题','req-new')
        self.model.side_effect=None
        done=self.chat.retry(self.session['id'],'req-fail')
        self.assertEqual(done['status'],'succeeded')
        self.assertEqual(done['user_message']['id'],failed['user_message']['id'])
        self.assertEqual(len(self.store.list_messages(self.session['id'])),2)

    def test_request_id_collision_retry_not_found_and_input_validation(self):
        self.chat.send(self.session['id'],'问题','same-id')
        with self.assertRaises(InvalidState):self.chat.send(self.session['id'],'不同问题','same-id')
        other=self.store.create_conversation('不同会话')
        with self.assertRaises(InvalidState):self.chat.send(other['id'],'问题','same-id')
        with self.assertRaises(NotFound):self.chat.retry(other['id'],'missing')
        for content in ('','x'*4001,None,False):
            with self.assertRaises(ValidationError): self.chat.send(other['id'],content,'invalid')
        self.model.assert_called_once()

    def test_concurrent_send_duplicate_raw_endpoint_and_no_database_write_lock(self):
        started,finish=Event(),Event()
        def model(messages):
            with db.connection(self.database,write=True) as conn:
                conn.execute('UPDATE profile SET version=version')
            started.set()
            self.assertTrue(finish.wait(5))
            return answer()
        self.chat.model=model
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.chat.send,self.session['id'],'并发问题','concurrent')
            try:
                self.assertTrue(started.wait(5))
                self.assertEqual(self.chat.send(self.session['id'],'并发问题','concurrent')['status'],'running')
                with self.assertRaises(InvalidState):self.chat.send(self.session['id'],'后一个问题','other')
                with self.assertRaises(InvalidState):self.store.add_message(self.session['id'],'user','原始保存接口也不能插队')
                self.assertEqual(len(self.chat.history(self.session['id'])['messages']),1)
                other=self.store.create_conversation('可并行的其他会话')
                self.assertEqual(ChatService(self.database,model=self.model).send(other['id'],'其他问题','parallel')['status'],'succeeded')
            finally:finish.set()
            self.assertEqual(future.result()['status'],'succeeded')

    def test_expired_attempt_retry_fences_late_original_result(self):
        replacement=ChatService(self.database,model=Mock(return_value=answer('新尝试回复')),clock=lambda:self.clock)
        def old(messages):
            self.clock += timedelta(seconds=181)
            state=self.chat.history(self.session['id'])['turns'][0]
            self.assertEqual(state['status'],'interrupted')
            self.assertTrue(state['can_retry'])
            self.assertEqual(replacement.retry(self.session['id'],'lease')['status'],'succeeded')
            return answer('迟到旧回复')
        self.chat.model=old
        result=self.chat.send(self.session['id'],'问题','lease')
        self.assertEqual(result['assistant_message']['content'],'新尝试回复')
        self.assertEqual(len(self.store.list_messages(self.session['id'])),2)

    def test_assistant_save_failure_rolls_back_and_is_not_reported_success(self):
        original=db.connection
        from contextlib import contextmanager
        @contextmanager
        def failing(*args,**kwargs):
            with original(*args,**kwargs) as conn:
                if kwargs.get('write'):
                    conn.execute("CREATE TEMP TRIGGER fail_assistant BEFORE INSERT ON main.messages WHEN NEW.role='assistant' BEGIN SELECT RAISE(ABORT,'injected'); END")
                yield conn
        with patch.object(db,'connection',failing):
            result=self.chat.send(self.session['id'],'保存失败问题','save-fail')
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['error']['code'],'save_failed')
        self.assertIsNone(result['assistant_message'])
        self.assertEqual(len(self.store.list_messages(self.session['id'])),1)
        self.assertEqual(self.chat.retry(self.session['id'],'save-fail')['status'],'succeeded')

    def test_model_success_and_all_status_saves_fail_is_safe(self):
        with patch.object(self.chat,'_finish',side_effect=OSError('FAKE-SECRET')):
            with self.assertRaises(ChatSaveError) as caught:self.chat.send(self.session['id'],'问题','db-fail')
        self.assertIn('已取得模型回复但保存失败',str(caught.exception))
        self.assertNotIn('FAKE-SECRET',str(caught.exception))
        self.assertEqual(len(self.store.list_messages(self.session['id'])),1)

    def test_chat_does_not_change_profile_summary_overview_or_candidates(self):
        watched=('profile','goals','holdings','summaries','overviews','overview_sources','profile_changes')
        def snapshot():
            with db.connection(self.database) as conn:return {t:[tuple(r) for r in conn.execute('SELECT * FROM '+t)] for t in watched}
        before=snapshot()
        self.chat.send(self.session['id'],'我想把预算改为900，请查询最新行情','no-tools')
        self.assertEqual(snapshot(),before)

    def test_http_chat_pipeline_get_is_read_only_and_raw_post_stays_raw(self):
        client=self.app.test_client();base='/api/conversations/'+self.session['id']
        with patch.object(ChatService,'_model',side_effect=self.model):
            sent=client.post(base+'/chat',json={'content':'HTTP问题','request_id':'http1'})
            self.assertEqual(sent.status_code,200)
            data=sent.json['data'];self.assertEqual(data['status'],'succeeded')
            self.assertEqual(client.get(base+'/chat').json['data']['turns'][0]['assistant_message'],data['assistant_message'])
            self.assertEqual(client.post(base+'/chat/http1/retry',json={}).json['data']['assistant_message']['id'],data['assistant_message']['id'])
            self.assertEqual(client.post(base+'/messages',json={'content':'仅保存原文'}).status_code,201)
            self.model.assert_called_once()
            for extra in ('api_key','system','role','model'):
                self.assertEqual(client.post(base+'/chat',json={'content':'x','request_id':'xx',extra:'forbidden'}).status_code,400)
            self.assertEqual(client.post(base+'/chat',json={'content':'不同问题','request_id':'http1'}).status_code,409)
        with patch('app.llm.load_config',side_effect=AssertionError('不得读密钥')),patch('app.llm.complete',side_effect=AssertionError('不得请求')):
            self.assertEqual(client.get(base+'/chat').status_code,200)
            self.assertEqual(client.get('/health').json,{'status':'ok'})

    def test_http_failure_persists_retry_status_without_exposing_exception(self):
        client=self.app.test_client();base='/api/conversations/'+self.session['id']+'/chat'
        with patch.object(ChatService,'_model',side_effect=RuntimeError('FAKE-SECRET')):
            with self.assertNoLogs(self.app.logger,level='ERROR'):
                result=client.post(base,json={'content':'问题','request_id':'http-fail'})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json['data']['status'],'failed')
        self.assertNotIn('FAKE-SECRET',result.get_data(as_text=True))
        self.assertEqual(client.get(base).json['data']['turns'][0]['status'],'failed')


class ChatMigrationTests(StorageCase):
    def old_database(self):
        old=self.root/'old';old.mkdir()
        for path in db.migration_files()[:2]:shutil.copy2(path,old/path.name)
        db.initialize(self.database,self.backups,migrations_dir=old)
        with patch.object(db,'MIGRATIONS_DIR',old):
            seed_demo(self.database)
            MDService(self.database,provider=MockProvider()).refresh(ASSETS[0].asset_id,'price')
        return old

    def snapshot(self,path):
        with closing(sqlite3.connect(path)) as conn:
            names=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='schema_migrations'")]
            return {t:conn.execute('SELECT * FROM '+t+' ORDER BY rowid').fetchall() for t in names}

    def test_v2_upgrade_preserves_sp_ca_md_and_valid_backup_reinitialize(self):
        self.old_database();before=self.snapshot(self.database)
        result=self.initialize()
        self.assertEqual(result['version'],3)
        self.assertEqual(self.snapshot(result['backup']),before)
        after=self.snapshot(self.database)
        self.assertEqual({k:after[k] for k in before},before)
        self.assertEqual(after['chat_requests'],[])
        with closing(sqlite3.connect(result['backup'])) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],2)
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertIsNone(self.initialize()['backup'])

    def test_v2_failed_migration_rolls_back_keeps_backup(self):
        old=self.old_database();before=self.snapshot(self.database)
        (old/'003_broken.sql').write_text("CREATE TABLE chat_partial (value TEXT);\nUPDATE profile SET investment_budget='999';\nINSERT INTO nonexistent VALUES (1);\n",encoding='utf-8',newline='\n')
        with self.assertRaises(SchemaError):db.initialize(self.database,self.backups,migrations_dir=old)
        self.assertEqual(self.snapshot(self.database),before)
        backup=next(self.backups.iterdir())
        self.assertEqual(self.snapshot(backup),before)
