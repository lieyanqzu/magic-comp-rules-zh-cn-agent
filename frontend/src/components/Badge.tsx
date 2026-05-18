import type { ReactNode } from 'react'

interface BadgeProps {
  children: ReactNode
  variant?: 'high' | 'medium' | 'low' | 'neutral' | 'warning'
  className?: string
}

const STYLES: Record<NonNullable<BadgeProps['variant']>, string> = {
  high: 'bg-emerald-100 text-emerald-700 ring-emerald-200',
  medium: 'bg-amber-100 text-amber-700 ring-amber-200',
  low: 'bg-rose-100 text-rose-700 ring-rose-200',
  neutral: 'bg-slate-100 text-slate-700 ring-slate-200',
  warning: 'bg-orange-100 text-orange-700 ring-orange-200',
}

export function Badge({ children, variant = 'neutral', className = '' }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${STYLES[variant]} ${className}`}
    >
      {children}
    </span>
  )
}
