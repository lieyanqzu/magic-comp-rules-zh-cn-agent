import { useEffect, useRef, useState } from 'react'
import type { AppSettings } from '../api/types'
import { DEFAULT_MAX_TOKENS } from '../hooks/useSettings'

interface SettingsPanelProps {
  settings: AppSettings
  onUpdate: (patch: Partial<AppSettings>) => void
  onUpdateLlm: (patch: Partial<AppSettings['llm']>) => void
  onReset: () => void
}

interface ProviderPreset {
  name: string
  baseUrl: string
  apiKeyUrl: string
}

const PROVIDER_PRESETS: ProviderPreset[] = [
  { name: 'OpenAI', baseUrl: 'https://api.openai.com/v1', apiKeyUrl: 'https://platform.openai.com/api-keys' },
  { name: 'DeepSeek', baseUrl: 'https://api.deepseek.com/v1', apiKeyUrl: 'https://platform.deepseek.com/api_keys' },
  { name: 'OpenRouter', baseUrl: 'https://openrouter.ai/api/v1', apiKeyUrl: 'https://openrouter.ai/keys' },
  { name: 'Moonshot', baseUrl: 'https://api.moonshot.cn/v1', apiKeyUrl: 'https://platform.moonshot.cn/console/api-keys' },
  { name: 'SiliconFlow', baseUrl: 'https://api.siliconflow.cn/v1', apiKeyUrl: 'https://cloud.siliconflow.cn/account/ak' },
  { name: '智谱 GLM', baseUrl: 'https://open.bigmodel.cn/api/paas/v4', apiKeyUrl: 'https://bigmodel.cn/usercenter/proj-mgmt/apikeys' },
]

const CUSTOM = '__custom'

async function fetchModels(baseUrl: string, apiKey: string): Promise<string[]> {
  const trimmed = baseUrl.replace(/\/+$/, '')
  if (!trimmed || !apiKey) return []
  try {
    const res = await fetch(`${trimmed}/models`, {
      headers: { Authorization: `Bearer ${apiKey}` },
    })
    if (!res.ok) return []
    const data = (await res.json()) as { data?: Array<{ id?: string }> }
    return (data.data ?? [])
      .map((m) => m?.id)
      .filter((s): s is string => typeof s === 'string' && s.length > 0)
      .sort()
  } catch {
    return []
  }
}

