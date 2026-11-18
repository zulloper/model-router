# Model Router (Hermes 플러그인) 0.4.1

막힌 답을 위한 **티어 사다리**입니다. 평소에는 프로필 기본 모델이 그대로 답합니다. 답이 막히면 사다리의
모델들에게 같은 요청을 대신 묻습니다. 막혔다는 건 가드레일, 거절, 요청한 코드가 빠진 답, 모델 오류를
말합니다. 1티어 모델들에게 동시에 먼저 묻고, 안 되면 2티어로 갑니다. 티어마다 원문으로 한 번, 정보를
보강해서 한 번 묻습니다. 정상 답이 여럿이면 하나로 합쳐 원래 답 자리에 넣습니다. 자주 이기는 모델은 사다리
위로 올라갑니다.

설계 근거와 결정 기록은 [DESIGN.md](DESIGN.md), 진행 회고는 [RETRO.md](RETRO.md)에 있습니다.

## 목차

1. 무엇을 하는가
2. 요구사항과 설치
3. 빠른 시작
4. 동작 방식
5. 보안 작업에서
6. 설정 레퍼런스
7. 명령과 화면
8. 도구
9. 0.3.x에서 옮기기
10. 문제 해결
11. 한계와 주의
12. 개발

## 1. 무엇을 하는가

- **평소에는 손대지 않습니다.** 프로필 모델의 요청을 바꾸지 않습니다(0.3.x와 다른 점). 답이 막혔을 때만 개입합니다.
- **사다리 = 허용 목록.** 사다리에 넣은 모델 말고는 부르지 않습니다.
- **티어 안은 병렬, 티어끼리는 순서대로.** 한 티어의 모델들에게 동시에 묻고, 모두 실패하면 다음 티어로 갑니다.
- **원문 → 보강.** 원문 요청으로 티어가 모두 실패하면, 같은 티어에 보강한 요청으로 한 번 더 묻습니다. 보강 내용은 사용자 ROE, 코드 요청일 때의 산출물 요구, 지켜야 할 사실입니다(§5).
- **종합.** 한 티어에서 정상 답이 둘 이상 나오면 한 번 더 호출해 하나로 합칩니다.
- **자동 승격.** 시도 기록에서 꾸준히 이기는 모델은 한 칸씩 위 티어로 올라갑니다. 사용자 설정은 고치지 않습니다.
- **기록.** 모든 시도(보낸 프롬프트 포함)를 기록해 어떤 답이 왜 나갔는지 되짚을 수 있습니다.

## 2. 요구사항과 설치

- Hermes `>= 0.22.5`
- 플러그인 권한 `llm.model_override`, `llm.provider_override`(재시도와 종합을 사다리 모델로 보내는 데 필요)
- 권장: `plugins.hook_callback_timeout: 300` (아래 참고)

```bash
ln -sfn /path/to/model-router ~/.hermes/plugins/model-router
hermes plugins enable model-router        # 일반 터미널에서: 권한 승인을 묻습니다
```

- **권한 승인은 대화형 터미널에서만 됩니다.** IDE나 에이전트의 셸처럼 TTY가 아닌 곳에서는 자동으로 거부됩니다.
  `hermes plugins capabilities model-router`로 상태를 확인하세요.
- **시간 제한을 올리세요.** 답 대체는 `transform_llm_output` 훅 안에서 일어나고, 이 훅은
  `plugins.hook_callback_timeout`(기본 30초) 안에 끝나야 합니다. 사다리는 이 제한보다 4초 먼저 스스로
  멈추지만, 30초로는 짧은 답 1티어 정도가 한계입니다. 실측에서 긴 보안 답(검토 + 코드)은 1티어 재시도에 약
  95초, 종합에 약 60초가 걸렸습니다.
  ```yaml
  plugins:
    hook_callback_timeout: 300
  ```
- **링크 설치의 동작.** `~/.hermes/plugins/model-router`가 이 폴더를 가리키면, 체크아웃된 코드가 곧 실행
  코드입니다. `hermes plugins enable`을 하면 Desktop 화면 사본(`~/.hermes/desktop-plugins/model-router/`)도
  Hermes가 다시 복사합니다.
