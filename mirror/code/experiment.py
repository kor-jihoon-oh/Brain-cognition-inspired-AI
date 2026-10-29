"""Build model requests and advance attempts, rounds, and problem state."""
from copy import deepcopy
import hashlib
from optics import judge, boardForRound
from protocol import initialPrompt, updatedBoardInput, publicBoard, OUTPUT_SCHEMA
from serialization import js_dumps, js_truthy, UNDEFINED

PROTOCOL = 'mirror-light-all-moving-v3-flexible-layout-sensor-feedback-en-every-attempt-no-rule-disclosure-v4-correct-incorrect-100x5-p001-100-k2-T20'
CONFIG = {
    'model':'gpt-6.1-sol','reasoning_effort':'medium','k':2,'T':20,'rounds':5,'problems':100,
    'source_dataset_problems':100,'first_problem':1,'last_problem':100,
    'model_input_language':'en','prompt_file':'prompt_en.txt','board_delivery':'every_attempt',
    'rule_existence_disclosed':False,
    'feedback':'per-sensor-color-correctness-and-overall-correct-incorrect-only',
    'sensor_feedback_definition':'actual_current_trace_matches_target_ignoring_rule_pass_flag',
    'feedback_received_colors':False,'feedback_rule_violation_reason':False,
    'history':'complete-output-items-with-encrypted-reasoning-per-problem','reasoning_context':'all_turns',
    'after_failed_round':'skip_remaining_rounds_if_no_correct_submission','local_timeout_ms':0,'service_tier':'default',
    'per_definition':'previous_round_any_full_success AND current_valid_failure AND same_layout_on_current_board_satisfies_previous_rule_IGNORING_previous_target_colors',
    'per_reference_board':'current_round','source_policy':'all_grid_cells_no_initial_or_step_distance_limit',
    'board_policy':'source_both_filters_both_sensors_move_every_round',
    'object_min_manhattan_distance':None,'filter_position_domain':'all_grid_cells',
    'rule_order_sampling':'random_permutation_with_balanced_first_rule',
    'pass_avoid_regions':'independent_all_25_regions_empty_in_active_round',
    'target_color_sampling':'random_pairs_without_replacement_per_problem_reject_axis_white_white',
    'target_pairs_unique_within_problem':True,
    'adjacent_target_pairs_must_differ':True,
    'filter_axis_requires_nonwhite_target':True,
    'source_direction_sampling':'max_distance_to_grid_boundary_random_ties',
    'device_types':['mirror','splitter'],'device_count_limit':None
}


