import type { ThinkingTraceEntry } from '../hooks/useConversation'

interface ThinkingTraceProps {
  entries: ThinkingTraceEntry[]
  loading: boolean
}

const KIND_STYLES: Record<ThinkingTraceEntry['kind'], string> = {
  thinking: 'text-slate-600 dark:text-slate-300',
  tool_call: 'text-indigo-700 dark:text-indigo-300',
  tool_result: 'text-emerald-700 dark:text-emerald-300',
  error: 'text-rose-700 dark:text-rose-300',
}

const KIND_LABEL: Record<ThinkingTraceEntry['kind'], string> = {
  thinking: '思考',
  tool_call: '工具',
  tool_result: '结果',
  error: '错误',
}

export function ThinkingTrace({ entries, loading }: ThinkingTraceProps) {
  if (entries.length === 0 && !loading) return null
  return (
    <details className="rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900/50" open>
      <summary className="cursor-pointer select-none text-xs font-medium text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200">
        推理过程 {loading && <span className="ml-1 text-slate-400 dark:text-slate-500">（进行中…）</span>}
      </summary>
      <ol className="mt-2 space-y-1.5 text-xs">
        {entries.map((entry, i) => (
          <li key={i} className="flex gap-2">
            <span className="shrink-0 text-slate-400 dark:text-slate-500">{i + 1}.</span>
            <span className={`shrink-0 font-medium ${KIND_STYLES[entry.kind]}`}>
              [{KIND_LABEL[entry.kind]}]
            </span>
            <span className="text-slate-700 dark:text-slate-300">{entry.content}</span>
          </li>
        ))}
        {loading && (
          <li className="flex items-center gap-2 text-slate-400 dark:text-slate-500">
            <Spinner />
            <span>等待下一步…</span>
          </li>
        )}
      </ol>
    </details>
  )
}

function Spinner() {
  return (
    <svg
      className="h-3 w-3 animate-spin text-slate-400 dark:text-slate-500"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
      />
    </svg>
  )
}
