// Многоуровневая мнемосхема ПС 110/10 кВ (специф §16, MVP_SCADA_Denis).
//
// Три уровня навигации:
//   Уровень 0 «ПС 110/10»: плитки плеч/секций + СВ + тепловая карта аварий
//   Уровень 1 «Схема ПС»: полная однолинейная схема (как рисунок в docx):
//     два «плеча» сверху (ввод 110 -> Q110 -> трансформатор -> ввод 10 кВ),
//     две шины 10 кВ внизу, секционный QF в центре, 8 фидеров
//   Уровень 2 «Объект»: детальная схема фидера/ввода + плитки показателей
//
// Динамическая индикация по ТЗ 3.3.1 (кабели/аппараты) — см. хелперы ниже.

import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { api, type FactoryTree, type LivePoint } from '../api'
import { useAuth } from '../authStore'
import TuDialog from './TuDialog'
import { FaultContextMenu } from './FaultContextMenu'

// ---------- Хелперы индикации ----------

/** Состояние аппарата по значению BREAKER (0/1/2) + признак аварии. */
function breakerVisual(value: number, emergency: boolean) {
  if (emergency) return { fill: '#ff4444', label: 'авария', blink: true }
  switch (value) {
    case 1: return { fill: '#35c46a', label: 'вкл', blink: false }
    case 0: return { fill: 'none', label: 'откл', blink: false }
    case 2: return { fill: '#ff9f2e', label: 'Р', blink: false }
    default: return { fill: '#ff9f2e', label: '?', blink: true }
  }
}

/** Цвет кабельной линии: под напряжением / обесточена / авария. */
function cableVisual(energized: boolean, emergency: boolean) {
  if (emergency) return { stroke: '#ff4444', dash: '', blink: true }
  if (energized) return { stroke: '#555b66', dash: '', blink: false }
  return { stroke: '#4a4f58', dash: '6,4', blink: false }
}

const blinkStyle = `
@keyframes mnemo-blink { 50% { opacity: 0.25; } }
.mnemo-blink { animation: mnemo-blink 0.8s step-start infinite; }
`

type Level =
  | { kind: 'factory' }
  | { kind: 'ps' }
  | { kind: 'object'; obj: FactoryTree }

/** Хелперы доступа к live-кэшу (ключи 'arm1_incomer:U'). */
function pointOf(live: Record<string, LivePoint> | undefined, objId: string, code: string): LivePoint | undefined {
  return live?.[`${objId}:${code}`]
}
function val(live: Record<string, LivePoint> | undefined, objId: string, code: string): number {
  return pointOf(live, objId, code)?.value ?? 0
}
/** Есть ли у объекта «плохие» точки — признак аварии на нём. */
function qualBad(live: Record<string, LivePoint> | undefined, objId: string): boolean {
  return ['U', 'I', 'P'].some((code) => {
    const pnt = live?.[`${objId}:${code}`]
    return pnt ? pnt.quality !== 'good' : false
  })
}

const FAULT_LABELS: Record<number, string> = {
  1: 'КЗ, сработка МТЗ',
  2: 'Перегрузка',
  3: 'Перенапряжение',
  4: 'Обрыв кабеля',
  5: 'Потеря ввода 110 кВ',
}

/** Поиск дочернего узла по id внутри группы (локальный хелпер схемы). */
function find(group: FactoryTree, id: string): FactoryTree {
  return group.children.find((c) => c.id === id)!
}

/** Рекурсивный поиск узла по id (для deep-link из алармов). */
function findInTree(node: FactoryTree, id: string): FactoryTree | null {
  if (node.id === id) return node
  for (const c of node.children) {
    const found = findInTree(c, id)
    if (found) return found
  }
  return null
}

/** Короткое имя для крошек. */
function objShortName(obj: FactoryTree): string {
  return obj.name
}

