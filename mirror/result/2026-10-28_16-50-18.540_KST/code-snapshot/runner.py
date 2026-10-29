"""Run experiments with immutable receipts, snapshot checks, and safe resumption."""
from __future__ import annotations

import datetime as _dt
import errno
import os
from pathlib import Path
import platform as _platform
import shutil
import threading
import time

try:
    from .experiment import CONFIG, PROTOCOL, hash, createState, buildRequest, completeResponse, applyResponse
    from .optics import boardForRound
    from .protocol import initialPrompt, publicBoard
    from .cost import priceUsage
    from .api import postJSON, redact, _checkSignal
    from .storage import readJSON, writeJSON, atomicWrite, readCalls, exportReports
    from .dataset_validation import validateDataset
    from .serialization import js_dumps, json_loads, js_truthy, UNDEFINED
except ImportError:
    from experiment import CONFIG, PROTOCOL, hash, createState, buildRequest, completeResponse, applyResponse
    from optics import boardForRound
    from protocol import initialPrompt, publicBoard
    from cost import priceUsage
    from api import postJSON, redact, _checkSignal
    from storage import readJSON, writeJSON, atomicWrite, readCalls, exportReports
    from dataset_validation import validateDataset
    from serialization import js_dumps, json_loads, js_truthy, UNDEFINED

CODE = Path(__file__).resolve().parent
ROOT = CODE.parent
runtimeFiles = ['api.py', 'cost.py', 'pricing.json', 'protocol.py', 'optics.py', 'proof_checker.py',
                'dataset_validation.py', 'experiment.py', 'runner.py', 'storage.py',
                'start_gpt_with_key.py', 'serialization.py']


def _now():
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _integer(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and float(value).is_integer()


def _text(path):
    return Path(path).read_bytes().decode('utf-8')


def loadFullDataset(root=ROOT):
    folder = Path(root) / 'dataset'
    file = folder / 'private' / 'dataset.json'
    dataset = readJSON(file)
    validateDataset(dataset, readJSON(folder / 'private' / 'solution_witnesses.json'), readJSON(folder / 'exclusivity-proof.json'))
    manifest = readJSON(folder / 'manifest.json')
    if manifest.get('prompt_file') != CONFIG['prompt_file'] or manifest.get('prompt_language') != CONFIG['model_input_language']:
        raise ValueError('프롬프트 파일 또는 언어 설정이 manifest와 다릅니다')
    if hash((folder / CONFIG['prompt_file']).read_bytes()) != manifest.get('prompt_sha256'):
        raise ValueError('프롬프트 파일 해시가 manifest와 다릅니다')
    if (hash((folder / 'private' / 'solution_witnesses.json').read_bytes()) != manifest.get('witness_sha256')
            or hash((folder / 'exclusivity-proof.json').read_bytes()) != manifest.get('proof_sha256')):
        raise ValueError('정답/정답 재사용 배제 증명 파일 해시가 다릅니다')
    if len(dataset['problems']) != CONFIG['source_dataset_problems']:
        raise ValueError(f"{CONFIG['source_dataset_problems']}문제 데이터셋이 아닙니다")
    if hash(file.read_bytes()) != manifest.get('dataset_sha256'):
        raise ValueError('데이터셋 파일 해시가 manifest와 다릅니다')
    st = dataset['settings']
    if st.get('max_attempts_per_round') != CONFIG['T'] or st.get('consecutive_correct_to_advance') != CONFIG['k'] or st.get('device_count_limit', UNDEFINED) is not None:
        raise ValueError('데이터셋 설정 불일치')
    for i, p in enumerate(dataset['problems']):
        if p['problem_number'] != i + 1 or any(r['round_index'] != j + 1 for j, r in enumerate(p['rounds'])):
            raise ValueError('문제 또는 라운드 순서 불일치')
        for ri in range(5):
            public = folder / 'public_boards' / f"problem_{p['problem_number']:03d}" / f'round_{ri + 1}.json'
            if hash(readJSON(public)) != hash(publicBoard(boardForRound(p, ri))):
                raise ValueError('공개 배치와 평가 데이터가 다릅니다')
    return dataset


def loadDataset(root=ROOT):
    full = loadFullDataset(root)
    first, last = CONFIG['first_problem'], CONFIG['last_problem']
    if not _integer(first) or not _integer(last) or first < 1 or last > len(full['problems']) or last - first + 1 != CONFIG['problems']:
        raise ValueError('잘못된 실행 범위')
    return {**full, 'selection': {'source_problem_count': len(full['problems']), 'first_problem': first,
            'last_problem': last, 'selected_problem_count': CONFIG['problems'], 'order': 'original_dataset_order'},
            'problems': full['problems'][int(first) - 1:int(last)]}


def dateFolder():
    date = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=9)
    return date.strftime('%Y-%m-%d_%H-%M-%S.') + f'{date.microsecond // 1000:03d}_KST'


