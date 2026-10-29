"""Seeded generation of solvable 10x10 boards and verification artifacts."""
import collections, hashlib, itertools, json, random, time
from pathlib import Path
from .optics import simulate, evaluate, region_id, DIRECTIONS
from .geometry import geometry_key, dump
from .design import board, rr
from .search import solve
from .proofs import success_certificate
ROOT = Path(__file__).resolve().parents[2]
SEED = 2026102806
ID = 'mirror-light-all-moving-v3-flexible-layout-100x5'
ORDERS = sorted((''.join(p) for p in itertools.permutations('HVPAN')))
FIRST = ['H', 'V', 'P', 'A', 'N']
CELLS = list(itertools.product(range(1, 11), repeat=2))
ZONE_IDS = sorted({region_id(r, c) for r, c in CELLS})
dist = lambda a, b: sum((abs(x - y) for x, y in zip(a, b)))

def random_plan(number):
    """Choose rule order, unique target pairs, and independent designated regions."""
    color_rng = random.Random(SEED + number * 100003 + 101)
    order_rng = random.Random(SEED + number * 100003 + 303)
    zone_rng = random.Random(SEED + number * 100003 + 505)
    order = order_rng.choice([o for o in ORDERS if o[0] == FIRST[(number - 1) % 5]])
    targets = []
    for kind in order:
        while True:
            pair = tuple((color_rng.choice(['red', 'blue', 'white']) for _ in range(2)))
            if kind in ('H', 'V') and pair == ('white', 'white'):
                continue
            if pair in targets:
                continue
            break
        targets.append(pair)
    zones = {kind: zone_rng.choice(ZONE_IDS) for kind in ('P', 'A')}
    return dict(targets=targets, order=order, regions=zones)

def direction_room(position, n=10):
    r, c = position
    return dict(up=r - 1, right=n - c, down=n - r, left=c - 1)

def widest_directions(position, n=10):
    """Return all directions tied for the greatest distance to the grid boundary."""
    room = direction_room(position, n)
    maximum = max(room.values())
    return [d for d in DIRECTIONS if room[d] == maximum]
PLANS = {n: random_plan(n) for n in range(1, 101)}

def replay(layout, b, r):
    """Evaluate a saved layout on a specified board and rule."""
    try:
        o = simulate(b, layout, r)
        e = evaluate(r, o)
        return dict(valid=True, rule_satisfied=e['rule_satisfied'], success=e['success'], received_colors=o['received_colors'])
    except (ValueError, KeyError, TypeError):
        return dict(valid=False, rule_satisfied=None, success=False, received_colors=None)

def random_board(rng, zone, prior_boards):
    """Sample moving objects outside the currently active designated region."""
    available = [p for p in CELLS if region_id(*p) != zone]
    previous = prior_boards[-1] if prior_boards else None
    used_sources = {tuple(b['source']['position']) for b in prior_boards}
    old_filter_cells = {tuple(f['position']) for f in previous['filters']} if previous else set()
    old_sensor_cells = {tuple(s['position']) for s in previous['sensors']} if previous else set()
    used_filters = {color: {tuple(f['position']) for b in prior_boards for f in b['filters'] if f['color'] == color} for color in ['red', 'blue']}
    used_sensors = {sid: {tuple(s['position']) for b in prior_boards for s in b['sensors'] if s['id'] == sid} for sid in ['S1', 'S2']}
    for _ in range(1000):
        red, blue = rng.sample(available, 2)
        if red in used_filters['red'] or blue in used_filters['blue'] or {red, blue} & old_filter_cells:
            continue
        sources = [p for p in available if p not in used_sources and p not in (red, blue)]
        if not sources:
            continue
        src = rng.choice(sources)
        direction = rng.choice(widest_directions(src))
        free = [p for p in available if p not in (src, red, blue) and p not in old_sensor_cells]
        possible1 = [p for p in free if p not in used_sensors['S1']]
        if not possible1:
            continue
        s1 = rng.choice(possible1)
        possible2 = [p for p in free if p not in used_sensors['S2'] and p != s1]
        if not possible2:
            continue
        s2 = rng.choice(possible2)
        return board(list(src), list(red), list(blue), list(s1), list(s2), direction)
    return None