export function SettingsPanel({ settings, onUpdate, onUpdateLlm, onReset }: SettingsPanelProps) {
  const [showApiKey, setShowApiKey] = useState(false)
  const [showLlmKey, setShowLlmKey] = useState(false)

  const trimmedBase = (settings.llm.baseUrl ?? '').trim()
  const matched = PROVIDER_PRESETS.find((p) => p.baseUrl === trimmedBase)
  const isCustomBaseUrl = !matched && trimmedBase.length > 0

  // 用户从下拉菜单选了"自定义"但 baseUrl 还是空时，仅靠 settings 推不出该状态，
  // 用本地 state 记住"用户正在自定义模式"
  const [customMode, setCustomMode] = useState(isCustomBaseUrl)
  const showCustom = customMode || isCustomBaseUrl
  const selectValue = matched ? matched.baseUrl : showCustom ? CUSTOM : ''
  const showByokFields = matched != null || showCustom

  const handleProviderChange = (v: string) => {
    // 切换 provider 一律清空 apiKey 和 model：不同 provider 的 key 不通用，
    // 模型名也几乎都不一样，残留只会导致下次请求 401 或 model not found
    if (v === '') {
      setCustomMode(false)
      onUpdateLlm({ apiKey: '', baseUrl: '', model: '' })
    } else if (v === CUSTOM) {
      setCustomMode(true)
      onUpdateLlm({ apiKey: '', baseUrl: '', model: '' })
    } else {
      setCustomMode(false)
      onUpdateLlm({ apiKey: '', baseUrl: v, model: '' })
    }
  }

  return (
    <div className="space-y-6">
      <Section title="问答行为">
        <label className="flex items-center gap-3 text-sm">
          <input
            type="checkbox"
            checked={settings.stream}
            onChange={(e) => onUpdate({ stream: e.target.checked })}
            className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500 dark:border-slate-600 dark:bg-slate-800"
          />
          <span>
            <span className="font-medium text-slate-900 dark:text-slate-100">使用流式输出（SSE）</span>
            <span className="ml-2 text-xs text-slate-500 dark:text-slate-400">实时显示推理过程</span>
          </span>
        </label>

        <MaxTokensField
          value={settings.llm.maxTokens}
          onChange={(v) => onUpdateLlm({ maxTokens: v })}
        />
      </Section>

      <Section title="后端鉴权" hint="服务器开启 API_KEY 时必填">
        <PasswordField
          label="X-API-Key"
          value={settings.apiKey ?? ''}
          onChange={(v) => onUpdate({ apiKey: v })}
          show={showApiKey}
          onToggle={() => setShowApiKey((s) => !s)}
          placeholder="未启用可留空"
        />
      </Section>

      <Section
        title="自带 LLM（BYOK）"
        hint="留空则使用服务器预设模型。所有字段仅存浏览器本地，永不入库。"
      >
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs leading-relaxed text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/40 dark:text-amber-200">
          <p className="font-semibold">强烈建议填入自己的 API Key</p>
          <p className="mt-1">
            服务器预设模型共享额度，<span className="font-semibold">非常不稳定</span>，可能随时限流或失效。
            DeepSeek 等渠道价格便宜（百万 tokens 约几元起），自带 KEY 体验更好。
            填入的 KEY 仅保存在你的浏览器，不会写入服务器日志或数据库。
          </p>
        </div>

        <label className="block">
          <span className="mb-1 block text-xs font-medium text-slate-700 dark:text-slate-300">Provider</span>
          <select
            value={selectValue}
            onChange={(e) => handleProviderChange(e.target.value)}
            className="w-full rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:focus:border-indigo-500 dark:focus:ring-indigo-900/40"
          >
            <option value="">使用服务器默认</option>
            {PROVIDER_PRESETS.map((p) => (
              <option key={p.baseUrl} value={p.baseUrl}>
                {p.name}
              </option>
            ))}
            <option value={CUSTOM}>自定义…</option>
          </select>
        </label>

        {showCustom && (
          <TextField
            label="Base URL"
            value={settings.llm.baseUrl ?? ''}
            onChange={(v) => onUpdateLlm({ baseUrl: v })}
            placeholder="https://example.com/v1"
          />
        )}

        {matched && (
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-slate-700 dark:text-slate-300">Base URL</span>
            <input
              type="text"
              value={matched.baseUrl}
              readOnly
              className="w-full cursor-not-allowed rounded-md border border-slate-200 bg-slate-50 px-3 py-1.5 font-mono text-xs text-slate-600 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-400"
            />
          </label>
        )}

        {showByokFields && (
          <>
            <PasswordField
              label="API Key"
              value={settings.llm.apiKey ?? ''}
              onChange={(v) => onUpdateLlm({ apiKey: v })}
              show={showLlmKey}
              onToggle={() => setShowLlmKey((s) => !s)}
              placeholder="sk-..."
              hintRight={
                matched ? (
                  <a
                    href={matched.apiKeyUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-indigo-600 hover:underline dark:text-indigo-400"
                  >
                    前往 {matched.name} 申请 ↗
                  </a>
                ) : undefined
              }
            />

            <ModelField
              baseUrl={trimmedBase}
              apiKey={(settings.llm.apiKey ?? '').trim()}
              value={settings.llm.model ?? ''}
              onChange={(v) => onUpdateLlm({ model: v })}
            />
          </>
        )}
      </Section>

      <div className="border-t border-slate-100 pt-4 dark:border-slate-800">
        <button
          type="button"
          onClick={() => {
            if (confirm('确定要清空所有设置（含 BYOK）吗？')) onReset()
          }}
          className="text-xs text-rose-600 hover:text-rose-700 hover:underline dark:text-rose-400 dark:hover:text-rose-300"
        >
          清空所有设置
        </button>
      </div>
    </div>
  )
}

interface ModelFieldProps {
  baseUrl: string
  apiKey: string
  value: string
  onChange: (v: string) => void
}

function MaxTokensField({
  value,
  onChange,
}: {
  value: number | undefined
  onChange: (v: number | undefined) => void
}) {
  // value=undefined 表示用默认。input 显示空字符串。
  // 输入合法整数则上行 number；清空则上行 undefined（让后端走默认 32K）
  const display = value == null ? '' : String(value)
  const handleChange = (raw: string) => {
    const trimmed = raw.trim()
    if (trimmed === '') {
      onChange(undefined)
      return
    }
    const n = Number.parseInt(trimmed, 10)
    if (Number.isFinite(n) && n > 0) onChange(n)
  }
  return (
    <label className="block">
      <span className="mb-1 flex items-center justify-between text-xs font-medium text-slate-700 dark:text-slate-300">
        <span>响应最大 token 数</span>
        <span className="font-normal text-slate-500 dark:text-slate-400">
          默认 {DEFAULT_MAX_TOKENS.toLocaleString()}
        </span>
      </span>
      <input
        type="number"
        min={1}
        step={1000}
        inputMode="numeric"
        value={display}
        onChange={(e) => handleChange(e.target.value)}
        placeholder={`${DEFAULT_MAX_TOKENS}（留空使用默认）`}
        className="w-full rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:placeholder:text-slate-500 dark:focus:border-indigo-500 dark:focus:ring-indigo-900/40"
      />
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        长答案被截断时调高；上游有自己的硬上限（通常 8K-128K），过大会被 provider 拒绝。
      </p>
    </label>
  )
}

function ModelField({ baseUrl, apiKey, value, onChange }: ModelFieldProps) {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [models, setModels] = useState<string[]>([])
  // 缓存 baseUrl+apiKey 组合，避免重复拉取；任一变化则失效
  const cacheKeyRef = useRef<string>('')
  const wrapRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const handleToggle = async () => {
    if (open) {
      setOpen(false)
      return
    }
    const key = `${baseUrl}|${apiKey}`
    if (key !== cacheKeyRef.current) {
      setLoading(true)
      const list = await fetchModels(baseUrl, apiKey)
      cacheKeyRef.current = key
      setModels(list)
      setLoading(false)
      // 拉不到则保持静默不展开
      if (list.length > 0) setOpen(true)
      return
    }
    if (models.length > 0) setOpen(true)
  }

  const filtered = models

  return (
    <div ref={wrapRef} className="relative">
      <span className="mb-1 block text-xs font-medium text-slate-700 dark:text-slate-300">Model</span>
      <div className="flex">
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="留空使用服务器默认"
          autoComplete="off"
          className="flex-1 rounded-l-md border border-r-0 border-slate-200 bg-white px-3 py-1.5 text-sm focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:placeholder:text-slate-500 dark:focus:border-indigo-500 dark:focus:ring-indigo-900/40"
        />
        <button
          type="button"
          onClick={handleToggle}
          disabled={loading}
          aria-label="展开模型列表"
          className="rounded-r-md border border-slate-200 bg-white px-2 text-slate-500 hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400 dark:hover:bg-slate-800"
        >
          {loading ? (
            <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-slate-300 border-t-slate-600 dark:border-slate-600 dark:border-t-slate-200" />
          ) : (
            <span className="text-xs">▾</span>
          )}
        </button>
      </div>
      {open && filtered.length > 0 && (
        <ul className="absolute left-0 right-0 z-10 mt-1 max-h-56 overflow-auto rounded-md border border-slate-200 bg-white py-1 text-sm shadow-lg dark:border-slate-700 dark:bg-slate-900">
          {filtered.map((m) => (
            <li key={m}>
              <button
                type="button"
                onClick={() => {
                  onChange(m)
                  setOpen(false)
                }}
                className="block w-full truncate px-3 py-1 text-left hover:bg-slate-50 dark:hover:bg-slate-800"
              >
                {m}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

interface SectionProps {
  title: string
  hint?: string
  children: React.ReactNode
}

function Section({ title, hint, children }: SectionProps) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">{title}</h3>
      {hint && <p className="text-xs text-slate-500 dark:text-slate-400">{hint}</p>}
      <div className="space-y-2">{children}</div>
    </section>
  )
}

interface FieldProps {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
}

function TextField({ label, value, onChange, placeholder }: FieldProps) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-slate-700 dark:text-slate-300">{label}</span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete="off"
        className="w-full rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:placeholder:text-slate-500 dark:focus:border-indigo-500 dark:focus:ring-indigo-900/40"
      />
    </label>
  )
}

interface PasswordFieldProps extends FieldProps {
  show: boolean
  onToggle: () => void
  hintRight?: React.ReactNode
}

function PasswordField({ label, value, onChange, placeholder, show, onToggle, hintRight }: PasswordFieldProps) {
  return (
    <label className="block">
      <span className="mb-1 flex items-center justify-between text-xs font-medium text-slate-700 dark:text-slate-300">
        <span>{label}</span>
        {hintRight && <span className="font-normal">{hintRight}</span>}
      </span>
      <div className="flex gap-2">
        <input
          type={show ? 'text' : 'password'}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          autoComplete="off"
          className="flex-1 rounded-md border border-slate-200 bg-white px-3 py-1.5 font-mono text-sm focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:placeholder:text-slate-500 dark:focus:border-indigo-500 dark:focus:ring-indigo-900/40"
        />
        <button
          type="button"
          onClick={onToggle}
          className="rounded-md border border-slate-200 bg-white px-3 text-xs text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          {show ? '隐藏' : '显示'}
        </button>
      </div>
    </label>
  )
}
