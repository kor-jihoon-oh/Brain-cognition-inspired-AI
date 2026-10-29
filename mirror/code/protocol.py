"""Submission schema and model-visible inputs with a strict public-board allowlist."""
from pathlib import Path
from optics import boardForRound
from serialization import js_dumps

OUTPUT_SCHEMA = {
    'type':'object','additionalProperties':False,'required':['placements'],
    'properties':{'placements':{'type':'array','items':{
        'type':'object','additionalProperties':False,
        'required':['type','row','col','direction'],
        'properties':{
            'type':{'type':'string','enum':['mirror','splitter']},
            'row':{'type':'integer','minimum':1,'maximum':10},
            'col':{'type':'integer','minimum':1,'maximum':10},
            'direction':{'type':'string','enum':['R','L']}
        }
    }}}
}


def publicBoard(board):
    # Preserve this allowlist and property order. No private rule/target leaks.
    return {key:board[key] for key in ('grid_size','source','filters','sensors')}


def initialPrompt(problem, prompt=None):
    if prompt is None:
        prompt = (Path(__file__).resolve().parents[1]/'dataset'/'prompt_en.txt').read_bytes().decode('utf-8')
    # Accept BOM and NBSP as input whitespace, but not U+0085.
    whitespace = '\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
    return prompt.strip(whitespace)+'\n\n'+updatedBoardInput(problem,0)


def updatedBoardInput(problem, roundIndex):
    return 'Current board. Submit your complete device layout for this board.\n'+js_dumps(publicBoard(boardForRound(problem,roundIndex)))
