"""Canonical board geometry for rotation, reflection, and translation deduplication."""
import json
from .optics import DELTA, DIRECTIONS

def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def geometry_key(board):
    """Ignore filter colors and sensor IDs when detecting geometrically equivalent boards."""
    pts = [board['source']['position']] + [x['position'] for x in board['filters'] + board['sensors']]
    vec = DELTA[DIRECTIONS.index(board['source']['direction'])]
    variants = []
    for flip in (False, True):
        for turns in range(4):

            def tr(p):
                r, c = p
                if flip:
                    c = -c
                for _ in range(turns):
                    r, c = (c, -r)
                return (r, c)
            pp = list(map(tr, pts))
            mr = min((p[0] for p in pp))
            mc = min((p[1] for p in pp))
            pp = [(r - mr, c - mc) for r, c in pp]
            variants.append((pp[0], tr(vec), tuple(sorted(pp[1:3])), tuple(sorted(pp[3:]))))
    return min(variants)
