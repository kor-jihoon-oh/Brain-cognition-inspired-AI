"""Check geometry identity and same-board success-exclusion certificates."""
import re
from serialization import UNDEFINED, js_dumps
from optics import regionId, _get, _strict, _string, _includes


def _keys(value):
    if value is None or value is UNDEFINED:
        raise TypeError('Cannot convert undefined or null to object')
    if isinstance(value, dict):
        return list(value)
    if isinstance(value, (list, str)):
        return [str(i) for i in range(len(value))]
    return []


def _values(value):
    return [_get(value, key) for key in _keys(value)]


def proveSuccessExclusion(board, old, current):
    ruleKind(old)
    ruleKind(current)
    ids, colors = [s['id'] for s in board['sensors']], ['red', 'blue', 'white']
    for r in [old, current]:
        target = _get(r, 'target_colors')
        targetKeys = _keys({} if target is None or target is UNDEFINED else target)
        if len(targetKeys) != len(ids) or not all(_includes(colors, _get(target, s)) for s in ids):
            return None
    if any(not _strict(_get(old['target_colors'], s), _get(current['target_colors'], s)) for s in ids):
        return 'distinct_targets_under_common_passing_physics'
    return None


def ruleKind(r):
    constraints = _get(r, 'constraints')
    if not _strict(_get(constraints, 'length', optional=True), 1):
        raise ValueError('Exactly one path rule is required')
    q = _get(constraints, 0)
    typ, value = _get(q, 'type'), _get(q, 'value')
    if _strict(typ, 'filter_axis') and _includes(['horizontal', 'vertical'], value) and len(_keys(q)) == 2:
        return 'H' if value == 'horizontal' else 'V'
    if (_strict(typ, 'region') and _includes(['pass', 'avoid'], value)
            and re.fullmatch('[A-Y]', _string(_get(q, 'id'))) is not None and len(_keys(q)) == 3):
        return 'P' if value == 'pass' else 'A'
    if _strict(typ, 'different_color_overlap') and _strict(value, 'forbidden') and len(_keys(q)) == 2:
        return 'N'
    raise ValueError('Unknown path rule')


def _edgeIndex(position):
    r, c = position
    return c-1 if _strict(r, 1) else 9+r-1 if _strict(c, 10) else 18+10-c if _strict(r, 10) else 27+10-r if _strict(c, 1) else None


def proveTransition(board, old, current):
    a, b = ruleKind(old), ruleKind(current)
    q, targets = current['constraints'][0], _values(old['target_colors'])
    if ((a == 'H' and b == 'V') or (a == 'V' and b == 'H')) and any(c in ['red', 'blue'] for c in targets):
        return 'opposite_filter_axes'
    if ((a == 'P' and b == 'A') or (a == 'A' and b == 'P')) and _strict(old['constraints'][0]['id'], q['id']):
        return 'complementary_region_predicates'
    if b == 'A' and any(_includes(targets, f['color']) and _strict(regionId(*f['position']), q['id']) for f in board['filters']):
        return 'required_color_origin_in_forbidden_region'
    if b == 'A' and a in ['H', 'V']:
        steps = [[0, -1], [0, 1]] if a == 'H' else [[-1, 0], [1, 0]]
        for f in board['filters']:
            if _includes(targets, f['color']):
                for dr, dc in steps:
                    p = [f['position'][0]+dr, f['position'][1]+dc]
                    if all(1 <= v <= 10 for v in p) and _strict(regionId(*p), q['id']):
                        return 'required_filter_axis_neighbor_in_forbidden_region'
    if b in ['H', 'V'] and any(_includes(targets, f['color']) and
                              _includes([1, 10], f['position'][1 if b == 'H' else 0]) for f in board['filters']):
        return 'boundary_filter_cannot_emit_required_color'
    source = board['source']['position']
    if b == 'N' and all(_includes([1, 10], v) for v in source):
        for sensor in board['sensors']:
            color = _get(old['target_colors'], sensor['id'])
            if _strict(color, 'white'):
                continue
            filter_ = next((f for f in board['filters'] if _strict(f['color'], color)), UNDEFINED)
            other = next((s for s in board['sensors'] if not _strict(s['id'], sensor['id'])), UNDEFINED)
            if _strict(_get(old['target_colors'], _get(other, 'id')), color):
                continue
            t = [_edgeIndex(pos) for pos in [_get(filter_, 'position'), sensor['position'], source, _get(other, 'position')]]
            if None in t or len(set(t)) != 4:
                continue

            def inside(v):
                distance = (v-t[0]+36) % 36
                return distance > 0 and distance < (t[1]-t[0]+36) % 36

            if inside(t[2]) != inside(t[3]):
                return 'alternating_boundary_terminals'
    return None


def geometryKey(b):
    pts = [b['source']['position'], *[f['position'] for f in b['filters']], *[s['position'] for s in b['sensors']]]
    vector = {'up': [-1, 0], 'right': [0, 1], 'down': [1, 0], 'left': [0, -1]}[b['source']['direction']]
    variants = []
    for flip in [False, True]:
        for turns in range(4):
            def transform(position):
                r, c = position
                if flip:
                    c = -c
                for _ in range(turns):
                    r, c = c, -r
                return [r, c]

            pp = [transform(p) for p in pts]
            mr, mc = min(p[0] for p in pp), min(p[1] for p in pp)
            norm = [[r-mr, c-mc] for r, c in pp]
            variants.append(js_dumps([norm[0], transform(vector), sorted(norm[1:3], key=_string), sorted(norm[3:], key=_string)]))
    return sorted(variants)[0]
