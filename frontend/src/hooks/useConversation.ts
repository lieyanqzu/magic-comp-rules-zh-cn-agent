import { useCallback, useEffect, useRef, useState } from 'react'
import { askJudge, streamJudge } from '../api'
import type {
  AppSettings,
  JudgeResponse,
  StreamEvent,
  ToolCallEvent,
  ToolResultEvent,
} from '../api/types'

export interface ThinkingTraceEntry {
  kind: 'thinking' | 'tool_call' | 'tool_result' | 'error'
  content: string
  detail?: ToolCallEvent | ToolResultEvent
  ts: number
}

export interface Exchange {
  id: string
  question: string
  trace: ThinkingTraceEntry[]
  answer: JudgeResponse | null
  error: string | null
  latencyMs: number | null
  loading: boolean
  /** 是否流式（决定渲染时是否展示 trace） */
  stream: boolean
}

const STORAGE_KEY = 'mtg-judge-conversation-v1'
const MAX_PERSIST = 20 // 持久化最多 20 轮，避免 localStorage 膨胀

function loadExchanges(): Exchange[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as Exchange[]
    // loading 状态丢弃（刷新时不可能仍在 loading）
    return parsed
      .filter((e) => e && typeof e.id === 'string' && typeof e.question === 'string')
      .map((e) => ({ ...e, loading: false }))
  } catch {
    return []
  }
}

function persistExchanges(exchanges: Exchange[]): void {
  try {
    // 不持久化 in-flight；trace 数据可能很大，只持久化最近 N 轮
    const cleaned = exchanges
      .filter((e) => !e.loading)
      .slice(-MAX_PERSIST)
    localStorage.setItem(STORAGE_KEY, JSON.stringify(cleaned))
  } catch {
    // 配额满 / 隐私模式：放弃
  }
}

function genId(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 8)
}

function describe(event: StreamEvent): ThinkingTraceEntry | null {
  const ts = Date.now()
  switch (event.type) {
    case 'thinking':
      return { kind: 'thinking', content: event.content, ts }
    case 'tool_call':
      return {
        kind: 'tool_call',
        content: `调用工具：${toolLabel(event.tool)}${
          summarizeArgs(event.tool, event.args) ? `（${summarizeArgs(event.tool, event.args)}）` : ''
        }`,
        detail: event,
        ts,
      }
    case 'tool_result':
      return {
        kind: 'tool_result',
        content: `${toolLabel(event.tool)}：${summarizeResult(event)}`,
        detail: event,
        ts,
      }
    case 'error':
      return { kind: 'error', content: event.content, ts }
    default:
      return null
  }
}

function toolLabel(tool: string): string {
  return (
    {
      resolve_card: '查询牌张',
      search_rules: '检索规则',
      search_cards: '搜索牌库',
    }[tool] ?? tool
  )
}

function summarizeArgs(tool: string, args: Record<string, unknown>): string {
  if (tool === 'resolve_card') return String(args.card_name ?? '')
  if (tool === 'search_rules') {
    return [args.query, args.section_id ? `#${args.section_id}` : '']
      .filter(Boolean)
      .map(String)
      .join(' ')
  }
  if (tool === 'search_cards') return String(args.query ?? '')
  return ''
}

function summarizeResult(event: ToolResultEvent): string {
  if (event.status === 'error') return `失败 — ${event.error ?? '未知错误'}`
  if (event.tool === 'resolve_card') {
    if (event.status === 'not_found') return `未找到「${event.name ?? ''}」`
    if (event.name) return `已获取「${event.name}」`
    return '完成'
  }
  if (event.tool === 'search_rules') return `命中 ${event.results_count ?? 0} 条`
  if (event.tool === 'search_cards') {
    if (event.status === 'empty') return '无结果'
    return `命中 ${event.count ?? 0} 张`
  }
  return '完成'
}

/**
 * 对话历史 hook。每次 ask 追加一条 Exchange，不替换旧的。
 * 持久化到 localStorage，保留最近 20 轮。
 */
export function useConversation() {
  const [exchanges, setExchanges] = useState<Exchange[]>(loadExchanges)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    persistExchanges(exchanges)
  }, [exchanges])

  const updateExchange = useCallback(
    (id: string, patch: Partial<Exchange> | ((e: Exchange) => Partial<Exchange>)) => {
      setExchanges((prev) =>
        prev.map((e) => (e.id === id ? { ...e, ...(typeof patch === 'function' ? patch(e) : patch) } : e)),
      )
    },
    [],
  )

  const cancel = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
  }, [])

  const clear = useCallback(() => {
    cancel()
    setExchanges([])
    localStorage.removeItem(STORAGE_KEY)
  }, [cancel])

  const ask = useCallback(
    async (question: string, settings: AppSettings) => {
      cancel()
      const controller = new AbortController()
      abortRef.current = controller

      const id = genId()
      const exchange: Exchange = {
        id,
        question,
        trace: [],
        answer: null,
        error: null,
        latencyMs: null,
        loading: true,
        stream: settings.stream,
      }
      setExchanges((prev) => [...prev, exchange])

      try {
        if (settings.stream) {
          for await (const event of streamJudge(question, settings, controller.signal)) {
            const entry = describe(event)
            if (entry) {
              updateExchange(id, (e) => ({ trace: [...e.trace, entry] }))
            }
            if (event.type === 'answer') {
              updateExchange(id, { answer: event.data })
            }
            if (event.type === 'done') {
              updateExchange(id, { latencyMs: event.latency_ms ?? null })
            }
            if (event.type === 'error') {
              updateExchange(id, { error: event.content })
            }
          }
          updateExchange(id, { loading: false })
        } else {
          const answer = await askJudge(question, settings, controller.signal)
          updateExchange(id, {
            answer,
            latencyMs: answer.latency_ms ?? null,
            loading: false,
          })
        }
      } catch (err) {
        if ((err as Error).name === 'AbortError') {
          updateExchange(id, { loading: false })
          return
        }
        updateExchange(id, {
          error: (err as Error).message || '请求失败',
          loading: false,
        })
      } finally {
        if (abortRef.current === controller) abortRef.current = null
      }
    },
    [cancel, updateExchange],
  )

  return { exchanges, ask, cancel, clear }
}
