"""Construct a solution with one splitter and a configurable mirror budget."""
from .optics import DELTA, DIRECTIONS, REFLECT, simulate, evaluate, region_id

def ray_paths(board, start, sid, desired, max_new, rng, fixed, protected, forbidden, axis=None, max_results=40, budget=35000):
    """Search bounded ray routes toward one target sensor."""
    filters = {tuple(x['position']): x['color'] for x in board['filters']}
    sensors = {tuple(x['position']): x['id'] for x in board['sensors']}
    reserved = {tuple(board['source']['position']), *filters, *sensors}
    goal = next((p for p, s in sensors.items() if s == sid))
    waypoint = next((p for p, c in filters.items() if c == desired), goal)
    allowed = range(4) if axis is None else (0, 2) if axis == 'vertical' else (1, 3)
    results = []
    nodes = 0

    def dfs(r, c, d, color, seen, route, added):
        nonlocal nodes
        nodes += 1
        if nodes > budget or len(results) >= max_results:
            return
        if not (1 <= r <= board['grid_size'] and 1 <= c <= board['grid_size']) or (r, c) in seen or (r, c) in forbidden:
            return
        if (r, c) in sensors:
            if sensors[r, c] == sid and color == desired:
                results.append((added, route + [(r, c, d, color, None, None)]))
            return
        if (r, c) in filters:
            if d not in allowed:
                return
            if color != 'white' and color != filters[r, c]:
                return
            color = filters[r, c]
        if color != 'white' and color != desired:
            return
        if (r, c) in fixed:
            actions = [(REFLECT[fixed[r, c]][d], None)]
        elif (r, c) in reserved or (r, c) in protected or len(added) >= max_new:
            actions = [(d, None)]
        else:
            actions = [(d, None), (REFLECT['R'][d], 'R'), (REFLECT['L'][d], 'L')]
        aim = waypoint if color == 'white' and desired != 'white' else goal
        rng.shuffle(actions)
        actions.sort(key=lambda x: abs(r + DELTA[x[0]][0] - aim[0]) + abs(c + DELTA[x[0]][1] - aim[1]) + (1 if x[1] else 0))
        for out, o in actions:
            dr, dc = DELTA[out]
            dfs(r + dr, c + dc, out, color, seen | {(r, c)}, route + [(r, c, d, color, out, o)], added | {(r, c): o} if o else added)
    dfs(*start, set(), [], {})
    return results

def solve(board, rr, rng, max_mirrors=6, accept=None, protected_cells=()):
    """Join sensor routes through one splitter and verify the complete layout."""
    reserved = {tuple(board['source']['position']), *(tuple(x['position']) for x in board['filters'] + board['sensors'])}
    reserved.update(protected_cells)
    axis = next((r['value'] for r in rr['constraints'] if r['type'] == 'filter_axis'), None)
    avoid = next((r['id'] for r in rr['constraints'] if r['type'] == 'region' and r['value'] == 'avoid'), None)
    blocked = {(r, c) for r in range(1, board['grid_size'] + 1) for c in range(1, board['grid_size'] + 1) if region_id(r, c) == avoid}
    target = rr['target_colors']
    src = board['source']['position']
    d = DIRECTIONS.index(board['source']['direction'])
    dr, dc = DELTA[d]
    start = (src[0] + dr, src[1] + dc, d, 'white')
    candidates = []
    for sid in ('S1', 'S2'):
        for max_main in (2, 3, 4):
            paths = ray_paths(board, start, sid, target[sid], max_main, rng, {}, set(protected_cells), blocked, axis=axis, max_results=55, budget=25000)
            candidates.extend(((sid, m, p) for m, p in paths))
            if len(paths) >= 20:
                break
    rng.shuffle(candidates)
    candidates.sort(key=lambda x: len(x[1]))
    seen = set()
    for sid, fixed, path in candidates:
        other = 'S2' if sid == 'S1' else 'S1'
        protected = {(r, c) for r, c, *_ in path} | set(protected_cells)
        forks = []
        for r, c, d, color, out, old_o in path:
            if (r, c) in reserved or (color != 'white' and color != target[other]):
                continue
            for o in ('R', 'L'):
                ref = REFLECT[o][d]
                if out == d:
                    forks.append((r, c, o, ref, color))
                elif out == ref:
                    forks.append((r, c, o, d, color))
        rng.shuffle(forks)
        for r, c, o, d, color in forks:
            remain = {p: o2 for p, o2 in fixed.items() if p != (r, c)}
            if len(remain) > max_mirrors:
                continue
            dr, dc = DELTA[d]
            paths = ray_paths(board, (r + dr, c + dc, d, color), other, target[other], min(4, max_mirrors - len(remain)), rng, remain, protected, blocked | {(r, c)}, axis=axis, max_results=60, budget=20000)
            for added, _ in paths:
                mirrors = remain | added
                key = (r, c, o, tuple(sorted(mirrors.items())))
                if key in seen:
                    continue
                seen.add(key)
                pp = [dict(type='mirror', row=a, col=b, direction=orient) for (a, b), orient in sorted(mirrors.items())]
                pp.append(dict(type='splitter', row=r, col=c, direction=o))
                layout = {'placements': pp}
                op = simulate(board, layout, rr)
                ev = evaluate(rr, op)
                if ev['success'] and (accept is None or accept(layout)):
                    return dict(layout=layout, mirror_count=len(mirrors), splitter_count=1, device_count=len(pp), optics=op, evaluation=ev)
    return None
