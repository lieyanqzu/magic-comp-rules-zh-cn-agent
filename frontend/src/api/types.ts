// 与后端 backend/app/agent/schemas.py 对齐
// 任何字段调整都需要双向同步

export interface CardFace {
  face_name?: string
  face_name_zh?: string
  oracle_text?: string
  translated_text?: string
  type_line?: string
  translated_type?: string
  mana_cost?: string
  power?: string | null
  toughness?: string | null
  defense?: string | null
}

export interface CardRuling {
  date?: string
  text?: string
}

export interface CardRef {
  name: string
  oracle_name?: string | null
  oracle_text?: string | null
  oracle_text_en?: string | null
  translated_text?: string | null
  translated_type?: string | null
  type_line?: string | null
  type_line_en?: string | null
  mana_cost?: string | null
  power?: string | null
  toughness?: string | null
  defense?: string | null
  layout?: string | null
  display_text?: string | null
  display_type?: string | null
  faces: CardFace[]
  rulings: CardRuling[]
}

export interface RuleRef {
  section_id: string
  title: string
  content_snippet: string
  source_path: string
}

export type Confidence = 'high' | 'medium' | 'low'

export interface JudgeResponse {
  answer: string
  summary: string
  confidence: Confidence
  cards: CardRef[]
  rules: RuleRef[]
  reasoning_summary: string
  needs_human_judge: boolean
  latency_ms?: number | null
}

// ---- 流式事件 ----

export interface StartEvent {
  type: 'start'
  question: string
  request_id?: string
}

export interface ThinkingEvent {
  type: 'thinking'
  content: string
}

export interface ToolCallEvent {
  type: 'tool_call'
  tool: 'resolve_card' | 'search_rules' | 'search_cards' | string
  args: Record<string, unknown>
}

export interface ToolResultEvent {
  type: 'tool_result'
  tool: string
  status?: 'found' | 'not_found' | 'empty' | 'error' | 'ok'
  // resolve_card
  name?: string
  oracle_name?: string
  display_text?: string
  display_type?: string
  mana_cost?: string
  has_faces?: boolean
  has_rulings?: boolean
  // search_rules
  query?: string
  section_id?: string
  results_count?: number
  /** reranker 给出的最高分（0~1）。null 表示无法获取（rerank 失败 / 无候选） */
  best_score?: number | null
  /** 后端基于 best_score 与阈值给出的置信度提示，用于 LLM 决策是否继续检索 */
  confidence_hint?: 'high' | 'medium' | 'low' | null
  /**
   * 重排状态，反映本次分数信号是否可信：
   * - ok       真实精排
   * - cached   命中重排或 Redis 检索缓存（信号仍可信）
   * - fallback API 失败走线性兜底（信号不可信，仅作排序占位）
   * - disabled reranker_enabled=false（同上）
   * - no_input 无候选 chunk
   */
  rerank_status?: 'ok' | 'cached' | 'fallback' | 'disabled' | 'no_input' | null
  /** 后端自动扩展的同义词（含原 query 词） */
  expanded_terms?: string[] | null
  /** 本次工具调用完成后剩余的工具调用预算 */
  rounds_left?: number | null
  /** true = 后端检测到与之前查询参数完全相同，已直接返回缓存（提示 LLM 别重复搜） */
  duplicated_call?: boolean | null
  /** true = 已累计足够的高置信命中，本次调用被机制级短路，让 LLM 立即收尾 */
  high_hit_satisfied?: boolean | null
  // search_cards
  count?: number
  items?: Array<Record<string, unknown>>
  // error
  error?: string
}

export interface AnswerEvent {
  type: 'answer'
  data: JudgeResponse
}

export interface ErrorEvent {
  type: 'error'
  content: string
}

export interface DoneEvent {
  type: 'done'
  latency_ms?: number
  request_id?: string
}

export type StreamEvent =
  | StartEvent
  | ThinkingEvent
  | ToolCallEvent
  | ToolResultEvent
  | AnswerEvent
  | ErrorEvent
  | DoneEvent

// ---- 对话历史（发给后端） ----

export interface HistoryMessage {
  role: 'user' | 'assistant'
  content: string
}

// ---- Autocomplete ----

export interface AutocompleteItem {
  name_en: string
  name_zh: string
  type_zh?: string
  mana_cost?: string
  set?: string
  collector_number?: string
  rarity?: string
}

export interface AutocompleteResponse {
  items: AutocompleteItem[]
}

// ---- BYOK / 设置 ----

export interface LLMOverride {
  apiKey?: string
  baseUrl?: string
  model?: string
  /** 单次响应最大 token 数。空 / undefined 时使用服务器默认（32000）。 */
  maxTokens?: number
}

export interface AppSettings {
  apiKey?: string // 后端 X-API-Key（与 BYOK 不同）
  llm: LLMOverride // BYOK
  stream: boolean
}
