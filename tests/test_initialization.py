import copy
import pytest
from dictionforge import initialization as init
from dictionforge.audio import atomic_json, digest
from dictionforge.jobs import Conflict
from dictionforge.training_config import normalize_config


@pytest.fixture
def source(tmp_path,monkeypatch):
    (tmp_path/'configs').mkdir()
    for file in ('assets.lock.json','upstream.lock.json'):atomic_json(tmp_path/'configs'/file,{'fixture':True})
    monkeypatch.setattr(init,'ROOT',tmp_path)
    def artifact(name,data):
        file=tmp_path/name;file.write_bytes(data)
        return dict(path=str(file),sha256=digest(file),bytes=len(data))
    base=artifact('s2Gv4.pth',b'pinned base bytes')
    model=dict(id='source',weights=dict(gpt=artifact('gpt.ckpt',b'source GPT bytes'),sovits=artifact('lora.pth',b'source LoRA bytes')),
        dependencies=[base],validation={'sovits':{'format':'v4_lora','lora_rank':32}})
    monkeypatch.setattr(init.models,'resolve',lambda *_,**__:copy.deepcopy(model))
    return model,normalize_config({'version_id':'new-data','initial_model':'source'})


def test_source_pair_frozen_and_stale_bytes_rejected(source):
    from pathlib import Path
    model,config=source
    frozen=init.freeze(None,config)
    assert frozen['weights']==model['weights'] and frozen['s2_format']=='v4_lora'
    assert init.verify(frozen,config)==frozen
    Path(model['weights']['gpt']['path']).write_bytes(b'changed')
    with pytest.raises(Conflict,match='MODEL_FILE_CHANGED'):init.verify(frozen,config)


def test_rank_mismatch_only_for_enabled_s2(source):
    _,config=source;config['s2']['rank']=8
    with pytest.raises(ValueError,match='MODEL_RANK_MISMATCH'):init.freeze(None,config)
    config['stages']=['s1'];assert init.freeze(None,config)['lora_rank']==32


def test_official_base_copy_recognized_by_hash_not_path(source,tmp_path):
    model,config=source
    copied=tmp_path/'official-copy.pth';copied.write_bytes(b'pinned base bytes')
    model['weights']['sovits']=dict(path=str(copied),sha256=digest(copied),bytes=copied.stat().st_size)
    model['validation']['sovits']={'format':'v4_base','lora_rank':None}
    config['s2']['rank']=8
    frozen=init.freeze(None,config)
    assert frozen['s2_format']=='v4_base' and frozen['lora_rank'] is None


def test_frozen_model_identity_and_metadata_change_rejected(source):
    _,config=source;frozen=init.freeze(None,config)
    with pytest.raises(Conflict,match='INITIALIZATION_MODEL_CHANGED'):init.verify(frozen,dict(config,initial_model='official'))
    frozen['lora_rank']=8
    with pytest.raises(Conflict,match='INITIALIZATION_FINGERPRINT_CHANGED'):init.verify(frozen,config)


def test_actual_loaded_tensor_equality():
    torch=pytest.importorskip('torch')
    model=torch.nn.Linear(3,2)
    weights={k:v.detach().clone().half() for k,v in model.state_dict().items()}
    model.load_state_dict(weights)
    report=init.loaded_weights_report(model,weights)
    assert report['all_tensors_equal'] and report['source_tensor_sha256']==report['loaded_tensor_sha256']
    with torch.no_grad():model.weight.add_(1)
    with pytest.raises(ValueError,match='INITIALIZATION_TENSOR_MISMATCH'):init.loaded_weights_report(model,weights)
