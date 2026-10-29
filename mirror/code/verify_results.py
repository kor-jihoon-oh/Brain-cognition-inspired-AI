"""Offline parity check against preserved receipts; never sends an API request.

Usage: python code/verify_results.py [--run-folder PATH] [--output report.json]
The original result folder is read-only. Rebuilt reports use a temporary folder.
"""
from pathlib import Path
import argparse
import json
import tempfile
import time
from experiment import createState, buildRequest, applyResponse, hash
from serialization import js_dumps, json_loads
from storage import readJSON, readCalls, exportReports


def _different(a,b,path='$'):
    if isinstance(a,dict) and isinstance(b,dict):
        if a.keys()!=b.keys():
            return path+' keys'
        for key in a:
            mismatch=_different(a[key],b[key],path+'.'+key)
            if mismatch:return mismatch
    elif isinstance(a,list) and isinstance(b,list):
        if len(a)!=len(b):return path+' length'
        for i,(x,y) in enumerate(zip(a,b)):
            mismatch=_different(x,y,path+f'[{i}]')
            if mismatch:return mismatch
    elif a!=b or isinstance(a,bool)!=isinstance(b,bool):
        return path
    return None


def verify(folder):
    folder=Path(folder)
    start=time.monotonic()
    config=readJSON(folder/'config.json')
    dataset=readJSON(folder/'dataset-snapshot.json')
    prompt=(folder/'prompt-snapshot.txt').read_bytes().decode('utf-8')
    if hash(dataset)!=config['dataset_hash'] or hash(prompt)!=config['prompt_hash']:
        raise AssertionError('Dataset or prompt snapshot hash mismatch')
    calls=readCalls(folder)
    expected=[json_loads(line) for line in (folder/'attempts.jsonl').read_bytes().decode('utf-8').splitlines() if line]
    state=createState(dataset,prompt)
    count=0
    for i,call in enumerate(calls):
        if call['call_id']!=i+1:raise AssertionError('Call sequence mismatch')
        if (call['problem'],call['round'],call['attempt']) != (dataset['problems'][state['problemIndex']]['problem_number'],state['roundIndex']+1,state['attempt']):
            raise AssertionError(f'State position mismatch at call {i+1}')
        body=buildRequest(state)
        if hash(body)!=call['request_sha256']:
            original=readJSON(folder/call['request_file'])
            raise AssertionError(f'Request mismatch at call {i+1}: {_different(body,original)}')
        if call['status']=='completed':
            if hash(call['response'])!=call['response_sha256']:
                raise AssertionError(f'Response serialization mismatch at call {i+1}')
            if 'raw_http_body' in call and json_loads(call['raw_http_body'])!=call['response']:
                raise AssertionError(f'Raw HTTP body mismatch at call {i+1}')
            row=applyResponse(state,call['response'],call)
            if js_dumps(row)!=js_dumps(expected[count]):
                raise AssertionError(f'Judgment/state mismatch at call {i+1}: {_different(row,expected[count])}')
            count+=1
    if count!=len(expected):raise AssertionError('Missing or duplicate judgments')
    rebuilt_count=0
    with tempfile.TemporaryDirectory(prefix='mirror-verify-') as temp:
        summary=exportReports(temp,state,calls,config)
        for created in sorted(Path(temp).rglob('*')):
            if not created.is_file():continue
            relative=created.relative_to(temp)
            original=folder/relative
            if not original.exists():raise AssertionError(f'Missing original report: {relative}')
            if relative.as_posix()=='progress.json':
                a,b=readJSON(created),readJSON(original)
                a.pop('updated_at',None);b.pop('updated_at',None)
                if a!=b:raise AssertionError('Progress mismatch')
            elif created.read_bytes()!=original.read_bytes():
                raise AssertionError(f'Rebuilt report differs: {relative}')
            rebuilt_count+=1
    return {'status':'passed','paid_api_calls':0,'problem_count':len(dataset['problems']),
            'requests_checked':len(calls),'request_hashes_identical':True,
            'response_hashes_identical':True,'complete_attempt_rows_identical':count,
            'rebuilt_reports_identical':rebuilt_count,
            'exception':'progress.json updated_at is regenerated; all other report bytes are identical',
            'done':state['done'],'mpe_percent':summary['per_percent'],
            'successful_rounds':summary['successful_rounds'],
            'elapsed_seconds':round(time.monotonic()-start,3)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-folder',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.run_folder is None:
        result=Path(__file__).resolve().parents[1]/'result'
        runs=sorted(p for p in result.iterdir() if (p/'config.json').exists())
        if not runs:raise ValueError('No saved experiment')
        args.run_folder=runs[-1]
    report=verify(args.run_folder)
    output=json.dumps(report,ensure_ascii=False,indent=2)+'\n'
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(output,encoding='utf-8')
    print(output,end='')


if __name__=='__main__':
    main()
