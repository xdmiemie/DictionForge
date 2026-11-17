from fastapi import Request, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ConfigDict
from uuid import UUID
from typing import Literal
from pathlib import Path
from . import datasets
from .records import get, list_records

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

class Scan(Strict):
    request_id: UUID
    source: str
    role: str = Field(min_length=1, max_length=80)
    language: str = "zh"
    recursive: bool = True

class Prepare(Strict):
    request_id: UUID
    scan_id: str
    excluded: list[str] = Field(default_factory=list)
    min_seconds: float = Field(default=1, ge=.5, le=30)
    max_seconds: float = Field(default=12, ge=.5, le=30)
    silence_db: float = Field(default=-40, ge=-80, le=-10)
    silence_seconds: float = Field(default=.4, ge=.1, le=3)

class ClipEdit(Strict):
    id: str
    text: str | None = None
    language: str | None = None
    reviewed: bool | None = None
    excluded: bool | None = None

class DraftEdit(Strict):
    revision: int
    clips: list[ClipEdit]

class Publish(Strict):
    revision: int

class Retry(Strict):
    request_id: UUID
    clip_ids: list[str]

class Folder(Strict):
    initial: str = ""

class Identified(Strict):
    request_id: UUID

class S2Config(Strict):
    batch_size: int = Field(default=1, ge=1, le=64)
    precision: Literal["fp16", "fp32"] = "fp16"
    rank: int = Field(default=32, ge=1, le=128)
    stop_mode: Literal["steps", "epochs"] = "epochs"
    target: int = Field(default=1, ge=1)
    learning_rate: float = Field(default=.0001, gt=0, le=1)

class S1Config(Strict):
    batch_size: int = Field(default=1, ge=1, le=64)
    precision: Literal["fp16", "fp32"] = "fp16"
    stop_mode: Literal["steps", "epochs"] = "epochs"
    target: int = Field(default=1, ge=1)
    learning_rate: float = Field(default=.002, gt=0, le=1)
    accumulate: int = Field(default=4, ge=1, le=64)

class TrainingConfiguration(Identified):
    version_id: str
    initial_model: str = "official"
    stages: list[Literal["s2", "s1"]] = Field(default_factory=lambda: ["s2", "s1"])
    seed: int = Field(default=42, ge=0, le=2147483647)
    s2: S2Config = Field(default_factory=S2Config)
    s1: S1Config = Field(default_factory=S1Config)

class TrainingStart(Identified):
    preflight_id: str

class TrainingControl(Identified):
    action: Literal["pause", "resume", "export", "stop_export"]

class Derive(Identified):
    checkpoint_id: str

class ModelImport(Identified):
    gpt_path: str
    sovits_path: str
    role: str = Field(min_length=1, max_length=80)
    trusted: bool = False

class DeleteConfirmation(Strict):
    preview_id: str

class ModelProbe(Identified):
    reference_audio_id: str | None = None
    reference_text: str | None = None
    reference_language: Literal['zh'] = 'zh'

class ReferenceImport(Identified):
    source_path: str | None = None
    audio_id: str | None = None
    text: str = Field(min_length=1,max_length=1000)
    language: Literal['zh'] = 'zh'

class DefaultReference(Identified):
    reference_id: str

