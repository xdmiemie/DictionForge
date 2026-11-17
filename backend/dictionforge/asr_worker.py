"""Offline, isolated Chinese FunASR worker. One model instance per request."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import time
import traceback

from .audio import atomic_json, digest
from .settings import ROOT


class ASRError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@contextmanager
def offline():
    """Do not let a library fallback turn a missing local asset into a download."""
    names = ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'MODELSCOPE_OFFLINE')
    previous = {name: os.environ.get(name) for name in names}
    for name in names:
        os.environ[name] = '1'
    old_connect, old_connect_ex, old_dns = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def deny(*args, **kwargs):
        raise ASRError('NETWORK_DISABLED', 'ASR worker 只读取已校验的本地资产，请先准备资产')

    socket.socket.connect = socket.socket.connect_ex = socket.getaddrinfo = deny
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = old_connect, old_connect_ex, old_dns
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def assets(upstream: Path):
    lock_path = ROOT / 'configs/assets.lock.json'
    raw = lock_path.read_bytes()
    lock = json.loads(raw)
    selected = [item for item in lock['assets'] if item['group'] == 'asr']
    if not selected:
        raise ASRError('MODEL_DEPENDENCY_MISSING', '资产锁未包含 ASR 模型')
    revisions = {'lock_sha256': hashlib.sha256(raw).hexdigest(), 'repositories': {}}
    directories = {}
    for item in selected:
        relative = Path(item['upstream_path'])
        path = upstream / relative
        if relative.is_absolute() or '..' in relative.parts:
            raise ASRError('ASSET_LOCK_INVALID', '资产锁路径越界')
        if not path.is_file():
            raise ASRError('MODEL_DEPENDENCY_MISSING', f'缺少 ASR 资产：{path}')
        if path.stat().st_size != item['size'] or digest(path) != item['sha256']:
            raise ASRError('MODEL_DEPENDENCY_INVALID', f'ASR 资产校验失败：{path}')
        revisions['repositories'][item['repository']] = item['revision']
        directories[item['repository']] = path.parent
    return {
        'model': str(directories['funasr/paraformer-zh']),
        'vad_model': str(directories['funasr/fsmn-vad']),
        'punc_model': str(directories['funasr/ct-punc']),
    }, revisions


def failure(exc):
    return {'code': getattr(exc, 'code', 'ASR_FAILED'), 'message': str(exc)}


def run(request, *, model_factory=None):
    if not isinstance(request, dict) or not isinstance(request.get('clips'), list):
        raise ASRError('INVALID_REQUEST', '请求需要 clips 列表')
    upstream = Path(request.get('upstream', ''))
    if not upstream.is_absolute() or not upstream.is_dir():
        raise ASRError('INVALID_UPSTREAM', 'upstream 必须是已准备的绝对目录')
    clips = request['clips']
    ids = [item.get('id') for item in clips if isinstance(item, dict)]
    if len(ids) != len(clips) or any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise ASRError('INVALID_REQUEST', '片段 ID 必须非空且唯一')
    rows = []
    revisions = None
    model = None
    load_error = None
    with offline():
        for clip in clips:
            started = time.monotonic()
            row = {'id': clip['id'], 'text': '', 'language': clip.get('language', 'zh'),
                   'error': None, 'confidence': None, 'seconds': 0.0}
            try:
                if row['language'] != 'zh':
                    raise ASRError('UNSUPPORTED_LANGUAGE', '当前 ASR worker 已验证语言为 zh（普通话）')
                path = Path(clip.get('path', ''))
                if not path.is_absolute() or not path.is_file():
                    raise ASRError('INVALID_AUDIO_PATH', f'音频必须是存在的绝对路径：{path}')
                if load_error is not None:
                    raise load_error
                if model is None:
                    try:
                        model_paths, revisions = assets(upstream)
                        if model_factory is None:
                            from funasr import AutoModel
                            import torch
                            if not torch.cuda.is_available():
                                raise ASRError('GPU_UNAVAILABLE', 'CUDA 不可用；未静默切换设备')
                            model_factory = AutoModel
                        model = model_factory(**model_paths, device='cuda', disable_update=True)
                    except Exception as exc:
                        load_error = exc
                        raise
                result = model.generate(input=str(path))
                text = result[0].get('text', '').strip() if result else ''
                if not text:
                    raise ASRError('ASR_EMPTY', 'ASR 未返回非空转写，请重试或人工校对')
                row['text'] = text
            except Exception as exc:
                row['error'] = failure(exc)
                traceback.print_exc(file=sys.stderr)
            row['seconds'] = time.monotonic() - started
            rows.append(row)
    return {'schema_version': 1, 'clips': rows, 'asset_revision': revisions}


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        raise SystemExit('usage: python -m dictionforge.asr_worker request.json result.json')
    request_path, output = map(Path, args)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        request = json.loads(request_path.read_text(encoding='utf-8-sig'))
        result = run(request)
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        atomic_json(output, {'schema_version': 1, 'clips': [], 'asset_revision': None, 'error': failure(exc)})
        return 1
    atomic_json(output, result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
