import { apiKeyHeader, byokHeaders } from './headers'
import type { AppSettings, AutocompleteResponse, JudgeResponse, StreamEvent } from './types'

/**
 * 非流式问答。命中 L1 安全过滤会直接返回拒绝消息（answer 字段就是给用户看的）。
 */
export async function askJudge(
  question: string,
  settings: AppSettings,
  signal?: AbortSignal,
): Promise<JudgeResponse> {
  const resp = await fetch('/v1/judge/ask', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...apiKeyHeader(settings.apiKey),
      ...byokHeaders(settings.llm),
    },
    body: JSON.stringify({ question, language: 'zh-CN' }),
    signal,
  })
  if (!resp.ok) {
    const text = await resp.text().catch(() => '')
    throw new Error(`HTTP ${resp.status}: ${text || resp.statusText}`)
  }
  return (await resp.json()) as JudgeResponse
}

/**
 * 流式问答。基于 fetch + ReadableStream 自己解 SSE，
 * 不用 EventSource：EventSource 只支持 GET、不支持自定义请求头（BYOK 必须用 header）。
 */
export async function* streamJudge(
  question: string,
  settings: AppSettings,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent, void, unknown> {
  const resp = await fetch('/v1/judge/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...apiKeyHeader(settings.apiKey),
      ...byokHeaders(settings.llm),
    },
    body: JSON.stringify({ question, language: 'zh-CN' }),
    signal,
  })
  if (!resp.ok) {
    const text = await resp.text().catch(() => '')
    throw new Error(`HTTP ${resp.status}: ${text || resp.statusText}`)
  }
  if (!resp.body) {
    throw new Error('响应体为空')
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // SSE 帧以 "\n\n" 分隔；每帧可能有多行（data: / event: / : 注释）
      let sepIdx: number
      while ((sepIdx = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, sepIdx)
        buffer = buffer.slice(sepIdx + 2)
        const event = parseSseFrame(frame)
        if (event) yield event
      }
    }
  } finally {
    reader.releaseLock()
  }
}

function parseSseFrame(frame: string): StreamEvent | null {
  // 后端发心跳时只发注释行 ": heartbeat"，跳过
  const dataLines: string[] = []
  for (const line of frame.split('\n')) {
    if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart())
    }
  }
  if (dataLines.length === 0) return null
  try {
    return JSON.parse(dataLines.join('\n')) as StreamEvent
  } catch {
    return null
  }
}

// ---- 健康检查（可选，用于设置页面探测后端） ----

export async function healthCheck(): Promise<{ status: string; checks: Record<string, string> }> {
  const resp = await fetch('/health')
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.json()
}

// ---- 牌名自动补全 ----

export async function autocompleteCard(
  q: string,
  settings: AppSettings,
  signal?: AbortSignal,
  limit = 8,
): Promise<AutocompleteResponse> {
  const params = new URLSearchParams({ q, limit: String(limit) })
  const resp = await fetch(`/v1/cards/autocomplete?${params}`, {
    headers: { ...apiKeyHeader(settings.apiKey) },
    signal,
  })
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.json()
}
