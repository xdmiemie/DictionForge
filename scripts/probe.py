"""Isolated real CUDA/ASR/v4 probes; never downloads implicit model assets."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--audio', type=Path)
    parser.add_argument('--asr', type=Path)
    parser.add_argument('--tts', action='store_true')
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--reference-text')
    parser.add_argument('--text', default='你好，欢迎来到声句工坊。')
    args = parser.parse_args()
    args.output = args.output.resolve()
    for key in ('audio', 'asr', 'reference'):
        if getattr(args, key):
            setattr(args, key, getattr(args, key).resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    dll_handle = None
    if os.name == 'nt' and shutil.which('ffmpeg'):
        dll_handle = os.add_dll_directory(str(Path(shutil.which('ffmpeg')).parent))
    started = time.monotonic()
    result = {'ok': False, 'python': sys.version, 'executable': sys.executable,
              'platform': platform.platform(), 'errors': [], 'imports': {},
              'imports_note': 'installed distribution metadata; actual CUDA and optional ASR/TTS imports run below'}
    for name in ('torch', 'torchaudio', 'numpy', 'transformers', 'funasr', 'pytorch-lightning', 'peft'):
        try:
            result['imports'][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result['imports'][name] = None
    try:
        result['ffmpeg'] = subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True,
                                          text=True, timeout=15).stdout.splitlines()[0]
        if args.audio:
            completed = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(args.audio),
                                        '-f', 'null', '-'], capture_output=True, check=True, timeout=60)
            result['audio'] = {'path': str(args.audio), 'sha256': sha256(args.audio), 'decoded': True}
        import torch
        result['torch'] = torch.__version__
        result['cuda'] = torch.version.cuda
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA 不可用')
        torch.cuda.reset_peak_memory_stats()
        result['device'] = torch.cuda.get_device_name(0)
        free, total = torch.cuda.mem_get_info()
        result.update(free_vram_bytes=free, total_vram_bytes=total)
        x = torch.arange(4096, device='cuda', dtype=torch.float32).reshape(64, 64)
        y = x @ x.T
        torch.cuda.synchronize()
        result['tensor_check'] = bool(torch.isfinite(y).all().item()) and y[0, 0].item() == 85344.0
        del x, y
        source = ROOT / 'vendor/gpt-sovits'
        if args.asr or args.tts:
            os.chdir(source)
            sys.path[:0] = [str(source), str(source / 'GPT_SoVITS')]
            os.environ['HF_HUB_OFFLINE'] = '1'
            os.environ['TRANSFORMERS_OFFLINE'] = '1'
            os.environ['TOKENIZERS_PARALLELISM'] = 'false'
        if args.asr:
            from funasr import AutoModel
            assets = ROOT / 'data/assets/asr'
            model = AutoModel(
                model=str(assets / 'paraformer-zh'),
                vad_model=str(assets / 'fsmn-vad'),
                punc_model=str(assets / 'ct-punc'),
                device='cuda', disable_update=True,
            )
            before = time.monotonic()
            recognized = model.generate(input=str(args.asr))
            result['asr'] = {'input': str(args.asr), 'sha256': sha256(args.asr),
                             'result': recognized, 'seconds': time.monotonic() - before}
            if not recognized or not recognized[0].get('text', '').strip():
                raise RuntimeError('真实 ASR 输出为空')
            del model
            torch.cuda.empty_cache()
        if args.tts:
            if not args.reference or not args.reference_text:
                raise ValueError('--tts 需要 --reference 与 --reference-text')
            from TTS_infer_pack.TTS import TTS, TTS_Config
            import numpy as np
            import soundfile as sf
            assets = source / 'GPT_SoVITS/pretrained_models'
            config = {'version': 'v4', 'device': 'cuda', 'is_half': True,
                      't2s_weights_path': str(assets / 's1v3.ckpt'),
                      'vits_weights_path': str(assets / 'gsv-v4-pretrained/s2Gv4.pth'),
                      'cnhuhbert_base_path': str(assets / 'chinese-hubert-base'),
                      'bert_base_path': str(assets / 'chinese-roberta-wwm-ext-large')}
            tts_config = TTS_Config({'version': 'v4', 'custom': config})
            tts_config.configs_path = str(args.output.with_suffix('.effective.yaml'))
            model = TTS(tts_config)
            request = {'text': args.text, 'text_lang': 'all_zh', 'ref_audio_path': str(args.reference),
                       'prompt_text': args.reference_text, 'prompt_lang': 'all_zh', 'top_k': 5,
                       'top_p': 1.0, 'temperature': 1.0, 'text_split_method': 'cut0', 'batch_size': 1,
                       'speed_factor': 1.0, 'seed': 42, 'sample_steps': 32, 'parallel_infer': False,
                       'fragment_interval': 0.3, 'repetition_penalty': 1.35}
            before = time.monotonic()
            chunks = list(model.run(request))
            if not chunks or any(rate != 48000 for rate, _ in chunks):
                raise RuntimeError('v4 未返回原生 48000 Hz 音频')
            pcm = np.concatenate([pcm for _, pcm in chunks])
            if pcm.size == 0 or not np.isfinite(pcm).all() or np.max(np.abs(pcm.astype(float))) == 0:
                raise RuntimeError('v4 输出无效或静音')
            output = args.output.with_suffix('.wav')
            sf.write(output, pcm, 48000, subtype='PCM_16')
            result['tts'] = {'config': config, 'request': request, 'output': str(output),
                             'sha256': sha256(output), 'sample_rate': 48000, 'samples': int(pcm.size),
                             'seconds': time.monotonic() - before}
        result['peak_vram_bytes'] = torch.cuda.max_memory_allocated()
        result['ok'] = result['tensor_check']
    except Exception as exc:
        result['errors'].append({'code': type(exc).__name__, 'message': str(exc), 'traceback': traceback.format_exc()})
    result['elapsed_seconds'] = time.monotonic() - started
    temporary = args.output.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
    temporary.replace(args.output)
    print(json.dumps({'ok': result['ok'], 'output': str(args.output)}, ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
