// Мнемосхема ПС 110/10 кВ — главный экран АРМ (по ГОСТ Р 70451-2022 §7.2.4:
// принципы избирательности и иерархичности «от общего к частному»).
//
// ГЛАВНЫЙ ЭКРАН = сразу ПОЛНАЯ однолинейная схема ПС (два плеча, шины,
// секционный QF, 8 фидеров). Клик по любому узлу схемы -> детальная карточка
// объекта в ПРАВОЙ ПАНЕЛИ (схема остаётся на месте — drill-down на месте).
// Слева — дерево объектов с подсветкой аварий прямо в дереве.
//
// Индикация по ТЗ 3.3.1 + палитра ЭЛТЕКО §6 (один цвет = одно значение):
//   зелёный=вкл, серый/пустой=откл, оранжевый=предупреждение,
//   красный(мигает)=авария, синий=выделение выбранного.

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, type FactoryTree, type LivePoint } from '../api'
import { useAuth } from '../authStore'
import TuDialog from './TuDialog'
import { FaultContextMenu } from './FaultContextMenu'

// ---------- Палитра ЭЛТЕКО §6: один цвет = одно значение ----------
const COLOR = {
  ok: '#35c46a',        // зелёный — работа, включено, готовность
  off: '#4a4f58',       // серый — остановлено, отключено
  alarm: '#ff4444',     // красный — авария (мигает до квитирования)
  warn: '#ff9f2e',      // оранжевый — предупреждение, предавария
  info: '#4d9fff',      // синий — выбранный режим, активный маршрут
  energized: '#555b66', // кабель под напряжением (тёмно-серый сплошной)
  dead: '#7a8090',      // кабель обесточен (пунктир)
} as const

const blinkStyle = `
@keyframes mnemo-blink { 50% { opacity: 0.25; } }
.mnemo-blink { animation: mnemo-blink 0.8s step-start infinite; }
`

/** Состояние аппарата по BREAKER (0/1/2) + авария. */
function breakerVisual(value: number, emergency: boolean) {
  if (emergency) return { fill: COLOR.alarm, blink: true }
  switch (value) {
    case 1: return { fill: COLOR.ok, blink: false }
    case 0: return { fill: 'none', blink: false }
    case 2: return { fill: COLOR.warn, blink: false }
    default: return { fill: COLOR.warn, blink: true }
  }
}

/** Цвет кабельной линии. */
function cableVisual(energized: boolean, emergency: boolean) {
  if (emergency) return { stroke: COLOR.alarm, dash: '', blink: true }
  if (energized) return { stroke: COLOR.energized, dash: '', blink: false }
  return { stroke: COLOR.dead, dash: '6,4', blink: false }
}

/** Хелперы live-кэша (ключи 'arm1_incomer:U'). */
function pointOf(live: Record<string, LivePoint> | undefined, objId: string, code: string): LivePoint | undefined {
  return live?.[`${objId}:${code}`]
}
function val(live: Record<string, LivePoint> | undefined, objId: string, code: string): number {
  return pointOf(live, objId, code)?.value ?? 0
}
/** Признак аварии на объекте (suspect/bad по ключевым точкам). */
function qualBad(live: Record<string, LivePoint> | undefined, objId: string): boolean {
  return ['U', 'I', 'P'].some((code) => {
    const pnt = live?.[`${objId}:${code}`]
    return pnt ? pnt.quality !== 'good' : false
  })
}

/** Аварии человеческим языком + подсказка «что делать» (статья ЭЛТЕКО §8). */
const FAULT_LABELS: Record<number, { text: string; hint: string }> = {
  1: { text: 'КЗ — сработала МТЗ', hint: 'Найдите повреждение линии, включайте ввод только после проверки' },
  2: { text: 'Перегрузка по току', hint: 'Отключите часть потребителей или снизьте нагрузку' },
  3: { text: 'Перенапряжение', hint: 'Проверьте РПН трансформатора и защиту от перенапряжений' },
  4: { text: 'Обрыв кабеля', hint: 'Осмотрите линию, переключите нагрузку на резерв' },
  5: { text: 'Потеря ввода 110 кВ', hint: 'Перебросьте питание: включите секционный QF (ПКМ на схеме)' },
}