- **업데이트 후에는 게이트웨이를 재시작하세요.** Hermes는 Desktop 백엔드(`dashboard/plugin_api.py`)를 처음
  한 번만 불러오고 계속 씁니다. `plugins enable/disable`로는 다시 불러오지 않습니다. 재시작하지 않으면
  Settings 화면에 "백엔드가 예전 버전입니다"가 뜹니다.
  ```bash
  hermes gateway restart
  ```

## 3. 빠른 시작

1. 사다리를 만듭니다. 방법은 세 가지입니다.
   - **Desktop:** Settings ▸ Plugins ▸ Model Router ▸ **Tiers**. 티어마다 [프로바이더] → [모델] → 추가.
     ↑↓로 순서를 바꾸고 **사다리 저장**을 누릅니다.
   - **터미널:** `hermes model-router setup`. 티어마다 프로바이더를 고르고 이어서 모델을 고릅니다. 프로바이더를
     하나도 안 고르면 끝납니다.
   - **config.yaml:**
     ```yaml
     plugins:
       entries:
         model-router:
           settings:
             tiers:
               - [openai-codex/gpt-6.2-sol, anthropic/claude-opus-4-8]
               - [xai/grok-5]
     ```
2. 넣을 수 있는 모델은 `hermes model-router models`로 확인합니다.
3. 사다리를 처음 저장한 뒤에는 `hermes plugins disable model-router && hermes plugins enable model-router`
   (또는 게이트웨이 재시작)를 한 번 합니다. 그래야 `hold_stream`(자동)이 켜집니다(§4).
4. 대화에서 `/route`로 사다리, 시간 예산, 경고를 확인합니다.

## 4. 동작 방식

```
프로필 모델의 답
   │  막혔나? (outcome.judge)  ── 아니면 그대로 전달
   ▼
1티어: 원문 요청을 모델들에게 동시에  ── 정상 답 1개 → 그 답으로 대체
   │                                  └ 정상 답 2개+ → 종합 호출 → 대체
   │ 모두 실패
1티어: 보강 요청을 동시에             ── (위와 같음, 표현 복원·사실 검사 후)
   │ 모두 실패
2티어: …                              ── 마지막 티어까지 실패하면 원래 답을 그대로 전달
```

**막힘 판정.** 시도마다 결과를 넷 중 하나로 매기고, 근거(오류 사유나 걸린 문구)를 기록합니다.

| 결과 | 뜻 |
|---|---|
| `ok` | 쓸 수 있는 답 |
| `blocked` | 프로바이더 정책 오류(Anthropic `stop_reason: refusal`, `category: cyber` 포함), 거절 문장 |
| `degraded` | 코드를 달라고 했는데 코드 없이 설명만 온 답. 보강 답에서 잠긴 사실이 빠진 경우 |
| `error` | 그 밖의 실패(모델 없음, 재시도 소진, 빈 답) |

- "proprietary", "NDA", "개념만 설명" 같은 보안식 돌려 말하기와 `degraded`는 **요청이 코드를 달라고 했을 때만**
  셉니다. 계약서 요약에 나온 "NDA"는 거절이 아닙니다.
- 턴이 API 오류로 끝났는지는 `post_api_request`/`api_request_error` 훅으로 추적합니다.

**재시도는 기본적으로 도구 없이** `ctx.llm.complete`로 합니다. 최근 대화 6개와 요청을 넘깁니다. 대체되는 건
막힌 답 하나이고, 대화는 원래 모델로 이어집니다.

**서브에이전트 재시도(선택).** `subagent_retry: true`면, 막힌 턴에서 원래 모델이 도구를 썼을 때만 직접 호출
대신 Hermes 서브에이전트로 재시도합니다. 서브에이전트는 요청을 목표로, 최근 대화를 문맥으로 받습니다.
- 쓸 수 있는 도구는 `subagent_toolsets`로 정합니다: `web`(검색·페이지 읽기만, 기본) / `web+file`(파일 쓰기·
  수정 포함) / `inherit`(세션의 모든 도구, 터미널 포함).
- **서브에이전트는 사용자에게 묻지 않고 실행됩니다.** `inherit`면 거절된 요청을 터미널로 실행할 수 있고,
  Hermes 명령 승인(`approvals.mode`)이 꺼져 있으면 승인 절차도 없습니다.
