import { useEffect, useRef, useState } from 'react'
import { autocompleteCard } from '../api'
import type { AppSettings, AutocompleteItem } from '../api/types'

interface UseAutocompleteOptions {
  /** 多少 ms 没新输入才发请求 */
  debounceMs?: number
  /** 少于这个长度不查 */
  minLength?: number
}

interface AutocompleteState {
  items: AutocompleteItem[]
  loading: boolean
  error: string | null
}

const INITIAL: AutocompleteState = { items: [], loading: false, error: null }

/**
 * 牌名自动补全。
 *
 * 边界处理：
 * - debounce 200ms：用户连续输入时只发最后一次
 * - AbortController：新请求发出时取消上一次未完成的（避免乱序覆盖）
 * - query 清空 → 立即清空结果，不再请求
 */
export function useAutocomplete(
  query: string,
  settings: AppSettings,
  { debounceMs = 200, minLength = 1 }: UseAutocompleteOptions = {},
) {
  const [state, setState] = useState<AutocompleteState>(INITIAL)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    const trimmed = query.trim()
    if (trimmed.length < minLength) {
      setState(INITIAL)
      abortRef.current?.abort()
      abortRef.current = null
      return
    }

    const timer = setTimeout(async () => {
      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller
      setState((s) => ({ ...s, loading: true, error: null }))
      try {
        const resp = await autocompleteCard(trimmed, settings, controller.signal)
        if (!controller.signal.aborted) {
          setState({ items: resp.items, loading: false, error: null })
        }
      } catch (err) {
        if ((err as Error).name === 'AbortError') return
        setState({ items: [], loading: false, error: (err as Error).message })
      }
    }, debounceMs)

    return () => {
      clearTimeout(timer)
    }
    // settings 变化（比如切换 BYOK / API Key）也重新查
  }, [query, settings.apiKey, debounceMs, minLength, settings])

  return state
}
