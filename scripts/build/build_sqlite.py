"""지식 DB(Markdown, SSOT) → 쿼리용 SQLite(libSQL 호환) 빌드.

flat-file 이 단일 진실원천(SSOT)이고, 이 SQLite는 **파생 인덱스**다(언제든 재생성).
정적 JSON(events.json)이 풀텍스트·범위·교차 필터에 약한 지점을 보완한다:
  - FTS5 전문 검색(name/description/themes)
  - sido/theme/status/event_type/start_date 인덱스
  - 엣지(Cloudflare D1 / Turso·libSQL)나 로컬 CLI에서 동일 스키마로 질의

산출: web/public/data/events.db  (Turso `turso db shell < dump.sql` 로도 이식 가능)

사용:
    python -m scripts.build.build_sqlite                 # 전체 빌드
    python -m scripts.build.build_sqlite --query 축제    # FTS 데모 질의
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from scripts.build.build_index import _flatten
from scripts.common.config import ROOT
from scripts.common.okf import iter_events

DB_PATH = ROOT / "web" / "public" / "data" / "events.db"

_SCHEMA = """
CREATE TABLE events (
    rowid       INTEGER PRIMARY KEY,
    id          TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    event_type  TEXT,
    themes      TEXT,          -- JSON 배열 문자열
    start_date  TEXT,
    end_date    TEXT,
    application_start TEXT,
    application_end   TEXT,
    status      TEXT,
    sido        TEXT,
    sigungu     TEXT,
    lat         REAL,
    lng         REAL,
    age         TEXT,
    age_bands   TEXT,          -- JSON 배열 문자열
    price       TEXT,
    organizer   TEXT,
    url         TEXT,
    image       TEXT,
    source      TEXT
);
CREATE INDEX idx_events_sido       ON events(sido);
CREATE INDEX idx_events_status     ON events(status);
CREATE INDEX idx_events_type       ON events(event_type);
CREATE INDEX idx_events_start      ON events(start_date);
CREATE VIRTUAL TABLE events_fts USING fts5(
    name, description, themes,
    content='events', content_rowid='rowid', tokenize='unicode61'
);
-- 한국어 부분일치 보강: 이름/설명/테마의 문자 N-gram(기본 bigram) 색인.
-- unicode61은 CJK를 공백 단위로만 토큰화해 "음악"으로 "음악축제"를 못 찾는다.
-- events_ngram(rowid 정렬 동기화)에 bigram 토큰을 적재해 부분일치 재현율을 높인다.
CREATE VIRTUAL TABLE events_ngram USING fts5(ngram, tokenize='unicode61');
"""

# flat-file SSOT 불변: ngram은 events 텍스트에서 빌드타임에 파생(재생성 가능).
_NGRAM_N = 2


def _ngrams(text: str, n: int = _NGRAM_N) -> list[str]:
    """공백으로 나눈 각 어절의 슬라이딩 문자 N-gram 토큰. 어절이 n보다 짧으면 그대로."""
    out: list[str] = []
    for word in (text or "").split():
        if len(word) < n:
            out.append(word)
        else:
            out.extend(word[i:i + n] for i in range(len(word) - n + 1))
    return out


def _ngram_text(e: dict) -> str:
    """행사 1건의 검색 대상 텍스트(name+description+themes) → N-gram 토큰 문자열."""
    parts = [e.get("name") or "", e.get("description") or ""]
    parts.extend(e.get("themes") or [])
    return " ".join(_ngrams(" ".join(parts)))



def _has_fts5() -> bool:
    try:
        c = sqlite3.connect(":memory:")
        c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        c.close()
        return True
    except sqlite3.OperationalError:
        return False


def _row_values(e: dict) -> tuple:
    return (
        e.get("id"), e.get("name"), e.get("description"), e.get("event_type"),
        json.dumps(e.get("themes") or [], ensure_ascii=False),
        e.get("start_date"), e.get("end_date"),
        e.get("application_start"), e.get("application_end"),
        e.get("status"), e.get("sido"), e.get("sigungu"),
        e.get("lat"), e.get("lng"), e.get("age"),
        json.dumps(e.get("age_bands") or [], ensure_ascii=False),
        None if e.get("price") is None else str(e.get("price")),
        e.get("organizer"), e.get("url"), e.get("image"), e.get("source"),
    )


_COLUMNS = (
    "id,name,description,event_type,themes,start_date,end_date,"
    "application_start,application_end,status,sido,sigungu,lat,lng,age,"
    "age_bands,price,organizer,url,image,source"
)


def build(db_path: Path | str | None = None, events: list[dict] | None = None) -> dict:
    """Markdown(또는 주어진 평탄화 events) → SQLite. archived 제외."""
    if not _has_fts5():
        raise RuntimeError("이 sqlite3 빌드에 FTS5가 없습니다 — 검색 인덱스를 만들 수 없음")
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    if events is None:
        events = [
            _flatten(fm) for _p, fm, _b in iter_events()
            if fm.get("status") != "archived"
        ]

    con = sqlite3.connect(path)
    try:
        con.executescript(_SCHEMA)
        con.executemany(
            f"INSERT INTO events({_COLUMNS}) VALUES ({','.join('?' * 21)})",
            [_row_values(e) for e in events],
        )
        # FTS 채우기(content 테이블과 rowid 동기화)
        con.execute(
            "INSERT INTO events_fts(rowid, name, description, themes) "
            "SELECT rowid, name, COALESCE(description,''), themes FROM events"
        )
        # N-gram 색인 채우기(rowid 동기화) — 한국어 부분일치 보강.
        ng_rows = con.execute("SELECT rowid, name, description, themes FROM events").fetchall()
        con.executemany(
            "INSERT INTO events_ngram(rowid, ngram) VALUES (?, ?)",
            [
                (rid, _ngram_text({
                    "name": name, "description": desc,
                    "themes": json.loads(themes or "[]"),
                }))
                for rid, name, desc, themes in ng_rows
            ],
        )

        con.commit()
        n = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        con.close()
    print(f"sqlite: events.db ← {n}건 (FTS5 + sido/status/type/start 인덱스)")
    return {"path": str(path), "events": n}


def search(
    db_path: Path | str | None = None,
    *,
    text: str | None = None,
    sido: str | None = None,
    theme: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """FTS + 컬럼 필터 교차 질의. dict 리스트 반환."""
    path = Path(db_path) if db_path else DB_PATH
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        where: list[str] = []
        params: list = []
        if text:
            # unicode61 전문검색(어절 단위) ∪ N-gram 부분일치 → rowid 합집합.
            rowids: set[int] = set()
            try:
                rowids.update(
                    r[0] for r in con.execute(
                        "SELECT rowid FROM events_fts WHERE events_fts MATCH ?", (text,)
                    )
                )
            except sqlite3.OperationalError:
                pass  # FTS 특수문자 등으로 파싱 실패 시 N-gram만 사용
            ngram_query = " ".join(_ngrams(text))
            if ngram_query:
                try:
                    rowids.update(
                        r[0] for r in con.execute(
                            "SELECT rowid FROM events_ngram WHERE events_ngram MATCH ?",
                            (ngram_query,),
                        )
                    )
                except sqlite3.OperationalError:
                    pass
            if not rowids:
                return []
            placeholders = ",".join("?" * len(rowids))
            where.append(f"e.rowid IN ({placeholders})")
            params.extend(sorted(rowids))
        if sido:
            where.append("e.sido = ?")
            params.append(sido)
        if status:
            where.append("e.status = ?")
            params.append(status)
        if theme:
            where.append("e.themes LIKE ?")
            params.append(f'%"{theme}"%')
        sql = "SELECT e.* FROM events e"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY e.start_date LIMIT ?"
        params.append(limit)
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()

    out = []
    for r in rows:
        d = dict(r)
        d["themes"] = json.loads(d.get("themes") or "[]")
        d["age_bands"] = json.loads(d.get("age_bands") or "[]")
        out.append(d)
    return out


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--query":
        q = argv[1] if len(argv) > 1 else "축제"
        hits = search(text=q, limit=10)
        print(f"FTS '{q}': {len(hits)}건")
        for h in hits:
            print(" -", h["id"], h["name"], h["sido"])
        return 0
    build()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
