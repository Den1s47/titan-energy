// Многоуровневая мнемосхема (специф §16) — главный экран АРМ диспетчера.
//
// Три уровня навигации (переключение кнопками-крошками, состояние в useState):
//   Уровень 0 «Завод»: обе ТП плитками (суммарные кВт, цвет = худшее состояние
//     внутри, мигание при аварии) — обзор всего предприятия;
//   Уровень 1 «ТП-х»: однолинейная схема ТП (ввод -> Т -> секция -> 4 фидера)
//     — значения per-object из live-кэша ('tp1_f2:I'), авария указывает на
//     КОНКРЕТНЫЙ фидер (мигает только он);
//   Уровень 2 «Объект»: детальная схема фидера/ввода + плитки показателей.
//
// Динамическая индикация по ТЗ 3.3.1:
//   кабели: под напряжением — сплошная тёмно-серая; обесточена — пунктирная
//     блёклая; аварийное — мигающий красный;
//   аппараты: вкл — зелёный квадрат; откл — пустой; местное — «Р»;
//     неопределённое — оранжевый; аварийное — мигающий красный.

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, type FactoryTree, type LivePoint } from '../api'
import { useAuth } from '../authStore'
import TuDialog from './TuDialog'

// ---------- Хелперы индикации (общие для всех уровней) ----------

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

/** Цвет кабельной линии: под напряжением / обесточена / авария. */
function cableVisual(energized: boolean, emergency: boolean) {
  if (emergency) return { stroke: '#ff4444', dash: '', blink: true }
  if (energized) return { stroke: '#555b66', dash: '', blink: false }   // сплошная
  return { stroke: '#4a4f58', dash: '6,4', blink: false }               // пунктир
}

// CSS-анимация мигания аварийных элементов
const blinkStyle = `
@keyframes mnemo-blink { 50% { opacity: 0.25; } }
.mnemo-blink { animation: mnemo-blink 0.8s step-start infinite; }
`

// ---------- Типы уровня ----------
type Level =
  | { kind: 'factory' }
  | { kind: 'tp'; tp: FactoryTree }
  | { kind: 'object'; tp: FactoryTree; obj: FactoryTree }

/** Хелперы доступа к live-кэшу multi-object (ключи 'tp1_f2:I'). */
function pointOf(live: Record<string, LivePoint> | undefined, objId: string, code: string): LivePoint | undefined {
  return live?.[`${objId}:${code}`]
}
function val(live: Record<string, LivePoint> | undefined, objId: string, code: string): number {
  return pointOf(live, objId, code)?.value ?? 0
}

/** Аварийные коды симулятора (регистр 8) — совпадают с бэкендом. */
const FAULT_LABELS: Record<number, string> = {
  1: 'КЗ, сработка МТЗ',
  2: 'Перегрузка',
  3: 'Перенапряжение',
  4: 'Обрыв кабеля',
}