- 느립니다(매번 에이전트 전체 실행, 실측 5~8초~수십 초). 재시도마다 자식 세션이 생깁니다. 사다리 마감 시각에
  남은 서브에이전트는 취소합니다. Hermes는 서브에이전트 답을 32,000자에서 자르고, 잘리면 답 끝에 표시합니다.
- 종합은 그대로 직접 호출입니다. 서브에이전트의 추론 강도는 `delegation.reasoning_effort`를 따릅니다.

**꼬리말.** 대체된 답에는 무엇이 일어났는지 붙습니다.
```
— model-router: openai-codex/gpt-6.1-sol-900k was blocked (refusal: I can't help);
  anthropic/claude-opus-4-8 answered instead (tier 1, enriched request, without tools).
```

**시간 예산.** 사다리 전체가 `hook_callback_timeout - 4초`(또는 `retry_budget_seconds`) 안에 끝납니다.
- 남은 시간이 5초 미만이면 새 호출을 시작하지 않습니다.
- 멈춘 호출은 마감 시각에 버립니다.
- 종합이 뒤따를 수 있는 병렬 단계는 남은 시간의 70%만 씁니다.

예산을 넘기지 않는 건 필수입니다. 훅이 Hermes 제한에 걸리면 그 답이 "withheld" 문구로 바뀌고, 훅이
**60초 동안 모든 세션에서** 건너뛰어집니다.

**hold_stream.**
- 프로바이더 오류로 끝난 턴은 답을 보류(hold)할 때만 대체할 수 있습니다.
- 기본값 `auto`는 사다리가 있으면 켭니다. 켜면 스트리밍이 꺼집니다(답이 끝난 뒤 한 번에 보임).
- 등록 시점에 정해지므로, 바꾼 뒤에는 플러그인을 다시 켜거나 게이트웨이를 재시작합니다.

**자동 승격.** 근거는 원문 재시도(`plain`) 기록만 씁니다. 시간 예산 때문에 버린 호출은 뺍니다.

| 항목 | 값 |
|---|---|
| 관찰 범위 | 모델별 최근 20회, 최소 8회 |
| 올리기 | 성공률 ≥ max(60%, 바로 위 티어 평균 + 15%p) → 한 칸 위. 위 티어에 판단 가능한 모델이 없으면 ≥ 75% |
| 내리기 | 올렸던 모델이 40% 미만 → 한 칸 되돌림. 사용자가 넣은 티어보다 아래로는 안 내림 |

승격은 `config.yaml`이 아니라 플러그인 상태에 저장됩니다. `/route`에서 `↑1`처럼 보이고, `/route reset`으로 지웁니다.

**시도 기록.**
- 위치: `~/.hermes/plugin-data/agent-plugin-model-router-*/attempts.jsonl` (프로필별)
- 내용: 원래 답, 원문·보강 재시도, 종합 호출을 한 줄씩. 보낸 프롬프트는 레코드당 16,000자까지 남습니다.
- 크기: 5MB마다 회전하고 백업 3개만 남깁니다.
- 프롬프트에는 사용자 코드나 호스트명이 들어갈 수 있습니다.

## 5. 보안 작업에서

보강 재시도는 **정보를 더합니다. 숨기지 않습니다.** 보강 요청은 원래 요청 바로 뒤에 이렇게 붙습니다.

```
<프레이밍만 순화한 원래 요청>

Context from the user's rules of engagement: <security_roe 앞 600자>     ← 설정했을 때만

Requirements for the answer:
- Give complete, runnable code: one file with its imports, ...           ← 코드 요청일 때만
- It must work with ASLR enabled.                                        ← 의미 델타
- Keep these exactly as written: CVE-2024-3094, 5.6.1.                   ← 잠긴 사실
```

- **권한은 사용자 ROE에서만 옵니다.** `security_roe`가 비어 있으면 권한에 대한 말은 전혀 넣지 않습니다.
  ROE에는 대상, 기간, 범위, 승인자를 구체적으로 쓰세요.
- **프레이밍 순화**는 산문에만 적용합니다(`exploit` → `proof-of-concept code` 등). 코드 블록, 식별자, CVE
  번호, 버전, 오프셋, 해시, 심볼은 그대로 둡니다. 답에서는 원래 용어로 되돌립니다.
