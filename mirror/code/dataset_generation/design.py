"""Build rule and board records for the 10x10 benchmark."""
from .optics import regions
RULES = {'H': dict(type='filter_axis', value='horizontal'), 'V': dict(type='filter_axis', value='vertical'), 'N': dict(type='different_color_overlap', value='forbidden')}

def rule(k, z='C'):
    return RULES[k].copy() if k in RULES else dict(type='region', id=z, value='pass' if k == 'P' else 'avoid')

def rr(k, colors, z='C'):
    return dict(constraints=[rule(k, z)], target_colors=dict(zip(['S1', 'S2'], colors)))

def board(source, red, blue, s1, s2, direction='right'):
    return dict(grid_size=10, source=dict(position=source, direction=direction, color='white'), filters=[dict(id='F1', position=red, color='red'), dict(id='F2', position=blue, color='blue')], sensors=[dict(id='S1', position=s1), dict(id='S2', position=s2)], regions=regions())
