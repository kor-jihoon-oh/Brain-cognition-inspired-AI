"""Canonical JSON serialization and value handling for the benchmark protocol.

Request hashes, saved receipts, and feedback use the same number, missing-value,
and Unicode conventions so records remain reproducible across runs.
"""
from __future__ import annotations

import json
import math
import re
from decimal import Decimal


class _Undefined:
    def __repr__(self):
        return 'undefined'

    def __bool__(self):
        return False

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


UNDEFINED = _Undefined()


def js_truthy(value):
    if value is UNDEFINED or value is None or value is False:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0 and not (isinstance(value, float) and math.isnan(value))
    if isinstance(value, str):
        return len(value) != 0
    return True  # Containers are present values, including empty lists and dictionaries.


def js_number_string(value):
    value = float(value)
    if math.isnan(value):
        return 'NaN'
    if math.isinf(value):
        return '-Infinity' if value < 0 else 'Infinity'
    if value == 0:
        return '0'
    negative = value < 0
    value = abs(value)
    shortest = repr(value)
    if 1e-6 <= value < 1e21:
        out = format(Decimal(shortest), 'f')
        if '.' in out:
            out = out.rstrip('0').rstrip('.')
    else:
        if 'e' not in shortest.lower():
            shortest = format(value, '.15e')
        mantissa, exponent = shortest.lower().split('e')
        mantissa = mantissa.rstrip('0').rstrip('.') if '.' in mantissa else mantissa
        number = int(exponent)
        out = mantissa + 'e' + ('+' if number >= 0 else '-') + str(abs(number))
    return ('-' if negative else '') + out


def js_string(value):
    if value is UNDEFINED:
        return 'undefined'
    if value is None:
        return 'null'
    if value is True:
        return 'true'
    if value is False:
        return 'false'
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return js_number_string(value)
    if isinstance(value, list):
        return ','.join('' if x is None or x is UNDEFINED else js_string(x) for x in value)
    return '[object Object]'


def js_strict_equal(a, b):
    if a is UNDEFINED or b is UNDEFINED:
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, (list, dict)) or isinstance(b, (list, dict)):
        return a is b
    return type(a) is type(b) and a == b


