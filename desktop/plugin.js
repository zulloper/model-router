// Model Router — Desktop half: Settings ▸ Plugins ▸ Model Router.
//
//   Model Router        routing on/off, classifier, cross-provider switch, retries, warnings
//   ├ Allowed models    check up to 3 providers and the models routing may use on each
//   ├ Routes            per task class: the model to try first and its reasoning effort
//   └ Stats             per-model outcomes, cool-downs, learned preferences, reset
//
// Every read and write goes to this package's backend (dashboard/plugin_api.py) through ctx.rest,
// which the app scopes to the active profile, so each profile keeps its own allowlist.
// Plain ESM with jsx() calls: Desktop loads this file uncompiled.

import {
  Badge, Button, Checkbox, EmptyState, ErrorState, Input, ListRow, Loader, SegmentedControl, Select,
  SelectContent, SelectItem, SelectTrigger, SelectValue, StatusDot, ToggleRow, host, pluginSettingsHref,
  queryClient, useQuery, useValue, atom
} from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'model-router'
const CLASS_HELP = {
  quick: 'Short lookups, translation, summaries',
  standard: 'Everyday writing and explanations',
  code: 'Writing, fixing and reviewing code',
  deep: 'Hard reasoning, design, analysis, long tasks',
  sec: 'CVE analysis, exploit/PoC, reversing, red team'
}
const CLASSIFIERS = [
  { id: 'rules', label: 'Rules' },
  { id: 'hybrid', label: 'Hybrid' },
  { id: 'llm', label: 'Model' }
]

const keyOf = cand => String(cand || '').split('@')[0]
const effortOf = cand => {
  const s = String(cand || '')
  return s.includes('@') ? s.slice(s.lastIndexOf('@') + 1) : ''
}
const errText = e => (e && (e.detail || e.message)) || String(e)

// ── Language ───────────────────────────────────────────────────────────────────────────
// The app's own language list has no Korean, so this plugin carries a small English/Korean switch
// (General ▸ Language). Without a saved choice it follows the browser/OS language. English is the key,
// so a missing translation simply shows the English text.
const LANG_KEY = 'lang'
const LANGS = [
  { id: 'en', label: 'English' },
  { id: 'ko', label: '한국어' }
]
const detectLang = () =>
  typeof navigator !== 'undefined' && /^ko\b/i.test(navigator.language || '') ? 'ko' : 'en'