def register(app):
    @app.get('/api/synthesis/selection')
    def selection(request: Request):
        from .references_service import selection
        return selection(request.app.state.jobs)

    @app.get('/api/references')
    def references(request: Request):
        from .references_service import list_references
        return list_references(request.app.state.jobs)

    @app.post('/api/references/import')
    def reference_import(body: ReferenceImport, request: Request):
        from .references_service import import_reference
        return import_reference(request.app.state.jobs,source_path=body.source_path,audio_id=body.audio_id,
            text=body.text,language=body.language,request_id=str(body.request_id))

    @app.post('/api/references/upload')
    async def reference_upload(request: Request, request_id: UUID, text: str = Query(min_length=1,max_length=1000),
                               language: Literal['zh'] = 'zh', name: str = Query(default='reference',max_length=255)):
        import asyncio
        import os
        from .references_service import import_reference
        area=request.app.state.jobs.root/'uploads'
        area.mkdir(parents=True,exist_ok=True)
        incoming=area/(request_id.hex+'.upload')
        try:
            stream=incoming.open('xb')
        except FileExistsError:
            raise HTTPException(409,'同一参考上传正在处理，请稍后重试') from None
        try:
            size=0
            with stream:
                async for chunk in request.stream():
                    size+=len(chunk)
                    if size>25*1024**2:raise HTTPException(413,'参考文件最大25 MiB')
                    stream.write(chunk)
                stream.flush();os.fsync(stream.fileno())
            if not size:raise ValueError('参考音频文件不能为空')
            importing=asyncio.create_task(asyncio.to_thread(import_reference,request.app.state.jobs,source_path=str(incoming),
                text=text,language=language,request_id=str(request_id)))
            cancelled=False
            while True:
                try:
                    result=await asyncio.shield(importing)
                    break
                except asyncio.CancelledError:
                    cancelled=True
                    if importing.done():
                        # A completed/cancelled importer no longer owns this request.
                        result=importing.result()
                        break
            if cancelled:raise asyncio.CancelledError()
            return result
        finally:
            incoming.unlink(missing_ok=True)

    @app.post('/api/models/{key}/reference')
    def default_reference(key: str,body: DefaultReference,request: Request):
        from .references_service import set_default
        return set_default(request.app.state.jobs,key,body.reference_id,str(body.request_id))

    @app.post('/api/models/{key}/enable')
    def enable_model(key: str,body: Identified,request: Request):
        from .references_service import enable
        return enable(request.app.state.jobs,key,str(body.request_id))

    @app.post('/api/models/disable')
    def disable_model(body: Identified,request: Request):
        from .references_service import disable
        return disable(request.app.state.jobs)

    @app.post('/api/models/{key}/compare',status_code=202)
    def compare_model(key: str,body: Identified,request: Request):
        from .model_probe import compare
        return compare(request.app.state.jobs,key,str(body.request_id))

    @app.get('/api/comparisons')
    def comparisons(request: Request):
        from .model_probe import comparisons
        return comparisons(request.app.state.jobs)

    @app.get('/api/comparisons/{key}')
    def comparison(key: str,request: Request):
        from .model_probe import comparison_view
        return comparison_view(request.app.state.jobs,key)

    @app.get("/api/models")
    def models(request: Request):
        from .models import list_models
        return list_models(request.app.state.jobs)

    @app.post("/api/models/import", status_code=202)
    def model_import(body: ModelImport, request: Request):
        from .models import import_submit
        return import_submit(request.app.state.jobs, body.gpt_path, body.sovits_path,
                             body.role, trusted=body.trusted, request_id=str(body.request_id))

    @app.post("/api/models/{key}/probe", status_code=202)
    def model_probe(key: str, body: ModelProbe, request: Request):
        from .model_probe import submit
        return submit(request.app.state.jobs,key,str(body.request_id),body.reference_audio_id,body.reference_text,body.reference_language)

    @app.post("/api/model-exports/{key}/retry", status_code=202)
    def export_retry(key: str, body: Identified, request: Request):
        from .exports import retry
        return retry(request.app.state.jobs, key, str(body.request_id))

    @app.get("/api/models/{key}/delete-preview")
    def model_delete_preview(key: str, request: Request):
        from .references import delete_preview
        return delete_preview(request.app.state.jobs, "model", key)

    @app.delete("/api/models/{key}")
    def model_delete(key: str, body: DeleteConfirmation, request: Request):
        from .references import delete
        return delete(request.app.state.jobs, "model", key, body.preview_id)

    @app.get("/api/dataset-versions/{key}/delete-preview")
    def dataset_delete_preview(key: str, request: Request):
        from .references import delete_preview
        return delete_preview(request.app.state.jobs, "version", key)

    @app.delete("/api/dataset-versions/{key}")
    def dataset_delete(key: str, body: DeleteConfirmation, request: Request):
        from .references import delete
        return delete(request.app.state.jobs, "version", key, body.preview_id)

    @app.post("/api/folders/pick")
    def folder(body: Folder):
        from .folder_picker import pick_folder
        return {"path": pick_folder(body.initial)}

    @app.post("/api/datasets/scan", status_code=202)
    def scan(body: Scan, request: Request):
        return request.app.state.jobs.submit("dataset_scan", body.model_dump(exclude={"request_id"}), str(body.request_id))

    @app.post("/api/datasets/prepare", status_code=202)
    def prepare(body: Prepare, request: Request):
        jobs = request.app.state.jobs
        get(jobs, "scan", body.scan_id)
        if body.min_seconds > body.max_seconds:
            raise ValueError("最小时长不能超过最大片段时长")
        return jobs.submit("dataset_prepare", body.model_dump(exclude={"request_id"}), str(body.request_id))

    @app.get("/api/datasets")
    def versions(request: Request):
        return datasets.versions(request.app.state.jobs)

    @app.post("/api/dataset-versions/{key}/draft")
    def edit_version(key: str, body: Identified, request: Request):
        return datasets.edit_version(request.app.state.jobs, key, str(body.request_id))

    @app.post("/api/dataset-versions/{key}/features", status_code=202)
    def features(key: str, body: Identified, request: Request):
        jobs = request.app.state.jobs
        get(jobs, "version", key)
        return jobs.submit("features", {"version_id": key}, str(body.request_id))

    @app.post("/api/training/preflight", status_code=202)
    def preflight(body: TrainingConfiguration, request: Request):
        from .training_config import normalize_config
        jobs = request.app.state.jobs
        get(jobs, "version", body.version_id)
        config = normalize_config(body.model_dump(exclude={"request_id"}))
        return jobs.submit("preflight", config, str(body.request_id))

    @app.post("/api/training", status_code=202)
    def training_start(body: TrainingStart, request: Request):
        from .training import submit
        return submit(request.app.state.jobs, body.preflight_id, str(body.request_id))

    @app.get("/api/training/{key}")
    def training_snapshot(key: str, request: Request):
        from .training import snapshot
        return snapshot(request.app.state.jobs, key)

    @app.get("/api/training/{key}/checkpoints")
    def training_checkpoints(key: str, request: Request):
        from .training import checkpoints
        return checkpoints(request.app.state.jobs, key)

    @app.post("/api/training/{key}/controls", status_code=202)
    def training_control(key: str, body: TrainingControl, request: Request):
        from .training import control
        return control(request.app.state.jobs, key, body.action, str(body.request_id))

    @app.post("/api/training/{key}/derive", status_code=202)
    def training_derive(key: str, body: Derive, request: Request):
        from .training import derive
        return derive(request.app.state.jobs, key, body.checkpoint_id, str(body.request_id))

    @app.post("/api/training/{key}/export-intents/{intent_id}/retry", status_code=202)
    def retry_export_intent(key: str, intent_id: str, body: Identified, request: Request):
        from .exports import retry_intent
        return retry_intent(request.app.state.jobs, key, intent_id)

    @app.get("/api/dataset-drafts")
    def drafts(request: Request):
        return list_records(request.app.state.jobs, "draft")

    @app.get("/api/dataset-drafts/{key}")
    def draft(key: str, request: Request):
        return datasets.get_draft(request.app.state.jobs, key)

    @app.patch("/api/dataset-drafts/{key}")
    def edit(key: str, body: DraftEdit, request: Request):
        return datasets.update_draft(request.app.state.jobs, key, body.revision,
                                     [c.model_dump(exclude_none=True) for c in body.clips])

    @app.post("/api/dataset-drafts/{key}/publish")
    def publish(key: str, body: Publish, request: Request):
        return datasets.publish(request.app.state.jobs, key, body.revision)

    @app.post("/api/dataset-drafts/{key}/asr", status_code=202)
    def retry(key: str, body: Retry, request: Request):
        jobs = request.app.state.jobs
        datasets.get_draft(jobs, key)
        return jobs.submit("dataset_asr", {"draft_id": key, "clip_ids": body.clip_ids}, str(body.request_id))

    @app.get("/api/audio/{key}")
    def audio(key: str, request: Request, download: bool = False):
        record = get(request.app.state.jobs, "media", key)
        path = Path(record["path"])
        if record.get("state") == "DELETED" or not path.is_file():
            raise HTTPException(410, "AUDIO_EXPIRED：音频已失效")
        return FileResponse(path, filename=path.name if download else None)
