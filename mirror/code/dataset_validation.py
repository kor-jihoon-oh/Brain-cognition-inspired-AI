"""Validate dataset identity, geometry, rule schedules, and solution witnesses."""
from serialization import UNDEFINED, js_dumps, js_truthy
from optics import simulate, evaluate, allRegions, regionId, boardForRound
from optics import _strict, _same, _integer, _get, _string
from proof_checker import ruleKind, proveSuccessExclusion, geometryKey


def _distance(a, b):
    return sum(abs(v-b[i]) for i, v in enumerate(a))


def _replay(layout, board, round):
    try:
        optics = simulate(board, layout, round)
        evaluation = evaluate(round, optics)
        return {'valid': True, 'rule_satisfied': evaluation['rule_satisfied'],
                'success': evaluation['success'], 'received_colors': optics['received_colors']}
    except Exception:
        return {'valid': False, 'rule_satisfied': None, 'success': False, 'received_colors': None}


def validateDataset(dataset, witnesses, certificates):
    def check(ok, message):
        if not js_truthy(ok):
            raise ValueError('Dataset verification: '+message)

    check(_strict(_get(dataset, 'dataset_id'), 'mirror-light-all-moving-v3-flexible-layout-100x5') and len(dataset['problems']) == 100,
          '100 problems / flexible-layout v3 schema')
    check(_strict(_get(witnesses, 'dataset_id'), dataset['dataset_id']) and len(witnesses['problems']) == 100, 'witness identity')
    check(_strict(_get(certificates, 'dataset_id'), dataset['dataset_id']) and len(certificates['problems']) == 100
          and _strict(_get(certificates, 'transitions_assessed'), 400)
          and _get(certificates, 'full_exclusivity_required') is False
          and _get(certificates, 'cross_board_universal_success_exclusion_claimed') is False, 'proof scope')
    seen, sources = set(), set()
    first = {'H': 0, 'V': 0, 'P': 0, 'A': 0, 'N': 0}
    regionCounts = {'empty_independent_regions': 0}
    verified = transitions = proven = invalidReplays = maxStep = 0
    changeCounts = {'0': 0, '1': 0, '2': 0}
    directions = {'up': 0, 'right': 0, 'down': 0, 'left': 0}
    colorCounts = {'S1': {'red': 0, 'blue': 0, 'white': 0}, 'S2': {'red': 0, 'blue': 0, 'white': 0}}
    repeatedDirections = axisRounds = axisWitnessesWithFilterVisit = maxSpaceRounds = 0
    nonadjacentAxes = nonadjacentRegions = differentRegions = emptyRegions = outsideCentralFilters = adjacentObjects = closeSensors = 0
    regionIds, orders = set(), set()
    for pi, p in enumerate(dataset['problems']):
        tag, w = f'P{pi+1}', witnesses['problems'][pi]
        check(_strict(_get(p, 'problem_number'), pi+1) and _strict(_get(p, 'problem_id'), f'all_moving_{pi+1:03d}')
              and len(p['rounds']) == 5, tag+' numbering')
        check('board' not in p and _strict(_get(w, 'problem_id'), p['problem_id']) and len(w['rounds']) == 5, tag+' per-round boards')
        kinds = [ruleKind(r) for r in p['rounds']]
        check(_same(sorted(kinds), ['A', 'H', 'N', 'P', 'V']), tag+' five rule kinds')
        first[kinds[0]] += 1
        orders.add(''.join(kinds))
        if abs(kinds.index('H')-kinds.index('V')) > 1:
            nonadjacentAxes += 1
        if abs(kinds.index('P')-kinds.index('A')) > 1:
            nonadjacentRegions += 1
        check('designated_region' not in p and _same(_get(p, 'designated_regions'), {
            'pass_region': p['rounds'][kinds.index('P')]['constraints'][0]['id'],
            'avoid_region': p['rounds'][kinds.index('A')]['constraints'][0]['id']}), tag+' separate pass/avoid region metadata')
        check(_strict(_get(p['region_design'], 'pass_round'), kinds.index('P')+1)
              and _strict(_get(p['region_design'], 'avoid_round'), kinds.index('A')+1), tag+' region round metadata')
        if not _strict(p['designated_regions']['pass_region'], p['designated_regions']['avoid_region']):
            differentRegions += 1
        check(_get(p['region_design'], 'kind') in regionCounts, tag+' region design')
        regionCounts[p['region_design']['kind']] += 1
        totalMirrors, uniqueEntities, usedTargets = 0, {}, set()
        for ri, r in enumerate(p['rounds']):
            b, wr, where = boardForRound(p, ri), w['rounds'][ri], tag+f' R{ri+1}'
            check(_strict(_get(r, 'round_index'), ri+1) and _strict(_get(b, 'grid_size'), 10)
                  and _strict(_get(b['source'], 'color'), 'white'), where+' board')
            check(_same(_get(b, 'regions'), allRegions()), where+' regions')
            check(_same([[f['id'], f['color']] for f in b['filters']], [['F1', 'red'], ['F2', 'blue']])
                  and _same([s['id'] for s in b['sensors']], ['S1', 'S2']), where+' objects')
            entities = [['source', b['source']], *[[f['id'], f] for f in b['filters']], *[[s['id'], s] for s in b['sensors']]]
            check(all(isinstance(_get(e, 'position'), list) and len(e['position']) == 2
                      and all(_integer(v) and 1 <= v <= 10 for v in e['position']) for _, e in entities), where+' coordinates')
            join = lambda pos: ','.join(_string(v) for v in pos)
            check(len({join(e['position']) for _, e in entities}) == 5, where+' occupied cells')
            for ident, e in entities:
                uniqueEntities.setdefault(ident, set())
                check(join(e['position']) not in uniqueEntities[ident], where+' each object moves')
                uniqueEntities[ident].add(join(e['position']))
            delta = {'up': [-1, 0], 'right': [0, 1], 'down': [1, 0], 'left': [0, -1]}.get(b['source']['direction'])
            check(delta is not None and all(1 <= v+delta[i] <= 10 for i, v in enumerate(b['source']['position'])), where+' source points inside')
            sr, sc = b['source']['position']
            room = {'up': sr-1, 'right': 10-sc, 'down': 10-sr, 'left': sc-1}
            check(_strict(room[b['source']['direction']], max(room.values())), where+' source faces longest available grid distance')
            maxSpaceRounds += 1
            if kinds[ri] in ['H', 'V']:
                check(any(c == 'red' or c == 'blue' for c in r['target_colors'].values()), where+' filter axis requires a nonwhite target')
                axisRounds += 1
            targetKey = js_dumps([_get(r['target_colors'], 'S1'), _get(r['target_colors'], 'S2')])
            check(targetKey not in usedTargets, where+' target pairs must be unique within a problem')
            usedTargets.add(targetKey)
            directions[b['source']['direction']] += 1
            q = r['constraints'][0]
            if _strict(_get(q, 'type'), 'region'):
                check(all(not _strict(regionId(*e['position']), q['id']) for _, e in entities), where+' designated region must contain no source, filter or sensor')
                emptyRegions += 1
                regionIds.add(q['id'])
            outsideCentralFilters += sum(any(v < 4 or v > 7 for v in f['position']) for f in b['filters'])
            closeSensors += sum(_distance(b['source']['position'], s['position']) < 7 for s in b['sensors'])
            if any(any(_distance(a['position'], other['position']) == 1 for _, other in entities[i+1:]) for i, (_, a) in enumerate(entities)):
                adjacentObjects += 1
            geometry = geometryKey(b)
            check(geometry not in seen, where+' duplicate geometry including rotations/reflections/translations')
            seen.add(geometry)
            sources.add(join(b['source']['position']))
            check(_same(sorted(r['target_colors']), ['S1', 'S2']) and all(c in ['red', 'blue', 'white'] for c in r['target_colors'].values()), where+' targets')
            for s in ['S1', 'S2']:
                colorCounts[s][r['target_colors'][s]] += 1
            if ri:
                old = p['rounds'][ri-1]
                ob = old['board']
                changed = [s for s in ['S1', 'S2'] if not _strict(old['target_colors'][s], r['target_colors'][s])]
                check(len(changed) >= 1, where+' adjacent target pairs must differ')
                check(_same(_get(r, 'changed_sensors'), changed) and 'changed_sensor' not in r, where+' target transition metadata')
                changeCounts[str(len(changed))] += 1
                if _strict(ob['source']['direction'], b['source']['direction']):
                    repeatedDirections += 1
                check(all(all(not _same(f['position'], of['position']) for of in ob['filters']) for f in b['filters'])
                      and all(all(not _same(s['position'], os['position']) for os in ob['sensors']) for s in b['sensors']), where+' both position sets move')
                maxStep = max(maxStep, _distance(ob['source']['position'], b['source']['position']))
                c = certificates['problems'][pi]['transitions'][ri-1]
                theorem = proveSuccessExclusion(b, old, r)
                check(_strict(_get(c, 'theorem'), theorem) and _strict(_get(c, 'scope'), 'both_rule_evaluations_on_current_round_board')
                      and _same(_get(c, 'reference_board'), b) and _get(c, 'device_count_limit') is None
                      and _same(_get(c, 'previous_targets'), old['target_colors']) and _same(_get(c, 'current_targets'), r['target_colors']), where+' same-current-board proof')
                check(_strict(_get(c, 'status'), 'proven_success_exclusive' if theorem else 'not_claimed'), where+' no false exclusivity claim for repeated targets')
                if theorem:
                    proven += 1
                transitions += 1
                oldLayout = w['rounds'][ri-1]['layout']
                a, prev = _replay(oldLayout, b, r), _replay(oldLayout, b, old)
                check(not a['success'] and _same(a, _get(wr, 'previous_layout_current'))
                      and _same(prev, _get(wr, 'previous_layout_previous_rule')), where+' witness replay')
                check(_get(c, 'previous_stored_witness_current_success') is False
                      and _strict(_get(c, 'previous_stored_witness_previous_rule_satisfied'), prev['rule_satisfied']), where+' replay certificate')
                if not a['valid']:
                    invalidReplays += 1
            else:
                check(_same(_get(r, 'changed_sensors'), []) and 'changed_sensor' not in r, where+' first target')
            optics = simulate(b, wr['layout'], r)
            evaluation = evaluate(r, optics)
            check(evaluation['success'] and _same(optics, _get(wr, 'optics')) and _same(evaluation, _get(wr, 'evaluation')), where+' stored solution trace and evaluation')
            if kinds[ri] in ['H', 'V']:
                check(len(optics['filter_arrivals']) > 0, where+' successful axis witness reaches a filter')
                axisWitnessesWithFilterVisit += 1
            mirrors = sum(x['type'] == 'mirror' for x in wr['layout']['placements'])
            splitters = sum(x['type'] == 'splitter' for x in wr['layout']['placements'])
            check(4 <= mirrors <= 5 and splitters == 1 and _strict(mirrors, _get(wr, 'mirror_count'))
                  and _strict(_get(wr, 'device_count'), mirrors+1), where+' witness complexity')
            totalMirrors += mirrors
            verified += 1
        check(20 <= totalMirrors <= 23, tag+' witness complexity')
    check(all(v == 20 for v in first.values()), 'first rule balance')
    st = dataset['settings']
    check(_same(regionCounts, _get(st, 'region_design_counts')) and regionCounts['empty_independent_regions'] == 100 and emptyRegions == 200, 'all pass/avoid regions empty in their active round')
    check(_same(_get(st, 'adjacent_rule_pairs'), []) and _strict(_get(st, 'rule_order_sampling'), 'random_permutation_with_balanced_first_rule') and _strict(_get(st, 'first_rule_counts_per_type'), 20), 'no paired rule adjacency requirement')
    check(_same(_get(st, 'filter_rows_and_cols'), list(range(1, 11))) and _get(st, 'sensor_min_manhattan_from_source') is None and _get(st, 'min_filter_sensor_separation') is None and _get(st, 'object_min_manhattan_distance') is None, 'full grid and no minimum object spacing')
    check(_same(_get(st, 'designated_region_ids'), [chr(65+i) for i in range(25)]) and _get(st, 'pass_avoid_regions_independently_sampled') is True and _get(st, 'designated_regions_must_be_empty') is True and _strict(_get(st, 'designated_region_empty_scope'), 'active_round'), 'independent empty regions across the full grid')
    check(_get(st, 'sensor_color_changes_per_transition') is None and _get(st, 'sensor_changes_alternate') is False and _strict(_get(st, 'target_color_sampling'), 'random_pairs_without_replacement_per_problem_reject_axis_white_white') and _get(st, 'target_pair_repeat_allowed') is False and _get(st, 'target_pairs_unique_within_problem') is True and _get(st, 'adjacent_target_pairs_must_differ') is True and _get(st, 'filter_axis_requires_nonwhite_target') is True, 'random colors without repeated pairs within a problem and meaningful filter-axis targets')
    check(_strict(_get(st, 'source_direction_sampling'), 'max_distance_to_grid_boundary_random_ties') and _get(st, 'source_direction_repeat_allowed') is True and _get(st, 'target_schedule_frozen_before_geometry_search') is True, 'inward source direction policy')
    check(_get(st, 'full_success_exclusion_required') is False and _strict(_get(certificates, 'transitions_proven'), proven) and _strict(_get(certificates, 'transitions_not_claimed'), 400-proven), 'exclusion audit counts')
    check(_strict(_get(st, 'max_attempts_per_round'), 20) and _strict(_get(st, 'consecutive_correct_to_advance'), 2) and _get(st, 'device_count_limit') is None, 'T/k')
    check(js_truthy(_get(st, 'source_changes_every_round')) and js_truthy(_get(st, 'filters_change_every_round')) and js_truthy(_get(st, 'sensors_change_every_round')) and len(st['fixed_objects']) == 0, 'all positions change')
    check(_get(st, 'source_max_manhattan_distance_from_initial') is None and _get(st, 'source_max_step_manhattan_distance') is None and _get(st, 'initial_source_at_corner') is False and maxStep > 2, 'unrestricted movement')
    check(_strict(_get(st, 'per_reference_board'), 'current_round') and _get(st, 'per_previous_target_colors_used') is False, 'PER')
    return {'passed': True, 'problems': 100, 'rounds': 500, 'solution_witnesses_verified': verified,
            'unique_round_geometries': len(seen), 'unique_source_cells': len(sources), 'max_source_step': maxStep,
            'first_rule_counts': first, 'region_design_counts': regionCounts,
            'problems_with_nonadjacent_filter_axes': nonadjacentAxes, 'problems_with_nonadjacent_pass_avoid': nonadjacentRegions,
            'problems_with_different_pass_avoid_regions': differentRegions, 'empty_designated_region_rounds': emptyRegions,
            'distinct_designated_regions_used': len(regionIds), 'distinct_rule_orders': len(orders),
            'filters_outside_previous_central_area': outsideCentralFilters, 'rounds_with_adjacent_objects': adjacentObjects,
            'sensors_closer_than_previous_minimum': closeSensors, 'current_board_transitions_audited': transitions,
            'current_board_success_exclusion_verified': proven, 'exclusion_not_claimed_for_repeated_targets': 400-proven,
            'target_change_count_histogram': changeCounts, 'source_direction_counts': directions,
            'repeated_direction_transitions': repeatedDirections, 'target_color_counts': colorCounts,
            'invalid_previous_witness_replays': invalidReplays, 'filter_axis_rounds': axisRounds,
            'filter_axis_witnesses_with_filter_visit': axisWitnessesWithFilterVisit,
            'source_points_to_maximum_space_count': maxSpaceRounds, 'paid_api_calls': 0}
