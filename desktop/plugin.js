// Model Router — Desktop half: Settings ▸ Plugins ▸ Model Router (0.4.0, tier ladder).
//
//   Model Router        on/off, retry options, time budget, rules of engagement, warnings
//   ├ Tiers             the ladder a blocked reply climbs: models per tier, their order
//   └ Stats             per-model outcomes from the attempt log, promotions, reset
//
// Every read and write goes to this package's backend (dashboard/plugin_api.py) through ctx.rest,
// which the app scopes to the active profile, so each profile keeps its own ladder.
// Plain ESM with jsx() calls: Desktop loads this file uncompiled.

import {
  Badge, Button, EmptyState, ErrorState, Input, ListRow, Loader, SegmentedControl, Select,
  SelectContent, SelectItem, SelectTrigger, SelectValue, StatusDot, ToggleRow, host, pluginSettingsHref,
  queryClient, useQuery, useValue, atom
} from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'model-router'
// GET /state shape this page understands (dashboard/plugin_api.py STATE_SCHEMA).
const STATE_SCHEMA = 3
const errText = e => (e && (e.detail || e.message)) || String(e)
// Inline styles, not utility classes: Desktop loads this file uncompiled, so only classes the app's
// own CSS already has take effect. Long provider/model names must stay inside their box.
const CLIP = { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }
const clipped = text => jsx('span', { style: { ...CLIP, display: 'block' }, title: text, children: text })
const SUBAGENT_TOOLSETS = [
  { id: 'web', label: 'Web' },
  { id: 'web+file', label: 'Web + files' },
  { id: 'inherit', label: 'All (terminal)' }
]
const HOLD_OPTIONS = [
  { id: 'auto', label: 'Auto' },
  { id: 'on', label: 'On' },
  { id: 'off', label: 'Off' }
]

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
  "General": "일반",
  "Tiers": "티어",
  "Stats": "통계",
  "profile": "프로필",
  "enabled": "활성",
  "not enabled in this profile": "이 프로필에서 비활성",
  "Saving…": "저장 중…",
  "Model Router backend unavailable": "Model Router 백엔드를 사용할 수 없습니다",
  "Model Router backend is out of date": "Model Router 백엔드가 예전 버전입니다",
  "The gateway is still serving the previous version of this plugin's backend. Restart it (hermes gateway restart), then reopen this page.":
    "게이트웨이가 아직 이 플러그인 백엔드의 예전 버전을 쓰고 있습니다. 게이트웨이를 재시작(hermes gateway restart)한 뒤 이 페이지를 다시 여세요.",
  "Language of this plugin's screens.": "이 플러그인 화면의 언어입니다.",
  "Climb the ladder on blocked replies": "막힌 답에 티어 사다리 사용",
  "The profile's own model answers every turn. When a reply is blocked (a guardrail, a refusal, an answer without the requested code, a model error), the ladder's models are asked instead.":
    "평소에는 프로필 기본 모델이 그대로 답합니다. 답이 막히면(가드레일, 거절, 요청한 코드가 빠진 답, 모델 오류) 사다리의 모델들에게 대신 묻습니다.",
  "Tier ladder": "티어 사다리",
  "Edit": "편집",
  "No tiers yet — nothing happens until you put models on the ladder.": "아직 티어가 없습니다 — 사다리에 모델을 넣기 전에는 아무 일도 하지 않습니다.",
  "Ask a tier's models in parallel": "티어 안의 모델을 동시에 묻기",
  "Off asks them one after another and stops at the first good answer.": "끄면 하나씩 차례로 묻고 첫 정상 답에서 멈춥니다.",
  "Combine good answers": "정상 답 종합",
  "When two or more models of a tier answer, one more call merges their answers. Skipped when time is short.":
    "한 티어에서 둘 이상 정상 답이 나오면 한 번 더 호출해 합칩니다. 시간이 부족하면 건너뜁니다.",
  "Enriched retry": "보강 재시도",
  "After a tier fails on the request as written, ask it once more with your rules of engagement, an output contract when code was asked for, and the facts to keep. Framing terms are reworded and restored in the answer.":
    "원문 요청에 티어가 모두 실패하면, 교전 규칙(ROE), 코드 요청일 때의 산출물 요구, 지켜야 할 사실을 붙여 한 번 더 묻습니다. 표현 순화는 답에서 원래대로 되돌립니다.",
  "Move models that keep winning up": "자주 이기는 모델 승격",
  "Learned from the attempt log. Never rewrites your tiers and never puts a model below where you placed it.":
    "시도 기록으로 학습합니다. 설정한 티어를 고치지 않고, 넣은 자리보다 아래로 내리지 않습니다.",
  "Retry tool-using turns with subagents": "도구를 쓴 턴은 서브에이전트로 재시도",
  "When the blocked turn's model had called tools, retry with Hermes subagents that can use tools, instead of direct calls. Slower (a full agent run each), creates child sessions, and runs without asking you.":
    "막힌 턴에서 원래 모델이 도구를 썼다면, 직접 호출 대신 도구를 쓸 수 있는 Hermes 서브에이전트로 재시도합니다. 느리고(매번 에이전트 전체 실행), 자식 세션이 생기며, 사용자에게 묻지 않고 실행됩니다.",
  "Tools a retry subagent may use": "재시도 서브에이전트가 쓸 도구",
  "Web: search and read pages only. Web + files: also writes and patches files. All: everything the session has, terminal included — commands run without your review.":
    "웹: 검색과 페이지 읽기만. 웹 + 파일: 파일 쓰기·수정까지. 전체: 세션이 가진 모든 도구(터미널 포함) — 명령이 사용자 확인 없이 실행됩니다.",
  "Web": "웹",
  "Web + files": "웹 + 파일",
  "All (terminal)": "전체(터미널)",
  "Hold replies": "답 보류(hold_stream)",
  "Auto: on when tiers are set. Replies that end on a provider error can only be replaced while held. Holding turns off streaming. Restart the gateway to apply.":
    "자동: 티어가 있으면 켭니다. 프로바이더 오류로 끝난 답은 보류 중일 때만 바꿀 수 있습니다. 보류하면 스트리밍이 꺼집니다. 게이트웨이를 재시작해야 적용됩니다.",
  "Auto": "자동",
  "On": "켬",
  "Off": "끔",
  "Time budget": "시간 예산",
  "Seconds the ladder may spend, or auto (a few seconds inside the hook timeout).": "사다리가 쓸 수 있는 시간(초), 또는 auto(훅 시간 제한보다 몇 초 짧게).",
  "Rules of engagement": "교전 규칙(ROE)",
  "Target, scope, period, approver. Quoted in enriched retries; with none set, no authorization is ever claimed.":
    "대상, 범위, 기간, 승인자. 보강 재시도에 그대로 인용됩니다. 비워 두면 권한에 대한 말은 전혀 넣지 않습니다.",
  "e.g. Target 10.10.0.0/24 lab, 2026-11-01..30, approved SEC-123": "예: 대상 10.10.0.0/24 랩, 2026-11-01..30, 승인 SEC-123",
  "Generic guardrail errors": "일반 가드레일 오류",
  "Treat \"content filter / safety / blocked by policy / cyber\" in a provider error as a guardrail block.":
    "프로바이더 오류에 \"content filter / safety / blocked by policy / cyber\"가 있으면 가드레일 차단으로 봅니다.",
  "Models per comparison": "비교당 모델 수",
  "The most models route_explore runs side by side (one subagent each).": "route_explore가 한 번에 나란히 돌리는 최대 모델 수(모델마다 서브에이전트 하나).",
  "Warnings": "경고",
  "Tier": "티어",
  "Provider": "프로바이더",
  "Model": "모델",
  "Pick a provider first": "먼저 프로바이더를 고르세요",
  "or": "또는",
  "type provider/model": "provider/model 직접 입력",
  "Add": "추가",
  "Add tier": "티어 추가",
  "Remove tier": "티어 삭제",
  "Move up": "위로",
  "Move down": "아래로",
  "Remove": "삭제",
  "Save ladder": "사다리 저장",
  "Undo": "되돌리기",
  "Unsaved changes. A running gateway reads tiers on the next turn.": "저장되지 않은 변경이 있습니다. 실행 중인 게이트웨이는 다음 턴부터 새 티어를 씁니다.",
  "empty tiers are dropped on save": "빈 티어는 저장할 때 빠집니다",
  "default": "기본",
  "As used now (with promotions)": "지금 실제 순서(승격 포함)",
  "(empty — its models were promoted)": "(비어 있음 — 모델이 승격됨)",
  "No providers found": "프로바이더가 없습니다",
  "This profile has no provider with credentials yet. Add one with `hermes model`, then come back.":
    "이 프로필에는 자격 증명이 있는 프로바이더가 아직 없습니다. `hermes model`로 추가한 뒤 다시 오세요.",
  "Models": "모델",
  "Outcomes recorded when the ladder ran, last 20 per model.": "사다리가 돌 때 기록된 결과, 모델별 최근 20회.",
  "No ladder attempts recorded yet.": "아직 기록된 사다리 시도가 없습니다.",
  "as written": "원문",
  "enriched": "보강",
  "last failure": "마지막 실패",
  "Refresh": "새로고침",
  "Reset promotions": "승격 초기화",
  "Click again to forget promotions": "한 번 더 누르면 승격을 지웁니다",
  "The attempt log is kept; only learned promotions are cleared.": "시도 기록은 남고, 학습된 승격만 지워집니다."
}
const tr = s => ($lang.get() === 'ko' && KO[s]) || s
const FN = {
  backend: {
    en: (p, e) => `Profile "${p}": install the model-router plugin and enable it for this profile (hermes -p ${p} plugins enable model-router), then restart its gateway. (${e})`,
    ko: (p, e) => `프로필 "${p}": model-router 플러그인을 설치하고 이 프로필에서 활성화한 뒤(hermes -p ${p} plugins enable model-router) 게이트웨이를 재시작하세요. (${e})`
  },
  ladderSummary: {
    en: tiers => tiers.map((t, i) => `${i + 1}: ${t.map(m => m.key).join(', ') || '—'}`).join(' · '),
    ko: tiers => tiers.map((t, i) => `${i + 1}: ${t.map(m => m.key).join(', ') || '—'}`).join(' · ')
  },
  time: {
    en: (timeout, budget) => `Hook timeout ${timeout || 'off'}s, ladder budget ${budget}s.` + (timeout > 0 && timeout < 120 ? ' Raise plugins.hook_callback_timeout to 180–300 for longer answers.' : ''),
    ko: (timeout, budget) => `훅 시간 제한 ${timeout || '없음'}초, 사다리 예산 ${budget}초.` + (timeout > 0 && timeout < 120 ? ' 긴 답을 위해 plugins.hook_callback_timeout을 180–300으로 올리세요.' : '')
  },
  tierHelp: {
    en: n => (n === 1 ? 'Asked first.' : `Asked after tier ${n - 1} fails.`),
    ko: n => (n === 1 ? '가장 먼저 묻습니다.' : `${n - 1}티어가 실패하면 묻습니다.`)
  },
  tooMany: {
    en: max => `More than ${max} models run in parallel within one time budget.`,
    ko: max => `${max}개가 넘는 모델이 한 시간 예산 안에서 동시에 돕니다.`
  },
  counts: {
    en: r => (r && r.samples ? `${r.ok}/${r.samples} ok` + ['blocked', 'degraded', 'error'].filter(o => r[o]).map(o => `, ${r[o]} ${o}`).join('') : '—'),
    ko: r => (r && r.samples ? `${r.samples}회 중 ${r.ok}회 성공` + [['blocked', '막힘'], ['degraded', '불완전'], ['error', '오류']].filter(([o]) => r[o]).map(([o, l]) => `, ${l} ${r[o]}`).join('') : '—')
  },
  intro: {
    en: () => 'A blocked reply climbs the ladder: tier 1\'s models are asked first, all at once, then tier 2, and so on. Each tier is asked as written, then once more enriched. Within a tier, the order is the priority. Nothing off the ladder is ever called.',
    ko: () => '막힌 답은 사다리를 오릅니다: 1티어 모델들에게 동시에 먼저 묻고, 실패하면 2티어, 그다음… 티어마다 원문으로 한 번, 보강해서 한 번 묻습니다. 티어 안의 순서가 우선순위입니다. 사다리 밖의 모델은 절대 부르지 않습니다.'
  }
}
const tf = (name, ...a) => FN[name][$lang.get() === 'ko' ? 'ko' : 'en'](...a)

