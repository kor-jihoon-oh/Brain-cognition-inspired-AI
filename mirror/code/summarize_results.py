"""Summarize completed problems from saved JSON reports without API requests.

MPE and mean error are macro averages: average within each problem first,
then across problems. Skipped rounds are not assigned zero error.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
from statistics import mean


def summarize(problems, rounds, config):
    """Compute metrics for completed problems, excluding any partial problem."""
    completed = [p for p in problems if p['status'] == 'completed']
    by_problem = defaultdict(list)
    completed_ids = {p['problem'] for p in completed}
    for r in rounds:
        if r['problem'] in completed_ids:
            by_problem[r['problem']].append(r)
    mpe_values, error_values = [], []
    successful = failed = skipped = submissions = persistent = incorrect = 0
    per_problem = []
    for p in completed:
        rr = by_problem[p['problem']]
        if len(rr) != config['rounds'] or len({r['round'] for r in rr}) != len(rr):
            raise ValueError('Incomplete round reports; retry after the next report update.')
        active = [r for r in rr if r['attempts'] > 0]
        if not active or any(r['status'] not in ('success', 'failed') for r in active):
            raise ValueError('Completed problem contains incomplete rounds.')
        if sum(r['attempts'] for r in active) != p['attempts']:
            raise ValueError('Problem and round reports differ; retry after the next report update.')
        pers, errors = [], []
        for r in active:
            if r['correct_count'] + r['incorrect_count'] != r['attempts']:
                raise ValueError('Round submission counts do not sum correctly.')
            errors.append(100 * r['incorrect_count'] / r['attempts'])
            if r['round'] > 1 and r['per_eligible']:
                pers.append(100 * r['previous_rule_error_count'] / r['attempts'])
            successful += r['reached_k'] is True
            failed += r['status'] == 'failed'
            submissions += r['attempts']
            incorrect += r['incorrect_count']
            persistent += r['previous_rule_error_count']
        skipped += sum(r['skipped'] is True for r in rr)
        problem_mpe = mean(pers) if pers else None
        problem_error = mean(errors)
        if problem_mpe is not None:
            mpe_values.append(problem_mpe)
        error_values.append(problem_error)
        per_problem.append({'problem':p['problem'], 'mpe_percent':problem_mpe,
                            'mean_error_percent':problem_error,
                            'successful_rounds':sum(r['reached_k'] is True for r in active)})

    unknown_time = sum(p['unknown_time_calls'] for p in completed)
    unpriced = sum(p['unpriced_calls'] for p in completed)
    known_seconds = sum(p['known_api_seconds'] for p in completed)
    known_cost = sum(p['known_cost_subtotal_usd'] for p in completed)
    start = min((p['first_request_at'] for p in completed if p['first_request_at']), default=None)
    end = max((p['last_response_at'] for p in completed if p['last_response_at']), default=None)
    elapsed = ((datetime.fromisoformat(end.replace('Z','+00:00')) -
                datetime.fromisoformat(start.replace('Z','+00:00'))).total_seconds()
               if start and end else None)
    return {
        'model':config['model'], 'T':config['T'], 'k':config['k'],
        'planned_problems':len(problems), 'completed_problems':len(completed),
        'mpe_included_problems':len(mpe_values),
        'mpe_percent':mean(mpe_values) if mpe_values else None,
        'mean_error_percent':mean(error_values) if error_values else None,
        'all_rounds_passed_problems':sum(p['successful_rounds']==config['rounds'] for p in per_problem),
        'successful_rounds':successful, 'failed_rounds':failed, 'skipped_rounds':skipped,
        'planned_rounds_in_completed_problems':len(completed)*config['rounds'],
        'attempted_rounds':successful+failed, 'submissions':submissions,
        'correct_submissions':submissions-incorrect, 'persistent_errors':persistent,
        'ordinary_errors':incorrect-persistent,
        'api_calls':sum(p['api_calls'] for p in completed),
        'api_seconds':None if unknown_time else known_seconds,
        'known_api_seconds':known_seconds, 'unknown_time_calls':unknown_time,
        'estimated_cost_usd':None if unpriced else known_cost,
        'known_cost_subtotal_usd':known_cost, 'unpriced_calls':unpriced,
        'first_request_at':start, 'last_response_at':end,
        'wall_elapsed_seconds_including_pauses':elapsed,
        'scope':'Completed problems only; partial problems are excluded from every metric.',
        'aggregation':'MPE: mean eligible round PER per problem, then mean of defined problem MPEs. Mean error: mean attempted round error per problem, then mean across completed problems.',
        'time_definition':'API time sums request durations. Wall elapsed includes pauses between requests.',
        'cost_definition':'Estimate from recorded API usage and the saved pricing snapshot, not an invoice.',
        'per_problem':per_problem,
    }


def summarize_folder(folder):
    folder = Path(folder)
    read = lambda name: json.loads((folder/name).read_text(encoding='utf-8-sig'))
    return summarize(read('problems.json'), read('rounds.json'), read('config.json'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-folder', type=Path, help='Saved run directory; defaults to the latest run.')
    args = parser.parse_args()
    folder = args.run_folder
    if folder is None:
        root = Path(__file__).resolve().parents[1]/'result'
        runs = sorted(p for p in root.iterdir() if (p/'config.json').is_file()) if root.exists() else []
        if not runs:
            parser.error('No saved result found; pass --run-folder or run an experiment first.')
        folder = runs[-1]
    try:
        report = summarize_folder(folder)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f'Cannot summarize results: {exc}\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
