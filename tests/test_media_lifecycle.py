import ctypes
import json
import os
from pathlib import Path
import subprocess
import threading
import uuid

import pytest

from dictionforge import media_lifecycle as lifecycle
from dictionforge.jobs import Conflict, Jobs
from dictionforge.records import get, put, media, list_records


def tts(jobs, *, state='COMPLETED', current=False, count=2):
    job = jobs.submit('tts', {}, uuid.uuid4().hex)
    area = jobs.root/'runtime'/'tts'/job['id']
    area.mkdir(parents=True)
    files, ids = [], []
    for index in range(count):
        path = area/f'fixture-{index}.wav'
        path.write_bytes(b'owned native audio fixture '+str(index).encode())
        files.append(path)
        ids.append(media(jobs, path, owner=job['id']))
    put(jobs, 'tts', job['id'], {'job_id': job['id'], 'output_dir': str(area)})
    put(jobs, 'tts_result', job['id'], {'outcome': 'COMPLETED', 'text': '保留的原文',
        'audio': {'audio_id': ids[0]}, 'sentences': [{'audio': {'audio_id': key}} for key in ids[1:]]})
    with jobs.connect(write=True) as db:
        db.execute('UPDATE jobs SET state=? WHERE id=?', (state, job['id']))
    if current:
        put(jobs, 'tts_current', 'current', {'job_id': job['id']})
    return job['id'], files, ids


def test_startup_preserves_history_until_new_accept_and_current_is_never_collected(tmp_path):
    jobs = Jobs(tmp_path)
    old, files, ids = tts(jobs, current=True)
    assert not lifecycle.reconcile(jobs)['purges'] and all(path.exists() for path in files)
    new, current_files, _ = tts(jobs, current=True)
    with jobs.connect(write=True) as db:
        lifecycle.retire_previous(jobs, new, db=db)
        lifecycle.retire_owner(jobs, new, db=db)  # even an accidental retirement cannot collect current success.
    report = lifecycle.reconcile(jobs)
    assert report['purges'][0]['state'] == 'PURGED' and new in report['protected']
    assert all(not path.exists() for path in files) and all(path.exists() for path in current_files)
    assert all(get(jobs, 'media', key)['state'] == 'DELETED' for key in ids)
    assert get(jobs, 'tts_result', old)['text'] == '保留的原文'
    assert get(jobs, 'tts_result', old)['media_state'] == 'DELETED'
    with pytest.raises(lifecycle.MediaGone):
        lifecycle.acquire_browser(jobs, ids[0])


def test_browser_owner_lease_protects_all_sentences_and_heartbeat_release(tmp_path, monkeypatch):
    jobs = Jobs(tmp_path)
    owner, files, ids = tts(jobs)
    monkeypatch.setattr(lifecycle.time, 'time', lambda: 100.)
    lease = lifecycle.acquire_browser(jobs, ids[0], ttl=90)
    assert lease['expires_at'] == 190 and lifecycle.active_leases(jobs, media_id=ids[1])
    lifecycle.retire_owner(jobs, owner)
    assert owner in lifecycle.reconcile(jobs)['protected']
    monkeypatch.setattr(lifecycle.time, 'time', lambda: 150.)
    assert lifecycle.heartbeat(jobs, lease['token'])['expires_at'] == 240
    monkeypatch.setattr(lifecycle.time, 'time', lambda: 241.)
    with pytest.raises(lifecycle.MediaGone):
        lifecycle.heartbeat(jobs, lease['token'])
    assert lease['token'] in lifecycle.reconcile(jobs)['released_leases']
    assert all(not path.exists() for path in files)
    assert lifecycle.release(jobs, lease['token'])['released']


def test_job_lease_has_no_ttl_and_survives_restart_until_terminal(tmp_path, monkeypatch):
    jobs = Jobs(tmp_path)
    owner, files, ids = tts(jobs)
    def accepted(db, key):
        lifecycle.acquire_job(jobs, ids, key, db=db)
    job = jobs.submit('speech_save', {}, 'save-lease', on_accept=accepted)
    lifecycle.retire_owner(jobs, owner)
    restarted = Jobs(tmp_path)
    monkeypatch.setattr(lifecycle.time, 'time', lambda: 1e15)
    assert owner in lifecycle.reconcile(restarted)['protected']
    leases = lifecycle.active_leases(restarted, owner=owner)
    assert len(leases) == 2 and all(row['expires_at'] is None for row in leases)
    with pytest.raises(Conflict, match='ACTIVE'):
        lifecycle.release(jobs, leases[0]['token'])
    jobs.claim('cpu') if False else None  # No worker or GPU is started by this fixture.
    with jobs.connect(write=True) as db:
        db.execute("UPDATE jobs SET state='COMPLETED' WHERE id=?", (job['id'],))
    assert len(lifecycle.reconcile(restarted)['released_leases']) == 2
    assert all(not path.exists() for path in files)