const $lang = atom(detectLang())
const KO = {
  "Route each turn": "요청마다 모델 자동 선택",
  "Task classifier": "작업 분류 방식",
  "Move the session across providers": "프로바이더 간 세션 이동",
  "Re-answer soft refusals": "약한 거절 재답변",
  "Cool-down (minutes)": "쿨다운(분)",
  "Models per comparison": "비교당 모델 수",
  "Redo security answers without code": "코드 없는 보안 답변 재작성",
  "Security preamble": "보안 프리앰블",
  "Rules of engagement": "교전 규칙(ROE)",
  "Generic guardrail errors": "일반 가드레일 오류",
  "Guardrail round-trip": "가드레일 왕복",
  "Highest sanitize level": "최고 순화 단계",
  "Retry the refused model first": "거절한 모델 먼저 재시도",
  "Lock facts": "사실 고정",
  "Model-assisted rephrase": "모델 보조 재서술",
  "Warnings": "경고",
  "Model Router backend unavailable": "Model Router 백엔드를 사용할 수 없습니다",
  "enabled": "활성",
  "not enabled in this profile": "이 프로필에서 비활성",
  "Saving…": "저장 중…",
  "No providers found": "프로바이더가 없습니다",
  "session provider": "세션 프로바이더",
  "Add a model id": "모델 ID 추가",
  "Check at least one model, or this provider is not saved.": "모델을 하나 이상 체크하세요. 그렇지 않으면 이 프로바이더는 저장되지 않습니다.",
  "Save allowlist": "허용 목록 저장",
  "Undo": "되돌리기",
  "No allowed models yet": "허용된 모델이 아직 없습니다",
  "Models": "모델",
  "No outcomes yet.": "아직 기록이 없습니다.",
  "Learned preferences": "학습된 선호",
  "None.": "없음.",
  "Clear": "지우기",
  "Refresh": "새로고침",
  "Click again to forget everything": "한 번 더 누르면 전부 지웁니다",
  "Reset stats and preferences": "통계와 선호 초기화",
  "Edit": "편집",
  "Save": "저장",
  "Add": "추가",
  "Pick a model and reasoning effort per request from the allowed models. Off leaves the session model alone.": "허용된 모델 중에서 요청마다 모델과 추론 강도를 고릅니다. 끄면 세션의 모델을 그대로 둡니다.",
  "Nothing allowed yet — routing does nothing until you check models under Allowed models.": "아직 허용된 모델이 없습니다. 허용 모델에서 모델을 체크하기 전까지 라우팅은 아무것도 하지 않습니다.",
  "Rules: keywords and length, no extra call. Hybrid: asks a model only when rules are unsure. Model: always asks.": "규칙: 키워드와 길이로 판단하며 추가 호출이 없습니다. 하이브리드: 규칙이 애매할 때만 모델에게 묻습니다. 모델: 항상 모델에게 묻습니다.",
  "Off: models change only within the provider the session is on (public plugin API). On: the session moves to another allowed provider when the route says so. Uses Hermes internals and may break after an update; it falls back to off when they are missing.": "꺼짐: 세션이 속한 프로바이더 안에서만 모델을 바꿉니다(공개 플러그인 API). 켜짐: 경로가 지시하면 세션을 다른 허용 프로바이더로 옮깁니다. Hermes 내부 기능을 쓰기 때문에 업데이트 후 동작하지 않을 수 있으며, 내부 기능이 없으면 꺼진 상태로 돌아갑니다.",
  "A short \"I can't help with that\" reply is answered again by another allowed model (without tools).": "\"도와드릴 수 없습니다\" 같은 짧은 거절 답변은 다른 허용 모델이 (도구 없이) 다시 답합니다.",
  "A model that fails twice in a row moves to the back of every route for this long.": "연속 두 번 실패한 모델은 이 시간 동안 모든 경로의 맨 뒤로 밀립니다.",
  "The most models route_explore runs side by side (one subagent each).": "route_explore가 한 번에 나란히 실행하는 최대 모델 수입니다(모델마다 서브에이전트 하나).",
  "On security turns, an answer asked for a PoC or code but delivered prose is retried on another allowed model with an output contract.": "보안 요청에서 PoC나 코드를 요청했는데 설명만 돌아오면, 출력 규칙을 붙여 다른 허용 모델로 다시 시킵니다.",
  "On security turns, put authorization, an output contract (runnable PoC, exact offsets, no hedging) and a CVE workflow into the system prompt. Does not remove provider guardrails.": "보안 요청의 시스템 프롬프트에 권한 가정, 출력 규칙(실행 가능한 PoC, 정확한 오프셋, 얼버무리지 않기), CVE 작업 순서를 넣습니다. 제공자의 가드레일을 없애지는 않습니다.",
  "Target, period, scope, contacts. Models refuse less when authorization is explicit.": "대상, 기간, 범위, 연락처. 권한이 명확하면 모델이 덜 거절합니다.",
  "e.g. Target 10.10.0.0/24, 2026-11-01..11-30, authorized": "예: 대상 10.10.0.0/24, 2026-11-01..11-30, 승인됨",
  "Treat \"content filter / safety / blocked by policy / cyber\" in a provider error as a guardrail and move to the next allowed model.": "제공자 오류에 \"content filter / safety / blocked by policy / cyber\"가 있으면 가드레일로 보고 다음 허용 모델로 넘어갑니다.",
  "After a refusal, rephrase the request (framing terms only; CVE ids, versions and offsets stay verbatim), retry, then restore the answer to your terms. Escalates L1 wording -> L2 walkthrough -> L3 analysis/code split.": "거절된 뒤 요청의 표현만 바꿔(CVE 번호, 버전, 오프셋은 그대로) 다시 보내고, 답변은 원래 표현으로 복원합니다. 1단계 표현 → 2단계 단계별 구성 → 3단계 분석/코드 분리 순으로 올라갑니다.",
  "1: wording only. 2: + walkthrough structure. 3: + split into analysis and code.": "1: 표현만. 2: + 단계별 구성. 3: + 분석과 코드 분리.",
  "Use the next level on the model that refused before switching to another one.": "다른 모델로 넘어가기 전에, 거절한 모델에 다음 단계로 먼저 시도합니다.",
  "Never substitute CVE ids, versions, offsets, hashes or symbols; if the answer drops one, retry with the missing facts listed.": "CVE 번호, 버전, 오프셋, 해시, 심볼은 바꾸지 않습니다. 답변에서 빠지면 빠진 항목을 적어 다시 요청합니다.",
  "At L2+, let a model reword the request first (one structured call). Deterministic rules remain the fallback.": "2단계 이상에서 모델이 요청을 먼저 다듬습니다(구조화 호출 1회). 규칙 기반 방식은 대체 수단으로 남습니다.",
  "This profile has no provider with credentials yet. Add one with `hermes model`, then come back.": "이 프로필에는 자격 증명이 있는 프로바이더가 아직 없습니다. `hermes model`로 추가한 뒤 다시 오세요.",
  "Unsaved changes. Running sessions pick them up on their next turn.": "저장하지 않은 변경 사항이 있습니다. 실행 중인 세션은 다음 턴부터 반영합니다.",
  "Pick the model each kind of task tries first and its reasoning effort. The other allowed models follow automatically as backups when a model is stopped by a guardrail, a refusal or an error.": "작업 종류마다 가장 먼저 시도할 모델과 추론 강도를 고르세요. 나머지 허용 모델은 모델이 가드레일, 거절, 오류로 멈출 때 자동으로 예비가 됩니다.",
  "Check providers and models under Allowed models first.": "먼저 허용 모델에서 프로바이더와 모델을 체크하세요.",
  "Outcomes the router recorded in this profile.": "이 프로필에서 라우터가 기록한 결과입니다.",
  "Set by route_explore comparisons or /route prefer.": "route_explore 비교나 /route prefer로 설정됩니다.",
  "default": "기본",
  "Allowed models": "허용 모델",
  "profile": "프로필",
  "General": "일반",
  "Routes": "경로",
  "Stats": "통계",
  "Rules": "규칙",
  "Hybrid": "하이브리드",
  "Model": "모델",
  "Quick": "간단",
  "Standard": "일반",
  "Code": "코드",
  "Deep": "심층",
  "Sec": "보안",
  "Order": "순서",
  "Short lookups, translation, summaries": "짧은 조회, 번역, 요약",
  "Everyday writing and explanations": "일상적인 글쓰기와 설명",
  "Writing, fixing and reviewing code": "코드 작성, 수정, 리뷰",
  "Hard reasoning, design, analysis, long tasks": "어려운 추론, 설계, 분석, 긴 작업",
  "CVE analysis, exploit/PoC, reversing, red team": "CVE 분석, 익스플로잇/PoC, 리버싱, 레드팀",
  "Language of this plugin's screens.": "이 플러그인 화면의 언어입니다."
}
const tr = s => ($lang.get() === 'ko' && KO[s]) || s
const FN = {
  backend: {
    en: (p, e) => `Profile "${p}": install the model-router plugin and enable it for this profile (hermes -p ${p} plugins enable model-router), then restart its gateway. (${e})`,
    ko: (p, e) => `프로필 "${p}": model-router 플러그인을 설치하고 이 프로필에서 활성화한 뒤(hermes -p ${p} plugins enable model-router) 게이트웨이를 재시작하세요. (${e})`
  },
  summary: {
    en: (n, m) => `${n} provider${n === 1 ? '' : 's'}, ${m} model${m === 1 ? '' : 's'}: `,
    ko: (n, m) => `프로바이더 ${n}개, 모델 ${m}개: `
  },
  allowIntro: {
    en: max => `Routing only ever uses what is checked here: at most ${max} providers, and on each only the checked models. A route, a pin, a learned preference or a comparison outside this list is ignored.`,
    ko: max => `라우팅은 여기서 체크한 것만 사용합니다: 프로바이더는 최대 ${max}개, 각 프로바이더에서는 체크한 모델만. 이 목록 밖의 경로, 고정, 학습된 선호, 비교는 무시됩니다.`
  },
  checked: { en: n => `${n} checked`, ko: n => `${n}개 선택됨` },
  limit: { en: max => `limit of ${max} providers reached`, ko: max => `프로바이더 ${max}개 한도에 도달했습니다` },
  learned: {
    en: m => `★ Learned from a comparison: starts on ${m} (clear it under Stats)`,
    ko: m => `★ 비교로 학습됨: ${m}에서 시작합니다 (통계에서 지울 수 있습니다)`
  },
  stat: {
    en: (ok, fail, cool, why) => `${ok} ok · ${fail} failed` + (cool ? ` · cooling down ~${cool}m` : '') + (why ? ` · last failure: ${why}` : ''),
    ko: (ok, fail, cool, why) => `${ok}회 성공 · ${fail}회 실패` + (cool ? ` · 쿨다운 약 ${cool}분` : '') + (why ? ` · 마지막 실패: ${why}` : '')
  }
}
const tf = (name, ...a) => FN[name][$lang.get() === 'ko' ? 'ko' : 'en'](...a)

