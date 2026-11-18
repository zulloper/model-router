# Model Router (Hermes 플러그인)

업무 지시마다 작업 종류를 분류해 **허용 목록 안에서만** 모델과 추론 강도를 고릅니다. 가드레일·거절·모델 오류로 멈추면 다음 허용 모델로 넘어가고, 여러 모델을 서브에이전트로 돌려 비교한 결과를 학습합니다. 보안 작업(CVE 분석·익스플로잇·PoC·리버싱·레드팀)은 전용 `sec` 분류, 권한/출력 계약 프리앰블, 가드레일 왕복, NVD 조회로 처리합니다.

이 문서는 설치부터 보안 워크플로, 설정·명령 레퍼런스, 문제 해결까지 다룹니다.

## 목차

1. 무엇을 하는가
2. 요구사항과 설치
3. 빠른 시작
4. 보안 업무 가이드
5. 설정 레퍼런스
6. 채팅 명령
7. Desktop 화면
8. 동작 방식
9. 도구 레퍼런스
10. 문제 해결
11. 한계와 주의
12. 공개 API와 개발

## 무엇을 하는가

- **턴 단위 라우팅**: 요청을 quick / standard / code / deep / sec로 분류하고, 그 종류의 라우트에서 모델과 추론 강도를 고릅니다.
- **허용 목록 강제**: 프로바이더 최대 3개, 각 프로바이더에서 체크한 모델만 씁니다. 라우트·핀·학습된 선호·비교 후보가 목록 밖이면 무시합니다.
- **실패 시 전환**: 가드레일, 거절, 모델 없음, 권한 오류에서 다음 허용 모델로 넘어갑니다. 속도 제한 같은 일시 오류에서는 모델을 유지합니다.
- **소프트 거절·불완전 답변 재시도**: 짧은 거절 답변, 그리고 보안 턴에서 코드 없이 개념만 설명한 답변을 다른 허용 모델로 다시 시킵니다.
- **보안 특화**: `sec` 분류, 권한 가정·출력 계약 프리앰블, 거절 감지 확장, cyber refusal 매핑, NVD `cve_lookup`, 요청 순화/응답 복원 왕복.
- **모델 비교 학습**: `route_explore`가 같은 과제를 허용 모델들에 병렬로 시키고 채점해, 그 작업 종류의 선호 모델로 저장합니다.

## 요구사항과 설치

- Hermes `>= 0.22.5`
- 재답변·비교·순화 재시도에는 플러그인 권한 `llm.model_override`, `llm.provider_override` 동의가 필요합니다.
- `cve_lookup`은 `https://services.nvd.nist.gov`로 나가는 네트워크가 필요합니다. (선택) `NVD_API_KEY` 환경 변수를 두면 NVD 레이트리밋이 완화됩니다.
- 선택: 로컬 모델(Ollama 등)을 허용 목록 마지막 예비로 두면 제공자 가드레일이 전혀 없는 경로가 생깁니다.

이 디렉터리를 Hermes가 플러그인으로 읽는 위치에 두고 활성화합니다.

```bash
ln -sfn /path/to/model-router ~/.hermes/plugins/model-router
```

```yaml
plugins:
  enabled: [model-router]       # 또는 Desktop Capabilities → Plugins에서 켜기
```

기존에 같은 이름의 복사본이 있으면 symlink로 바꾸거나 이 디렉터리 내용으로 덮어쓰세요. Desktop 화면은 Hermes가 `~/.hermes/desktop-plugins/model-router/`로 복사해 불러오므로, `desktop/plugin.js`를 고친 뒤에는 복사본도 갱신돼야 합니다. 켠 뒤 대화에서 `/route`로 상태를 확인할 수 있습니다.

## 빠른 시작

1. 플러그인을 켜고 `hermes model-router setup`을 실행합니다.
2. 1단계에서 프로바이더(최대 3개), 2단계에서 모델을 체크합니다. 3단계에서는 작업 종류별로 **첫 모델과 추론 강도**를 고릅니다. 나머지 허용 모델은 자동으로 예비가 됩니다.
3. 대화에서 `/route`를 실행해 허용 목록과 작업 종류별 순서를 확인합니다.
4. `/route auto`로 라우팅을 켜고, 보안 작업이면 `/route sec on`을 실행합니다.