def test_http_lease_lives_with_process_identity_not_clock(tmp_path, monkeypatch):
    jobs = Jobs(tmp_path)
    owner, files, ids = tts(jobs)
    lease = lifecycle.acquire_http(jobs, ids[0])
    assert lease['expires_at'] is None
    lifecycle.retire_owner(jobs, owner)
    monkeypatch.setattr(lifecycle.time, 'time', lambda: 1e15)
    assert owner in lifecycle.reconcile(jobs)['protected'] and files[0].exists()
    lease['process_start'] -= 100  # Simulates reused PID identity, not elapsed lease time.
    put(jobs, 'media_lease', lease['token'], lease)
    assert lifecycle.reconcile(jobs)['released_leases'] == [lease['token']]
    assert not files[0].exists()


def test_same_transaction_job_admission_wins_against_collector(tmp_path):
    jobs = Jobs(tmp_path)
    owner, files, ids = tts(jobs)
    lifecycle.retire_owner(jobs, owner)
    admitted, collector_started, unblock = threading.Event(), threading.Event(), threading.Event()
    errors = []
    def accept():
        try:
            def callback(db, key):
                lifecycle.acquire_job(jobs, ids, key, db=db)
                admitted.set()
                assert unblock.wait(10)
            jobs.submit('speech_save', {}, 'concurrent-save', on_accept=callback)
        except Exception as exc:
            errors.append(exc)
    def collect():
        try:
            assert admitted.wait(10)
            collector_started.set()
            lifecycle.reconcile(jobs)
        except Exception as exc:
            errors.append(exc)
    first, second = threading.Thread(target=accept), threading.Thread(target=collect)
    first.start(); second.start()
    assert collector_started.wait(10)
    unblock.set()
    first.join(15); second.join(15)
    assert not errors and not first.is_alive() and not second.is_alive()
    assert all(path.exists() for path in files) and lifecycle.active_leases(jobs, owner=owner)


def test_committed_purge_intent_wins_against_new_reader_or_save(tmp_path):
    jobs = Jobs(tmp_path)
    owner, _, ids = tts(jobs)
    lifecycle.retire_owner(jobs, owner)
    with jobs.connect(write=True) as db:
        lifecycle._plan(jobs, owner, db)
    with pytest.raises(lifecycle.MediaGone):
        lifecycle.acquire_browser(jobs, ids[0])
    with pytest.raises(lifecycle.MediaGone):
        jobs.submit('speech_save', {}, 'late-save', on_accept=lambda db, key:
                    lifecycle.acquire_job(jobs, ids, key, db=db))
    assert not [job for job in jobs.list() if job['request'] == 'late-save']
    assert lifecycle.reconcile(Jobs(tmp_path))['purges'][0]['state'] == 'PURGED'


def test_move_then_metadata_failure_recovers_from_committed_intent(tmp_path, monkeypatch):
    jobs = Jobs(tmp_path)
    owner, files, ids = tts(jobs)
    lifecycle.retire_owner(jobs, owner)
    real_put = lifecycle.put
    def crash_after_move(jobs, kind, key, value, db=None):
        if kind == 'media' and value.get('state') == 'DELETED':
            raise RuntimeError('simulated crash after atomic move before metadata commit')
        return real_put(jobs, kind, key, value, db)
    monkeypatch.setattr(lifecycle, 'put', crash_after_move)
    with pytest.raises(RuntimeError, match='simulated crash'):
        lifecycle.reconcile(jobs)
    assert not files[0].exists() and get(jobs, 'media', ids[0])['state'] == 'PURGE_PENDING'
    intent = list_records(jobs, 'media_purge')[0]
    assert Path(intent['files'][0]['trash']).exists()
    monkeypatch.setattr(lifecycle, 'put', real_put)
    assert lifecycle.reconcile(Jobs(tmp_path))['purges'][0]['state'] == 'PURGED'
    assert all(get(jobs, 'media', key)['state'] == 'DELETED' for key in ids)
    assert not any(Path(item['trash']).exists() for item in intent['files'])


