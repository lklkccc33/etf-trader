from dataclasses import dataclass

import yaml

from .config import settings

_REQUIRED_KEYS = ("name", "health_url", "systemd_service")


@dataclass(frozen=True)
class Target:
    name: str
    health_url: str
    systemd_service: str


def load_targets() -> list[Target]:
    """targets.yaml을 읽어 감시 대상 목록을 만든다.

    설정이 잘못된 경우 어느 항목이 왜 잘못됐는지 알 수 있는 메시지와 함께
    ValueError를 던진다 — 호출부(monitor.py)에서 이 메시지를 그대로 알림에 실어보낸다.
    """
    with open(settings.TARGETS_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    raw_targets = data.get("targets") or []
    if not isinstance(raw_targets, list):
        raise ValueError("targets.yaml의 'targets'는 목록이어야 합니다.")

    targets = []
    seen_names = set()
    for i, raw in enumerate(raw_targets, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"targets.yaml의 {i}번째 항목이 올바른 형식이 아닙니다.")
        if not raw.get("enabled", True):
            continue

        missing = [k for k in _REQUIRED_KEYS if not raw.get(k)]
        if missing:
            raise ValueError(
                f"targets.yaml의 {i}번째 항목에 필수 항목이 없습니다: {', '.join(missing)}"
            )

        name = raw["name"]
        if name in seen_names:
            raise ValueError(f"targets.yaml에 중복된 name이 있습니다: {name}")
        seen_names.add(name)

        targets.append(
            Target(
                name=name,
                health_url=raw["health_url"],
                systemd_service=raw["systemd_service"],
            )
        )
    return targets