// Sub-pages also live in the Settings rail, folded under the entry; the rail can be collapsed or
// scrolled away, so every page carries its own tabs too.
const PAGES = [
  { id: '', label: 'General' },
  { id: 'allowlist', label: 'Allowed models' },
  { id: 'routes', label: 'Routes' },
  { id: 'stats', label: 'Stats' }
]
const openPage = id => host.navigate?.(pluginSettingsHref ? pluginSettingsHref(ID, id || undefined) : '/settings?tab=plugins')

function PageTabs({ page }) {
  return jsx('div', {
    className: 'mb-3 flex flex-wrap gap-1 border-b border-(--ui-stroke-secondary) pb-2',
    children: PAGES.map(p =>
      jsx(Button, {
        key: p.id || 'general',
        size: 'xs',
        variant: p.id === page ? 'secondary' : 'ghost',
        'aria-current': p.id === page ? 'page' : undefined,
        onClick: () => openPage(p.id),
        children: tr(p.label)
      }))
  })
}

function Muted({ children, className = '' }) {
  return jsx('div', { className: `text-xs text-(--ui-text-tertiary) ${className}`, children })
}

function Heading({ title, children }) {
  return jsxs('div', {
    className: 'mt-6 mb-1 first:mt-0',
    children: [
      jsx('div', { className: 'text-sm font-semibold', children: title }),
      children ? jsx(Muted, { children }) : null
    ]
  })
}

