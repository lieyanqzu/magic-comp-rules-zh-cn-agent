import type { RuleRef } from '../api/types'

const DOC_LABELS: Record<string, string> = {
  cr: '完整规则',
  reference: '专题参考',
  mtr: '比赛规则',
  ipg: '违规处理',
}

interface RuleCardProps {
  rule: RuleRef
}

/** 渲染单条规则。source_path 形如 "magic-comp-rules-zh-cn/markdown/613.md"，提取文档类型作徽标。 */
export function RuleCard({ rule }: RuleCardProps) {
  const docType = inferDocType(rule.source_path)
  const docLabel = docType ? DOC_LABELS[docType] ?? docType : null
  return (
    <article className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <header className="mb-2 flex flex-wrap items-baseline gap-2">
        <span className="rounded bg-indigo-50 px-2 py-0.5 font-mono text-xs font-semibold text-indigo-700 ring-1 ring-inset ring-indigo-200">
          {rule.section_id}
        </span>
        {docLabel && <span className="text-xs text-slate-500">{docLabel}</span>}
        {rule.title && <span className="text-sm text-slate-700">{rule.title}</span>}
      </header>
      {rule.content_snippet && (
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-800">
          {rule.content_snippet}
        </p>
      )}
      {rule.source_path && (
        <div className="mt-2 font-mono text-xs text-slate-400">{rule.source_path}</div>
      )}
    </article>
  )
}

function inferDocType(sourcePath: string): string | null {
  if (!sourcePath) return null
  if (sourcePath.includes('magic-comp-rules')) return 'cr'
  if (sourcePath.includes('references')) return 'reference'
  if (sourcePath.includes('mtr')) return 'mtr'
  if (sourcePath.includes('ipg')) return 'ipg'
  return null
}