`hermes model-router show`는 어디서든 현재 설정을 출력합니다. 설정 파일은 `config.yaml`의 `plugins.entries.model-router.settings`입니다.

```yaml
plugins:
  entries:
    model-router:
      settings:
        mode: auto                 # auto | off
        scope:                     # 허용 목록. 여기 없는 모델은 절대 선택되지 않음
          openai-codex: [gpt-6.2-sol]
          anthropic: [claude-opus-4-8]
        routes:                    # 앞에서부터 시도. 나머지 허용 모델은 자동으로 예비 모델이 됨
          quick:    [openai-codex/gpt-6.2-sol@low]
          standard: [openai-codex/gpt-6.2-sol@medium]
          code:     [openai-codex/gpt-6.2-sol@high]
          deep:     [anthropic/claude-opus-4-8@high, openai-codex/gpt-6.2-sol@xhigh]
          sec:      [anthropic/claude-opus-4-8@xhigh, openai-codex/gpt-6.2-sol@xhigh]
```

`routes`의 각 항목은 `provider/model@effort`입니다. 작업 종류의 라우트가 비어 있으면 허용 목록 전체가 그 종류의 기본 effort로 예비 목록이 됩니다.

## 보안 업무 가이드

### 준비

보안 턴에는 다음이 자동으로 붙습니다.

- **`security_preamble`**: 권한 있는 랩/계약 가정, 출력 계약(완전한 실행 가능 PoC, 정확한 명령·버전·오프셋, 개념 설명만이면 실패), `[unverified]` 표기, CVE 작업 순서를 system 앞에 주입합니다.
- **`security_roe`**: 대상·기간·범위·연락처. 프리앰블에 최대 4,000자까지 붙습니다. 모델은 권한 문구가 있으면 덜 거절합니다.
- **거절 감지 확장**: "공격 코드는 개념적으로만", "proprietary", "NDA" 같은 보안식 회피도 거절로 판정합니다.
- **불완전 답변 재시도**: PoC/코드를 요청했는데 코드 블록·명령이 없으면 다른 모델에 출력 계약을 붙여 다시 시킵니다.
- **오류 매핑**: `stop_reason: refusal`, `stop_details.category: cyber`, "content filter/safety/blocked by policy"류 오류를 모델 실패로 처리합니다.
- **가드레일 왕복**: 거절되면 요청을 프레이밍만 바꿔 같은 모델에 다시 보냅니다. 아래 참조.
- **`cve_lookup`**: NVD 2.0의 설명·CVSS·CWE·영향 CPE 범위·참조를 조회합니다.

권장 설정:

```yaml
security_preamble: true
security_roe: |
  Target: 10.10.0.0/24 (lab), 2026-11-01..2026-11-30.
  Authorized by: 홍길동 / ticket SEC-123.
security_sanitize: true          # 실패 후에만 작동
security_sanitize_max_level: 3
security_sanitize_retry_same_model: true
security_fact_lock: true
degraded_retry: true
hold_stream: true                # 거절 검사 뒤에 표시 (재시작 필요)
cross_provider: true             # 허용 프로바이더로 직접 이동 (Hermes 내부 API)
```

`plugins.hook_callback_timeout`은 60~120초로 올리세요. 재답변·2차 코드 요청·사실 보정이 이 시간 안에서 돌아갑니다.

### CVE 분석 절차