const PAGES = [
  { id: '', label: 'General' },
  { id: 'tiers', label: 'Tiers' },
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
      // Hermes loads the backend once per plugin host process, so right after an update this page
      // can meet the previous version's backend. Say so instead of failing on missing fields.
      if (!data || data.schema !== STATE_SCHEMA) {
        return jsx(ErrorState, {
          title: tr("Model Router backend is out of date"),
          description: tr("The gateway is still serving the previous version of this plugin's backend. Restart it (hermes gateway restart), then reopen this page.")
        })
      }
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

    function TextRow({ title, description, value, onSave, placeholder, width = 'w-64' }) {
      const [draft, setDraft] = useState(value || '')
      useEffect(() => setDraft(value || ''), [value])
      const commit = () => { if (draft !== (value || '')) onSave(draft) }
      return jsx(ListRow, {
        title,
        description,
        action: jsx(Input, {
          value: draft, placeholder, className: width,
          onChange: e => setDraft(e.target.value),
          onBlur: commit,
          onKeyDown: e => { if (e.key === 'Enter') commit() }
        })
      })
    }

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

    // ── General ─────────────────────────────────────────────────────────────────────────────
    function General() {
      return jsx(Frame, {
        render: (data, st) => {
          const s = data.settings
          const toggle = (key, label, description) =>
            jsx(ToggleRow, {
              label: tr(label), description: tr(description), checked: s[key], disabled: st.busy,
              onChange: on => st.save({ [key]: on })
            })
          return jsxs('div', {
            children: [
              jsx(ListRow, {
                title: 'Language / 언어',
                description: tr("Language of this plugin's screens."),
                action: jsx(SegmentedControl, { options: LANGS, value: $lang.get(), onChange: setLang })
              }),
              jsx(ToggleRow, {
                label: tr("Climb the ladder on blocked replies"),
                description: tr("The profile's own model answers every turn. When a reply is blocked (a guardrail, a refusal, an answer without the requested code, a model error), the ladder's models are asked instead."),
                checked: s.mode === 'auto',
                disabled: st.busy,
                onChange: on => st.save({ mode: on ? 'auto' : 'off' })
              }),
              jsx(ListRow, {
                title: tr('Tier ladder'),
                action: jsx(Button, { variant: 'outline', size: 'xs', onClick: () => openPage('tiers'), children: tr("Edit") }),
                description: data.ladder.length
                  ? tf('ladderSummary', data.ladder)
                  : tr("No tiers yet — nothing happens until you put models on the ladder.")
              }),
              toggle('parallel', "Ask a tier's models in parallel", "Off asks them one after another and stops at the first good answer."),
              toggle('synthesize', "Combine good answers", "When two or more models of a tier answer, one more call merges their answers. Skipped when time is short."),
              toggle('enrich_on_retry', "Enriched retry", "After a tier fails on the request as written, ask it once more with your rules of engagement, an output contract when code was asked for, and the facts to keep. Framing terms are reworded and restored in the answer."),
              toggle('auto_promote', "Move models that keep winning up", "Learned from the attempt log. Never rewrites your tiers and never puts a model below where you placed it."),
              toggle('subagent_retry', "Retry tool-using turns with subagents", "When the blocked turn's model had called tools, retry with Hermes subagents that can use tools, instead of direct calls. Slower (a full agent run each), creates child sessions, and runs without asking you."),
              s.subagent_retry
                ? jsx(ListRow, {
                    title: tr("Tools a retry subagent may use"),
                    description: tr("Web: search and read pages only. Web + files: also writes and patches files. All: everything the session has, terminal included — commands run without your review."),
                    action: jsx(SegmentedControl, {
                      options: SUBAGENT_TOOLSETS.map(o => ({ ...o, label: tr(o.label) })), value: s.subagent_toolsets, disabled: st.busy,
                      onChange: v => st.save({ subagent_toolsets: v })
                    })
                  })
                : null,
              jsx(ListRow, {
                title: tr("Hold replies"),
                description: tr("Auto: on when tiers are set. Replies that end on a provider error can only be replaced while held. Holding turns off streaming. Restart the gateway to apply."),
                action: jsx(SegmentedControl, {
                  options: HOLD_OPTIONS.map(o => ({ ...o, label: tr(o.label) })), value: s.hold_stream, disabled: st.busy,
                  onChange: v => st.save({ hold_stream: v })
                })
              }),
              jsx(TextRow, {
                title: tr("Time budget"),
                description: [tr("Seconds the ladder may spend, or auto (a few seconds inside the hook timeout)."), ' ',
                  tf('time', data.time.hook_timeout, data.time.budget)].join(''),
                value: s.retry_budget_seconds, placeholder: 'auto', width: 'w-24',
                onSave: v => st.save({ retry_budget_seconds: v || 'auto' })
              }),
              jsx(TextRow, {
                title: tr("Rules of engagement"),
                description: tr("Target, scope, period, approver. Quoted in enriched retries; with none set, no authorization is ever claimed."),
                value: s.security_roe, placeholder: tr("e.g. Target 10.10.0.0/24 lab, 2026-11-01..30, approved SEC-123"),
                onSave: v => st.save({ security_roe: v })
              }),
              toggle('security_guardrail_defaults', "Generic guardrail errors", "Treat \"content filter / safety / blocked by policy / cyber\" in a provider error as a guardrail block."),
              jsx(NumberRow, {
                title: tr("Models per comparison"),
                description: tr("The most models route_explore runs side by side (one subagent each)."),
                value: s.max_explore, min: 1, max: 8,
                onSave: v => st.save({ max_explore: v })
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

    // ── Tiers ───────────────────────────────────────────────────────────────────────────────
    function TierEditor({ data, st }) {
      const saved = data.settings.tiers
      const [draft, setDraft] = useState(saved.length ? saved : [[]])
      const [prov, setProv] = useState({})     // tier -> provider picked in its "add" row
      const [model, setModel] = useState({})   // tier -> model picked for that provider
      const [typed, setTyped] = useState({})
      useEffect(() => setDraft(saved.length ? saved : [[]]), [JSON.stringify(saved)])

      // provider/model: the provider is the first segment; the model may contain "/" itself.
      const split = key => [key.slice(0, key.indexOf('/')), key.slice(key.indexOf('/') + 1)]
      const placed = new Set(draft.flat())
      const catalog = {}
      for (const [p, ms] of Object.entries(data.inventory)) catalog[p] = [...ms]
      for (const key of saved.flat()) {
        const [p, m] = split(key)
        catalog[p] = catalog[p] || []
        if (!catalog[p].includes(m)) catalog[p].push(m)
      }
      const providers = Object.keys(catalog)
      const freeModels = p => (catalog[p] || []).filter(m => !placed.has(`${p}/${m}`))
      const known = providers.length
      const cleaned = draft.filter(t => t.length)
      const dirty = JSON.stringify(cleaned) !== JSON.stringify(saved)
      const isDefault = key => key === `${data.current.provider}/${data.current.model}`
      const promoted = data.promotions || {}

      const setTier = (i, models) => setDraft(draft.map((t, j) => (j === i ? models : t)))
      const move = (i, k, by) => {
        const models = [...draft[i]]
        const [m] = models.splice(k, 1)
        models.splice(k + by, 0, m)
        setTier(i, models)
      }
      const add = (i, key) => {
        const k = String(key || '').trim()
        if (!k || !k.includes('/') || placed.has(k)) return
        setTier(i, [...draft[i], k])
        setModel({ ...model, [i]: '' })
        setTyped({ ...typed, [i]: '' })
      }
      const pickProvider = (i, p) => {
        setProv({ ...prov, [i]: p })
        setModel({ ...model, [i]: '' })
      }

      if (!known && !draft.flat().length) {
        return jsx(EmptyState, {
          title: tr("No providers found"),
          description: tr("This profile has no provider with credentials yet. Add one with `hermes model`, then come back.")
        })
      }

      const tierBox = (models, i) =>
        jsxs('div', {
          key: i,
          className: 'border-t border-(--ui-stroke-secondary) py-3',
          children: [
            jsxs('div', {
              className: 'flex items-center gap-2',
              children: [
                jsx('span', { className: 'text-sm font-semibold', children: `${tr('Tier')} ${i + 1}` }),
                jsx(Muted, { children: tf('tierHelp', i + 1) }),
                models.length > data.limits.tier_size_hint ? jsx(Badge, { variant: 'warn', size: 'xs', children: tf('tooMany', data.limits.tier_size_hint) }) : null,
                jsx('div', { className: 'flex-1' }),
                draft.length > 1
                  ? jsx(Button, { variant: 'ghost', size: 'xs', onClick: () => setDraft(draft.filter((_, j) => j !== i)), children: tr("Remove tier") })
                  : null
              ]
            }),
            jsx('div', {
              className: 'mt-2 ml-2 flex flex-col gap-1',
              children: models.map((key, k) =>
                jsxs('div', {
                  key,
                  className: 'flex items-center gap-2 text-sm',
                  children: [
                    jsx('span', { className: 'w-5 text-right text-xs text-(--ui-text-tertiary)', children: `${k + 1}.` }),
                    jsx('span', { className: 'font-mono text-xs', style: CLIP, title: key, children: key }),
                    isDefault(key) ? jsx(Badge, { variant: 'muted', size: 'xs', children: tr('default') }) : null,
                    promoted[key] ? jsx(Badge, { variant: 'success', size: 'xs', children: `↑${promoted[key]}` }) : null,
                    jsx('div', { className: 'flex-1' }),
                    jsx(Button, { variant: 'ghost', size: 'xs', disabled: k === 0, title: tr('Move up'), onClick: () => move(i, k, -1), children: '↑' }),
                    jsx(Button, { variant: 'ghost', size: 'xs', disabled: k === models.length - 1, title: tr('Move down'), onClick: () => move(i, k, 1), children: '↓' }),
                    jsx(Button, { variant: 'ghost', size: 'xs', title: tr('Remove'), onClick: () => setTier(i, models.filter(m => m !== key)), children: '✕' })
                  ]
                }))
            }),
            jsxs('div', {
              className: 'mt-2 ml-2 flex flex-wrap items-center gap-2',
              children: [
                jsxs(Select, {
                  value: prov[i] || '', onValueChange: v => pickProvider(i, v),
                  children: [
                    jsx(SelectTrigger, {
                      className: 'h-7 text-xs', style: { width: 240, maxWidth: '100%', ...CLIP },
                      title: prov[i] || undefined,
                      children: jsx(SelectValue, {
                        placeholder: tr("Provider"),
                        children: prov[i] ? clipped(`${prov[i]} (${freeModels(prov[i]).length})`) : undefined
                      })
                    }),
                    jsx(SelectContent, {
                      children: providers.map(p =>
                        jsx(SelectItem, { key: p, value: p, children: `${p} (${freeModels(p).length})` }))
                    })
                  ]
                }),
                jsxs(Select, {
                  value: model[i] || '', disabled: !prov[i] || !freeModels(prov[i]).length,
                  onValueChange: v => setModel({ ...model, [i]: v }),
                  children: [
                    jsx(SelectTrigger, {
                      className: 'h-7 text-xs', style: { width: 300, maxWidth: '100%', ...CLIP },
                      title: model[i] || undefined,
                      children: jsx(SelectValue, {
                        placeholder: prov[i] ? tr("Model") : tr("Pick a provider first"),
                        children: model[i] ? clipped(model[i]) : undefined
                      })
                    }),
                    jsx(SelectContent, {
                      children: (prov[i] ? freeModels(prov[i]) : []).map(m =>
                        jsx(SelectItem, { key: m, value: m, children: m }))
                    })
                  ]
                }),
                jsx(Button, {
                  variant: 'outline', size: 'xs', disabled: !prov[i] || !model[i],
                  onClick: () => add(i, `${prov[i]}/${model[i]}`), children: tr("Add")
                }),
                jsx(Muted, { children: tr("or") }),
                jsx(Input, {
                  placeholder: tr("type provider/model"), className: 'h-7 w-56 text-xs',
                  value: typed[i] || '',
                  onChange: e => setTyped({ ...typed, [i]: e.target.value }),
                  onKeyDown: e => { if (e.key === 'Enter') add(i, typed[i]) }
                }),
                jsx(Button, { variant: 'outline', size: 'xs', onClick: () => add(i, typed[i]), children: tr("Add") })
              ]
            })
          ]
        })

      return jsxs('div', {
        children: [
          jsx(Muted, { className: 'mb-3', children: tf('intro') }),
          ...draft.map(tierBox),
          jsx(Button, { variant: 'outline', size: 'xs', className: 'mt-1 self-start', onClick: () => setDraft([...draft, []]), children: tr("Add tier") }),
          jsxs('div', {
            className: 'mt-4 flex flex-wrap items-center gap-2',
            children: [
              jsx(Button, { disabled: !dirty || st.busy, onClick: () => st.save({ tiers: cleaned }), children: tr("Save ladder") }),
              jsx(Button, { variant: 'ghost', disabled: !dirty || st.busy, onClick: () => setDraft(saved.length ? saved : [[]]), children: tr("Undo") }),
              dirty ? jsx(Muted, { children: `${tr("Unsaved changes. A running gateway reads tiers on the next turn.")} (${tr("empty tiers are dropped on save")})` }) : null
            ]
          }),
          data.ladder.length && Object.keys(promoted).length
            ? jsxs('div', {
                children: [
                  jsx(Heading, { title: tr("As used now (with promotions)") }),
                  ...data.ladder.map((tier, i) =>
                    jsx(Muted, {
                      key: i,
                      children: `${tr('Tier')} ${i + 1}: ` + (tier.map(m => (m.promoted ? `${m.key} ↑${m.promoted}` : m.key)).join(', ') || tr("(empty — its models were promoted)"))
                    }))
                ]
              })
            : null
        ]
      })
    }

    // ── Stats ───────────────────────────────────────────────────────────────────────────────
    function Stats() {
      const [confirm, setConfirm] = useState(false)
      return jsx(Frame, {
        page: 'stats',
        render: (data, st) => {
          const rows = Object.entries(data.stats).sort(([a], [b]) => a.localeCompare(b))
          const promoted = data.promotions || {}
          return jsxs('div', {
            children: [
              jsx(Heading, { title: tr("Models"), children: tr("Outcomes recorded when the ladder ran, last 20 per model.") }),
              rows.length
                ? jsx('div', {
                    className: 'flex flex-col',
                    children: rows.map(([key, r]) => {
                      const plain = r.plain || {}
                      const rate = plain.samples ? plain.ok / plain.samples : null
                      const tone = rate === null ? 'warn' : rate >= 0.6 ? 'good' : rate >= 0.4 ? 'warn' : 'bad'
                      return jsx(ListRow, {
                        key,
                        title: jsxs('span', {
                          className: 'flex items-center gap-2',
                          children: [
                            jsx(StatusDot, { tone }),
                            jsx('span', { className: 'font-mono text-xs', children: key }),
                            promoted[key] ? jsx(Badge, { variant: 'success', size: 'xs', children: `↑${promoted[key]}` }) : null
                          ]
                        }),
                        description: `${tr('as written')}: ${tf('counts', r.plain)} · ${tr('enriched')}: ${tf('counts', r.enrich)}` +
                          (r.last_failure ? ` · ${tr('last failure')}: ${r.last_failure}` : '')
                      })
                    })
                  })
                : jsx(Muted, { children: tr("No ladder attempts recorded yet.") }),
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
                    children: confirm ? tr("Click again to forget promotions") : tr("Reset promotions")
                  }),
                  jsx(Muted, { children: tr("The attempt log is kept; only learned promotions are cleared.") })
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
          { id: 'tiers', title: tr('Tiers'), render: () => jsx(Frame, { page: 'tiers', render: (data, st) => jsx(TierEditor, { data, st }) }) },
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
