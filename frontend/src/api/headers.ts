import type { LLMOverride } from './types'

/** 拼装 BYOK 请求头。空值字段不发，避免覆盖服务端默认 */
export function byokHeaders(llm: LLMOverride | undefined): Record<string, string> {
  if (!llm) return {}
  const h: Record<string, string> = {}
  if (llm.apiKey?.trim()) h['X-LLM-Api-Key'] = llm.apiKey.trim()
  if (llm.baseUrl?.trim()) h['X-LLM-Base-URL'] = llm.baseUrl.trim()
  if (llm.model?.trim()) h['X-LLM-Model'] = llm.model.trim()
  return h
}

export function apiKeyHeader(apiKey: string | undefined): Record<string, string> {
  return apiKey?.trim() ? { 'X-API-Key': apiKey.trim() } : {}
}