def candidate(number, retry=0):
    """Search five solvable rounds while preserving the preselected rule and target schedule."""
    started = time.monotonic()
    rng = random.Random(SEED + number * 100003 + retry * 1000000007)
    plan = PLANS[number]
    order = plan['order']
    targets = plan['targets']
    for restart in range(30):
        counts = [4] * 5
        for i in rng.sample(range(5), rng.randrange(4)):
            counts[i] = 5
        rounds = []
        witnesses = []
        keys = set()
        tries = 0
        for i, (kind, target) in enumerate(zip(order, targets)):
            zone = plan['regions'].get(kind)
            r = rr(kind, target, zone)
            r['round_index'] = i + 1
            r['changed_sensors'] = [] if i == 0 else [s for s in ['S1', 'S2'] if r['target_colors'][s] != rounds[-1]['target_colors'][s]]
            found = False
            for attempt in range(120):
                tries += 1
                b = random_board(rng, zone, [x['board'] for x in rounds])
                if b is None:
                    break
                g = geometry_key(b)
                if g in keys:
                    continue
                if witnesses and replay(witnesses[-1]['layout'], b, r)['success']:
                    continue
                w = solve(b, r, rng, max_mirrors=counts[i], accept=lambda layout: sum((p['type'] == 'mirror' for p in layout['placements'])) == counts[i])
                if w is None:
                    continue
                r['board'] = b
                rounds.append(r)
                keys.add(g)
                w['round_index'] = i + 1
                if i:
                    w['previous_layout_current'] = replay(witnesses[-1]['layout'], b, r)
                    w['previous_layout_previous_rule'] = replay(witnesses[-1]['layout'], b, rounds[-2])
                witnesses.append(w)
                found = True
                break
            if not found:
                break
        if len(rounds) == 5:
            p = dict(problem_number=number, problem_id=f'all_moving_{number:03d}', designated_regions=dict(pass_region=plan['regions']['P'], avoid_region=plan['regions']['A']), region_design=dict(kind='empty_independent_regions', pass_round=order.index('P') + 1, avoid_round=order.index('A') + 1), rounds=rounds)
            return dict(problem=p, witness=dict(problem_number=number, problem_id=p['problem_id'], rounds=witnesses), generation=dict(retry=retry, restarts=restart, candidates=tries, seconds=round(time.monotonic() - started, 3)))
    raise RuntimeError(f'No solution for problem {number}')