def createRun(root=ROOT):
    root = Path(root)
    dataset = loadDataset(root)
    prompt = _text(root / 'dataset' / CONFIG['prompt_file'])
    pricing = readJSON(CODE / 'pricing.json')
    (root / 'result').mkdir(parents=True, exist_ok=True)
    for n in range(100):
        folder = root / 'result' / (dateFolder() + (f'_{n}' if n else ''))
        try:
            folder.mkdir()
            break
        except FileExistsError:
            if n == 99:
                raise
    arch = {'AMD64': 'x64', 'x86_64': 'x64', 'ARM64': 'arm64', 'aarch64': 'arm64'}.get(_platform.machine(), _platform.machine())
    created_at = _now()
    config = {'protocol': PROTOCOL, **CONFIG, 'created_at': created_at, 'dataset_id': dataset['dataset_id'],
              'record_source': 'live_api', 'snapshot_role': 'execution_and_evaluation',
              'snapshot_created_at': created_at,
              'selection': dataset['selection'], 'selected_problem_ids': [p['problem_id'] for p in dataset['problems']],
              'python_version': _platform.python_version(), 'runtime': 'python',
              'platform': 'win32' if os.name == 'nt' else ('darwin' if _platform.system() == 'Darwin' else 'linux'),
              'architecture': arch, 'time_zone': 'Asia/Seoul', 'dataset_hash': hash(dataset),
              'dataset_file_sha256': hash((root / 'dataset/private/dataset.json').read_bytes()),
              'prompt_hash': hash(prompt), 'pricing_hash': hash(pricing),
              'witness_file_sha256': hash((root / 'dataset/private/solution_witnesses.json').read_bytes()),
              'proof_file_sha256': hash((root / 'dataset/exclusivity-proof.json').read_bytes()),
              'manifest_file_sha256': hash((root / 'dataset/manifest.json').read_bytes()), 'code_hashes': {}}
    for name in runtimeFiles:
        src, dest = CODE / name, folder / 'code-snapshot' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        config['code_hashes'][name] = hash(src.read_bytes())
    writeJSON(folder / 'dataset-snapshot.json', dataset)
    atomicWrite(folder / 'prompt-snapshot.txt', prompt)
    for src, dest in [('private/dataset.json', 'source-dataset.json'), ('private/solution_witnesses.json', 'solution-witnesses-snapshot.json'),
                      ('manifest.json', 'dataset-manifest-snapshot.json'), ('exclusivity-proof.json', 'exclusivity-proof-snapshot.json')]:
        shutil.copyfile(root / 'dataset' / src, folder / dest)
    writeJSON(folder / 'selection.json', {**dataset['selection'], 'source_sha256': config['dataset_file_sha256'], 'selected_problem_ids': config['selected_problem_ids']})
    writeJSON(folder / 'pricing-snapshot.json', pricing)
    writeJSON(folder / 'config.json', config)
    return str(folder)


