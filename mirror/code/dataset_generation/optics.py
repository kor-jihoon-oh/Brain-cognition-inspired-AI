"""Independent ray simulator used to construct and verify solution witnesses."""
from collections import deque
COLORS = ('red', 'blue', 'white')
BITS = {'red': 1, 'blue': 2, 'white': 4}
DIRECTIONS = ('up', 'right', 'down', 'left')
DELTA = ((-1, 0), (0, 1), (1, 0), (0, -1))
REFLECT = {'R': (1, 0, 3, 2), 'L': (3, 2, 1, 0)}

def regions():
    return [dict(id=chr(65 + 5 * r + c), rows=[2 * r + 1, 2 * r + 2], cols=[2 * c + 1, 2 * c + 2]) for r in range(5) for c in range(5)]

def region_id(r, c):
    return chr(65 + 5 * ((r - 1) // 2) + (c - 1) // 2)

def simulate(board, layout, round_=None):
    """Trace each ray state, applying reflections, splitting, filters, and sensor termination."""
    axis = next((r['value'] for r in (round_ or {}).get('constraints', []) if r['type'] == 'filter_axis'), None)
    allowed = ('up', 'down') if axis == 'vertical' else ('left', 'right')
    source = tuple(board['source']['position'])
    n = board['grid_size']
    filters = {tuple(x['position']): x['color'] for x in board['filters']}
    sensors = {tuple(x['position']): x['id'] for x in board['sensors']}
    reserved = {source, *filters, *sensors}
    devices = {}
    if not isinstance(layout, dict) or set(layout) != {'placements'} or (not isinstance(layout['placements'], list)):
        raise ValueError('Expected placements array')
    for x in layout['placements']:
        if not isinstance(x, dict) or set(x) != {'type', 'row', 'col', 'direction'}:
            raise ValueError('Invalid placement keys')
        r, c = (x['row'], x['col'])
        if type(r) != int or type(c) != int or (not (1 <= r <= n and 1 <= c <= n)):
            raise ValueError('Invalid coordinate')
        if x['type'] not in ('mirror', 'splitter') or x['direction'] not in REFLECT:
            raise ValueError('Invalid device')
        if (r, c) in reserved or (r, c) in devices:
            raise ValueError('Occupied cell')
        devices[r, c] = (x['type'], x['direction'])
    q = deque()
    seen = set()
    trace = []
    segments = []
    received = {'S1': 0, 'S2': 0}
    arrivals = {'S1': [], 'S2': []}
    cellcolors = {}
    filter_arrivals = []

    def emit(r, c, d, color):
        dr, dc = DELTA[d]
        segments.append([r, c, r + dr, c + dc, color])
        q.append((r + dr, c + dc, d, color))
    emit(*source, DIRECTIONS.index(board['source']['direction']), 'white')
    while q:
        r, c, d, color = q.popleft()
        if not (1 <= r <= n and 1 <= c <= n) or (r, c, d, color) in seen:
            continue
        seen.add((r, c, d, color))
        trace.append([r, c, DIRECTIONS[d], color])
        if (r, c) not in reserved:
            cellcolors.setdefault((r, c), set()).add(color)
        if (r, c) in sensors:
            sid = sensors[r, c]
            received[sid] |= BITS[color]
            arrivals[sid].append(dict(direction=DIRECTIONS[d], color=color))
            continue
        if (r, c) in filters:
            reason = 'axis' if axis and DIRECTIONS[d] not in allowed else 'color' if color != 'white' and color != filters[r, c] else None
            filter_arrivals.append(dict(position=[r, c], direction=DIRECTIONS[d], incoming_color=color, blocked_reason=reason, transmitted_color=None if reason else filters[r, c]))
            if reason:
                continue
            color = filters[r, c]
        outs = [d]
        if (r, c) in devices:
            t, o = devices[r, c]
            outs = [REFLECT[o][d]] if t == 'mirror' else [d, REFLECT[o][d]]
        for out in outs:
            emit(r, c, out, color)
    overlaps = [dict(position=list(p), colors=[c for c in COLORS if c in cs]) for p, cs in sorted(cellcolors.items()) if len(cs) > 1]
    visitedregions = sorted({region_id(*source)} | {region_id(t[0], t[1]) for t in trace})
    return dict(received_masks=received, received_colors={s: [c for c in COLORS if received[s] & BITS[c]] for s in received}, sensor_arrivals=arrivals, filter_arrivals=filter_arrivals, trace=trace, segments=segments, overlap_cells=overlaps, visited_regions=visitedregions, visited_state_count=len(seen))

def evaluate(round_, optics):
    """Evaluate one rule and singleton sensor target colors against a traced layout."""
    results = []
    for rule in round_['constraints']:
        typ = rule['type']
        if typ == 'filter_axis':
            allowed = ('up', 'down') if rule['value'] == 'vertical' else ('left', 'right')
            ok = all((a['direction'] in allowed for a in optics['filter_arrivals']))
        elif typ == 'region':
            visited = rule['id'] in optics['visited_regions']
            ok = visited if rule['value'] == 'pass' else not visited
        elif typ == 'different_color_overlap':
            if rule['value'] != 'forbidden':
                raise ValueError('Only forbidden overlap is supported')
            ok = not optics['overlap_cells']
        else:
            raise ValueError(typ)
        results.append(dict(constraint=rule, passed=bool(ok)))
    valid = all((x['passed'] for x in results))
    matches = {s: optics['received_colors'][s] == [round_['target_colors'][s]] for s in ('S1', 'S2')}
    detected = optics['received_colors'] if valid else None
    return dict(rule_satisfied=valid, constraint_results=results, raw_color_matches=matches, detected_colors=detected, sensor_matches={s: valid and matches[s] for s in matches}, success=valid and all(matches.values()))