def test_failed_current_collects_unregistered_worker_audio_but_preserves_json_and_other_files(tmp_path):
    jobs = Jobs(tmp_path)
    owner, files, _ = tts(jobs, state='CANCELLED', current=True)
    area = files[0].parent
    partial = area/('sentence-'+'a'*32+'-0000.wav.part')
    partial.write_bytes(b'partial worker output')
    metadata, unrelated = area/'inference-request.json', area/'personal.wav'
    metadata.write_text('{"text":"keep"}'); unrelated.write_bytes(b'not a recognized generated file')
    assert lifecycle.reconcile(jobs)['purges'][0]['state'] == 'PURGED'
    assert not partial.exists() and metadata.exists() and unrelated.exists()


def test_external_and_saved_paths_never_enter_tts_collection(tmp_path):
    jobs = Jobs(tmp_path)
    owner, files, ids = tts(jobs)
    saved = tmp_path/'saved_speech'/'saved.wav'; saved.parent.mkdir(); saved.write_bytes(b'independent saved')
    external = tmp_path/'original.wav'; external.write_bytes(b'original')
    external_id = media(jobs, external, external=True, owner=owner)
    saved_id = media(jobs, saved, owner='saved-version')
    lifecycle.retire_owner(jobs, owner)
    assert not lifecycle.reconcile(jobs)['errors']
    assert saved.read_bytes() == b'independent saved' and external.read_bytes() == b'original'
    assert get(jobs, 'media', external_id)['state'] == get(jobs, 'media', saved_id)['state'] == 'AVAILABLE'
    assert all(not path.exists() for path in files)
    assert lifecycle.acquire_http(jobs, external_id)['kind'] == 'http'


def test_managed_record_outside_owner_directory_is_rejected(tmp_path):
    jobs = Jobs(tmp_path)
    owner, _, ids = tts(jobs)
    outside = tmp_path/'saved_speech'/'owned.wav'; outside.parent.mkdir(); outside.write_bytes(b'protect saved')
    item = get(jobs, 'media', ids[0]); item['path'] = str(outside); put(jobs, 'media', ids[0], item)
    lifecycle.retire_owner(jobs, owner)
    report = lifecycle.reconcile(jobs)
    assert report['errors'] and 'PATH_INVALID' in report['errors'][0]['error']
    assert outside.read_bytes() == b'protect saved'


@pytest.mark.skipif(os.name != 'nt', reason='Real Windows no-delete-share handle semantics')
@pytest.mark.parametrize('share_mode', [0, 1])
def test_real_windows_handle_becomes_pending_then_retries_after_restart(tmp_path, share_mode):
    jobs = Jobs(tmp_path)
    owner, files, ids = tts(jobs, count=1)
    lifecycle.retire_owner(jobs, owner)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                  ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateFileW(str(files[0]), 0x80000000, share_mode, None, 3, 0, None)
    assert handle != ctypes.c_void_p(-1).value
    try:
        report = lifecycle.reconcile(jobs)
        assert report['purges'][0]['state'] == 'PURGE_PENDING'
        assert files[0].exists() and get(jobs, 'media', ids[0])['state'] == 'PURGE_PENDING'
        assert list_records(jobs, 'media_purge')[0]['errors']
        with pytest.raises(lifecycle.MediaGone):
            lifecycle.acquire_browser(jobs, ids[0])
    finally:
        kernel.CloseHandle(handle)
    assert lifecycle.reconcile(Jobs(tmp_path))['purges'][0]['state'] == 'PURGED'
    assert not files[0].exists() and get(jobs, 'media', ids[0])['state'] == 'DELETED'


def test_real_reparse_directory_is_rejected_without_touching_target(tmp_path):
    jobs = Jobs(tmp_path)
    owner, files, ids = tts(jobs, count=1)
    target = tmp_path/'outside'; target.mkdir()
    original = target/'fixture-0.wav'; original.write_bytes(files[0].read_bytes())
    files[0].unlink(); files[0].parent.rmdir()
    alias = files[0].parent
    try:
        if os.name == 'nt':
            script = tmp_path/'junction.ps1'
            script.write_text("param([string]$Target,[string]$Link)\n$ErrorActionPreference='Stop'\n"
                              "New-Item -ItemType Junction -Path $Link -Target $Target | Out-Null\n", encoding='utf-8')
            subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script),
                            '-Target', str(target), '-Link', str(alias)], check=True, capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=20)
        else:
            alias.symlink_to(target, target_is_directory=True)
    except (OSError, subprocess.SubprocessError):
        pytest.skip('Sandbox cannot create junction; dedicated elevated acceptance can run this case')
    try:
        with pytest.raises(ValueError, match='REPARSE'):
            lifecycle.retire_owner(jobs, owner)
        assert original.read_bytes().startswith(b'owned native audio')
        assert get(jobs, 'media', ids[0])['state'] == 'AVAILABLE'
    finally:
        if os.name == 'nt':
            alias.rmdir()
        else:
            alias.unlink()
