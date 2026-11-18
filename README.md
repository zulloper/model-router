# Model Router (Hermes 플러그인)

업무 지시마다 작업 종류를 분류해 **허용 목록 안에서만** 모델과 추론 강도를 고릅니다. 가드레일·거절·모델 오류로 멈추면 다음 허용 모델로 넘어가고, 여러 모델을 서브에이전트로 돌려 비교한 결과를 학습합니다.

## 허용 목록 (scope)

라우팅이 고를 수 있는 건 여기 체크한 것뿐입니다. 프로바이더는 **최대 3개**, 각 프로바이더에서 체크한 모델만 씁니다. 라우트, `/route pin`, 학습된 선호, `route_explore` 후보가 목록 밖이면 모두 무시합니다.

```bash
hermes model-router setup   # 1) 프로바이더 체크 → 2) 모델 체크 → 3) 작업 종류별 첫 모델·추론 강도
hermes model-router show    # 현재 설정 출력
```

설정은 `config.yaml`의 `plugins.entries.model-router.settings`에 저장됩니다. Desktop 앱 **Settings → Plugins**에서 고칠 수도 있습니다.

```yaml
plugins:
  entries:
    model-router:
      settings:
        mode: auto                 # auto | off
        scope:
          openai-codex: [gpt-6.2-sol, gpt-daybreak-blue-latest-900k]
          anthropic: [claude-opus-4-8]
        routes:                    # 앞에서부터 시도. 나머지 허용 모델은 자동으로 예비 모델이 됨
          quick:    [openai-codex/gpt-6.2-sol@low]
          standard: [openai-codex/gpt-6.2-sol@medium]
          code:     [openai-codex/gpt-6.2-sol@high]
          deep:     [anthropic/claude-opus-4-8@high, openai-codex/gpt-6.2-sol@xhigh]
        cross_provider: false      # true면 세션 자체를 다른 프로바이더로 옮김 (Hermes 내부 API 사용)
        classifier: rules          # rules | hybrid | llm
        soft_refusal_retry: true
```

## Desktop 설정 화면

**Settings → Plugins → Model Router**에 아래 화면이 생깁니다. 화면 위에 지금 편집 중인 프로필이 표시되고, 모든 읽기·쓰기는 그 프로필에만 적용됩니다.

| 화면 | 내용 |
|---|---|
| Model Router | 라우팅 켜기/끄기, 분류 방식, 프로바이더 넘나들기, 소프트 거절 재답변, 쿨다운, 비교할 모델 수, 경고 |
| Allowed models | 프로바이더 체크박스(최대 3개, 3개를 채우면 나머지는 잠김)와 각 프로바이더의 모델 체크박스, 모델 id 직접 추가. 저장하면 허용 목록에서 빠진 모델은 라우트에서도 같이 빠짐 |
| Routes | 작업 종류(quick / standard / code / deep)별 첫 모델과 추론 강도 드롭다운, 실제 시도 순서, 학습된 선호 모델 |
| Stats | 모델별 성공·실패 횟수, 쿨다운 상태, 마지막 실패 이유, 선호 모델 지우기, 전체 초기화 |

`plugin.yaml`의 자동 설정 폼은 위 화면 아래 **Agent settings** 하위 페이지로 들어갑니다.

구성은 이렇습니다.
- `desktop/plugin.js`: Desktop 화면. Hermes가 `~/.hermes/desktop-plugins/model-router/`로 복사해서 불러옵니다.
- `dashboard/plugin_api.py`: 화면 뒤의 백엔드. `/api/plugins/model-router/`에 연결되고, Desktop이 고른 프로필 기준으로 동작합니다.

Desktop 쪽도 처음에는 꺼져 있어서, **Capabilities → Plugins**에서 Model Router를 켜야 합니다. 백엔드는 플러그인을 켠 프로필에서만 응답하고, 꺼진 프로필에서는 화면에 켜는 방법을 안내합니다. Desktop에서 통계를 초기화하거나 선호 모델을 바꾸면, 실행 중인 세션도 다음 턴부터 반영합니다.

## 동작 방식

