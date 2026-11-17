"""Request cancellation cannot delete a file still consumed by the CPU importer."""
import asyncio
from pathlib import Path
import threading
from types import SimpleNamespace
import uuid
import pytest
from dictionforge.api import create_app
from dictionforge.jobs import Jobs
from dictionforge.settings import Settings
from dictionforge import references_service

def test_upload_cancellation_waits_for_owned_import_thread(tmp_path,monkeypatch):
    started,release=threading.Event(),threading.Event()
    seen={}
    def slow_import(jobs,source_path,**kwargs):
        seen['path']=Path(source_path)
        started.set()
        assert release.wait(5)
        seen['bytes']=Path(source_path).read_bytes()
        return {'id':'fixture-only'}
    monkeypatch.setattr(references_service,'import_reference',slow_import)
    app=create_app(Settings(tmp_path))
    app.state.jobs=Jobs(tmp_path)
    endpoint=next(route.endpoint for route in app.routes if getattr(route,'path',None)=='/api/references/upload')
    class Request:
        async def stream(self):yield b'input lifetime fixture'
    request=Request();request.app=app
    async def scenario():
        task=asyncio.create_task(endpoint(request,uuid.uuid4(),'reference','zh','fixture.wav'))
        assert await asyncio.to_thread(started.wait,5)
        task.cancel()
        await asyncio.sleep(.05)
        assert seen['path'].is_file() and not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):await task
    asyncio.run(scenario())
    assert seen['bytes']==b'input lifetime fixture'
    assert not seen['path'].exists()