export default {
  id: ID,
  name: 'Model Router',
  defaultEnabled: false,
  register(ctx) {
    let disposed = false
    const root = [ID, ctx.source]
    $lang.set(ctx.storage.get(LANG_KEY, detectLang()) === 'ko' ? 'ko' : 'en')
    const setLang = v => {
      $lang.set(v)
      ctx.storage.set(LANG_KEY, v)
    }

    function useScope() {
      const connection = useValue(host.state.connectionId) || 'local'
      const profile = useValue(host.state.profile) || 'default'
      return { connection, profile, key: [...root, connection, profile, 'state'] }
    }

    // One query per (connection, profile); writes return the fresh state and replace it.
    function useRouterState() {
      const scope = useScope()
      const query = useQuery({
        queryKey: scope.key,
        retry: false,
        gcTime: 0,
        queryFn: () => ctx.rest('/state')
      })
      const [busy, setBusy] = useState(false)
      const [error, setError] = useState('')
      async function call(path, method, body) {
        setBusy(true)
        setError('')
        try {
          const fresh = await ctx.rest(path, { method, body })
          if (!disposed) queryClient.setQueryData(scope.key, fresh)
          return true
        } catch (e) {
          setError(errText(e))
          host.notify?.({ kind: 'error', message: `Model Router: ${errText(e)}` })
          return false
        } finally {
          setBusy(false)
        }
      }
      const save = values => call('/settings', 'PUT', { values })
      return { ...query, scope, busy, error, call, save }
    }

    // Loading / backend-missing / plugin-off states shared by every page.
    function Frame({ render, page = '' }) {
      const st = useRouterState()
      useValue($lang)
      if (st.isLoading) return jsx('div', { className: 'flex justify-center p-6', children: jsx(Loader, {}) })
      if (st.isError) {
        return jsx(ErrorState, {
          title: tr("Model Router backend unavailable"),
          description: tf('backend', st.scope.profile, errText(st.error))
        })
      }
      const data = st.data
      return jsxs('div', {
        className: 'flex flex-col',
        children: [
          jsx(PageTabs, { page }),
          jsxs('div', {
            className: 'mb-2 flex flex-wrap items-center gap-2',
            children: [
              jsx(Badge, { variant: 'outline', children: `${tr('profile')}: ${st.scope.profile}` }),
              data.plugin_enabled
                ? jsx(Badge, { variant: 'success', children: tr("enabled") })
                : jsx(Badge, { variant: 'warn', children: tr("not enabled in this profile") }),
              st.busy ? jsx(Muted, { children: tr("Saving…") }) : null
            ]
          }),
          st.error ? jsx('div', { className: 'mb-2 text-xs text-destructive', children: st.error }) : null,
          render(data, st)
        ]
      })
    }

    function TextRow({ title, description, value, onSave, placeholder }) {
      const [draft, setDraft] = useState(value || '')
      useEffect(() => setDraft(value || ''), [value])
      const commit = () => { if (draft !== (value || '')) onSave(draft) }
      return jsx(ListRow, {
        title,
        description,
        action: jsx(Input, {
          value: draft, placeholder, className: 'w-64',
          onChange: e => setDraft(e.target.value),
          onBlur: commit,
          onKeyDown: e => { if (e.key === 'Enter') commit() }
        })
      })
    }

    // ── General ─────────────────────────────────────────────────────────────────────────────
    function NumberRow({ title, description, value, onSave, min, max }) {
      const [draft, setDraft] = useState(String(value))
      useEffect(() => setDraft(String(value)), [value])
      const commit = () => {
        const n = Number(draft)
        if (Number.isFinite(n) && n !== value) onSave(Math.max(min, Math.min(max, Math.round(n))))
        else setDraft(String(value))
      }
      return jsx(ListRow, {
        title,
        description,
        action: jsx(Input, {
          type: 'number', min, max, value: draft, className: 'w-24',
          onChange: e => setDraft(e.target.value),
          onBlur: commit,
          onKeyDown: e => { if (e.key === 'Enter') commit() }
        })
      })
    }

    function General() {
      return jsx(Frame, {
        render: (data, st) => {
          const s = data.settings
          const providers = Object.keys(s.scope)
          const models = providers.reduce((n, p) => n + s.scope[p].length, 0)
          return jsxs('div', {
            children: [
              jsx(ListRow, {
                title: 'Language / 언어',
                description: tr("Language of this plugin's screens."),
                action: jsx(SegmentedControl, { options: LANGS, value: $lang.get(), onChange: setLang })
              }),
              jsx(ToggleRow, {
                label: tr("Route each turn"),
                description:
                  tr("Pick a model and reasoning effort per request from the allowed models. Off leaves the session model alone."),
                checked: s.mode === 'auto',
                disabled: st.busy,
                onChange: on => st.save({ mode: on ? 'auto' : 'off' })
              }),
              jsx(ListRow, {
                title: tr('Allowed models'),
                action: jsx(Button, { variant: 'outline', size: 'xs', onClick: () => openPage('allowlist'), children: tr("Edit") }),
                description: models
                  ? tf('summary', providers.length, models) +
                    providers.map(p => `${p} (${s.scope[p].join(', ')})`).join(' · ')
                  : tr("Nothing allowed yet — routing does nothing until you check models under Allowed models.")
              }),
              jsx(ListRow, {
                title: tr("Task classifier"),
                description:
                  tr("Rules: keywords and length, no extra call. Hybrid: asks a model only when rules are unsure. Model: always asks."),
                action: jsx(SegmentedControl, {
                  options: CLASSIFIERS.map(o => ({ ...o, label: tr(o.label) })), value: s.classifier, disabled: st.busy,
                  onChange: v => st.save({ classifier: v })
                })
              }),
              jsx(ToggleRow, {
                label: tr("Move the session across providers"),
                description:
                  tr("Off: models change only within the provider the session is on (public plugin API). On: the session moves to another allowed provider when the route says so. Uses Hermes internals and may break after an update; it falls back to off when they are missing."),
                checked: s.cross_provider,
                disabled: st.busy,
                onChange: on => st.save({ cross_provider: on })
              }),
              jsx(ToggleRow, {
                label: tr("Re-answer soft refusals"),
                description: tr("A short \"I can't help with that\" reply is answered again by another allowed model (without tools)."),
                checked: s.soft_refusal_retry,
                disabled: st.busy,
                onChange: on => st.save({ soft_refusal_retry: on })
              }),
              jsx(NumberRow, {
                title: tr("Cool-down (minutes)"),
                description: tr("A model that fails twice in a row moves to the back of every route for this long."),
                value: s.cooldown_minutes, min: 0, max: 1440,
                onSave: v => st.save({ cooldown_minutes: v })
              }),
              jsx(NumberRow, {
                title: tr("Models per comparison"),
                description: tr("The most models route_explore runs side by side (one subagent each)."),
                value: s.max_explore, min: 1, max: 8,
                onSave: v => st.save({ max_explore: v })
              }),
              jsx(ToggleRow, {
                label: tr("Redo security answers without code"),
                description:
                  tr("On security turns, an answer asked for a PoC or code but delivered prose is retried on another allowed model with an output contract."),
                checked: s.degraded_retry,
                disabled: st.busy,
                onChange: on => st.save({ degraded_retry: on })
              }),
              jsx(ToggleRow, {
                label: tr("Security preamble"),
                description:
                  tr("On security turns, put authorization, an output contract (runnable PoC, exact offsets, no hedging) and a CVE workflow into the system prompt. Does not remove provider guardrails."),
                checked: s.security_preamble,
                disabled: st.busy,
                onChange: on => st.save({ security_preamble: on })
              }),
              jsx(TextRow, {
                title: tr("Rules of engagement"),
                description: tr("Target, period, scope, contacts. Models refuse less when authorization is explicit."),
                value: s.security_roe, placeholder: tr("e.g. Target 10.10.0.0/24, 2026-11-01..11-30, authorized"),
                onSave: v => st.save({ security_roe: v })
              }),
              jsx(ToggleRow, {
                label: tr("Generic guardrail errors"),
                description:
                  tr("Treat \"content filter / safety / blocked by policy / cyber\" in a provider error as a guardrail and move to the next allowed model."),
                checked: s.security_guardrail_defaults,
                disabled: st.busy,
                onChange: on => st.save({ security_guardrail_defaults: on })
              }),
              jsx(ToggleRow, {
                label: tr("Guardrail round-trip"),
                description:
                  tr("After a refusal, rephrase the request (framing terms only; CVE ids, versions and offsets stay verbatim), retry, then restore the answer to your terms. Escalates L1 wording -> L2 walkthrough -> L3 analysis/code split."),
                checked: s.security_sanitize,
                disabled: st.busy,
                onChange: on => st.save({ security_sanitize: on })
              }),
              jsx(NumberRow, {
                title: tr("Highest sanitize level"),
                description: tr("1: wording only. 2: + walkthrough structure. 3: + split into analysis and code."),
                value: s.security_sanitize_max_level, min: 1, max: 3,
                onSave: v => st.save({ security_sanitize_max_level: v })
              }),
              jsx(ToggleRow, {
                label: tr("Retry the refused model first"),
                description: tr("Use the next level on the model that refused before switching to another one."),
                checked: s.security_sanitize_retry_same_model,
                disabled: st.busy,
                onChange: on => st.save({ security_sanitize_retry_same_model: on })
              }),
              jsx(ToggleRow, {
                label: tr("Lock facts"),
                description:
                  tr("Never substitute CVE ids, versions, offsets, hashes or symbols; if the answer drops one, retry with the missing facts listed."),
                checked: s.security_fact_lock,
                disabled: st.busy,
                onChange: on => st.save({ security_fact_lock: on })
              }),
              jsx(ToggleRow, {
                label: tr("Model-assisted rephrase"),
                description:
                  tr("At L2+, let a model reword the request first (one structured call). Deterministic rules remain the fallback."),
                checked: s.security_sanitize_llm,
                disabled: st.busy,
                onChange: on => st.save({ security_sanitize_llm: on })
              }),
              data.warnings.length
                ? jsxs('div', {
                    className: 'mt-4 flex flex-col gap-1',
                    children: [
                      jsx(Heading, { title: tr("Warnings") }),
                      ...data.warnings.map((w, i) =>
                        jsxs('div', {
                          key: i, className: 'flex items-start gap-2 text-xs',
                          children: [jsx(StatusDot, { tone: 'warn', className: 'mt-1.5' }), jsx('span', { children: w })]
                        }))
                    ]
                  })
                : null
            ]
          })
        }
      })
    }

    // ── Allowed models ──────────────────────────────────────────────────────────────────────
    function AllowlistEditor({ data, st }) {
      const max = data.limits.max_providers
      const saved = data.settings.scope
      const [draft, setDraft] = useState(saved)
      const [adding, setAdding] = useState({})
      useEffect(() => setDraft(saved), [JSON.stringify(saved)])

      const providers = [...new Set([...Object.keys(saved), ...Object.keys(data.inventory)])]
      const checked = Object.keys(draft)
      const full = checked.length >= max
      const dirty = JSON.stringify(draft) !== JSON.stringify(saved)

      const toggleProvider = (p, on) => {
        const next = { ...draft }
        if (on) next[p] = saved[p] || []
        else delete next[p]
        setDraft(next)
      }
      const toggleModel = (p, m, on) => {
        const cur = draft[p] || []
        setDraft({ ...draft, [p]: on ? [...cur, m] : cur.filter(x => x !== m) })
      }
      const addModel = p => {
        const m = String(adding[p] || '').trim()
        if (!m) return
        const cur = draft[p] || []
        if (!cur.includes(m)) setDraft({ ...draft, [p]: [...cur, m] })
        setAdding({ ...adding, [p]: '' })
      }
      const save = () => {
        const scope = {}
        for (const p of Object.keys(draft)) if ((draft[p] || []).length) scope[p] = draft[p]
        const allowed = new Set(Object.entries(scope).flatMap(([p, ms]) => ms.map(m => `${p}/${m}`)))
        const routes = {}
        for (const [cls, entries] of Object.entries(data.settings.routes || {})) {
          const keep = entries.filter(e => allowed.has(keyOf(e)))
          if (keep.length) routes[cls] = keep
        }
        st.save({ scope, routes })
      }

      if (!providers.length) {
        return jsx(EmptyState, {
          title: tr("No providers found"),
          description: tr("This profile has no provider with credentials yet. Add one with `hermes model`, then come back.")
        })
      }

      return jsxs('div', {
        children: [
          jsx(Muted, {
            className: 'mb-3',
            children:
              tf('allowIntro', max)
          }),
          ...providers.map(p => {
            const on = p in draft
            const known = [...new Set([...(data.inventory[p] || []), ...(saved[p] || []), ...(draft[p] || [])])]
            const isCurrent = data.current.provider === p
            return jsxs('div', {
              key: p,
              className: 'border-t border-(--ui-stroke-secondary) py-3',
              children: [
                jsxs('label', {
                  className: 'flex cursor-pointer items-center gap-2',
                  children: [
                    jsx(Checkbox, {
                      checked: on,
                      disabled: !on && full,
                      onCheckedChange: v => toggleProvider(p, v === true)
                    }),
                    jsx('span', { className: 'text-sm font-medium', children: p }),
                    isCurrent ? jsx(Badge, { variant: 'muted', children: tr("session provider") }) : null,
                    on ? jsx(Muted, { children: tf('checked', (draft[p] || []).length) }) : null,
                    !on && full ? jsx(Muted, { children: tf('limit', max) }) : null
                  ]
                }),
                on
                  ? jsxs('div', {
                      className: 'mt-2 ml-6 flex flex-col gap-1.5',
                      children: [
                        ...known.map(m =>
                          jsxs('label', {
                            key: m,
                            className: 'flex cursor-pointer items-center gap-2 text-sm',
                            children: [
                              jsx(Checkbox, {
                                checked: (draft[p] || []).includes(m),
                                onCheckedChange: v => toggleModel(p, m, v === true)
                              }),
                              jsx('span', { className: 'font-mono text-xs', children: m }),
                              data.current.provider === p && data.current.model === m
                                ? jsx(Badge, { variant: 'muted', size: 'xs', children: tr('default') })
                                : null
                            ]
                          })),
                        jsxs('div', {
                          className: 'mt-1 flex items-center gap-2',
                          children: [
                            jsx(Input, {
                              placeholder: tr("Add a model id"), className: 'h-7 w-64 text-xs',
                              value: adding[p] || '',
                              onChange: e => setAdding({ ...adding, [p]: e.target.value }),
                              onKeyDown: e => { if (e.key === 'Enter') addModel(p) }
                            }),
                            jsx(Button, { variant: 'outline', size: 'xs', onClick: () => addModel(p), children: tr("Add") })
                          ]
                        }),
                        (draft[p] || []).length === 0
                          ? jsx(Muted, { children: tr("Check at least one model, or this provider is not saved.") })
                          : null
                      ]
                    })
                  : null
              ]
            })
          }),
          jsxs('div', {
            className: 'mt-4 flex items-center gap-2',
            children: [
              jsx(Button, { disabled: !dirty || st.busy, onClick: save, children: tr("Save allowlist") }),
              jsx(Button, { variant: 'ghost', disabled: !dirty || st.busy, onClick: () => setDraft(saved), children: tr("Undo") }),
              dirty ? jsx(Muted, { children: tr("Unsaved changes. Running sessions pick them up on their next turn.") }) : null
            ]
          })
        ]
      })
    }

    // ── Routes ──────────────────────────────────────────────────────────────────────────────
    function RouteRow({ cls, data, st, allowed }) {
      const explicit = (data.settings.routes || {})[cls] || []
      const effective = data.effective_routes[cls] || []
      const first = explicit[0] || effective[0] || ''
      const [model, setModel] = useState(keyOf(first))
      const [effort, setEffort] = useState(effortOf(first) || data.limits.default_effort[cls])
      useEffect(() => {
        setModel(keyOf(first))
        setEffort(effortOf(first) || data.limits.default_effort[cls])
      }, [first])
      const dirty = model !== keyOf(first) || effort !== (effortOf(first) || data.limits.default_effort[cls])
      const save = () => {
        const head = `${model}@${effort}`
        const rest = explicit.filter(e => keyOf(e) !== model)
        st.save({ routes: { ...(data.settings.routes || {}), [cls]: [head, ...rest] } })
      }
      const select = (value, onChange, items, width) =>
        jsxs(Select, {
          value, onValueChange: onChange,
          children: [
            jsx(SelectTrigger, { className: `h-7 ${width} text-xs`, children: jsx(SelectValue, {}) }),
            jsx(SelectContent, { children: items.map(i => jsx(SelectItem, { key: i, value: i, children: i })) })
          ]
        })
      const preferred = data.preferred[cls]
      return jsx(ListRow, {
        title: tr(`${cls[0].toUpperCase()}${cls.slice(1)}`),
        description: tr(CLASS_HELP[cls]),
        below: jsxs('div', {
          className: 'mt-1 flex flex-col gap-0.5',
          children: [
            jsx(Muted, { children: `${tr('Order')}: ${effective.join(' → ') || '—'}` }),
            preferred
              ? jsx(Muted, { children: tf('learned', preferred) })
              : null
          ]
        }),
        action: jsxs('div', {
          className: 'flex flex-wrap items-center gap-2',
          children: [
            select(model, setModel, allowed, 'w-56'),
            select(effort, setEffort, data.limits.efforts, 'w-24'),
            jsx(Button, { size: 'xs', disabled: !dirty || st.busy || !model, onClick: save, children: tr("Save") })
          ]
        })
      })
    }

    function Routes() {
      return jsx(Frame, {
        page: 'routes',
        render: (data, st) => {
          const allowed = Object.entries(data.settings.scope).flatMap(([p, ms]) => ms.map(m => `${p}/${m}`))
          if (!allowed.length) {
            return jsx(EmptyState, {
              title: tr("No allowed models yet"),
              description: tr("Check providers and models under Allowed models first.")
            })
          }
          return jsxs('div', {
            children: [
              jsx(Muted, {
                className: 'mb-2',
                children:
                  tr("Pick the model each kind of task tries first and its reasoning effort. The other allowed models follow automatically as backups when a model is stopped by a guardrail, a refusal or an error.")
              }),
              ...data.limits.task_classes.map(cls => jsx(RouteRow, { key: cls, cls, data, st, allowed }))
            ]
          })
        }
      })
    }

    // ── Stats ───────────────────────────────────────────────────────────────────────────────
    function Stats() {
      const [confirm, setConfirm] = useState(false)
      return jsx(Frame, {
        page: 'stats',
        render: (data, st) => {
          const rows = Object.entries(data.health).sort(([a], [b]) => a.localeCompare(b))
          const prefs = Object.entries(data.preferred)
          return jsxs('div', {
            children: [
              jsx(Heading, { title: tr("Models"), children: tr("Outcomes the router recorded in this profile.") }),
              rows.length
                ? jsx('div', {
                    className: 'flex flex-col',
                    children: rows.map(([key, h]) => {
                      const cooling = data.cooling[key]
                      const tone = cooling ? 'bad' : (h.streak || 0) > 0 ? 'warn' : 'good'
                      return jsx(ListRow, {
                        key,
                        title: jsxs('span', {
                          className: 'flex items-center gap-2',
                          children: [jsx(StatusDot, { tone }), jsx('span', { className: 'font-mono text-xs', children: key })]
                        }),
                        description:
                          tf('stat', h.ok || 0, h.fail || 0, cooling, h.last_reason)
                      })
                    })
                  })
                : jsx(Muted, { children: tr("No outcomes yet.") }),
              jsx(Heading, { title: tr("Learned preferences"), children: tr("Set by route_explore comparisons or /route prefer.") }),
              prefs.length
                ? jsx('div', {
                    children: prefs.map(([cls, cand]) =>
                      jsx(ListRow, {
                        key: cls, title: cls, description: cand,
                        action: jsx(Button, {
                          variant: 'outline', size: 'xs', disabled: st.busy,
                          onClick: () => st.call('/prefer', 'POST', { task_class: cls, candidate: null }),
                          children: tr("Clear")
                        })
                      }))
                  })
                : jsx(Muted, { children: tr("None.") }),
              jsxs('div', {
                className: 'mt-4 flex items-center gap-2',
                children: [
                  jsx(Button, { variant: 'outline', size: 'xs', onClick: () => st.refetch(), children: tr("Refresh") }),
                  jsx(Button, {
                    variant: confirm ? 'destructive' : 'outline', size: 'xs', disabled: st.busy,
                    onClick: async () => {
                      if (!confirm) return setConfirm(true)
                      setConfirm(false)
                      await st.call('/reset', 'POST', {})
                    },
                    children: confirm ? tr("Click again to forget everything") : tr("Reset stats and preferences")
                  })
                ]
              })
            ]
          })
        }
      })
    }

    ctx.onDispose(() => {
      disposed = true
      void queryClient.cancelQueries({ queryKey: root })
      queryClient.removeQueries({ queryKey: root })
    })

    let disposeSettings = null
    function mountSettings() {
      if (disposed) return
      disposeSettings?.()
      disposeSettings = ctx.registerSettingsPage?.({
        id: 'settings',
        title: 'Model Router',
        icon: 'type-hierarchy',
        render: () => jsx(General, {}),
        children: [
          { id: 'allowlist', title: tr('Allowed models'), render: () => jsx(Frame, { page: 'allowlist', render: (data, st) => jsx(AllowlistEditor, { data, st }) }) },
          { id: 'routes', title: tr('Routes'), render: () => jsx(Routes, {}) },
          { id: 'stats', title: tr('Stats'), render: () => jsx(Stats, {}) }
        ]
      })
    }
    mountSettings()
    const unlisten = $lang.listen(() => mountSettings())
    ctx.onDispose(() => {
      unlisten()
      disposeSettings?.()
    })
  }
}
