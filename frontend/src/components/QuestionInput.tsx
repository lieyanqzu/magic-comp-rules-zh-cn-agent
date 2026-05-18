import { useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { AutocompleteDropdown } from './AutocompleteDropdown'
import type { AppSettings, AutocompleteItem } from '../api/types'
import { useAutocomplete } from '../hooks/useAutocomplete'

interface QuestionInputProps {
  onSubmit: (q: string) => void
  onCancel: () => void
  loading: boolean
  settings: AppSettings
  disabled?: boolean
}

const PLACEHOLDER =
  '提一个万智牌规则问题。输入 [[ 唤起牌名补全，比如 [[谦卑、[[蛋白玛珂'

/**
 * 解析触发：从 caret 往前找最近的 [[，且其后到 caret 之间不含 ]] 或换行。
 * 命中返回 { from: '[[' 起点, query: '[[' 之后到 caret 的文本 }；否则 null。
 */
function parseTrigger(text: string, caret: number): { from: number; query: string } | null {
  if (caret < 2) return null
  for (let i = caret - 1; i >= 1; i--) {
    const ch = text[i]
    if (ch === '\n') return null
    if (ch === ']' && text[i - 1] === ']') return null
    if (ch === '[' && text[i - 1] === '[') {
      return { from: i - 1, query: text.slice(i + 1, caret) }
    }
  }
  return null
}

export function QuestionInput({
  onSubmit,
  onCancel,
  loading,
  settings,
  disabled,
}: QuestionInputProps) {
  const [value, setValue] = useState('')
  const [caret, setCaret] = useState(0)
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState(0)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const trigger = useMemo(() => (dropdownOpen ? parseTrigger(value, caret) : null), [
    dropdownOpen,
    value,
    caret,
  ])
  const query = trigger?.query ?? ''
  const { items, loading: acLoading, error: acError } = useAutocomplete(query, settings)

  // 重置选中项当结果集变化
  useMemo(() => setSelectedIndex(0), [items])

  const submit = () => {
    const q = value.trim()
    if (!q || loading) return
    onSubmit(q)
    setValue('')
    setCaret(0)
    setDropdownOpen(false)
  }

  const updateValue = (next: string, nextCaret: number) => {
    setValue(next)
    setCaret(nextCaret)
    // 检查是否需要打开 / 关闭下拉
    const t = parseTrigger(next, nextCaret)
    setDropdownOpen(t !== null)
  }

  const insertCard = (item: AutocompleteItem) => {
    const t = parseTrigger(value, caret)
    if (!t) return
    const before = value.slice(0, t.from)
    const after = value.slice(caret)
    const insertion = item.name_zh
    const next = `${before}${insertion}${after}`
    const nextCaret = before.length + insertion.length
    setValue(next)
    setCaret(nextCaret)
    setDropdownOpen(false)
    // 把焦点保留在 textarea 并把 caret 移到插入位置之后
    requestAnimationFrame(() => {
      const ta = textareaRef.current
      if (ta) {
        ta.focus()
        ta.setSelectionRange(nextCaret, nextCaret)
      }
    })
  }

  const handleKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // 下拉打开且有结果时，方向键 / Enter / Esc 由下拉接管
    if (dropdownOpen && items.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSelectedIndex((i) => (i + 1) % items.length)
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSelectedIndex((i) => (i - 1 + items.length) % items.length)
        return
      }
      if (e.key === 'Enter' && !e.metaKey && !e.ctrlKey) {
        e.preventDefault()
        insertCard(items[selectedIndex])
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setDropdownOpen(false)
        return
      }
    }
    // Cmd/Ctrl + Enter 始终提交（即使下拉打开）
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault()
      submit()
    }
  }

  return (
    <div className="relative rounded-lg border border-slate-200 bg-white p-3 shadow-sm focus-within:border-indigo-300 focus-within:ring-2 focus-within:ring-indigo-100 dark:border-slate-700 dark:bg-slate-900 dark:focus-within:border-indigo-500 dark:focus-within:ring-indigo-900/40">
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => updateValue(e.target.value, e.target.selectionStart ?? 0)}
        onKeyUp={(e) => setCaret(e.currentTarget.selectionStart ?? 0)}
        onClick={(e) => setCaret(e.currentTarget.selectionStart ?? 0)}
        onBlur={() => {
          // 让 onMouseDown 选项点击先执行；纯失焦才关下拉
          setTimeout(() => setDropdownOpen(false), 100)
        }}
        onFocus={() => {
          // 重新进入时，如果当前光标位置确实在触发态，重新打开
          const t = parseTrigger(value, caret)
          if (t) setDropdownOpen(true)
        }}
        onKeyDown={handleKey}
        placeholder={PLACEHOLDER}
        rows={3}
        disabled={disabled}
        className="w-full resize-none border-0 bg-transparent text-sm leading-relaxed text-slate-900 placeholder:text-slate-400 focus:outline-none disabled:opacity-60 dark:text-slate-100 dark:placeholder:text-slate-500"
      />
      {dropdownOpen && (
        <AutocompleteDropdown
          items={items}
          loading={acLoading}
          error={acError}
          selectedIndex={selectedIndex}
          onSelect={insertCard}
          onHover={setSelectedIndex}
        />
      )}
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
