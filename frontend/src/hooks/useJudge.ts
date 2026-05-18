import { useCallback, useRef, useState } from 'react'
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

export interface JudgeState {
  loading: boolean
  trace: ThinkingTraceEntry[]
  answer: JudgeResponse | null
  error: string | null
  latencyMs: number | null
}

const INITIAL: JudgeState = {
  loading: false,
  trace: [],
  answer: null,
  error: null,
  latencyMs: null,
}

/** 把流式事件渲染成人类可读的中文短句。 */
function describe(event: StreamEvent): ThinkingTraceEntry | null {
  const ts = Date.now()
  switch (event.type) {
    case 'thinking':
      return { kind: 'thinking', content: event.content, ts }
    case 'tool_call': {
      const toolName = toolLabel(event.tool)
      const argSummary = summarizeArgs(event.tool, event.args)
      return {
        kind: 'tool_call',
        content: `调用工具：${toolName}${argSummary ? `（${argSummary}）` : ''}`,
        detail: event,
        ts,
      }
    }
    case 'tool_result': {
      const toolName = toolLabel(event.tool)
      const status = summarizeResult(event)
      return {
        kind: 'tool_result',
        content: `${toolName}：${status}`,
        detail: event,
        ts,
      }
    }
    case 'error':
      return { kind: 'error', content: event.content, ts }
    default:
      return null
  }
}

function toolLabel(tool: string): string {
  switch (tool) {
    case 'resolve_card':
      return '查询牌张'
    case 'search_rules':
      return '检索规则'
    case 'search_cards':
      return '搜索牌库'
    default:
      return tool
  }
}

function summarizeArgs(tool: string, args: Record<string, unknown>): string {
  if (tool === 'resolve_card') return String(args.card_name ?? '')
  if (tool === 'search_rules') {
    const q = args.query ? String(args.query) : ''
    const sid = args.section_id ? `#${String(args.section_id)}` : ''
    return [q, sid].filter(Boolean).join(' ')
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
  if (event.tool === 'search_rules') {
    return `命中 ${event.results_count ?? 0} 条`
  }
  if (event.tool === 'search_cards') {
    if (event.status === 'empty') return '无结果'
    return `命中 ${event.count ?? 0} 张`
  }
  return '完成'
}

export function useJudge() {
  const [state, setState] = useState<JudgeState>(INITIAL)
  const abortRef = useRef<AbortController | null>(null)

  const cancel = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
  }, [])

  const reset = useCallback(() => {
    cancel()
    setState(INITIAL)
  }, [cancel])

  const ask = useCallback(
    async (question: string, settings: AppSettings) => {
      cancel()
      const controller = new AbortController()
      abortRef.current = controller

      setState({
        loading: true,
        trace: [],
        answer: null,
        error: null,
        latencyMs: null,
      })

      try {
        if (settings.stream) {
          for await (const event of streamJudge(question, settings, controller.signal)) {
            const entry = describe(event)
            if (entry) {
              setState((s) => ({ ...s, trace: [...s.trace, entry] }))
            }
            if (event.type === 'answer') {
              setState((s) => ({ ...s, answer: event.data }))
            }
            if (event.type === 'done') {
              setState((s) => ({ ...s, latencyMs: event.latency_ms ?? null }))
            }
            if (event.type === 'error') {
              setState((s) => ({ ...s, error: event.content }))
            }
          }
          setState((s) => ({ ...s, loading: false }))
        } else {
          const answer = await askJudge(question, settings, controller.signal)
          setState({
            loading: false,
            trace: [],
            answer,
            error: null,
            latencyMs: answer.latency_ms ?? null,
          })
        }
      } catch (err) {
        if ((err as Error).name === 'AbortError') {
          setState((s) => ({ ...s, loading: false }))
          return
        }
        setState((s) => ({
          ...s,
          loading: false,
          error: (err as Error).message || '请求失败',
        }))
      } finally {
        if (abortRef.current === controller) abortRef.current = null
      }
    },
    [cancel],
  )

  return { state, ask, cancel, reset }
}