1. `/route sec on`.
2. 먼저 `cve_lookup`으로 NVD 기록을 확보하게 합니다: `CVE-2024-3094를 cve_lookup으로 조회하고 영향 범위와 참조를 정리해줘`.
3. 패치 diff·어드바이저리·소스가 있으면 함께 제공합니다. 모델은 근본 원인을 패치 기준으로 써야 합니다.
4. PoC는 "랩 가정, 실행 가능한 전체 코드, 정확한 버전·오프셋"을 명시해 요청합니다. 예: `xz 5.6.1, Ubuntu 24.04 x86_64 랩에서 재현 가능한 전체 PoC를 작성해줘. 빌드·실행 명령과 확인 방법 포함.`
5. 답변 푸터에 `fact(s) unconfirmed`가 있으면 그 사실은 NVD/어드바이저리로 재확인합니다.
6. 가능하면 에이전트의 셸 도구로 PoC를 실제 실행해 오류를 다시 모델에 피드백합니다.

### 익스플로잇 / PoC 개발

- 가정(아키텍처, libc, 완화 기법: ASLR/PIE/NX/RELRO/canary)을 프롬프트에 먼저 적습니다. 답변도 그 가정을 명시해야 합니다.
- 한 번에 전부 요구하기보다: ① 버그 트리거 조건 ② 레이아웃/오프셋 ③ 코드 → ④ 검증 순서로 나눠도 됩니다. 왕복 L3도 같은 분할을 자동으로 합니다.
- 컴파일·실행 오류는 그대로 붙여 `이 오류를 고쳐서 전체 코드를 다시 출력`이라고 요청합니다. 이 플러그인은 턴마다 라우팅하므로 이어지는 수정도 같은 허용 목록 안에서 처리됩니다.
- 개념 설명만 오면 자동 재시도가 걸리지만, 계속 개념만 오면 `/route pin`으로 성향이 다른 모델을 고정하거나 로컬 모델을 예비에 추가하세요.

### 레드팀 / 모의해킹

- `security_roe`에 범위·기간·제외 대상을 적고, 턴마다 "범위 밖 대상은 표시"를 요구합니다.
- 정찰 → 취약점 검증 → PoC → 영향/완화 → 리포트 순으로 요청을 나눕니다.
- 결과 보고서에는 명령, 버전, 증거(출력), `[unverified]` 표시를 요구하세요. 프리앰블이 이미 이 형식을 지시합니다.

### 가드레일 왕복 (sanitize → 재시도 → 복원)

거절·가드레일이 나면 요청을 **프레이밍만** 순화해 **거절한 모델에 먼저** 다시 보냅니다. 사실 토큰(CVE id, 버전, 오프셋, 해시, `__free_hook` 같은 심볼)은 치환하지 않고 잠급니다.

| 레벨 | 내용 |
|---|---|
| 0 | 원문 (첫 시도) |
| 1 | 용어·프레이밍 치환: `exploit` → `proof-of-concept code`, `0-day` → `unpatched vulnerability` 등. 코드 블록·인라인 코드는 건드리지 않음 |
| 2 | + "랩/권한 가정, (1) 원인·영향 (2) 실행 가능한 전체 PoC" 구조 |
| 3 | + 1/2 분할: 먼저 분석만 받고, 두 번째 호출로 코드만 받아 합침 |

- 답변은 역매핑으로 **원래 용어로 복원**한 뒤 표시합니다. 잠긴 사실이 빠지면 그 사실을 붙여 다른 모델에 한 번 더 요청하고, 그래도 빠지면 푸터에 남깁니다: `— model-router: request sanitized L2 and restored; 1 fact(s) unconfirmed.`
- 성공한 레벨은 그 채팅에 기억되어 다음 턴이 그 레벨에서 시작합니다(최대 L2 기억, L3는 그 턴 한정). `/route`에 표시됩니다. 초기화는 `/route sec off` 또는 `/route reset`.
- 같은 모델 재시도는 `(모델, 레벨)` 쌍으로 1회씩만 허용합니다. 최고 레벨에서도 막히면 다음 허용 모델로 넘어갑니다.
- `security_sanitize_llm: true`면 L2+에서 모델이 문장을 직접 다듬고 `{순화어: 원어}` 사전을 반환하는 단계가 한 번 붙습니다. 실패하면 규칙 기반으로 폴백합니다. 기본은 꺼짐입니다.
- 순화는 실패 후에만 켜지므로 평상시 프롬프트 캐시·충실도 손실이 없습니다. `security_sanitize: false`로 전체를 끕니다.

