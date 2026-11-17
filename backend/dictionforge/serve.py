"""Owned local server with the application's safe shutdown endpoint."""
import uvicorn
from .api import create_app
from .settings import Settings

def main():
    settings = Settings.from_environment()
    app = create_app(settings)
    # Browser media navigation on Windows can leave a disconnected transport in
    # uvicorn's connection set. The stop route already blocks active jobs and
    # drains new submissions; bound HTTP teardown so a stale player cannot hold
    # app.lock indefinitely after an otherwise safe shutdown.
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=settings.port,
        access_log=False, timeout_graceful_shutdown=15))
    app.state.server = server
    server.run()

if __name__ == "__main__":
    main()
