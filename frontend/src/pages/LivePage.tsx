// Телеметрия в реальном времени: плитки значений с опросом раз в секунду.
// Цвет плитки = quality-флаг с бэкенда (good/suspect/bad) — ТЗ §3.3.1.

import { useQuery } from '@tanstack/react-query'
import { api, type LivePoint } from '../api'

// Подписи и единицы измерения точек телеметрии
const POINT_META: Record<string, { name: string; unit: string }> = {
  U: { name: 'Напряжение', unit: 'В' },
  I: { name: 'Ток', unit: 'А' },
  P: { name: 'Активная мощность', unit: 'кВт' },
  Q: { name: 'Реактивная мощность', unit: 'кВАр' },
  THDu: { name: 'THDu (искажения U)', unit: '%' },
  THDi: { name: 'THDi (искажения I)', unit: '%' },
  PF: { name: 'Коэф. мощности', unit: 'о.е.' },
  BREAKER: { name: 'Выключатель', unit: '' },
  FAULT: { name: 'Код аварии', unit: '' },
}

// Расшифровка дискретных состояний
const BREAKER_NAMES: Record<number, string> = {
  0: 'отключён',
  1: 'включён',
  2: 'местное упр.',
}
const FAULT_NAMES: Record<number, string> = {
  0: 'нет',
  1: 'КЗ (МТЗ)',
  2: 'перегрузка',
  3: 'перенапряжение',
}

function Tile({ code, point }: { code: string; point: LivePoint }) {
  const meta = POINT_META[code] ?? { name: code, unit: '' }
  // Дискреты показываем словами, аналоги — числом с единицей
  const display =
    code === 'BREAKER'
      ? (BREAKER_NAMES[point.value] ?? point.value)
      : code === 'FAULT'
        ? (FAULT_NAMES[point.value] ?? point.value)
        : point.value.toFixed(1)

  return (
    <div className={`tile q-${point.quality}`}>
      <div className="label">
        {meta.name} {point.quality !== 'good' && '⚠️'}
      </div>
      <div className="value">
        {display} {meta.unit && <span className="unit">{meta.unit}</span>}
      </div>
    </div>
  )
}

export default function LivePage() {
  // refetchInterval: 1000 — опрос раз в секунду (ТЗ: мониторинг в реальном времени)
  const { data, isLoading } = useQuery({
    queryKey: ['live'],
    queryFn: api.live,
    refetchInterval: 1000,
  })

  if (isLoading) return <h2>Загрузка телеметрии...</h2>

  return (
    <>
      <h2>Телеметрия ТП-1 (реальное время)</h2>
      <div className="tiles">
        {data &&
          Object.entries(data)
            .filter(([code]) => POINT_META[code])   // служебные точки не показываем
            .map(([code, point]) => <Tile key={code} code={code} point={point} />)}
      </div>
    </>
  )
}
