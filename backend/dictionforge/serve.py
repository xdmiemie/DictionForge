"""Owned local server with the application's safe shutdown endpoint."""
import uvicorn
from .api import create_app
from .settings import Settings

def main():
    settings = Settings.from_environment()
    app = create_app(settings)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=settings.port, access_log=False))
    app.state.server = server
    server.run()

if __name__ == "__main__":
    main()
