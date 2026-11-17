"""Owned external CUDA allocation / deliberate OOM probe; never touches other PIDs."""
from __future__ import annotations

import argparse
import gc
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
from dictionforge.audio import atomic_json


def managed_path(value):
    path = Path(value).resolve()
    if not path.is_relative_to((ROOT/'data').resolve()):
        raise ValueError('Probe output/release paths must stay in this repository data directory')
    return path


def allocation_plan(mode, total_bytes, mib=512):
    if mode == 'oom':
        return total_bytes * 4
    if mode != 'hold' or type(mib) is not int or not 64 <= mib <= 2048:
        raise ValueError('hold allocation must be 64–2048 MiB')
    return mib * 1024**2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['hold', 'oom'], required=True)
    parser.add_argument('--output', type=managed_path, required=True)
    parser.add_argument('--mib', type=int, default=512)
    wait = parser.add_mutually_exclusive_group()
    wait.add_argument('--release-file', type=managed_path)
    wait.add_argument('--stdin', action='store_true', help='Release on one input line or EOF')
    parser.add_argument('--timeout-seconds', type=int, default=600)
    args = parser.parse_args(argv)
    if args.mode == 'hold' and not (args.release_file or args.stdin):
        parser.error('hold mode requires --release-file or --stdin')
    if not 1 <= args.timeout_seconds <= 3600:
        parser.error('timeout must be 1–3600 seconds')
    if args.release_file and args.release_file.exists():
        parser.error('release file already exists; use a fresh path')
    if args.output.exists():
        parser.error('output already exists; use a fresh evidence path')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    import psutil
    current = psutil.Process(os.getpid())
    report = {'probe': 'owned_external_cuda_process', 'mode': args.mode, 'state': 'STARTING',
              'pid': current.pid, 'process_start': current.create_time(), 'executable': current.exe(),
              'command': current.cmdline(), 'requested_mib': args.mib if args.mode == 'hold' else None,
              'release_file': str(args.release_file) if args.release_file else None,
              'dtype': 'uint8', 'attempts': 0, 'parameters_changed': False, 'other_processes_modified': False}
    atomic_json(args.output, report)
    tensor = None
    started = time.perf_counter()
    exit_code = 0
    try:
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError('GPU_UNAVAILABLE')
        free, total = torch.cuda.mem_get_info()
        torch.cuda.reset_peak_memory_stats()
        report.update(device=torch.cuda.get_device_name(0), torch=torch.__version__, cuda=torch.version.cuda,
                      free_before_bytes=free, total_bytes=total,
                      requested_bytes=allocation_plan(args.mode, total, args.mib))
        report['attempts'] = 1
        if args.mode == 'oom':
            try:
                tensor = torch.empty((report['requested_bytes'],), dtype=torch.uint8, device='cuda')
            except torch.cuda.OutOfMemoryError as exc:
                report.update(state='OOM_CONFIRMED', code='CUDA_OOM', error=str(exc),
                              note='Dedicated allocation probe; no TTS/training model was loaded and no fallback allocation was attempted')
            else:
                report.update(state='OOM_NOT_TRIGGERED', error='Allocation unexpectedly succeeded; releasing immediately without writing the tensor')
                exit_code = 1
        else:
            tensor = torch.empty((report['requested_bytes'],), dtype=torch.uint8, device='cuda')
            tensor.zero_()
            torch.cuda.synchronize()
            report.update(state='HOLDING', allocated_bytes=torch.cuda.memory_allocated(),
                          reserved_bytes=torch.cuda.memory_reserved(), free_while_held_bytes=torch.cuda.mem_get_info()[0])
            atomic_json(args.output, report)
            print(json.dumps({'state': 'HOLDING', 'pid': report['pid'], 'output': str(args.output)}, ensure_ascii=False), flush=True)
            released = threading.Event()
            if args.stdin:
                def read_release():
                    sys.stdin.readline()
                    released.set()
                threading.Thread(target=read_release, daemon=True).start()
            deadline = time.monotonic()+args.timeout_seconds
            while not released.is_set() and not (args.release_file and args.release_file.exists()):
                if time.monotonic() >= deadline:
                    report['release_reason'] = 'timeout'
                    break
                released.wait(.1)
            else:
                report['release_reason'] = 'stdin' if released.is_set() else 'release_file'
            report['state'] = 'RELEASED'
        tensor = None
        gc.collect()
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        report.update(allocated_after_release_bytes=torch.cuda.memory_allocated(),
                      reserved_after_release_bytes=torch.cuda.memory_reserved(),
                      peak_allocated_bytes=torch.cuda.max_memory_allocated(),
                      free_after_release_bytes=torch.cuda.mem_get_info()[0], process_exit_pending=True)
    except Exception as exc:
        report.update(state='FAILED', error=str(exc), code=type(exc).__name__, traceback=traceback.format_exc())
        exit_code = 1
    finally:
        tensor = None
        report['elapsed_seconds'] = time.perf_counter()-started
        atomic_json(args.output, report)
        print(json.dumps({'state': report['state'], 'pid': report['pid'], 'output': str(args.output)}, ensure_ascii=False), flush=True)
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
