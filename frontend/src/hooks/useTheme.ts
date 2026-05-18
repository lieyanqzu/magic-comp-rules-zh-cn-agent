import { useCallback, useEffect, useState } from 'react'

type Theme = 'light' | 'dark'
type ThemePref = Theme | 'system'

const STORAGE_KEY = 'mtg-judge-theme-v1'

function getSystemTheme(): Theme {
  if (typeof window === 'undefined') return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function loadPref(): ThemePref {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v === 'light' || v === 'dark' || v === 'system') return v
  } catch {
    // ignore
  }
  return 'system'
}

function applyTheme(theme: Theme): void {
  const root = document.documentElement
  root.classList.toggle('dark', theme === 'dark')
  // 让浏览器原生控件（滚动条、表单）跟随主题
  root.style.colorScheme = theme
}

/** 主题：light / dark / system；持久化偏好；跟随系统变化（仅当 pref=system）。 */
export function useTheme() {
  const [pref, setPref] = useState<ThemePref>(loadPref)
  const [resolved, setResolved] = useState<Theme>(() =>
    loadPref() === 'system' ? getSystemTheme() : (loadPref() as Theme),
  )

  useEffect(() => {
    const next: Theme = pref === 'system' ? getSystemTheme() : pref
    setResolved(next)
    applyTheme(next)
    try {
      localStorage.setItem(STORAGE_KEY, pref)
    } catch {
      // ignore
    }
  }, [pref])

  // pref=system 时跟随系统变化
  useEffect(() => {
    if (pref !== 'system') return
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const handler = (e: MediaQueryListEvent) => {
      const next: Theme = e.matches ? 'dark' : 'light'
      setResolved(next)
      applyTheme(next)
    }
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [pref])

  const toggle = useCallback(() => {
    // 简单切换：当前是 dark → light，否则 → dark；丢掉 system 偏好
    setPref((prev) => {
      const current: Theme = prev === 'system' ? getSystemTheme() : prev
      return current === 'dark' ? 'light' : 'dark'
    })
  }, [])

  return { pref, theme: resolved, setPref, toggle }
}
