// Глобальное состояние аутентификации (Zustand — лёгкий стор, специф §3).
// Хранит текущего пользователя; сбрасывается при выходе или 401.

import { create } from 'zustand'
import { api, clearToken, getToken, type UserInfo } from './api'

interface AuthState {
  user: UserInfo | null      // null = не вошёл
  loading: boolean           // идёт проверка токена при старте
  login: (username: string, password: string) => Promise<void>
  logout: () => void
  /** Проверить сохранённый токен при загрузке приложения. */
  restore: () => Promise<void>
}

export const useAuth = create<AuthState>((set) => ({
  user: null,
  loading: true,

  login: async (username, password) => {
    const user = await api.login(username, password)
    set({ user })
  },

  logout: () => {
    api.logout()
    set({ user: null })
  },

  restore: async () => {
    // Токена нет — сразу показываем логин
    if (!getToken()) {
      set({ loading: false })
      return
    }
    try {
      const user = await api.me()
      set({ user, loading: false })
    } catch {
      // токен невалиден — apiFetch уже очистил его
      clearToken()
      set({ user: null, loading: false })
    }
  },
}))
