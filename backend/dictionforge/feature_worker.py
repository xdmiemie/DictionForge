"""Pinned GPT-SoVITS feature scripts and actual dataset/collate preflight."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
import uuid

from .audio import atomic_json, digest
from .settings import ROOT
from .training_config import config_fingerprint, feature_fingerprint
from .source_lock import verify_source


def key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def component_keys(clip, upstream_lock, asset_lock):
    # Text edits deliberately leave the audio/semantic cache valid.
    common = {"upstream": upstream_lock, "assets": asset_lock, "schema": 1}
    acoustic = key(dict(common, audio=clip["sha256"], rate=32000, precision="fp32"))
    return dict(text=key(dict(common, text=clip["text"], language=clip["language"])),
                acoustic=acoustic, semantic=key(dict(common, acoustic=acoustic, extractor="s2Gv4")))


def emit(request, stage, completed, total, **extra):
    if request.get("job_id"):
        from .jobs import Jobs
        Jobs(Path(request["data_root"])).progress(request["job_id"],
            dict(stage=stage, completed=completed, total=total, unit="片段", **extra))


def copy_atomic(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".part")
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def checked_assets(upstream):
    lock = json.loads((ROOT / "configs/assets.lock.json").read_text(encoding="utf-8"))
    for item in lock["assets"]:
        if item["group"] not in ("v4", "text"):
            continue
        for entry in [item, *item.get("members", [])]:
            if not entry.get("upstream_path"):
                continue
            path = upstream / entry["upstream_path"]
            if not path.is_file() or path.stat().st_size != entry["size"] or digest(path) != entry["sha256"]:
                raise ValueError(f"MODEL_DEPENDENCY_INVALID: {path}")


def init_runtime(upstream):
    os.environ.update(version="v4", hz="25hz", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      TOKENIZERS_PARALLELISM="false", OMP_NUM_THREADS="2", TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD="1")
    os.chdir(upstream)
    sys.path[:0] = [str(upstream / "GPT_SoVITS"), str(upstream)]


def output_files(stage, name, language):
    if stage == "text":
        return {"bert": f"3-bert/{name}.pt"} if language == "zh" else {}
    if stage == "acoustic":
        return {"ssl": f"4-cnhubert/{name}.pt", "wav": f"5-wav32k/{name}"}
    return {}


def restore(pool, area, stage, cache_key, name, language):
    folder = pool / stage / cache_key
    metadata = folder / "entry.json"
    try:
        entry = json.loads(metadata.read_text(encoding="utf-8"))
        for label, relative in output_files(stage, name, language).items():
            source = folder / label
            if digest(source) != entry["files"][label]:
                return None
        for label, relative in output_files(stage, name, language).items():
            target = area / relative
            if not target.is_file() or digest(target) != entry["files"][label]:
                copy_atomic(folder / label, target)
        return entry
    except (OSError, ValueError, KeyError):
        return None


def cache(pool, area, stage, cache_key, name, language, value):
    folder = pool / stage / cache_key
    folder.mkdir(parents=True, exist_ok=True)
    entry = {"value": value, "files": {}}
    for label, relative in output_files(stage, name, language).items():
        source = area / relative
        copy_atomic(source, folder / label)
        entry["files"][label] = digest(source)
    atomic_json(folder / "entry.json", entry)
    return entry


def validate_component(area, stage, name, language, value):
    import torch
    import soundfile as sf
    if stage == "text":
        fields = value.split("\t")
        if len(fields) != 3 or not fields[0].strip() or not fields[2].strip():
            raise ValueError("文本音素索引为空或字段数错误")
        if language == "zh":
            bert = torch.load(area / f"3-bert/{name}.pt", map_location="cpu", weights_only=True)
            if bert.ndim != 2 or bert.shape[-1] != len(fields[0].split()) or not torch.isfinite(bert).all():
                raise ValueError("BERT 形状或数值错误")
    elif stage == "acoustic":
        ssl = torch.load(area / f"4-cnhubert/{name}.pt", map_location="cpu", weights_only=True)
        wav = sf.info(area / f"5-wav32k/{name}")
        if ssl.ndim != 3 or ssl.shape[:2] != (1, 768) or ssl.shape[-1] < 1 or not torch.isfinite(ssl).all():
            raise ValueError("SSL 形状或数值错误")
        if wav.samplerate != 32000 or wav.channels != 1 or wav.frames < 1:
            raise ValueError("训练音频必须是非空 32 kHz 单声道")
        if abs(ssl.shape[-1] - wav.frames / 640) > 3:
            raise ValueError("SSL 长度与音频不匹配")
    else:
        tokens = [int(x) for x in value.split()]
        if not tokens or min(tokens) < 0 or max(tokens) >= 1024:
            raise ValueError("语义 token 为空或范围不正确")


def run_script(upstream, area, stage, clips):
    names = {"text": "1-get-text.py", "acoustic": "2-get-hubert-wav32k.py", "semantic": "3-get-semantic.py"}
    listing = area / f"input-{stage}.list"
    listing.write_text("\n".join(f"{c['path']}|dictionforge|{c['language'].upper()}|{c['text']}" for c in clips)+"\n", encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", inp_text=str(listing), inp_wav_dir="", exp_name=area.name,
        i_part="0", all_parts="1", opt_dir=str(area), is_half="False", version="v4",
        bert_pretrained_dir=str(upstream / "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large"),
        cnhubert_base_dir=str(upstream / "GPT_SoVITS/pretrained_models/chinese-hubert-base"),
        pretrained_s2G=str(upstream / "GPT_SoVITS/pretrained_models/gsv-v4-pretrained/s2Gv4.pth"),
        s2config_path=str(upstream / "GPT_SoVITS/configs/s2.json"),
        PYTHONPATH=os.pathsep.join([str(ROOT / "backend"), str(upstream), str(upstream / "GPT_SoVITS")]))
    partial = area / ("2-name2text-0.txt" if stage == "text" else "6-name2semantic-0.tsv")
    if stage != "acoustic":
        partial.unlink(missing_ok=True)
    for clip in clips:
        for relative in output_files(stage, Path(clip["path"]).name, clip["language"]).values():
            (area / relative).unlink(missing_ok=True)
    subprocess.run([sys.executable, str(upstream / "GPT_SoVITS/prepare_datasets" / names[stage])],
                   cwd=upstream, env=env, check=True)
    if stage == "acoustic":
        return {}
    if not partial.is_file():
        raise ValueError(f"缺少上游阶段索引：{partial}")
    return dict(line.split("\t", 1) for line in partial.read_text(encoding="utf-8").splitlines() if "\t" in line)


def features(request):
    started = time.monotonic()
    version = request["version"]
    upstream, root = Path(request["upstream"]), Path(request["data_root"])
    area = root / "features" / version["id"]
    area.mkdir(parents=True, exist_ok=True)
    pool = root / "feature-cache"
    checked_assets(upstream)
    verify_source(ROOT, upstream)
    init_runtime(upstream)
    import torch
    if not torch.cuda.is_available():
        raise ValueError("GPU_UNAVAILABLE：特征准备没有静默回退 CPU")
    clips = [c for c in version["clips"] if c["split"] == "train"]
    if not clips:
        raise ValueError("NO_TRAIN_BATCH：版本没有训练片段")
    for clip in clips:
        if digest(Path(clip["path"])) != clip["sha256"]:
            raise ValueError(f"DATA_CHANGED: {clip['id']}")
    upstream_hash = digest(ROOT / "configs/upstream.lock.json")
    asset_hash = digest(ROOT / "configs/assets.lock.json")
    keys = {c["id"]: component_keys(c, upstream_hash, asset_hash) for c in clips}
    result = dict(schema_version=1, path=str(area), version_id=version["id"],
        version_fingerprint=version["fingerprint"], upstream_lock=upstream_hash, asset_lock=asset_hash,
        ready=False, stages={}, errors=[], entries={}, precision="fp32")
    values = {stage: {} for stage in ("text", "acoustic", "semantic")}
    for stage in values:
        missing = []
        for clip in clips:
            name = Path(clip["path"]).name
            entry = restore(pool, area, stage, keys[clip["id"]][stage], name, clip["language"])
            if entry:
                try:
                    validate_component(area, stage, name, clip["language"], entry["value"])
                    values[stage][name] = entry["value"]
                except Exception:
                    entry = None
            if not entry:
                missing.append(clip)
        reused = len(clips)-len(missing)
        emit(request, stage, reused, len(clips), reused=reused)
        script_rows = {}
        if missing:
            try:
                script_rows = run_script(upstream, area, stage, missing)
            except Exception as exc:
                result["errors"].append(dict(stage=stage, message=str(exc)))
        for i, clip in enumerate(missing):
            name = Path(clip["path"]).name
            try:
                value = script_rows.get(name, "") if stage != "acoustic" else "32k"
                validate_component(area, stage, name, clip["language"], value)
                cache(pool, area, stage, keys[clip["id"]][stage], name, clip["language"], value)
                values[stage][name] = value
            except Exception as exc:
                result["errors"].append(dict(stage=stage, clip_id=clip["id"], message=str(exc)))
            emit(request, stage, reused+i+1, len(clips), reused=reused)
        result["stages"][stage] = dict(total=len(clips), reused=reused, rebuilt=len(missing), valid=len(values[stage]))
        if len(values[stage]) != len(clips):
            break
        if stage == "text":
            (area / "2-name2text.txt").write_text("\n".join(name+"\t"+row for name, row in values[stage].items())+"\n", encoding="utf-8")
        if stage == "semantic":
            (area / "6-name2semantic.tsv").write_text("item_name\tsemantic_audio\n"+"\n".join(name+"\t"+row for name, row in values[stage].items())+"\n", encoding="utf-8")
    for clip in clips:
        name = Path(clip["path"]).name
        paths = [relative for stage in values for relative in output_files(stage, name, clip["language"]).values()]
        result["entries"][name] = dict(keys=keys[clip["id"]], files={p: digest(area/p) for p in paths if (area/p).is_file()})
    result["ready"] = not result["errors"] and all(len(values[s]) == len(clips) for s in values)
    result["indexes"] = {p: digest(area/p) for p in ("2-name2text.txt", "6-name2semantic.tsv") if (area/p).is_file()}
    result["seconds"] = time.monotonic()-started
    atomic_json(area / "manifest.json", result)
    return result


def build_loaders(area, config):
    """Shared with training: actual upstream datasets/collates, explicit counts."""
    import torch
    import utils
    from AR.data.dataset import Text2SemanticDataset
    from AR.data.bucket_sampler import DistributedBucketSampler as S1Sampler
    from module.data_utils import TextAudioSpeakerLoaderV4, TextAudioSpeakerCollateV4, DistributedBucketSampler as S2Sampler
    from torch.utils.data import DataLoader
    loaders, report = {}, {}
    for stage in config["stages"]:
        try:
            batch = config[stage]["batch_size"]
            if stage == "s1":
                dataset = Text2SemanticDataset(str(area / "2-name2text.txt"), str(area / "6-name2semantic.tsv"), max_sec=54)
                original = len(dataset)
                names = sorted(set(dataset.item_names))
                indices = [dataset.item_names.index(n) for n in names]
                dataset.semantic_phoneme = [dataset.semantic_phoneme[i] for i in indices]
                dataset.item_names = names
                sampler = S1Sampler(dataset, num_replicas=1, rank=0, batch_size=batch, seed=config["seed"])
                loader = DataLoader(dataset, batch_size=batch, sampler=sampler, collate_fn=dataset.collate, num_workers=0)
                consumed = len(sampler)
                sampled_names = names
            else:
                hps = utils.get_hparams_from_file("GPT_SoVITS/configs/s2.json")
                hps.data.exp_dir = str(area)
                dataset = TextAudioSpeakerLoaderV4(hps.data)
                original = len(dataset)
                lookup = {row[0]: i for i, row in enumerate(dataset.audiopaths_sid_text)}
                names = sorted(lookup)
                indices = [lookup[name] for name in names]
                dataset.audiopaths_sid_text = [dataset.audiopaths_sid_text[i] for i in indices]
                dataset.lengths = [dataset.lengths[i] for i in indices]
                sampler = S2Sampler(dataset, batch, [32,300,400,500,600,700,800,900,1000], num_replicas=1, rank=0)
                loader = DataLoader(dataset, batch_sampler=sampler, collate_fn=TextAudioSpeakerCollateV4(), num_workers=0)
                consumed = len(loader)*batch
                sampled_indices = sorted({i for bucket in sampler.buckets for i in bucket})
                sampled_names = [names[i] for i in sampled_indices]
            if not len(loader):
                raise ValueError("NO_TRAIN_BATCH：过滤后零批次")
            loaders[stage] = loader
            report[stage] = dict(samples=consumed, unique_samples=len(sampled_names), dataset_unique_samples=len(dataset), native_expanded_samples=original,
                removed_native_repeats=original-len(dataset), sampler_padding=consumed-len(sampled_names), batches=len(loader),
                batch_size=batch, precision=config[stage]["precision"], names=sampled_names, dataset_names=names, num_workers=0,
                updates_per_epoch=math.ceil(len(loader)/config[stage].get("accumulate", 1)))
            if stage == "s2":
                report[stage]["grad_ckpt"] = False
        except Exception as exc:
            report[stage] = dict(error=f"NO_TRAIN_BATCH: {type(exc).__name__}: {exc}", samples=0, unique_samples=0, batches=0)
    return loaders, report


def preflight(request):
    version, config = request["version"], request["config"]
    area = Path(request["data_root"]) / "features" / version["id"]
    result = dict(ready=False, blockers=[], stages={}, config=config, config_fingerprint=config_fingerprint(config))
    if config["initial_model"] != "official":
        result["blockers"].append("MODEL_INITIALIZATION_NOT_AVAILABLE：来源模型初始化将在模型注册后校验")
    try:
        manifest = json.loads((area / "manifest.json").read_text(encoding="utf-8"))
        if not manifest["ready"] or manifest["version_fingerprint"] != version["fingerprint"]:
            raise ValueError("特征未就绪或数据指纹变化")
        if manifest["upstream_lock"] != digest(ROOT/"configs/upstream.lock.json") or manifest["asset_lock"] != digest(ROOT/"configs/assets.lock.json"):
            raise ValueError("源码或资产锁已变化，请重新准备特征")
        result.update(features_manifest_sha256=digest(area/"manifest.json"), features_content_sha256=feature_fingerprint(manifest),
                      upstream_lock=manifest["upstream_lock"], asset_lock=manifest["asset_lock"])
        training_clips = [c for c in version["clips"] if c["split"] == "train"]
        if set(manifest["entries"]) != {Path(c["path"]).name for c in training_clips}:
            raise ValueError("特征键与冻结训练片段集合不一致")
        for clip in training_clips:
            if digest(Path(clip["path"])) != clip["sha256"]:
                raise ValueError(f"DATA_CHANGED: {clip['id']}")
        expected = dict(manifest["indexes"])
        for entry in manifest["entries"].values():
            expected.update(entry["files"])
        for relative, expected_hash in expected.items():
            if digest(area/relative) != expected_hash:
                raise ValueError(f"特征哈希变化：{relative}")
        checked_assets(Path(request["upstream"]))
        verify_source(ROOT, Path(request["upstream"]))
        init_runtime(Path(request["upstream"]))
        import torch
        if not torch.cuda.is_available():
            raise ValueError("GPU_UNAVAILABLE")
        torch.cuda.reset_peak_memory_stats()
        loaders, result["stages"] = build_loaders(area, config)
        for stage, report in result["stages"].items():
            if report.get("error"):
                result["blockers"].append(stage+": "+report["error"])
                continue
            # Read every native batch, not merely len(dataset), to expose decoding/shape failures.
            for batch in loaders[stage]:
                tensors = list(batch.values()) if isinstance(batch, dict) else list(batch)
                for tensor in tensors:
                    if torch.is_tensor(tensor):
                        if not torch.isfinite(tensor).all():
                            raise ValueError(f"{stage} batch 含非法数值")
                        device_tensor = tensor.cuda()
                        del device_tensor
            report["loader_read"] = "PASSED"
            report["filtered_names"] = sorted(set(manifest["entries"])-set(report["names"]))
        torch.cuda.synchronize()
        free, total = torch.cuda.mem_get_info()
        result["gpu"] = dict(device=torch.cuda.get_device_name(0), free_bytes=free, total_bytes=total,
            peak_allocated_bytes=torch.cuda.max_memory_allocated(), probe="loader_tensors_only",
            training_memory="NOT_MEASURED")
    except Exception as exc:
        traceback.print_exc()
        result["blockers"].append(f"PREFLIGHT_FAILED: {type(exc).__name__}: {exc}")
    result["ready"] = not result["blockers"]
    return result


def main():
    request_path, result_path = [Path(p).resolve() for p in sys.argv[1:3]]
    request = json.loads(request_path.read_text(encoding="utf-8-sig"))
    try:
        result = features(request) if request["action"] == "features" else preflight(request)
    except Exception as exc:
        traceback.print_exc()
        result = dict(ready=False, errors=[dict(message=str(exc))], path=str(Path(request["data_root"])/"features"/request["version"]["id"]))
    result_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(result_path, result)


if __name__ == "__main__":
    main()
