import type { ThinkingTraceEntry } from '../hooks/useConversation'
import type { ToolCallEvent, ToolResultEvent } from '../api/types'

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
    <details
      className="rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900/50"
      open
    >
      <summary className="cursor-pointer select-none text-xs font-medium text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200">
        推理过程 {loading && <span className="ml-1 text-slate-400 dark:text-slate-500">（进行中…）</span>}
      </summary>
      <ol className="mt-2 space-y-1.5 text-xs">
        {entries.map((entry, i) => (
          <li key={i} className="flex flex-col gap-0.5">
            <div className="flex gap-2">
              <span className="shrink-0 text-slate-400 dark:text-slate-500">{i + 1}.</span>
              <span className={`shrink-0 font-medium ${KIND_STYLES[entry.kind]}`}>
                [{KIND_LABEL[entry.kind]}]
              </span>
              <span className="text-slate-700 dark:text-slate-300">{entry.content}</span>
              {entry.kind === 'tool_result' && entry.detail && (
                <Badges detail={entry.detail as ToolResultEvent} />
              )}
            </div>
            {entry.kind === 'tool_result' && entry.detail && (
              <ToolResultDetail detail={entry.detail as ToolResultEvent} />
            )}
            {entry.kind === 'tool_call' && entry.detail && (
              <ToolCallDetail detail={entry.detail as ToolCallEvent} />
            )}
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

/** 内联徽标：置信度 / 兜底 / 去重命中。颜色强烈，肉眼一扫就能看到异常。 */
function Badges({ detail }: { detail: ToolResultEvent }) {
  if (detail.tool !== 'search_rules') return null
  const items: Array<{ key: string; label: string; cls: string }> = []

  if (detail.duplicated_call) {
    items.push({
      key: 'dup',
      label: '重复',
      cls: 'border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200',
    })
  }

  if (detail.high_hit_satisfied) {
    items.push({
      key: 'high-hit',
      label: '已够用',
      cls: 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-200',
    })
  }

  // 重排状态非 ok/cached 时单独高亮（说明分数信号不可信）
  if (
    detail.rerank_status &&
    detail.rerank_status !== 'ok' &&
    detail.rerank_status !== 'cached' &&
    detail.rerank_status !== 'no_input'
  ) {
    items.push({
      key: 'rerank',
      label: detail.rerank_status === 'fallback' ? '重排兜底' : '重排关闭',
      cls: 'border-rose-300 bg-rose-50 text-rose-800 dark:border-rose-700 dark:bg-rose-950/40 dark:text-rose-200',
    })
  }

  if (detail.confidence_hint) {
    items.push({
      key: 'conf',
      label: { high: '高', medium: '中', low: '低' }[detail.confidence_hint],
      cls: {
        high: 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-200',
        medium: 'border-sky-300 bg-sky-50 text-sky-800 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-200',
        low: 'border-slate-300 bg-slate-100 text-slate-700 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300',
      }[detail.confidence_hint],
    })
  }

  if (items.length === 0) return null
  return (
    <span className="ml-auto flex shrink-0 flex-wrap gap-1">
      {items.map((it) => (
        <span
          key={it.key}
          className={`inline-flex items-center rounded border px-1.5 text-[10px] font-medium leading-4 ${it.cls}`}
        >
          {it.label}
        </span>
      ))}
    </span>
  )
}

/** tool_call 详情：展示 args 关键字段。同一行宽展不下时也只取摘要。 */
function ToolCallDetail({ detail }: { detail: ToolCallEvent }) {
  if (detail.tool !== 'search_rules') return null
  const args = detail.args as { query?: unknown; section_id?: unknown; document_types?: unknown }
  const chips: string[] = []
  if (args.section_id) chips.push(`#${String(args.section_id)}`)
  if (Array.isArray(args.document_types) && args.document_types.length > 0) {
    chips.push(`类型：${args.document_types.map(String).join(', ')}`)
  }
  if (chips.length === 0) return null
  return (
    <div className="ml-12 flex flex-wrap gap-1 text-[11px] text-slate-500 dark:text-slate-400">
      {chips.map((c, i) => (
        <span key={i} className="rounded bg-slate-100 px-1.5 py-0.5 font-mono dark:bg-slate-800/70">
          {c}
        </span>
      ))}
    </div>
  )
}

/** tool_result 详情：搜索的扩展词、剩余预算、状态等。只在有信息时展示。 */
function ToolResultDetail({ detail }: { detail: ToolResultEvent }) {
  if (detail.tool !== 'search_rules') return null

  // 同义词扩展：只展示真正"额外"加入的词（去掉与 query 完全相同的部分）
  const baseTerms = (detail.query ?? '')
    .split(/[\s,，、;；]+/)
    .map((t) => t.trim())
    .filter(Boolean)
  const extras = (detail.expanded_terms ?? []).filter((t) => !baseTerms.includes(t))

  const hasContent =
    extras.length > 0 ||
    typeof detail.best_score === 'number' ||
    detail.rerank_status === 'fallback' ||
    detail.rerank_status === 'disabled'

  if (!hasContent) return null

  return (
    <div className="ml-12 mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-slate-500 dark:text-slate-400">
      {extras.length > 0 && (
        <span className="flex flex-wrap items-center gap-1">
          <span className="text-slate-400 dark:text-slate-500">同义词扩展：</span>
          {extras.slice(0, 8).map((t) => (
            <span
              key={t}
              className="rounded bg-indigo-50 px-1.5 py-0.5 font-mono text-indigo-700 dark:bg-indigo-950/40 dark:text-indigo-200"
            >
              {t}
            </span>
          ))}
          {extras.length > 8 && (
            <span className="text-slate-400 dark:text-slate-500">+{extras.length - 8}</span>
          )}
        </span>
      )}
      {detail.rerank_status === 'fallback' && (
        <span className="text-rose-600 dark:text-rose-300">
          ⚠ 重排服务返回失败，分数仅作排序占位
        </span>
      )}
      {detail.rerank_status === 'disabled' && (
        <span className="text-slate-500 dark:text-slate-400">重排已关闭，分数为线性兜底</span>
      )}
    </div>
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
