"""Create isolated CPU-only deletion fixtures, never fake trainable weights."""
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid
import wave

from dictionforge.jobs import Jobs
from dictionforge.records import put, media
from dictionforge.audio import digest
from dictionforge.settings import ROOT


def main():
    area=ROOT/'data'/'cascade-ui-fixture'/datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    area.mkdir(parents=True);jobs=Jobs(area/'managed')
    def file(path,content=b'CPU deletion fixture, not trainable weights'):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content);return path
    source=area/'original.wav'
    with wave.open(str(source),'wb') as audio:
        audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(48000);audio.writeframes(b'\0\0'*48000)
    asset=file(jobs.root/'assets'/'retained.bin',b'shared fixture asset')
    def version(key,parent=None):
        folder=jobs.root/'datasets'/'role'/key;audio=file(folder/'clip.wav',source.read_bytes())
        clip=dict(id=key+'-clip',path=str(audio),audio_id=media(jobs,audio,owner=key),seconds=1,text='独立验收夹具',language='zh',reviewed=True,excluded=False)
        return put(jobs,'version',key,dict(id=key,dataset_id='role',path=str(folder),role=key,seconds=1,clips=[clip],feature_status='MISSING',parent_id=parent))
    upstream=version('upstream');version('feedback','upstream');version('derived','upstream')
    def model(key,**provenance):
        folder=jobs.root/'models'/'role'/key;weights={}
        for name in ('gpt','sovits'):
            path=file(folder/(name+'.fixture'));weights[name]=dict(path=str(path),sha256=digest(path),bytes=path.stat().st_size)
        return put(jobs,'model',key,dict(id=key,version_id=key,model_id='role',path=str(folder),role='级联验收 '+key,status='READY',weights=weights,
                   provenance=provenance,default_reference=None,dependencies=[],validation=dict(structure='NOT_RUN',gpu='NOT_RUN')))
    run=jobs.submit('train',dict(initial_model='parent'),str(uuid.uuid4()))['id']
    with jobs.connect(write=True) as db:db.execute("UPDATE jobs SET state='PAUSED' WHERE id=?",(run,))
    put(jobs,'run',run,dict(id=run,config=dict(initial_model='parent'),version=dict(id='upstream')))
    file(jobs.root/'runs'/run/'checkpoints'/'fixture.ckpt')
    model('parent');model('child',initial_model='parent',run_id=run);model('independent',version_id='upstream')
    put(jobs,'model_selection','active',dict(model_id='parent',enabled=1,reference_fingerprint='fixture'))
    put(jobs,'draft','feedback-draft',dict(id='feedback-draft',role='级联回流草稿',revision=0,clips=[]))
    file(jobs.root/'drafts'/'feedback-draft'/'clip.wav',source.read_bytes())
    put(jobs,'draft','revision',dict(id='revision',role='借用版本音频的草稿',revision=0,parent_version='upstream',clips=upstream['clips']))
    put(jobs,'augmentation','append',dict(id='append',source_model_id='parent',target_version_id='upstream',source_version_id='upstream',version_id='feedback',draft_id='feedback-draft',jobs=[]))
    saved=file(jobs.root/'saved_speech'/'keep'/'source.wav',source.read_bytes())
    put(jobs,'saved_speech','keep',dict(id='keep',title='必须保留的独立作品',text='独立作品',model_id='parent',model_name='级联验收 parent',format='wav',created=1,status='READY',audio=dict(path=str(saved),audio_id=media(jobs,saved,owner='keep'),seconds=1,sample_rate=48000,sha256=digest(saved),bytes=saved.stat().st_size)))
    result=dict(area=str(area),data_root=str(jobs.root),run_id=run,source=str(source),source_sha256=digest(source),asset=str(asset),asset_sha256=digest(asset),saved=str(saved),saved_sha256=digest(saved))
    (area/'fixture.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'data/cascade-ui-fixture/latest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
