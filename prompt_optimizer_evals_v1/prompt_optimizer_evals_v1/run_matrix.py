"""Evaluate the three supplied candidates with shared executions and reversed judges."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys

import run_evals as runner

PROJECT = runner.BASE.parent.parent


def load_local_env():
    path = PROJECT/'.env'
    if not path.exists():
        return
    allowed = {'OPENAI_API_KEY','OPENAI_BASE_URL','EVAL_MODEL','EVAL_OPTIMIZER_MODEL','EVAL_EXECUTOR_MODEL','EVAL_JUDGE_MODEL'}
    for number,line in enumerate(path.read_text(encoding='utf-8').splitlines(),1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key,separator,value = line.removeprefix('export ').partition('=')
        if not separator:
            raise ValueError(f'Invalid .env assignment on line {number}')
        key,value = key.strip(),value.strip()
        if key not in allowed:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key,value)


def write_json(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def generation_summary(path):
    rows = runner.load_generations(str(path))
    return {status:sum(row['status']==status for row in rows.values()) for status in ('ok','interaction_required','error')}


def artifact_summary(path, summarize):
    try:
        return summarize(path)
    except (OSError,ValueError,KeyError,TypeError) as error:
        return {'status':'unavailable','error':runner.error_info(error)}


def order_audit(first, second):
    left,right = runner.load_generations(str(first)),runner.load_generations(str(second))
    changed,unpaired,review = [],[],[]
    for case_id,a in left.items():
        b = right.get(case_id)
        if not b or a.get('status') != 'ok' or b.get('status') != 'ok':
            unpaired.append(case_id)
            continue
        if a['judgment']['review_required'] or b['judgment']['review_required']:
            review.append(case_id)
        preferences_changed = any(a['judgment'][key] != b['judgment'][key] for key in ('rewrite_preference','execution_preference'))
        fidelity_changed = any(a['judgment']['fidelity'][side]['pass'] != b['judgment']['fidelity'][side]['pass'] for side in ('left','right'))
        if preferences_changed or fidelity_changed:
            changed.append(case_id)
    return {'order_sensitive_cases':changed,'unpaired_cases':unpaired,'review_required_in_either_order':review,'note':'Final counts require successful, unflagged judgments in both orders. Changing violation wording alone is not an order-sensitive decision.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,help='New directory inside the project; existing runs are never overwritten')
    parser.add_argument('--optimizer-model')
    parser.add_argument('--executor-model')
    parser.add_argument('--judge-model')
    parser.add_argument('--timeout',type=runner.positive,default=60)
    parser.add_argument('--max-output-tokens',type=runner.positive,default=4096)
    parser.add_argument('--jobs',type=runner.positive,default=3)
    parser.add_argument('--dry-run',action='store_true',help='Freeze inputs and the full call plan without model calls')
    args = parser.parse_args()
    load_local_env()
    default_model = os.getenv('EVAL_MODEL')
    models = {role:getattr(args,role+'_model') or os.getenv('EVAL_'+role.upper()+'_MODEL') or default_model for role in ('optimizer','executor','judge')}
    cases = runner.load_suite(runner.BASE/'evals.json',runner.BASE/'sources.json')
    output = Path(args.output).resolve()
    if PROJECT != output and PROJECT not in output.parents:
        raise ValueError('Output directory must stay inside the project')
    if not args.dry_run:
        if not os.getenv('OPENAI_API_KEY') or not all(models.values()):
            raise ValueError('Live evaluation requires OPENAI_API_KEY and optimizer/executor/judge model IDs in the project .env or environment')
    output.mkdir(parents=True,exist_ok=False)
    snapshot = output/'inputs'
    snapshot.mkdir()
    for name in ('evals.json','sources.json','run_evals.py'):
        (snapshot/name).write_bytes((runner.BASE/name).read_bytes())
    variants = {
        'better_prompt_original':['--skill-directory',str(snapshot/'better-prompt')],
        'better_prompt_noninteractive':['--skill-directory',str(snapshot/'better-prompt'),'--noninteractive'],
        'request_meta':['--meta-prompt',str(snapshot/'request_meta_prompt.md')],
        'current':['--meta-prompt',str(snapshot/'meta_prompt.md')],
    }
    for relative in ('SKILL.md','references/lyra.md','references/meta.md'):
        target = snapshot/'better-prompt'/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((PROJECT/'better-prompt'/relative).read_bytes())
    (snapshot/'request_meta_prompt.md').write_bytes((PROJECT/'request/meta_prompt.md').read_bytes())
    (snapshot/'meta_prompt.md').write_bytes((PROJECT/'meta_prompt.md').read_bytes())
    common = ['--cases',str(snapshot/'evals.json'),'--sources',str(snapshot/'sources.json'),'--split','all','--timeout',str(args.timeout),'--max-retries','0','--max-output-tokens',str(args.max_output_tokens)]
    generations = {name:output/(name+'-generate.jsonl') for name in variants}
    runnable = ['original','better_prompt_noninteractive','request_meta','current']
    executions = {name:output/(name+'-execute.jsonl') for name in runnable}
    gen_tasks = [(name,['generate',*common,'--model',models['optimizer'] or '<optimizer-model>','--output',str(generations[name]),*source]) for name,source in variants.items()]
    exec_tasks = [(name,['execute',*common,'--model',models['executor'] or '<executor-model>','--prompts','original' if name=='original' else str(generations[name]),'--output',str(executions[name])]) for name in runnable]
    judge_tasks = []
    pairs = list(itertools.combinations(runnable,2))
    for left,right in pairs:
        for reversed_order in (False,True):
            name = left+'-vs-'+right+('-reversed' if reversed_order else '')
            judge_tasks.append((name,['compare',*common,'--left','original' if left=='original' else str(generations[left]),'--right',str(generations[right]),'--left-executions',str(executions[left]),'--right-executions',str(executions[right]),'--judge-model',models['judge'] or '<judge-model>','--executor-model',models['executor'] or '<executor-model>','--execute','--output',str(output/(name+'.jsonl')),*(['--reverse-order'] if reversed_order else [])]))
    manifest = {
        'status':'prepared' if args.dry_run else 'running',
        'created_at':datetime.now(timezone.utc).isoformat(),'models':models,
        'total_cases':len(cases),'executable_cases':sum(case['execution']['enabled'] for case in cases),
        'expected_model_calls':len(cases)*5+sum(case['execution']['enabled'] for case in cases)*4+len(cases)*len(judge_tasks),
        'scope':'All exposed diagnostic/dev/test cases; no hidden-test generalization claim.',
        'better_prompt_original':'Run Lyra only, then record interaction_required. Original mandatory user checkpoint is not fabricated.',
        'better_prompt_noninteractive':'Explicit adaptation: disable questions/checkpoint/commentary, keep Lyra then Meta; not the original skill.',
        'execution_sampling':'One execution per case and candidate, shared across all six pairs and both judge orders; order audit does not measure executor sampling variance.',
        'inputs':{str(path.relative_to(snapshot)):runner.sha(path.read_text(encoding='utf-8')) for path in snapshot.rglob('*') if path.is_file()},
        'commands':{'generate':gen_tasks,'execute':exec_tasks,'judge':judge_tasks},
    }
    write_json(output/'manifest.json',manifest)
    if args.dry_run:
        print(json.dumps({'status':'prepared','manifest':str(output/'manifest.json'),'model_calls':0,'planned_model_calls':manifest['expected_model_calls']},ensure_ascii=False))
        return 0

    def run_task(task):
        name,arguments = task
        phase = arguments[0]
        row = {'name':name,'phase':phase}
        required = [arguments[arguments.index(flag)+1] for flag in ('--prompts','--left','--right','--left-executions','--right-executions') if flag in arguments]
        missing = [path for path in required if path != 'original' and not Path(path).is_file()]
        if missing:
            return dict(row,exit_code=1,status='skipped_missing_prerequisite',missing_artifacts=missing)
        try:
            with (output/(name+'-'+phase+'.log')).open('x',encoding='utf-8') as log:
                result = subprocess.run([sys.executable,'-B',str(snapshot/'run_evals.py'),*arguments],stdout=log,stderr=subprocess.STDOUT)
            row.update(exit_code=result.returncode,status='ok' if result.returncode==0 else 'error')
        except OSError as error:
            row.update(exit_code=1,status='error',error=runner.error_info(error))
        print(phase,name,'exit',row['exit_code'],flush=True)
        return row

    completed = []
    for tasks in (gen_tasks,exec_tasks,judge_tasks):
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            completed.extend(pool.map(run_task,tasks))
    audits = {}
    for left,right in pairs:
        name = left+'-vs-'+right
        audit = artifact_summary(output/(name+'.jsonl'),lambda path:order_audit(path,output/(name+'-reversed.jsonl')))
        if audit.get('status') == 'unavailable':
            audit.update(order_sensitive_cases=[],unpaired_cases=[case['id'] for case in cases],review_required_in_either_order=[])
        audits[name] = audit
    excluded = {name:set(audit['order_sensitive_cases']+audit['unpaired_cases']+audit['review_required_in_either_order']) for name,audit in audits.items()}
    report = {
        'generations':{name:artifact_summary(path,generation_summary) for name,path in generations.items()},
        'comparisons':{name:artifact_summary(output/(name+'.jsonl'),lambda path,name=name:runner.summary_data(path,excluded[name.removesuffix('-reversed')])) for name,_ in judge_tasks},
        'order_audits':audits,
        'tasks':completed,
        'note':'Review every fidelity failure, hard-constraint failure, flagged judgment and order-sensitive case. Original skill has no final automatic output; its adaptation is labeled separately.',
    }
    write_json(output/'report.json',report)
    manifest['status'] = 'completed_with_errors' if any(item['exit_code'] for item in completed) else 'completed'
    write_json(output/'manifest.json',manifest)
    print(json.dumps({'status':manifest['status'],'report':str(output/'report.json')},ensure_ascii=False))
    return int(manifest['status']=='completed_with_errors')


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError,OSError) as error:
        print(json.dumps({'status':'error','message':str(error)},ensure_ascii=False),file=sys.stderr)
        sys.exit(1)
