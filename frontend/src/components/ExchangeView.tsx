import type { Exchange } from '../hooks/useConversation'
import { AnswerView } from './AnswerView'
import { ThinkingTrace } from './ThinkingTrace'

interface ExchangeViewProps {
  exchange: Exchange
}

/** 单条问答（问题 + 推理过程 + 答案 / 错误）。 */
export function ExchangeView({ exchange }: ExchangeViewProps) {
  const { question, trace, answer, error, latencyMs, loading, stream } = exchange
  return (
    <article className="space-y-4">
      <div className="rounded-lg bg-slate-100 px-4 py-2.5 text-sm text-slate-700 dark:bg-slate-800/60 dark:text-slate-200">
        <span className="mr-2 text-xs font-medium text-slate-500 dark:text-slate-400">问题：</span>
        <span>{question}</span>
      </div>

      {error && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300">
          <strong className="font-semibold">出错了：</strong> {error}
        </div>
      )}

      {stream && (trace.length > 0 || (loading && !answer)) && (
        <ThinkingTrace entries={trace} loading={loading && !answer} />
      )}

      {!answer && loading && !stream && (
        <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400">
          <Spinner />
          <span>正在生成回答…</span>
        </div>
      )}

      {answer && <AnswerView answer={answer} latencyMs={latencyMs} />}
    </article>
  )
}

function Spinner() {
  return (
    <svg className="h-3.5 w-3.5 animate-spin text-slate-400 dark:text-slate-500" viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
    </svg>
  )
}
