// API-клиент: все запросы к бэкенду в одном месте.
// Токен хранится в localStorage и автоматически подставляется в заголовок.

const TOKEN_KEY = 'titan_token'

// ---------- Токен ----------

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

// ---------- Универсальный запрос ----------

// Ошибка авторизации/доступа — фронт по ней понимает, что надо перелогиниться
export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/** GET/POST/PATCH-запрос с JWT; при 401 токен сбрасывается. */
export async function apiFetch<T>(
  path: string,
  options: { method?: string; body?: unknown } = {}
): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(path, {
    method: options.method ?? 'GET',
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  })

  if (res.status === 401) {
    // Токен протух/невалиден — выкидываем на логин
    clearToken()
    throw new ApiError(401, 'Не авторизован')
  }
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new ApiError(res.status, detail || `Ошибка ${res.status}`)
  }
  return res.json() as Promise<T>
}

// ---------- Типы данных (соответствуют контракту бэкенда) ----------

export interface LivePoint {
  value: number
  quality: 'good' | 'suspect' | 'bad'
}

/** Узел дерева объектов завода (специф §16): ПС 110/10 -> плечи/секции -> фидеры. */
export interface FactoryTree {
  id: string
  name: string
  kind: 'group' | 'incomer' | 'incomer110' | 'incomer10' | 'sectional' | 'transformer' | 'bus' | 'feeder'
  voltage?: string
  feeders?: string[]           // подписи фидеров (только у секций)
  children: FactoryTree[]
}

export interface UserInfo {
  username: string
  role: 'admin' | 'dispatcher' | 'viewer'
}

export interface UserRow {
  id: number
  username: string
  role: string
  is_active: boolean
}

export interface EventRow {
  id: number
  time: string
  object: string
  cls: string
  priority: string
  code: string
  message: string
  value: number | null
  setpoint: number | null
  acked: boolean
  acked_by: string | null
  acked_at: string | null
}

export interface ActionRow {
  time: string
  username: string
  action: string
  object: string | null
  result: string
  details: string | null
}

export interface WaveformData {
  id: number
  time: string
  object: string
  fault_code: number
  sample_rate_hz: number
  duration_ms: number
  channels: Record<string, number[]>
}

export interface SeriesStats {
  count: number
  min: number
  max: number
  mean: number
  std: number
}

export interface AnalyticsSeries {
  point_code: string
  times: string[]
  values: number[]
  moving_avg: (number | null)[]
  avg_window_s: number
  stats: SeriesStats
}

export interface HistogramStats {
  count: number
  bin_counts: number[]
  bin_edges: number[]
  mode: number
  median: number
  mean: number
  std: number
  std3?: number
  max_bin_count: number
}

export interface ShewhartData {
  cl: number
  ucl: number
  lcl: number
  points: number[]
  out_of_control: number[]
}

// ---------- Конкретные вызовы API ----------

