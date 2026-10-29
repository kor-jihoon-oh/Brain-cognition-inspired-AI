"""Trace light cell by cell, evaluate rules and colors, and label submissions."""
from copy import deepcopy
import math
import re

from serialization import UNDEFINED, js_dumps, json_loads, js_truthy
from serialization import js_strict_equal as _strict, js_deep_strict_equal as _same, js_string as _string

COLORS = ['red', 'blue', 'white']
BITS = {'red': 1, 'blue': 2, 'white': 4}
DIR = ['up', 'right', 'down', 'left']
DELTA = [[-1, 0], [0, 1], [1, 0], [0, -1]]
REFLECT = {'R': [1, 0, 3, 2], 'L': [3, 2, 1, 0]}


def _get(obj, key, optional=False):
    if obj is None or obj is UNDEFINED:
        if optional:
            return UNDEFINED
        raise TypeError(f"Cannot read properties of {_string(obj)} (reading '{key}')")
    if isinstance(obj, dict):
        return obj.get(str(key), UNDEFINED)
    if isinstance(obj, (list, str)):
        if key == 'length':
            return len(obj)
        text = _string(key)
        if re.fullmatch(r'0|[1-9][0-9]*', text):
            index = int(text)
            return obj[index] if index < len(obj) else UNDEFINED
    return UNDEFINED


def _number(value):
    if value is UNDEFINED:
        return math.nan
    if value is None:
        return 0
    if type(value) is bool:
        return int(value)
    if type(value) in (int, float):
        return value
    text = _string(value).strip()
    if not text:
        return 0
    try:
        if text.lower().startswith(('0x', '0b', '0o')):
            return int(text, 0)
        return float(text)
    except ValueError:
        return math.nan


def _integer(value):
    return type(value) in (int, float) and math.isfinite(value) and value == math.trunc(value)


def _key(r, c):
    return f'{_string(r)},{_string(c)}'


def _includes(values, target):
    return any(_strict(value, target) for value in values)


def regionId(r=UNDEFINED, c=UNDEFINED):
    r, c = _number(r), _number(c)
    if not math.isfinite(r) or not math.isfinite(c):
        return '\x00'
    return chr((65 + 5 * math.floor((r - 1) / 2) + math.floor((c - 1) / 2)) % 65536)