def resolveResume(value, root=ROOT):
    if value != 'latest':
        return str(Path(value).resolve())
    result = Path(root) / 'result'
    names = sorted(p.name for p in result.iterdir() if (p / 'config.json').exists()) if result.exists() else []
    if not names:
        raise ValueError('이어서 실행할 결과 폴더가 없습니다')
    return str(result / names[-1])


def loadRun(folder):
    folder = Path(folder)
    config, dataset = readJSON(folder / 'config.json'), readJSON(folder / 'dataset-snapshot.json')
    prompt, pricing = _text(folder / 'prompt-snapshot.txt'), readJSON(folder / 'pricing-snapshot.json')
    if config.get('protocol') != PROTOCOL or any(hash(config.get(k, UNDEFINED)) != hash(v) for k, v in CONFIG.items()):
        raise ValueError('피드백 방식 또는 실험 설정이 현재 코드와 다릅니다. 이전 결과는 원래 코드로 재개하고, 새 피드백 실험은 --resume 없이 시작하세요')
    if hash(dataset) != config['dataset_hash'] or hash(prompt) != config['prompt_hash'] or hash(pricing) != config['pricing_hash']:
        raise ValueError('저장된 실험 스냅샷이 변경됐습니다')
    if hash((folder / 'source-dataset.json').read_bytes()) != config['dataset_file_sha256']:
        raise ValueError('원본 데이터셋 스냅샷이 변경됐습니다')
    for name, field in [('solution-witnesses-snapshot.json', 'witness_file_sha256'), ('exclusivity-proof-snapshot.json', 'proof_file_sha256'), ('dataset-manifest-snapshot.json', 'manifest_file_sha256')]:
        if hash((folder / name).read_bytes()) != config[field]:
            raise ValueError('검증 스냅샷이 변경됐습니다: ' + name)
    code_hashes = config['code_hashes']
    if set(code_hashes) != set(runtimeFiles):
        raise ValueError('실행 코드 스냅샷 목록이 현재 Python 런타임과 다릅니다')
    for name, expected in code_hashes.items():
        snapshot = folder / 'code-snapshot' / name
        if not snapshot.is_file() or hash(snapshot.read_bytes()) != expected:
            raise ValueError('저장된 코드 스냅샷이 변경됐습니다: ' + name)
        current = CODE / name
        if not current.is_file() or hash(current.read_bytes()) != expected:
            raise ValueError('실행 코드가 변경됐습니다. 결과 폴더의 code-snapshot/start_gpt_with_key.py로 --resume 실행하세요')
    return {'config': config, 'dataset': dataset, 'prompt': prompt, 'pricing': pricing}


def replay(dataset, prompt, calls):
    state, seen = createState(dataset, prompt), set()
    for i, call in enumerate(calls):
        if call['call_id'] != i + 1:
            raise ValueError('API 기록 파일이 누락되었거나 순서가 다릅니다')
        if state['done']:
            raise ValueError('완료된 데이터 뒤에 요청 기록이 있습니다')
        if call['problem'] != state['dataset']['problems'][state['problemIndex']]['problem_number'] or call['round'] != state['roundIndex'] + 1 or call['attempt'] != state['attempt']:
            raise ValueError('요청 순서가 저장된 실험 진행과 다릅니다')
        if hash(buildRequest(state)) != call['request_sha256']:
            raise ValueError('이전 대화 또는 API 요청 설정이 바뀌었습니다')
        if call['status'] == 'completed':
            response = call.get('response') or {}
            if not response.get('id') or response['id'] in seen:
                raise ValueError('누락되거나 중복된 응답 ID')
            seen.add(response['id'])
            if hash(response) != call.get('response_sha256'):
                raise ValueError('응답 원본이 변경됐습니다')
            applyResponse(state, response, call)
    return state


