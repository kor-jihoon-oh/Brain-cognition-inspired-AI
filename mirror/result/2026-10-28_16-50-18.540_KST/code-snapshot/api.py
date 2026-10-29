"""Synchronous, single-attempt OpenAI Responses transport; standard library only."""
from __future__ import annotations

import http.client
import re
import socket
import threading
from urllib.parse import urlsplit

try:
    from .serialization import js_dumps, js_string, UNDEFINED
except ImportError:
    from serialization import js_dumps, js_string, UNDEFINED

ENDPOINT = 'https://api.openai.com/v1/responses'
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


def validateKey(value):
    if not isinstance(value, str) or not re.fullmatch(r'sk-[A-Za-z0-9_-]+', value.strip()):
        raise ValueError('유효한 OpenAI API 키가 필요합니다. 입력값에 공백이나 다른 문자가 섞이지 않았는지 확인하세요')
    return value.strip()


def redact(value, key=''):
    text = '' if value is None or value is UNDEFINED else js_string(value)
    return re.sub(r'sk-[A-Za-z0-9_-]+', '[REDACTED]', text.replace(key or '\x00', '[REDACTED]'))


class AbortError(RuntimeError):
    pass


class AbortSignal:
    """AbortController-compatible signal for synchronous calls and offline stubs."""
    def __init__(self):
        self.aborted = False
        self._callbacks = []
        self._lock = threading.RLock()

    def throwIfAborted(self):
        if self.aborted:
            raise AbortError('사용자가 요청을 중단했습니다')

    def add_abort_callback(self, callback):
        with self._lock:
            self._callbacks.append(callback)
            already = self.aborted
        if already:
            callback()
        return lambda: self.remove_abort_callback(callback)

    def remove_abort_callback(self, callback):
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def abort(self):
        with self._lock:
            if self.aborted:
                return
            self.aborted = True
            callbacks = list(self._callbacks)
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass


class AbortController:
    def __init__(self):
        self.signal = AbortSignal()

    def abort(self):
        self.signal.abort()


def _checkSignal(signal):
    if signal is None:
        return
    throw = getattr(signal, 'throwIfAborted', None)
    if throw:
        throw()
    elif getattr(signal, 'aborted', False):
        raise AbortError('사용자가 요청을 중단했습니다')


def _httpsRequest(endpoint, request):
    """One request, no redirects/retries/timeouts; returns a live HTTPResponse."""
    url = urlsplit(endpoint)
    if url.scheme != 'https':
        raise ValueError('HTTPS endpoint required')
    connection = http.client.HTTPSConnection(url.hostname, url.port or 443, timeout=None)
    signal = request.get('signal')
    live_socket = [None]

    def cancel():
        sock = live_socket[0] or connection.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        connection.close()

    unregister = signal.add_abort_callback(cancel) if hasattr(signal, 'add_abort_callback') else lambda: None
    try:
        _checkSignal(signal)
        connection.connect()
        live_socket[0] = connection.sock
        connection.auto_open = 0  # Never reopen after cancellation or a transport failure.
        _checkSignal(signal)
        connection.request(request['method'], url.path + ('?' + url.query if url.query else ''),
                           body=request['payload'], headers=request['headers'])
        response = connection.getresponse()
        response._benchmark_close = lambda: (unregister(), connection.close())
        return response
    except BaseException:
        unregister()
        connection.close()
        raise


def postJSON(options=None, **kwargs):
    """Send once. requestImpl(endpoint, request_dict) may inject an HTTPResponse stub.

    request_dict contains method, headers, payload bytes, signal, and timeout=None.
    Raw UTF-8 response text (including opaque reasoning) is never redacted.
    """
    opts = dict(options or {})
    opts.update(kwargs)
    body, key, signal = opts['body'], opts['key'], opts.get('signal')
    payload = js_dumps(body).encode('utf-8')
    request = {'method': 'POST', 'signal': signal, 'timeout': None,
               'headers': {'authorization': f'Bearer {key}', 'content-type': 'application/json',
                           'accept': 'application/json', 'content-length': str(len(payload))},
               'payload': payload}
    response = None
    try:
        _checkSignal(signal)
        response = opts.get('requestImpl', _httpsRequest)(ENDPOINT, request)
        parts, total = [], 0
        getheader = getattr(response, 'getheader', None)
        headers = getattr(response, 'headers', {})
        header = lambda name: getheader(name) if getheader else headers.get(name)
        expected = header('content-length')
        expected = int(expected) if expected is not None else None
        while True:
            _checkSignal(signal)
            chunk = response.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise RuntimeError('API 응답이 32 MiB를 초과했습니다')
            parts.append(chunk)
        _checkSignal(signal)
        if getattr(response, 'complete', True) is False or (expected is not None and expected != total):
            raise RuntimeError('불완전한 HTTP 응답')
        return {'status': getattr(response, 'status', getattr(response, 'statusCode', None)),
                'request_id': header('x-request-id'), 'text': b''.join(parts).decode('utf-8', errors='replace')}
    except (KeyboardInterrupt, AbortError):
        raise AbortError('사용자가 요청을 중단했습니다') from None
    except http.client.IncompleteRead:
        raise RuntimeError('API 연결이 응답 완료 전에 끊겼습니다') from None
    except Exception as exc:
        message = '사용자가 요청을 중단했습니다' if getattr(signal, 'aborted', False) else redact(str(exc), key)
        raise RuntimeError(message) from None
    finally:
        if response is not None:
            close = getattr(response, '_benchmark_close', None) or getattr(response, 'close', None)
            if close:
                close()
