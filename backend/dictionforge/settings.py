from dataclasses import dataclass
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

@dataclass(frozen=True)
class Settings:
    root: Path
    upstream: Path = ROOT / "vendor/gpt-sovits"
    worker_python: str = str(ROOT / ".venv-worker/Scripts/python.exe")
    port: int = 7867

    @classmethod
    def from_environment(cls):
        return cls(Path(os.environ.get("DICTIONFORGE_DATA", ROOT / "data")).resolve(),
                   Path(os.environ.get("DICTIONFORGE_UPSTREAM", ROOT / "vendor/gpt-sovits")).resolve(),
                   os.environ.get("DICTIONFORGE_WORKER_PYTHON", cls.worker_python),
                   int(os.environ.get("DICTIONFORGE_PORT", "7867")))