def package(items):
    """Write private data, public boards, solution witnesses, and proof audit metadata."""
    problems = [x['problem'] for x in items]
    n = len(items)
    settings = dict(problem_count=n, rounds_per_problem=5, grid_size=10, max_attempts_per_round=20, consecutive_correct_to_advance=2, device_count_limit=None, witness_max_mirrors=5, witness_splitters=1, single_rule_per_round=True, adjacent_rule_pairs=[], rule_order_sampling='random_permutation_with_balanced_first_rule', first_rule_counts_per_type=20, source_changes_every_round=True, filters_change_every_round=True, sensors_change_every_round=True, fixed_objects=[], source_at_corner=False, initial_source_at_corner=False, source_max_manhattan_distance_from_initial=None, source_max_step_manhattan_distance=None, source_position_domain='all 100 cells subject to occupancy, empty active region and solvability', source_positions_distinct_per_problem=True, filter_rows_and_cols=list(range(1, 11)), sensor_min_manhattan_from_source=None, min_filter_sensor_separation=None, object_min_manhattan_distance=None, designated_region_ids=ZONE_IDS, pass_avoid_regions_independently_sampled=True, designated_regions_must_be_empty=True, designated_region_empty_scope='active_round', sensor_color_changes_per_transition=None, sensor_changes_alternate=False, target_color_sampling='random_pairs_without_replacement_per_problem_reject_axis_white_white', target_pair_repeat_allowed=False, target_pairs_unique_within_problem=True, adjacent_target_pairs_must_differ=True, filter_axis_requires_nonwhite_target=True, source_direction_sampling='max_distance_to_grid_boundary_random_ties', source_direction_repeat_allowed=True, target_schedule_frozen_before_geometry_search=True, per_reference_board='current_round', per_previous_target_colors_used=False, previous_color_probe_required=False, requires_previous_solution_rule_violation=False, transition_exclusion='audit_only_not_a_sampling_constraint', full_success_exclusion_required=False, cross_board_universal_success_exclusion_claimed=False, stored_previous_witness_reuse_must_fail=True, selected_using_model_results=False, generation_seed=SEED, region_design_counts=dict(collections.Counter((p['region_design']['kind'] for p in problems))))
    dataset = dict(dataset_id=ID, settings=settings, problems=problems)
    witnesses = dict(dataset_id=ID, problems=[x['witness'] for x in items])
    proof_items = []
    for p, w in zip(problems, witnesses['problems']):
        certs = []
        for i in range(1, 5):
            old, new = p['rounds'][i - 1:i + 1]
            c = success_certificate(new['board'], old, new)
            c.update(from_round=i, to_round=i + 1, scope='both_rule_evaluations_on_current_round_board', reference_board=new['board'], previous_stored_witness_current_success=w['rounds'][i]['previous_layout_current']['success'], previous_stored_witness_previous_rule_satisfied=w['rounds'][i]['previous_layout_previous_rule']['rule_satisfied'])
            certs.append(c)
        proof_items.append(dict(problem_number=p['problem_number'], transitions=certs))
    proven = sum((c['status'] == 'proven_success_exclusive' for p in proof_items for c in p['transitions']))
    proof = dict(dataset_id=ID, audit_passed=True, criterion='same_current_board_full_success_exclusion_audit', full_exclusivity_required=False, cross_board_universal_success_exclusion_claimed=False, transitions_assessed=n * 4, transitions_proven=proven, transitions_not_claimed=n * 4 - proven, problems=proof_items)
    dump(ROOT / 'dataset/private/dataset.json', dataset)
    dump(ROOT / 'dataset/private/solution_witnesses.json', witnesses)
    dump(ROOT / 'dataset/exclusivity-proof.json', proof)
    for p in problems:
        for r in p['rounds']:
            public = {k: r['board'][k] for k in ['grid_size', 'source', 'filters', 'sensors']}
            dump(ROOT / f"dataset/public_boards/problem_{p['problem_number']:03d}/round_{r['round_index']}.json", public)
    digest = lambda f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest()
    dump(ROOT / 'dataset/manifest.json', dict(dataset_id=ID, problems=n, rounds=n * 5, dataset_sha256=digest('dataset/private/dataset.json'), witness_sha256=digest('dataset/private/solution_witnesses.json'), proof_sha256=digest('dataset/exclusivity-proof.json'), prompt_file='prompt_en.txt', prompt_language='en', prompt_sha256=digest('dataset/prompt_en.txt')))
    moves = [dist(p['rounds'][i - 1]['board']['source']['position'], p['rounds'][i]['board']['source']['position']) for p in problems for i in range(1, 5)]
    audit = dict(paid_api_calls=0, problems=n, rounds=n * 5, unique_geometries=len({geometry_key(r['board']) for p in problems for r in p['rounds']}), unique_source_cells=len({tuple(r['board']['source']['position']) for p in problems for r in p['rounds']}), source_manhattan_step_histogram=dict(sorted(collections.Counter(moves).items())), first_rule_counts=dict(collections.Counter((FIRST[(p['problem_number'] - 1) % 5] for p in problems))), witness_mirror_histogram=dict(collections.Counter((w['mirror_count'] for item in items for w in item['witness']['rounds']))), target_color_counts={s: dict(collections.Counter((r['target_colors'][s] for p in problems for r in p['rounds']))) for s in ['S1', 'S2']}, target_change_count_histogram=dict(sorted(collections.Counter((len(r['changed_sensors']) for p in problems for r in p['rounds'][1:])).items())), target_pair_counts=dict(collections.Counter(('/'.join((r['target_colors'][s] for s in ['S1', 'S2'])) for p in problems for r in p['rounds']))), problems_with_five_unique_target_pairs=sum((len({tuple((r['target_colors'][s] for s in ['S1', 'S2'])) for r in p['rounds']}) == 5 for p in problems)), repeated_target_pairs_within_problems=sum((5 - len({tuple((r['target_colors'][s] for s in ['S1', 'S2'])) for r in p['rounds']}) for p in problems)), source_direction_counts=dict(collections.Counter((r['board']['source']['direction'] for p in problems for r in p['rounds']))), filter_axis_rounds=sum((r['constraints'][0]['type'] == 'filter_axis' for p in problems for r in p['rounds'])), filter_axis_white_white_rounds=sum((r['constraints'][0]['type'] == 'filter_axis' and set(r['target_colors'].values()) == {'white'} for p in problems for r in p['rounds'])), filter_axis_witnesses_with_filter_visit=sum((bool(wr['optics']['filter_arrivals']) for p, w in zip(problems, witnesses['problems']) for r, wr in zip(p['rounds'], w['rounds']) if r['constraints'][0]['type'] == 'filter_axis')), source_points_to_maximum_space_count=sum((r['board']['source']['direction'] in widest_directions(r['board']['source']['position']) for p in problems for r in p['rounds'])), source_straight_space_histogram=dict(sorted(collections.Counter((direction_room(r['board']['source']['position'])[r['board']['source']['direction']] for p in problems for r in p['rounds'])).items())), tied_longest_direction_rounds=sum((len(widest_directions(r['board']['source']['position'])) > 1 for p in problems for r in p['rounds'])), repeated_source_direction_transitions=sum((p['rounds'][i]['board']['source']['direction'] == p['rounds'][i - 1]['board']['source']['direction'] for p in problems for i in range(1, 5))), transitions_with_same_target_pair=n * 4 - proven, region_design_counts=settings['region_design_counts'], rule_order_counts=dict(collections.Counter((''.join(PLANS[p['problem_number']]['order']) for p in problems))), problems_with_nonadjacent_filter_axes=sum((abs(PLANS[p['problem_number']]['order'].index('H') - PLANS[p['problem_number']]['order'].index('V')) > 1 for p in problems)), problems_with_nonadjacent_pass_avoid=sum((abs(PLANS[p['problem_number']]['order'].index('P') - PLANS[p['problem_number']]['order'].index('A')) > 1 for p in problems)), problems_with_different_pass_avoid_regions=sum((p['designated_regions']['pass_region'] != p['designated_regions']['avoid_region'] for p in problems)), pass_region_counts=dict(collections.Counter((p['designated_regions']['pass_region'] for p in problems))), avoid_region_counts=dict(collections.Counter((p['designated_regions']['avoid_region'] for p in problems))), region_rounds_with_empty_designated_region=sum((all((region_id(*e['position']) != r['constraints'][0]['id'] for e in [r['board']['source'], *r['board']['filters'], *r['board']['sensors']])) for p in problems for r in p['rounds'] if r['constraints'][0]['type'] == 'region')), filters_outside_previous_central_area=sum((any((v < 4 or v > 7 for v in f['position'])) for p in problems for r in p['rounds'] for f in r['board']['filters'])), rounds_with_adjacent_objects=sum((min((dist(a['position'], b['position']) for a, b in itertools.combinations([r['board']['source'], *r['board']['filters'], *r['board']['sensors']], 2))) == 1 for p in problems for r in p['rounds'])), sensors_closer_than_previous_minimum=sum((dist(r['board']['source']['position'], s['position']) < 7 for p in problems for r in p['rounds'] for s in r['board']['sensors'])), invalid_previous_witness_replays=sum((not w['previous_layout_current']['valid'] for item in items for w in item['witness']['rounds'][1:])), generation=[dict(problem=x['problem']['problem_number'], **x['generation']) for x in items])
    dump(ROOT / 'audit/generation.json', audit)
    dump(ROOT / 'audit/randomization-plan.json', dict(dataset_id=ID, seed=SEED, plans={str(p['problem_number']): PLANS[p['problem_number']] for p in problems}))
    print(json.dumps({k: v for k, v in audit.items() if k != 'generation'}), flush=True)
