"""KOPIS(공연예술통합전산망) 어댑터. 공연/전시 목록 XML → OKF Event."""
from __future__ import annotations

import datetime as dt

from scripts.common.config import canonical_sido
from scripts.common.dates import combine_kst
from scripts.common.okf import content_hash
from scripts.ingest.base import SourceAdapter, now_kst

_GENRE_THEME = {
    "연극": ("공연", "Event"),
    "뮤지컬": ("공연", "MusicEvent"),
    "서양음악(클래식)": ("공연", "MusicEvent"),
    "한국음악(국악)": ("공연", "MusicEvent"),
    "대중음악": ("공연", "MusicEvent"),
    "무용": ("공연", "Event"),
    "대중무용": ("공연", "Event"),
    "서커스/마술": ("공연", "Event"),
    "복합": ("공연", "Event"),
    "전시": ("전시", "ExhibitionEvent"),
}

_STATE = {"공연중": "Scheduled", "공연예정": "Scheduled", "공연완료": "archived"}


def _kst_date(value: str, end: bool = False) -> str | None:
    """'2026.07.18' → ISO 8601(KST). end=True면 23:59:59."""
    value = (value or "").replace("-", ".").strip()
    try:
        d = dt.datetime.strptime(value, "%Y.%m.%d")
    except ValueError:
        return None
    return combine_kst(d, end)


class KopisAdapter(SourceAdapter):
    key = "kopis"
    fmt = "xml"

    def map_to_okf(self, n: dict) -> dict | None:
        mt20id = n.get("mt20id")
        name = n.get("prfnm")
        if not mt20id or not name:
            return None
        genre = (n.get("genrenm") or "").strip()
        theme, event_type = _GENRE_THEME.get(genre, ("공연", "Event"))
        start = _kst_date(n.get("prfpdfrom", ""))
        end = _kst_date(n.get("prfpdto", ""), end=True)
        if not start:
            return None
        event = {
            "id": f"kopis:{mt20id}",
            "name": name,
            "event_type": event_type,
            "themes": [theme],
            "start_date": start,
            "status": _STATE.get((n.get("prfstate") or "").strip(), "Scheduled"),
            "attendance_mode": "Offline",
            "location": {
                "name": n.get("fcltynm") or None,
                "sido": canonical_sido(n.get("area")),
            },
            "url": f"https://www.kopis.or.kr/por/db/pblprfr/pblprfrView.do?mt20id={mt20id}",
            "source": self.key,
            "source_url": f"{self.cfg.get('base_url','')}/pblprfr/{mt20id}",
            "fetched_at": now_kst(),
        }
        if end:
            event["end_date"] = end
        if n.get("poster"):
            event["image"] = n["poster"]
        event["content_hash"] = content_hash(event)
        return event


if __name__ == "__main__":
    rows = KopisAdapter().collect()
    print(f"kopis: {len(rows)} OKF 레코드")
    for r in rows[:3]:
        print(" -", r["id"], r["name"], r["location"]["sido"])