// ============================================================
// УРОВЕНЬ 0: ЗАВОД (обзорные плитки обеих ТП)
// ============================================================
function FactoryLevel({ tree, live, onOpenTp }: {
  tree: FactoryTree
  live: Record<string, LivePoint> | undefined
  onOpenTp: (tp: FactoryTree) => void
}) {
  return (
    <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', padding: 8 }}>
      {tree.children.map((tp) => {
        // Агрегация «снизу вверх»: статус ТП = худший статус её объектов
        const tpKeys = Object.keys(live ?? {}).filter((k) => k.startsWith(`${tp.id}_`))
        const hasFault = tpKeys.some((k) => k.endsWith(':FAULT') && (live![k]?.value ?? 0) !== 0)
        const hasSuspect = tpKeys.some((k) => !k.endsWith(':FAULT') && live![k]?.quality !== 'good')
        const energized = val(live, `${tp.id}_incomer`, 'BREAKER') === 1
        const p = val(live, `${tp.id}_incomer`, 'P')
        const i = val(live, `${tp.id}_incomer`, 'I')
        const u = val(live, `${tp.id}_incomer`, 'U')

        // Цвет рамки плитки: красный (авария) > оранжевый (suspect) > зелёный (норма)
        const borderColor = hasFault ? '#ff4444' : hasSuspect ? '#ff9f2e' : '#35c46a'

        return (
          <div
            key={tp.id}
            onClick={() => onOpenTp(tp)}
            style={{
              cursor: 'pointer', minWidth: 260, padding: '16px 20px',
              background: '#fff', borderRadius: 10,
              border: `2px solid ${borderColor}`,
              boxShadow: hasFault ? '0 0 12px rgba(255,68,68,.5)' : 'none',
            }}
            title="ЛКМ — открыть схему ТП"
          >
            <div style={{ fontWeight: 700, fontSize: 18, marginBottom: 6 }}>{tp.name}</div>
            {hasFault && (
              <div style={{ color: '#ff4444', fontWeight: 700, marginBottom: 4 }} className="mnemo-blink">
                ⚡ АВАРИЯ
              </div>
            )}
            {hasSuspect && !hasFault && (
              <div style={{ color: '#ff9f2e', fontWeight: 700, marginBottom: 4 }}>⚠️ Ненормальный режим</div>
            )}
            {!hasFault && !hasSuspect && (
              <div style={{ color: energized ? '#35c46a' : '#888', fontWeight: 600, marginBottom: 4 }}>
                {energized ? '● В работе' : '○ Обесточено'}
              </div>
            )}
            {/* Интегральные показатели ввода ТП */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4, color: '#444', fontSize: 14 }}>
              <span>P: <b>{Math.round(p)}</b> кВт</span>
              <span>I: <b>{Math.round(i)}</b> А</span>
              <span>U: <b>{Math.round(u)}</b> В</span>
              <span>Фидеров: {tp.children.filter((c) => c.kind === 'feeder').length}</span>
            </div>
            <div style={{ marginTop: 10, color: '#4d9fff', fontSize: 13 }}>Открыть схему →</div>
          </div>
        )
      })}
    </div>
  )
}

