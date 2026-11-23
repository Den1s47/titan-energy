// Аларм-бар — окно сообщений внизу экрана (ТЗ 3.3.3 / ISA-18.2):
// - порядок: неквитированные сверху по приоритету, потом квитированные;
// - мигание неквитированных critical/high до квитирования;
// - поля: время (мс), приоритет цветом, объект, описание, значение/уставка;
// - кнопка «Подтвердить» — dispatcher+;
// - клик по объекту в строке — переход к месту аварии на мнемосхеме (специф §16:
//   «ТП-1, Ф-2» -> /?obj=tp1_f2 — мнемосхема раскроет нужный уровень).

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api, type EventRow, type FactoryTree } from '../api'
import { useAuth } from '../authStore'
import ErrorBoundary from '../ErrorBoundary'

// Вес приоритета для сортировки (ISA-18.2): critical сверху
const PRIO_WEIGHT: Record<string, number> = { critical: 0, high: 1, low: 2, info: 3 }

function fmtTime(iso: string) {
  return iso.slice(11, 23)
}

/**
 * Имя объекта события («ТП-1, Ф-2») -> id узла дерева ('tp1_f2').
 * Сопоставление по справочнику дерева, загруженного заранее; если объект
 * не найден (событие старого формата) — возвращаем null (перехода нет).
 */
export function objectIdFromName(name: string, tree: FactoryTree | undefined): string | null {
  if (!tree || !name) return null
  // Ищем конечный узел, чьё «полное имя» (родитель + узел) совпадает
  const walk = (n: FactoryTree, parentName: string | null): string | null => {
    const full = parentName ? `${parentName}, ${n.name}` : n.name
    if (full === name) return n.id
    for (const c of n.children) {
      const found = walk(c, n.id === 'factory' ? null : n.name)
      if (found) return found
    }
    return null
  }
  return walk(tree, null)
}

// ErrorBoundary вокруг аларм-бара: его падение не должно уносить страницы
export default function AlarmBar() {
  return (
    <ErrorBoundary label="Аларм-бар">
      <AlarmBarInner />
    </ErrorBoundary>
  )
}

function AlarmBarInner() {
  const role = useAuth((s) => s.user?.role)
  const canAck = role === 'dispatcher' || role === 'admin'
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  // Дерево завода — для преобразования «ТП-1, Ф-2» -> id объекта при переходе
  const { data: tree } = useQuery({
    queryKey: ['objectsTree'],
    queryFn: api.objectsTree,
    staleTime: Infinity,
  })

  // Переход к месту аварии: мнемосхема с параметром ?obj=<id> раскрывает уровень
  function goToObject(name: string) {
    const id = objectIdFromName(name, tree)
    if (id) navigate(`/?obj=${id}`)
  }

  // Неквитированные события — обновление раз в 1 с (аларм-бар должен быть мгновенным)
  const { data: unacked } = useQuery({
    queryKey: ['alarms-unacked'],
    queryFn: () => api.events(undefined),   // все классы — аларм-бар показывает всё
    refetchInterval: 1000,
    select: (d) => d.events.filter((e) => !e.acked),
  })
  const { data: acked } = useQuery({
    queryKey: ['alarms-acked'],
    queryFn: () => api.events(undefined),
    refetchInterval: 1000,
    select: (d) => d.events.filter((e) => e.acked).slice(0, 5),
  })

  async function onAck(id: number) {
    await api.ackEvent(id)
    queryClient.invalidateQueries({ queryKey: ['alarms-unacked'] })
    queryClient.invalidateQueries({ queryKey: ['alarms-acked'] })
  }

  // Порядок по ТЗ: неквитированные по приоритету сверху, затем квитированные
  const sortedUnacked = [...(unacked ?? [])].sort(
    (a, b) => (PRIO_WEIGHT[a.priority] ?? 9) - (PRIO_WEIGHT[b.priority] ?? 9)
  )

  return (
    <div style={{
      position: 'fixed', bottom: 0, left: 0, right: 0,
      background: 'var(--panel)', borderTop: '2px solid var(--border)',
      maxHeight: 180, overflowY: 'auto', zIndex: 50,
    }}>
      <table style={{ margin: 0 }}>
        <tbody>
          {/* --- активные неквитированные: мигают до ack --- */}
          {sortedUnacked.map((e: EventRow) => (
            <tr key={e.id} className={e.priority === 'critical' ? 'unacked-critical' : ''}>
              <td className={`prio-${e.priority}`} style={{ width: 90 }}>
                <span className={e.priority === 'critical' || e.priority === 'high' ? 'mnemo-blink-inline' : ''}>
                  {e.priority}
                </span>
              </td>
              <td style={{ width: 110 }}>{fmtTime(e.time)}</td>
              <td
                style={{ width: 80, cursor: 'pointer', color: '#4d9fff', textDecoration: 'underline' }}
                title="Перейти к месту аварии на мнемосхеме"
                onClick={() => goToObject(e.object)}
              >
                {e.object}
              </td>
              <td>{e.message}</td>
              <td style={{ width: 140 }}>
                {e.value != null && `знач.: ${e.value.toFixed(1)}`}
                {e.setpoint != null && ` / уставка: ${e.setpoint}`}
              </td>
              <td style={{ width: 110 }}>
                {canAck && <button className="btn secondary" onClick={() => onAck(e.id)}>Подтвердить</button>}
              </td>
            </tr>
          ))}
          {/* --- квитированные: мигание прекращено, цвет остался --- */}
          {(acked ?? []).map((e: EventRow) => (
            <tr key={`a${e.id}`} style={{ opacity: 0.55 }}>
              <td className={`prio-${e.priority}`} style={{ width: 90 }}>{e.priority}</td>
              <td style={{ width: 110 }}>{fmtTime(e.time)}</td>
              <td style={{ width: 80 }}>{e.object}</td>
              <td>{e.message} <span className="muted">(ack: {e.acked_by})</span></td>
              <td style={{ width: 140 }}></td>
              <td style={{ width: 110 }}></td>
            </tr>
          ))}
          {sortedUnacked.length === 0 && (acked ?? []).length === 0 && (
            <tr><td className="muted" style={{ padding: 8 }}>Активных сообщений нет</td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
