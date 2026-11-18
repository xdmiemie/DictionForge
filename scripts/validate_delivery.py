"""Read-only delivery audit; source recordings and accepted saved speech stay intact."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(4*1024*1024),b''):h.update(chunk)
    return h.hexdigest()
def api(route):
    with urllib.request.urlopen('http://127.0.0.1:7867/api'+route,timeout=30) as r:return json.load(r)
def main():
    p=argparse.ArgumentParser();p.add_argument('--phases',type=int,default=13)
    p.add_argument('--allow-maintenance',action='store_true',help='Allow later non-phase maintenance commits')
    p.add_argument('--output',default='data/p12-delivery.json');args=p.parse_args()
    result={'schema_version':1,'checks':{},'source_files':[]}
    assert digest(ROOT/'DictionForge_Codex_Project_Plan.md')==digest(ROOT/'docs/PROJECT_PLAN.md')
    result['checks']['plan_verbatim']='PASS'
    prior=json.loads((ROOT/'docs/evidence/P03_DATASET_INITIAL.json').read_text(encoding='utf-8'))
    for row in prior['source_files']:
        actual=digest(row['original']);assert actual==row['before'],row['original']
        result['source_files'].append({'path':row['original'],'sha256':actual})
    result['checks']['source_recordings_unchanged']='PASS'
    history=subprocess.check_output(['git','-C',str(ROOT),'log','--reverse','--format=%H %s'],text=True).splitlines()
    assert len(history)>=args.phases if args.allow_maintenance else len(history)==args.phases,history
    for index,line in enumerate(history[:args.phases]):assert line.split(' ',1)[1].startswith(f'P{index:02d} '),line
    for line in history[args.phases:]:
        assert not any(line.split(' ',1)[1].startswith(f'P{index:02d} ') for index in range(args.phases)),line
    result['commits']=history;result['checks']['one_commit_per_phase']='PASS'
    result['timbreforge']={key:subprocess.check_output(['git','-C','E:/TimbreForge',*command],text=True).strip()
        for key,command in [('head',['rev-parse','HEAD']),('status',['status','--porcelain'])]}
    result['health']=api('/health');assert result['health']['workspace'].lower()==str(ROOT).lower()
    assert not [j for j in api('/jobs') if j['state'] in ('RUNNING','QUEUED')]
    result['checks']['no_active_jobs']='PASS'
    with sqlite3.connect((ROOT/'data/state/dictionforge.sqlite').as_uri()+'?mode=ro',uri=True) as db:
        result['database_integrity']=db.execute('PRAGMA integrity_check').fetchone()[0];assert result['database_integrity']=='ok'
        result['saved_speech']=[]
        for key in ('bfa6b69242ae406f9b04859837d763e3','7c4fff962a0a4f46b171b16f7c939e41','b2968e66c6294d3d9f654b2f0b2768b9','6d8a0b80a5384cb79f0133e0c47e3613','7604a45d9eb44897bb1165436a021326','4841aea38d3f4637be47cd90dc9a165f'):
            row=db.execute("SELECT metadata FROM records WHERE kind='saved_speech' AND id=?",(key,)).fetchone()
            assert row,key
            record=json.loads(row[0]);audio=record['audio'];assert digest(audio['path'])==audio['sha256']
            result['saved_speech'].append({'id':key,'audio':audio})
    result['checks']['permanent_saved_audio']='PASS'
    browser=json.loads((ROOT/'data/p12-browser.json').read_text(encoding='utf-8'))
    assert all(x['status']=='PASS' for x in browser['phases'].values())
    point=browser['points']['last_valid'];s2=browser['finalTraining']['stage_state']['stages']['s2']
    assert digest(point['path'])==point['sha256']
    assert s2['restored_parameters_sha256']==point['metrics']['final_parameters_sha256']
    assert s2['restored_optimizer_updates']==point['metrics']['optimizer_updates']==2
    assert s2['optimizer_updates']==40
    result['checks']['exact_restart_parameters_and_counter']='PASS'
    for key in ('secondView','finalTraining'):
        view=api('/training/'+browser[key]['id']);assert view['state']=='COMPLETED'
        for stage in view['stage_state']['stages'].values():
            point=stage['checkpoint'];assert digest(point['path'])==point['sha256']
    audio=browser['secondProbe']['result']['audio'];assert audio['sample_rate']==48000 and digest(audio['path'])==audio['sha256']
    result['checks']['final_checkpoints_and_native_48k_pair']='PASS'
    result['selection']=api('/synthesis/selection');assert result['selection']['active_model']==browser['deliveredModel']
    result['result']='PASS'
    (ROOT/args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'result':'PASS','checks':result['checks'],'commits':len(history)},ensure_ascii=False))
if __name__=='__main__':main()
