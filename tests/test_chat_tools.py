"""T08：真实HTTP聊天编排配合离线模型/行情替身，不使用密钥或外网。"""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from threading import Event
from unittest.mock import Mock, patch

import httpx
from openai import DefaultHttpxClient, OpenAI

from app import create_app, db, llm
from app.archive import ArchiveService
from app.chat import ChatService
from app.chat_tools import ChatTools
from app.demo import budget, summary
from app.md import MDService
from app.md_provider import ASSETS, MockProvider
from app.storage import Store
from test_archive import completion as archive_completion
from test_storage import StorageCase


def tool_call(name, args, call_id="call-1"):
    return {"id": call_id, "type": "function", "function": {
        "name": name, "arguments": json.dumps(args, ensure_ascii=False) if not isinstance(args, str) else args}}


def calls(*items):
    return llm.CheckResult("", "fake-qwen", {"total_tokens": 10}, items)


def answer(text="已基于工具结果回答。", usage=None):
    return llm.CheckResult(text, "fake-qwen", {"total_tokens": 5} if usage is None else usage)


def amount(value, currency="CNY", source="user", goal_id=None):
    return {"source": source, "value": value, "currency": currency, "goal_id": goal_id}


def scenario(target="12000", saved="3000", monthly="1500"):
    return {"target": amount(target), "saved": amount(saved), "monthly": amount(monthly)}