def inspectResume(folder):
    loaded = loadRun(folder)
    dataset = loaded['dataset']
    state = replay(dataset, loaded['prompt'], readCalls(folder))
    body = None if state['done'] else buildRequest(state)
    return {'done': state['done'], 'preserved_attempts': len(state['attempts']),
            'next_problem': None if state['done'] else dataset['problems'][state['problemIndex']]['problem_number'],
            'next_round': None if state['done'] else state['roundIndex'] + 1,
            'next_attempt': None if state['done'] else state['attempt'], 'consecutive_correct': state['streak'],
            'retained_assistant_messages': sum(x.get('type') == 'message' for x in body['input']) if body else 0,
            'retained_reasoning_items': sum(x.get('type') == 'reasoning' for x in body['input']) if body else 0, 'api_calls': 0}


def rebuildReports(folder):
    if (Path(folder) / 'run.lock').exists():
        raise ValueError('실행 중에는 보고서를 재생성하지 마세요. 종료 후 다시 실행하세요')
    loaded, calls = loadRun(folder), readCalls(folder)
    return exportReports(folder, replay(loaded['dataset'], loaded['prompt'], calls), calls, loaded['config'])


def reconstructRequest(folder, callId):
    loaded, calls = loadRun(folder), readCalls(folder)
    if not _integer(callId) or callId < 1 or callId > len(calls):
        raise ValueError('저장된 API 호출 번호가 아닙니다')
    callId = int(callId)
    body = buildRequest(replay(loaded['dataset'], loaded['prompt'], calls[:callId - 1]))
    if hash(body) != calls[callId - 1]['request_sha256']:
        raise ValueError('재구성한 요청 해시가 원본과 다릅니다')
    return body


def _pidAlive(pid):
    if os.name == 'nt':
        # os.kill(pid, 0) is not a harmless liveness probe on Windows Python.
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if handle:
            kernel.CloseHandle(handle)
            return True
        return ctypes.get_last_error() == 5  # access denied => process exists
    try:
        os.kill(pid, 0)
        return True
    except OSError as exc:
        return exc.errno == errno.EPERM


def acquireLock(folder):
    file = Path(folder) / 'run.lock'
    if file.exists():
        old = readJSON(file)
        if not _integer(old.get('pid')) or old['pid'] <= 0:
            raise ValueError('잘못된 run.lock 파일입니다')
        if _pidAlive(int(old['pid'])):
            raise ValueError(f"이 결과 폴더를 실행 중인 프로세스가 있습니다 (PID {old['pid']})")
        file.unlink()
    fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        payload = js_dumps({'pid': os.getpid(), 'started_at': _now()}).encode('utf-8')
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)
    def release():
        if file.exists():
            file.unlink()
    return release


def _redactFailedReceipt(call, apiKey):
    """Remove an echoed credential from failed receipts without altering successes.

    Only the exact key used for this request is removed. Successful responses,
    including opaque reasoning data, retain their original bytes and hashes.
    """
    if call.get('status') == 'completed' or not isinstance(apiKey, str) or not apiKey:
        return

    def clean(value):
        if isinstance(value, str):
            return value.replace(apiKey, '[REDACTED]')
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            return {clean(key): clean(item) for key, item in value.items()}
        return value

    changed = False
    for field in ('raw_http_body', 'response', 'error', 'request_id'):
        if field not in call:
            continue
        old, new = call[field], clean(call[field])
        if old != new:
            call[field] = new
            changed = True
            if field == 'response':
                # Re-serialize when JSON escapes concealed the literal key in the
                # raw body; neither raw text nor parsed content may retain it.
                call['raw_http_body'] = js_dumps(new)
                call['response_sha256'] = hash(new)
    if changed:
        call['credential_redacted'] = True