**푸터 읽는 법**

| 푸터 | 의미 |
|---|---|
| `sanitized L1 and restored; facts verified.` | 1단계 순화로 통과, 원래 용어로 복원, 잠긴 사실 모두 확인됨 |
| `...; N fact(s) unconfirmed.` | 사실 N개가 답변에 없음. NVD/소스로 직접 확인 |
| `... without the requested code, so X redid it.` | 코드 없는 답변이라 다른 모델이 다시 작성 |
| `... declined this, so X answered instead (without tools).` | 거절이라 다른 모델이 도구 없이 재답변 |

프리앰블과 순화는 **제공자 가드레일을 없애지 않습니다.** 제공자 정책이 막으면 모델을 바꾸는 수밖에 없고, 이 플러그인은 그 전환과 재시도를 자동화할 뿐입니다.

### 프라이버시

- 순화본·프리앰블은 원문과 같은 프로바이더로 갑니다. 별도 중계 서비스는 없습니다.
- `cve_lookup`은 NVD에 CVE id 또는 키워드만 보냅니다(사용자 정보 없음, User-Agent는 플러그인 이름).
- 모델 기반 재서술을 켜면 `ctx.llm` 기본 모델로 요청문이 한 번 더 갑니다. 끄면 규칙 기반만 사용합니다.
- 로컬 모델을 허용 목록에 두면 그 경로에서는 텍스트가 외부로 나가지 않습니다.

## 설정 레퍼런스

전부 `plugins.entries.model-router.settings` 아래에 둡니다. Desktop Settings 화면과 `plugin.yaml` 폼에서도 편집할 수 있습니다.

### 일반

| 키 | 기본 | 설명 |
|---|---|---|
| `mode` | `auto` | `auto`: 턴마다 라우팅. `off`: 세션 모델 유지, cross_provider로 옮긴 세션은 원복 |
| `scope` | `{}` | 프로바이더 → 허용 모델 목록. 최대 3개 프로바이더 |
| `routes` | `{}` | 작업 종류별 `provider/model@effort` 시도 순서. 목록 밖 항목은 경고 후 무시 |
| `cross_provider` | `false` | 세션 자체를 다른 허용 프로바이더로 이동. Hermes 내부 API 사용, 실패 시 공개 경로로 폴백 |
| `classifier` | `rules` | `rules`(키워드·길이), `hybrid`(불확실할 때만 모델), `llm`(항상 모델) |
| `soft_refusal_retry` | `true` | 짧은 거절 답변을 다른 모델로 재답변 (도구 없음) |
| `degraded_retry` | `true` | 보안 턴에서 코드 없는 답변을 다른 모델로 재작성 |
| `hold_stream` | `false` | 스트리밍 답변을 거절 검사 뒤에 표시. 재시작 필요 |
| `cooldown_minutes` | `30` | 연속 2회 실패한 모델이 뒤로 밀리는 시간 |
| `max_explore` | `3` | `route_explore`가 한 번에 돌릴 최대 모델 수 (1~8) |
| `extra_guardrail_patterns` | `[]` | 제공자 오류 문자열 중 가드레일로 볼 추가 문구(소문자) |

### 보안

| 키 | 기본 | 설명 |
|---|---|---|
| `security_preamble` | `true` | sec 턴에 권한·출력 계약·CVE 절차 프리앰블 주입 |
| `security_roe` | `""` | 권한/범위 문구. 프리앰블에 최대 4,000자 |
| `security_preamble_extra` | `""` | 프리앰블 뒤에 붙는 추가 상시 지시 |
| `security_guardrail_defaults` | `true` | "content filter / safety / cyber"류 오류를 가드레일로 기본 처리 |
| `security_sanitize` | `true` | 가드레일 왕복 사용. 실패 후에만 작동 |
| `security_sanitize_max_level` | `3` | 최고 순화 레벨 (1~3) |
| `security_sanitize_llm` | `false` | L2+에서 모델이 요청을 재서술 (구조화 호출 1회) |
| `security_sanitize_retry_same_model` | `true` | 다음 레벨을 다른 모델이 아니라 거절한 모델에 먼저 시도 |
| `security_fact_lock` | `true` | CVE id·버전·오프셋·해시·심볼 치환 금지 + 답변 검사 |

