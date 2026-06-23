# 다이어그램 (Mermaid → SVG)

루트 `README.md`에 임베드되는 다이어그램의 **소스(.mmd)**와 **렌더 결과(.svg)**.
브랜드 컬러(민트 `#3FD3B0` · 코랄 `#FF7A5C` · 성공 `#2ea44f`)를 `theme.json`과
각 `.mmd`의 `classDef`로 입혀 그룹별로 색을 구분한다.

| 소스 | 결과 | 내용 |
|---|---|---|
| `system-logic.mmd` | `system-logic.svg` | 데이터 흐름 (소스→쓰기→DB→읽기·엣지) |
| `ci-workflow.mmd` | `ci-workflow.svg` | CI 워크플로우 (ingest.yml) |
| `module-graph.mmd` | `module-graph.svg` | 모듈 그래프 (scripts/**) |

## 재생성

```bash
# Chrome 경로는 환경에 맞게 지정 (puppeteer가 헤드리스 브라우저로 렌더)
cat > /tmp/pptr.json <<'JSON'
{ "executablePath": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "args": ["--no-sandbox"] }
JSON

for f in system-logic ci-workflow module-graph; do
  mmdc -i "$f.mmd" -o "$f.svg" -c theme.json -b transparent -p /tmp/pptr.json
done
```

> `theme.json`은 `htmlLabels:false`로 둔다 — GitHub은 `<img>`로 임베드된 SVG의
> `<foreignObject>`(HTML 라벨)를 렌더하지 않으므로, 네이티브 `<text>` 라벨이라야
> 글자가 보인다. 폭은 `useMaxWidth:true` + README의 `width="100%"`로 페이지에 맞춘다.
