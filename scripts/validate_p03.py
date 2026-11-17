"""Repeatable real HTTP data acceptance. Original recordings are read-only."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
NAMES = [f'archive_jingyuan_{index}' for index in (1, 11, 12, 13, 14, 15)]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stable_id(kind, value):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'DictionForge:P03:' + kind + json.dumps(value, sort_keys=True)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:7867')
    parser.add_argument('--source', type=Path, default=Path('D:/中文 - Chinese/景元'))
    parser.add_argument('--run-tag', default='stable-media')
    parser.add_argument('--derive-from')
    args = parser.parse_args()
    target = ROOT / 'data/fixtures/景元 训练'
    target.mkdir(parents=True, exist_ok=True)
    evidence = ROOT / 'docs/evidence/P03_DATASET.json'
    snapshots = ROOT / 'data/p03-http'
    snapshots.mkdir(parents=True, exist_ok=True)
    report = {'schema_version': 1, 'base_url': args.base, 'run_tag': args.run_tag, 'source': str(args.source),
              'fixture': str(target), 'source_files': [], 'stages': {}}

    def save():
        temporary = evidence.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(evidence)

    def api(method, path, payload=None, headers=None):
        request = urllib.request.Request(args.base + path,
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None,
            method=method, headers={'Content-Type': 'application/json', **(headers or {})})
        with urllib.request.urlopen(request, timeout=90) as response:
            data = response.read()
            if response.headers.get('Content-Type', '').startswith('application/json'):
                return json.loads(data)
            return response.status, dict(response.headers), data

    def wait(job):
        deadline = time.monotonic() + 900
        while job['state'] in ('QUEUED', 'RUNNING'):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"job {job['id']} exceeded 900s")
            time.sleep(.5)
            job = api('GET', '/api/jobs/' + job['id'])
        if job['state'] != 'COMPLETED':
            raise RuntimeError(f"job {job['id']}: {job['state']}: {job.get('error')}")
        return job

    try:
        api('GET', '/api/health')
        for name in NAMES:
            for extension in ('.wav', '.lab'):
                original, copy = args.source / (name + extension), target / (name + extension)
                before = digest(original)
                if copy.exists():
                    if digest(copy) != before:
                        raise ValueError(f'existing fixture differs; refusing overwrite: {copy}')
                else:
                    shutil.copy2(original, copy)
                report['source_files'].append({'original': str(original), 'copy': str(copy), 'before': before,
                                               'copy_sha256': digest(copy)})
        if args.derive_from:
            parent = next(v for v in api('GET', '/api/datasets') if v['id'] == args.derive_from)
            manifest = Path(parent['path']) / 'manifest.json'
            parent_before = digest(manifest)
            draft = api('POST', '/api/dataset-versions/' + parent['id'] + '/draft',
                        {'request_id': stable_id('derive:' + args.run_tag, parent['id'])})
            if not draft.get('published'):
                clip = draft['clips'][0]
                draft = api('PATCH', '/api/dataset-drafts/' + draft['id'],
                    {'revision': draft['revision'], 'clips': [{'id': clip['id'], 'text': clip['text'], 'reviewed': True}]})
            published = api('POST', '/api/dataset-drafts/' + draft['id'] + '/publish', {'revision': draft['revision']})
            assert published['parent_id'] == parent['id']
            assert parent_before == digest(manifest)
            assert [(c['id'], c['split']) for c in published['clips']] == [(c['id'], c['split']) for c in parent['clips']]
            report['stages']['publish'] = {k: published[k] for k in ('id', 'dataset_id', 'parent_id', 'seconds',
                'fingerprint', 'path', 'list_path', 'feature_status')}
            report['stages']['publish']['version_id'] = published['id']
            report['parent_manifest'] = {'path': str(manifest), 'before': parent_before, 'after': digest(manifest)}
            report['clips'] = []
            for clip in published['clips']:
                with sqlite3.connect((ROOT / 'data/state/dictionforge.sqlite').as_uri() + '?mode=ro', uri=True) as db:
                    row = db.execute("SELECT metadata FROM records WHERE kind='media' AND id=?", (clip['audio_id'],)).fetchone()
                record = json.loads(row[0])
                assert Path(record['path']).parent == Path(published['path'])
                assert record['owner'] == published['id']
                status, headers, content = api('GET', '/api/audio/' + clip['audio_id'], headers={'Range': 'bytes=0-255'})
                assert status == 206 and len(content) == 256
                report['clips'].append({'id': clip['id'], 'split': clip['split'], 'seconds': clip['seconds'],
                    'audio_id': clip['audio_id'], 'media_path': record['path'], 'range_status': status,
                    'content_range': headers.get('content-range'), 'sha256': digest(clip['path'])})
            report['ok'] = True
            print(json.dumps(report['stages']['publish'], ensure_ascii=False, indent=2))
            return
        scan_body = {'source': str(target), 'role': '景元', 'language': 'zh', 'recursive': False}
        scan_body['request_id'] = stable_id('scan:' + args.run_tag, scan_body)
        scan = wait(api('POST', '/api/datasets/scan', scan_body))
        report['stages']['scan'] = {'job_id': scan['id'], 'state': scan['state'], 'result': scan['result']}
        assert len(scan['result']['files']) == 6
        assert all(item['status'] == 'ready' for item in scan['result']['files'])
        ready = scan['result']['files'][0]
        status, headers, content = api('GET', '/api/audio/' + ready['audio_id'], headers={'Range': 'bytes=0-255'})
        assert status == 206 and len(content) == 256
        report['range'] = {'status': status, 'bytes': len(content), 'headers': headers,
                           'body_sha256': hashlib.sha256(content).hexdigest()}
        prepare_body = {'scan_id': scan['result']['scan_id'], 'excluded': [], 'min_seconds': 1,
                        'max_seconds': 12, 'silence_db': -40, 'silence_seconds': 3}
        prepare_body['request_id'] = stable_id('prepare:' + args.run_tag, prepare_body)
        prepared = wait(api('POST', '/api/datasets/prepare', prepare_body))
        draft_id = prepared['result']['draft_id']
        draft = api('GET', '/api/dataset-drafts/' + draft_id)
        report['stages']['prepare'] = {'job_id': prepared['id'], 'result': prepared['result']}
        if not draft.get('published'):
            counts = {}
            for clip in draft['clips']:
                counts[clip['source_path']] = counts.get(clip['source_path'], 0) + 1
            if len(draft['clips']) != 6 or any(count != 1 for count in counts.values()):
                raise RuntimeError('unexpected split; leave draft unreviewed for per-segment alignment')
            retry = {'clip_ids': [clip['id'] for clip in draft['clips']]}
            retry['request_id'] = stable_id('asr-retry:' + args.run_tag, retry)
            recognized = wait(api('POST', '/api/dataset-drafts/' + draft_id + '/asr', retry))
            report['stages']['asr_retry'] = {'job_id': recognized['id'], 'state': recognized['state']}
            draft = api('GET', '/api/dataset-drafts/' + draft_id)
            (snapshots / 'before-review.json').write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding='utf-8')
            edits, review = [], []
            for clip in draft['clips']:
                if clip.get('asr_error'):
                    raise RuntimeError(f"real ASR error: {clip['asr_error']}")
                sidecar = Path(clip['source_path']).with_suffix('.lab')
                text = sidecar.read_text(encoding='utf-8-sig').strip()
                if not text:
                    raise ValueError('empty source sidecar')
                assert clip['start_sample'] >= 0 and clip['end_sample'] > clip['start_sample']
                review.append({'clip_id': clip['id'], 'source': clip['source_path'], 'sidecar': str(sidecar),
                    'sidecar_sha256': digest(sidecar), 'asr_candidate': clip['asr_candidate'], 'confirmed_text': text,
                    'method': 'whole-source sidecar mapping; boundary/source hash checked; no subjective listening claimed'})
                edits.append({'id': clip['id'], 'text': text, 'language': 'zh', 'reviewed': True})
            draft = api('PATCH', '/api/dataset-drafts/' + draft_id, {'revision': draft['revision'], 'clips': edits})
            report['review'] = review
        else:
            report['review'] = {'reused_published_draft': draft['published'], 'note': 'prior immutable version reused'}
        published = api('POST', '/api/dataset-drafts/' + draft_id + '/publish', {'revision': draft['revision']})
        report['stages']['publish'] = {'version_id': published['id'], 'dataset_id': published['dataset_id'],
            'seconds': published['seconds'], 'fingerprint': published['fingerprint'], 'path': published['path'],
            'list_path': published['list_path'], 'feature_status': published['feature_status']}
        report['clips'] = [{'id': c['id'], 'source_path': c['source_path'], 'split': c['split'],
                            'seconds': c['seconds'], 'sha256': c['sha256']} for c in published['clips']]
        assert len(published['clips']) == 6
        assert sum(c['split'] == 'train' for c in published['clips']) == 5
        assert sum(c['split'] == 'validation' for c in published['clips']) == 1
        lines = Path(published['list_path']).read_text(encoding='utf-8').splitlines()
        assert len(lines) == 5 and all(len(line.split('|')) == 4 for line in lines)
        report['train_list_sha256'] = digest(published['list_path'])
        published_audio = published['clips'][0]['audio_id']
        status, headers, content = api('GET', '/api/audio/' + published_audio, headers={'Range': 'bytes=0-255'})
        assert status == 206 and len(content) == 256
        report['published_range'] = {'audio_id': published_audio, 'status': status, 'bytes': len(content), 'headers': headers}
        report['ok'] = True
    except Exception as exc:
        report['ok'] = False
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        raise
    finally:
        for item in report['source_files']:
            item['after'] = digest(item['original'])
            item['unchanged'] = item['after'] == item['before'] == digest(item['copy'])
        save()
    print(json.dumps(report['stages']['publish'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