- **의미 델타**: 순화하면서 흐려지는 요구를 요구사항으로 다시 적습니다. 예를 들어 "bypass ASLR"을 "work
  around address randomization"으로 바꾸면 "ASLR이 켜진 상태에서 동작해야 한다"를 덧붙입니다.
- **사실 검사**: 보강 답에서 잠긴 사실이 빠지면 `degraded`로 보고 다음 시도로 넘어갑니다.
- 난독화나 탐지 회피는 하지 않습니다. 이유는 [DESIGN.md](DESIGN.md) §5·§8에 있습니다. **프로바이더의
  가드레일은 그대로입니다.** 정책이 막으면 다른 모델로 넘어가는 것뿐입니다.
- `cve_lookup`으로 NVD 기록을 먼저 확보하고, 답에 나온 버전·오프셋은 소스나 어드바이저리로 다시 확인하세요.

## 6. 설정 레퍼런스

모두 `plugins.entries.model-router.settings` 아래에 있습니다. Desktop General 화면에서도 바꿀 수 있습니다.

| 키 | 기본 | 설명 |
|---|---|---|
| `mode` | `auto` | `auto`: 막힌 답에 사다리 사용. `off`: 그대로 둠 |
| `tiers` | `[]` | 티어 목록. 각 티어는 `provider/model` 목록(`@effort`는 무시하고 경고). 한 모델은 한 티어에만. 4개 넘는 티어는 경고 |
| `parallel` | `true` | 끄면 티어 안을 차례로 묻고 첫 정상 답에서 멈춤 |
| `synthesize` | `true` | 한 티어에서 정상 답 2개 이상이면 종합 |
| `enrich_on_retry` | `true` | 티어마다 보강 재시도. 보탤 것이 없으면 건너뜀 |
| `auto_promote` | `true` | 자동 승격. 끄면 저장된 승격도 무시하고 설정 순서대로 |
| `security_roe` | `""` | 교전 규칙. 보강 재시도에 인용(앞 600자) |
| `hold_stream` | `auto` | `auto`/`on`/`off`. 0.3.x의 true/false도 읽음. 등록 시점에 적용 |
| `retry_budget_seconds` | `auto` | 사다리 시간 예산(초). `hook_callback_timeout - 4`를 넘지 않음 |
| `security_guardrail_defaults` | `true` | 프로바이더 오류의 "content filter / safety / cyber" 등을 가드레일 차단으로 분류 |
| `extra_guardrail_patterns` | `[]` | 가드레일로 볼 추가 오류 문구(소문자) |
| `max_explore` | `3` | `route_explore`가 한 번에 비교할 모델 수(1~8) |
| `subagent_retry` | `false` | 도구를 쓴 턴이 막히면 서브에이전트로 재시도(§4) |
| `subagent_toolsets` | `web` | 재시도 서브에이전트의 도구: `web` / `web+file` / `inherit`(터미널 포함, 확인 없이 실행) |

## 7. 명령과 화면

```
/route            사다리(승격 포함), 재시도 옵션, hold_stream, 시간 예산, 경고
/route auto|off   켜기/끄기
/route stats      모델별 최근 결과(원문 | 보강), 종합 횟수, 마지막 실패
/route reset      학습된 승격 지우기(시도 기록은 남김)

hermes model-router setup    티어 마법사(프로바이더 → 모델)
hermes model-router show     사다리와 시간 예산
hermes model-router models   사다리에 넣을 수 있는 프로바이더와 모델
```

**Desktop:** Settings ▸ Plugins ▸ Model Router (English/한국어). 화면에 표시된 프로필에만 읽고 씁니다.

| 화면 | 내용 |
|---|---|
| General | 켜기/끄기, 병렬·종합·보강·승격, 서브에이전트 재시도와 도구 범위, hold_stream, 시간 예산(현재 제한과 권장값 표시), ROE, 가드레일, 비교 수, 경고 |
| Tiers | 티어 추가·삭제, [프로바이더] → [모델] 선택 또는 직접 입력, 순서 이동, 승격 표시 |
| Stats | 모델별 원문·보강 결과와 마지막 실패, 승격 초기화 |

## 8. 도구

**`route_explore`**: 같은 과제를 사다리의 모델 여럿에 서브에이전트로 동시에 맡기고 채점합니다. 결과는 아무것도
저장하지 않습니다. 티어 순서를 정하는 참고 자료로 쓰세요.

