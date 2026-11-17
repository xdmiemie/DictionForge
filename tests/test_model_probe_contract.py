"""Reference condition error/history contracts, not substitute GPU inference."""
from dictionforge.jobs import Jobs,Conflict
from dictionforge.records import get,put
from dictionforge import model_probe,models,references
import pytest

def test_compare_model_deleted_during_acceptance_records_failure(tmp_path,monkeypatch):
    jobs=Jobs(tmp_path)
    monkeypatch.setattr(models,'resolve',lambda *args:{'default_reference':{
        'path':'fixture','audio_id':'reference','sha256':'hash','text':'测试','language':'zh'}})
    def deleted(*args):raise Conflict('模型已删除')
    monkeypatch.setattr(references,'acquire',deleted)
    with pytest.raises(Conflict,match='已删除'):
        model_probe.compare(jobs,'model','comparison')
    result=get(jobs,'comparison','comparison')
    assert result['state']=='FAILED' and result['job_ids']==[]

def test_failed_inference_records_exact_reference_condition(tmp_path,monkeypatch):
    jobs=Jobs(tmp_path)
    job=jobs.submit('model_probe',{'model_version_id':'model'},'probe')
    jobs.claim()
    put(jobs,'model_probe',job['id'],dict(model_id='model',reference_fingerprint='ref-b',conditions_fingerprint='conditions-b'))
    monkeypatch.setattr(model_probe,'invoke',lambda *args,**kwargs:{'outcome':'FAILED','error':'CUDA_OOM','phase':'model_loading'})
    result=model_probe.process(jobs,job)
    assert result['outcome']=='FAILED'
    record=get(jobs,'model_test',job['id'])
    assert record['reference_fingerprint']=='ref-b' and record['phase']=='model_loading'
    assert jobs.events(job['id'],0)[-1]['payload']['gpu_resident'] is False


def test_invalid_output_cannot_leave_earlier_condition_pass(tmp_path,monkeypatch):
    jobs=Jobs(tmp_path)
    job=jobs.submit('model_probe',{'model_version_id':'model'},'probe')
    jobs.claim()
    put(jobs,'model_probe',job['id'],dict(model_id='model',reference_fingerprint='ref',conditions_fingerprint='conditions'))
    monkeypatch.setattr(model_probe,'invoke',lambda *args,**kwargs:{'outcome':'COMPLETED','audio':{'sample_rate':16000}})
    result=model_probe.process(jobs,job)
    assert result['outcome']=='FAILED' and result['phase']=='output_validation'
    assert get(jobs,'model_test',job['id'])['outcome']=='FAILED'
