"""공통 HTTP 재시도/백오프 정책 (docs/08 견고성).

ai_planner 등 원격 호출이 일시적 장애(타임아웃·연결오류·5xx·429)에 한해서만
지수 백오프로 재시도하도록 정책을 한 곳에 모은다. 4xx(429 제외)는 클라이언트
오류이므로 즉시 실패한다(재시도해도 결과 동일).

순수 로직(should_retry_status / request_with_retry)은 send 콜러블과 sleep을
주입받아 네트워크 없이 테스트된다. httpx에 직접 의존하지 않는다(호출부가 주입).
"""
from __future__ import annotations

import time
from typing import Callable, Protocol

# 명시적 재시도 상태 + 그 외 5xx 일반화.
RETRY_STATUS = {429, 500, 502, 503, 504}


def should_retry_status(status: int) -> bool:
    """이 HTTP 상태코드가 재시도 가치가 있는 일시적 장애인가."""
    return status in RETRY_STATUS or 500 <= status < 600


class HttpError(Exception):
    """비재시도 HTTP 실패(4xx 또는 재시도 소진된 5xx)."""

    def __init__(self, status: int, body: str = "") -> None:
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


class _Resp(Protocol):
    status_code: int

    def json(self) -> dict: ...


def request_with_retry(
    send: Callable[[], _Resp],
    *,
    retries: int = 2,
    backoff: float = 0.5,
    retry_exceptions: tuple[type[BaseException], ...] = (),
    sleep: Callable[[float], None] = time.sleep,
) -> _Resp:
    """`send()`를 호출하고 일시적 장애 시 지수 백오프로 재시도한다.

    인자:
        send: 인자 없는 호출자. `.status_code`(int) 응답을 반환하거나
              `retry_exceptions`(예: httpx.TransportError)를 던진다.
        retries: 추가 재시도 횟수(총 시도 = retries + 1).
        backoff: 1차 대기(초). n번째 재시도 전 `backoff * 2**n`초 대기.
        retry_exceptions: 재시도 대상 예외 튜플.
        sleep: 대기 함수(테스트 주입용).

    반환: status_code < 400 인 첫 응답.
    예외: HttpError(비재시도 상태 또는 재시도 소진), 또는 재시도 소진된 send 예외.
    """
    last_exc: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            resp = send()
        except retry_exceptions as exc:
            last_exc = exc
            if attempt < retries:
                sleep(backoff * (2 ** attempt))
                continue
            raise
        status = getattr(resp, "status_code", 200)
        if status < 400:
            return resp
        if should_retry_status(status) and attempt < retries:
            sleep(backoff * (2 ** attempt))
            continue
        raise HttpError(status, getattr(resp, "text", ""))
    # 도달 불가: 루프는 반환/예외로 종료된다.
    if last_exc is not None:  # pragma: no cover - 방어
        raise last_exc
    raise RuntimeError("request_with_retry: unreachable")  # pragma: no cover