// ============================================================
// ДЕРЕВО ОБЪЕКТОВ (слева): аварийные объекты подсвечены прямо в дереве
// ============================================================
function ObjectTree({ tree, live, selectedId, onSelect }: {
  tree: FactoryTree
  live: Record<string, LivePoint> | undefined
  selectedId: string | null
  onSelect: (obj: FactoryTree) => void
}) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  /** Ряд дерева: значок состояния + имя. */
  function Row({ node, depth }: { node: FactoryTree; depth: number }) {
    const isGroup = node.children.length > 0
    const broken = qualBad(live, node.id)
    const hasFaultCode = !isGroup && val(live, node.id, 'FAULT') !== 0
    const bad = broken || hasFaultCode
    const selected = selectedId === node.id

    // Состояние-значок: для групп агрегируем снизу вверх
    let dot: string = COLOR.ok
    let dotTitle = 'норма'
    if (isGroup) {
      const subtreeBad = node.children.some((c) =>
        c.children.length ? c.children.some((cc) => qualBad(live, cc.id)) : qualBad(live, c.id))
      if (subtreeBad) { dot = COLOR.alarm; dotTitle = 'есть нарушения внутри' }
    } else {
      const b = val(live, node.id, 'BREAKER')
      if (hasFaultCode || broken) { dot = COLOR.alarm; dotTitle = 'авария/отклонение' }
      else if (b === 0) { dot = '#b9bec7'; dotTitle = 'отключён' }
    }

    return (
      <div style={{ paddingLeft: depth * 14 }}>
        <div
          onClick={() => onSelect(node)}
          style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '3px 6px', borderRadius: 6, cursor: 'pointer',
            background: selected ? 'rgba(77,159,255,.15)' : 'transparent',
            color: bad ? COLOR.alarm : 'inherit',
            fontWeight: bad ? 700 : 400,
          }}
          title={node.name + (bad ? ' — есть нарушение' : '')}
        >
          {isGroup && (
            <span
              onClick={(e) => { e.stopPropagation(); setCollapsed((m) => ({ ...m, [node.id]: !m[node.id] })) }}
              style={{ cursor: 'pointer', width: 12, display: 'inline-block', color: '#889' }}
            >
              {collapsed[node.id] ? '▸' : '▾'}
            </span>
          )}
          <span
            style={{
              width: 10, height: 10, borderRadius: '50%', background: dot,
              flexShrink: 0, border: '1px solid rgba(0,0,0,.2)',
              animation: bad ? 'mnemo-blink 0.8s step-start infinite' : undefined,
            }}
            title={dotTitle}
          />
          <span style={{ fontSize: 13 }}>{node.name}</span>
        </div>
        {!collapsed[node.id] && node.children.map((c) => <Row key={c.id} node={c} depth={depth + 1} />)}
      </div>
    )
  }

  return (
    <div className="card" style={{ padding: 10, maxHeight: 560, overflowY: 'auto' }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: '#556', marginBottom: 6 }}>ОБЪЕКТЫ ПС</div>
      <Row node={tree} depth={0} />
    </div>
  )
}

