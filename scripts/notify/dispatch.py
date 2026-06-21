"""결과/마감/신규 알람 계산 + 디스패치 (docs/07).

순수 계산(compute_notifications)과 전송(dispatch)을 분리해 테스트 가능하게 한다.
중복 억제는 knowledge/notify/sent.json. 채널 토큰이 없으면 dry-run 출력.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

from scripts.common.config import ROOT

EVENTS_JSON = ROOT / "web" / "public" / "data" / "events.json"
SENT_PATH = ROOT / "knowledge" / "notify" / "sent.json"


def _date(s: str | None) -> str:
    return (s or "")[:10]


def _matches(event: dict, filters: dict) -> bool:
    if not filters:
        return True
    if filters.get("sido") and event.get("sido") != filters["sido"]:
        return False
    if filters.get("themes"):
        if not (set(filters["themes"]) & set(event.get("themes") or [])):
            return False
    if filters.get("age_band") and filters["age_band"] not in (event.get("age_bands") or []):
        return False
    return True


def compute_notifications(events: list[dict], subscriptions: list[dict],
                          now: dt.datetime, deadline_days=(1, 3)) -> list[dict]:
    """각 구독에 대해 발송 후보 알람 리스트(중복 억제 전)."""
    today = now.date()
    out = []
    for sub in subscriptions:
        for e in events:
            if not _matches(e, sub.get("filters", {})):
                continue
            # 신청 오픈
            if e.get("application_start"):
                if _date(e["application_start"]) == today.isoformat() and e.get("status") == "Open":
                    out.append(_mk(sub, e, "open", "신청이 오픈되었습니다"))
            # 마감 임박
            if e.get("application_end"):
                end = dt.date.fromisoformat(_date(e["application_end"]))
                d_remain = (end - today).days
                if d_remain in deadline_days:
                    out.append(_mk(sub, e, f"deadline-D{d_remain}", f"신청 마감 D-{d_remain}"))
    return out


def _mk(sub: dict, e: dict, kind: str, headline: str) -> dict:
    key = f"{sub['id']}|{e['id']}|{kind}"
    return {
        "dedupe_key": hashlib.sha256(key.encode()).hexdigest()[:16],
        "subscription_id": sub["id"],
        "channel": sub.get("channel", "stdout"),
        "target": sub.get("target"),
        "subscription": sub.get("subscription"),  # web push 구독 객체(선택)
        "event_id": e["id"],
        "kind": kind,
        "title": f"[{headline}] {e['name']}",
        "body": f"{e.get('sido','')} · 신청마감 {_date(e.get('application_end'))} · {e.get('url','')}",
    }


def _load_sent() -> set[str]:
    if SENT_PATH.exists():
        return set(json.loads(SENT_PATH.read_text(encoding="utf-8")))
    return set()


def _save_sent(keys: set[str]) -> None:
    SENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SENT_PATH.write_text(json.dumps(sorted(keys), ensure_ascii=False, indent=2), encoding="utf-8")


def _telegram_payload(notif: dict) -> dict:
    """Telegram sendMessage 페이로드(전송 분리 → 구조 테스트 가능)."""
    return {"chat_id": notif.get("target"), "text": f"{notif['title']}\n{notif['body']}"}


def _webpush_payload(notif: dict) -> dict:
    """Web Push 페이로드(서비스워커가 표시할 알림 본문)."""
    return {
        "title": notif["title"],
        "body": notif["body"],
        "data": {"event_id": notif.get("event_id"), "kind": notif.get("kind")},
    }


def _send_telegram(notif: dict) -> bool:
    """토큰/타깃 없으면 dry-run(False) — 무오류. 있으면 실제 전송."""
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token or not notif.get("target"):
        return False
    try:  # pragma: no cover - 네트워크 경로
        import httpx
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=_telegram_payload(notif),
            timeout=10,
        )
        return True
    except Exception as exc:
        print(f"telegram 실패: {exc}", file=sys.stderr)
        return False


def _send_webpush(notif: dict, subscription: dict | None = None) -> bool:
    """VAPID 키/구독 없으면 dry-run(False) — 무오류. 있으면 실제 전송(무료 VAPID).

    pywebpush는 선택 의존: 미설치 시에도 dry-run으로 무오류 처리한다(C1).
    """
    vapid = os.environ.get("VAPID_PRIVATE_KEY")
    subscription = subscription or notif.get("subscription")
    if not vapid or not subscription:
        return False
    try:  # pragma: no cover - 네트워크/의존성 경로
        from pywebpush import webpush
        webpush(
            subscription_info=subscription,
            data=json.dumps(_webpush_payload(notif), ensure_ascii=False),
            vapid_private_key=vapid,
            vapid_claims={"sub": os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com")},
        )
        return True
    except Exception as exc:
        print(f"webpush 실패: {exc}", file=sys.stderr)
        return False


def dispatch(events: list[dict], subscriptions: list[dict], now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
    sent = _load_sent()
    candidates = compute_notifications(events, subscriptions, now)
    delivered, suppressed = 0, 0
    for n in candidates:
        if n["dedupe_key"] in sent:
            suppressed += 1
            continue
        if n["channel"] == "telegram":
            ok = _send_telegram(n)
        elif n["channel"] == "webpush":
            ok = _send_webpush(n)
        else:
            ok = False
        prefix = "SENT" if ok else "DRY "
        print(f"{prefix} → {n['channel']}: {n['title']}")
        sent.add(n["dedupe_key"])
        delivered += 1
    _save_sent(sent)
    return {"candidates": len(candidates), "delivered": delivered, "suppressed": suppressed}


def main() -> int:
    if not EVENTS_JSON.exists():
        print("events.json 없음 — build_index 먼저", file=sys.stderr)
        return 1
    events = json.loads(EVENTS_JSON.read_text(encoding="utf-8"))
    subs = [{"id": "demo-seoul-kids", "filters": {"sido": "서울특별시"}, "channel": "stdout"}]
    # 데모: 기준시각을 데모 행사 마감 D-1 로 맞춰 알람이 보이게 함
    now = dt.datetime(2026, 7, 9, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    print(dispatch(events, subs, now))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
