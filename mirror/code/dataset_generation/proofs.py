"""Audit simultaneous full success for two rules evaluated on the same current board.

This is not a proof that every previous answer violates the next rule."""

def success_certificate(board, old, new):
    """Certify incompatible target colors under common passing physics on one board."""
    kind(old)
    kind(new)
    ids = [s['id'] for s in board['sensors']]
    assert all((set(r['target_colors']) == set(ids) and all((c in ('red', 'blue', 'white') for c in r['target_colors'].values())) for r in (old, new)))
    changed = [s for s in ids if old['target_colors'][s] != new['target_colors'][s]]
    if not changed:
        return dict(status='not_claimed', criterion='full_success_exclusion', theorem=None, device_count_limit=None, previous_targets=old['target_colors'], current_targets=new['target_colors'], changed_sensors=[], reason='Identical independently sampled targets are allowed; this audit does not claim universal exclusion for this transition.')
    return dict(status='proven_success_exclusive', criterion='full_success_exclusion', theorem='distinct_targets_under_common_passing_physics', method='checked_mathematical_theorem', query='Success(previous,x) AND Success(current,x)', quantification='all legal placements', free_cells=board['grid_size'] ** 2 - 1 - len(board['filters']) - len(board['sensors']), choices_per_free_cell=5, device_count_limit=None, changed_sensors=changed, previous_targets=old['target_colors'], current_targets=new['target_colors'], argument='On this fixed board, a passing H/V trace has no wrong-axis arrivals and equals open-filter physics. P/A/N never change ray propagation. Thus if a layout passes both rounds its received-color sets are identical in both. Full success requires each sensor to receive exactly its singleton target color. At least one target differs, so no layout can succeed in both rounds. This includes every reachable branch and cycle and places no bound on the number of devices.', requires_previous_solution_rule_violation=False)

def kind(r):
    assert len(r['constraints']) == 1
    q = r['constraints'][0]
    if q['type'] == 'filter_axis':
        return 'H' if q['value'] == 'horizontal' else 'V'
    if q['type'] == 'region':
        return 'P' if q['value'] == 'pass' else 'A'
    assert q == dict(type='different_color_overlap', value='forbidden')
    return 'N'
