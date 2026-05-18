import { useEffect, useRef, useState } from 'react'
import { Drawer } from './components/Drawer'
import { ExchangeView } from './components/ExchangeView'
import { QuestionInput } from './components/QuestionInput'
import { SettingsPanel } from './components/SettingsPanel'
import { ThemeToggle } from './components/ThemeToggle'
import { useConversation } from './hooks/useConversation'
import { useSettings } from './hooks/useSettings'
import { useTheme } from './hooks/useTheme'

function App() {
  const { settings, update, updateLlm, reset } = useSettings()
  const { exchanges, ask, cancel, clear } = useConversation()
  const { theme, toggle: toggleTheme } = useTheme()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const lastExchange = exchanges[exchanges.length - 1]
  const loading = lastExchange?.loading ?? false

  useEffect(() => {
    if (exchanges.length === 0) return
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [exchanges.length, lastExchange?.trace.length, lastExchange?.answer])

  const byokActive =
    !!settings.llm.apiKey?.trim() ||
    !!settings.llm.baseUrl?.trim() ||
    !!settings.llm.model?.trim()

  return (
    <div className="mx-auto flex min-h-full max-w-3xl flex-col px-4 py-6 sm:px-6 lg:py-10">
      <header className="mb-6 flex items-center gap-3">
        <h1 className="text-xl font-semibold text-slate-900 dark:text-slate-100">万智牌中文规则裁判</h1>
        <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-500 dark:bg-slate-800 dark:text-slate-400">
          beta
        </span>
        <div className="ml-auto flex items-center gap-2 text-xs">
          {byokActive && (
            <span className="rounded-full bg-emerald-50 px-2 py-1 text-emerald-700 ring-1 ring-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:ring-emerald-800">
              BYOK 已启用
            </span>
          )}
          {exchanges.length > 0 && (
            <button
              type="button"
              onClick={() => {
                if (confirm('确定要清空当前对话历史吗？')) clear()
              }}
              className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              清空
            </button>
          )}
          <ThemeToggle theme={theme} onToggle={toggleTheme} />
          <button
            type="button"
            onClick={() => setSettingsOpen(true)}
            className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
            设置
          </button>
        </div>
      </header>

      <main className="flex flex-1 flex-col gap-6">
        {exchanges.length === 0 ? (
          <EmptyState />
        ) : (
          <div className="space-y-8">
            {exchanges.map((e, i) => (
              <div key={e.id}>
                {i > 0 && <hr className="mb-8 border-slate-200 dark:border-slate-800" />}
                <ExchangeView exchange={e} />
              </div>
            ))}
            <div ref={bottomRef} />
          </div>
        )}

        <div className="sticky bottom-0 -mx-4 mt-auto bg-gradient-to-t from-slate-50 via-slate-50 px-4 pb-1 pt-4 sm:-mx-6 sm:px-6 dark:from-slate-950 dark:via-slate-950">
          <QuestionInput onSubmit={(q) => void ask(q, settings)} onCancel={cancel} loading={loading} />
        </div>
      </main>

      <footer className="mt-6 border-t border-slate-100 pt-4 text-center text-xs text-slate-400 dark:border-slate-800 dark:text-slate-500">
        本助手仅回答万智牌（Magic: The Gathering）规则相关问题。规则引用为本地索引；牌张信息来自 mtgch。
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
    <div className="rounded-lg border border-dashed border-slate-300 bg-white px-6 py-10 text-center text-sm text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400">
      <p className="mb-2">提一个万智牌规则问题，比如：</p>
      <ul className="space-y-1 text-slate-600 dark:text-slate-300">
        <li>「层系统的应用顺序是什么？」</li>
        <li>「证人保护怎么和异能增减效应互动？」</li>
        <li>「跃回后是否触发进战场异能？」</li>
      </ul>
    </div>
  )
}

export default App