| 파라미터 | 설명 |
|---|---|
| `task` (필수) | 모든 모델에 줄 자기완결적 과제 |
| `models` | `provider/model` 목록(사다리에 있어야 함). 기본은 티어 순서대로 `max_explore`개 |
| `timeout_seconds` | 전체 제한(기본 600). 넘으면 서브에이전트를 취소 |

서브에이전트는 `delegation.reasoning_effort`로 돕니다. 모델별 effort는 정할 수 없습니다.

**`cve_lookup`**: NVD 2.0 조회입니다. `cve_id` 또는 `keyword`가 필요하고, `limit`은 1~20입니다.
- 결과: 설명, CVSS v4.0/v3.1, CWE, 영향 CPE 버전 범위, 참조
- 15분 동안 캐시합니다. 익명 사용 한도에 걸리면 403/429를 돌려줍니다. `NVD_API_KEY`를 설정하면 한도가 완화됩니다.

## 9. 0.3.x에서 옮기기

0.4.0은 0.3.x의 라우팅을 없앴습니다. 작업 종류 분류, 허용 목록과 작업별 라우트, 요청마다 모델과 effort를
바꾸던 처리, 세션 프로바이더 이동, 보안 프리앰블, 순화 L1~L3이 모두 빠졌습니다. 남아 있는 0.3.x 키는
무시하지 않고 `/route`와 Desktop에 경고로 표시합니다.

| 0.3.x | 0.4.0 |
|---|---|
| `scope` + `routes` | `tiers`. 자동 변환하지 않습니다(아래) |
| `classifier`, `/route sec` | 없음. 보안식 감지는 항상 켜져 있고, 코드 요청 여부로 거릅니다 |
| `cross_provider` | 없음. 다른 프로바이더 모델은 재시도로만 불립니다 |
| `soft_refusal_retry`, `degraded_retry` | 사다리 자체가 같은 일을 합니다 |
| `security_preamble*`, `security_sanitize*`, `security_fact_lock` | 보강 재시도(§5). 사실 검사는 항상 켜져 있습니다 |
| `security_roe` | 그대로 씁니다(보강 재시도에 인용) |
| `cooldown_minutes`, health 통계 | 시도 기록과 자동 승격 |
| `/route pin`, `/route prefer` | 티어 순서를 직접 정하세요 |
| `hold_stream: true/false` | 그대로 읽습니다. 새 값은 `auto`/`on`/`off` |

**옮기는 순서.**
1. 0.3.x에서 가장 믿던 모델들을 1티어에, 나머지 허용 모델을 2티어에 넣습니다.
2. `scope`, `routes` 등 경고에 나온 키를 지웁니다.
3. `hook_callback_timeout`을 올리고 게이트웨이를 재시작합니다.

자동 변환을 하지 않는 이유가 있습니다. 0.3.x는 작업 종류마다 순서가 달랐는데, 이를 사다리 하나로 기계적으로
합치면 의도가 바뀝니다.

## 10. 문제 해결

**Settings 화면에 "백엔드가 예전 버전입니다"가 나옵니다.**
업데이트 후 게이트웨이를 재시작하지 않은 경우입니다. `hermes gateway restart` 후 페이지를 다시 여세요.

**막힌 답이 대체되지 않습니다.**
1. `/route`로 `mode: auto`와 사다리를 확인합니다.
2. API 오류로 끝난 턴이라면 `hold_stream`이 켜져 있어야 합니다. 사다리를 처음 저장했다면 플러그인을 다시 켜세요.
3. `/route stats`에서 사다리 모델들의 실패 이유를 봅니다. 예: 모델 id가 그 프로바이더에서 지원되지 않음
   (`model_not_found`), 인증 문제, 시간 예산 초과.
4. 시간 예산이 26초(기본)라면 `hook_callback_timeout`을 올리세요.

**답이 "This reply was withheld…"로 바뀝니다.**
훅이 Hermes 시간 제한을 넘겼거나 예외가 난 경우입니다(hold 상태). 사다리는 스스로 예산 안에서 멈추도록
돼 있으니 로그(`~/.hermes/logs/gateway.error.log`)를 확인하고 이슈로 알려 주세요. 그동안은
`hold_stream: off`로 둘 수 있습니다.