// ============================================================
// ДЕТАЛЬНАЯ КАРТОЧКА ОБЪЕКТА (правая панель при клике на узел схемы)
// ============================================================
function ObjectCard({ obj, live, canTu, onTu, onClose }: {
  obj: FactoryTree
  live: Record<string, LivePoint> | undefined
  canTu: boolean
  onTu: (obj: FactoryTree) => void
  onClose: () => void
}) {
  const breaker = val(live, obj.id, 'BREAKER')
  const faulty = qualBad(live, obj.id)
  const faultCode = val(live, obj.id, 'FAULT')
  const rows: Array<[string, string, string]> = [
    ['U', 'Напряжение', 'В'],
    ['I', 'Ток', 'А'],
    ['P', 'Активная мощность', 'кВт'],
    ['Q', 'Реактивная мощность', 'кВАр'],
    ['THDu', 'Несинусоидальность U', '%'],
    ['THDi', 'Несинусоидальность I', '%'],
    ['PF', 'Коэффициент мощности', 'о.е.'],
  ]
  const tuTarget = obj.kind === 'incomer10' || obj.kind === 'sectional' || obj.kind === 'incomer'

  return (
    <div className="card" style={{ padding: 14, position: 'relative' }}>
      <button
        onClick={onClose}
        style={{ position: 'absolute', top: 8, right: 10, background: 'none', border: 'none', fontSize: 18, cursor: 'pointer', color: '#889' }}
        title="Закрыть карточку"
      >✕</button>

      <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 2 }}>{obj.name}</div>
      {obj.voltage && <div style={{ fontSize: 12, color: '#777', marginBottom: 8 }}>{obj.voltage}</div>}

      {/* Состояние + авария с подсказкой */}
      {faulty || faultCode !== 0 ? (
        <div style={{ background: '#fff2f0', borderLeft: '4px solid ' + COLOR.alarm, padding: '8px 10px', marginBottom: 10, borderRadius: 6 }}>
          <div style={{ fontWeight: 700, color: COLOR.alarm }}>
            ⚠️ {FAULT_LABELS[faultCode]?.text ?? 'Отклонение параметров'}
          </div>
          {FAULT_LABELS[faultCode] && (
            <div style={{ fontSize: 12, color: '#666', marginTop: 4 }}>
              Что делать: {FAULT_LABELS[faultCode].hint}
            </div>
          )}
        </div>
      ) : (
        <div style={{ background: '#f0faf3', borderLeft: '4px solid ' + COLOR.ok, padding: '8px 10px', marginBottom: 10, borderRadius: 6 }}>
          <b style={{ color: COLOR.ok }}>
            {breaker === 1 ? 'Включён, работа в норме' : breaker === 0 ? 'Отключён' : 'Местное управление'}
          </b>
        </div>
      )}

      {/* ТУ (только управляемые аппараты) */}
      {canTu && tuTarget && (
        <button className="btn" style={{ width: '100%', marginBottom: 10 }}
          onClick={() => onTu(obj)}>
          Телеуправление: включить / отключить
        </button>
      )}

      {/* Плитки показателей */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        {rows.map(([code, name, unit]) => {
          const pnt = pointOf(live, obj.id, code)
          const isSuspect = pnt ? pnt.quality !== 'good' : false
          return (
            <div key={code} style={{
              padding: '6px 10px', borderRadius: 6, background: '#f6f7f9',
              borderLeft: `3px solid ${isSuspect ? COLOR.warn : COLOR.ok}`,
            }}>
              <div style={{ fontSize: 11, color: '#777' }}>{name} {isSuspect && '⚠️'}</div>
              <div style={{ fontSize: 16, fontWeight: 700, color: isSuspect ? '#e67e00' : '#222' }}>
                {pnt ? pnt.value.toFixed(code === 'PF' || code.startsWith('THD') ? 1 : 0) : '—'}
                <span style={{ fontSize: 11, fontWeight: 400, color: '#888' }}> {unit}</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ============================================================
// ГЛАВНАЯ СХЕМА ПС 110/10 (вся сразу, клик по узлу = карточка)
// ============================================================
function PsScheme({ tree, live, canTu, onTu, selectedId, onSelect, onObjContextMenu }: {
  tree: FactoryTree
  live: Record<string, LivePoint> | undefined
  canTu: boolean
  onTu: (obj: FactoryTree) => void
  selectedId: string | null
  onSelect: (obj: FactoryTree, clickX?: number) => void
  onObjContextMenu: (e: React.MouseEvent, obj: FactoryTree) => void
}) {
  const arm1 = tree.children.find((c) => c.id === 'arm1')!
  const arm2 = tree.children.find((c) => c.id === 'arm2')!
  const sec1 = tree.children.find((c) => c.id === 'sec1')!
  const sec2 = tree.children.find((c) => c.id === 'sec2')!
  const secQf = tree.children.find((c) => c.id === 'sec_qf')!

  const b1 = val(live, 'arm1_incomer', 'BREAKER')
  const b2 = val(live, 'arm2_incomer', 'BREAKER')
  const sectional = val(live, 'sec_qf', 'BREAKER')
  const fault1 = val(live, 'arm1_incomer', 'FAULT')
  const fault2 = val(live, 'arm2_incomer', 'FAULT')

  const u1 = val(live, 'arm1_incomer', 'U')
  const i1 = val(live, 'arm1_incomer', 'I')
  const p1 = val(live, 'arm1_incomer', 'P')
  const u2 = val(live, 'arm2_incomer', 'U')
  const i2 = val(live, 'arm2_incomer', 'I')
  const p2 = val(live, 'arm2_incomer', 'P')

  const sec1Fed = b1 === 1 || (sectional === 1 && b2 === 1)
  const sec2Fed = b2 === 1 || (sectional === 1 && b1 === 1)
  const anyFault = fault1 !== 0 || fault2 !== 0

  /** Кликабельный аппарат на схеме: передаём координату клика — карточка
   *  откроется с противоположной стороны, не перекрывая место клика. */
  function Apparatus({ x, y, obj, breaker, emergency }: {
    x: number; y: number; obj: FactoryTree; breaker: number; emergency: boolean
  }) {
    const vis = breakerVisual(breaker, emergency)
    return (
      <g
        style={{ cursor: 'pointer' }}
        pointerEvents="bounding-box"
        onClick={(e) => onSelect(obj, e.clientX)}
        onContextMenu={(e) => { e.preventDefault(); if (canTu) onTu(obj); onObjContextMenu(e, obj) }}
      >
        <rect
          x={x - 12} y={y - 12} width={24} height={24}
          fill={vis.fill}
          stroke={selectedId === obj.id ? COLOR.info : '#333'} strokeWidth={selectedId === obj.id ? 3.5 : 2.5}
          className={vis.blink ? 'mnemo-blink' : ''}
        />
        {breaker === 2 && <text x={x - 6} y={y + 5} fontSize={13} fontWeight="bold" fill="#fff">Р</text>}
      </g>
    )
  }

  return (
    <div className="card" style={{ background: '#e8eaed' }}>
      <svg viewBox="0 0 900 460" style={{ width: '100%', display: 'block' }}>
        {/* --- Заголовки вводов 110 кВ --- */}
        <text x={150} y={30} fontSize={14} fontWeight="bold" fill="#333" textAnchor="middle">Ввод 110 кВ №1</text>
        <text x={750} y={30} fontSize={14} fontWeight="bold" fill="#333" textAnchor="middle">Ввод 110 кВ №2</text>

        <line x1={150} y1={40} x2={150} y2={70} stroke="#333" strokeWidth={2.5} />
        <line x1={750} y1={40} x2={750} y2={70} stroke="#333" strokeWidth={2.5} />

        {/* --- Q-110 --- */}
        <Apparatus x={150} y={85} obj={find(arm1, 'arm1_q110')} breaker={b1} emergency={fault1 !== 0} />
        <Apparatus x={750} y={85} obj={find(arm2, 'arm2_q110')} breaker={b2} emergency={fault2 !== 0} />
        <text x={175} y={90} fontSize={12} fill="#555">Q-110</text>
        <text x={775} y={90} fontSize={12} fill="#555">Q-110</text>

        <line x1={150} y1={97} x2={150} y2={130} stroke="#333" strokeWidth={2.5} />
        <line x1={750} y1={97} x2={750} y2={130} stroke="#333" strokeWidth={2.5} />

        {/* --- Трансформаторы --- */}
        <g style={{ cursor: 'pointer' }} onClick={(e) => onSelect(find(arm1, 'arm1_tr'), e.clientX)}>
          <circle cx={150} cy={148} r={16} fill="none" stroke="#333" strokeWidth={2.5} />
          <circle cx={150} cy={168} r={16} fill="none" stroke="#333" strokeWidth={2.5} />
          <text x={178} y={162} fontSize={13} fontWeight="bold" fill="#333">T1</text>
        </g>
        <g style={{ cursor: 'pointer' }} onClick={(e) => onSelect(find(arm2, 'arm2_tr'), e.clientX)}>
          <circle cx={750} cy={148} r={16} fill="none" stroke="#333" strokeWidth={2.5} />
          <circle cx={750} cy={168} r={16} fill="none" stroke="#333" strokeWidth={2.5} />
          <text x={778} y={162} fontSize={13} fontWeight="bold" fill="#333">T2</text>
        </g>

        <line x1={150} y1={184} x2={150} y2={225} stroke={cableVisual(b1 === 1, fault1 !== 0).stroke}
          strokeWidth={3} strokeDasharray={cableVisual(b1 === 1, fault1 !== 0).dash}
          className={cableVisual(b1 === 1, fault1 !== 0).blink ? 'mnemo-blink' : ''} />
        <line x1={750} y1={184} x2={750} y2={225} stroke={cableVisual(b2 === 1, fault2 !== 0).stroke}
          strokeWidth={3} strokeDasharray={cableVisual(b2 === 1, fault2 !== 0).dash}
          className={cableVisual(b2 === 1, fault2 !== 0).blink ? 'mnemo-blink' : ''} />

        {/* --- Вводные 10 кВ с показаниями --- */}
        <Apparatus x={150} y={240} obj={find(arm1, 'arm1_incomer')} breaker={b1} emergency={fault1 !== 0} />
        <Apparatus x={750} y={240} obj={find(arm2, 'arm2_incomer')} breaker={b2} emergency={fault2 !== 0} />
        <text x={175} y={238} fontSize={12} fill="#555">Ввод 10 кВ</text>
        <text x={775} y={238} fontSize={12} fill="#555">Ввод 10 кВ</text>
        <text x={175} y={254} fontSize={11} fill="#777">U={Math.round(u1)} · I={Math.round(i1)} · P={Math.round(p1)} кВт</text>
        <text x={775} y={254} fontSize={11} fill="#777">U={Math.round(u2)} · I={Math.round(i2)} · P={Math.round(p2)} кВт</text>

        <line x1={150} y1={252} x2={150} y2={300} stroke={cableVisual(b1 === 1, fault1 !== 0).stroke} strokeWidth={3} strokeDasharray={cableVisual(b1 === 1, fault1 !== 0).dash} />
        <line x1={750} y1={252} x2={750} y2={300} stroke={cableVisual(b2 === 1, fault2 !== 0).stroke} strokeWidth={3} strokeDasharray={cableVisual(b2 === 1, fault2 !== 0).dash} />

        {/* --- Шины 10 кВ --- */}
        <line x1={60} y1={300} x2={380} y2={300} stroke={sec1Fed ? '#2a6fd4' : '#888'} strokeWidth={7} />
        <line x1={520} y1={300} x2={840} y2={300} stroke={sec2Fed ? '#2a6fd4' : '#888'} strokeWidth={7} />
        <text x={70} y={325} fontSize={13} fontWeight="bold" fill="#333">Секция I</text>
        <text x={530} y={325} fontSize={13} fontWeight="bold" fill="#333">Секция II</text>
        <text x={230} y={325} fontSize={11} fill={sec1Fed ? '#2a6fd4' : '#c44'}>
          {sec1Fed ? (b1 === 1 ? 'от ввода №1' : 'через СВ') : 'ОБЕСТОЧЕНА'}
        </text>
        <text x={680} y={325} fontSize={11} fill={sec2Fed ? '#2a6fd4' : '#c44'}>
          {sec2Fed ? (b2 === 1 ? 'от ввода №2' : 'через СВ') : 'ОБЕСТОЧЕНА'}
        </text>

        {/* --- Секционный QF --- */}
        <line x1={380} y1={300} x2={430} y2={300} stroke="#333" strokeWidth={3} />
        <g
          style={{ cursor: 'pointer' }}
          pointerEvents="bounding-box"
          onClick={(e) => onSelect(secQf, e.clientX)}
          onContextMenu={(e) => { e.preventDefault(); if (canTu) onTu(secQf); onObjContextMenu(e, secQf) }}
        >
          <rect
            x={430} y={288} width={40} height={24}
            fill={breakerVisual(sectional, false).fill}
            stroke={selectedId === secQf.id ? COLOR.info : '#333'} strokeWidth={selectedId === secQf.id ? 3.5 : 2.5}
            className={breakerVisual(sectional, false).blink ? 'mnemo-blink' : ''}
          />
          <line x1={433} y1={310} x2={467} y2={290} stroke="#333" strokeWidth={2} />
        </g>
        <line x1={470} y1={300} x2={520} y2={300} stroke="#333" strokeWidth={3} />
        <text x={450} y={275} fontSize={12} textAnchor="middle" fill="#333">Секционный</text>
        <text x={450} y={332} fontSize={11} textAnchor="middle" fill={sectional === 1 ? '#2a6fd4' : '#555'}>
          {sectional === 1 ? 'ВКЛЮЧЕН' : 'Отключен'}
        </text>

        {/* --- Фидеры секции I --- */}
        {sec1.children.map((f, idx) => {
          const x = 90 + idx * 75
          const fBad = qualBad(live, f.id)
          const vis = breakerVisual(sec1Fed ? 1 : 0, fBad)
          const cable = cableVisual(sec1Fed, fBad)
          return (
            <g key={f.id} style={{ cursor: 'pointer' }} pointerEvents="bounding-box"
              onClick={(e) => onSelect(f, e.clientX)} onContextMenu={(e) => onObjContextMenu(e, f)}>
              <rect x={x - 10} y={340} width={20} height={20} fill={vis.fill} stroke={selectedId === f.id ? COLOR.info : '#333'} strokeWidth={2}
                className={vis.blink ? 'mnemo-blink' : ''} />
              <line x1={x} y1={300} x2={x} y2={340} stroke={cable.stroke} strokeWidth={2.5}
                strokeDasharray={cable.dash} className={cable.blink ? 'mnemo-blink' : ''} />
              <line x1={x} y1={360} x2={x} y2={395} stroke={cable.stroke} strokeWidth={2.5}
                strokeDasharray={cable.dash} className={cable.blink ? 'mnemo-blink' : ''} />
              <path d={`M ${x - 6} ${390} L ${x + 6} ${390} L ${x} ${398} Z`} fill={cable.stroke} />
              <text x={x} y={415} fontSize={11} textAnchor="middle"
                fill={fBad ? COLOR.alarm : '#555'} fontWeight={fBad ? 700 : 400}>Ф-{idx + 1}</text>
              <text x={x} y={430} fontSize={10} textAnchor="middle" fill="#99a">
                {val(live, f.id, 'I') ? `${Math.round(val(live, f.id, 'I'))} А` : ''}
              </text>
            </g>
          )
        })}

        {/* --- Фидеры секции II --- */}
        {sec2.children.map((f, idx) => {
          const x = 550 + idx * 75
          const fBad = qualBad(live, f.id)
          const vis = breakerVisual(sec2Fed ? 1 : 0, fBad)
          const cable = cableVisual(sec2Fed, fBad)
          return (
            <g key={f.id} style={{ cursor: 'pointer' }} pointerEvents="bounding-box"
              onClick={(e) => onSelect(f, e.clientX)} onContextMenu={(e) => onObjContextMenu(e, f)}>
              <rect x={x - 10} y={340} width={20} height={20} fill={vis.fill} stroke={selectedId === f.id ? COLOR.info : '#333'} strokeWidth={2}
                className={vis.blink ? 'mnemo-blink' : ''} />
              <line x1={x} y1={300} x2={x} y2={340} stroke={cable.stroke} strokeWidth={2.5}
                strokeDasharray={cable.dash} className={cable.blink ? 'mnemo-blink' : ''} />
              <line x1={x} y1={360} x2={x} y2={395} stroke={cable.stroke} strokeWidth={2.5}
                strokeDasharray={cable.dash} className={cable.blink ? 'mnemo-blink' : ''} />
              <path d={`M ${x - 6} ${390} L ${x + 6} ${390} L ${x} ${398} Z`} fill={cable.stroke} />
              <text x={x} y={415} fontSize={11} textAnchor="middle"
                fill={fBad ? COLOR.alarm : '#555'} fontWeight={fBad ? 700 : 400}>Ф-{idx + 5}</text>
              <text x={x} y={430} fontSize={10} textAnchor="middle" fill="#99a">
                {val(live, f.id, 'I') ? `${Math.round(val(live, f.id, 'I'))} А` : ''}
              </text>
            </g>
          )
        })}

        {/* --- Баннер аварии с подсказкой --- */}
        {anyFault && FAULT_LABELS[fault1 || fault2] && (
          <>
            <text x={450} y={20} fontSize={14} fill={COLOR.alarm} textAnchor="middle" className="mnemo-blink">
              ⚡ {FAULT_LABELS[fault1 || fault2].text}
              {fault1 !== 0 ? ' (плечо А)' : ''}{fault2 !== 0 ? ' (плечо Б)' : ''}
            </text>
            <text x={450} y={38} fontSize={12} fill={COLOR.warn} textAnchor="middle">
              Что делать: {FAULT_LABELS[fault1 || fault2].hint}
            </text>
          </>
        )}
      </svg>

      {/* Легенда */}
      <div style={{ display: 'flex', gap: 16, padding: '8px 14px', flexWrap: 'wrap', fontSize: 12, color: '#555' }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 14, height: 14, background: COLOR.ok, border: '1px solid #999', borderRadius: 3 }} /> включён
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 14, height: 14, background: '#fff', border: '1px solid #999', borderRadius: 3 }} /> отключён
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 14, height: 14, background: COLOR.warn, border: '1px solid #999', borderRadius: 3 }} /> неопределённое
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 14, height: 14, background: COLOR.alarm, border: '1px solid #999', borderRadius: 3 }} /> авария
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 26, borderTop: `3px solid ${COLOR.energized}`, display: 'inline-block' }} /> под напряжением
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 26, borderTop: '3px dashed #4a4f58', display: 'inline-block' }} /> обесточено
        </span>
      </div>
    </div>
  )
}

