from dataclasses import dataclass, field


@dataclass
class TargetState:
    in_incident: bool = False
    gave_up: bool = False
    # 재시작을 실행한 시각(time.monotonic 기준) 목록.
    # 단순 카운터가 아니라 시각 목록인 이유: 장애→복구→장애가 반복될 때
    # 카운터가 매번 0으로 초기화되면 재시작 한도가 사실상 사라지기 때문.
    restart_times: list[float] = field(default_factory=list)
    # 재시작 "명령" 자체가 실패했는지(주로 sudo 권한 문제).
    last_restart_failed: bool = False
    # systemd가 상태 전환 중이라 판단을 미룬 연속 횟수.
    transition_cycles: int = 0

    def clear_incident(self) -> None:
        """복구 시 호출. restart_times는 일부러 남긴다 —
        플래핑으로 재시작 한도가 초기화되는 것을 막기 위해."""
        self.in_incident = False
        self.gave_up = False
        self.last_restart_failed = False
        self.transition_cycles = 0

    def prune_restarts(self, now: float, window: float) -> None:
        self.restart_times = [t for t in self.restart_times if now - t < window]
