"""模型候选契约：替身只验证结构/来源/确认流程，不验证真实模型语义能力。"""

import json
import shutil
import subprocess
import unittest
from uuid import uuid4

from app.archive import ArchiveService
from app.chat import ChatService
from app.config import Config
from app.demo import budget, summary
from app.llm import CheckResult
from test_storage import StorageCase


class CandidateContractTests(StorageCase):
    def setUp(self):
        super().setUp()
        self.initialize()
        self.store.edit_profile(budget('800'), expected_version=0)

    def session(self, texts):
        session = self.store.create_conversation('虚构候选契约')
        chat = ChatService(self.database, model=lambda _:CheckResult('虚构回复','fake',None))
        turns = [chat.send(session['id'], text, uuid4().hex) for text in texts]
        return session['id'], turns

    @staticmethod
    def proposal(turn, changes):
        return {'source_message_id':turn['user_message']['id'], 'evidence':turn['user_message']['content'], 'operations':changes}

    def archive(self, cid, candidates):
        def model(messages):
            rows = json.loads(messages[1]['content'])['messages']
            content = {key:[] for key in summary()}
            content['facts'] = [{'text':'虚构用户已讨论财务安排','message_ids':[rows[0]['id']]}]
            return CheckResult(json.dumps({'summary':content,'candidates':candidates}), 'fake', None)
        return ArchiveService(self.database, model=model).generate(cid)

    def test_colloquial_intent_and_risk_need_no_fixed_phrases(self):
        cid, turns = self.session(['这月宽裕点，九百块钱留给投资，就这么定了，人民币。',
                                   '我能承受的波动不高不低，按中等风险记录吧。'])
        result = self.archive(cid, [self.proposal(turns[0], budget('900')),
                                    self.proposal(turns[1], [{'op':'set_risk','value':'medium'}])])
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(result['candidate_count'],2)
        self.assertEqual(self.store.get_profile()['investment_budget'],'800.00')
        self.store.confirm_change_batch(result['id'],result['change_ids'])
        self.assertEqual(self.store.get_profile()['investment_budget'],'900.00')
        self.assertEqual(self.store.get_profile()['risk_preference'],'medium')

    def test_normalized_wanyuan_value_remains_pending_until_explicit_confirmation(self):
        cid, turns = self.session(['投资预算就900吧，单位是万元，币种CNY，这次确定了。'])
        result = self.archive(cid,[self.proposal(turns[0],budget('9000000'))])
        candidate = self.store.get_change(result['change_ids'][0])
        self.assertEqual(candidate['operations'][0]['values']['investment_budget'],'9000000.00')
        self.assertEqual(candidate['source_message_id'],turns[0]['user_message']['id'])
        self.assertEqual(self.store.get_profile()['investment_budget'],'800.00')
        self.store.confirm_change(candidate['id'])
        self.assertEqual(self.store.get_profile()['investment_budget'],'9000000.00')

    def test_invalid_candidate_contract_or_source_does_not_block_valid_results(self):
        other, outside = self.session(['其他会话的用户消息'])
        invalidators = [
            lambda p,t:p.update(source_message_id=t['assistant_message']['id']),
            lambda p,t:p.update(source_message_id=outside[0]['user_message']['id']),
            lambda p,t:p.update(evidence='并非保存的原文'),
            lambda p,t:p.update(source_message_id='missing'),
            lambda p,t:p.update(operations=[{'op':'arbitrary_sql','value':'DROP TABLE profile'}]),
            lambda p,t:p['operations'][0]['values'].update(nonexistent='900'),
            lambda p,t:p['operations'][0].update(currency='usd'),
            lambda p,t:p['operations'][0].update(currency='USD'),
            lambda p,t:p.update(operations=[{'op':'set_goal','goal':{'id':'goal','name':'目标','target_amount':'1','currency':'CNY','target_date':None}}]),
        ]
        for raw in ('900万','9,000','900元','NaN','Infinity','1e6','-1','900.001',900,900.0,True,{'value':'900'}):
            invalidators.append(lambda p,t,raw=raw:p['operations'][0]['values'].update(investment_budget=raw))
        for invalid in invalidators:
            with self.subTest(invalid=invalid):
                cid, turns = self.session(['九百，投资，已经决定。','这三千留作现金。'])
                bad = self.proposal(turns[0],budget('900'))
                invalid(bad,turns[0])
                good = self.proposal(turns[1],[{'op':'set_finances','currency':'CNY','values':{'cash':'3000'}}])
                result = self.archive(cid,[bad,good])
                self.assertEqual(result['status'],'succeeded')
                self.assertEqual(result['candidate_count'],1)
                candidate = self.store.get_change(result['change_ids'][0])
                self.assertEqual(candidate['source_message_id'],turns[1]['user_message']['id'])
                self.assertEqual(candidate['operations'][0]['values'],{'cash':'3000.00'})
                self.assertEqual(len(self.store.list_summaries(cid)),1)
        self.assertEqual(self.store.get_profile()['investment_budget'],'800.00')

    def test_no_change_filter_and_first_valid_candidate_per_source(self):
        cid, turns = self.session(['沿用原来的安排。','新的投资安排就九百。'])
        bad = self.proposal(turns[1],budget('900万'))
        good = self.proposal(turns[1],budget('900'))
        result = self.archive(cid,[self.proposal(turns[0],budget('800')),bad,good,good])
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(result['candidate_count'],1)
        self.assertEqual(self.store.get_change(result['change_ids'][0])['source_message_id'],turns[1]['user_message']['id'])

    def test_provenance_validation_does_not_claim_semantic_correctness(self):
        # 模型若错误提取了假设，来源仍然真实，后端不能靠黑名单证明语义。
        cid, turns = self.session(['如果以后改成900万元，现在只是举个例子。'])
        result = self.archive(cid,[self.proposal(turns[0],budget('900'))])
        self.assertEqual(result['candidate_count'],1)
        self.assertEqual(self.store.get_profile()['investment_budget'],'800.00')
        self.store.reject_change(result['change_ids'][0])
        self.assertEqual(self.store.get_profile()['investment_budget'],'800.00')


class CandidateJavaScriptTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node不可用；尚未运行DOM替身交互测试')
    def test_operation_feedback_dialog_content_and_cancel_with_dom_double(self):
        result = subprocess.run([shutil.which('node'), str(Config.PROJECT_ROOT/'tests/candidate_ui_check.js'),
                                 str(Config.PROJECT_ROOT/'app/static/app.js')],
                                cwd=Config.PROJECT_ROOT,capture_output=True,text=True,encoding='utf8',timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
