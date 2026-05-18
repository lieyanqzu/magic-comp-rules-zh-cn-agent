import { useState } from 'react'
import { AnswerView } from './components/AnswerView'
import { Drawer } from './components/Drawer'
import { QuestionInput } from './components/QuestionInput'
import { SettingsPanel } from './components/SettingsPanel'
import { ThinkingTrace } from './components/ThinkingTrace'
import { useJudge } from './hooks/useJudge'
import { useSettings } from './hooks/useSettings'

function App() {
  const { settings, update, updateLlm, reset } = useSettings()
  const { state, ask, cancel } = useJudge()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [lastQuestion, setLastQuestion] = useState<string | null>(null)

  const handleSubmit = (q: string) => {
    setLastQuestion(q)
    void ask(q, settings)
  }

  const byokActive =
    !!settings.llm.apiKey?.trim() ||
    !!settings.llm.baseUrl?.trim() ||
    !!settings.llm.model?.trim()

  return (
    <div className="mx-auto flex min-h-full max-w-3xl flex-col px-4 py-6 sm:px-6 lg:py-10">
      <header className="mb-6 flex items-center gap-3">
        <h1 className="text-xl font-semibold text-slate-900">万智牌中文规则裁判</h1>
        <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-500">beta</span>
        <div className="ml-auto flex items-center gap-2 text-xs">
          {byokActive && (
            <span className="rounded-full bg-emerald-50 px-2 py-1 text-emerald-700 ring-1 ring-emerald-200">
              BYOK 已启用
            </span>
          )}
          <button
            type="button"
            onClick={() => setSettingsOpen(true)}
            className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-slate-700 hover:bg-slate-50"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
            设置
          </button>
        </div>
      </header>

      <main className="flex flex-1 flex-col gap-5">
        <QuestionInput onSubmit={handleSubmit} onCancel={cancel} loading={state.loading} />

        {lastQuestion && (
          <section className="rounded-lg bg-slate-100 px-4 py-2 text-sm text-slate-700">
            <span className="mr-2 text-xs font-medium text-slate-500">问题：</span>
            <span>{lastQuestion}</span>
          </section>
        )}

        {state.error && (
          <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            <strong className="font-semibold">出错了：</strong> {state.error}
          </div>
        )}

        {settings.stream && (
          <ThinkingTrace entries={state.trace} loading={state.loading && !state.answer} />
        )}

        {state.answer && <AnswerView answer={state.answer} latencyMs={state.latencyMs} />}

        {!state.loading && !state.answer && !state.error && !lastQuestion && (
          <EmptyState />
        )}
      </main>

      <footer className="mt-8 border-t border-slate-100 pt-4 text-center text-xs text-slate-400">
        本助手仅回答万智牌（Magic: The Gathering）规则相关问题。所有规则引用均为本地索引；牌张信息来自 mtgch。
      </footer>

      <Drawer open={settingsOpen} onClose={() => setSettingsOpen(false)} title="设置">
        <SettingsPanel
          settings={settings}
          onUpdate={update}
          onUpdateLlm={updateLlm}
          onReset={reset}
        />
      </Drawer>
    </div>
  )
}

function EmptyState() {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 bg-white px-6 py-10 text-center text-sm text-slate-500">
      <p className="mb-2">提一个万智牌规则问题，比如：</p>
      <ul className="space-y-1 text-slate-600">
        <li>「层系统的应用顺序是什么？」</li>
        <li>「证人保护怎么和异能增减效应互动？」</li>
        <li>「相位回归后是否触发进战场异能？」</li>
      </ul>
    </div>
  )
}

export default App