/** Компактная тепловая карта аварий по фидерам (полоса над схемой). */
function HeatStrip({ tree, onOpenObject }: {
  tree: FactoryTree
  onOpenObject: (obj: FactoryTree) => void
}) {
  const { data: stats } = useQuery({
    queryKey: ['alarmStats'],
    queryFn: () => api.alarmStats(60),
    refetchInterval: 5000,
  })
  const alarmByLeaf: Record<string, number> = {}
  for (const o of stats?.objects ?? []) {
    const leaf = o.object.split(',').map((s) => s.trim()).pop() ?? o.object
    for (const sec of tree.children.filter((c) => c.feeders)) {
      const hit = sec.children.find((c) => c.name === leaf)
      if (hit) { alarmByLeaf[hit.id] = (alarmByLeaf[hit.id] ?? 0) + o.alarms }
    }
  }
  const max = Math.max(1, ...Object.values(alarmByLeaf))
  function heat(a: number): string {
    if (a === 0) return '#e8f5e9'
    const t = Math.min(a / max, 1)
    return `rgb(255,${Math.round(235 - t * 168)},${Math.round(59 - t * 5)})`
  }
  return (
    <div className="card" style={{ marginBottom: 12, padding: '8px 12px', display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
      <b style={{ fontSize: 13 }}>Аварии за час:</b>
      {tree.children.filter((c) => c.feeders).map((sec) => (
        <div key={sec.id} style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
          <span style={{ fontSize: 11, color: '#556', marginRight: 2 }}>{sec.name.replace(', 10 кВ', '')}:</span>
          {sec.children.map((f) => {
            const a = alarmByLeaf[f.id] ?? 0
            return (
              <span
                key={f.id}
                onClick={() => onOpenObject(f)}
                title={`${sec.name}, ${f.name}: ${a} алармов`}
                style={{
                  background: heat(a), border: '1px solid #bbb', borderRadius: 4,
                  padding: '2px 7px', fontSize: 11, cursor: 'pointer',
                  color: a > 0 ? '#7a0000' : '#345', fontWeight: a > 0 ? 700 : 400,
                }}
              >
                {f.name}{a > 0 ? ` ⚡${a}` : ''}
              </span>
            )
          })}
        </div>
      ))}
    </div>
  )
}

// УРОВЕНЬ 1: ПОЛНАЯ ОДНОЛИНЕЙНАЯ СХЕМА ПС (как рисунок из docx)
// ============================================================
function PsScheme({ tree, live, onOpenObject, canTu, onTu, selectedId, onObjContextMenu }: {
  tree: FactoryTree
  live: Record<string, LivePoint> | undefined
  onOpenObject: (obj: FactoryTree) => void
  canTu: boolean
  onTu: (obj: FactoryTree) => void
  selectedId: string | null
  onObjContextMenu: (e: React.MouseEvent, obj: FactoryTree) => void
}) {
  const arm1 = tree.children.find((c) => c.id === 'arm1')!
  const arm2 = tree.children.find((c) => c.id === 'arm2')!
  const sec1 = tree.children.find((c) => c.id === 'sec1')!
  const sec2 = tree.children.find((c) => c.id === 'sec2')!
  const secQf = tree.children.find((c) => c.id === 'sec_qf')!

  const b1 = val(live, 'arm1_incomer', 'BREAKER')      // ввод 10 кВ №1
  const b2 = val(live, 'arm2_incomer', 'BREAKER')      // ввод 10 кВ №2
  const sectional = val(live, 'sec_qf', 'BREAKER')
  const fault1 = val(live, 'arm1_incomer', 'FAULT')
  const fault2 = val(live, 'arm2_incomer', 'FAULT')

  // Запитанность секций (по связности): секция I запитана если её ввод вкл,
  // ИЛИ СВ вкл и соседнее плечо работает (переброс)
  const sec1Fed = b1 === 1 || (sectional === 1 && b2 === 1)
  const sec2Fed = b2 === 1 || (sectional === 1 && b1 === 1)

  /** Аппарат-квадрат с кликами и ПКМ. */
  function Apparatus({ x, y, obj, breaker, emergency }: {
    x: number; y: number; obj: FactoryTree; breaker: number; emergency: boolean
  }) {
    const vis = breakerVisual(breaker, emergency)
    return (
      <g
        style={{ cursor: 'pointer' }}
        pointerEvents="bounding-box"
        onClick={() => onOpenObject(obj)}
        onContextMenu={(e) => { e.preventDefault(); if (canTu) onTu(obj); onObjContextMenu(e, obj) }}
      >
        <rect
          x={x - 12} y={y - 12} width={24} height={24}
          fill={vis.fill}
          stroke={selectedId === obj.id ? '#4d9fff' : '#333'} strokeWidth={2.5}
          className={vis.blink ? 'mnemo-blink' : ''}
        />
        {breaker === 2 && <text x={x - 6} y={y + 5} fontSize={13} fontWeight="bold" fill="#fff">Р</text>}
      </g>
    )
  }

  return (
    <div>
      <HeatStrip tree={tree} onOpenObject={onOpenObject} />
      <div className="card" style={{ background: '#e8eaed' }}>
      <svg viewBox="0 0 900 460" style={{ width: '100%', display: 'block' }}>
        {/* --- Заголовки вводов 110 кВ --- */}
        <text x={150} y={30} fontSize={14} fontWeight="bold" fill="#333" textAnchor="middle">Ввод 110 кВ №1</text>
        <text x={750} y={30} fontSize={14} fontWeight="bold" fill="#333" textAnchor="middle">Ввод 110 кВ №2</text>

        {/* --- Кабели 110 кВ: линия -> Q-110 --- */}
        <line x1={150} y1={40} x2={150} y2={70} stroke="#333" strokeWidth={2.5} />
        <line x1={750} y1={40} x2={750} y2={70} stroke="#333" strokeWidth={2.5} />

        {/* --- Q-110 (состояние зеркалит ввод 10 кВ плеча) --- */}
        <Apparatus x={150} y={85} obj={find(arm1, 'arm1_q110')} breaker={b1} emergency={fault1 !== 0} />
        <Apparatus x={750} y={85} obj={find(arm2, 'arm2_q110')} breaker={b2} emergency={fault2 !== 0} />
        <text x={175} y={90} fontSize={12} fill="#555">Q-110</text>
        <text x={775} y={90} fontSize={12} fill="#555">Q-110</text>

        {/* --- Кабели Q-110 -> трансформаторы --- */}
        <line x1={150} y1={97} x2={150} y2={130} stroke="#333" strokeWidth={2.5} />
        <line x1={750} y1={97} x2={750} y2={130} stroke="#333" strokeWidth={2.5} />

        {/* --- Трансформаторы T1/T2 (два круга) --- */}
        <g onClick={() => onOpenObject(find(arm1, 'arm1_tr'))} style={{ cursor: 'pointer' }}>
          <circle cx={150} cy={148} r={16} fill="none" stroke="#333" strokeWidth={2.5} />
          <circle cx={150} cy={168} r={16} fill="none" stroke="#333" strokeWidth={2.5} />
          <text x={178} y={162} fontSize={13} fontWeight="bold" fill="#333">T1</text>
        </g>
        <g onClick={() => onOpenObject(find(arm2, 'arm2_tr'))} style={{ cursor: 'pointer' }}>
          <circle cx={750} cy={148} r={16} fill="none" stroke="#333" strokeWidth={2.5} />
          <circle cx={750} cy={168} r={16} fill="none" stroke="#333" strokeWidth={2.5} />
          <text x={778} y={162} fontSize={13} fontWeight="bold" fill="#333">T2</text>
        </g>

        {/* --- Кабели транс -> ввод 10 кВ --- */}
        <line x1={150} y1={184} x2={150} y2={225} stroke={cableVisual(b1 === 1, fault1 !== 0).stroke}
          strokeWidth={3} strokeDasharray={cableVisual(b1 === 1, fault1 !== 0).dash}
          className={cableVisual(b1 === 1, fault1 !== 0).blink ? 'mnemo-blink' : ''} />
        <line x1={750} y1={184} x2={750} y2={225} stroke={cableVisual(b2 === 1, fault2 !== 0).stroke}
          strokeWidth={3} strokeDasharray={cableVisual(b2 === 1, fault2 !== 0).dash}
          className={cableVisual(b2 === 1, fault2 !== 0).blink ? 'mnemo-blink' : ''} />

        {/* --- Вводные выключатели 10 кВ --- */}
        <Apparatus x={150} y={240} obj={find(arm1, 'arm1_incomer')} breaker={b1} emergency={fault1 !== 0} />
        <Apparatus x={750} y={240} obj={find(arm2, 'arm2_incomer')} breaker={b2} emergency={fault2 !== 0} />
        <text x={175} y={245} fontSize={12} fill="#555">Ввод 10 кВ</text>
        <text x={775} y={245} fontSize={12} fill="#555">Ввод 10 кВ</text>

        {/* Кабели вводов к шинам */}
        <line x1={150} y1={252} x2={150} y2={300} stroke={cableVisual(b1 === 1, fault1 !== 0).stroke} strokeWidth={3} strokeDasharray={cableVisual(b1 === 1, fault1 !== 0).dash} />
        <line x1={750} y1={252} x2={750} y2={300} stroke={cableVisual(b2 === 1, fault2 !== 0).stroke} strokeWidth={3} strokeDasharray={cableVisual(b2 === 1, fault2 !== 0).dash} />

        {/* --- Шины 10 кВ (две секции) --- */}
        <line x1={60} y1={300} x2={380} y2={300} stroke={sec1Fed ? '#2a6fd4' : '#888'} strokeWidth={7} />
        <line x1={520} y1={300} x2={840} y2={300} stroke={sec2Fed ? '#2a6fd4' : '#888'} strokeWidth={7} />
        <text x={70} y={325} fontSize={13} fontWeight="bold" fill="#333">Секция I, 10 кВ</text>
        <text x={530} y={325} fontSize={13} fontWeight="bold" fill="#333">Секция II, 10 кВ</text>
        <text x={230} y={325} fontSize={11} fill={sec1Fed ? '#2a6fd4' : '#c44'}>
          {sec1Fed ? (b1 === 1 ? 'от ввода №1' : 'запитана через СВ') : 'ОБЕСТОЧЕНА'}
        </text>
        <text x={680} y={325} fontSize={11} fill={sec2Fed ? '#2a6fd4' : '#c44'}>
          {sec2Fed ? (b2 === 1 ? 'от ввода №2' : 'запитана через СВ') : 'ОБЕСТОЧЕНА'}
        </text>

        {/* --- СЕКЦИОННЫЙ выключатель (между шинами) --- */}
        <line x1={380} y1={300} x2={430} y2={300} stroke="#333" strokeWidth={3} />
        <g
          style={{ cursor: 'pointer' }}
          pointerEvents="bounding-box"
          onClick={() => onOpenObject(secQf)}
          onContextMenu={(e) => { e.preventDefault(); if (canTu) onTu(secQf); onObjContextMenu(e, secQf) }}
        >
          <rect
            x={430} y={288} width={40} height={24}
            fill={breakerVisual(sectional, false).fill}
            stroke={selectedId === secQf.id ? '#4d9fff' : '#333'} strokeWidth={2.5}
            className={breakerVisual(sectional, false).blink ? 'mnemo-blink' : ''}
          />
          {/* диагональ — символ секционного */}
          <line x1={433} y1={310} x2={467} y2={290} stroke="#333" strokeWidth={2} />
        </g>
        <line x1={470} y1={300} x2={520} y2={300} stroke="#333" strokeWidth={3} />
        <text x={450} y={275} fontSize={12} textAnchor="middle" fill="#333">Секционный</text>
        <text x={450} y={332} fontSize={11} textAnchor="middle" fill={sectional === 1 ? '#2a6fd4' : '#555'}>
          {sectional === 1 ? 'ВКЛЮЧЕН' : 'Отключен'}
        </text>

        {/* --- Фидеры секции I (от шины вниз) --- */}
        {sec1.children.map((f, idx) => {
          const x = 90 + idx * 75
          const vis = breakerVisual(sec1Fed ? 1 : 0, qualBad(live, f.id))
          const cable = cableVisual(sec1Fed, qualBad(live, f.id))
          return (
            <g key={f.id} style={{ cursor: 'pointer' }} pointerEvents="bounding-box"
              onClick={() => onOpenObject(f)} onContextMenu={(e) => onObjContextMenu(e, f)}>
              <rect x={x - 10} y={340} width={20} height={20} fill={vis.fill} stroke="#333" strokeWidth={2}
                className={vis.blink ? 'mnemo-blink' : ''} />
              <line x1={x} y1={300} x2={x} y2={340} stroke={cable.stroke} strokeWidth={2.5}
                strokeDasharray={cable.dash} className={cable.blink ? 'mnemo-blink' : ''} />
              <line x1={x} y1={360} x2={x} y2={395} stroke={cable.stroke} strokeWidth={2.5}
                strokeDasharray={cable.dash} className={cable.blink ? 'mnemo-blink' : ''} />
              <path d={`M ${x - 6} ${390} L ${x + 6} ${390} L ${x} ${398} Z`} fill={cable.stroke} />
              <text x={x} y={415} fontSize={11} textAnchor="middle" fill="#555">Ф-{idx + 1}</text>
              <text x={x} y={430} fontSize={10} textAnchor="middle" fill="#99a">
                {val(live, f.id, 'I') ? `${Math.round(val(live, f.id, 'I'))} А` : ''}
              </text>
            </g>
          )
        })}

        {/* --- Фидеры секции II --- */}
        {sec2.children.map((f, idx) => {
          const x = 550 + idx * 75
          const vis = breakerVisual(sec2Fed ? 1 : 0, qualBad(live, f.id))
          const cable = cableVisual(sec2Fed, qualBad(live, f.id))
          return (
            <g key={f.id} style={{ cursor: 'pointer' }} pointerEvents="bounding-box"
              onClick={() => onOpenObject(f)} onContextMenu={(e) => onObjContextMenu(e, f)}>
              <rect x={x - 10} y={340} width={20} height={20} fill={vis.fill} stroke="#333" strokeWidth={2}
                className={vis.blink ? 'mnemo-blink' : ''} />
              <line x1={x} y1={300} x2={x} y2={340} stroke={cable.stroke} strokeWidth={2.5}
                strokeDasharray={cable.dash} className={cable.blink ? 'mnemo-blink' : ''} />
              <line x1={x} y1={360} x2={x} y2={395} stroke={cable.stroke} strokeWidth={2.5}
                strokeDasharray={cable.dash} className={cable.blink ? 'mnemo-blink' : ''} />
              <path d={`M ${x - 6} ${390} L ${x + 6} ${390} L ${x} ${398} Z`} fill={cable.stroke} />
              <text x={x} y={415} fontSize={11} textAnchor="middle" fill="#555">Ф-{idx + 5}</text>
              <text x={x} y={430} fontSize={10} textAnchor="middle" fill="#99a">
                {val(live, f.id, 'I') ? `${Math.round(val(live, f.id, 'I'))} А` : ''}
              </text>
            </g>
          )
        })}

        {/* --- Баннер аварии --- */}
        {(fault1 !== 0 || fault2 !== 0) && (
          <text x={450} y={20} fontSize={14} fill="#ff4444" textAnchor="middle" className="mnemo-blink">
            АВАРИЯ: {FAULT_LABELS[fault1 || fault2] ?? ''}
            {fault1 !== 0 ? ' (плечо А)' : ''}{fault2 !== 0 ? ' (плечо Б)' : ''}
          </text>
        )}
      </svg>
      </div>
    </div>
  )
}

// ============================================================
// УРОВЕНЬ 2: ОБЪЕКТ (детальная схема + плитки показателей)
// ============================================================
function ObjectLevel({ obj, live }: {
  obj: FactoryTree
  live: Record<string, LivePoint> | undefined
}) {
  const breaker = val(live, obj.id, 'BREAKER')
  const faulty = qualBad(live, obj.id)
  const energized = breaker === 1 && !faulty
  const rows: Array<[string, string, string]> = [
    ['U', 'Напряжение', 'В'],
    ['I', 'Ток', 'А'],
    ['P', 'Активная мощность', 'кВт'],
    ['Q', 'Реактивная мощность', 'кВАр'],
    ['THDu', 'Несинусоидальность U', '%'],
    ['THDi', 'Несинусоидальность I', '%'],
    ['PF', 'Коэффициент мощности', 'о.е.'],
  ]
  const objLabel = obj.voltage ? `${obj.name} (${obj.voltage})` : obj.name
  return (
    <div>
      <div className="card" style={{ background: '#e8eaed' }}>
        <svg viewBox="0 0 760 200" style={{ width: '100%', display: 'block' }}>
          <text x={20} y={24} fontSize={14} fill="#333">{objLabel}</text>
          <line
            x1={380} y1={40} x2={380} y2={90}
            stroke={cableVisual(energized, faulty).stroke}
            strokeWidth={3} strokeDasharray={cableVisual(energized, faulty).dash}
            className={cableVisual(energized, faulty).blink ? 'mnemo-blink' : ''}
          />
          <rect
            x={368} y={90} width={24} height={24}
            fill={breakerVisual(breaker, faulty).fill}
            stroke="#333" strokeWidth={2.5}
            className={breakerVisual(breaker, faulty).blink ? 'mnemo-blink' : ''}
          />
          <text x={404} y={108} fontSize={12} fill="#555">
            {obj.kind === 'incomer10' ? 'Ввод 10 кВ' : obj.kind === 'incomer110' ? 'Q-110 / ввод' : obj.kind === 'sectional' ? 'Секционный QF' : obj.kind === 'transformer' ? 'Т (нет ТУ)' : 'QF фидера'}
          </text>
          <line x1={380} y1={114} x2={380} y2={150} stroke={cableVisual(energized, faulty).stroke} strokeWidth={3} strokeDasharray={cableVisual(energized, faulty).dash} />
          <rect x={340} y={150} width={80} height={34} rx={4} fill="none" stroke="#555b66" strokeWidth={2} />
          <text x={352} y={172} fontSize={12} fill="#555">
            {obj.kind === 'feeder' ? 'нагрузка' : obj.kind === 'bus' ? 'шины' : obj.kind === 'transformer' ? 'Т 110/10' : 'сеть'}
          </text>
          {faulty && (
            <text x={520} y={60} fontSize={14} fill="#ff4444" className="mnemo-blink">
              ⚠️ АВАРИЯ / ОТКЛОНЕНИЕ
            </text>
          )}
        </svg>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 12, marginTop: 12 }}>
        {rows.map(([code, name, unit]) => {
          const pnt = pointOf(live, obj.id, code)
          const isSuspect = pnt ? pnt.quality !== 'good' : false
          return (
            <div key={code} className="card" style={{
              padding: '10px 14px',
              borderLeft: `4px solid ${isSuspect ? '#ff9f2e' : '#35c46a'}`,
              background: isSuspect ? '#fff7ec' : '#fff',
            }}>
              <div style={{ fontSize: 12, color: '#777' }}>
                {name} {isSuspect && '⚠️'}
              </div>
              <div style={{ fontSize: 20, fontWeight: 700, color: isSuspect ? '#e67e00' : '#222' }}>
                {pnt ? pnt.value.toFixed(code === 'PF' || code.startsWith('THD') ? 1 : 0) : '—'}{' '}
                <span style={{ fontSize: 13, fontWeight: 400 }}>{unit}</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ============================================================
// ГЛАВНЫЙ КОМПОНЕНТ: навигация по уровням
// ============================================================
export default function MnemoPage() {
  const role = useAuth((s) => s.user?.role)
  const canTu = role === 'dispatcher' || role === 'admin'
  const isAdmin = role === 'admin'
  const [searchParams] = useSearchParams()

  const { data: live } = useQuery({
    queryKey: ['live'],
    queryFn: api.live,
    refetchInterval: 1000,
  })

  const { data: tree } = useQuery({
    queryKey: ['objectsTree'],
    queryFn: api.objectsTree,
    staleTime: Infinity,
  })

  const [level, setLevel] = useState<Level>({ kind: 'ps' })   // СРАЗУ полная схема
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [tuOpen, setTuOpen] = useState(false)
  const [tuObject, setTuObject] = useState('Секционный QF')
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; obj: FactoryTree } | null>(null)

  // Переход из аларм-бара: /?obj=<id> — раскрываем уровень объекта
  useEffect(() => {
    const objId = searchParams.get('obj')
    if (!objId || !tree) return
    const found = findInTree(tree, objId)
    if (found) setLevel({ kind: 'object', obj: found })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, tree])

  function onObjectContextMenu(e: React.MouseEvent, obj: FactoryTree) {
    if (!isAdmin) return
    e.preventDefault()
    setCtxMenu({ x: e.clientX, y: e.clientY, obj })
  }

  const crumbs: Array<{ label: string; go: () => void }> = [
    { label: 'ПС 110/10', go: () => setLevel({ kind: 'ps' }) },
  ]
  if (level.kind === 'ps') {
    crumbs.push({ label: 'Схема ПС', go: () => {} })
  } else if (level.kind === 'object') {
    crumbs.push({ label: 'Схема ПС', go: () => setLevel({ kind: 'ps' }) })
    crumbs.push({ label: objShortName(level.obj), go: () => {} })
  }

  if (!tree) return <div className="muted">Загрузка дерева объектов…</div>

  return (
    <>
      <style>{blinkStyle}</style>
      <h2>Мнемосхема — ПС 110/10 кВ</h2>

      <div style={{ marginBottom: 10, display: 'flex', gap: 6, alignItems: 'center' }}>
        {crumbs.map((c, idx) => (
          <span key={idx} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            {idx > 0 && <span style={{ color: '#999' }}>→</span>}
            <button
              onClick={c.go}
              disabled={idx === crumbs.length - 1}
              style={{
                padding: '4px 10px', borderRadius: 6,
                cursor: idx === crumbs.length - 1 ? 'default' : 'pointer',
                background: idx === crumbs.length - 1 ? '#e8eaed' : '#fff',
                border: '1px solid #ccc',
                fontWeight: idx === crumbs.length - 1 ? 700 : 400,
              }}
            >
              {c.label}
            </button>
          </span>
        ))}
      </div>

      <div className="muted" style={{ marginBottom: 8 }}>
        {level.kind === 'ps' && 'Схема ПС 110/10 кВ. ЛКМ по объекту — детали; ' + (canTu ? 'ПКМ по выключателю — телеуправление.' : 'ТУ доступно роли dispatcher+.')}
        {level.kind === 'object' && `Детали объекта «${objShortName(level.obj)}». Кнопка «ПС 110/10» вверху — вернуться к схеме.`}
      </div>

      {level.kind === 'ps' && (
        <PsScheme
          tree={tree}
          live={live}
          canTu={canTu}
          selectedId={selectedId}
          onOpenObject={(obj) => setLevel({ kind: 'object', obj })}
          onTu={(obj) => { setTuObject(obj.name); setSelectedId(obj.id); setTuOpen(true) }}
          onObjContextMenu={onObjectContextMenu}
        />
      )}
      {level.kind === 'object' && (
        <ObjectLevel obj={level.obj} live={live} />
      )}

      {ctxMenu && (
        <FaultContextMenu
          x={ctxMenu.x}
          y={ctxMenu.y}
          objectId={ctxMenu.obj.id}
          objectName={ctxMenu.obj.name}
          onClose={() => setCtxMenu(null)}
        />
      )}

      {tuOpen && (
        <TuDialog
          objectName={tuObject}
          onClose={() => { setTuOpen(false); setSelectedId(null) }}
        />
      )}
    </>
  )
}
