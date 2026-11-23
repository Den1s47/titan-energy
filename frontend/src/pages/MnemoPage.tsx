// Однолинейная мнемосхема ТП (SVG) — главный экран АРМ диспетчера (ТЗ 3.3.1).
//
// Схема (MVP, специф §1 п.3): Ввод 10 кВ -> Трансформатор -> Секция шин 0,4 кВ
// -> 4 фидера. Динамическая индикация по ТЗ:
//   кабели:  под напряжением — сплошная тёмно-серая; обесточена — пунктирная
//            блёклая; неопределённое — оранжевая + ⚠️; аварийное — мигающий красный
//   аппараты (выключатель): вкл — зелёный заполненный квадрат; откл — пустой;
//            местное — «Р»; неопределённое — оранжевый; аварийное — мигающий красный

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { useAuth } from '../authStore'
import TuDialog from './TuDialog'

// ---------- Хелперы индикации ----------

/** Состояние аппарата по значению BREAKER (0/1/2) + признак аварии. */
function breakerVisual(value: number, emergency: boolean) {
  if (emergency) return { fill: '#ff4444', label: 'авария', blink: true }
  switch (value) {
    case 1: return { fill: '#35c46a', label: 'вкл', blink: false }
    case 0: return { fill: 'none', label: 'откл', blink: false }
    case 2: return { fill: '#ff9f2e', label: 'Р', blink: false }
    default: return { fill: '#ff9f2e', label: '?', blink: true }  // неопределённое
  }
}

/** Цвет кабельной линии по напряжению/току (упрощённо: P > 0 = под напряжением). */
function cableVisual(energized: boolean, emergency: boolean) {
  if (emergency) return { stroke: '#ff4444', dash: '', blink: true }
  if (energized) return { stroke: '#555b66', dash: '', blink: false }   // сплошная тёмно-серая
  return { stroke: '#4a4f58', dash: '6,4', blink: false }               // пунктирная блёклая
}

// CSS-анимация мигания аварийных элементов
const blinkStyle = `
@keyframes mnemo-blink { 50% { opacity: 0.25; } }
.mnemo-blink { animation: mnemo-blink 0.8s step-start infinite; }
`