`routes.sec`의 기본 effort는 `xhigh`입니다.

## 채팅 명령

```
/route                 상태: 모드, 허용 목록, 작업 종류별 순서, 보안 모드·순화 레벨, 경고
/route auto | off      라우팅 켜기/끄기 (off면 cross_provider로 옮긴 세션을 원래대로 돌려놓음)
/route pin openai-codex/gpt-6.2-sol@high    이 채팅은 이 모델로 고정 (/route unpin)
/route prefer sec anthropic/claude-opus-4-8
/route clear sec
/route sec on | off    이 채팅을 보안 모드로 고정/해제. on은 sec 분류 강제 + 프리앰블
/route stats | reset   모델별 성공·실패·쿨다운 / 통계·선호·순화 레벨 초기화
```

`pin`, `sec` 상태는 게이트웨이 메모리에 있습니다. 재시작하면 초기화되며 `config.yaml`에는 저장되지 않습니다. `prefer`는 영구 저장됩니다.

## Desktop 화면

**Settings → Plugins → Model Router**에 다음 화면이 생깁니다. 화면 위에 편집 중인 프로필이 표시되고 모든 읽기·쓰기는 그 프로필에만 적용됩니다.

| 화면 | 내용 |
|---|---|
| Model Router | 라우팅 on/off, 분류 방식, 프로바이더 넘나들기, 소프트 거절·불완전 답변 재시도, 보안 프리앰블·ROE, 가드레일 왕복과 레벨, 사실 잠금, 쿨다운, 비교 수, 경고 |
| Allowed models | 프로바이더 체크박스(최대 3개)와 모델 체크박스, 모델 id 직접 추가. 목록에서 빠진 모델은 라우트에서도 제거 |
| Routes | 작업 종류(quick / standard / code / deep / sec)별 첫 모델·effort, 실제 시도 순서, 학습된 선호 |
| Stats | 모델별 성공·실패, 쿨다운, 마지막 실패 이유, 선호 삭제, 전체 초기화 |

Desktop에서 통계를 초기화하거나 선호를 바꾸면 실행 중 세션도 다음 턴부터 반영합니다. Desktop 쪽은 **Capabilities → Plugins**에서 먼저 켜야 하고, 백엔드는 플러그인을 켠 프로필에서만 응답합니다.

- `desktop/plugin.js`: 화면. Hermes가 `~/.hermes/desktop-plugins/model-router/`로 복사해 불러옵니다.
- `dashboard/plugin_api.py`: 화면 백엔드. `/api/plugins/model-router/`에 연결됩니다.

## 동작 방식

| 단계 | 하는 일 |
|---|---|
| 턴 시작 (`pre_llm_call`) | 요청을 작업 종류로 분류하고(sec 모드면 강제 sec), 후보 순서를 정합니다. 순서는 pin → 학습된 선호 → 라우트, 쿨다운 모델은 뒤로 |
| 요청마다 (`llm_request`) | 요청 프로바이더에서 이번 턴의 첫 미실패 후보로 `model`·effort를 바꿉니다. sec 턴이면 프리앰블·순화본·시스템 블록을 주입합니다. |
| 오류 (`api_request_error`) | 모델 탓 오류면 실패로 기록합니다. 정책성 오류면 순화 레벨을 올리고, 같은 모델을 새 레벨로 한 번 더 시도할 수 있게 합니다. |
| 소프트 거절·불완전 (`transform_llm_output`) | 짧은 거절, sec의 코드 없는 답변을 감지해 실패로 기록하고 다음 모델(또는 새 레벨의 같은 모델)에 재요청합니다. 성공 답변은 용어 복원·사실 검사 후 전달합니다. |
| 성공 (`post_llm_call`) | 성공 횟수를 기록하고 실패 연속을 리셋합니다. |
| 비교 (`route_explore`) | 허용 모델마다 서브에이전트를 띄워 같은 과제를 시키고 채점해 이긴 모델을 학습합니다. |

