"""Offline checks only: no credentials, network access, or paid model calls."""
import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import run_evals as runner


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
    def test_dataset_counts_and_provenance(self):
        cases = runner.load_suite(runner.BASE/'evals.json',runner.BASE/'sources.json')
        self.assertEqual(len(cases),20)
        self.assertEqual(sum(case['split']=='dev' for case in cases),10)
        self.assertEqual(sum(case['execution']['enabled'] for case in cases),16)
        sources = runner.read_json(runner.BASE/'sources.json')['cases']
        self.assertEqual(sum(item['origin']=='synthetic_edge' for item in sources),8)

    def test_mutated_input_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
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

    def test_execution_checks(self):
        self.assertEqual(runner.check_execution({'json_format':True},'```json\n{"a":1}\n```'),[])
        self.assertTrue(runner.check_execution({'json_format':True},'说明：{"a":1}'))
        self.assertTrue(runner.check_execution({'json_array_equals':['李明','王芳']},'["王芳","李明"]'))
        self.assertTrue(runner.check_execution({'max_words':2},'one two three'))
        self.assertTrue(runner.check_execution({'star_bullets':3},'* a\n* b\n* c\n- d'))
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

    def test_existing_evidence_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
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

    def test_generate_records_failure_without_success_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
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

    def test_compare_uses_same_system_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(cases=runner.BASE/'evals.json',sources=runner.BASE/'sources.json',split='dev',limit=1,left='original',right='original',output=Path(directory)/'comparison.jsonl',judge_model='fake-judge',executor_model='fake-executor',execute=True,seed=42,timeout=10,max_retries=0,max_output_tokens=200)
            client = FakeClient(['实际回答A','实际回答B',json.dumps(judgment(True),ensure_ascii=False)])
            with patch.object(runner,'client_for',return_value=client),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.compare(args),0)
            self.assertEqual(len(client.calls),3)
            self.assertEqual(client.calls[0]['input'],client.calls[1]['input'])
            self.assertEqual(client.calls[0]['instructions'],client.calls[1]['instructions'])
            row = json.loads(args.output.read_text())
            self.assertEqual(row['status'],'ok')
            self.assertEqual(row['judgment']['execution_preference'],'tie')

    def test_generation_from_other_input_is_rejected(self):
        case = {'id':'E01','prompt':'first'}
        records = {'E01':{'status':'ok','input_sha256':runner.sha('another'),'optimized_prompt':'text'}}
        with self.assertRaises(ValueError):
            runner.prompt_for(case,records)

    def test_failed_fidelity_cannot_win_preference(self):
        with tempfile.TemporaryDirectory() as directory:
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
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(cases=runner.BASE/'evals.json',sources=runner.BASE/'sources.json',split='dev',limit=1,left='original',right='original',output=Path(directory)/'comparison.jsonl',judge_model='fake',executor_model=None,execute=False,seed=42,timeout=10,max_retries=0,max_output_tokens=200)
            value = judgment()
            value['rewrite_preference'] = 'left'
            with patch.object(runner,'client_for',return_value=FakeClient([json.dumps(value)])),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.compare(args),0)
            row = json.loads(args.output.read_text())
            self.assertEqual(row['judgment']['rewrite_preference'],row['judge_order'][0])


if __name__ == '__main__':
    unittest.main()
