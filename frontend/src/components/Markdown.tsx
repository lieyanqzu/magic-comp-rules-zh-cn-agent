import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Components } from 'react-markdown'

interface MarkdownProps {
  children: string
  className?: string
}

const COMPONENTS: Components = {
  p: ({ children }) => <p className="mb-3 leading-relaxed last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="mb-3 list-disc space-y-1 pl-5 last:mb-0">{children}</ul>,
  ol: ({ children }) => <ol className="mb-3 list-decimal space-y-1 pl-5 last:mb-0">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  h1: ({ children }) => <h1 className="mb-2 mt-3 text-base font-semibold first:mt-0">{children}</h1>,
  h2: ({ children }) => <h2 className="mb-2 mt-3 text-base font-semibold first:mt-0">{children}</h2>,
  h3: ({ children }) => <h3 className="mb-2 mt-3 text-sm font-semibold first:mt-0">{children}</h3>,
  h4: ({ children }) => <h4 className="mb-1 mt-3 text-sm font-semibold first:mt-0">{children}</h4>,
  strong: ({ children }) => (
    <strong className="font-semibold text-slate-900 dark:text-slate-100">{children}</strong>
  ),
  em: ({ children }) => <em className="italic">{children}</em>,
  code: ({ children, className }) => {
    if (!className) {
      return (
        <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[0.85em] text-slate-800 dark:bg-slate-800 dark:text-slate-200">
          {children}
        </code>
      )
    }
    return <code className={className}>{children}</code>
  },
  pre: ({ children }) => (
    <pre className="mb-3 overflow-x-auto rounded-md bg-slate-900 p-3 font-mono text-xs text-slate-100 last:mb-0 dark:bg-slate-800">
      {children}
    </pre>
  ),
  blockquote: ({ children }) => (
    <blockquote className="mb-3 border-l-4 border-slate-200 pl-3 italic text-slate-600 last:mb-0 dark:border-slate-700 dark:text-slate-400">
      {children}
    </blockquote>
  ),
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-indigo-600 underline decoration-indigo-300 underline-offset-2 hover:text-indigo-700 dark:text-indigo-400 dark:decoration-indigo-700 dark:hover:text-indigo-300"
    >
      {children}
    </a>
  ),
  table: ({ children }) => (
    <div className="mb-3 overflow-x-auto last:mb-0">
      <table className="min-w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="border-b border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800/40">
      {children}
    </thead>
  ),
  th: ({ children }) => (
    <th className="px-3 py-1.5 text-left font-semibold text-slate-700 dark:text-slate-200">{children}</th>
  ),
  td: ({ children }) => (
    <td className="border-b border-slate-100 px-3 py-1.5 dark:border-slate-800">{children}</td>
  ),
  hr: () => <hr className="my-4 border-slate-200 dark:border-slate-700" />,
}

/** 渲染 LLM 返回的 markdown 文本。GFM（表格、删除线、任务列表）启用，HTML 关闭。 */
export function Markdown({ children, className = '' }: MarkdownProps) {
  return (
    <div className={`text-sm leading-relaxed text-slate-800 dark:text-slate-200 ${className}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
        {children}
      </ReactMarkdown>
    </div>
  )
}
