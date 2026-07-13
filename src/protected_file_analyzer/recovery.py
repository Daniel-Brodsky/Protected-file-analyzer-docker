from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Settings


@dataclass(frozen=True)
class RecoveryAttempt:
    kind: str
    source: str
    path: Path | None = None
    mask: str | None = None
    timeout_seconds: int = 0
    max_candidates: int | None = None
    prefixes: tuple[str, ...] = ()


INTERNAL_POLICY_ORDER = (
    "custom_upload",
    "mounted_wordlists",
    "pin4",
    "rockyou",
    "scoped_org_patterns",
)


def describe_wordlist_providers(settings: Settings) -> dict:
    mounted = settings.mounted_wordlists()
    scoped_prefixes = settings.scoped_org_id_prefixes()
    return {
        "rockyou": settings.default_rockyou_path.exists(),
        "mounted": [path.name for path in mounted],
        "custom_upload": True,
        "scoped_org_patterns": {
            "enabled": settings.scoped_org_patterns_enabled,
            "configured_prefixes": len(scoped_prefixes),
            "max_candidates": settings.scoped_org_pattern_max_candidates,
            "timeout_seconds": settings.scoped_org_pattern_timeout_seconds,
        },
    }


def build_recovery_plan(settings: Settings, *, custom_wordlist_path: Path | None = None) -> list[RecoveryAttempt]:
    attempts: list[RecoveryAttempt] = []
    if custom_wordlist_path and custom_wordlist_path.exists():
        attempts.append(
            RecoveryAttempt(
                kind="wordlist",
                source="custom_upload",
                path=custom_wordlist_path,
                timeout_seconds=settings.recovery_custom_timeout_seconds,
                max_candidates=settings.recovery_custom_max_candidates,
            )
        )
    for candidate in settings.mounted_wordlists():
        attempts.append(
            RecoveryAttempt(
                kind="wordlist",
                source="mounted_wordlists",
                path=candidate,
                timeout_seconds=settings.recovery_mounted_timeout_seconds,
                max_candidates=settings.recovery_mounted_max_candidates,
            )
        )
    attempts.append(
        RecoveryAttempt(
            kind="mask",
            source="pin4",
            mask="?d?d?d?d",
            timeout_seconds=settings.recovery_pin_timeout_seconds,
            max_candidates=settings.recovery_pin_max_candidates,
        )
    )
    if settings.default_rockyou_path.exists():
        attempts.append(
            RecoveryAttempt(
                kind="wordlist",
                source="rockyou",
                path=settings.default_rockyou_path,
                timeout_seconds=settings.recovery_rockyou_timeout_seconds,
                max_candidates=settings.recovery_rockyou_max_candidates,
            )
        )
    scoped_prefixes = tuple(settings.scoped_org_id_prefixes())
    if settings.scoped_org_patterns_enabled and scoped_prefixes:
        attempts.append(
            RecoveryAttempt(
                kind="generator",
                source="scoped_org_patterns",
                timeout_seconds=settings.scoped_org_pattern_timeout_seconds,
                max_candidates=settings.scoped_org_pattern_max_candidates,
                prefixes=scoped_prefixes,
            )
        )
    return attempts