같은 모델이 연속 2번 실패하면 `cooldown_minutes` 동안 후보 뒤로 밀립니다. 턴마다 모델이 바뀌면 프롬프트 캐시는 깨집니다. effort는 요청에 이미 있는 값만 바꾸고 새 추론 블록을 추가하지 않습니다.

## 도구 레퍼런스

### `route_explore`

같은 과제를 허용 모델 여럿에 병렬로 시키고 채점합니다. 느리고 비쌉니다.

| 파라미터 | 설명 |
|---|---|
| `task` (필수) | 모든 모델에 줄 자기완결적 과제 |
| `task_class` | quick / standard / code / deep / sec. 승자를 기억할 종류 (기본 standard) |
| `models` | 시도할 `provider/model` 목록. 허용 목록 안이어야 함 |
| `save` | 승자를 선호로 저장 (기본 true) |
| `timeout_seconds` | 모델별 제한 (기본 600) |

sec 판정은 "실행 가능성, 정확한 오프셋·버전, 군더더기 감점" 기준이고, 승자는 그 작업 종류의 선호가 됩니다. 예: `여러 모델로 이 CVE PoC를 비교해서 가장 구체적인 모델을 찾아줘`.

### `cve_lookup`

NVD 2.0 조회. CVE id 또는 키워드 중 하나가 필요합니다.

| 파라미터 | 설명 |
|---|---|
| `cve_id` | 예: `CVE-2024-3094` |
| `keyword` | 제품/자유 검색어. 예: `OpenSSH 9.7p1` |
| `limit` | 키워드 검색 최대 건수 1~20 (기본 5) |

반환: 설명, CVSS v4.0/v3.1, CWE, 영향 CPE 버전 범위, 참조, NVD 원본 URL. 15분 캐시. 익명 한도(대략 30초당 5회)에 걸리면 403/429와 안내를 반환합니다.

## 문제 해결

**계속 거절합니다.**
1. 허용 목록에 프로바이더가 2개 이상인지 확인합니다. 하나뿐이면 갈 곳이 없습니다.
2. `/route`에서 기억된 순화 레벨을 봅니다. L2에서 시작하면 이미 순화가 기본이고, 그 턴에서도 L3까지 갈 수 있습니다. 그래도 막히면 지식/정책 한계일 수 있습니다.
3. 로컬 모델을 마지막 예비로 추가합니다. 제공자 가드레일이 없는 마지막 수단입니다.
4. `security_roe`를 더 구체적으로 씁니다(대상·기간·승인자).
5. 요청을 ① 개념 ② 코드 ③ 검증으로 직접 나눕니다.

**순화 없이 프리앰블만 쓰고 싶습니다.**
`security_sanitize: false`. 프리앰블·ROE·거절 감지·오류 매핑은 그대로 동작하고, 요청 문장은 바뀌지 않습니다.

**답변 단어가 어색합니다(예: "exploit"이 "proof-of-concept code"로 보임).**
복원이 일부 실패한 것입니다. 푸터에 `restored`가 있는지 확인합니다. `security_sanitize_llm`을 끄면 결정론적 사전만 쓰므로 변형이 줄어듭니다. 레벨이 높게 기억돼 있으면 `/route sec off` 후 다시 켭니다.

**다음 턴도 계속 순화된 채로 시작합니다.**
의도된 동작입니다(최대 L2 기억). `/route sec off` 또는 `/route reset`으로 지웁니다.

**`fact(s) unconfirmed`가 붙습니다.**
잠긴 사실을 답변이 빠뜨렸다는 뜻입니다. `cve_lookup`이나 소스로 확인하고, 답변에 반영해 달라고 다시 요청하세요.