// ============================================================
// УРОВЕНЬ 1: ТП (однолинейная схема с per-object значениями)
// ============================================================
function TpLevel({ tp, live, onOpenObject, canTu, onTu, selectedId }: {
  tp: FactoryTree
  live: Record<string, LivePoint> | undefined
  onOpenObject: (obj: FactoryTree) => void
  canTu: boolean
  onTu: (obj: FactoryTree) => void
  selectedId: string | null
}) {
  const incomerId = `${tp.id}_incomer`
  const breaker = val(live, incomerId, 'BREAKER')
  const faultCode = val(live, incomerId, 'FAULT')
  const energized = breaker === 1
  const u = val(live, incomerId, 'U')
  const i = val(live, incomerId, 'I')
  const p = val(live, incomerId, 'P')
  const q = val(live, incomerId, 'Q')

  const feeders = tp.children.filter((c) => c.kind === 'feeder')
  const feederLabels = tp.feeders ?? feeders.map((_, idx) => `Ф-${idx + 1}`)

  return (
    <div className="card" style={{ background: '#e8eaed' }}>
      <svg viewBox="0 0 760 320" style={{ width: '100%', display: 'block' }}>
        {/* ---------- Ввод 10 кВ ---------- */}
        <text x={20} y={22} fontSize={13} fill="#555">Ввод 10 кВ</text>
        <text x={560} y={22} fontSize={12} fill="#444">
          U={Math.round(u)} В · I={Math.round(i)} А · P={Math.round(p)} кВт · Q={Math.round(q)} кВАр
        </text>
        <line
          x1={380} y1={10} x2={380} y2={70}
          stroke={cableVisual(energized, false).stroke}
          strokeWidth={3}
          strokeDasharray={cableVisual(energized, false).dash}
        />
        {/* Трансформатор (два круга) */}
        <circle cx={380} cy={88} r={14} fill="none" stroke="#555b66" strokeWidth={2.5} />
        <circle cx={380} cy={104} r={14} fill="none" stroke="#555b66" strokeWidth={2.5} />
        <text x={404} y={100} fontSize={12} fill="#555">
          {tp.children.find((c) => c.kind === 'transformer')?.name ?? 'Т'}
        </text>
        <line x1={380} y1={118} x2={380} y2={148} stroke={cableVisual(energized, false).stroke} strokeWidth={3} strokeDasharray={cableVisual(energized, false).dash} />

        {/* Секция шин 0,4 кВ */}
        <line x1={120} y1={150} x2={640} y2={150} stroke="#333" strokeWidth={5} />
        <text x={20} y={155} fontSize={13} fill="#555">Секция 0,4 кВ</text>

        {/* Выключатель ввода (ЛКМ — детали, ПКМ — ТУ) */}
        <g
          style={{ cursor: 'pointer' }}
          pointerEvents="bounding-box"
          onClick={() => onOpenObject(tp.children.find((c) => c.kind === 'incomer')!)}
          onContextMenu={(e) => { e.preventDefault(); if (canTu) onTu(tp.children.find((c) => c.kind === 'incomer')!) }}
        >
          <rect
            x={368} y={122} width={24} height={24}
            fill={breakerVisual(breaker, false).fill}
            stroke={selectedId === incomerId ? '#4d9fff' : '#333'} strokeWidth={2.5}
            className={breakerVisual(breaker, false).blink ? 'mnemo-blink' : ''}
          />
          {breaker === 2 && <text x={374} y={140} fontSize={13} fontWeight="bold" fill="#fff">Р</text>}
          <text x={404} y={140} fontSize={12} fill="#555">Ввод QF1</text>
        </g>

        {/* ---------- 4 фидера: авария мигает ТОЛЬКО на виновнике ---------- */}
        {feeders.map((f, idx) => {
          const x = 170 + idx * 140
          // Авария этого фидера: бэкенд в событии даёт имя ('ТП-1, Ф-2');
          // на схеме фидер-виновник определяется по просадке мощности:
          // бэкенд распределяет P/I равными долями, при cable_break P падает.
          // MVP-индикация: авария на фидере = мигание всех при faultObjIdx=0
          // недоступно из регистров напрямую — используем QUALITY точек фидера.
          const fFaulty = qualBad(live, f.id)
          const vis = breakerVisual(energized ? 1 : 0, fFaulty)
          const fi = val(live, f.id, 'I')
          const fp = val(live, f.id, 'P')
          const cable = cableVisual(energized, fFaulty)
          return (
            <g
              key={f.id}
              style={{ cursor: 'pointer' }}
              pointerEvents="bounding-box"
              onClick={() => onOpenObject(f)}
            >
              <rect
                x={x - 12} y={158} width={24} height={24}
                fill={vis.fill} stroke={selectedId === f.id ? '#4d9fff' : '#333'} strokeWidth={2}
                className={vis.blink ? 'mnemo-blink' : ''}
              />
              <text x={x - 18} y={198} fontSize={12} fill="#555">Ф-{idx + 1}</text>
              <line
                x1={x} y1={182} x2={x} y2={230}
                stroke={cable.stroke} strokeWidth={2.5}
                strokeDasharray={cable.dash}
                className={cable.blink ? 'mnemo-blink' : ''}
              />
              <text x={x - 24} y={250} fontSize={12} fill="#777">
                {energized ? `${Math.round(fi)} А · ${Math.round(fp)} кВт` : '0 А'}
              </text>
              <text x={x - 32} y={266} fontSize={11} fill="#99a">
                {feederLabels[idx]}
              </text>
            </g>
          )
        })}

        {/* ---------- ⚠️ и баннер аварии с указанием фидера ---------- */}
        {live && Object.entries(live).some(([k, pnt]) => k.startsWith(`${tp.id}_`) && pnt.quality !== 'good') && (
          <text x={700} y={30} fontSize={22}>⚠️</text>
        )}
        {faultCode !== 0 && (
          <text x={480} y={30} fontSize={14} fill="#ff4444" className="mnemo-blink">
            АВАРИЯ: {FAULT_LABELS[faultCode] ?? 'неизвестно'}
          </text>
        )}
      </svg>
    </div>
  )
}

/** Есть ли у объекта «плохие» точки (suspect/bad) — признак аварии на нём. */
function qualBad(live: Record<string, LivePoint> | undefined, objId: string): boolean {
  return ['U', 'I', 'P'].some((code) => {
    const pnt = live?.[`${objId}:${code}`]
    return pnt ? pnt.quality !== 'good' : false
  })
}

