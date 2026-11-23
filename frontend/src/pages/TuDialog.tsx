// Диалог телеуправления — полный двухшаговый алгоритм ТЗ 3.3.2 / специф §7:
// 1. проверка блокировок (select) — при запрете кнопка неактивна + причина;
// 2. выбор команды «Включить»/«Отключить» с визуальным выделением;
// 3. подтверждение с таймаутом 10 с (обратный отсчёт) — выбор сбрасывается;
// 4. исполнение с ожиданием смены состояния (до 5 с, «Выполняется...»);
// 5. результат: успех / неуспех (состояние «неопределённое»).

import { useEffect, useRef, useState } from 'react'
import { api } from '../api'

// Таймаут выбора из ТЗ (10 с) — обратный отсчёт до автосброса
const SELECT_COUNTDOWN_S = 10

type Phase = 'choose' | 'confirm' | 'executing' | 'done' | 'error'

export default function TuDialog({ objectName, onClose }: {
  objectName: string
  onClose: () => void
}) {
  const [phase, setPhase] = useState<Phase>('choose')
  const [action, setAction] = useState<'close' | 'open' | null>(null)
  const [lockReason, setLockReason] = useState<string | null>(null)
  const [countdown, setCountdown] = useState(SELECT_COUNTDOWN_S)
  const [resultMsg, setResultMsg] = useState('')
  const [errorMsg, setErrorMsg] = useState('')

  // Токен операции (шаг 1) живёт 10 с — держим ref, чтобы не терять при ререндерах
  const tokenRef = useRef<string | null>(null)

  // Обратный отсчёт на шаге «confirm» — авто-сброс выбора по истечении (ТЗ)
  useEffect(() => {
    if (phase !== 'confirm') return
    setCountdown(SELECT_COUNTDOWN_S)
    const timer = setInterval(() => {
      setCountdown((c) => {
        if (c <= 1) {
          clearInterval(timer)
          // Таймаут выбора: команда сбрасывается автоматически (ТЗ 3.3.2)
          setPhase('error')
          setErrorMsg('Время выбора истекло — операция сброшена')
          return 0
        }
        return c - 1
      })
    }, 1000)
    return () => clearInterval(timer)
  }, [phase])

  /** Шаг 1: выбор команды -> проверка блокировок -> получение токена. */
  async function onSelect(a: 'close' | 'open') {
    setErrorMsg('')
    try {
      const res = await api.tuSelect(objectName, a)
      tokenRef.current = res.token
      setAction(a)
      setPhase('confirm')          // переходим к подтверждению с отсчётом
    } catch (e) {
      // 409: блокировка — показываем причину, кнопка исполнения не появится
      setLockReason(e instanceof Error ? e.message : 'Блокировка')
    }
  }

  /** Шаг 2: подтверждение -> исполнение с ожиданием смены состояния. */
  async function onExecute() {
    if (!tokenRef.current) return
    setPhase('executing')
    try {
      const res = await api.tuOperate(tokenRef.current)
      setResultMsg(res.message)    // «Команда исполнена: ...»
      setPhase('done')
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : 'Команда не исполнена')
      setPhase('error')
    }
  }

  return (
    // Полупрозрачная подложка на весь экран
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100,
    }}>
      <div className="card" style={{ width: 420 }}>
        <h3 style={{ marginTop: 0 }}>Телеуправление: {objectName}</h3>

        {/* ---------- Шаг 1: выбор команды ---------- */}
        {phase === 'choose' && (
          <>
            <div className="muted" style={{ marginBottom: 12 }}>
              Выберите команду (проверка блокировок выполняется автоматически)
            </div>
            {lockReason && <div className="error-msg">{lockReason}</div>}
            <div className="row">
              <button className="btn" onClick={() => onSelect('close')}>Включить</button>
              <button className="btn danger" onClick={() => onSelect('open')}>Отключить</button>
              <button className="btn secondary" onClick={onClose}>Отмена</button>
            </div>
          </>
        )}

        {/* ---------- Шаг 2: подтверждение с таймаутом 10 с ---------- */}
        {phase === 'confirm' && (
          <>
            <div>
              Команда: <b>{action === 'close' ? 'ВКЛЮЧИТЬ' : 'ОТКЛЮЧИТЬ'}</b> — {objectName}
            </div>
            <div className="muted" style={{ margin: '8px 0' }}>
              Подтвердите в течение <b style={{ color: countdown <= 3 ? 'var(--bad)' : 'var(--text)' }}>
                {countdown} с
              </b> — иначе выбор сбросится автоматически
            </div>
            <div className="row">
              <button className="btn" onClick={onExecute}>Выполнить</button>
              <button className="btn secondary" onClick={onClose}>Отмена</button>
            </div>
          </>
        )}

        {/* ---------- Шаг 3: исполнение (ожидание смены состояния) ---------- */}
        {phase === 'executing' && (
          <div>
            <div>Выполняется команда {action === 'close' ? 'включения' : 'отключения'}...</div>
            <div className="muted" style={{ marginTop: 8 }}>
              Ожидание изменения состояния объекта (до 5 с)
            </div>
          </div>
        )}

        {/* ---------- Результат: успех ---------- */}
        {phase === 'done' && (
          <>
            <div className="ok-msg">{resultMsg}</div>
            <button className="btn secondary" style={{ marginTop: 12 }} onClick={onClose}>
              Закрыть
            </button>
          </>
        )}

        {/* ---------- Результат: сброс/неуспех ---------- */}
        {phase === 'error' && (
          <>
            <div className="error-msg">{errorMsg}</div>
            <div className="muted" style={{ margin: '8px 0' }}>
              Состояние объекта может быть «неопределённым» — проверьте индикацию на схеме.
            </div>
            <div className="row">
              <button className="btn" onClick={() => { setPhase('choose'); setAction(null) }}>
                Начать заново
              </button>
              <button className="btn secondary" onClick={onClose}>Закрыть</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
