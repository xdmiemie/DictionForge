import hashlib
import json
import math
from pathlib import Path
import shutil
import time
import uuid
import numpy as np
import soundfile as sf
from .audio import atomic_json, digest, ffmpeg, probe
from .jobs import Conflict
from .records import get, put, list_records, media
from .worker import check_cancel

RATE = 48000
ACCEPTED = {".wav", ".flac", ".mp3", ".ogg", ".m4a", ".opus", ".aac"}
LANGUAGES = {"zh"}

def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def decode(path):
    pcm = np.frombuffer(ffmpeg("-i", path, "-vn", "-t", "1801", "-ac", 1, "-ar", RATE, "-f", "f32le", "pipe:1"), dtype="<f4")
    if not pcm.size or not np.isfinite(pcm).all() or pcm.size > 1800 * RATE:
        raise ValueError("音频为空、损坏或超过30分钟")
    return pcm

def scan(jobs, job):
    p = job["payload"]
    source = Path(p["source"]).expanduser().resolve()
    if not source.is_dir():
        raise ValueError("录音目录不存在")
    if jobs.root.resolve().is_relative_to(source) or source.is_relative_to(jobs.root.resolve() / "datasets"):
        raise ValueError("录音目录不能包含应用数据根或已整理数据")
    if p["language"] not in LANGUAGES:
        raise ValueError("UNSUPPORTED_LANGUAGE：当前已验收语言为普通话 zh")
    candidates = source.rglob("*") if p.get("recursive", True) else source.glob("*")
    files = sorted(path for path in candidates if path.suffix.lower() in ACCEPTED and path.is_file()
                   and not path.is_symlink() and path.resolve().is_relative_to(source))
    if not files:
        raise ValueError("目录内没有支持的录音")
    records, seen, signatures = [], set(), []
    for i, path in enumerate(files):
        check_cancel(jobs, job["id"])
        record = {"id": uuid.uuid4().hex, "name": str(path.relative_to(source)), "path": str(path), "seconds": 0,
                  "status": "damaged", "reason": None}
        try:
            record.update(probe(path))
            record["source_sha256"] = digest(path)
            label = path.with_suffix(".lab")
            if label.is_file():
                record["label_sha256"] = digest(label)
                record["label_text"] = label.read_text(encoding="utf-8-sig").strip()
            pcm = decode(path)
            record["seconds"] = len(pcm) / RATE
            record["samples"] = len(pcm)
            record["pcm_sha256"] = hashlib.sha256(pcm.tobytes()).hexdigest()
            record["group"] = record["pcm_sha256"]
            signature = pcm[np.linspace(0, len(pcm)-1, min(8192, len(pcm)), dtype=int)].astype(np.float64)
            norm = float(np.linalg.norm(signature))
            if norm:
                signature /= norm
                for frames, prior_signature, group in signatures:
                    if frames == len(pcm) and float(signature @ prior_signature) > .995:
                        record["group"] = group
                        record["near_duplicate"] = True
                        break
                signatures.append((len(pcm), signature, record["group"]))
            record["peak"] = float(np.max(np.abs(pcm)))
            record["status"] = "ready"
            if record["peak"] < 10 ** (-48 / 20):
                record.update(status="silent", reason="静音或电平低于 -48 dB")
            elif record["seconds"] < .5:
                record.update(status="short", reason="录音短于0.5秒")
            elif record["pcm_sha256"] in seen or record.get("near_duplicate"):
                record.update(status="duplicate", reason="解码重复或同长度波形高度一致（相关度>0.995）")
            seen.add(record["pcm_sha256"])
            record["audio_id"] = media(jobs, path, external=True, owner=job["id"])
        except Exception as exc:
            record["status"] = "damaged"
            record["reason"] = str(exc)
        records.append(record)
        jobs.progress(job["id"], {"stage": "scan", "completed": i+1, "total": len(files), "unit": "文件"})
    result = {"scan_id": job["id"], "source": str(source), "role": p["role"], "language": p["language"],
              "files": records, "seconds": sum(r["seconds"] for r in records if r["status"] == "ready"),
              "fingerprint": fingerprint(records)}
    put(jobs, "scan", job["id"], result)
    return result

