import type { ThinkingTraceEntry } from '../hooks/useJudge'

interface ThinkingTraceProps {
  entries: ThinkingTraceEntry[]
  loading: boolean
}

const KIND_STYLES: Record<ThinkingTraceEntry['kind'], string> = {
  thinking: 'text-slate-600',
  tool_call: 'text-indigo-700',
  tool_result: 'text-emerald-700',
  error: 'text-rose-700',
}

const KIND_LABEL: Record<ThinkingTraceEntry['kind'], string> = {
  thinking: '思考',
  tool_call: '工具',
  tool_result: '结果',
  error: '错误',
}

/** 流式推理过程：单列时间线，可折叠。 */
export function ThinkingTrace({ entries, loading }: ThinkingTraceProps) {
  if (entries.length === 0 && !loading) return null
  return (
    <details className="rounded-lg border border-slate-200 bg-slate-50 p-3" open>
      <summary className="cursor-pointer select-none text-xs font-medium text-slate-600 hover:text-slate-900">
        推理过程 {loading && <span className="ml-1 text-slate-400">（进行中…）</span>}
      </summary>
      <ol className="mt-2 space-y-1.5 text-xs">
        {entries.map((entry, i) => (
          <li key={i} className="flex gap-2">
            <span className="shrink-0 text-slate-400">{i + 1}.</span>
            <span className={`shrink-0 font-medium ${KIND_STYLES[entry.kind]}`}>
              [{KIND_LABEL[entry.kind]}]
            </span>
            <span className="text-slate-700">{entry.content}</span>
          </li>
        ))}
        {loading && (
          <li className="flex items-center gap-2 text-slate-400">
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
      className="h-3 w-3 animate-spin text-slate-400"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
      />
    </svg>
  )
}
