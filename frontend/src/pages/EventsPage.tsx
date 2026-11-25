// Журнал событий: таблица с приоритетами ISA-18.2 и кнопкой квитирования.
// Квитировать может только dispatcher и выше (кнопка скрыта у viewer).
//
// Усилено по замечанию проверяющих (показ 26.11) — «журнал должен явно
// подсвечивать и мигать, как в их SCADA»:
//   - НЕКВИТИРОВАННЫЙ critical: вся строка залита красным и МИГАЕТ
//     (animation row-blink), текст белым;
//   - неквитированные high: оранжевая заливка, лёгкая пульсация;
//   - квитированные — приглушены, мигание прекращено (ISA-18.2);
//   - ЗВУКОВОЙ СИГНАЛ: новый непроквитированный critical → короткий писк
//     через WebAudio (без файлов, генерация осциллятором), повтор раз в
//     8 с, пока есть неквитированные critical; кнопка «🔊/🔇» — вкл/выкл
//     (сохраняется в localStorage); звук разрешён после первого клика
//     по странице (политика автоплея браузеров).

import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
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

// ---------- Звуковой сигнал (WebAudio, без файлов) ----------
let audioCtx: AudioContext | null = null
/** Короткий писк 880 Гц 200 мс — «внимание» (как в промышленных SCADA). */
function beep() {
  try {
    audioCtx = audioCtx ?? new AudioContext()
    if (audioCtx.state === 'suspended') return   // нет разрешения пользователя
    const osc = audioCtx.createOscillator()
    const gain = audioCtx.createGain()
    osc.frequency.value = 880
    gain.gain.setValueAtTime(0.12, audioCtx.currentTime)
    gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.2)
    osc.connect(gain).connect(audioCtx.destination)
    osc.start()
    osc.stop(audioCtx.currentTime + 0.2)
  } catch { /* звук не критичен */ }
}

// Стили мигания строк (глобально, один раз)
const ROW_STYLES = `
@keyframes row-blink-critical { 50% { background: #5a1512; } }
@keyframes row-pulse-high { 50% { background: rgba(255,159,46,0.28); } }
tr.ev-critical-unacked {
  background: #7a1d18; color: #fff;
  animation: row-blink-critical 1s step-start infinite;
  font-weight: 700;
}
tr.ev-critical-unacked td { color: #fff; border-color: #a33; }
tr.ev-high-unacked {
  background: rgba(255,159,46,0.18);
  animation: row-pulse-high 2s ease-in-out infinite;
}
tr.ev-acked-muted { opacity: 0.55; }
`

export default function EventsPage() {
  const role = useAuth((s) => s.user?.role)
  const canAck = role === 'dispatcher' || role === 'admin'
  const queryClient = useQueryClient()
  const [cls, setCls] = useState('')
  // Звук: вкл по умолчанию, состояние в localStorage (переживает перезагрузку)
  const [soundOn, setSoundOn] = useState(() => localStorage.getItem('ev-sound') !== 'off')
  // Множество id уже «прописанных» критических — чтобы пикать только на НОВЫЕ
  const seenCritical = useRef<Set<number>>(new Set())
  const seenInitialized = useRef(false)

  const { data, isLoading } = useQuery({
    queryKey: ['events', cls],
    queryFn: () => api.events(cls || undefined),
    refetchInterval: 2000,     // журнал обновляется раз в 2 с
  })

  // Пищим при появлении НОВОГО неквитированного critical; повтор каждые 8 с,
  // пока такие существуют (стандартное поведение SCADA-систем).
  const unackedCritical = (data?.events ?? []).filter(
    (ev: EventRow) => ev.priority === 'critical' && !ev.acked)
  const unackedIds = unackedCritical.map((e) => e.id).join(',')
  useEffect(() => {
    // Первый заход: просто запоминаем текущие (не пикать за старые при открытии)
    if (!seenInitialized.current) {
      unackedCritical.forEach((ev) => seenCritical.current.add(ev.id))
      seenInitialized.current = true
      return
    }
    const fresh = unackedCritical.filter((ev) => !seenCritical.current.has(ev.id))
    fresh.forEach((ev) => seenCritical.current.add(ev.id))
    if (!soundOn) return
    if (unackedCritical.length === 0) return
    if (fresh.length > 0) beep()
    const t = setInterval(() => { if (unackedCritical.length > 0) beep() }, 8000)
    return () => clearInterval(t)
  }, [unackedIds, soundOn])

  function toggleSound() {
    const next = !soundOn
    setSoundOn(next)
    localStorage.setItem('ev-sound', next ? 'on' : 'off')
    if (next) beep()   // подтверждение клика (заодно разблокирует AudioContext)
  }

  async function onAck(id: number) {
    await api.ackEvent(id)
    queryClient.invalidateQueries({ queryKey: ['events'] })
  }

  /** Класс строки: заливка/мигание по приоритету и квитированию. */
  function rowClass(ev: EventRow): string {
    if (ev.acked) return 'ev-acked-muted'
    if (ev.priority === 'critical') return 'ev-critical-unacked'
    if (ev.priority === 'high') return 'ev-high-unacked'
    return ''
  }

  return (
    <>
      <style>{ROW_STYLES}</style>
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
        {/* Звуковой сигнал неквитированных critical (замечание проверяющих) */}
        <button className="btn secondary" onClick={toggleSound}
                title="Звуковой сигнал при новой критической аварии">
          {soundOn ? '🔊 Звук вкл' : '🔇 Звук выкл'}
        </button>
        {unackedCritical.length > 0 && (
          <span style={{ color: '#ff4444', fontWeight: 700 }}>
            ⚠ Неквитированных критических: {unackedCritical.length}
          </span>
        )}
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
              <tr key={ev.id} className={rowClass(ev)}>
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
