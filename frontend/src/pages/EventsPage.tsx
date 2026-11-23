// Журнал событий: таблица с приоритетами ISA-18.2 и кнопкой квитирования.
// Квитировать может только dispatcher и выше (кнопка скрыта у viewer).

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type EventRow } from '../api'
import { useAuth } from '../authStore'

// Классы событий = разделы «Журналы» из ТЗ
const CLASSES = [
  { value: '', label: 'Все события' },
  { value: 'alarm', label: 'Аварийные события' },
  { value: 'abnormal', label: 'Ненормальные режимы' },
  { value: 'state', label: 'Изменения состояний' },
] as const

function fmtTime(iso: string) {
  return iso.slice(11, 23)   // ЧЧ:ММ:СС.мсс — точность до мс (ТЗ 3.3.3)
}

export default function EventsPage() {
  const role = useAuth((s) => s.user?.role)
  const canAck = role === 'dispatcher' || role === 'admin'
  const queryClient = useQueryClient()
  const [cls, setCls] = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['events', cls],
    queryFn: () => api.events(cls || undefined),
    refetchInterval: 2000,     // журнал обновляется раз в 2 с
  })

  async function onAck(id: number) {
    await api.ackEvent(id)
    queryClient.invalidateQueries({ queryKey: ['events'] })
  }

  return (
    <>
      <h2>Журнал событий</h2>
      <div className="row" style={{ marginBottom: 12 }}>
        <label>
          Раздел:{' '}
          <select value={cls} onChange={(e) => setCls(e.target.value)}>
            {CLASSES.map((c) => (
              <option key={c.value} value={c.value}>{c.label}</option>
            ))}
          </select>
        </label>
        <span className="muted">Неквитированные алармы мигают в АРМ (M4)</span>
      </div>

      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Время</th><th>Приоритет</th><th>Объект</th>
              <th>Сообщение</th><th>Значение</th><th></th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={6}>Загрузка...</td></tr>
            )}
            {data?.events.map((ev: EventRow) => (
              <tr key={ev.id} className={ev.priority === 'critical' && !ev.acked ? 'unacked-critical' : ''}>
                <td>{fmtTime(ev.time)}</td>
                <td className={`prio-${ev.priority}`}>
                  {ev.priority}{!ev.acked && ' ●'}
                </td>
                <td>{ev.object}</td>
                <td>
                  {ev.message}
                  {ev.setpoint != null && (
                    <span className="muted"> (уставка: {ev.setpoint})</span>
                  )}
                </td>
                <td>{ev.value != null ? ev.value.toFixed(1) : ''}</td>
                <td>
                  {!ev.acked && canAck && (
                    <button className="btn secondary" onClick={() => onAck(ev.id)}>
                      Подтвердить
                    </button>
                  )}
                  {ev.acked && (
                    <span className="muted">ack: {ev.acked_by}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
