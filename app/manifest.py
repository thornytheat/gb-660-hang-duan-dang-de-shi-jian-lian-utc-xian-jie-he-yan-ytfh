from hashlib import sha256

from app.baseline_state import current_baseline
from app.config import RULE_VERSION


def rule_version(baseline: str | None = None) -> str:
    """规则版本包含生效的机场基准版本，换版后同一输入不再复用旧运行。"""
    version = baseline or current_baseline()
    return f"{RULE_VERSION}+{version}"


def input_digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def run_id(payload: bytes, baseline: str | None = None) -> str:
    return f"{rule_version(baseline)}-{input_digest(payload)[:12]}"


def build_manifest(payload: bytes, accepted: int, quarantined: int, baseline: str | None = None) -> dict:
    version = baseline or current_baseline()
    return {
        "run_id": run_id(payload, version),
        "input_sha256": input_digest(payload),
        "rule_version": rule_version(version),
        "baseline_version": version,
        "accepted": accepted,
        "quarantined": quarantined,
    }
