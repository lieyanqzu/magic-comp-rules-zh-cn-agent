import { useState } from 'react'
import type { AppSettings } from '../api/types'

interface SettingsPanelProps {
  settings: AppSettings
  onUpdate: (patch: Partial<AppSettings>) => void
  onUpdateLlm: (patch: Partial<AppSettings['llm']>) => void
  onReset: () => void
}

export function SettingsPanel({ settings, onUpdate, onUpdateLlm, onReset }: SettingsPanelProps) {
  const [showApiKey, setShowApiKey] = useState(false)
  const [showLlmKey, setShowLlmKey] = useState(false)

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
        <PasswordField
          label="LLM API Key"
          value={settings.llm.apiKey ?? ''}
          onChange={(v) => onUpdateLlm({ apiKey: v })}
          show={showLlmKey}
          onToggle={() => setShowLlmKey((s) => !s)}
          placeholder="sk-..."
        />
        <TextField
          label="Base URL"
          value={settings.llm.baseUrl ?? ''}
          onChange={(v) => onUpdateLlm({ baseUrl: v })}
          placeholder="https://api.openai.com/v1"
        />
        <TextField
          label="Model"
          value={settings.llm.model ?? ''}
          onChange={(v) => onUpdateLlm({ model: v })}
          placeholder="gpt-4o"
        />
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
}

function PasswordField({ label, value, onChange, placeholder, show, onToggle }: PasswordFieldProps) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-slate-700 dark:text-slate-300">{label}</span>
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
