"""P07 CPU negative acceptance from a real valid pair, without editing originals."""
import copy
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'

from dictionforge.audio import atomic_json, digest
from dictionforge.models import artifact
from dictionforge.model_worker import load_package, validate


def main():
    import torch
    request_path, output_root = [Path(value).resolve() for value in sys.argv[1:3]]
    output_root.mkdir(parents=True, exist_ok=True)
    request = json.loads(request_path.read_text(encoding='utf-8'))
    original = {name: digest(Path(item['path'])) for name, item in request['weights'].items()}
    report = {'device': 'cpu', 'request': str(request_path), 'original_hashes': original, 'cases': {}}
    def check(name, test_request, code):
        started = time.monotonic()
        try:
            validate(test_request)
        except Exception as exc:
            if code not in str(exc):
                raise AssertionError(f'{name}: expected {code}, got {exc}') from exc
            report['cases'][name] = dict(result='REJECTED', error=str(exc), seconds=time.monotonic()-started,
                                         weights=test_request['weights'])
            print(name+': REJECTED', flush=True)
        else:
            raise AssertionError(name+': invalid package accepted')
        atomic_json(output_root/'report.json', report)
    package = load_package(request['weights']['sovits']['path'])
    original_rank = package['lora_rank']
    package['lora_rank'] = 16 if original_rank != 16 else 32
    altered = output_root/'wrong-rank.pth'
    torch.save(package, altered)
    with altered.open('r+b') as stream:
        stream.write(b'04')
    test_request = copy.deepcopy(request)
    test_request['weights']['sovits'] = artifact(altered)
    check('real_lora_wrong_rank', test_request, 'LORA_STRUCTURE_INVALID')
    package['lora_rank'] = original_rank
    removed = next(key for key in package['weight'] if key.startswith('bridge.'))
    del package['weight'][removed]
    altered = output_root/'missing-trainable.pth'
    torch.save(package, altered)
    with altered.open('r+b') as stream:
        stream.write(b'04')
    del package
    test_request = copy.deepcopy(request)
    test_request['weights']['sovits'] = artifact(altered)
    check('real_lora_missing_branch', test_request, 'LORA_STRUCTURE_INVALID')
    report['cases']['real_lora_missing_branch']['removed_tensor'] = removed
    package = load_package(request['weights']['sovits']['path'])
    package['config']['data']['sampling_rate'] = 22050
    altered = output_root/'wrong-audio-config.pth'
    torch.save(package, altered)
    with altered.open('r+b') as stream:
        stream.write(b'04')
    del package
    test_request = copy.deepcopy(request)
    test_request['weights']['sovits'] = artifact(altered)
    check('real_lora_wrong_audio_config', test_request, 'SOVITS_AUDIO_CONFIG_MISMATCH')
    test_request = copy.deepcopy(request)
    base = next(item for item in test_request['dependencies'] if item['path'].replace('\\','/').endswith('/s2Gv4.pth'))
    base['path'] = str(output_root/'not-present'/'s2Gv4.pth')
    check('missing_base_dependency', test_request, 'MODEL_FILE_CHANGED')
    test_request = copy.deepcopy(request)
    test_request['weights']['gpt'], test_request['weights']['sovits'] = test_request['weights']['sovits'], test_request['weights']['gpt']
    check('swapped_real_gpt_sovits_pair', test_request, 'GPT_ARCHITECTURE_MISMATCH')
    report['originals_unchanged'] = all(digest(Path(item['path'])) == original[name] for name, item in request['weights'].items())
    report['cuda_initialized'] = torch.cuda.is_initialized()
    assert report['originals_unchanged'] and not report['cuda_initialized']
    atomic_json(output_root/'report.json', report)


if __name__ == '__main__':
    main()
