"""Compare actual offline ASR of each P09 sentence; no subjective voice claim."""
import json
from pathlib import Path
import sys
import unicodedata

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from dictionforge.audio import atomic_json

def normalized(text):
    return ''.join(c.lower() for c in text if unicodedata.category(c)[0] in {'L','N'})

def distance(a,b):
    row=list(range(len(b)+1))
    for i,left in enumerate(a,1):
        next_row=[i]
        for j,right in enumerate(b,1):
            next_row.append(min(next_row[-1]+1,row[j]+1,row[j-1]+(left!=right)))
        row=next_row
    return row[-1]

def main():
    request=json.loads((ROOT/'data/p09-asr-request.json').read_text(encoding='utf-8'))
    result=json.loads((ROOT/'data/p09-asr-result.json').read_text(encoding='utf-8'))
    rows={row['id']:row for row in result['clips']}
    report={'method':'Real offline CUDA FunASR per independent sentence WAV; punctuation ignored.',
        'limitations':'ASR text comparison is an objective content/order check, not subjective voice quality evaluation.',
        'asset_revision':result['asset_revision'],'clips':[]}
    for clip in request['clips']:
        row=rows[clip['id']]
        original,recognized=normalized(clip['expected_text']),normalized(row['text'])
        edits=distance(original,recognized)
        cer=edits/max(1,len(original))
        report['clips'].append(dict(id=clip['id'],path=clip['path'],expected=clip['expected_text'],
            recognized=row['text'],error=row['error'],seconds=row['seconds'],edits=edits,characters=len(original),cer=cer,
            content_check='PASS' if not row['error'] and cer<=.15 else 'REVIEW_REQUIRED'))
    report['outcome']='PASS' if all(row['content_check']=='PASS' for row in report['clips']) else 'REVIEW_REQUIRED'
    atomic_json(ROOT/'docs/evidence/P09_ASR.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
