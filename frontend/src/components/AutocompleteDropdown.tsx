import { useEffect, useRef } from 'react'
import type { AutocompleteItem } from '../api/types'

interface AutocompleteDropdownProps {
  items: AutocompleteItem[]
  loading: boolean
  error: string | null
  selectedIndex: number
  onSelect: (item: AutocompleteItem) => void
  onHover: (index: number) => void
}

/**
 * 牌名补全下拉。固定定位到容器底部（textarea 下方），父级需要 relative。
 */
export function AutocompleteDropdown({
  items,
  loading,
  error,
  selectedIndex,
  onSelect,
  onHover,
}: AutocompleteDropdownProps) {
  const listRef = useRef<HTMLUListElement>(null)

  // 选中项滚到可视区域内（键盘上下时）
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLLIElement>(
      `[data-index="${selectedIndex}"]`,
    )
    el?.scrollIntoView({ block: 'nearest' })
  }, [selectedIndex])

  if (!loading && !error && items.length === 0) return null

  return (
    <div
      className="absolute left-0 right-0 top-full z-30 mt-1 max-h-72 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-lg dark:border-slate-700 dark:bg-slate-900"
      role="listbox"
    >
      {loading && items.length === 0 && (
        <div className="px-3 py-2 text-xs text-slate-500 dark:text-slate-400">搜索中…</div>
      )}
      {error && (
        <div className="px-3 py-2 text-xs text-rose-600 dark:text-rose-400">加载失败：{error}</div>
      )}
      {items.length > 0 && (
        <ul ref={listRef} className="max-h-72 overflow-y-auto py-1">
          {items.map((item, i) => {
            const active = i === selectedIndex
            return (
              <li
                key={`${item.name_en}-${i}`}
                data-index={i}
                role="option"
                aria-selected={active}
                onMouseDown={(e) => {
                  e.preventDefault()
                  onSelect(item)
                }}
                onMouseEnter={() => onHover(i)}
                className={`flex cursor-pointer items-baseline gap-2 px-3 py-1.5 text-sm ${
                  active
                    ? 'bg-indigo-50 text-indigo-900 dark:bg-indigo-950/40 dark:text-indigo-100'
                    : 'text-slate-800 hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-800'
                }`}
              >
                <span className="font-medium">{item.name_zh}</span>
                {item.name_en && item.name_en !== item.name_zh && (
                  <span className="truncate text-xs text-slate-500 dark:text-slate-400">
                    {item.name_en}
                  </span>
                )}
                {item.type_zh && (
                  <span className="ml-auto shrink-0 text-xs text-slate-400 dark:text-slate-500">
                    {item.type_zh}
                  </span>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
