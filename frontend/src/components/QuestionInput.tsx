import { useState, type KeyboardEvent } from 'react'

interface QuestionInputProps {
  onSubmit: (q: string) => void
  onCancel: () => void
  loading: boolean
  disabled?: boolean
}

const PLACEHOLDER = '提一个万智牌规则问题，比如：层系统中操控权改变和复制效应应用顺序如何？'

export function QuestionInput({ onSubmit, onCancel, loading, disabled }: QuestionInputProps) {
  const [value, setValue] = useState('')

  const submit = () => {
    const q = value.trim()
    if (!q || loading) return
    onSubmit(q)
    setValue('')
  }

  const handleKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault()
      submit()
    }
  }

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm focus-within:border-indigo-300 focus-within:ring-2 focus-within:ring-indigo-100 dark:border-slate-700 dark:bg-slate-900 dark:focus-within:border-indigo-500 dark:focus-within:ring-indigo-900/40">
      <textarea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKey}
        placeholder={PLACEHOLDER}
        rows={3}
        disabled={disabled}
        className="w-full resize-none border-0 bg-transparent text-sm leading-relaxed text-slate-900 placeholder:text-slate-400 focus:outline-none disabled:opacity-60 dark:text-slate-100 dark:placeholder:text-slate-500"
      />
      <div className="mt-2 flex items-center justify-between">
        <span className="text-xs text-slate-400 dark:text-slate-500">
          按{' '}
          <kbd className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[10px] dark:bg-slate-800 dark:text-slate-300">
            ⌘/Ctrl + Enter
          </kbd>{' '}
          发送
        </span>
        <div className="flex gap-2">
          {loading && (
            <button
              type="button"
              onClick={onCancel}
              className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              取消
            </button>
          )}
          <button
            type="button"
            onClick={submit}
            disabled={!value.trim() || loading || disabled}
            className="rounded-md bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-slate-300 dark:disabled:bg-slate-700 dark:disabled:text-slate-500"
          >
            {loading ? '思考中…' : '发送'}
          </button>
        </div>
      </div>
    </div>
  )
}