// ============================================================
// УРОВЕНЬ 2: ОБЪЕКТ (детальная схема + плитки показателей)
// ============================================================
function ObjectLevel({ tp, obj, live }: {
  tp: FactoryTree
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
  return (
    <div>
      <div className="card" style={{ background: '#e8eaed' }}>
        <svg viewBox="0 0 760 200" style={{ width: '100%', display: 'block' }}>
          <text x={20} y={24} fontSize={14} fill="#333">{tp.name} → {obj.name}</text>
          {/* кабель питания объекта */}
          <line
            x1={380} y1={40} x2={380} y2={90}
            stroke={cableVisual(energized, faulty).stroke}
            strokeWidth={3} strokeDasharray={cableVisual(energized, faulty).dash}
            className={cableVisual(energized, faulty).blink ? 'mnemo-blink' : ''}
          />
          {/* выключатель объекта */}
          <rect
            x={368} y={90} width={24} height={24}
            fill={breakerVisual(breaker, faulty).fill}
            stroke="#333" strokeWidth={2.5}
            className={breakerVisual(breaker, faulty).blink ? 'mnemo-blink' : ''}
          />
          <text x={404} y={108} fontSize={12} fill="#555">
            {obj.kind === 'incomer' ? 'Ввод QF1' : 'QF фидера'}
          </text>
          {/* нагрузка */}
          <line x1={380} y1={114} x2={380} y2={150} stroke={cableVisual(energized, faulty).stroke} strokeWidth={3} strokeDasharray={cableVisual(energized, faulty).dash} />
          <rect x={340} y={150} width={80} height={34} rx={4} fill="none" stroke="#555b66" strokeWidth={2} />
          <text x={352} y={172} fontSize={12} fill="#555">
            {obj.kind === 'feeder' ? 'нагрузка' : obj.kind === 'bus' ? 'секция' : 'шины 10 кВ'}
          </text>
          {faulty && (
            <text x={520} y={60} fontSize={14} fill="#ff4444" className="mnemo-blink">
              ⚠️ АВАРИЯ / ОТКЛОНЕНИЕ
            </text>
          )}
        </svg>
      </div>

      {/* Показатели объекта (per-object из live-кэша) */}
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

  // Телеметрия 1 Гц
  const { data: live } = useQuery({
    queryKey: ['live'],
    queryFn: api.live,
    refetchInterval: 1000,
  })

  // Дерево завода (статично — грузится один раз)
  const { data: tree } = useQuery({
    queryKey: ['objectsTree'],
    queryFn: api.objectsTree,
    staleTime: Infinity,
  })

  // Текущий уровень навигации (по умолчанию — завод)
  const [level, setLevel] = useState<Level>({ kind: 'factory' })
  const [selectedId, setSelectedId] = useState<string | null>(null)

  // Диалог ТУ
  const [tuOpen, setTuOpen] = useState(false)
  const [tuObject, setTuObject] = useState('Ввод')

  // Хлебные крошки: Завод → ТП-1 → Ф-2
  const crumbs: Array<{ label: string; go: () => void }> = [
    { label: 'Завод', go: () => setLevel({ kind: 'factory' }) },
  ]
  if (level.kind !== 'factory') {
    crumbs.push({ label: level.tp.name, go: () => setLevel({ kind: 'tp', tp: level.tp }) })
    if (level.kind === 'object') {
      crumbs.push({ label: level.obj.name, go: () => {} })
    }
  }

  if (!tree) return <div className="muted">Загрузка дерева объектов…</div>

  return (
    <>
      <style>{blinkStyle}</style>
      <h2>Мнемосхема</h2>

      {/* ---------- Навигация: хлебные крошки ---------- */}
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
        {level.kind === 'factory' && 'Обзор завода. ЛКМ по ТП — открыть её схему.'}
        {level.kind === 'tp' && `Схема ${level.tp.name}. ЛКМ по объекту — детали; ${canTu ? 'ПКМ по выключателю ввода — телеуправление.' : 'ТУ доступно роли dispatcher+.'}`}
        {level.kind === 'object' && `Детали объекта «${level.obj.name}».`}
      </div>

      {/* ---------- Уровни ---------- */}
      {level.kind === 'factory' && (
        <FactoryLevel tree={tree} live={live} onOpenTp={(tp) => setLevel({ kind: 'tp', tp })} />
      )}
      {level.kind === 'tp' && (
        <TpLevel
          tp={level.tp}
          live={live}
          canTu={canTu}
          selectedId={selectedId}
          onOpenObject={(obj) => setLevel({ kind: 'object', tp: level.tp, obj })}
          onTu={(obj) => { setTuObject(obj.name); setSelectedId(obj.id); setTuOpen(true) }}
        />
      )}
      {level.kind === 'object' && (
        <ObjectLevel tp={level.tp} obj={level.obj} live={live} />
      )}

      {/* Диалог ТУ (двухшаговый алгоритм) */}
      {tuOpen && (
        <TuDialog
          objectName={tuObject}
          onClose={() => { setTuOpen(false); setSelectedId(null) }}
        />
      )}
    </>
  )
}
