<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-06-25 23:02 KST | Updated: 2026-06-25 23:02 KST -->

# specs/

## Purpose
spec-stack 동결 번들 — 프로젝트의 **단일 진실원천(SSOT)**. survey(발견) → spec(쓰기) → seed(동결) → tasks(실행) → harness(검증)로 한 방향(`spec.md → seed.yaml`) 흐른다. 요구가 바뀌면 spec을 먼저 고치고 seed를 재동결한다.

## Key Files

| File | Description |
|------|-------------|
| `spec.md` | **문서 SSOT** — 기능·제약(C1–C7)·기계검증 AC(F1–F6) |
| `seed.yaml` | **실행 SSOT** — 도구 제약 + 성공기준 미러 + drift guards (완료 게이트) |
| `tasks.md` | team executor 작업 큐(T1–T6, DONE/TODO) |
| `cli-harness.md` | 산출물 증거 계약(exit code 아닌 artifact 검증) |
| `README.md` | 번들 개요·실행 결과 |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `verify/` | 검증 하니스/증거 |

## For AI Agents

### Working In This Directory
- **방향은 단방향**: spec.md가 쓰고, seed.yaml이 동결·게이트한다. 두 파이프라인을 병렬 SSOT로 돌리지 않는다.
- 명시되지 않은 도구 사용 = 측정 가능한 드리프트. seed.yaml `tools:`에 허가된 하니스만 사용.
- 제약/AC 변경은 반드시 spec.md → seed.yaml 순으로 재동결.

### Testing Requirements
`seed.yaml`의 success_criteria가 게이트. evaluate 규칙: "verify artifacts, not exit codes".

### Common Patterns
frontmatter에 spec_version·source_of_truth. 발견 근거는 `../.survey/`.

## Dependencies

### Internal
`../scripts/`(구현 대상), `../tests/`(AC 미러), `../docs/`(배경).

### External
`../.survey/free-saas-websearch-ai-stack-2026/`(발견 landscape).

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
