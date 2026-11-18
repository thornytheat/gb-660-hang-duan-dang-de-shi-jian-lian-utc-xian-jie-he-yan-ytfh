from app.baseline_state import current_baseline
from app.manifest import build_manifest
from app.store import record_run

from .compare import reconcile


def run(payload: bytes = b"flightclock-sample", baseline: str | None = None) -> dict:
    account = reconcile(14, 11, 2, 1)
    manifest = build_manifest(payload, 11, 1, baseline or current_baseline())
    manifest["created"] = record_run(manifest)
    return {**manifest, **account}


def demo():
    return run()
