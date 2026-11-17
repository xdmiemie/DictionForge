"""CPU import acceptance from an existing real model reference, isolated from UI selection."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
from dictionforge.audio import atomic_json, digest
from dictionforge.jobs import Jobs
from dictionforge.references_service import import_reference, validate_reference


def main():
    manifest, area = [Path(value).resolve() for value in sys.argv[1:3]]
    model = json.loads(manifest.read_text(encoding='utf-8'))
    source = model['default_reference']
    original = digest(Path(source['path']))
    jobs = Jobs(area)
    first = import_reference(jobs, source_path=source['path'], text=source['text'], language=source['language'], request_id='real-reference-1')
    repeated = import_reference(jobs, source_path=source['path'], text=source['text'], language=source['language'], request_id='real-reference-1')
    second = import_reference(jobs, source_path=source['path'], text=source['text'], language=source['language'], request_id='real-reference-2')
    validate_reference(first)
    validate_reference(second)
    assert first == repeated and first['id'] != second['id'] and first['path'] != second['path']
    assert digest(Path(source['path'])) == original == source['sha256']
    assert digest(Path(first['path'])) == first['sha256']
    report = dict(source_model=model['id'], source_manifest=str(manifest), original_sha256=original,
        original_unchanged=True, idempotent=True, separate_immutable_files=True,
        gpu_used=False, first=first, second=second)
    atomic_json(area/'report.json', report)
    print(json.dumps(dict(result='PASS', seconds=first['seconds'], sample_rate=first['sample_rate'],
        channels=first['channels'], first_id=first['id'], second_id=second['id'], original_unchanged=True)))


if __name__ == '__main__':
    main()
