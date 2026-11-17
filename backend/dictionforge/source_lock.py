"""Expose the repository's read-only source-lock verifier to isolated workers."""
import importlib.util
from pathlib import Path


def verify_source(root, upstream):
    script = Path(__file__).resolve().parents[2] / "scripts/apply_patches.py"
    spec = importlib.util.spec_from_file_location("dictionforge_locked_patches", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify_locked(Path(root), upstream=Path(upstream))