class ChatToolTests(StorageCase):
    def setUp(self):
        super().setUp()
        self.initialize()
        self.client = self.app.test_client()
        self.session = self.store.create_conversation("T08虚构测试")
        self.tools = ChatTools.from_app(self.app)
        self.config = patch.object(llm, "load_config", return_value=llm.LLMConfig("https://unit.test/v1", "qwen-plus", "FAKE-SECRET"))
        self.config.start()
        self.addCleanup(self.config.stop)

    def post(self, question="测试问题", request_id="t08-1", session=None):
        response = self.client.post('/api/conversations/' + (session or self.session['id']) + '/chat',
                                    json={"content": question, "request_id": request_id})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json["data"]

    def tool_result(self, name, args):
        return self.tools.execute(name, json.dumps(args, ensure_ascii=False))

    def test_calculator_precision_rounding_zero_unknown_currency_and_sources(self):
        for target, saved, monthly, status, gap, months in (
            ("12000", "3000", "1500", "calculated", "9000.00", 6),
            ("12000.01", "3000", "1500", "calculated", "9000.01", 7),
            ("0", "0", "0", "reached", "0.00", 0),
            ("1", "2", "0", "reached", "0.00", 0),
            ("1", "0", "0", "unreachable", "1.00", None),
        ):
            with self.subTest(target=target, saved=saved, monthly=monthly):
                result = self.tool_result("calculate_savings", scenario(target, saved, monthly))
                self.assertEqual((result['status'], result['gap'], result['months']), (status, gap, months))
        unknown = self.tool_result("calculate_savings", scenario(saved=None))
        self.assertEqual(unknown['status'], 'missing_input')
        self.assertEqual(unknown['missing'], ['saved'])
        self.assertEqual(self.tool_result('calculate_savings', scenario('0', '0', None))['months'], 0)
        self.assertEqual(self.tool_result('calculate_savings', scenario(monthly=None))['missing'], ['monthly'])
        mismatch = scenario(); mismatch['saved']['currency'] = 'USD'
        self.assertEqual(self.tool_result('calculate_savings', mismatch)['status'], 'currency_mismatch')
        self.store.edit_profile([{'op':'set_finances','currency':'CNY','values':{'savings_budget':'1500','cash':'3000'}},
                                {'op':'set_goal','goal':{'id':'goal1','name':'虚构目标','target_amount':'12000','currency':'CNY','target_date':None}}], expected_version=0)
        args = scenario(saved=None)
        args['target'] = amount(None, None, 'sp_goal', 'goal1')
        args['monthly'] = amount(None, None, 'sp_savings_budget')
        result = self.tool_result('calculate_savings', args)
        self.assertEqual(result['status'], 'missing_input')  # 现金不冒充目标已存款。
        self.assertEqual(result['inputs']['target']['value'], '12000.00')
        self.assertEqual(result['inputs']['monthly']['sp_version'], 1)
        args['saved'] = amount('3000')
        self.assertEqual(self.tool_result('calculate_savings', args)['months'], 6)
        args['saved'] = amount(None, None, 'sp_savings_budget')
        self.assertFalse(self.tool_result('calculate_savings', args)['ok'])

    def test_strict_contract_unknown_tool_invalid_amounts_extra_fields_and_no_match(self):
        self.assertEqual(self.tool_result('execute_sql', {})['error']['code'], 'unknown_tool')
        invalid = [None, [], '{"keyword":"x","keyword":"y","limit":2}', '{"keyword":NaN,"limit":2}', 'x'*4001]
        for raw in invalid:
            self.assertEqual(self.tools.execute('search_history', raw)['error']['code'], 'invalid_arguments')
        for args in ({'keyword':'','limit':1}, {'keyword':'x','limit':True}, {'keyword':'x','limit':6},
                     {'keyword':'x','limit':1,'sql':'SELECT *'}, {'keyword':'x'*81,'limit':1}):
            self.assertFalse(self.tool_result('search_history',args)['ok'])
        self.assertFalse(self.tool_result('search_history', {'keyword': "%' OR 1=1 --", 'limit':5})['matched'])
        for value in ('1,500', '-1', 'NaN', 'Infinity', '0.001', '1e3', 1.5, True, '900万元'):
            self.assertFalse(self.tool_result('calculate_savings', scenario(monthly=value))['ok'])
        for unit in ('人民币', 'cny', 123, 'CNYY'):
            args=scenario(); args['monthly']['currency']=unit
            self.assertFalse(self.tool_result('calculate_savings', args)['ok'])
        self.assertFalse(self.tool_result('query_market', {'asset_id':'DEMO001','metric':'price'})['ok'])
        self.assertFalse(self.tool_result('query_market', {'asset_id':ASSETS[0].asset_id,'metric':'nav'})['ok'])

    def test_history_latest_range_versions_literal_keywords_and_bounded_excerpts(self):
        for index in range(5):
            session = self.store.create_conversation('长摘要' + str(index))
            message = self.store.add_message(session['id'], 'user', '原消息')
            self.store.save_summary(session['id'], message['id'], message['id'], summary(reasons=['旧的理由']))
            self.store.save_summary(session['id'], message['id'], message['id'], summary(
                reasons=['r' * 1900 + '匹配关键词最新理由'] * 30, facts=['其他事实' * 200] * 30))
        result = self.tool_result('search_history', {'keyword':'匹配关键词', 'limit':5})
        self.assertTrue(result['ok']); self.assertEqual(len(result['records']), 5)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), 6000)
        self.assertTrue(all('匹配关键词' in r['content_excerpt']['reasons'][0] for r in result['records']))
        self.assertTrue(all(r['version'] == 2 for r in result['records']))
        self.assertFalse(self.tool_result('search_history', {'keyword':'旧的理由', 'limit':5})['matched'])

    def test_history_matches_only_body_entries_including_literal_field_names(self):
        message = self.store.add_message(self.session['id'], 'user', '原文')
        saved = self.store.save_summary(self.session['id'], message['id'], message['id'],
                                        summary(reasons=['交通费用降低']))
        result = self.tool_result('search_history', {'keyword':'交通', 'limit':1})
        self.assertEqual(result['records'][0]['id'], saved['id'])
        self.assertIn('交通费用降低', result['records'][0]['content_excerpt']['reasons'])
        for keyword in ('confirmed', 'unresolved'):
            self.assertFalse(self.tool_result('search_history', {'keyword':keyword, 'limit':5})['matched'])
        updated = self.store.save_summary(self.session['id'], message['id'], message['id'],
            summary(facts=['正文中确实提到了 confirmed 和 unresolved'], reasons=['交通费用降低']))
        for keyword in ('confirmed', 'unresolved'):
            result = self.tool_result('search_history', {'keyword':keyword, 'limit':1})
            self.assertEqual(result['records'][0]['id'], updated['id'])
            self.assertIn(keyword, result['records'][0]['content_excerpt']['facts'][0])

    def test_history_applies_limit_after_body_match_and_decodes_json_text(self):
        message = self.store.add_message(self.session['id'], 'user', '原文')
        saved = self.store.save_summary(self.session['id'], message['id'], message['id'],
            summary(facts=['confirmed 是这条正文的一部分', '文字含换行\n与引号"和百分号%_']))
        for index in range(3):
            session = self.store.create_conversation('更新但正文不匹配' + str(index))
            other = self.store.add_message(session['id'], 'user', '原文')
            self.store.save_summary(session['id'], other['id'], other['id'], summary(reasons=['交通费用降低']))
        for keyword in ('confirmed', '换行\n与引号"', '%_'):
            result = self.tool_result('search_history', {'keyword':keyword, 'limit':1})
            self.assertEqual([r['id'] for r in result['records']], [saved['id']])

    def test_http_savings_unavailable_replaces_model_guess_and_replays_saved_reason(self):
        mismatch = scenario(); mismatch['saved']['currency'] = 'USD'
        currency_unknown = scenario(); currency_unknown['monthly']['currency'] = None
        inputs = [(scenario(monthly=None), '月存入金额'),
                  (scenario(monthly='0'), '月存入为零'),
                  (mismatch, '币种不一致'), (currency_unknown, '月存入币种')]
        for index, (args, reason) in enumerate(inputs):
            with self.subTest(reason=reason):
                session = self.store.create_conversation('无可用月数' + str(index))
                with patch.object(llm, 'complete', side_effect=[
                    calls(tool_call('calculate_savings', args)), answer('预计 6 个月')]) as model:
                    done = self.post('算一下储蓄时间', 'unavailable-' + str(index), session['id'])
                    self.assertEqual(done['status'], 'succeeded')
                    reply = done['assistant_message']['content']
                    self.assertIn(reason, reply)
                    self.assertIn('无法给出月数', reply)
                    self.assertNotIn('预计 6 个月', reply)
                    again = self.post('算一下储蓄时间', 'unavailable-' + str(index), session['id'])
                    self.assertEqual(again, done)
                    self.assertEqual(model.call_count, 2)
                self.assertEqual(Store(self.database).list_messages(session['id'])[-1]['content'], reply)

    def test_http_savings_success_and_reached_have_deterministic_months(self):
        for index, args, expected in ((0, scenario(), '6 个月'), (1, scenario('0','0',None), '0 个月')):
            session = self.store.create_conversation('有效月数' + str(index))
            with patch.object(llm, 'complete', side_effect=[calls(tool_call('calculate_savings',args)),answer('模型解释')]):
                done = self.post('计算储蓄时间', 'available-' + str(index), session['id'])
            reply = done['assistant_message']['content']
            self.assertIn(expected, reply)
            if index:
                self.assertIn('已达到', reply)
            else:
                self.assertIn('9000.00', reply)
            self.assertNotIn('无法给出月数', reply)

    def test_http_mixed_results_keep_answers_and_expose_savings_status_to_model(self):
        for index, name, args in ((0, 'search_history', {'keyword':'交通', 'limit':1}),
                                 (1, 'query_market', {'asset_id':ASSETS[0].asset_id, 'metric':'price'})):
            session = self.store.create_conversation('混合工具' + str(index))
            message = self.store.add_message(session['id'], 'user', '历史原文')
            saved = self.store.save_summary(session['id'],message['id'],message['id'],summary(reasons=['交通费用降低']))
            seen = []
            def model(config, messages, **kwargs):
                seen.append(deepcopy(messages))
                if len(seen) == 1:
                    return calls(tool_call('calculate_savings',scenario(monthly=None),'calc'),tool_call(name,args,'other'))
                result = json.loads(next(m['content'] for m in messages if m.get('tool_call_id') == 'calc'))
                self.assertEqual(result['status'], 'missing_input')
                self.assertIsNone(result['months'])
                self.assertIn('无法给出月数', result['message'])
                return answer('其他查询已经完成，预计 6 个月')
            with patch.object(llm,'complete',side_effect=model):
                done = self.post('查询并计算', 'mixed-' + str(index), session['id'])
            self.assertEqual(done['status'], 'succeeded')
            reply = done['assistant_message']['content']
            self.assertIn('其他查询已经完成', reply)
            self.assertIn('储蓄计算（后端结果）', reply)
            self.assertIn('月存入金额', reply)
            self.assertIn('无法给出月数', reply)
            self.assertIn(saved['id'] if name == 'search_history' else 'CNY/股', reply)

    def test_http_empty_failed_or_skipped_other_tools_do_not_preserve_guessed_months(self):
        cases = [('history-empty', 'search_history', {'keyword':'无匹配历史', 'limit':1}, 4),
                 ('market-failed', 'query_market', {'asset_id':ASSETS[0].asset_id, 'metric':'price'}, 4),
                 ('history-skipped', 'search_history', {'keyword':'历史', 'limit':1}, 1),
                 ('market-skipped', 'query_market', {'asset_id':ASSETS[0].asset_id, 'metric':'price'}, 1)]
        for request_id, name, args, limit in cases:
            with self.subTest(request_id=request_id):
                self.app.config['CHAT_TOOL_CALLS'] = limit
                session = self.store.create_conversation(request_id)
                seen = []
                def model(config, messages, **kwargs):
                    seen.append(deepcopy(messages))
                    if len(seen) == 1:
                        return calls(tool_call('calculate_savings',scenario(monthly=None),'calc'), tool_call(name,args,'other'))
                    other = json.loads(messages[-1]['content'])
                    if limit == 1:
                        self.assertEqual(other['error']['code'], 'tool_limit')
                    elif name == 'search_history':
                        self.assertEqual(other['records'], [])
                    else:
                        self.assertIsNone(other['data'])
                        self.assertEqual(other['refresh_status'], 'failed')
                    return answer('预计 6 个月')
                with patch.object(MockProvider,'fetch',side_effect=RuntimeError('离线模拟失败')), patch.object(llm,'complete',side_effect=model) as complete:
                    done = self.post('查询并计算',request_id,session['id'])
                    self.assertEqual(done['status'], 'succeeded')
                    reply = done['assistant_message']['content']
                    self.assertIn('月存入金额', reply)
                    self.assertIn('无法给出月数', reply)
                    self.assertNotIn('预计 6 个月', reply)
                    self.assertEqual(self.post('查询并计算',request_id,session['id']), done)
                    self.assertNotIn('6 个月', reply)
                    self.assertEqual(complete.call_count, 2)
                history = self.client.get('/api/conversations/'+session['id']+'/chat').json['data']
                self.assertEqual(history['messages'][-1]['content'], reply)
                self.assertEqual(history['turns'][0]['assistant_message'], done['assistant_message'])

    def test_http_stale_market_data_remains_usable_with_savings_missing_input(self):
        clock = datetime(2026,11,4,tzinfo=timezone.utc)
        provider = MockProvider()
        market = MDService(self.database,provider=provider,clock=lambda:clock,ttl_seconds=1)
        cached = market.refresh(ASSETS[0].asset_id, 'price')['data']
        clock += timedelta(seconds=2)
        provider.fetch = Mock(side_effect=RuntimeError('离线模拟刷新失败'))
        tools = ChatTools(self.database,market=market)
        with patch.object(ChatTools,'from_app',return_value=tools), patch.object(llm,'complete',side_effect=[
            calls(tool_call('calculate_savings',scenario(monthly=None),'calc'),
                  tool_call('query_market',{'asset_id':ASSETS[0].asset_id,'metric':'price'},'quote')),
            answer('旧行情仍可参考，请注意过期。')]):
            done = self.post('查看旧行情并计算')
        self.assertEqual(done['status'], 'succeeded')
        reply = done['assistant_message']['content']
        self.assertIn('旧行情仍可参考', reply)
        self.assertIn(cached['value'], reply)
        self.assertIn('缓存 stale', reply)
        self.assertIn('储蓄计算（后端结果）', reply)
        self.assertIn('月存入金额', reply)
        self.assertIn('无法给出月数', reply)
        history = self.client.get('/api/conversations/'+self.session['id']+'/chat').json['data']
        self.assertEqual(history['messages'][-1]['content'], reply)

    def test_http_savings_recalculation_success_clears_resolved_missing_state(self):
        seen = []
        def model(config,messages,**kwargs):
            seen.append(deepcopy(messages))
            if len(seen) == 1:
                return calls(tool_call('search_history',{'keyword':'没有匹配历史','limit':1},'history-empty'),
                             tool_call('calculate_savings',scenario(monthly=None),'incomplete'))
            if len(seen) == 2:
                self.assertIn('无法给出月数', json.loads(messages[-1]['content'])['message'])
                return calls(tool_call('calculate_savings',scenario(),'complete'))
            self.assertEqual(json.loads(messages[-1]['content'])['months'], 6)
            return answer('已根据补齐的输入计算完成。')
        with patch.object(llm,'complete',side_effect=model):
            done = self.post('补齐参数后计算')
        self.assertEqual(done['status'], 'succeeded')
        reply = done['assistant_message']['content']
        self.assertIn('6 个月', reply)
        self.assertIn('9000.00', reply)
        self.assertNotIn('未知', reply)
        self.assertNotIn('无法给出月数', reply)

    def test_http_archive_confirm_current_sp_and_history_outside_overview(self):
        self.store.edit_profile(budget('800'), expected_version=0)
        with patch.object(llm, 'complete', return_value=answer('请归档后核对候选。')):
            turn=self.post('把每月投资预算改为900元，因为日常支出减少了')
        base='/api/conversations/'+self.session['id']
        with patch.object(ArchiveService, '_model', side_effect=lambda batch: archive_completion(batch,candidate=True)):
            status=self.client.get(base+'/archive').json['data']
            archived=self.client.post(base+'/archive',json=status['next_range']).json['data']
        self.assertEqual(archived['status'],'succeeded')
        self.assertEqual(self.store.get_profile()['investment_budget'],'800.00')
        candidate=self.store.get_change(archived['change_ids'][0])
        self.assertEqual(candidate['source_message_id'],turn['user_message']['id'])
        self.assertEqual(self.client.post('/api/profile-changes/'+candidate['id']+'/confirm',json={}).status_code,200)
        self.assertEqual(self.store.get_profile()['investment_budget'],'900.00')
        historical=self.store.create_conversation('Overview未收录的旧决定')
        msg=self.store.add_message(historical['id'],'user','虚构旧决定')
        missing=self.store.save_summary(historical['id'],msg['id'],msg['id'],summary(reasons=['缩短通勤是当时降低开支的理由'],plans=['当时考虑1000，未确认']))
        self.assertNotIn(missing['id'],self.store.get_overview()['summary_ids'])
        new=self.store.create_conversation('新会话')
        snapshots=[]
        def current(config,messages,**kwargs):
            sp=json.loads(messages[1]['content'].split('\n',1)[1])['current_confirmed_sp']
            self.assertEqual(sp['investment_budget'],'900.00')
            self.assertEqual(sum(m.get('content')=='我现在的预算是多少？' for m in messages),1)
            return answer('当前900元，来自已确认SP。')
        with patch.object(llm,'complete',side_effect=current) as model:
            self.assertEqual(self.post('我现在的预算是多少？','current',new['id'])['status'],'succeeded')
            self.assertEqual(model.call_count,1)
        def history(config,messages,**kwargs):
            snapshots.append(deepcopy(messages))
            if len(snapshots)==1:
                return calls(tool_call('search_history',{'keyword':'通勤','limit':3}))
            result=json.loads(messages[-1]['content'])
            self.assertEqual(result['records'][0]['id'],missing['id'])
            self.assertEqual(result['records'][0]['conversation_id'],historical['id'])
            self.assertIn('缩短通勤',result['records'][0]['content_excerpt']['reasons'][0])
            return answer('历史理由见摘要'+missing['id']+'；当前预算900。')
        with patch.object(llm,'complete',side_effect=history):
            done=self.post('之前为什么调整？请查通勤的历史理由','why',new['id'])
            self.assertEqual(done['status'],'succeeded')
        self.assertEqual(len(self.store.list_messages(new['id'])),4)
        self.assertEqual([m['role'] for m in self.store.list_messages(new['id'])],['user','assistant']*2)

    def test_http_tool_ids_transient_messages_usage_replay_reconnect_and_archive(self):
        seen=[]
        def model(config,messages,**kwargs):
            seen.append(deepcopy(messages))
            # 模型/工具等待期间可以获取另一写连接。
            with db.connection(self.database,write=True) as conn: conn.execute('UPDATE profile SET version=version')
            if len(seen)==1:
                return calls(tool_call('calculate_savings',scenario(),'calc-id'), tool_call('search_history',{'keyword':'无记录','limit':1},'history-id'))
            self.assertEqual([m['tool_call_id'] for m in messages if m['role']=='tool'],['calc-id','history-id'])
            calc=json.loads(messages[-2]['content']); self.assertEqual((calc['gap'],calc['months']),('9000.00',6))
            return answer('差额9000元，6个月；不计收益或通胀。')
        with patch.object(llm,'complete',side_effect=model) as complete:
            done=self.post('目标12000元，已存3000元，每月1500元，需要多久？')
            again=self.post('目标12000元，已存3000元，每月1500元，需要多久？')
            self.assertEqual(done,again)
            self.assertEqual(complete.call_count,2)
            self.assertEqual(done['usage']['total_tokens'],15)
        self.assertEqual(len(Store(self.database).list_messages(self.session['id'])),2)
        self.assertEqual(create_app({'DATABASE_PATH':self.database}).test_client().get('/api/conversations/'+self.session['id']+'/chat').json['data']['turns'][0]['id'],done['id'])
        with patch.object(ArchiveService,'_model',side_effect=lambda batch: archive_completion(batch)) as archive:
            result=ArchiveService(self.database).generate(self.session['id'])
        self.assertEqual(result['status'],'succeeded')
        batch=json.loads(archive.call_args.args[0][1]['content'])['messages']
        self.assertEqual([m['role'] for m in batch],['user','assistant'])

    def test_md_cache_stale_rate_limit_missing_and_no_sp_ca_changes(self):
        clock=datetime(2026,11,3,tzinfo=timezone.utc)
        provider=MockProvider(); provider.fetch=Mock(wraps=provider.fetch)
        market=MDService(self.database,provider=provider,clock=lambda:clock,ttl_seconds=10)
        tools=ChatTools(self.database,market=market)
        watched=('profile','goals','holdings','summaries','overviews','overview_sources','profile_changes','archive_requests')
        def snapshot():
            with db.connection(self.database) as conn:
                return {t:[tuple(r) for r in conn.execute('SELECT * FROM '+t)] for t in watched}
        before=snapshot()
        for asset in ASSETS:
            args=json.dumps({'asset_id':asset.asset_id,'metric':asset.metric})
            first=tools.execute('query_market',args)
            second=tools.execute('query_market',args)
            self.assertEqual((second['cache_status'],second['refresh_status']),('fresh','not_needed'))
            self.assertEqual(first['data']['unit'],asset.unit)
            self.assertEqual(first['data']['currency'],asset.currency)
            self.assertEqual(first['display_unit'], '点' if asset.asset_type=='index' else 'CNY/股' if asset.asset_type=='stock' else 'CNY/份')
            self.assertTrue(first['data']['is_simulated'])
        self.assertEqual(provider.fetch.call_count,3)
        clock+=timedelta(seconds=10)
        failure=RuntimeError('FAKE-SECRET'); failure.response=Mock(status_code=429)
        provider.fetch.side_effect=failure
        result=tools.execute('query_market',json.dumps({'asset_id':ASSETS[0].asset_id,'metric':'price'}))
        self.assertEqual(result['cache_status'],'stale')
        self.assertEqual(result['error']['code'],'rate_limit')
        self.assertNotIn('FAKE-SECRET',json.dumps(result))
        with patch.object(ChatTools,'from_app',return_value=tools),patch.object(llm,'complete',side_effect=[
            calls(tool_call('query_market',{'asset_id':ASSETS[0].asset_id,'metric':'price'})),answer('示例旧值')]):
            final=self.post('查虚构演示股票价格')
        self.assertIn('限流',final['assistant_message']['content'])
        self.assertIn('CNY/股',final['assistant_message']['content'])
        self.assertIn('缓存 stale',final['assistant_message']['content'])
        self.assertEqual(snapshot(),before)
        with db.connection(self.database,write=True) as conn: conn.execute('DELETE FROM md_cache')
        missing=tools.execute('query_market',json.dumps({'asset_id':ASSETS[0].asset_id,'metric':'price'}))
        self.assertFalse(missing['ok']); self.assertIsNone(missing['data'])

    def test_md_result_metadata_reaches_model_and_app_provider_is_used(self):
        for provider_id,asset_id,metric in [('mock-fixed-v1',ASSETS[0].asset_id,'price'),('akshare-real-daily-v1','cn:index:SHS:000001','index_level')]:
            self.app.config['MD_PROVIDER_ID']=provider_id
            new=self.store.create_conversation(provider_id)
            market=ChatTools.from_app(self.app).market
            provider=market.provider
            # 真实模式同样离线：只替换fetch，保留真实目录/单位/标记与缓存路径。
            def fetch(asset,metric):
                return {'asset_id':asset.asset_id,'metric':metric,'value':'123.12345678',
                    'currency':asset.currency,'unit':asset.unit,'provider_id':provider.provider_id,
                    'is_simulated':provider.is_simulated,'data_time_kind':'date','data_time':'2026-10-30'}
            provider.fetch=Mock(side_effect=fetch)
            tools=ChatTools(self.database,market=market)
            def model(config,messages,**kwargs):
                if messages[-1]['role']!='tool': return calls(tool_call('query_market',{'asset_id':asset_id,'metric':metric}))
                quote=json.loads(messages[-1]['content'])
                self.assertEqual(quote['data']['provider_id'],provider_id)
                self.assertEqual(quote['data']['data_time'],'2026-10-30')
                self.assertEqual(quote['data']['is_simulated'],provider_id=='mock-fixed-v1')
                self.assertEqual(quote['data']['value'],'123.12345678')
                return answer('来源与日期见工具数据。')
            with patch.object(ChatTools,'from_app',return_value=tools),patch.object(llm,'complete',side_effect=model):
                self.assertEqual(self.post('查行情',provider_id,new['id'])['status'],'succeeded')
            provider.fetch.assert_called_once()

    def test_errors_are_tool_results_and_response_warning_not_fake_success(self):
        cases=[tool_call('unknown',{},'unknown'),tool_call('search_history',{'keyword':'x','limit':False},'invalid'),
               tool_call('search_history','invalid JSON','json')]
        for index,call in enumerate(cases):
            session=self.store.create_conversation('错误'+str(index))
            def model(config,messages,**kwargs):
                if messages[-1]['role']!='tool': return calls(call)
                self.assertFalse(json.loads(messages[-1]['content'])['ok'])
                return answer('未查到，请调整问题。')
            with patch.object(llm,'complete',side_effect=model):
                result=self.post('无效工具',str(index),session['id'])
            self.assertEqual(result['status'],'succeeded')
            self.assertIn('工具提示',result['assistant_message']['content'])
        with patch.object(self.tools.market,'query',side_effect=RuntimeError('FAKE-SECRET')):
            result=self.tools.execute('query_market',json.dumps({'asset_id':ASSETS[0].asset_id,'metric':'price'}))
        self.assertEqual(result['error']['code'],'tool_failed')
        self.assertNotIn('FAKE-SECRET',json.dumps(result))

    def test_tool_limits_force_final_and_repeated_ids_fail_then_explicit_retry(self):
        self.app.config.update(CHAT_TOOL_ROUNDS=1,CHAT_TOOL_CALLS=1)
        attempted=[]
        def model(config,messages,**kwargs):
            attempted.append(kwargs['tool_choice'])
            if len(attempted)==1: return calls(tool_call('search_history',{'keyword':'x','limit':1},'a'),tool_call('search_history',{'keyword':'x','limit':1},'b'))
            self.assertEqual(json.loads(messages[-1]['content'])['error']['code'],'tool_limit')
            return calls(tool_call('search_history',{'keyword':'x','limit':1},'c'))
        with patch.object(llm,'complete',side_effect=model),patch.object(ChatTools,'execute',wraps=self.tools.execute) as tool:
            failed=self.post('查询多个历史')
            self.assertEqual(failed['status'],'failed')
            self.assertEqual(failed['error']['code'],'tool_limit')
            self.assertEqual(tool.call_count,1)
        self.assertEqual(attempted,['auto','none'])
        self.assertEqual(len(self.store.list_messages(self.session['id'])),1)
        with patch.object(llm,'complete',return_value=answer('重试成功')):
            done=self.client.post('/api/conversations/'+self.session['id']+'/chat/t08-1/retry',json={}).json['data']
        self.assertEqual(done['user_message']['id'],failed['user_message']['id'])
        self.app.config.update(CHAT_TOOL_ROUNDS=2,CHAT_TOOL_CALLS=4)
        session=self.store.create_conversation('重复工具ID')
        with patch.object(llm,'complete',side_effect=[calls(tool_call('search_history',{'keyword':'x','limit':1})),calls(tool_call('search_history',{'keyword':'x','limit':1}))]):
            self.assertEqual(self.post('查询','repeat',session['id'])['error']['code'],'response')

    def test_missing_round_usage_not_estimated_and_context_overflow_safe(self):
        with patch.object(llm,'complete',side_effect=[llm.CheckResult('',None,None,(tool_call('search_history',{'keyword':'x','limit':1}),)),answer()]):
            self.assertIsNone(self.post()['usage'])
        session=self.store.create_conversation('上下文超限')
        huge=calls(*[tool_call('search_history',{'keyword':'x'*80,'limit':5},'call-'+str(i)) for i in range(8)])
        with patch.object(ChatTools,'execute',return_value={'ok':True,'data':'x'*5900}),patch.object(llm,'complete',return_value=huge):
            result=self.post('查询','overflow',session['id'])
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['error']['code'],'tool_limit')

    def test_default_tool_pipeline_concurrent_replay_no_lock_and_failure_retry(self):
        started, release = Event(), Event()
        count = 0
        def query(args):
            with db.connection(self.database, write=True) as conn:
                conn.execute('UPDATE profile SET version=version')
            started.set()
            self.assertTrue(release.wait(5))
            return {'ok': True, 'matched': False, 'records': []}
        def model(config, messages, **kwargs):
            nonlocal count
            count += 1
            if count == 1: return calls(tool_call('search_history', {'keyword':'测试', 'limit':1}))
            raise llm.LLMError('network', 'FAKE-SECRET')
        with patch.object(ChatTools, 'search_history', side_effect=query), patch.object(llm, 'complete', side_effect=model):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self.post)
                try:
                    self.assertTrue(started.wait(5))
                    with self.app.test_client() as other:
                        path = '/api/conversations/'+self.session['id']+'/chat'
                        repeated = other.post(path, json={'content':'测试问题','request_id':'t08-1'})
                        self.assertEqual(repeated.json['data']['status'], 'running')
                        self.assertEqual(other.post(path, json={'content':'插队','request_id':'other'}).status_code, 409)
                    self.assertEqual(count, 1)
                finally:
                    release.set()
                failed = future.result()
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['error']['code'], 'network')
        self.assertNotIn('FAKE-SECRET', json.dumps(failed))
        self.assertIsNone(failed['assistant_message'])
        with patch.object(llm, 'complete', return_value=answer('重试后的真实保存回复')):
            done = self.client.post('/api/conversations/'+self.session['id']+'/chat/t08-1/retry', json={}).json['data']
        self.assertEqual(done['user_message']['id'], failed['user_message']['id'])
        self.assertEqual(len(self.store.list_messages(self.session['id'])), 2)