/** Поиск узла по id внутри группы. */
function find(group: FactoryTree, id: string): FactoryTree {
  return group.children.find((c) => c.id === id)!
}

// ============================================================
// ГЛАВНЫЙ КОМПОНЕНТ: СХЕМА НА ВЕСЬ ЭКРАН + выезжающие панели
// ============================================================
// Компоновка (пожелание заказчика: «мнемосхема должна быть на весь экран,
// это самое главное»):
//   - схема занимает ВСЮ ширину окна контента;
//   - дерево объектов — выезжающая панель СЛЕВА (кнопка «Объекты»),
//     поверх схемы, не сжимает её;
//   - карточка объекта — выезжающая панель СПРАВА при клике на узел,
//     поверх схемы (как в настоящих SCADA).
export default function MnemoPage() {
  const role = useAuth((s) => s.user?.role)
  const canTu = role === 'dispatcher' || role === 'admin'
  const isAdmin = role === 'admin'

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

  // Выбранный объект (для правой панели). Схема ВСЕГДА на месте.
  const [selected, setSelected] = useState<FactoryTree | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [treeOpen, setTreeOpen] = useState(false)      // панель «Объекты»
  const [cardSide, setCardSide] = useState<'left' | 'right'>('right') // сторона карточки
  const [tuOpen, setTuOpen] = useState(false)
  const [tuObject, setTuObject] = useState('Секционный QF')
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; obj: FactoryTree } | null>(null)

  function onSelect(obj: FactoryTree, clickX?: number) {
    setSelected(obj)      // карточка открывается поверх схемы
    setSelectedId(obj.id)
    // УМНОЕ ПОЗИЦИОНИРОВАНИЕ карточки (чтобы не перекрывала место клика):
    // клик по ЛЕВОЙ половине схемы -> карточка справа,
    // клик по ПРАВОЙ половине -> карточка слева (зеркально).
    if (clickX !== undefined && window.innerWidth > 0) {
      setCardSide(clickX > window.innerWidth / 2 ? 'left' : 'right')
    }
    // клик из дерева (без координат) — карточка остаётся справа
  }

  function closeCard() {
    setSelected(null)
    setSelectedId(null)
  }

  function onObjectContextMenu(e: React.MouseEvent, obj: FactoryTree) {
    if (!isAdmin) return
    e.preventDefault()
    setCtxMenu({ x: e.clientX, y: e.clientY, obj })
  }

  if (!tree) return <div className="muted">Загрузка дерева объектов…</div>

  return (
    <>
      <style>{blinkStyle}</style>

      {/* --- Шапка: заголовок + кнопка дерева --- */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
        <button
          className="btn"
          onClick={() => setTreeOpen((v) => !v)}
          style={{ padding: '4px 12px', whiteSpace: 'nowrap' }}
          title="Показать/скрыть дерево объектов"
        >
          ☰ Объекты
        </button>
        <h2 style={{ margin: 0 }}>ПС 110/10 кВ</h2>
        <span className="muted" style={{ fontSize: 13 }}>
          Клик по узлу — карточка объекта.{canTu ? ' ПКМ по аппарату — ТУ.' : ''}
          {isAdmin ? ' ПКМ — создать аварию (симуляция).' : ''}
        </span>
      </div>

      {/* --- Контейнер схемы (позиция relative — панели ложатся поверх) --- */}
      <div style={{ position: 'relative' }}>

        {/* === СХЕМА: вся ширина === */}
        <PsScheme
          tree={tree}
          live={live}
          canTu={canTu}
          selectedId={selectedId}
          onSelect={onSelect}
          onTu={(obj) => { setTuObject(obj.name); setSelectedId(obj.id); setTuOpen(true) }}
          onObjContextMenu={onObjectContextMenu}
        />

        {/* === ДЕРЕВО ОБЪЕКТОВ: выезжает слева поверх схемы === */}
        {treeOpen && (
          <>
            {/* Полупрозрачная заслонка: клик вне панели закрывает её */}
            <div
              onClick={() => setTreeOpen(false)}
              style={{ position: 'fixed', inset: 0, zIndex: 40, background: 'rgba(0,0,0,0.25)' }}
            />
            <div style={{
              position: 'absolute', top: 0, left: 0, bottom: 0, width: 270,
              zIndex: 41, boxShadow: '8px 0 24px rgba(0,0,0,0.35)',
            }}>
              <ObjectTree
                tree={tree}
                live={live}
                selectedId={selectedId}
                onSelect={(obj) => { onSelect(obj); setTreeOpen(false) }}
              />
            </div>
          </>
        )}

        {/* === КАРТОЧКА ОБЪЕКТА: выезжает поверх схемы, сторона зависит
              от места клика (кликнул справа — карточка слева и наоборот) === */}
        {selected && (
          <div style={{
            position: 'absolute', top: 10,
            [cardSide]: 10, width: 330,
            zIndex: 42, maxHeight: 'calc(100% - 20px)', overflowY: 'auto',
            boxShadow: '-8px 0 24px rgba(0,0,0,0.35)',
          }}>
            <ObjectCard
              obj={selected}
              live={live}
              canTu={canTu}
              onTu={(obj) => { setTuObject(obj.name); setTuOpen(true) }}
              onClose={closeCard}
            />
          </div>
        )}
      </div>

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
          onClose={() => setTuOpen(false)}
        />
      )}
    </>
  )
}