**재시도가 같은 모델에서 계속 실패합니다.**
설정의 별칭 id(예: `-900k`)와 실제 요청 id가 다를 수 있습니다. `hermes model-router models`에 나온 id를 쓰고,
Stats의 실패 이유를 보세요.

**사다리 모델 목록에 원하는 프로바이더가 없습니다.**
`hermes model-router models`에 나오는지 확인합니다. 없으면 `hermes model`로 자격 증명을 추가하세요. 직접
`provider/model`로 입력해도 됩니다.

**권한 승인이 안 됩니다.**
일반 터미널 앱에서 `hermes plugins enable model-router`를 실행하세요.

## 11. 한계와 주의

- 프로바이더 가드레일을 제거하지 않습니다. 다른 모델에 다시 묻는 것뿐입니다.
- 재시도는 막힌 답 하나만 대체합니다. 서브에이전트 재시도를 켜지 않으면 도구 없이 이루어집니다.
- 사실 검사는 빠진 것만 잡습니다. 틀린 값은 잡지 못합니다.
- 재시도마다 비용이 듭니다. 티어당 모델 수만큼 병렬 호출이 나가고, 보강과 종합까지 더해집니다. 비용 상한은
  없으니 티어 크기와 `/route stats`로 직접 조절하세요.
- 승격 권장값은 기록이 쌓이면 다시 조정할 예정입니다(DESIGN §18).
- 서브에이전트 재시도는 사용자 확인 없이 실행됩니다. 도구 범위를 넓힐수록(특히 `inherit`) 위험도 커집니다.

## 12. 개발

```bash
PYTHONPATH=~/.hermes/hermes-agent uv run --no-project \
  --with pytest --with pyyaml --with fastapi --with httpx --with ruamel.yaml \
  python -m pytest
```

Desktop 백엔드 테스트에는 `fastapi`, `httpx`, `ruamel.yaml`이 필요합니다. 빠지면 조용히 건너뛰니 "skipped"가
보이면 이유를 확인하세요.

구성:

| 파일 | 역할 |
|---|---|
| `hooks.py` | 훅 연결 |
| `ladder.py` | 사다리 실행: 시간 예산, 병렬, 종합 |
| `outcome.py` | 막힘 판정, API 신호 |
| `enrich.py` | 보강 요청 |
| `sanitize.py` | 순화 사전, 복원, 사실 |
| `refusal.py` | 거절, 코드 요청, 가드레일 문구 |
| `promote.py` | 승격 |
| `attempts.py` | 시도 기록 |
| `tiers.py` | 티어 파싱 |
| `settings.py` | 설정 |
| `report.py` | `/route`·CLI·Desktop 공용 설명 |
| `slash.py` | `/route` |
| `cli.py` | `hermes model-router` |
| `explore.py` | `route_explore` |
| `nvd.py` | `cve_lookup` |
| `dashboard/plugin_api.py`, `desktop/plugin.js` | Desktop 백엔드와 화면 |
| `spikes/hook_spike/` | 설계 단계 측정용 임시 플러그인(DESIGN §13) |

### 변경 기록

- **0.4.1**:
  - 도구를 쓴 턴의 서브에이전트 재시도(선택, 기본 꺼짐·웹 도구만): `subagent_retry`, `subagent_toolsets`.
  - 재시도 중 프로바이더 가드레일 오류를 `blocked`로 기록(가드레일 문구 판정을 재시도에도 적용).
  - 종합 답이 "여러 답이 있었다"는 사실을 드러내지 않게 함.
  - 권장 `hook_callback_timeout`을 300초로.
- **0.4.0**: 티어 사다리로 재설계.
  - 원문·보강 재시도, 티어 안 병렬과 종합, 자동 승격, 시도 기록 추가.
  - 막힘 판정 4분류, 시간 예산과 예외 금지, hold_stream `auto`.
  - Desktop을 General / Tiers / Stats로 개편, `hermes model-router models` 추가.
  - 0.3.x 라우팅 제거.
  - `route_explore` 수정: 실행이 Hermes에 항상 거부되던 문제.
- **0.3.0**: `sec` 작업 종류, 보안 프리앰블·ROE, 거절·불완전 답 감지, 가드레일 왕복, `cve_lookup`.
- **0.2.0**: 허용 목록 라우팅, 실패 시 전환, 소프트 거절 재답변, 비교 학습, Desktop 화면.
