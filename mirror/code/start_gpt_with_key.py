"""Run or inspect Mirror experiments with credentials kept out of saved artifacts."""
from __future__ import annotations

import getpass
import os
from pathlib import Path
import signal as signals
import subprocess
import sys
import warnings

try:
    from .runner import run, createRun, resolveResume, preview, dryRun, inspectResume, rebuildReports, reconstructRequest
    from .storage import writeJSON
    from .api import validateKey, AbortController, redact
    from .experiment import CONFIG
    from .serialization import js_dumps
except ImportError:
    from runner import run, createRun, resolveResume, preview, dryRun, inspectResume, rebuildReports, reconstructRequest
    from storage import writeJSON
    from api import validateKey, AbortController, redact
    from experiment import CONFIG
    from serialization import js_dumps


def _execute(command, args, options):
    kwargs = {'capture_output': True, 'encoding': options.get('encoding', 'utf8'), 'timeout': options.get('timeout', 15000) / 1000,
              'check': True}
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    result = subprocess.run([command, *args], **kwargs)
    if len(result.stdout.encode('utf8')) > options.get('maxBuffer', 65536):
        raise ValueError('Clipboard output exceeds limit')
    return {'stdout': result.stdout}


def readClipboardApiKey(execute=_execute):
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    script = '$OutputEncoding = [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false); $clipboardText = Get-Clipboard -Raw; if ($null -ne $clipboardText) { [Console]::Write($clipboardText) }'
    try:
        result = execute(str(powershell), ['-NoProfile', '-NonInteractive', '-Command', script],
                         {'encoding': 'utf8', 'windowsHide': True, 'timeout': 15000, 'maxBuffer': 65536})
    except Exception:
        raise RuntimeError('클립보드를 읽을 수 없습니다. API 키를 복사한 뒤 다시 실행하세요') from None
    value = result.get('stdout', '') if isinstance(result, dict) else getattr(result, 'stdout', '')
    return validateKey(str(value or '').strip())


def readApiKey(clipboard=False, environ=None, platform=None, secret_input=None):
    """Read an explicit clipboard key, an environment key, or a hidden prompt.

    Clipboard access is opt-in and available on Windows only. A terminal that
    cannot hide input is rejected rather than falling back to echoed input.
    """
    if clipboard:
        if (os.name if platform is None else platform) != 'nt':
            raise ValueError('--clipboard는 Windows에서만 사용할 수 있습니다')
        print('OpenAI API 키를 복사한 뒤 Enter를 누르세요. 키를 터미널에 붙여넣지 마세요')
        input('복사 완료 후 Enter: ')
        return readClipboardApiKey()
    env = os.environ if environ is None else environ
    value = env.get('OPENAI_API_KEY', '').strip()
    if value:
        return validateKey(value)
    prompt = getpass.getpass if secret_input is None else secret_input
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            value = prompt('OpenAI API key (입력 내용은 표시되지 않습니다): ')
    except (getpass.GetPassWarning, EOFError):
        raise RuntimeError('키를 숨겨 입력할 수 없습니다. OPENAI_API_KEY 환경변수를 설정하거나 Windows에서 --clipboard를 사용하세요') from None
    return validateKey(value)


def printHelp():
    """Show commands that work regardless of the current working directory."""
    command = f'python "{Path(__file__).resolve()}"'
    print('Mirror 10×10 · GPT-6.1 Sol · T=20 · k=2')
    print('새 실험은 1번부터 시작하며 유료 API를 사용합니다. 중단한 실험은 --resume으로 이어갑니다.')
    for suffix in ('', ' --clipboard', ' --resume latest', ' --resume "결과 폴더 전체 경로"',
                   ' --dry-run', ' --preview 1', ' --inspect --resume latest',
                   ' --rebuild --resume latest', ' --export-request 1 --resume latest'):
        print(command + suffix)
    print('키: OPENAI_API_KEY 환경변수 또는 숨김 입력. Windows 클립보드는 --clipboard로 선택합니다.')
    print('--dry-run / --preview / --inspect는 API를 호출하지 않습니다.')