def bounds(pcm, *, min_seconds=1.0, max_seconds=12.0, silence_db=-40.0, silence_seconds=.4):
    if not (0.5 <= min_seconds <= max_seconds <= 30 and -80 <= silence_db <= -10 and .1 <= silence_seconds <= 3):
        raise ValueError("切分参数无效")
    frame = RATE // 50
    padded = np.pad(pcm, (0, -len(pcm) % frame))
    rms = np.sqrt(np.mean(padded.reshape(-1, frame) ** 2, axis=1))
    voiced = np.flatnonzero(rms >= 10 ** (silence_db / 20))
    if not len(voiced):
        return []
    breaks = np.flatnonzero(np.diff(voiced) * frame >= silence_seconds * RATE) + 1
    intervals = []
    for group in np.split(voiced, breaks):
        start, stop = max(0, int(group[0])*frame-RATE//10), min(len(pcm), (int(group[-1])+1)*frame+RATE//10)
        if intervals and start <= intervals[-1][1]:
            intervals[-1] = (intervals[-1][0], stop)
        else:
            intervals.append((start, stop))
    answer = []
    for start, stop in intervals:
        while stop-start > max_seconds*RATE:
            high = start + int(max_seconds*RATE)
            low = max(start+int(min_seconds*RATE), high-RATE)
            region = rms[(low+frame-1)//frame:high//frame]
            cut = ((low+frame-1)//frame+int(np.argmin(region)))*frame if len(region) else high
            answer.append((start, cut))
            start = cut
        if stop-start >= min_seconds*RATE:
            answer.append((start, stop))
    return answer

def get_draft(jobs, key):
    return get(jobs, "draft", key)

def versions(jobs):
    return list_records(jobs, "version")

def edit_version(jobs, version_id, request_id):
    with jobs.connect(write=True) as db:
        version = get(jobs, "version", version_id, db)
        try:
            existing = get(jobs, "draft", request_id, db)
            if existing.get("parent_version") != version_id:
                raise Conflict("该请求已关联其他版本")
            return existing
        except KeyError:
            pass
        draft = {"id": request_id, "dataset_id": version["dataset_id"], "role": version["role"], "revision": 0,
                 "clips": [dict(c) for c in version["clips"]], "config": version["config"],
                 "parent_version": version_id, "created": time.time(), "rejected": []}
        return put(jobs, "draft", request_id, draft, db)

def transcribe(jobs, job, draft, clip_ids=None):
    from .settings import Settings, ROOT
    from .engine import invoke
    settings = Settings.from_environment()
    selected = [c for c in draft["clips"] if not c["reviewed"] and not c["excluded"]
                and not (c.get("label_source") == "manual" and c["text"].strip())
                and (clip_ids is None or c["id"] in clip_ids)]
    if not selected:
        return draft
    area = jobs.root / "runs" / job["id"]
    area.mkdir(parents=True, exist_ok=True)
    request, result = area / "asr-request.json", area / "asr-result.json"
    atomic_json(request, {"upstream": str(settings.upstream), "clips": [{"id": c["id"], "path": c["path"], "language": c["language"]} for c in selected]})
    output = invoke(jobs, job, [settings.worker_python, "-m", "dictionforge.asr_worker", request, result], result=result)
    with jobs.connect(write=True) as db:
        current = get(jobs, "draft", draft["id"], db)
        if current.get("published"):
            return current
        by_id = {c["id"]: c for c in output["clips"]}
        for clip in current["clips"]:
            if (clip["id"] in by_id and not clip["reviewed"]
                    and not (clip.get("label_source") == "manual" and clip["text"].strip())):
                row = by_id[clip["id"]]
                clip.update(text=row.get("text", ""), asr_candidate=row.get("text", ""), asr_error=row.get("error"),
                            label_source="asr", confidence=None, asr_seconds=row.get("seconds"))
        current["revision"] += 1
        current["asr_assets"] = output.get("asset_revision")
        return put(jobs, "draft", current["id"], current, db)

def prepare(jobs, job):
    p = job["payload"]
    scanned = get(jobs, "scan", p["scan_id"])
    excluded = set(p.get("excluded", []))
    if excluded - {f["id"] for f in scanned["files"]}:
        raise ValueError("排除项不属于本次扫描")
    config = {k: p.get(k, v) for k, v in {"min_seconds": 1.0, "max_seconds": 12.0, "silence_db": -40.0, "silence_seconds": .4}.items()}
    draft_id = job["id"]
    area = jobs.root / "drafts" / draft_id
    area.mkdir(parents=True, exist_ok=True)
    clips, rejected = [], []
    for record in scanned["files"]:
        check_cancel(jobs, job["id"])
        if record["id"] in excluded or record["status"] != "ready":
            rejected.append({"id": record["id"], "reason": "手动排除" if record["id"] in excluded else record["reason"]})
            continue
        source = Path(record["path"])
        if digest(source) != record["source_sha256"]:
            raise Conflict("源录音已变化，请重新扫描")
        pcm = decode(source)
        ranges = bounds(pcm, **config)
        if not ranges:
            rejected.append({"id": record["id"], "reason": "按当前规则未保留有效片段"})
        label = source.with_suffix(".lab")
        if record.get("label_sha256") and (not label.is_file() or digest(label) != record["label_sha256"]):
            raise Conflict("源标注已变化，请重新扫描")
        text = record.get("label_text", "") if len(ranges) == 1 else ""
        for start, stop in ranges:
            key = uuid.uuid4().hex
            path = area / (key + ".wav")
            sf.write(path, pcm[start:stop], RATE, subtype="PCM_16")
            clips.append({"id": key, "path": str(path), "audio_id": media(jobs, path, owner=draft_id),
                          "sha256": digest(path), "seconds": (stop-start)/RATE, "sample_rate": RATE,
                          "start_sample": start, "end_sample": stop, "source_sample_rate": record["sample_rate"],
                          "source_start_sample": round(start*record["sample_rate"]/RATE),
                          "source_end_sample": round(stop*record["sample_rate"]/RATE),
                          "source_id": record["id"], "source_sha256": record["source_sha256"], "group": record["group"],
                          "source_path": record["path"], "source": "recording", "language": scanned["language"],
                          "text": text, "label_source": "sidecar" if text else "pending", "reviewed": False,
                          "excluded": False, "asr_error": None, "clipping_ratio": float(np.mean(np.abs(pcm[start:stop]) >= .999))})
        jobs.progress(job["id"], {"stage": "slice", "completed": len(clips), "unit": "片段"})
    if not clips:
        raise ValueError("整理后没有可用语音片段，请检查排除项和切分参数")
    dataset_id = fingerprint([scanned["source"].casefold(), scanned["role"]])[:24]
    draft = {"id": draft_id, "dataset_id": dataset_id, "role": scanned["role"], "revision": 0,
             "clips": clips, "scan_id": p["scan_id"], "config": config, "rejected": rejected, "created": time.time()}
    put(jobs, "draft", draft_id, draft)
    if p.get("asr", True):
        try:
            transcribe(jobs, job, draft, [c["id"] for c in clips if not c["text"]])
        except Exception as exc:
            # Preserve audio and editable draft when the recognizer is unavailable.
            from .errors import Cancelled
            if isinstance(exc, Cancelled):
                raise
            draft = get_draft(jobs, draft_id)
            draft["asr_error"] = str(exc)
            put(jobs, "draft", draft_id, draft)
    return {"draft_id": draft_id, "clips": len(clips), "seconds": sum(c["seconds"] for c in clips)}

def update_draft(jobs, key, revision, clips):
    with jobs.connect(write=True) as db:
        draft = get(jobs, "draft", key, db)
        if draft["revision"] != revision:
            raise Conflict("VERSION_CONFLICT：草稿已更新，请重新载入")
        if draft.get("published"):
            raise Conflict("草稿已发布，修改标注应创建新版本")
        lookup = {c["id"]: c for c in draft["clips"]}
        for change in clips:
            clip = lookup[change["id"]]
            if change.get("language", clip["language"]) not in LANGUAGES:
                raise ValueError("UNSUPPORTED_LANGUAGE")
            clip.update({k: v for k, v in change.items() if k in {"text", "language", "reviewed", "excluded"}})
            if "text" in change:
                clip["label_source"] = "manual"
        draft["revision"] += 1
        return put(jobs, "draft", key, draft, db)

def publish(jobs, key, revision):
    with jobs.connect(write=True) as db:
        draft = get(jobs, "draft", key, db)
        if draft.get('augmentation_id'):
            raise Conflict('APPEND_PUBLISH_REQUIRED：回流草稿请从当前句追加面板发布，以验证特征和父版本')
        if draft.get("published"):
            return get(jobs, "version", draft["published"], db)
        if draft["revision"] != revision:
            raise Conflict("VERSION_CONFLICT：草稿已更新")
        clips = [dict(c) for c in draft["clips"] if not c["excluded"]]
        if not clips or any(not c["reviewed"] or not c["text"].strip() for c in clips):
            raise ValueError("发布前请确认每个保留片段的非空标注")
        groups = sorted({c["group"] for c in clips})
        validation = {groups[0]} if len(groups) > 1 else set()
        version_id = uuid.uuid4().hex
        parent = jobs.root / "datasets" / draft["dataset_id"]
        staging, final = parent / ("."+version_id+".part"), parent / version_id
        staging.mkdir(parents=True)
        lines = []
        try:
            for c in clips:
                if digest(Path(c["path"])) != c["sha256"]:
                    raise ValueError("片段哈希变化，无法发布")
                name = c["id"]+".wav"
                shutil.copyfile(c["path"], staging / name)
                c["path"] = str(final / name)
                c["raw_text"] = c["text"]
                c["text"] = " ".join(c["text"].replace("|", "，").split())
                c["split"] = c.get("split") if draft.get("parent_version") else ("validation" if c["group"] in validation else "train")
                if c["split"] == "train":
                    lines.append(f"{final / name}|{draft['dataset_id']}|ZH|{c['text']}")
            version = {"id": version_id, "dataset_id": draft["dataset_id"], "role": draft["role"], "parent_id": draft.get("parent_version"),
                       "created": time.time(), "clips": clips, "seconds": sum(c["seconds"] for c in clips),
                       "feature_status": "MISSING", "config": draft["config"], "fingerprint": fingerprint(clips),
                       "path": str(final), "list_path": str(final / "train.list"),
                       "warnings": [] if validation else ["仅一个来源组，无独立留出试听"]}
            (staging / "train.list").write_text("\n".join(lines)+"\n", encoding="utf-8")
            atomic_json(staging / "manifest.json", version)
            staging.replace(final)
            for c in clips:
                c["audio_id"] = media(jobs, c["path"], owner=version_id, db=db)
            version["fingerprint"] = fingerprint(clips)
            atomic_json(final / "manifest.json", version)
            put(jobs, "version", version_id, version, db)
            draft["published"] = version_id
            put(jobs, "draft", key, draft, db)
            return version
        except BaseException:
            # Leave unregistered staging for diagnosis; never remove original recordings.
            raise
