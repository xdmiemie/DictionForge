"""Keep an HTTP file reference until the entire response/disconnect completes."""
from pathlib import Path
from mimetypes import guess_type
from fastapi.responses import FileResponse
from .records import get
from .media_lifecycle import acquire_http, release


class LeasedFileResponse(FileResponse):
    def __init__(self, jobs, lease, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.jobs, self.lease = jobs, lease

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Synchronous finally also runs when ASGI cancels a disconnected
            # browser response. No fire-and-forget release can be lost.
            release(self.jobs, self.lease['token'])


def leased_audio(jobs, key, *, download=False):
    lease = acquire_http(jobs, key)
    try:
        record = get(jobs, 'media', key)
        path = Path(record['path'])
        media_type = {'.mp3': 'audio/mpeg', '.wav': 'audio/wav'}.get(path.suffix.lower())
        return LeasedFileResponse(jobs, lease, path, filename=path.name if download else None,
            media_type=media_type or guess_type(path.name)[0] or 'application/octet-stream')
    except BaseException:
        release(jobs, lease['token'])
        raise
