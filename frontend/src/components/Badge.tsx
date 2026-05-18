import type { ReactNode } from 'react'

interface BadgeProps {
  children: ReactNode
  variant?: 'high' | 'medium' | 'low' | 'neutral' | 'warning'
  className?: string
}

const STYLES: Record<NonNullable<BadgeProps['variant']>, string> = {
  high: 'bg-emerald-100 text-emerald-700 ring-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:ring-emerald-800',
  medium: 'bg-amber-100 text-amber-700 ring-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:ring-amber-800',
  low: 'bg-rose-100 text-rose-700 ring-rose-200 dark:bg-rose-950/40 dark:text-rose-300 dark:ring-rose-800',
  neutral: 'bg-slate-100 text-slate-700 ring-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-700',
  warning: 'bg-orange-100 text-orange-700 ring-orange-200 dark:bg-orange-950/40 dark:text-orange-300 dark:ring-orange-800',
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
