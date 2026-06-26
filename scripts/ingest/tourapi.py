"""한국관광공사 TourAPI 4.0 searchFestival 어댑터. 지역축제 JSON → OKF Event."""
from __future__ import annotations

import datetime as dt
import json

from scripts.common.config import canonical_sido
from scripts.common.dates import combine_kst
from scripts.common.okf import content_hash
from scripts.ingest.base import SourceAdapter, now_kst


def _kst_date(value: str, end: bool = False) -> str | None:
    """'20260718' → ISO 8601(KST)."""
    value = (value or "").strip()
    try:
        d = dt.datetime.strptime(value, "%Y%m%d")
    except ValueError:
        return None
    return combine_kst(d, end)


def _num(value) -> float | None:
    try:
        f = float(value)
        return f if f else None
    except (TypeError, ValueError):
        return None


class TourApiAdapter(SourceAdapter):
    key = "tourapi"
    fmt = "json"

    def _parse_json(self, text: str) -> list[dict]:
        data = json.loads(text)
        items = (
            data.get("response", {})
            .get("body", {})
            .get("items", {})
            .get("item", [])
        )
        if isinstance(items, dict):  # 단건 응답
            items = [items]
        return items

    def map_to_okf(self, n: dict) -> dict | None:
        cid = n.get("contentid")
        name = n.get("title")
        start = _kst_date(n.get("eventstartdate", ""))
        if not cid or not name or not start:
            return None
        addr = (n.get("addr1") or "").strip()
        sido = canonical_sido(addr.split()[0]) if addr else None
        lat, lng = _num(n.get("mapy")), _num(n.get("mapx"))
        event = {
            "id": f"tourapi:{cid}",
            "name": name,
            "event_type": "Festival",
            "themes": ["축제"],
            "start_date": start,
            "status": "Scheduled",
            "attendance_mode": "Offline",
            "location": {
                "name": n.get("title"),
                "sido": sido,
                "address": addr or None,
            },
            "url": f"https://korean.visitkorea.or.kr/detail/ms_detail.do?cotid={cid}",
            "source": self.key,
            "source_url": f"{self.cfg.get('base_url','')}/detailCommon2?contentId={cid}",
            "fetched_at": now_kst(),
        }
        end = _kst_date(n.get("eventenddate", ""), end=True)
        if end:
            event["end_date"] = end
        if lat and lng:
            event["location"]["lat"] = lat
            event["location"]["lng"] = lng
        if n.get("firstimage"):
            event["image"] = n["firstimage"]
        event["content_hash"] = content_hash(event)
        return event


if __name__ == "__main__":
    rows = TourApiAdapter().collect()
    print(f"tourapi: {len(rows)} OKF 레코드")
    for r in rows[:3]:
        print(" -", r["id"], r["name"], r["location"]["sido"])
