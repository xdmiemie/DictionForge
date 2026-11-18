from contextlib import asynccontextmanager
import asyncio
import json
import os
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from .jobs import Conflict, Jobs, TERMINAL
from .settings import ROOT, Settings
from .supervisor import Supervisor, exclusive
from .worker import JobError


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID


class Control(Submission):
    action: Literal["cancel", "pause", "export", "stop_export"]


def create_app(settings=None):
    settings = settings or Settings.from_environment()

    @asynccontextmanager
    async def lifespan(app):
        jobs = Jobs(settings.root)
        with exclusive(jobs.state / "app.lock"):
            from .references import reconcile_deletions
            reconcile_deletions(jobs)
            from .saved_speech import reconcile_deletions as reconcile_saved
            from .media_lifecycle import reconcile as reconcile_media
            reconcile_saved(jobs)
            reconcile_media(jobs)
            with jobs.connect(write=True) as db:
                db.execute("DELETE FROM records WHERE kind='service' AND id='draining'")
            supervisor = Supervisor(jobs)
            app.state.jobs = jobs
            supervisor.thread.start()
            try:
                yield
            finally:
                supervisor.stop.set()
                supervisor.thread.join()

    app = FastAPI(title="DictionForge", lifespan=lifespan)
    app.state.settings = settings

    @app.middleware("http")
    async def trusted_local_request(request, call_next):
        if request.url.hostname not in {"127.0.0.1", "localhost", "::1", "testserver"}:
            return JSONResponse({"detail": "服务仅支持本机访问"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "请求来源不匹配"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "记录不存在"}, status_code=404)

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc), "code": type(exc).__name__, "stage": "request", "retryable": False}, status_code=409)

    from .media_lifecycle import MediaGone
    @app.exception_handler(MediaGone)
    async def media_gone(request, exc):
        return JSONResponse({'detail': str(exc), 'code': 'AUDIO_EXPIRED', 'stage': 'media', 'retryable': False}, status_code=410)

    @app.exception_handler(JobError)
    @app.exception_handler(ValueError)
    async def job_error(request, exc):
        return JSONResponse({"detail": str(exc), "code": type(exc).__name__, "stage": "request", "retryable": False}, status_code=422)

    @app.get("/api/health")
    def health():
        return {"service": "ready", "name": app.title, "version": "0.1.0"}

    @app.post("/api/system/stop")
    def stop(request: Request):
        server = getattr(request.app.state, "server", None)
        if server is None:
            raise HTTPException(409, "请使用项目启动脚本管理该服务")
        store = request.app.state.jobs
        with store.connect(write=True) as db:
            if db.execute("SELECT 1 FROM jobs WHERE state IN ('QUEUED','RUNNING')").fetchone():
                raise Conflict("存在排队或运行中的任务，请先暂停训练或等待任务结束")
            db.execute("INSERT OR REPLACE INTO records VALUES('service','draining','{}')")
            server.should_exit = True
        return {"stopping": True, "pid": os.getpid()}

    @app.get("/api/jobs")
    def jobs(request: Request):
        return request.app.state.jobs.list()

    @app.post("/api/jobs/diagnostic", status_code=202)
    def diagnostic(body: Submission, request: Request):
        return request.app.state.jobs.submit("diagnostic", {}, str(body.request_id))

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str, request: Request):
        return request.app.state.jobs.get(job_id)

    @app.get("/api/jobs/{job_id}/log")
    def job_log(job_id: str, request: Request):
        store = request.app.state.jobs
        store.get(job_id)
        logs = []
        for name in ("worker.log", "process.log"):
            path = store.root / "runs" / job_id / name
            if path.is_file():
                with path.open("rb") as stream:
                    stream.seek(max(0, path.stat().st_size - 65536))
                    logs.append({"name": name, "text": stream.read().decode("utf-8", errors="replace")})
        return logs

    @app.post("/api/jobs/{job_id}/control", status_code=202)
    def control(job_id: str, body: Control, request: Request):
        return request.app.state.jobs.control(job_id, body.action, str(body.request_id))

    @app.get("/api/jobs/{job_id}/events")
    async def events(job_id: str, request: Request):
        store = request.app.state.jobs
        await asyncio.to_thread(store.get, job_id)
        try:
            after = max(0, int(request.headers.get("Last-Event-ID", "0")))
        except ValueError:
            raise HTTPException(400, "Last-Event-ID 无效") from None

        async def stream():
            cursor = after
            while not await request.is_disconnected():
                rows = await asyncio.to_thread(store.events, job_id, cursor)
                for row in rows:
                    cursor = row["id"]
                    data = json.dumps({**row, "schema_version": 1, "event_seq": row["id"], "event_type": row["kind"], "timestamp": row["created"], "stage": row["payload"].get("stage")}, ensure_ascii=False)
                    yield f"id: {cursor}\ndata: {data}\n\n"
                snapshot = await asyncio.to_thread(store.get, job_id)
                if not rows and snapshot["state"] in TERMINAL:
                    yield "event: end\ndata: {}\n\n"
                    return
                if not rows:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/settings")
    def settings_view():
        return {"data_root": str(settings.root), "upstream": str(settings.upstream),
                "worker_python": settings.worker_python, "port": settings.port}

    @app.get("/api/system/status")
    def system_status(request: Request):
        from .environment import status
        return status(settings, request.app.state.jobs)

    @app.post("/api/system/probe", status_code=202)
    def system_probe(body: Submission, request: Request):
        return request.app.state.jobs.submit("system_probe", {}, str(body.request_id))

    @app.post("/api/assets/prepare", status_code=202)
    def assets_prepare(body: Submission, request: Request):
        return request.app.state.jobs.submit("asset_prepare", {}, str(body.request_id))

    from .routes import register
    register(app)

    dist = ROOT / "frontend/dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def index():
        if not (dist / "index.html").is_file():
            raise HTTPException(503, "尚未构建前端，请运行 scripts/setup.ps1")
        return FileResponse(dist / "index.html")

    return app


app = create_app()
