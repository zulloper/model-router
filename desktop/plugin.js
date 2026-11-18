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
  queryClient, useQuery, useValue
} from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'model-router'
const CLASS_HELP = {
  quick: 'Short lookups, translation, summaries',
  standard: 'Everyday writing and explanations',
  code: 'Writing, fixing and reviewing code',
  deep: 'Hard reasoning, design, analysis, long tasks'
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
        children: p.label
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
      if (st.isLoading) return jsx('div', { className: 'flex justify-center p-6', children: jsx(Loader, {}) })
      if (st.isError) {
        return jsx(ErrorState, {
          title: 'Model Router backend unavailable',
          description:
            `Profile "${st.scope.profile}": install the model-router plugin and enable it for this profile ` +
            `(hermes -p ${st.scope.profile} plugins enable model-router), then restart its gateway. ` +
            `(${errText(st.error)})`
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
              jsx(Badge, { variant: 'outline', children: `profile: ${st.scope.profile}` }),
              data.plugin_enabled
                ? jsx(Badge, { variant: 'success', children: 'enabled' })
                : jsx(Badge, { variant: 'warn', children: 'not enabled in this profile' }),
              st.busy ? jsx(Muted, { children: 'Saving…' }) : null
            ]
          }),
          st.error ? jsx('div', { className: 'mb-2 text-xs text-destructive', children: st.error }) : null,
          render(data, st)
        ]
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
              jsx(ToggleRow, {
                label: 'Route each turn',
                description:
                  'Pick a model and reasoning effort per request from the allowed models. Off leaves the session model alone.',
                checked: s.mode === 'auto',
                disabled: st.busy,
                onChange: on => st.save({ mode: on ? 'auto' : 'off' })
              }),
              jsx(ListRow, {
                title: 'Allowed models',
                action: jsx(Button, { variant: 'outline', size: 'xs', onClick: () => openPage('allowlist'), children: 'Edit' }),
                description: models
                  ? `${providers.length} provider${providers.length === 1 ? '' : 's'}, ${models} model${models === 1 ? '' : 's'}: ` +
                    providers.map(p => `${p} (${s.scope[p].join(', ')})`).join(' · ')
                  : 'Nothing allowed yet — routing does nothing until you check models under Allowed models.'
              }),
              jsx(ListRow, {
                title: 'Task classifier',
                description:
                  'Rules: keywords and length, no extra call. Hybrid: asks a model only when rules are unsure. Model: always asks.',
                action: jsx(SegmentedControl, {
                  options: CLASSIFIERS, value: s.classifier, disabled: st.busy,
                  onChange: v => st.save({ classifier: v })
                })
              }),
              jsx(ToggleRow, {
                label: 'Move the session across providers',
                description:
                  'Off: models change only within the provider the session is on (public plugin API). On: the session ' +
                  'moves to another allowed provider when the route says so. Uses Hermes internals and may break after ' +
                  'an update; it falls back to off when they are missing.',
                checked: s.cross_provider,
                disabled: st.busy,
                onChange: on => st.save({ cross_provider: on })
              }),
              jsx(ToggleRow, {
                label: 'Re-answer soft refusals',
                description: 'A short "I can\'t help with that" reply is answered again by another allowed model (without tools).',
                checked: s.soft_refusal_retry,
                disabled: st.busy,
                onChange: on => st.save({ soft_refusal_retry: on })
              }),
              jsx(NumberRow, {
                title: 'Cool-down (minutes)',
                description: 'A model that fails twice in a row moves to the back of every route for this long.',
                value: s.cooldown_minutes, min: 0, max: 1440,
                onSave: v => st.save({ cooldown_minutes: v })
              }),
              jsx(NumberRow, {
                title: 'Models per comparison',
                description: 'The most models route_explore runs side by side (one subagent each).',
                value: s.max_explore, min: 1, max: 8,
                onSave: v => st.save({ max_explore: v })
              }),
              data.warnings.length
                ? jsxs('div', {
                    className: 'mt-4 flex flex-col gap-1',
                    children: [
                      jsx(Heading, { title: 'Warnings' }),
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
          title: 'No providers found',
          description: 'This profile has no provider with credentials yet. Add one with `hermes model`, then come back.'
        })
      }

      return jsxs('div', {
        children: [
          jsx(Muted, {
            className: 'mb-3',
            children:
              `Routing only ever uses what is checked here: at most ${max} providers, and on each only the checked models. ` +
              'A route, a pin, a learned preference or a comparison outside this list is ignored.'
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
                    isCurrent ? jsx(Badge, { variant: 'muted', children: 'session provider' }) : null,
                    on ? jsx(Muted, { children: `${(draft[p] || []).length} checked` }) : null,
                    !on && full ? jsx(Muted, { children: `limit of ${max} providers reached` }) : null
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
                                ? jsx(Badge, { variant: 'muted', size: 'xs', children: 'default' })
                                : null
                            ]
                          })),
                        jsxs('div', {
                          className: 'mt-1 flex items-center gap-2',
                          children: [
                            jsx(Input, {
                              placeholder: 'Add a model id', className: 'h-7 w-64 text-xs',
                              value: adding[p] || '',
                              onChange: e => setAdding({ ...adding, [p]: e.target.value }),
                              onKeyDown: e => { if (e.key === 'Enter') addModel(p) }
                            }),
                            jsx(Button, { variant: 'outline', size: 'xs', onClick: () => addModel(p), children: 'Add' })
                          ]
                        }),
                        (draft[p] || []).length === 0
                          ? jsx(Muted, { children: 'Check at least one model, or this provider is not saved.' })
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
              jsx(Button, { disabled: !dirty || st.busy, onClick: save, children: 'Save allowlist' }),
              jsx(Button, { variant: 'ghost', disabled: !dirty || st.busy, onClick: () => setDraft(saved), children: 'Undo' }),
              dirty ? jsx(Muted, { children: 'Unsaved changes. Running sessions pick them up on their next turn.' }) : null
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
        title: `${cls[0].toUpperCase()}${cls.slice(1)}`,
        description: CLASS_HELP[cls],
        below: jsxs('div', {
          className: 'mt-1 flex flex-col gap-0.5',
          children: [
            jsx(Muted, { children: `Order: ${effective.join(' → ') || '—'}` }),
            preferred
              ? jsx(Muted, { children: `★ Learned from a comparison: starts on ${preferred} (clear it under Stats)` })
              : null
          ]
        }),
        action: jsxs('div', {
          className: 'flex flex-wrap items-center gap-2',
          children: [
            select(model, setModel, allowed, 'w-56'),
            select(effort, setEffort, data.limits.efforts, 'w-24'),
            jsx(Button, { size: 'xs', disabled: !dirty || st.busy || !model, onClick: save, children: 'Save' })
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
              title: 'No allowed models yet',
              description: 'Check providers and models under Allowed models first.'
            })
          }
          return jsxs('div', {
            children: [
              jsx(Muted, {
                className: 'mb-2',
                children:
                  'Pick the model each kind of task tries first and its reasoning effort. The other allowed models follow ' +
                  'automatically as backups when a model is stopped by a guardrail, a refusal or an error.'
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
              jsx(Heading, { title: 'Models', children: 'Outcomes the router recorded in this profile.' }),
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
                          `${h.ok || 0} ok · ${h.fail || 0} failed` +
                          (cooling ? ` · cooling down ~${cooling}m` : '') +
                          (h.last_reason ? ` · last failure: ${h.last_reason}` : '')
                      })
                    })
                  })
                : jsx(Muted, { children: 'No outcomes yet.' }),
              jsx(Heading, { title: 'Learned preferences', children: 'Set by route_explore comparisons or /route prefer.' }),
              prefs.length
                ? jsx('div', {
                    children: prefs.map(([cls, cand]) =>
                      jsx(ListRow, {
                        key: cls, title: cls, description: cand,
                        action: jsx(Button, {
                          variant: 'outline', size: 'xs', disabled: st.busy,
                          onClick: () => st.call('/prefer', 'POST', { task_class: cls, candidate: null }),
                          children: 'Clear'
                        })
                      }))
                  })
                : jsx(Muted, { children: 'None.' }),
              jsxs('div', {
                className: 'mt-4 flex items-center gap-2',
                children: [
                  jsx(Button, { variant: 'outline', size: 'xs', onClick: () => st.refetch(), children: 'Refresh' }),
                  jsx(Button, {
                    variant: confirm ? 'destructive' : 'outline', size: 'xs', disabled: st.busy,
                    onClick: async () => {
                      if (!confirm) return setConfirm(true)
                      setConfirm(false)
                      await st.call('/reset', 'POST', {})
                    },
                    children: confirm ? 'Click again to forget everything' : 'Reset stats and preferences'
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

    ctx.registerSettingsPage?.({
      id: 'settings',
      title: 'Model Router',
      icon: 'type-hierarchy',
      render: () => jsx(General, {}),
      children: [
        { id: 'allowlist', title: 'Allowed models', render: () => jsx(Frame, { page: 'allowlist', render: (data, st) => jsx(AllowlistEditor, { data, st }) }) },
        { id: 'routes', title: 'Routes', render: () => jsx(Routes, {}) },
        { id: 'stats', title: 'Stats', render: () => jsx(Stats, {}) }
      ]
    })
  }
}