export default function MnemoPage() {
  const role = useAuth((s) => s.user?.role)
  const canTu = role === 'dispatcher' || role === 'admin'

  // Телеметрия 1 Гц
  const { data: live } = useQuery({
    queryKey: ['live'],
    queryFn: api.live,
    refetchInterval: 1000,
  })

  // Состояние системы
  const breaker = live?.BREAKER?.value ?? 1
  const faultCode = live?.FAULT?.value ?? 0
  const emergency = faultCode !== 0
  const energized = breaker === 1

  // Диалог ТУ
  const [tuOpen, setTuOpen] = useState(false)
  const [tuObject, setTuObject] = useState('Ввод')

  // Локальное мигание объекта при выборе ТУ (синее — ТЗ 3.3.2)
  const [selected, setSelected] = useState(false)

  // Сообщение внизу (результат последней операции)
  const [notice, setNotice] = useState<string | null>(null)

  return (
    <>
      <style>{blinkStyle}</style>
      <h2>Мнемосхема ТП-1</h2>
      <div className="muted" style={{ marginBottom: 8 }}>
        ЛКМ по объекту — детальные показатели; {canTu ? 'ПКМ — телеуправление.' : 'ТУ доступно роли dispatcher+.'}
      </div>

      <div className="card" style={{ background: '#e8eaed' }}>
        <svg viewBox="0 0 760 320" style={{ width: '100%', display: 'block' }}>
          {/* ---------- Ввод 10 кВ (кабель сверху) ---------- */}
          <text x={20} y={22} fontSize={13} fill="#555">Ввод 10 кВ</text>
          <line
            x1={380} y1={10} x2={380} y2={70}
            stroke={cableVisual(energized, emergency).stroke}
            strokeWidth={3}
            strokeDasharray={cableVisual(energized, emergency).dash}
            className={cableVisual(energized, emergency).blink ? 'mnemo-blink' : ''}
          />
          {/* Трансформатор (два круга — символ силового трансформатора) */}
          <circle cx={380} cy={88} r={14} fill="none" stroke="#555b66" strokeWidth={2.5} />
          <circle cx={380} cy={104} r={14} fill="none" stroke="#555b66" strokeWidth={2.5} />
          <text x={404} y={100} fontSize={12} fill="#555">Т-1</text>

          {/* Кабель трансформатор -> секция */}
          <line
            x1={380} y1={118} x2={380} y2={148}
            stroke={cableVisual(energized, emergency).stroke}
            strokeWidth={3}
            strokeDasharray={cableVisual(energized, emergency).dash}
          />

          {/* ---------- Секция шин 0,4 кВ ---------- */}
          <line x1={120} y1={150} x2={640} y2={150} stroke="#333" strokeWidth={5} />
          <text x={20} y={155} fontSize={13} fill="#555">Секция 0,4 кВ</text>

          {/* ---------- Выключатель ввода (между Т и секцией, кликабельный) ---------- */}
          <g
            style={{ cursor: 'pointer' }}
            // bounding-box: события ловятся по ВСЕЙ области группы, включая
            // прозрачную заливку rect (fill="none" при отключённом состоянии).
            // Без этого ПКМ по отключённому выключателю «проваливается» в svg.
            pointerEvents="bounding-box"
            onClick={() => setNotice('Ввод: см. плитки телеметрии')}
            onContextMenu={(e) => { e.preventDefault(); if (canTu) { setTuObject('Ввод'); setSelected(true); setTuOpen(true) } }}
          >
            {/* корпус выключателя на кабеле Т -> секция */}
            <rect
              x={368} y={122} width={24} height={24}
              fill={breakerVisual(breaker, emergency).fill}
              stroke={selected ? '#4d9fff' : '#333'} strokeWidth={2.5}
              className={breakerVisual(breaker, emergency).blink ? 'mnemo-blink' : ''}
            />
            {/* буква «Р» при местном управлении */}
            {breaker === 2 && (
              <text x={374} y={140} fontSize={13} fontWeight="bold" fill="#fff">Р</text>
            )}
            <text x={404} y={140} fontSize={12} fill="#555">Ввод QF1</text>
          </g>

          {/* ---------- 4 фидера (ниже секции) ---------- */}
          {[0, 1, 2, 3].map((i) => {
            const x = 170 + i * 140
            // Фидеры «питаются» от секции: индикация по общему состоянию ввода
            const feederBreaker = energized ? 1 : 0
            const vis = breakerVisual(feederBreaker, false)   // авария — только на вводе
            return (
              <g key={i}>
                {/* выключатель фидера на отводе от секции */}
                <rect
                  x={x - 12} y={158} width={24} height={24}
                  fill={vis.fill} stroke="#333" strokeWidth={2}
                  className={vis.blink ? 'mnemo-blink' : ''}
                />
                <text x={x - 18} y={198} fontSize={12} fill="#555">Ф-{i + 1}</text>
                {/* кабель фидера вниз */}
                <line
                  x1={x} y1={182} x2={x} y2={230}
                  stroke={cableVisual(energized, emergency).stroke}
                  strokeWidth={2.5}
                  strokeDasharray={cableVisual(energized, emergency).dash}
                  className={cableVisual(energized, emergency).blink ? 'mnemo-blink' : ''}
                />
                {/* ток нагрузки внизу */}
                <text x={x - 14} y={250} fontSize={12} fill="#777">
                  {energized ? `${Math.max(5, Math.round((live?.I?.value ?? 0) / 4 - i * 2))} А` : '0 А'}
                </text>
              </g>
            )
          })}

          {/* ---------- ⚠️ при неопределённых значениях (ТЗ 3.3.1) ---------- */}
          {live && Object.entries(live).some(([, p]) => p.quality !== 'good') && (
            <text x={700} y={30} fontSize={22}>⚠️</text>
          )}
          {emergency && (
            <text x={640} y={30} fontSize={14} fill="#ff4444" className="mnemo-blink">
              АВАРИЙНЫЙ РЕЖИМ
            </text>
          )}
        </svg>
      </div>

      {notice && <div className="ok-msg">{notice}</div>}

      {/* Диалог ТУ (двухшаговый алгоритм) */}
      {tuOpen && (
        <TuDialog
          objectName={tuObject}
          onClose={() => { setTuOpen(false); setSelected(false) }}
        />
      )}
    </>
  )
}
