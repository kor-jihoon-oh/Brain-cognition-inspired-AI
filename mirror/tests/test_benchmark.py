"""Offline regression tests for physics, feedback, PER, and round advancement."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'code'))
from optics import simulate, evaluate, judge, regionId
from protocol import publicBoard, updatedBoardInput
from experiment import createState, applyResponse, buildRequest
from summarize_results import summarize, summarize_folder


class BenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        read = lambda name: json.loads((ROOT/'dataset'/name).read_text(encoding='utf-8'))
        cls.data = read('private/dataset.json')
        cls.witnesses = read('private/solution_witnesses.json')
        cls.prompt = (ROOT/'dataset/prompt_en.txt').read_text(encoding='utf-8')

    def test_all_stored_solutions_reproduce_their_paths(self):
        for p, w in zip(self.data['problems'], self.witnesses['problems']):
            for r, solution in zip(p['rounds'], w['rounds']):
                with self.subTest(problem=p['problem_number'], round=r['round_index']):
                    trace = simulate(r['board'], solution['layout'], r)
                    self.assertEqual(trace, solution['optics'])
                    self.assertTrue(evaluate(r, trace)['success'])

    def test_public_board_omits_private_information(self):
        for p in self.data['problems']:
            for i, r in enumerate(p['rounds']):
                self.assertEqual(set(publicBoard(r['board'])), {'grid_size','source','filters','sensors'})
                board = json.loads(updatedBoardInput(p, i).split('\n', 1)[1])
                self.assertEqual(board, publicBoard(r['board']))

    def test_previous_rule_uses_current_board_and_ignores_previous_targets(self):
        p = deepcopy(self.data['problems'][0])
        layout = self.witnesses['problems'][0]['rounds'][1]['layout']
        p['rounds'][0]['constraints'] = deepcopy(p['rounds'][1]['constraints'])
        # One current color is deliberately wrong; the path still satisfies the rule.
        target = p['rounds'][1]['target_colors']
        target['S1'] = next(c for c in ('red','blue','white') if c != target['S1'])
        p['rounds'][0]['target_colors'] = deepcopy(target)
        verdict = judge(p, 1, json.dumps(layout), True)
        self.assertTrue(verdict['valid'])
        self.assertFalse(verdict['correct'])
        self.assertTrue(verdict['previous_rule_accepts'])
        self.assertFalse(verdict['previous_full_success'])
        self.assertTrue(verdict['previous_rule_only_error'])
        self.assertEqual(verdict['per_reference_geometry'], p['rounds'][1]['board'])
        self.assertFalse(judge(p, 1, json.dumps(layout), False)['previous_rule_only_error'])
        self.assertFalse(judge(p, 1, '{invalid json}', True)['previous_rule_only_error'])

    def test_previous_avoid_region_includes_source_cell(self):
        p = deepcopy(self.data['problems'][0])
        layout = self.witnesses['problems'][0]['rounds'][1]['layout']
        region = regionId(*p['rounds'][1]['board']['source']['position'])
        p['rounds'][0]['constraints'] = [{'type':'region','id':region,'value':'avoid'}]
        verdict = judge(p, 1, json.dumps(layout), True)
        self.assertFalse(verdict['previous_rule_accepts'])
        self.assertFalse(verdict['previous_rule_only_error'])

    def test_filter_bypass_does_not_violate_axis_rule(self):
        found = False
        for p in self.data['problems']:
            for r in p['rounds']:
                if r['constraints'][0]['type'] != 'filter_axis':
                    continue
                trace = simulate(r['board'], {'placements':[]}, r)
                if not trace['filter_arrivals']:
                    self.assertTrue(evaluate(r, trace)['rule_satisfied'])
                    self.assertFalse(evaluate(r, trace)['success'])
                    found = True
                    break
            if found:
                break
        self.assertTrue(found)

    def test_feedback_has_only_sensor_and_overall_judgments(self):
        p = self.data['problems'][0]
        layout = self.witnesses['problems'][0]['rounds'][0]['layout']
        verdict = judge(p, 0, json.dumps(layout), False)
        self.assertEqual(json.loads(verdict['feedback']), {
            'sensor_results':{'S1':'correct','S2':'correct'}, 'result':'correct'})
        invalid = judge(p, 0, json.dumps({'placements':[{'type':'mirror','row':11,'col':1,'direction':'R'}]}),False)
        self.assertFalse(invalid['valid'])
        self.assertEqual(json.loads(invalid['feedback']), {
            'sensor_results':{'S1':'incorrect','S2':'incorrect'},'result':'incorrect'})

    def test_two_consecutive_correct_submissions_advance_round(self):
        state = createState(self.data, self.prompt)
        layout = self.witnesses['problems'][0]['rounds'][0]['layout']
        for call_id in (1, 2):
            response = {'id':f'offline-{call_id}','status':'completed','output':[{
                'type':'message','role':'assistant','status':'completed',
                'content':[{'type':'output_text','text':json.dumps(layout)}]}]}
            call = {'call_id':call_id,'started_at':'2026-01-01T00:00:00Z','finished_at':'2026-01-01T00:00:01Z'}
            row = applyResponse(state, response, call)
            self.assertEqual(row['consecutive_correct'], call_id)
            self.assertEqual(state['roundIndex'], 0 if call_id == 1 else 1)
        request = buildRequest(state)
        self.assertEqual(request['reasoning'], {'effort':'medium','context':'all_turns'})
        self.assertIn(updatedBoardInput(self.data['problems'][0], 1), request['input'][-1]['content'])

    def test_archived_metrics_use_problem_macro_averages(self):
        folder = ROOT/'result/2026-10-28_16-50-18.540_KST'
        if not (folder/'config.json').exists():
            self.skipTest('Archived result not included in this checkout.')
        report = summarize_folder(folder)
        self.assertEqual(report['completed_problems'], 100)
        self.assertEqual(report['mpe_included_problems'], 85)
        self.assertAlmostEqual(report['mpe_percent'], 38.80131160170088)
        self.assertEqual(report['all_rounds_passed_problems'], 54)
        self.assertEqual(report['successful_rounds'], 337)
        self.assertEqual(report['submissions'], 2734)

    def test_summary_excludes_partial_problems_and_skipped_rounds(self):
        resource = {'api_calls':0,'unknown_time_calls':0,'unpriced_calls':0,
                    'known_api_seconds':0,'known_cost_subtotal_usd':0,
                    'first_request_at':None,'last_response_at':None}
        problems = [dict(resource, problem=1, status='completed', attempts=20),
                    dict(resource, problem=2, status='completed', attempts=6),
                    dict(resource, problem=3, status='in_progress', attempts=1)]
        rounds = []
        for problem in (1,2):
            for i in range(1,6):
                n = (20 if i == 1 else 0) if problem == 1 else (2 if i == 1 else 4 if i == 2 else 0)
                correct = 0 if problem == 1 else 2 if i <= 2 else 0
                rounds.append({'problem':problem,'round':i,'attempts':n,
                               'correct_count':correct,'incorrect_count':n-correct,
                               'previous_rule_error_count':1 if problem==2 and i==2 else 0,
                               'status':'skipped' if n==0 else 'failed' if problem==1 else 'success',
                               'skipped':n==0,'reached_k':problem==2 and n>0,
                               'per_eligible':problem==2 and i==2})
        report = summarize(problems, rounds, {'model':'offline-test','T':20,'k':2,'rounds':5})
        self.assertEqual(report['completed_problems'], 2)
        self.assertEqual(report['mpe_included_problems'], 1)
        self.assertEqual(report['mpe_percent'], 25)
        self.assertEqual(report['mean_error_percent'], 62.5)  # mean(100, mean(0, 50))
        self.assertEqual(report['submissions'], 26)
        problems[0]['unpriced_calls'] = 1
        self.assertIsNone(summarize(problems, rounds, {'model':'offline-test','T':20,'k':2,'rounds':5})['estimated_cost_usd'])


if __name__ == '__main__':
    unittest.main()