def allRegions():
    return [{'id': chr(65+i), 'rows': [2*(i//5)+1, 2*(i//5)+2],
             'cols': [2*(i % 5)+1, 2*(i % 5)+2]} for i in range(25)]


def simulate(board, layout, round=None):
    """Trace branched beams one cell at a time until blocked, sensed, or repeated.

    Direction and color form part of each visited state, so loops terminate
    without suppressing distinct beams. Forbidden filter-axis entries stop
    that branch. The returned trace also records regions and color overlaps.
    """
    axis = UNDEFINED
    if round is not None and round is not UNDEFINED:
        constraints = _get(round, 'constraints')
        if constraints is None or constraints is UNDEFINED:
            raise TypeError(f"Cannot read properties of {_string(constraints)} (reading 'find')")
        if not isinstance(constraints, list):
            raise TypeError('round?.constraints.find is not a function')
        for rule in constraints:
            if _strict(_get(rule, 'type'), 'filter_axis'):
                axis = _get(rule, 'value', optional=True)
                break
    allowed = ['up', 'down'] if _strict(axis, 'vertical') else ['left', 'right']
    if not isinstance(layout, dict) or list(layout) != ['placements'] or not isinstance(layout['placements'], list):
        raise ValueError('Expected exactly a placements array')
    n = board['grid_size']
    source = board['source']['position']
    filters = {_key(*f['position']): f['color'] for f in board['filters']}
    sensors = {_key(*s['position']): s['id'] for s in board['sensors']}
    reserved = {_key(*source), *filters, *sensors}
    devices = {}
    for item in layout['placements']:
        if not isinstance(item, dict) or sorted(item) != ['col', 'direction', 'row', 'type']:
            raise ValueError('Expected type, row, col, direction')
        typ, r, c, orientation = item['type'], item['row'], item['col'], item['direction']
        if not _includes(['mirror', 'splitter'], typ) or not _includes(['R', 'L'], orientation):
            raise ValueError('Invalid device or direction')
        if not _integer(r) or not _integer(c) or r < 1 or r > n or c < 1 or c > n:
            raise ValueError('Coordinates must be integers inside the grid')
        cell = _key(r, c)
        if cell in reserved or cell in devices:
            raise ValueError('Occupied or duplicate cell')
        devices[cell] = [typ, orientation]
    queue, visited, trace, segments = [], set(), [], []
    received, arrivals = {'S1': 0, 'S2': 0}, {'S1': [], 'S2': []}
    cellColors, filterArrivals = {}, []

    def emit(r, c, d, color):
        if d < 0 or d >= len(DELTA):
            raise TypeError('undefined is not iterable (cannot read property Symbol(Symbol.iterator))')
        dr, dc = DELTA[d]
        segments.append([r, c, r+dr, c+dc, color])
        queue.append([r+dr, c+dc, d, color])

    direction = board['source']['direction']
    emit(*source, DIR.index(direction) if direction in DIR else -1, 'white')
    at = 0
    while at < len(queue):
        r, c, d, color = queue[at]
        at += 1
        if r < 1 or r > n or c < 1 or c > n:
            continue
        state, cell = (r, c, d, color), _key(r, c)
        if state in visited:
            continue
        visited.add(state)
        trace.append([r, c, DIR[d], color])
        if cell not in reserved:
            cellColors.setdefault(cell, set()).add(color)
        if cell in sensors:
            sid = sensors[cell]
            received[sid] |= BITS[color]
            arrivals[sid].append({'direction': DIR[d], 'color': color})
            continue
        if cell in filters:
            reason = ('axis' if js_truthy(axis) and DIR[d] not in allowed else
                      'color' if color != 'white' and color != filters[cell] else None)
            filterArrivals.append({'position': [r, c], 'direction': DIR[d],
                                   'incoming_color': color, 'blocked_reason': reason,
                                   'transmitted_color': None if reason else filters[cell]})
            if reason:
                continue
            color = filters[cell]
        outs = [d]
        if cell in devices:
            typ, orientation = devices[cell]
            outs = [REFLECT[orientation][d]] if typ == 'mirror' else [d, REFLECT[orientation][d]]
        for out in outs:
            emit(r, c, out, color)
    overlaps = [{'position': [int(v) for v in p.split(',')],
                 'colors': [c for c in COLORS if c in colors]}
                for p, colors in cellColors.items() if len(colors) > 1]
    overlaps.sort(key=lambda a: (a['position'][0], a['position'][1]))
    return {'received_masks': received,
            'received_colors': {s: [c for c in COLORS if mask & BITS[c]] for s, mask in received.items()},
            'sensor_arrivals': arrivals, 'filter_arrivals': filterArrivals,
            'trace': trace, 'segments': segments, 'overlap_cells': overlaps,
            'visited_regions': sorted({regionId(*source), *(regionId(t[0], t[1]) for t in trace)}),
            'visited_state_count': len(visited)}


def evaluate(round, optics):
    """Compare traced paths with the active rule and each sensor's target color."""
    results = []
    constraints = _get(round, 'constraints')
    if constraints is None or constraints is UNDEFINED:
        raise TypeError(f"Cannot read properties of {_string(constraints)} (reading 'map')")
    if not isinstance(constraints, list):
        raise TypeError('round.constraints.map is not a function')
    for rule in constraints:
        typ = _get(rule, 'type')
        value = _get(rule, 'value')
        if _strict(typ, 'filter_axis'):
            if not _includes(['vertical', 'horizontal'], value):
                raise ValueError('Unknown filter axis')
            allowed = ['up', 'down'] if value == 'vertical' else ['left', 'right']
            passed = all(a['direction'] in allowed for a in optics['filter_arrivals'])
        elif _strict(typ, 'region'):
            region = _get(rule, 'id')
            if not _includes(['pass', 'avoid'], value) or re.fullmatch('[A-Y]', _string(region)) is None:
                raise ValueError('Unknown region rule')
            visited = _includes(optics['visited_regions'], region)
            passed = visited if value == 'pass' else not visited
        elif _strict(typ, 'different_color_overlap'):
            if not _strict(value, 'forbidden'):
                raise ValueError('Only forbidden overlap is supported')
            passed = len(optics['overlap_cells']) == 0
        else:
            raise ValueError('Unknown rule')
        results.append({'constraint': rule, 'passed': passed})
    valid = all(x['passed'] for x in results)
    matches = {s: len(optics['received_colors'][s]) == 1 and
               _strict(optics['received_colors'][s][0], _get(_get(round, 'target_colors'), s))
               for s in ['S1', 'S2']}
    return {'rule_satisfied': valid, 'constraint_results': results, 'raw_color_matches': matches,
            'detected_colors': deepcopy(optics['received_colors']) if valid else None,
            'sensor_matches': {s: valid and matches[s] for s in ['S1', 'S2']},
            'success': valid and all(matches.values())}


def boardForRound(problem, roundIndex):
    """Return the complete source, filter, and sensor geometry for one round."""
    board = _get(_get(_get(problem, 'rounds'), roundIndex), 'board', optional=True)
    if not js_truthy(board):
        raise ValueError('Every round requires a complete board')
    return board


def judge(problem, roundIndex, text, previousRoundHadSuccess=False):
    """Validate a submitted layout and produce correctness, feedback, and PER labels.

    Persistent error requires a valid current failure and a previous rule that
    accepts the same layout on the current board. Previous target colors do not
    enter that label. Eligibility requires an earlier full success in the
    immediately preceding round.
    """
    layout = optics = error = None
    try:
        layout = json_loads(text)
        optics = simulate(boardForRound(problem, roundIndex), layout, _get(problem['rounds'], roundIndex))
    except Exception as exc:
        error = str(exc)
    previousRule = _get(problem['rounds'], _number(roundIndex)-1)
    current = evaluate(_get(problem['rounds'], roundIndex), optics) if optics is not None else None
    previousOptics = (simulate(boardForRound(problem, roundIndex), layout, previousRule)
                      if optics is not None and js_truthy(previousRule) else None)
    previous = evaluate(previousRule, previousOptics) if previousOptics is not None else None
    correct = current is not None and current['success'] is True
    sensorResults = {s: 'correct' if current is not None and current['raw_color_matches'][s] is True else 'incorrect'
                     for s in ['S1', 'S2']}
    eligible = previousRoundHadSuccess if _number(roundIndex) > 0 else False
    persistent = (error is None and not correct and previous is not None and previous['rule_satisfied'] is True
                  if js_truthy(eligible) else eligible)
    return {'layout': layout, 'valid': error is None, 'validation_error': error,
            'optics': optics, 'previous_optics': previousOptics, 'current_evaluation': current,
            'previous_evaluation': previous, 'correct': correct, 'sensor_results': sensorResults,
            'feedback_received_colors': None,
            'feedback': js_dumps({'sensor_results': sensorResults, 'result': 'correct' if correct else 'incorrect'}),
            'previous_rule_accepts': previous['rule_satisfied'] if previous is not None else None,
            'previous_full_success': previous['success'] if previous is not None else None,
            'per_eligible': eligible, 'previous_rule_only_error': persistent,
            'per_reference_source': deepcopy(boardForRound(problem, roundIndex)['source']),
            'per_reference_geometry': deepcopy(boardForRound(problem, roundIndex)),
            'symbol': 'O' if correct else 'triangle' if js_truthy(persistent) else 'X'}