**재답변이 잘립니다 / 30초 훅 제한.**
`plugins.hook_callback_timeout`을 60~120으로 올리고, `hold_stream: true`로 바꾼 뒤 재시작하세요.

**`cross_provider`가 동작하지 않습니다.**
`plugins.isolation: host`이거나 Hermes 내부 함수가 바뀌면 경고만 남고 공개 경로로 동작합니다. `fallback_providers`로 대체하거나 버전을 맞추세요.

**NVD가 403/429를 반환합니다.**
익명 레이트리밋입니다. 30초 뒤 다시 시도하거나 `NVD_API_KEY`를 설정하세요.

**Desktop 화면이 안 보입니다.**
Capabilities → Plugins에서 켜고, `desktop/plugin.js` 변경분이 `~/.hermes/desktop-plugins/model-router/`에 복사됐는지 확인하세요.

**`hermes plugins validate`가 실패합니다.**
private import 경고(쿨다운/모델 전환, curses UI, dashboard 저장) 때문이며 기존부터 있습니다. 각 경로는 실패해도 대체 동작이 있습니다.

## 한계와 주의

- 프리앰블·순화·재시도는 제공자 가드레일을 **제거**하지 않습니다. 정책이 막으면 모델 교체가 유일한 해법입니다.
- 순화는 프레이밍만 바꿉니다. 모델이 모르는 오프셋·비공개 정보는 그대로 모릅니다.
- 사실 잠금은 "빠짐"만 잡습니다. 틀린 값을 잡지는 못합니다. NVD/소스 대조는 사용자 몫입니다.
- 재답변·2차 코드 요청·사실 보정은 **도구 없이** 돌아갑니다. 실행 검증은 원래 턴의 에이전트 도구로 하세요.
- 순화가 시작되면 프롬프트 캐시 접두사가 바뀝니다. 실패 후에만 시작하므로 평상시 비용은 없습니다.
- `route_explore` 서브에이전트는 모델별 추론 강도를 지정할 수 없어 `delegation.reasoning_effort`를 씁니다.
- `fallback_providers`에 허용 목록 밖 모델이 있으면 그 프로바이더에 남은 허용 모델이 없을 때 Hermes가 그 모델을 씁니다. `/route`가 경고합니다.

## 공개 API와 개발

- **기본(`cross_provider: false`)은 공개 플러그인 API만 씁니다.** 모델은 요청이 가는 프로바이더 안에서만 바뀝니다. 다른 프로바이더의 허용 모델은 Hermes의 `fallback_providers`가 넘어갔을 때 쓰입니다.
- **`cross_provider: true`** 는 `hermes_cli.model_switch`, `AIAgent.switch_model` 내부 함수를 씁니다. Hermes 업데이트 후 깨질 수 있습니다.
- 재답변·채점은 `llm.model_override`, `llm.provider_override` 권한이 필요합니다.
- 턴마다 모델이 바뀌면 프롬프트 캐시가 깨집니다.

```bash
hermes plugins validate .
hermes plugins doctor . --ci
PYTHONPATH=~/.hermes/hermes-agent:~/.hermes/hermes-agent/venv/lib/python3.11/site-packages python -m pytest
```

구성: `router.py` 턴 상태·건강도, `classify.py` 분류, `hooks.py` 훅/미들웨어, `refusal.py` 거절·불완전 감지, `security.py` 프리앰블, `sanitize.py` 왕복, `nvd.py` NVD, `explore.py` 비교 도구, `slash.py` 명령, `dashboard/`·`desktop/` UI.

### 변경 기록

- **0.3.0** — `sec` 작업 종류, 보안 프리앰블/ROE, 거절·불완전 답변 감지, cyber 오류 매핑, 가드레일 왕복(L1~L3, 사실 잠금, 복원), `/route sec`, `cve_lookup`, `route_explore` sec 판정, Desktop/README.
- **0.2.0** — 허용 목록 라우팅, 실패 전환, 소프트 거절 재답변, 비교 학습, Desktop 화면.
