import { useCallback, useEffect, useState } from 'react'
import type { AppSettings } from '../api/types'

const STORAGE_KEY = 'mtg-judge-settings-v1'

// 单次 LLM 响应默认 token 上限。32K 是大多数主流模型（Claude 4.x / GPT-4 系列 / Qwen 等）
// 都能稳定吐出的范围；过小会让长答案被截断，过大上游会 4xx 拒。
export const DEFAULT_MAX_TOKENS = 32000

const DEFAULT_SETTINGS: AppSettings = {
  apiKey: '',
  llm: {},
  stream: true,
}

function loadSettings(): AppSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULT_SETTINGS
    const parsed = JSON.parse(raw) as Partial<AppSettings>
    return {
      apiKey: parsed.apiKey ?? '',
      llm: parsed.llm ?? {},
      stream: parsed.stream ?? true,
    }
  } catch {
    return DEFAULT_SETTINGS
  }
}

/**
 * 应用设置：localStorage 持久化。
 *
 * 安全：BYOK 字段（apiKey/baseUrl/model）只存浏览器本地，永远不进后端日志。
 * 同源访问下后端无法读取 localStorage；唯一暴露面是用户自己的浏览器。
 */
export function useSettings() {
  const [settings, setSettings] = useState<AppSettings>(loadSettings)

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings))
    } catch {
      // localStorage 满 / 隐私模式：忽略，下次重启就丢
    }
  }, [settings])

  const update = useCallback((patch: Partial<AppSettings>) => {
    setSettings((prev) => ({ ...prev, ...patch }))
  }, [])

  const updateLlm = useCallback((patch: Partial<AppSettings['llm']>) => {
    setSettings((prev) => ({ ...prev, llm: { ...prev.llm, ...patch } }))
  }, [])

  const reset = useCallback(() => {
    setSettings(DEFAULT_SETTINGS)
    localStorage.removeItem(STORAGE_KEY)
  }, [])

  return { settings, update, updateLlm, reset }
}
