import type { Confidence, JudgeResponse } from '../api/types'
import { Badge } from './Badge'
import { CardCard } from './CardCard'
import { RuleCard } from './RuleCard'

const CONFIDENCE_LABEL: Record<Confidence, string> = {
  high: '高置信度',
  medium: '中置信度',
  low: '低置信度',
}

interface AnswerViewProps {
  answer: JudgeResponse
  latencyMs?: number | null
}

export function AnswerView({ answer, latencyMs }: AnswerViewProps) {
  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center gap-2">
        <Badge variant={answer.confidence}>{CONFIDENCE_LABEL[answer.confidence]}</Badge>
        {answer.needs_human_judge && <Badge variant="warning">建议人工裁判</Badge>}
        {latencyMs != null && (
          <span className="ml-auto text-xs text-slate-400">耗时 {(latencyMs / 1000).toFixed(1)}s</span>
        )}
      </header>

      {answer.summary && (
        <div className="rounded-lg border-l-4 border-indigo-500 bg-indigo-50 px-4 py-3">
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-indigo-700">
            裁判结论
          </div>
          <p className="text-sm leading-relaxed text-slate-800">{answer.summary}</p>
        </div>
      )}

      {answer.answer && (
        <section>
          <h2 className="mb-2 text-sm font-semibold text-slate-900">完整解析</h2>
          <div className="prose prose-sm max-w-none whitespace-pre-wrap text-slate-800">
            {answer.answer}
          </div>
        </section>
      )}

      {answer.cards.length > 0 && (
        <section>
          <h2 className="mb-2 text-sm font-semibold text-slate-900">引用牌张</h2>
          <div className="space-y-3">
            {answer.cards.map((c, i) => (
              <CardCard key={`${c.oracle_name ?? c.name}-${i}`} card={c} />
            ))}
          </div>
        </section>
      )}

      {answer.rules.length > 0 && (
        <section>
          <h2 className="mb-2 text-sm font-semibold text-slate-900">引用规则</h2>
          <div className="space-y-3">
            {answer.rules.map((r, i) => (
              <RuleCard key={`${r.section_id}-${i}`} rule={r} />
            ))}
          </div>
        </section>
      )}

      {answer.reasoning_summary && (
        <details className="rounded-lg border border-slate-200 bg-white p-3 text-sm">
          <summary className="cursor-pointer text-xs font-medium text-slate-600 hover:text-slate-900">
            推理摘要
          </summary>
          <p className="mt-2 whitespace-pre-wrap text-slate-700">{answer.reasoning_summary}</p>
        </details>
      )}
    </div>
  )
}