| 단계 | 하는 일 |
|---|---|
| 턴 시작 (`pre_llm_call`) | 요청을 quick / standard / code / deep 중 하나로 분류하고, 시도할 후보 순서를 정합니다. 순서는 고정(pin) → 학습된 선호 → 라우트이고, 쿨다운 중인 모델은 뒤로 갑니다. |
| 요청마다 (`llm_request` 미들웨어) | 요청이 가는 프로바이더에서 이번 턴 후보 중 아직 실패하지 않은 첫 모델로 `model`과 추론 강도를 바꿔 끼웁니다. |
| 오류 (`api_request_error`) | 가드레일·모델 없음·권한 없음처럼 모델 탓인 오류면 그 모델을 실패로 표시합니다. Hermes가 다시 보내는 요청은 다음 허용 모델로 갑니다. 속도 제한 같은 일시적 오류에서는 모델을 유지합니다. |
| 소프트 거절 (`transform_llm_output`) | "도와드릴 수 없습니다" 같은 짧은 거절 답변을 감지하면 다른 허용 모델로 다시 답하게 합니다. 이때는 도구를 쓸 수 없습니다. 다음 턴은 거절한 모델이 아닌 모델로 시작합니다. |
| 비교 (`route_explore` 도구) | "여러 모델로 비교해서 제일 잘 맞는 모델 찾아줘"라고 하면 에이전트가 이 도구를 호출합니다. 허용 모델마다 서브에이전트를 띄워 같은 일을 시키고, 결과를 채점해 이긴 모델을 그 작업 종류의 선호 모델로 저장합니다. |

같은 모델이 연속 2번 실패하면 `cooldown_minutes`(기본 30분) 동안 후보 순서의 뒤로 밀립니다.

## 채팅 명령

```
/route                 상태: 허용 목록, 작업 종류별 순서, 이 채팅의 마지막 결정, 경고
/route auto | off      라우팅 켜기/끄기 (off면 cross_provider로 옮긴 세션을 원래대로 돌려놓음)
/route pin openai-codex/gpt-6.2-sol@high    이 채팅은 이 모델로 고정 (/route unpin)
/route prefer deep anthropic/claude-opus-4-8 | /route clear deep
/route stats | reset
```

## 공개 API와 옵션

- **기본(`cross_provider: false`)은 공개 플러그인 API만 씁니다.** 모델은 요청이 가는 프로바이더 안에서만 바뀝니다. 다른 프로바이더의 허용 모델은 Hermes의 `fallback_providers`가 그 프로바이더로 넘어갔을 때 쓰입니다.
- **`cross_provider: true`** 이면 턴 시작 시 세션을 라우트 첫 모델의 프로바이더로 옮깁니다. Hermes 내부 함수(`hermes_cli.model_switch`, `AIAgent.switch_model`)를 쓰므로 Hermes 업데이트 후 깨질 수 있습니다. 내부 함수가 없거나 `plugins.isolation: host`면 경고만 남기고 기본 방식으로 동작합니다. CLI 상태 표시줄에는 바뀐 모델이 바로 반영되지 않을 수 있습니다.
- `hermes plugins validate`가 private import 경고를 냅니다. 대상은 위 옵션과 setup 메뉴(curses 체크리스트, 프로바이더 목록)이고, 모두 실패해도 대체 경로가 있습니다.

## 알아둘 점

- `fallback_providers`에 허용 목록 밖 모델이 있으면, 그 프로바이더에 남은 허용 모델이 없을 때 Hermes가 그 모델을 씁니다. `/route`가 경고로 알려 줍니다.
- 소프트 거절 재답변은 `plugins.hook_callback_timeout`(기본 30초) 안에 끝나야 합니다. 긴 답변이 필요하면 이 값을 늘리세요.
- 다시 답하거나 채점할 때 다른 모델을 쓰려면 플러그인을 켤 때 `llm.model_override`, `llm.provider_override` 권한에 동의해야 합니다.
- 턴마다 모델이 바뀌면 프롬프트 캐시가 깨집니다. 추론 강도는 요청에 이미 들어 있는 값만 바꾸고, 추론 블록을 새로 추가하지는 않습니다.
- `route_explore` 서브에이전트는 모델별로 추론 강도를 지정할 수 없어 `delegation.reasoning_effort`로 돌아갑니다.

## 개발

```bash
hermes plugins validate .
hermes plugins doctor . --ci
PYTHONPATH=~/.hermes/hermes-agent:~/.hermes/hermes-agent/venv/lib/python3.11/site-packages python -m pytest
```
