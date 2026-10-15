"""API 키 인입 상태 점검 + 경계 live probe (aside 키 handoff용).

- 기본 실행: 키 보유 여부만 이름 단위로 보고 (값은 절대 출력하지 않음).
- `--probe`: 보유 키마다 1회·소량(bounded) live 요청으로 자격 증명 유효성만
  확인. 응답 본문·키는 출력하지 않고 상태/건수만 보고.
- 종료 코드: 0 = 점검 완료(키 없어도 0, fixture 모드 안내), 1 = 실행 오류.

사용:
    python -m scripts.ops.check_keys            # 보유 현황만
    python -m scripts.ops.check_keys --probe    # live 유효성까지 (네트워크 사용)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.parse

from scripts.common.env import load_dotenv

# (env 이름, 용도) — 값은 어디에도 기록/출력하지 않는다.
KEY_SOURCES = [
    ("KOPIS_KEY", "kopis: 공연/전시 live 수집"),
    ("TOURAPI_KEY", "tourapi: 축제 live 수집"),
    ("EXA_API_KEY", "websearch/exa: 행사 발견"),
    ("BRAVE_API_KEY", "websearch/brave: 행사 발견"),
    ("TAVILY_API_KEY", "websearch/tavily: 행사 발견"),
]

_TIMEOUT = 15.0


def _present(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


def _sanitize(text: str) -> str:
    """응답 텍스트에서 알려진 키 문자열이 섞여 있으면 마스킹."""
    out = text
    for name, _ in KEY_SOURCES:
        secret = os.environ.get(name, "").strip()
        if secret and secret in out:
            out = out.replace(secret, "***")
    return out[:200]


def _probe_kopis() -> tuple[bool, str]:
    import httpx

    today = dt.date.today()
    params = {
        "service": os.environ["KOPIS_KEY"].strip(),
        "stdate": today.strftime("%Y%m%d"),
        "eddate": (today + dt.timedelta(days=7)).strftime("%Y%m%d"),
        "cpage": 1,
        "rows": 5,
    }
    url = "https://www.kopis.or.kr/openApi/restful/pblprfr?" + urllib.parse.urlencode(params)
    try:
        r = httpx.get(url, timeout=_TIMEOUT)
    except Exception as exc:
        return False, f"transport_{type(exc).__name__}"
    if r.status_code != 200:
        return False, f"http_{r.status_code}"
    body = _sanitize(r.text)
    if "openapi_serviceresponse" in body.lower() or "errormsg" in body.lower():
        return False, "provider_error(키 거부 또는 쿼터 초과 가능)"
    count = body.lower().count("<db>")
    return True, f"live OK, sample {count}건"


def _probe_tourapi() -> tuple[bool, str]:
    import httpx

    today = dt.date.today().strftime("%Y%m%d")
    params = {
        "serviceKey": os.environ["TOURAPI_KEY"].strip(),
        "eventStartDate": today,
        "numOfRows": 5,
        "pageNo": 1,
        "MobileOS": "ETC",
        "MobileApp": "notchima",
        "_type": "json",
    }
    url = "https://apis.data.go.kr/B551011/KorService2/searchFestival2?" + urllib.parse.urlencode(params)
    try:
        r = httpx.get(url, timeout=_TIMEOUT)
    except Exception as exc:
        return False, f"transport_{type(exc).__name__}"
    if r.status_code != 200:
        return False, f"http_{r.status_code}"
    try:
        payload = json.loads(r.text)
        header = payload.get("response", {}).get("header", {})
        code = str(header.get("resultCode", ""))
    except (ValueError, AttributeError):
        return False, "invalid_payload"
    if code != "0000":
        return False, f"provider_error(code={code})"
    try:
        items = payload["response"]["body"]["items"]["item"]
        count = len(items) if isinstance(items, list) else 1
    except (KeyError, TypeError):
        count = 0
    return True, f"live OK, sample {count}건"


def _probe_websearch() -> tuple[bool, str]:
    import httpx

    if _present("EXA_API_KEY"):
        try:
            r = httpx.post(
                "https://api.exa.ai/search",
                headers={"x-api-key": os.environ["EXA_API_KEY"].strip()},
                json={"query": "서울 축제", "numResults": 2},
                timeout=_TIMEOUT,
            )
            return (True, "live OK (exa)") if r.status_code == 200 else (False, f"exa_http_{r.status_code}")
        except Exception as exc:
            return False, f"exa_transport_{type(exc).__name__}"
    if _present("BRAVE_API_KEY"):
        try:
            r = httpx.get(
                "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({"q": "서울 축제", "count": 2}),
                headers={"X-Subscription-Token": os.environ["BRAVE_API_KEY"].strip()},
                timeout=_TIMEOUT,
            )
            return (True, "live OK (brave)") if r.status_code == 200 else (False, f"brave_http_{r.status_code}")
        except Exception as exc:
            return False, f"brave_transport_{type(exc).__name__}"
    if _present("TAVILY_API_KEY"):
        try:
            r = httpx.post(
                "https://api.tavily.com/search",
                json={"api_key": os.environ["TAVILY_API_KEY"].strip(), "query": "서울 축제", "max_results": 2},
                timeout=_TIMEOUT,
            )
            return (True, "live OK (tavily)") if r.status_code == 200 else (False, f"tavily_http_{r.status_code}")
        except Exception as exc:
            return False, f"tavily_transport_{type(exc).__name__}"
    return False, "no_websearch_key"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="API 키 보유 현황 + live probe (값 미출력)")
    parser.add_argument("--probe", action="store_true", help="보유 키 live 유효성 확인 (bounded 1회 요청)")
    args = parser.parse_args(argv)

    loaded = load_dotenv()
    print(f".env 로드: {len(loaded)}건 ({', '.join(loaded) if loaded else '없음 또는 미설정'})")

    live_ready = []
    for name, usage in KEY_SOURCES:
        mark = "보유" if _present(name) else "미보유"
        print(f" - {name}: {mark} ({usage})")
        if _present(name):
            live_ready.append(name)

    if not live_ready:
        print("결과: live 키 없음 → 파이프라인은 fixture 모드로 동작합니다.")
        return 0

    if not args.probe:
        print("결과: live 키 보유. `--probe`로 유효성을 확인할 수 있습니다.")
        return 0

    print("live probe (출처별 1회 소량 요청):")
    probes: list[tuple[str, tuple[bool, str]]] = []
    if _present("KOPIS_KEY"):
        probes.append(("kopis", _probe_kopis()))
    if _present("TOURAPI_KEY"):
        probes.append(("tourapi", _probe_tourapi()))
    if _present("EXA_API_KEY") or _present("BRAVE_API_KEY") or _present("TAVILY_API_KEY"):
        probes.append(("websearch", _probe_websearch()))
    for source, (ok, note) in probes:
        print(f" - {source}: {'OK' if ok else 'FAIL'} ({note})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