def js_deep_strict_equal(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if math.isnan(float(a)) and math.isnan(float(b)):
            return True
        if a == b == 0:
            return math.copysign(1, a) == math.copysign(1, b)
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(js_deep_strict_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(js_deep_strict_equal(x, y) for x, y in zip(a, b))
    return a == b


def _quote(value):
    # Keep Unicode readable and escape unpaired UTF-16 surrogates.
    value = value.encode('utf-16-le', errors='surrogatepass').decode('utf-16-le', errors='surrogatepass')
    result = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return re.sub('[\ud800-\udfff]', lambda m: '\\u%04x' % ord(m.group()), result)


def _object_items(value):
    indexed, others = [], []
    for key, item in value.items():
        key = js_string(key)
        if re.fullmatch(r'0|[1-9][0-9]*', key) and int(key) < 4294967295:
            indexed.append((int(key), key, item))
        else:
            others.append((key, item))
    return [(k, v) for _, k, v in sorted(indexed)] + others


def js_dumps(value, indent=None):
    gap = '' if indent is None else (' ' * min(10, max(0, int(indent))) if isinstance(indent, (int, float)) else str(indent)[:10])
    active = set()

    def encode(v, depth=0):
        if v is UNDEFINED:
            return UNDEFINED
        if v is None:
            return 'null'
        if v is True:
            return 'true'
        if v is False:
            return 'false'
        if isinstance(v, str):
            return _quote(v)
        if isinstance(v, (int, float)):
            return js_number_string(v) if math.isfinite(float(v)) else 'null'
        if not isinstance(v, (dict, list, tuple)):
            raise TypeError(f'Unsupported JSON type: {type(v).__name__}')
        if id(v) in active:
            raise ValueError('Converting circular structure to JSON')
        active.add(id(v))
        if isinstance(v, dict):
            pairs = []
            for k, item in _object_items(v):
                encoded = encode(item, depth + 1)
                if encoded is not UNDEFINED:
                    pairs.append(_quote(k) + (': ' if gap else ':') + encoded)
            opening, closing = '{', '}'
        else:
            pairs = []
            for item in v:
                encoded = encode(item, depth + 1)
                pairs.append('null' if encoded is UNDEFINED else encoded)
            opening, closing = '[', ']'
        active.remove(id(v))
        if not pairs:
            return opening + closing
        if not gap:
            return opening + ','.join(pairs) + closing
        return opening + '\n' + gap * (depth + 1) + (',\n' + gap * (depth + 1)).join(pairs) + '\n' + gap * depth + closing

    return encode(value)


def _utf16_length(text):
    return len(text.encode('utf-16-le', 'surrogatepass')) // 2


def _parse_error(text, error):
    pos = error.pos
    message = error.msg
    diagnostic = None
    if message.startswith('Illegal trailing comma before end of '):
        pos += 1
        while pos < len(text) and text[pos] in ' \t\r\n':
            pos += 1
        if message.endswith('object'):
            message = 'Expecting property name enclosed in double quotes'
        else:
            message = 'Expecting value'
    if message.startswith('Invalid \\escape'):
        pos += 1
    elif message.startswith('Invalid \\u'):
        pos += 1
        while pos < len(text) and text[pos] in '0123456789abcdefABCDEF':
            pos += 1
    elif message == 'Expecting value' and pos < len(text):
        char = text[pos]
        if char == '-':
            pos += 1
            diagnostic = 'No number after minus sign in JSON'
        elif char in 'tfn':
            literal = {'t':'true','f':'false','n':'null'}[char]
            start = pos
            while pos < len(text) and pos-start < len(literal) and text[pos] == literal[pos-start]:
                pos += 1
    if diagnostic is None and message in ('Extra data', "Expecting ',' delimiter") and pos < len(text):
        char = text[pos]
        if char.isascii() and char.isdigit() and pos and text[pos-1] == '0':
            diagnostic = 'Unexpected number in JSON'
        elif char == '.' and pos and text[pos-1].isdigit():
            pos += 1
            diagnostic = 'Unterminated fractional number in JSON'
        elif char in 'eE' and pos and text[pos-1].isdigit():
            pos += 1
            if pos < len(text) and text[pos] in '+-':
                pos += 1
            diagnostic = 'Exponent part is missing a number in JSON'
    prefix = text[:pos]
    position = _utf16_length(prefix)
    line = prefix.count('\n') + 1
    column = _utf16_length(prefix.rsplit('\n', 1)[-1]) + 1
    at = f' at position {position} (line {line} column {column})'
    tail = text[pos:]
    stripped = text.strip()
    if diagnostic is not None:
        return diagnostic + at
    if message == 'Extra data':
        return 'Unexpected non-whitespace character after JSON' + at
    if message.startswith('Unterminated string'):
        return 'Unterminated string in JSON' + f' at position {_utf16_length(text)} (line {text.count(chr(10))+1} column {_utf16_length(text.rsplit(chr(10),1)[-1])+1})'
    if message.startswith('Invalid control character'):
        return 'Bad control character in string literal in JSON' + at
    if message.startswith('Invalid \\escape'):
        return 'Bad escaped character in JSON' + at
    if message.startswith('Invalid \\u'):
        return 'Bad Unicode escape in JSON' + at
    if message == 'Expecting property name enclosed in double quotes':
        previous = prefix.rstrip()
        return ('Expected double-quoted property name in JSON' if previous.endswith(',') else "Expected property name or '}' in JSON") + at
    if message == "Expecting ':' delimiter":
        return "Expected ':' after property name in JSON" + at
    if message == "Expecting ',' delimiter":
        # Determine the innermost still-open container, ignoring quoted strings.
        stack=[]; quoted=False; escaped=False
        for char in prefix:
            if quoted:
                if escaped: escaped=False
                elif char=='\\': escaped=True
                elif char=='"': quoted=False
            elif char=='"': quoted=True
            elif char in '[{': stack.append(char)
            elif char in ']}':
                if stack: stack.pop()
        return ("Expected ',' or ']' after array element in JSON" if stack and stack[-1]=='[' else "Expected ',' or '}' after property value in JSON") + at
    if pos >= len(text):
        return 'Unexpected end of JSON input'
    if stripped in ('undefined', 'NaN', 'Infinity', '-Infinity', '[object Object]'):
        return f'"{stripped}" is not valid JSON'
    if len(text) <= 20:
        excerpt = '"' + text + '"'
    elif pos < 10:
        excerpt = '"' + text[:10] + '"...'
    elif pos >= len(text)-10:
        excerpt = '...' + '"' + text[-10:] + '"'
    else:
        excerpt = '...' + '"' + text[pos-10:pos+10] + '"' + '...'
    return f"Unexpected token '{tail[0]}', {excerpt} is not valid JSON"


def json_loads(text):
    """Parse strict JSON with finite literals and IEEE-754 numeric precision."""
    if not isinstance(text, str):
        text = js_string(text)
    def parse_integer(token):
        number = int(token)
        return number if abs(number) <= 9007199254740991 else float(token)
    def reject_constant(token):
        raise json.JSONDecodeError('Expecting value', text, text.find(token))
    try:
        return json.loads(text, parse_int=parse_integer, parse_constant=reject_constant)
    except json.JSONDecodeError as error:
        raise ValueError(_parse_error(text, error)) from None
