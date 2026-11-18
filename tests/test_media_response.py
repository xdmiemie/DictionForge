"""HTTP responses retain a file lease across ranges and release on ASGI failure."""
import asyncio
import json
import wave
import pytest
from fastapi.testclient import TestClient
from dictionforge.api import create_app
from dictionforge.jobs import Jobs
from dictionforge.settings import Settings
from dictionforge.records import media
from dictionforge.media_lifecycle import active_leases
from dictionforge.media_lifecycle import acquire_browser, release
from dictionforge.media_response import leased_audio


def setup(tmp_path):
    jobs=Jobs(tmp_path)
    path=tmp_path/'audio.wav'
    with wave.open(str(path),'wb') as stream:
        stream.setparams((1,2,48000,0,'NONE','not compressed'))
        stream.writeframes(b'\x00\x10'*100)
    key=media(jobs,path,owner='response-fixture')
    app=create_app(Settings(tmp_path));app.state.jobs=jobs
    return jobs,path,key,TestClient(app)


def test_ranges_headers_and_no_http_lease_leaks(tmp_path):
    jobs,path,key,client=setup(tmp_path)
    response=client.get('/api/audio/'+key,headers={'Range':'bytes=3-27'})
    assert response.status_code==206 and response.content==path.read_bytes()[3:28]
    assert response.headers['content-range']==f'bytes 3-27/{path.stat().st_size}'
    assert response.headers['content-type']=='audio/wav'
    assert not active_leases(jobs,media_id=key)
    assert client.get('/api/audio/'+key,headers={'Range':'bytes=99999-'}).status_code==416
    response=client.head('/api/audio/'+key+'?download=true')
    assert response.status_code==200 and response.content==b''
    assert 'attachment' in response.headers['content-disposition']
    assert not active_leases(jobs,media_id=key)


def test_disconnected_file_send_releases_reference(tmp_path):
    jobs,path,key,_=setup(tmp_path)
    response=leased_audio(jobs,key)
    assert len(active_leases(jobs,media_id=key))==1
    async def send(message):raise ConnectionResetError('controlled transport disconnect')
    async def receive():return {'type':'http.disconnect'}
    with pytest.raises(ConnectionResetError):
        asyncio.run(response({'type':'http','method':'GET','headers':[]},receive,send))
    assert not active_leases(jobs,media_id=key)


def test_scanned_flac_keeps_original_media_type(tmp_path):
    import numpy as np
    import soundfile as sf
    jobs,path,key,client=setup(tmp_path)
    flac=tmp_path/'original.flac'
    sf.write(flac,np.zeros(4800),48000)
    key=media(jobs,flac,external=True,owner='external-scan')
    response=client.get('/api/audio/'+key,headers={'Range':'bytes=0-3'})
    assert response.status_code==206 and response.content==b'fLaC'
    assert response.headers['content-type'] in ('audio/flac','audio/x-flac')
    assert not active_leases(jobs,media_id=key)


def test_media_playback_protects_dataset_deletion_references(tmp_path):
    from dictionforge.references import references_for
    jobs,path,key,_=setup(tmp_path)
    lease=acquire_browser(jobs,key)
    assert any(row['kind']=='media_lease' for row in references_for(jobs,'version','response-fixture'))
    release(jobs,lease['token'])
    assert not references_for(jobs,'version','response-fixture')
