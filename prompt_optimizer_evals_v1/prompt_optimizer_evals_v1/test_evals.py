"""Offline checks only: no credentials, network access, or paid model calls."""
import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import run_evals as runner
import run_matrix as matrix


def temporary_directory():
    return tempfile.TemporaryDirectory(dir=runner.BASE)


class FakeClient:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        value = next(self.outputs)
        if isinstance(value,Exception):
            raise value
        if isinstance(value,dict):
            return SimpleNamespace(**value)
        return SimpleNamespace(status='completed',output_text=value,id='fake',model='offline-test',usage=None)


def judgment(executed=False):
    return {'fidelity':{'left':{'pass':True,'violations':[]},'right':{'pass':True,'violations':[]}},'rewrite_preference':'tie','execution_preference':'tie' if executed else 'not_evaluated','evidence':[{'side':'both','quote':'AI','reason':'两侧输入包含相同的面试任务。'}],'review_required':False}


class EvaluationTests(unittest.TestCase):
    def test_dataset_preserves_baseline_ids_and_provenance(self):
        cases = runner.load_suite(runner.BASE/'evals.json',runner.BASE/'sources.json')
        ids = {case['id'] for case in cases}
        self.assertTrue({f'E{number:02d}' for number in range(1,21)} <= ids)
        self.assertEqual({case['split'] for case in cases},{'dev','test'})
        self.assertGreater(sum(case['execution']['enabled'] for case in cases),0)
        sources = runner.read_json(runner.BASE/'sources.json')['cases']
        self.assertEqual({item['case_id'] for item in sources},ids)
        for item in sources:
            self.assertIsInstance(item['origin'],str)
            self.assertTrue(item['origin'].strip())
            self.assertTrue(item['input_sha256'])

    def test_mutated_input_is_detected(self):
        with temporary_directory() as directory:
            data = runner.read_json(runner.BASE/'evals.json')
            data['evals'][0]['prompt'] += '修改'
            path = Path(directory)/'evals.json'
            path.write_text(json.dumps(data),encoding='utf-8')
            with self.assertRaises(ValueError):
                runner.load_suite(path,runner.BASE/'sources.json')

    def test_protected_variables_are_exact(self):
        case = {'rewrite_checks':{'variables':['{{source_text}}']}}
        self.assertEqual(runner.check_rewrite(case,'翻译 {{source_text}}'),[])
        self.assertTrue(runner.check_rewrite(case,'翻译 {{text}}'))
        self.assertTrue(runner.check_rewrite(case,'翻译 {{source_text}}，使用 {{extra}}'))

    def test_protected_source_material(self):
        case = {'rewrite_checks':{'verbatim':['目前没确认解决']}}
        self.assertTrue(runner.check_rewrite(case,'已经解决'))

    def test_json_format_requires_bare_json(self):
        self.assertEqual(runner.check_execution({'json_format':True},'{"a":1}'),[])
        self.assertTrue(runner.check_execution({'json_format':True},'```json\n{"a":1}\n```'))
        self.assertTrue(runner.check_execution({'json_format':True},'说明：{"a":1}'))

    def test_json_array_must_match_order(self):
        self.assertTrue(runner.check_execution({'json_array_equals':['李明','王芳']},'["王芳","李明"]'))

    def test_word_limit_is_enforced(self):
        self.assertTrue(runner.check_execution({'max_words':2},'one two three'))

    def test_star_bullets_allow_nonbullet_prose(self):
        self.assertEqual(runner.check_execution({'star_bullets':3},'Dialogue:\n* a\n* b\n* c\nThe end.'),[])

    def test_star_bullets_reject_numbered_bullets(self):
        self.assertTrue(runner.check_execution({'star_bullets':3},'* a\n* b\n* c\n1. d'))

    def test_star_bullets_count_nested_star_lines(self):
        self.assertTrue(runner.check_execution({'star_bullets':3},'* a\n  * nested\n* b\n* c'))

    def test_forbidden_words_are_enforced(self):
        self.assertTrue(runner.check_execution({'forbidden_words':['nourriture']},'{"text":"nourriture"}'))

    def test_empty_input_does_not_call_model(self):
        client = FakeClient([])
        for value in ('','  ',None,123):
            with self.assertRaises(ValueError):
                runner.call_model(client,'fake',value)
        self.assertEqual(client.calls,[])

    def test_empty_response_fails(self):
        with self.assertRaises(ValueError):
            runner.call_model(FakeClient(['  ']),'fake','input')

    def test_truncated_response_fails(self):
        with self.assertRaises(ValueError):
            runner.call_model(FakeClient([{'status':'incomplete','output_text':'partial'}]),'fake','input')

    def test_timeout_propagates(self):
        with self.assertRaises(TimeoutError):
            runner.call_model(FakeClient([TimeoutError('offline timeout')]),'fake','input')

    def test_error_info_redacts_configured_and_token_like_api_keys(self):
        configured_key = 'arbitrary-secret-value'
        token_like_key = 'sk-test_token-123'
        with patch.dict(os.environ,{'OPENAI_API_KEY':configured_key},clear=True):
            result = runner.error_info(RuntimeError('request failed: '+configured_key+' and '+token_like_key))
        self.assertNotIn(configured_key,result['message'])
        self.assertNotIn(token_like_key,result['message'])
        self.assertIn('[REDACTED]',result['message'])

    def test_existing_evidence_not_overwritten(self):
        with temporary_directory() as directory:
            path = Path(directory)/'evidence.jsonl'
            path.write_text('old evidence',encoding='utf-8')
            with self.assertRaises(FileExistsError):
                runner.open_output(path)
            self.assertEqual(path.read_text(),'old evidence')

    def test_fake_downstream_judgment_is_rejected(self):
        with self.assertRaises(ValueError):
            runner.validate_judgment(judgment(True),False)

    def test_failed_fidelity_needs_reason(self):
        value = judgment()
        value['fidelity']['right']['pass'] = False
        with self.assertRaises(ValueError):
            runner.validate_judgment(value,False)

    def test_passing_fidelity_rejects_violations(self):
        value = judgment()
        value['fidelity']['left']['violations'] = ['改变事实']
        with self.assertRaises(ValueError):
            runner.validate_judgment(value,False)

    def test_judgment_rejects_missing_required_field(self):
        value = judgment()
        del value['fidelity']
        with self.assertRaises(ValueError):
            runner.validate_judgment(value,False)

    def test_judgment_rejects_unknown_top_level_field(self):
        value = judgment()
        value['unexpected'] = True
        with self.assertRaises(ValueError):
            runner.validate_judgment(value,False)

    def test_generate_records_failure_without_success_fallback(self):
        with temporary_directory() as directory:
            root = Path(directory)
            meta = root/'meta.md'
            meta.write_text('Only optimize.',encoding='utf-8')
            args = argparse.Namespace(cases=runner.BASE/'evals.json',sources=runner.BASE/'sources.json',split='dev',limit=2,meta_prompt=meta,output=root/'generated.jsonl',model='fake',timeout=10,max_retries=0,max_output_tokens=100)
            client = FakeClient(['improved instruction',''])
            with patch.object(runner,'client_for',return_value=client),contextlib.redirect_stdout(io.StringIO()):
                result = runner.generate(args)
            rows = [json.loads(line) for line in args.output.read_text().splitlines()]
            self.assertEqual(result,1)
            self.assertEqual([row['status'] for row in rows],['ok','error'])
            self.assertNotIn('optimized_prompt',rows[1])
            self.assertIn('case_sha256',rows[0])

    def test_compare_uses_same_system_fixture(self):
        with temporary_directory() as directory:
            args = argparse.Namespace(cases=runner.BASE/'evals.json',sources=runner.BASE/'sources.json',split='test',limit=1,left='original',right='original',output=Path(directory)/'comparison.jsonl',judge_model='fake-judge',executor_model='fake-executor',execute=True,seed=42,timeout=10,max_retries=0,max_output_tokens=200)
            client = FakeClient(['实际回答A','实际回答B',json.dumps(judgment(True),ensure_ascii=False)])
            with patch.object(runner,'client_for',return_value=client),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.compare(args),0)
            self.assertEqual(len(client.calls),3)
            self.assertEqual(client.calls[0]['input'],client.calls[1]['input'])
            self.assertEqual(client.calls[0]['instructions'],client.calls[1]['instructions'])
            row = json.loads(args.output.read_text())
            self.assertEqual(row['status'],'ok')
            self.assertEqual(row['judgment']['execution_preference'],'tie')
            payload = json.loads(client.calls[2]['input'])
            case = runner.selected_cases(args)[0]
            self.assertEqual(payload['execution_fixture'],case['execution'])

    def test_generation_from_other_input_is_rejected(self):
        case = {'id':'E01','prompt':'first'}
        records = {'E01':{'status':'ok','input_sha256':runner.sha('another'),'optimized_prompt':'text'}}
        with self.assertRaises(ValueError):
            runner.prompt_for(case,records)

    def test_generation_from_mutated_case_is_rejected(self):
        case = runner.load_suite(runner.BASE/'evals.json',runner.BASE/'sources.json')[0]
        records = {case['id']:{'status':'ok','input_sha256':runner.sha(case['prompt']),'case_sha256':'wrong','optimized_prompt':'text'}}
        with self.assertRaises(ValueError):
            runner.prompt_for(case,records)

    def test_failed_fidelity_cannot_win_preference(self):
        with temporary_directory() as directory:
            args = argparse.Namespace(cases=runner.BASE/'evals.json',sources=runner.BASE/'sources.json',split='dev',limit=1,left='original',right='original',output=Path(directory)/'comparison.jsonl',judge_model='fake',executor_model=None,execute=False,seed=42,timeout=10,max_retries=0,max_output_tokens=200)
            value = judgment()
            value['rewrite_preference'] = 'left'
            value['fidelity']['left'] = {'pass':False,'violations':['测试：改变事实状态']}
            with patch.object(runner,'client_for',return_value=FakeClient([json.dumps(value)])),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.compare(args),0)
            row = json.loads(args.output.read_text())
            winner = row['judgment']['rewrite_preference']
            self.assertTrue(row['judgment']['fidelity'][winner]['pass'])
            self.assertTrue(row['judgment']['review_required'])

    def test_anonymous_order_is_mapped_to_original_sides(self):
        with temporary_directory() as directory:
            args = argparse.Namespace(cases=runner.BASE/'evals.json',sources=runner.BASE/'sources.json',split='dev',limit=1,left='original',right='original',output=Path(directory)/'comparison.jsonl',judge_model='fake',executor_model=None,execute=False,seed=42,timeout=10,max_retries=0,max_output_tokens=200)
            value = judgment()
            value['rewrite_preference'] = 'left'
            with patch.object(runner,'client_for',return_value=FakeClient([json.dumps(value)])),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.compare(args),0)
            row = json.loads(args.output.read_text())
            self.assertEqual(row['judgment']['rewrite_preference'],row['judge_order'][0])

    def test_downstream_winner_with_automatic_failure_is_corrected(self):
        case = {
            'id':'X01','split':'dev','prompt':'Write exactly three star bullets.',
            'expected_behavior':'Preserve exact bullet format.','must_preserve':['three bullets'],
            'must_not_add':['other content'],'rewrite_checks':{},
            'execution':{'enabled':True,'mode':'user','input':'','bindings':{},'checks':{'star_bullets':3}},
        }
        value = judgment(True)
        value['execution_preference'] = 'left'
        with temporary_directory() as directory:
            args = argparse.Namespace(cases=None,sources=None,split='dev',limit=None,left='original',right='original',output=Path(directory)/'comparison.jsonl',judge_model='fake-judge',executor_model='fake-executor',execute=True,seed=0,timeout=10,max_retries=0,max_output_tokens=200)
            client = FakeClient(['* one\n* two','* one\n* two\n* three',json.dumps(value,ensure_ascii=False)])
            with patch.object(runner,'selected_cases',return_value=[case]),patch.object(runner,'client_for',return_value=client),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.compare(args),0)
            row = json.loads(args.output.read_text())
            failed_side = next(side for side in ('left','right') if row['execution'][side]['automatic_failures'])
            clean_side = 'right' if failed_side == 'left' else 'left'
            self.assertEqual(row['judgment']['execution_preference'],clean_side)
            self.assertTrue(row['judgment']['review_required'])
            raw_judgment = json.loads(row['judge_call']['text'])
            self.assertEqual(raw_judgment['execution_preference'],'left')

    def test_evidence_quote_must_belong_to_designated_side(self):
        case = {
            'id':'X02','split':'dev','prompt':'Original evidence.','expected_behavior':'Rewrite.',
            'must_preserve':['intent'],'must_not_add':['facts'],'rewrite_checks':{},
            'execution':{'enabled':False,'mode':'user','input':'','bindings':{},'checks':{}},
        }
        value = judgment()
        value['evidence'] = [{'side':'left','quote':'RIGHT_ONLY','reason':'wrong side'}]
        case_digest = runner.case_sha(case)
        left_rows = {'X02':{'status':'ok','input_sha256':runner.sha(case['prompt']),'case_sha256':case_digest,'optimized_prompt':'LEFT_ONLY'}}
        right_rows = {'X02':{'status':'ok','input_sha256':runner.sha(case['prompt']),'case_sha256':case_digest,'optimized_prompt':'RIGHT_ONLY'}}
        with temporary_directory() as directory:
            args = argparse.Namespace(cases=None,sources=None,split='dev',limit=None,left='left.jsonl',right='right.jsonl',output=Path(directory)/'comparison.jsonl',judge_model='fake',executor_model=None,execute=False,seed=0,timeout=10,max_retries=0,max_output_tokens=200)
            with patch.object(runner,'selected_cases',return_value=[case]),patch.object(runner,'load_generations',side_effect=[left_rows,right_rows]),patch.object(runner,'client_for',return_value=FakeClient([json.dumps(value)])),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.compare(args),0)
            row = json.loads(args.output.read_text())
            self.assertTrue(row['judgment']['review_required'])

    def test_summary_excludes_review_required_preferences(self):
        clean = {'case_id':'X01','status':'ok','execution':{},'judgment':judgment()}
        review = {'case_id':'X02','status':'ok','execution':{},'judgment':judgment()}
        clean['judgment']['rewrite_preference'] = 'left'
        review['judgment']['rewrite_preference'] = 'right'
        review['judgment']['review_required'] = True
        with temporary_directory() as directory:
            path = Path(directory)/'results.jsonl'
            path.write_text('\n'.join(json.dumps(row) for row in (clean,review)),encoding='utf-8')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(runner.summarize(path),0)
            result = json.loads(output.getvalue())
            self.assertEqual(result['rewrite_preference'],{'left':1,'right':0,'tie':0})
            self.assertEqual(result['provisional_rewrite_preference'],{'left':1,'right':1,'tie':0})

    def test_design_briefs_execute_as_user_requests(self):
        cases = {case['id']:case for case in runner.load_suite(runner.BASE/'evals.json',runner.BASE/'sources.json')}
        for case_id in ('E01','E03'):
            self.assertEqual(cases[case_id]['execution']['mode'],'user')
            self.assertEqual(cases[case_id]['execution']['input'],'')
            self.assertIn('设计',cases[case_id]['expected_behavior'])

    def test_e06_does_not_require_instruction_prose_verbatim(self):
        cases = {case['id']:case for case in runner.load_suite(runner.BASE/'evals.json',runner.BASE/'sources.json')}
        self.assertNotIn('verbatim',cases['E06']['rewrite_checks'])

    def test_execute_suite_records_reusable_provenance(self):
        case = runner.load_suite(runner.BASE/'evals.json',runner.BASE/'sources.json')[0]
        with temporary_directory() as directory:
            args = argparse.Namespace(cases=None,sources=None,split='dev',limit=None,prompts='original',output=Path(directory)/'executions.jsonl',model='executor',timeout=10,max_retries=0,max_output_tokens=200)
            with patch.object(runner,'selected_cases',return_value=[case]),patch.object(runner,'client_for',return_value=FakeClient(['answer'])),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.execute_suite(args),0)
            row = json.loads(args.output.read_text())
            self.assertEqual(row['case_sha256'],runner.case_sha(case))
            self.assertEqual(row['prompt_sha256'],runner.sha(case['prompt']))
            self.assertEqual(row['requested_model'],'executor')

    def test_compare_reuses_cached_executions_without_executor_calls(self):
        case = runner.load_suite(runner.BASE/'evals.json',runner.BASE/'sources.json')[0]
        settings = {'timeout':10,'max_retries':0,'max_output_tokens':200}
        cached = {'case_id':case['id'],'status':'ok','case_sha256':runner.case_sha(case),'prompt_sha256':runner.sha(case['prompt']),'requested_model':'executor','call_settings':settings,'execution':{'text':'answer','automatic_failures':[]}}
        with temporary_directory() as directory:
            cache_path = Path(directory)/'executions.jsonl'
            cache_path.write_text(json.dumps(cached),encoding='utf-8')
            args = argparse.Namespace(cases=None,sources=None,split='dev',limit=None,left='original',right='original',left_executions=cache_path,right_executions=cache_path,output=Path(directory)/'comparison.jsonl',judge_model='judge',executor_model='executor',execute=True,seed=0,timeout=10,max_retries=0,max_output_tokens=200)
            client = FakeClient([json.dumps(judgment(True),ensure_ascii=False)])
            with patch.object(runner,'selected_cases',return_value=[case]),patch.object(runner,'client_for',return_value=client),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.compare(args),0)
            self.assertEqual(len(client.calls),1)
            self.assertEqual(client.calls[0]['model'],'judge')

    def test_compare_rejects_cache_for_another_prompt(self):
        case = runner.load_suite(runner.BASE/'evals.json',runner.BASE/'sources.json')[0]
        settings = {'timeout':10,'max_retries':0,'max_output_tokens':200}
        cached = {'case_id':case['id'],'status':'ok','case_sha256':runner.case_sha(case),'prompt_sha256':'wrong','requested_model':'executor','call_settings':settings,'execution':{'text':'answer','automatic_failures':[]}}
        with temporary_directory() as directory:
            cache_path = Path(directory)/'executions.jsonl'
            cache_path.write_text(json.dumps(cached),encoding='utf-8')
            args = argparse.Namespace(cases=None,sources=None,split='dev',limit=None,left='original',right='original',left_executions=cache_path,right_executions=cache_path,output=Path(directory)/'comparison.jsonl',judge_model='judge',executor_model='executor',execute=True,seed=0,timeout=10,max_retries=0,max_output_tokens=200)
            with patch.object(runner,'selected_cases',return_value=[case]),patch.object(runner,'client_for',return_value=FakeClient([])),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.compare(args),1)
            row = json.loads(args.output.read_text())
            self.assertEqual(row['status'],'error')
            self.assertIn('another case or prompt',row['error']['message'])

    def test_compare_rechecks_stale_cached_execution_failures(self):
        cases = {case['id']:case for case in runner.load_suite(runner.BASE/'evals.json',runner.BASE/'sources.json')}
        case = cases['E09']
        settings = {'timeout':10,'max_retries':0,'max_output_tokens':200}
        def cached(text):
            return {'case_id':case['id'],'status':'ok','case_sha256':runner.case_sha(case),'prompt_sha256':runner.sha(case['prompt']),'requested_model':'executor','call_settings':settings,'execution':{'text':text,'automatic_failures':[]}}
        value = judgment(True)
        value['execution_preference'] = 'tie'
        with temporary_directory() as directory:
            left_cache,right_cache = Path(directory)/'left.jsonl',Path(directory)/'right.jsonl'
            left_cache.write_text(json.dumps(cached('* one\n* two')),encoding='utf-8')
            right_cache.write_text(json.dumps(cached('* one\n* two\n* three')),encoding='utf-8')
            args = argparse.Namespace(cases=None,sources=None,split='dev',limit=None,left='original',right='original',left_executions=left_cache,right_executions=right_cache,output=Path(directory)/'comparison.jsonl',judge_model='judge',executor_model='executor',execute=True,reverse_order=False,seed=0,timeout=10,max_retries=0,max_output_tokens=200)
            client = FakeClient([json.dumps(value,ensure_ascii=False)])
            with patch.object(runner,'selected_cases',return_value=[case]),patch.object(runner,'client_for',return_value=client),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.compare(args),0)
            row = json.loads(args.output.read_text())
            self.assertTrue(row['execution']['left']['automatic_failures'])
            self.assertEqual(row['judgment']['execution_preference'],'right')
            self.assertTrue(row['judgment']['review_required'])

    def test_original_better_prompt_stops_at_required_interaction(self):
        case = runner.load_suite(runner.BASE/'evals.json',runner.BASE/'sources.json')[0]
        with temporary_directory() as directory:
            skill = Path(directory)/'skill'
            (skill/'references').mkdir(parents=True)
            (skill/'SKILL.md').write_text('skill',encoding='utf-8')
            (skill/'references'/'lyra.md').write_text('lyra',encoding='utf-8')
            (skill/'references'/'meta.md').write_text('meta',encoding='utf-8')
            args = argparse.Namespace(cases=None,sources=None,split='dev',limit=None,meta_prompt=None,skill_directory=skill,noninteractive=False,output=Path(directory)/'generated.jsonl',model='fake',timeout=10,max_retries=0,max_output_tokens=200)
            client = FakeClient(['draft'])
            with patch.object(runner,'selected_cases',return_value=[case]),patch.object(runner,'client_for',return_value=client),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.generate(args),0)
            row = json.loads(args.output.read_text())
            self.assertEqual(row['status'],'interaction_required')
            self.assertNotIn('optimized_prompt',row)
            self.assertEqual(len(client.calls),1)

    def test_noninteractive_better_prompt_feeds_lyra_draft_to_meta(self):
        case = runner.load_suite(runner.BASE/'evals.json',runner.BASE/'sources.json')[0]
        with temporary_directory() as directory:
            skill = Path(directory)/'skill'
            (skill/'references').mkdir(parents=True)
            (skill/'SKILL.md').write_text('skill',encoding='utf-8')
            (skill/'references'/'lyra.md').write_text('lyra',encoding='utf-8')
            (skill/'references'/'meta.md').write_text('meta',encoding='utf-8')
            args = argparse.Namespace(cases=None,sources=None,split='dev',limit=None,meta_prompt=None,skill_directory=skill,noninteractive=True,output=Path(directory)/'generated.jsonl',model='fake',timeout=10,max_retries=0,max_output_tokens=200)
            client = FakeClient(['lyra draft','refined prompt'])
            with patch.object(runner,'selected_cases',return_value=[case]),patch.object(runner,'client_for',return_value=client),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.generate(args),0)
            row = json.loads(args.output.read_text())
            self.assertEqual(row['variant'],'better_prompt_noninteractive')
            self.assertEqual(len(row['stage_calls']),2)
            self.assertIn('lyra draft',client.calls[1]['input'])
            self.assertIn('without questions',row['adaptation'])

    def test_json_object_types_require_exact_keys(self):
        checks = {'json_object_types':{'name':'string','links':'string_array'}}
        self.assertTrue(runner.check_execution(checks,'{"name":"A","links":[],"extra":true}'))
        self.assertTrue(runner.check_execution(checks,'{"name":"A"}'))

    def test_json_object_types_accept_declared_types(self):
        checks = {'json_object_types':{'name':'string','links':'string_array'}}
        self.assertEqual(runner.check_execution(checks,'{"name":"A","links":["one","two"]}'),[])

    def test_json_object_string_rejects_null_and_number(self):
        checks = {'json_object_types':{'name':'string'}}
        self.assertTrue(runner.check_execution(checks,'{"name":null}'))
        self.assertTrue(runner.check_execution(checks,'{"name":3}'))

    def test_json_object_string_array_rejects_null_and_number_items(self):
        checks = {'json_object_types':{'links':'string_array'}}
        self.assertTrue(runner.check_execution(checks,'{"links":null}'))
        self.assertTrue(runner.check_execution(checks,'{"links":["ok",3]}'))

    def test_json_rejects_nan(self):
        self.assertTrue(runner.check_execution({'json_format':True},'{"score":NaN}'))

    def test_json_rejects_duplicate_keys(self):
        self.assertTrue(runner.check_execution({'json_format':True},'{"name":"first","name":"second"}'))

    def test_forbidden_word_is_checked_after_json_unicode_decoding(self):
        text = r'{"text":"\u006e\u006f\u0075\u0072\u0072\u0069\u0074\u0075\u0072\u0065"}'
        self.assertTrue(runner.check_execution({'json_format':True,'forbidden_words':['nourriture']},text))

    def test_contains_accepts_json_escaped_url_slashes(self):
        text = r'{"url":"https:\/\/example.com\/docs"}'
        self.assertEqual(runner.check_execution({'json_format':True,'contains':['https://example.com/docs']},text),[])

    def test_summary_excludes_order_sensitive_case_only_from_final_counts(self):
        first = {'case_id':'X01','status':'ok','execution':{},'judgment':judgment()}
        second = {'case_id':'X02','status':'ok','execution':{},'judgment':judgment()}
        first['judgment']['rewrite_preference'] = 'left'
        second['judgment']['rewrite_preference'] = 'right'
        with temporary_directory() as directory:
            path = Path(directory)/'results.jsonl'
            path.write_text('\n'.join(json.dumps(row) for row in (first,second)),encoding='utf-8')
            result = runner.summary_data(path,excluded_case_ids=('X02',))
        self.assertEqual(result['rewrite_preference'],{'left':1,'right':0,'tie':0})
        self.assertEqual(result['provisional_rewrite_preference'],{'left':1,'right':1,'tie':0})
        self.assertEqual(result['excluded_from_final_preference_by_pair_audit'],1)

    def test_order_audit_detects_mapped_judgment_change(self):
        first = {'case_id':'X01','status':'ok','judgment':judgment()}
        second = {'case_id':'X01','status':'ok','judgment':judgment()}
        first['judgment']['rewrite_preference'] = 'left'
        second['judgment']['rewrite_preference'] = 'right'
        with temporary_directory() as directory:
            first_path,second_path = Path(directory)/'first.jsonl',Path(directory)/'second.jsonl'
            first_path.write_text(json.dumps(first),encoding='utf-8')
            second_path.write_text(json.dumps(second),encoding='utf-8')
            result = matrix.order_audit(first_path,second_path)
        self.assertEqual(result['order_sensitive_cases'],['X01'])

    def test_order_audit_ignores_nondecision_metadata(self):
        first = {'case_id':'X01','status':'ok','judgment':judgment()}
        second = {'case_id':'X01','status':'ok','judgment':judgment()}
        second['judgment']['review_required'] = True
        second['judgment']['evidence'][0]['reason'] = 'different wording'
        with temporary_directory() as directory:
            first_path,second_path = Path(directory)/'first.jsonl',Path(directory)/'second.jsonl'
            first_path.write_text(json.dumps(first),encoding='utf-8')
            second_path.write_text(json.dumps(second),encoding='utf-8')
            result = matrix.order_audit(first_path,second_path)
        self.assertEqual(result['order_sensitive_cases'],[])

    def test_order_audit_ignores_fidelity_violation_wording(self):
        first = {'case_id':'X01','status':'ok','judgment':judgment()}
        second = {'case_id':'X01','status':'ok','judgment':judgment()}
        first['judgment']['fidelity']['left'] = {'pass':False,'violations':['changed a fact']}
        second['judgment']['fidelity']['left'] = {'pass':False,'violations':['fact was altered']}
        with temporary_directory() as directory:
            first_path,second_path = Path(directory)/'first.jsonl',Path(directory)/'second.jsonl'
            first_path.write_text(json.dumps(first),encoding='utf-8')
            second_path.write_text(json.dumps(second),encoding='utf-8')
            result = matrix.order_audit(first_path,second_path)
        self.assertEqual(result['order_sensitive_cases'],[])

    def test_order_audit_reports_failed_reverse_as_unpaired(self):
        first = {'case_id':'X01','status':'ok','judgment':judgment()}
        second = {'case_id':'X01','status':'error','error':{'type':'ValueError','message':'judge failed'}}
        with temporary_directory() as directory:
            first_path,second_path = Path(directory)/'first.jsonl',Path(directory)/'second.jsonl'
            first_path.write_text(json.dumps(first),encoding='utf-8')
            second_path.write_text(json.dumps(second),encoding='utf-8')
            result = matrix.order_audit(first_path,second_path)
        self.assertEqual(result['unpaired_cases'],['X01'])
        self.assertEqual(result['order_sensitive_cases'],[])

    def test_order_audit_reports_review_without_order_sensitivity(self):
        first = {'case_id':'X01','status':'ok','judgment':judgment()}
        second = {'case_id':'X01','status':'ok','judgment':judgment()}
        second['judgment']['review_required'] = True
        with temporary_directory() as directory:
            first_path,second_path = Path(directory)/'first.jsonl',Path(directory)/'second.jsonl'
            first_path.write_text(json.dumps(first),encoding='utf-8')
            second_path.write_text(json.dumps(second),encoding='utf-8')
            result = matrix.order_audit(first_path,second_path)
        self.assertEqual(result['review_required_in_either_order'],['X01'])
        self.assertEqual(result['order_sensitive_cases'],[])

    def test_local_env_loads_allowed_literal_values_without_overwrite(self):
        with temporary_directory() as directory:
            project = Path(directory)
            (project/'.env').write_text("OPENAI_API_KEY=new\nEVAL_MODEL='$MODEL'\nIGNORED=value\n",encoding='utf-8')
            with patch.object(matrix,'PROJECT',project),patch.dict(os.environ,{'OPENAI_API_KEY':'existing'},clear=True):
                matrix.load_local_env()
                self.assertEqual(os.environ['OPENAI_API_KEY'],'existing')
                self.assertEqual(os.environ['EVAL_MODEL'],'$MODEL')
                self.assertNotIn('IGNORED',os.environ)

    def test_matrix_dry_run_prepares_manifest_without_subprocesses(self):
        with temporary_directory() as directory:
            project = Path(directory)
            for relative,content in (
                ('better-prompt/SKILL.md','skill'),('better-prompt/references/lyra.md','lyra'),
                ('better-prompt/references/meta.md','meta'),('request/meta_prompt.md','request'),
                ('meta_prompt.md','current'),
            ):
                path = project/relative
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(content,encoding='utf-8')
            output = project/'run'
            argv = ['run_matrix.py','--output',str(output),'--dry-run']
            with patch.object(matrix,'PROJECT',project),patch.object(sys,'argv',argv),patch.object(matrix.subprocess,'run',side_effect=AssertionError('subprocess called')),patch.dict(os.environ,{},clear=True),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(matrix.main(),0)
            manifest = json.loads((output/'manifest.json').read_text())
            self.assertEqual(manifest['status'],'prepared')

    def test_matrix_live_mode_requires_credentials_before_creating_output(self):
        with temporary_directory() as directory:
            project = Path(directory)
            output = project/'run'
            argv = ['run_matrix.py','--output',str(output)]
            with patch.object(matrix,'PROJECT',project),patch.object(sys,'argv',argv),patch.dict(os.environ,{},clear=True):
                with self.assertRaises(ValueError):
                    matrix.main()
            self.assertFalse(output.exists())

    def test_matrix_reports_failed_phases_when_subprocess_creates_no_artifacts(self):
        with temporary_directory() as directory:
            project = Path(directory)
            for relative,content in (
                ('better-prompt/SKILL.md','skill'),('better-prompt/references/lyra.md','lyra'),
                ('better-prompt/references/meta.md','meta'),('request/meta_prompt.md','request'),
                ('meta_prompt.md','current'),
            ):
                path = project/relative
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(content,encoding='utf-8')
            output = project/'run'
            argv = ['run_matrix.py','--output',str(output),'--jobs','1']
            environment = {'OPENAI_API_KEY':'dummy','EVAL_MODEL':'offline-model'}
            failed = SimpleNamespace(returncode=1)
            with patch.object(matrix,'PROJECT',project),patch.object(sys,'argv',argv),patch.object(matrix.subprocess,'run',return_value=failed),patch.dict(os.environ,environment,clear=True),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(matrix.main(),1)
            manifest = json.loads((output/'manifest.json').read_text())
            report = json.loads((output/'report.json').read_text())
            self.assertEqual(manifest['status'],'completed_with_errors')
            self.assertTrue(any(task['status']=='skipped_missing_prerequisite' for task in report['tasks']))
            self.assertTrue(all(item.get('status')=='unavailable' for item in report['comparisons'].values()))


if __name__ == '__main__':
    unittest.main()