class ToolSDKTests(StorageCase):
    def test_sdk_tool_protocol_sanitization_and_text_paths(self):
        self.initialize()
        requests=[]
        key='FAKE-KEY-NEVER-LOG'
        def handle(request):
            body=json.loads(request.content);requests.append(body)
            if len(requests)==1:
                message={'role':'assistant','content':None,'tool_calls':[tool_call('search_history',{'keyword':key,'limit':1})]}
            else:
                self.assertEqual(body['messages'][-1]['tool_call_id'],'call-1')
                self.assertEqual(body['messages'][-2]['tool_calls'][0]['id'],'call-1')
                self.assertNotIn(key,request.content.decode())
                message={'role':'assistant','content':'完成'+key}
            return httpx.Response(200,json={'choices':[{'message':message}],'model':'qwen-plus','usage':{'total_tokens':4}})
        config=llm.LLMConfig('https://unit.test/compatible-mode/v1','qwen-plus',key)
        client=OpenAI(api_key=key,base_url=config.base_url,max_retries=0,
                      http_client=DefaultHttpxClient(transport=httpx.MockTransport(handle),follow_redirects=False))
        self.addCleanup(client.close)
        tools=ChatTools(self.database).schemas()
        one=llm.complete(config,[{'role':'user','content':'查询'}],client=client,tools=tools)
        self.assertEqual(one.reply,'');self.assertEqual(len(one.tool_calls),1)
        self.assertNotIn(key,one.tool_calls[0]['function']['arguments'])
        two=llm.complete(config,[{'role':'assistant','content':None,'tool_calls':list(one.tool_calls)},
                                 {'role':'tool','tool_call_id':'call-1','content':key}],tools=tools,tool_choice='none',client=client)
        self.assertNotIn(key,two.reply)
        self.assertFalse(requests[0]['enable_thinking'])
        self.assertEqual(requests[1]['tool_choice'],'none')
        invalid=[{'id':'invalid id','type':'function','function':{'name':'search_history','arguments':'{}'}},
                 tool_call('search_history','x'*4001)]
        for call in invalid:
            with self.assertRaises(llm.LLMError):llm._parse_response({'choices':[{'message':{'tool_calls':[call]}}]},key,allow_tools=True)
        with self.assertRaises(llm.LLMError):llm._parse_response({'choices':[{'message':{'tool_calls':[tool_call('search_history',{})]}}]},key)