class _Heartbeat:
    def __init__(self, log, start):
        self.stop = threading.Event()
        def beat():
            while not self.stop.wait(60):
                log(f'응답 대기 중 ({int(time.perf_counter() - start)}초)')
        self.thread = threading.Thread(target=beat, daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()


def run(folder, options=None, **kwargs):
    options = {**(options or {}), **kwargs}
    apiKey, send, log, signal = options.get('apiKey'), options.get('send', postJSON), options.get('log', print), options.get('signal')
    folder = Path(folder)
    release = acquireLock(folder)
    try:
        loaded, calls = loadRun(folder), readCalls(folder)
        config, dataset, prompt, pricing = (loaded[k] for k in ('config', 'dataset', 'prompt', 'pricing'))
        for call in calls:
            if call['status'] == 'started':
                call.update(status='interrupted', error='이전 프로세스가 응답 저장 전에 중단됨', cost={'estimated_cost_usd': None, 'cost_status': 'usage_unknown_interruption'})
                writeJSON(folder / 'calls' / f"{call['call_id']:05d}.json", call)
        state = replay(dataset, prompt, calls)
        exportReports(folder, state, calls, config)
        log(f'결과 폴더: {folder}')
        if any(c['status'] in ('interrupted', 'transport_error') for c in calls):
            log('응답을 저장하지 못한 요청의 비용은 미확인으로 남습니다. 해당 게임 시도부터 이어갑니다')
        while not state['done']:
            _checkSignal(signal)
            body, problem = buildRequest(state), dataset['problems'][state['problemIndex']]
            call = {'call_id': len(calls) + 1, 'problem': problem['problem_number'], 'round': state['roundIndex'] + 1,
                    'attempt': state['attempt'], 'status': 'started', 'problem_id': problem['problem_id'],
                    'previous_completed_call_ids': [c['call_id'] for c in calls if c['problem'] == problem['problem_number'] and c['status'] == 'completed'],
                    'started_at': _now(), 'finished_at': None, 'api_seconds': None, 'request_sha256': hash(body),
                    'input_items': len(body['input']), 'new_input': body['input'][-1]['content'],
                    'request_settings': {k: v for k, v in body.items() if k != 'input'}}
            file = folder / 'calls' / f"{call['call_id']:05d}.json"
            call['request_file'] = f"requests/{call['call_id']:05d}.json"
            writeJSON(folder / call['request_file'], body)
            calls.append(call)
            writeJSON(file, call)
            log(f"\n{'=' * 60}\n문제 {call['problem']}/{CONFIG['last_problem']} | 이번 실행 {state['problemIndex'] + 1}/{len(dataset['problems'])} | 라운드 {call['round']}/{CONFIG['rounds']} | 시도 {call['attempt']}/{CONFIG['T']}")
            start = time.perf_counter()
            heartbeat = _Heartbeat(log, start)
            try:
                http = send({'body': body, 'key': apiKey, 'signal': signal})
                call.update(api_seconds=time.perf_counter() - start, finished_at=_now(), http_status=http['status'],
                            request_id=http.get('request_id'), raw_http_body=http['text'])
                try:
                    call['response'] = json_loads(http['text'])
                except (ValueError, TypeError):
                    pass
                response = call.get('response')
                call['cost'] = priceUsage(response, pricing)
                call['response_sha256'] = hash(response) if js_truthy(response) else None
                response_error = response.get('error') if isinstance(response, dict) else None
                if http['status'] < 200 or http['status'] >= 300 or js_truthy(response_error):
                    call['status'] = 'api_error'
                    message = response_error.get('message') if isinstance(response_error, dict) else None
                    call['error'] = redact(message if message is not None else f"HTTP {http['status']}", apiKey)
                elif not completeResponse(response):
                    call['status'] = 'incomplete'
                    status = response.get('status') if isinstance(response, dict) else None
                    call['error'] = f"API 응답 상태: {status if status is not None else '알 수 없음'}. 게임 시도로 반영하지 않음"
                else:
                    call['status'] = 'completed'
            except (Exception, KeyboardInterrupt) as exc:
                call.update(api_seconds=time.perf_counter() - start, finished_at=_now(), status='transport_error',
                            error=redact('사용자가 요청을 중단했습니다' if isinstance(exc, KeyboardInterrupt) else str(exc), apiKey),
                            cost={'estimated_cost_usd': None, 'cost_status': 'usage_unknown_transport_error'})
            finally:
                heartbeat.close()
            _redactFailedReceipt(call, apiKey)
            # Store the full receipt before judging; replay commits it exactly once.
            writeJSON(file, call)
            if call['status'] != 'completed':
                exportReports(folder, state, calls, config)
                raise RuntimeError(call['error'])
            row = applyResponse(state, call['response'], call)
            summary = exportReports(folder, state, calls, config)
            log(f"모델 응답\n{row['raw_output']}\n판정결과\n{row['next_model_input']}")
            usage = row.get('usage') or {}
            log(f"입력 {usage.get('input_tokens', '?')} / 출력 {usage.get('output_tokens', '?')} 토큰 (추론 {(usage.get('output_tokens_details') or {}).get('reasoning_tokens', '?')} 포함)")
            cost = call['cost'].get('estimated_cost_usd')
            cost_text = f'{cost:.8f}' if cost is not None else '미확인'
            log(f"API {call['api_seconds']:.2f}초 | 이번 추정 비용 ${cost_text} | 누적 확인액 ${summary['known_cost_subtotal_usd']:.8f}")
            if row['round_ended']:
                why = f"연속 정답 {CONFIG['k']}회" if row['round_end_reason'] == 'k_consecutive_correct' else f"{CONFIG['T']}회 제한 도달"
                log(f'라운드 종료: {why} (모델에는 알리지 않음)')
            if row['skipped_rounds']:
                log(f"전체 정답 0회: 남은 라운드 {', '.join(map(str, row['skipped_rounds']))} 제외 (모델에는 알리지 않음)")
        log(f"\n{len(dataset['problems'])}문제 실험 완료. summary.json, rounds.csv, problems.csv, attempts.jsonl 저장 완료")
        return {'state': state, 'calls': calls, 'summary': exportReports(folder, state, calls, config)}
    finally:
        release()


def preview(number=CONFIG['first_problem']):
    dataset = loadDataset()
    prompt = _text(ROOT / 'dataset' / CONFIG['prompt_file'])
    if not _integer(number) or number < CONFIG['first_problem'] or number > CONFIG['last_problem']:
        raise ValueError(f"이번 실험의 문제 번호는 {CONFIG['first_problem']}~{CONFIG['last_problem']}입니다")
    return initialPrompt(next(p for p in dataset['problems'] if p['problem_number'] == number), prompt)


def dryRun():
    dataset = loadDataset()
    state = createState(dataset, _text(ROOT / 'dataset' / CONFIG['prompt_file']))
    request = buildRequest(state)
    result = {'model': CONFIG['model'], 'reasoning': CONFIG['reasoning_effort'], 'problems': len(dataset['problems']),
              'rounds': CONFIG['rounds'], 'k': CONFIG['k'], 'T': CONFIG['T'], 'source_dataset_problems': CONFIG['source_dataset_problems'],
              'selection': dataset['selection'], 'first_problem_id': dataset['problems'][0]['problem_id'],
              'last_problem_id': dataset['problems'][-1]['problem_id'], 'dataset_hash': hash(dataset),
              'first_request_hash': hash(request), 'first_input_items': len(request['input'])}
    for key in ['feedback', 'model_input_language', 'board_delivery', 'prompt_file', 'per_definition', 'per_reference_board',
                'source_policy', 'board_policy', 'target_color_sampling', 'source_direction_sampling', 'after_failed_round']:
        result[key] = CONFIG[key]
    result.update(api_calls=0, result_folder=str(ROOT / 'result'))
    return result