def hash(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
    else:
        text = value if isinstance(value,str) else js_dumps(value)
        if text is UNDEFINED:
            raise TypeError('The data argument must be a string or bytes')
        # Node's UTF-8 encoder replaces lone UTF-16 surrogates with U+FFFD.
        text = text.encode('utf-16-le','surrogatepass').decode('utf-16-le','replace')
        data = text.encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def createState(dataset,prompt):
    return {'dataset':dataset,'prompt':prompt,'problemIndex':0,'roundIndex':0,
            'attempt':1,'streak':0,'history':[],'attempts':[],'rounds':[],
            'skippedRounds':[],'done':False}


def ensureHistory(s):
    if not s['history'] and not s['done']:
        s['history']=[{'role':'user','content':initialPrompt(s['dataset']['problems'][s['problemIndex']],s['prompt'])}]


def buildRequest(s):
    if s['done']:
        raise ValueError('The experiment is complete')
    ensureHistory(s)
    return {'model':CONFIG['model'],'input':deepcopy(s['history']),
            'reasoning':{'effort':CONFIG['reasoning_effort'],'context':CONFIG['reasoning_context']},
            'tools':[],'tool_choice':'none','store':False,'stream':False,
            'truncation':'disabled','include':['reasoning.encrypted_content'],
            'service_tier':CONFIG['service_tier'],'prompt_cache_options':{'mode':'implicit','ttl':'30m'},
            'text':{'format':{'type':'json_schema','name':'optical_layout','strict':True,'schema':deepcopy(OUTPUT_SCHEMA)}}}


def responseText(response):
    content=[c for x in (response.get('output') or []) if x.get('type')=='message' for c in (x.get('content') or [])]
    # Array.join treats null/undefined as empty strings.
    text=''.join(x.get('text') or '' for x in content if x.get('type')=='output_text')
    return text or '\n'.join(x.get('refusal') or '' for x in content if x.get('type')=='refusal')


def completeResponse(response):
    if not isinstance(response,dict) or response.get('status')!='completed':
        return False
    output=response.get('output')
    return (isinstance(output,list) and any(isinstance(x,dict) and x.get('type')=='message' for x in output)
            and all(isinstance(x,dict) and x.get('type') in ('reasoning','message') for x in output)
            and all(x.get('role')=='assistant' and x.get('status') not in ('in_progress','incomplete') for x in output if x.get('type')=='message'))


def applyResponse(s,response,call):
    if s['done'] or not completeResponse(response):
        raise ValueError('Cannot apply an incomplete or unsupported response')
    ensureHistory(s)
    p=s['dataset']['problems'][s['problemIndex']]
    r=s['roundIndex']+1
    prev=next((x for x in s['rounds'] if x['problem']==p['problem_number'] and x['round']==r-1),None)
    previous_success=prev is not None and prev['correct_count']>0
    verdict=judge(p,s['roundIndex'],responseText(response),previous_success)
    s['streak']=s['streak']+1 if verdict['correct'] else 0
    ended=s['streak']>=CONFIG['k'] or s['attempt']>=CONFIG['T']
    row={
        'problem':p['problem_number'],'problem_id':p['problem_id'],'round':r,'attempt':s['attempt'],'call_id':call['call_id'],
        'response_id':response.get('id'),'served_model':response.get('model'),
        'effective_reasoning_context':(response.get('reasoning') or {}).get('context'),
        'submitted_at':call.get('finished_at',UNDEFINED),
        'started_at':call.get('started_at'),'finished_at':call.get('finished_at'),
        'experiment_submission':len(s['attempts'])+1,
        'problem_submission':sum(a['problem']==p['problem_number'] for a in s['attempts'])+1,
        'raw_output':responseText(response),**verdict,'previous_rule_error':verdict['previous_rule_only_error'],
        'current_source':deepcopy(boardForRound(p,s['roundIndex'])['source']),
        'current_board':publicBoard(boardForRound(p,s['roundIndex'])),'next_model_input':verdict['feedback'],
        'previous_round_had_success':previous_success,'previous_round_reached_k':prev.get('reached_k',False) if prev else False,
        'consecutive_correct':s['streak'],'round_ended':ended,
        'round_end_reason':('k_consecutive_correct' if s['streak']>=CONFIG['k'] else 'attempt_limit') if ended else None,
        'problem_ended':False,'problem_end_reason':None,'skipped_rounds':[],
        'current_constraints':deepcopy(p['rounds'][s['roundIndex']]['constraints']),
        'previous_constraints':deepcopy(p['rounds'][s['roundIndex']-1]['constraints']) if s['roundIndex'] else None,
        'current_target':deepcopy(p['rounds'][s['roundIndex']]['target_colors']),
        'previous_target':deepcopy(p['rounds'][s['roundIndex']-1]['target_colors']) if s['roundIndex'] else None,
        'usage':response.get('usage'),'api_seconds':call.get('api_seconds'),'cost':call.get('cost',UNDEFINED)
    }
    s['attempts'].append(row)
    row['round_incorrect_so_far']=sum(a['problem']==p['problem_number'] and a['round']==r and not a['correct'] for a in s['attempts'])
    row['problem_incorrect_so_far']=sum(a['problem']==p['problem_number'] and not a['correct'] for a in s['attempts'])
    # Retain opaque reasoning exactly, including encrypted_content.
    s['history'].extend(deepcopy(response['output']))
    s['history'].append({'role':'user','content':row['feedback']})
    if ended:
        rr=[a for a in s['attempts'] if a['problem']==p['problem_number'] and a['round']==r]
        correctCount=sum(bool(a['correct']) for a in rr)
        s['rounds'].append({'problem':p['problem_number'],'round':r,'attempts':len(rr),
                            'correct_count':correctCount,'reached_k':s['streak']>=CONFIG['k'],
                            'end_reason':row['round_end_reason'],'previous_round_had_success':previous_success})
        if correctCount==0:
            for next_round in range(r+1,CONFIG['rounds']+1):
                s['skippedRounds'].append({'problem':p['problem_number'],'round':next_round,
                    'reason':'earlier_round_without_correct_submission','blocked_by_round':r})
                row['skipped_rounds'].append(next_round)
        s['roundIndex']+=1
        s['attempt']=1
        s['streak']=0
        if correctCount==0 or s['roundIndex']==CONFIG['rounds']:
            row['problem_ended']=True
            row['problem_end_reason']='round_without_correct_submission' if correctCount==0 else 'all_rounds_finished'
            s['problemIndex']+=1
            s['roundIndex']=0
            s['history']=[]
            s['done']=s['problemIndex']==len(s['dataset']['problems'])
    else:
        s['attempt']+=1
    if not row['problem_ended']:
        row['next_public_board']=publicBoard(boardForRound(p,s['roundIndex']))
        row['next_model_input']=row['feedback']+'\n'+updatedBoardInput(p,s['roundIndex'])
        s['history'][-1]['content']=row['next_model_input']
    return row