export const api = {
  /** Вход: получаем JWT и сразу сохраняем. */
  async login(username: string, password: string): Promise<UserInfo> {
    const res = await apiFetch<{ access_token: string; role: UserInfo['role'] }>(
      '/api/auth/login',
      { method: 'POST', body: { username, password } }
    )
    setToken(res.access_token)
    return { username, role: res.role }
  },

  logout() {
    clearToken()
  },

  me: () => apiFetch<UserInfo>('/api/me'),
  live: () => apiFetch<Record<string, LivePoint>>('/api/live'),

  /** Дерево объектов завода (специф §16): factory -> ТП -> фидеры. */
  objectsTree: () => apiFetch<FactoryTree>('/api/objects/tree'),

  /** Статистика аварий по объектам (тепловая карта, специф §16). */
  alarmStats: (minutes = 60) =>
    apiFetch<{ minutes: number; objects: Array<{ object: string; alarms: number }> }>(
      `/api/objects/alarm-stats?minutes=${minutes}`
    ),
  events: (cls?: string) =>
    apiFetch<{ count: number; events: EventRow[] }>(
      `/api/events${cls ? `?cls=${cls}` : ''}`
    ),
  ackEvent: (id: number) =>
    apiFetch<{ ok: boolean }>(`/api/events/${id}/ack`, { method: 'POST' }),

  // --- admin ---
  users: () => apiFetch<{ users: UserRow[] }>('/api/admin/users'),
  createUser: (username: string, password: string, role: string) =>
    apiFetch<UserRow>('/api/admin/users', {
      method: 'POST',
      body: { username, password, role },
    }),
  updateUser: (id: number, patch: { role?: string; is_active?: boolean }) =>
    apiFetch<UserRow>(`/api/admin/users/${id}`, { method: 'PATCH', body: patch }),
  actions: () => apiFetch<{ count: number; actions: ActionRow[] }>('/api/actions'),

  /** Пульт симулятора: ТОЧЕЧНАЯ инъекция на объект (специф §16). */
  simFault: (objectId: string, scenario: string, durationS = 8) =>
    apiFetch<{ ok: boolean; object: string; scenario: string }>(
      `/api/sim/fault?object_id=${encodeURIComponent(objectId)}&scenario=${scenario}&duration_s=${durationS}`,
      { method: 'POST' }
    ),

  // --- Телеуправление (двухшаговый алгоритм, ТЗ 3.3.2) ---
  /** Шаг 1: выбор объекта и команды -> токен (или 409 при блокировке). */
  tuSelect: (objectName: string, action: 'close' | 'open') =>
    apiFetch<{ token: string; expires_in_s: number }>('/api/commands/select', {
      method: 'POST',
      body: { object_name: objectName, action },
    }),
  /** Шаг 2: подтверждение по токену -> ожидание смены состояния (до 5 с). */
  tuOperate: async (token: string) => {
    try {
      return await apiFetch<{ ok: boolean; state: number; message: string }>(
        '/api/commands/operate',
        { method: 'POST', body: { token } }
      )
    } catch (e) {
      // Бэкенд возвращает detail в JSON-ошибке — распаковываем в человекочитаемый текст
      if (e instanceof ApiError) {
        try {
          const parsed = JSON.parse(e.message)
          if (parsed.detail) throw new ApiError(e.status, parsed.detail)
        } catch (parseErr) {
          // message не JSON — оставляем как есть (это уже распакованная ошибка)
          if (parseErr instanceof ApiError) throw parseErr
        }
      }
      throw e
    }
  },

  /** Осциллограмма по id (просмотр из строки журнала аварий). */
  waveform: (id: number) => apiFetch<WaveformData>(`/api/waveforms/${id}`),

  // --- Аналитика M5 (специф §9.2) ---
  analyticsSeries: (pointCode: string, minutes: number, avgS: number) =>
    apiFetch<AnalyticsSeries>(`/api/analytics/series?point_code=${pointCode}&minutes=${minutes}&avg_s=${avgS}`),
  analyticsHistogram: (pointCode: string, minutes: number, bins = 20) =>
    apiFetch<HistogramStats>(`/api/analytics/histogram?point_code=${pointCode}&minutes=${minutes}&bins=${bins}`),
  analyticsShewhart: (pointCode: string, minutes: number) =>
    apiFetch<ShewhartData>(`/api/analytics/shewhart?point_code=${pointCode}&minutes=${minutes}`),
  analyticsCusum: (pointCode: string, minutes: number, target: number) =>
    apiFetch<{ point_code: string; target: number; values: number[] }>(
      `/api/analytics/cusum?point_code=${pointCode}&minutes=${minutes}&target=${target}`),

  /** Ссылка на экспорт (открывается браузером как скачивание файла). */
  exportCsvUrl: (pointCode: string, minutes: number) =>
    `/api/export/csv?point_code=${pointCode}&minutes=${minutes}`,
  exportXlsxUrl: (pointCode: string, minutes: number) =>
    `/api/export/xlsx?point_code=${pointCode}&minutes=${minutes}`,
}
