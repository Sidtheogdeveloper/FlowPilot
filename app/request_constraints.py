"""Trusted explicit user constraints: ambiguous values require clarification."""
import re,math
from difflib import SequenceMatcher
_MARKER=re.compile(r'\bIQR[\s_-]+multiplier\b',re.I)
_NUMBER=re.compile(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z')

def extract_constraints(request:str)->dict:
    for possible in re.finditer(r'\bIQR[\s_-]+([a-z]+)',request,re.I):
        word=possible.group(1).lower()
        if word!='multiplier' and SequenceMatcher(None,word,'multiplier').ratio()>.72:raise ValueError('Ambiguous IQR parameter spelling; use IQR multiplier followed by one number')
    if re.search(r'\bIQRmultiplier\b|\bIQR[\s_-]+multiplier(?=\d)',request,re.I):raise ValueError('Use IQR multiplier followed by one number')
    values=[]
    for marker in _MARKER.finditer(request):
        rest=request[marker.end():]
        rest=re.sub(r'^\s*(?:(?:=|:|of\b|is\b|to\b)\s*)?','',rest,count=1,flags=re.I)
        token_match=re.match(r'([^\s,;]+)',rest)
        if not token_match or not _NUMBER.fullmatch(token_match.group(1)):raise ValueError('IQR multiplier must be one explicit numeric value between 0.1 and 10')
        value=float(token_match.group(1))
        if not math.isfinite(value) or not .1<=value<=10:raise ValueError('IQR multiplier must be between 0.1 and 10')
        remainder=rest[token_match.end():]
        if re.match(r'\s*(?:or\b|and\b|to\b|/|[-–,])\s*[+-]?(?:\d|\.\d)',remainder,re.I):raise ValueError('Ambiguous IQR multipliers; supply exactly one value')
        values.append(value)
    if values and any(value!=values[0] for value in values):raise ValueError('Conflicting explicit IQR multipliers; supply exactly one value')
    return {'iqr_multiplier':values[0]} if values else {}

def enforce_plan_constraints(plan:dict,constraints:dict):
    if 'iqr_multiplier' in constraints:
        for task in plan['tasks']:
            if task.get('handler')=='flag_unusual_amounts':task.setdefault('parameters',{})['iqr_multiplier']=constraints['iqr_multiplier']
    return plan
