import type { CardRef } from '../api/types'
import { Badge } from './Badge'

interface CardCardProps {
  card: CardRef
}

/** 渲染单张牌：中文优先，英文降级。 */
export function CardCard({ card }: CardCardProps) {
  const displayText = card.translated_text || card.oracle_text || card.display_text || ''
  const displayType = card.translated_type || card.type_line || card.display_type || ''
  const enText = card.translated_text ? card.oracle_text_en ?? card.oracle_text : null
  const ptLine = formatPT(card)

  return (
    <article className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <header className="mb-2 flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <h3 className="text-base font-semibold text-slate-900">{card.name}</h3>
        {card.oracle_name && card.oracle_name !== card.name && (
          <span className="text-sm text-slate-500">{card.oracle_name}</span>
        )}
        {card.mana_cost && (
          <span className="ml-auto font-mono text-sm text-slate-600">{card.mana_cost}</span>
        )}
      </header>
      {(displayType || ptLine) && (
        <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
          {displayType && <Badge variant="neutral">{displayType}</Badge>}
          {ptLine && <Badge variant="neutral">{ptLine}</Badge>}
          {card.layout && card.layout !== 'normal' && (
            <Badge variant="warning">{card.layout}</Badge>
          )}
        </div>
      )}
      {displayText && (
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-800">{displayText}</p>
      )}
      {enText && enText !== displayText && (
        <details className="mt-2 text-xs text-slate-500">
          <summary className="cursor-pointer hover:text-slate-700">英文原文</summary>
          <p className="mt-1 whitespace-pre-wrap text-slate-600">{enText}</p>
        </details>
      )}
      {card.faces.length > 1 && (
        <div className="mt-3 space-y-2 border-t border-slate-100 pt-3">
          <div className="text-xs font-medium text-slate-500">其他牌面</div>
          {card.faces.slice(1).map((f, i) => (
            <div key={i} className="text-sm">
              <div className="flex items-baseline gap-2">
                <span className="font-medium text-slate-800">
                  {f.face_name_zh || f.face_name}
                </span>
                {f.mana_cost && <span className="font-mono text-xs text-slate-500">{f.mana_cost}</span>}
              </div>
              {(f.translated_type || f.type_line) && (
                <div className="text-xs text-slate-500">{f.translated_type || f.type_line}</div>
              )}
              {(f.translated_text || f.oracle_text) && (
                <p className="mt-1 whitespace-pre-wrap text-slate-700">
                  {f.translated_text || f.oracle_text}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
      {card.rulings.length > 0 && (
        <details className="mt-3 border-t border-slate-100 pt-2 text-xs text-slate-600">
          <summary className="cursor-pointer hover:text-slate-800">官方裁定 {card.rulings.length} 条</summary>
          <ul className="mt-2 space-y-2">
            {card.rulings.map((r, i) => (
              <li key={i} className="rounded bg-slate-50 px-2 py-1.5">
                {r.date && <span className="mr-2 text-slate-400">{r.date}</span>}
                <span>{r.text}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </article>
  )
}

function formatPT(card: CardRef): string {
  if (card.power != null && card.toughness != null) return `${card.power}/${card.toughness}`
  if (card.defense != null) return `防御 ${card.defense}`
  return ''
}