def _number(value):
    try:
        if value is None:
            return float('nan')
        value = str(value).strip()
        if not value:
            return 0
        if value.lower().startswith(('0x', '0o', '0b')):
            return int(value, 0)
        return float(value)
    except (TypeError, ValueError):
        return float('nan')


def main(args=None):
    args = list(sys.argv[1:] if args is None else args)
    resume, mode, problem, callId, clipboard = None, 'live', CONFIG['first_problem'], None, False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--clipboard':
            clipboard = True
        elif arg == '--resume':
            i += 1
            resume = args[i] if i < len(args) else None
            if not resume:
                raise ValueError('--resume 뒤에 결과 폴더 경로 또는 latest가 필요합니다')
        elif arg == '--dry-run':
            mode = 'dry'
        elif arg == '--inspect':
            mode = 'inspect'
        elif arg == '--rebuild':
            mode = 'rebuild'
        elif arg == '--export-request':
            mode = 'export'
            i += 1
            callId = _number(args[i] if i < len(args) else None)
        elif arg == '--preview':
            mode = 'preview'
            if i + 1 < len(args) and not args[i + 1].startswith('--'):
                i += 1
                problem = _number(args[i])
        elif arg in ('--help', '-h'):
            mode = 'help'
        else:
            raise ValueError(f'알 수 없는 옵션: {arg}')
        i += 1
    if mode == 'help':
        printHelp()
        return 0
    if mode == 'dry':
        print(js_dumps(dryRun(), indent=2))
        return 0
    if mode == 'preview':
        print(preview(problem))
        return 0
    if mode in ('inspect', 'rebuild'):
        operation = inspectResume if mode == 'inspect' else rebuildReports
        print(js_dumps(operation(resolveResume(resume or 'latest')), indent=2))
        return 0
    if mode == 'export':
        folder = resolveResume(resume or 'latest')
        request = reconstructRequest(folder, callId)
        file = Path(folder) / 'requests' / f'{int(callId):05d}.json'
        writeJSON(file, request)
        print(f'원본 해시와 동일한 요청 본문 저장(키/인증 헤더 제외): {file}')
        return 0
    # Validate data/history before ever accessing the clipboard or asking for a key.
    if resume:
        if inspectResume(resolveResume(resume))['done']:
            print('이미 완료된 실험입니다')
            return 0
    else:
        dryRun()
    print(f"GPT-6.1 Sol · medium | Mirror · 10×10 · 영어 입력 · 매 시도 현재 배치 제공 · {CONFIG['first_problem']}~{CONFIG['last_problem']}번 · 센서별 판정 + 전체 correct/incorrect | k={CONFIG['k']}, T={CONFIG['T']}")
    print('저장된 기록에 이어서 유료 API 실험을 진행합니다' if resume else '1~100번을 처음부터 푸는 새 유료 API 실험을 시작합니다')
    apiKey = readApiKey(clipboard=clipboard)
    print('키 형식 확인 완료. 이 실행에서만 사용하며 파일에 저장하지 않습니다')
    folder = resolveResume(resume) if resume else createRun()
    abort = AbortController()
    old_handler = signals.getsignal(signals.SIGINT)
    stopped = False

    def stop(signum, frame):
        nonlocal stopped
        if stopped:
            # A second interrupt exits immediately instead of requesting cancellation again.
            signals.default_int_handler(signum, frame)
        stopped = True
        print('\n중단 요청. 현재 요청 상태를 저장합니다')
        abort.abort()

    signals.signal(signals.SIGINT, stop)
    try:
        run(folder, {'apiKey': apiKey, 'signal': abort.signal})
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(redact(str(exc), apiKey) or '사용자가 요청을 중단했습니다', file=sys.stderr)
        print(f'이어서 실행: python "{Path(__file__).resolve()}" --resume "{folder}"', file=sys.stderr)
        return 1
    finally:
        signals.signal(signals.SIGINT, old_handler)
        apiKey = ''


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(redact(str(exc)), file=sys.stderr)
        raise SystemExit(1)
