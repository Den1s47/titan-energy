// Страница аналитики MES-уровня (специф §9.2):
// - временной ряд с усреднением и Min/Max/средним (ТЗ «профили показателей»);
// - гистограмма с модой/медианой (ТЗ «анализ»);
// - карта Шухарта с CL ± 3σ и подсветкой выхода процесса;
// - карта CUSUM от целевого значения;
// - экспорт CSV / Excel (ТЗ «Отчёты»).
//
// Графики — ECharts (специф §3: зум, пороговые линии, большие объёмы точек).

import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import * as echarts from 'echarts'
import { api } from '../api'

// Точки телеметрии для анализа (подмножество MVP)
const POINTS = [
  { code: 'P', name: 'Активная мощность, кВт' },
  { code: 'I', name: 'Ток, А' },
  { code: 'U', name: 'Напряжение, В' },
  { code: 'THDi', name: 'THDi, %' },
]

// Интервалы усреднения из ТЗ (специф §9.2 «Выбор»)
const AVG_OPTIONS = [
  { value: 30, label: '30 сек' },
  { value: 60, label: '1 мин' },
  { value: 180, label: '3 мин' },
  { value: 300, label: '5 мин' },
]

function useEcharts(deps: unknown[], build: (chart: echarts.ECharts) => void) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)
  const buildRef = useRef(build)
  buildRef.current = build   // всегда свежая функция отрисовки

  useEffect(() => {
    if (!ref.current) return
    // Создаём график один раз; при новых данных только setOption (без мерцания)
    if (!chartRef.current) {
      chartRef.current = echarts.init(ref.current)
      const onResize = () => chartRef.current?.resize()
      window.addEventListener('resize', onResize)
    }
    buildRef.current(chartRef.current)
  }, deps)
  // Освобождение при размонтировании страницы
  useEffect(() => () => {
    chartRef.current?.dispose()
    chartRef.current = null
  }, [])
  return ref
}

export default function AnalyticsPage() {
  // ---------- Параметры выборки (раздел «Выбор» ТЗ) ----------
  const [pointCode, setPointCode] = useState('P')
  const [minutes, setMinutes] = useState(15)
  const [avgS, setAvgS] = useState(60)
  const [cusumTarget, setCusumTarget] = useState(100)
  // Автообновление графиков (реальное время, ТЗ: «последние 5/10/15/30 минут —
  // скользящее окно, тренд»). Отключаемо, чтобы не мешать разглядывать историю.
  const [autoRefresh, setAutoRefresh] = useState(true)
  const REFRESH_MS = 5000   // раз в 5 с — баланс между «живостью» и нагрузкой

  const seriesQuery = useQuery({
    queryKey: ['analytics-series', pointCode, minutes, avgS],
    queryFn: () => api.analyticsSeries(pointCode, minutes, avgS),
    refetchInterval: autoRefresh ? REFRESH_MS : false,
  })
  const histQuery = useQuery({
    queryKey: ['analytics-hist', pointCode, minutes],
    queryFn: () => api.analyticsHistogram(pointCode, minutes),
    refetchInterval: autoRefresh ? REFRESH_MS * 3 : false,   // гистограмма меняется медленнее
  })
  const shewhartQuery = useQuery({
    queryKey: ['analytics-shewhart', pointCode, minutes],
    queryFn: () => api.analyticsShewhart(pointCode, minutes),
    refetchInterval: autoRefresh ? REFRESH_MS : false,
  })
  const cusumQuery = useQuery({
    queryKey: ['analytics-cusum', pointCode, minutes, cusumTarget],
    queryFn: () => api.analyticsCusum(pointCode, minutes, cusumTarget),
    refetchInterval: autoRefresh ? REFRESH_MS : false,
  })

  // ---------- Графики ----------
  const seriesRef = useEcharts([seriesQuery.data], (chart) => {
    const d = seriesQuery.data
    if (!d) return
    chart.setOption({
      title: { text: `Временной ряд ${pointCode}`, textStyle: { color: '#d7dae0', fontSize: 14 } },
      tooltip: { trigger: 'axis' },
      dataZoom: [{ type: 'inside' }, { type: 'slider' }],   // зум мышью (ТЗ)
      xAxis: { type: 'category', data: d.times, axisLabel: { color: '#8b93a0' } },
      yAxis: { type: 'value', axisLabel: { color: '#8b93a0' }, splitLine: { lineStyle: { color: '#363b44' } } },
      series: [
        { name: pointCode, type: 'line', data: d.values, symbol: 'none', lineStyle: { color: '#4d9fff', width: 1 } },
        { name: `скользящее среднее (${d.avg_window_s}с)`, type: 'line', data: d.moving_avg, symbol: 'none',
          lineStyle: { color: '#35c46a', width: 2 } },
      ],
      legend: { textStyle: { color: '#8b93a0' } },
      grid: { left: 60, right: 20, top: 40, bottom: 60 },
    })
  })

  const histRef = useEcharts([histQuery.data], (chart) => {
    const h = histQuery.data
    if (!h) return
    const centers = h.bin_edges.slice(0, -1).map((e, i) => ((e + h.bin_edges[i + 1]) / 2).toFixed(1))
    chart.setOption({
      title: { text: `Гистограмма ${pointCode}`, textStyle: { color: '#d7dae0', fontSize: 14 } },
      tooltip: {},
      xAxis: { type: 'category', data: centers, axisLabel: { color: '#8b93a0' } },
      yAxis: { type: 'value', axisLabel: { color: '#8b93a0' }, splitLine: { lineStyle: { color: '#363b44' } } },
      series: [{ type: 'bar', data: h.bin_counts, itemStyle: { color: '#4d9fff' },
        markLine: {
          // Пороговые линии: мода и медиана (ТЗ: пороговые линии задаются)
          symbol: 'none', lineStyle: { color: '#ff9f2e' },
          data: [{ xAxis: nearestIndex(centers, h.mode), label: { formatter: 'мода' } }],
        } }],
      grid: { left: 60, right: 20, top: 40, bottom: 40 },
    })
  })

  const shewhartRef = useEcharts([shewhartQuery.data], (chart) => {
    const s = shewhartQuery.data
    if (!s) return
    // Точки выхода из-под контроля — красные (остальные синие)
    const pointData = s.points.map((v, i) =>
      s.out_of_control.includes(i) ? { value: v, itemStyle: { color: '#ff4444' } } : v)
    chart.setOption({
      title: { text: 'Карта Шухарта (CL ± 3σ)', textStyle: { color: '#d7dae0', fontSize: 14 } },
      tooltip: { trigger: 'axis' },
      dataZoom: [{ type: 'inside' }],
      xAxis: { type: 'category', data: s.points.map((_, i) => i), axisLabel: { color: '#8b93a0' } },
      yAxis: { type: 'value', axisLabel: { color: '#8b93a0' }, splitLine: { lineStyle: { color: '#363b44' } } },
      series: [{
        type: 'line', data: pointData, symbol: 'circle', symbolSize: 4, lineStyle: { color: '#4d9fff', width: 1 },
        markLine: {
          symbol: 'none', silent: true,
          data: [
            { yAxis: s.ucl, label: { formatter: 'UCL (3σ)', color: '#ff4444' }, lineStyle: { color: '#ff4444' } },
            { yAxis: s.cl, label: { formatter: 'CL', color: '#35c46a' }, lineStyle: { color: '#35c46a' } },
            { yAxis: s.lcl, label: { formatter: 'LCL (-3σ)', color: '#ff4444' }, lineStyle: { color: '#ff4444' } },
          ],
        },
      }],
      grid: { left: 60, right: 20, top: 40, bottom: 60 },
    })
  })

  const cusumRef = useEcharts([cusumQuery.data], (chart) => {
    const c = cusumQuery.data
    if (!c) return
    chart.setOption({
      title: { text: `CUSUM (target = ${c.target})`, textStyle: { color: '#d7dae0', fontSize: 14 } },
      tooltip: { trigger: 'axis' },
      dataZoom: [{ type: 'inside' }],
      xAxis: { type: 'category', data: c.values.map((_, i) => i), axisLabel: { color: '#8b93a0' } },
      yAxis: { type: 'value', axisLabel: { color: '#8b93a0' }, splitLine: { lineStyle: { color: '#363b44' } } },
      series: [{ type: 'line', data: c.values, symbol: 'none', lineStyle: { color: '#ff9f2e', width: 2 },
        markLine: { symbol: 'none', data: [{ yAxis: 0, label: { formatter: 'baseline' } }] } }],
      grid: { left: 60, right: 20, top: 40, bottom: 60 },
    })
  })

  const stats = seriesQuery.data?.stats

  return (
    <>
      <h2>Аналитика (MES-уровень)</h2>

      {/* ---------- Выбор (ТЗ: объекты, интервалы, усреднение) ---------- */}
      <div className="card">
        <div className="row">
          <label>
            Показатель:{' '}
            <select value={pointCode} onChange={(e) => setPointCode(e.target.value)}>
              {POINTS.map((p) => <option key={p.code} value={p.code}>{p.name}</option>)}
            </select>
          </label>
          <label>
            Период:{' '}
            <select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>
              <option value={5}>5 минут</option>
              <option value={15}>15 минут</option>
              <option value={30}>30 минут</option>
            </select>
          </label>
          <label>
            Усреднение:{' '}
            <select value={avgS} onChange={(e) => setAvgS(Number(e.target.value))}>
              {AVG_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
          <label>
            CUSUM target:{' '}
            <input type="number" value={cusumTarget} style={{ width: 80 }}
                   onChange={(e) => setCusumTarget(Number(e.target.value))} />
          </label>
          {/* Реальное время: скользящее окно обновляется само (ТЗ «тренд») */}
          <label style={{ marginLeft: 'auto' }}>
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              style={{ marginRight: 6 }}
            />
            Обновление 5 с
          </label>
        </div>
      </div>

      {/* ---------- Статистика ряда (ТЗ: Макс/Мин/среднее для каждой переменной) ---------- */}
      {stats && (
        <div className="tiles" style={{ marginBottom: 16 }}>
          <div className="tile"><div className="label">Максимум</div>
            <div className="value" style={{ color: 'var(--text)' }}>{stats.max.toFixed(1)}</div></div>
          <div className="tile"><div className="label">Минимум</div>
            <div className="value" style={{ color: 'var(--text)' }}>{stats.min.toFixed(1)}</div></div>
          <div className="tile"><div className="label">Среднее</div>
            <div className="value" style={{ color: 'var(--text)' }}>{stats.mean.toFixed(1)}</div></div>
          <div className="tile"><div className="label">σ (ст. отклонение)</div>
            <div className="value" style={{ color: 'var(--text)' }}>{stats.std.toFixed(2)}</div></div>
        </div>
      )}

      {/* ---------- Графики ---------- */}
      <div className="card"><div ref={seriesRef} style={{ height: 300 }} /></div>
      <div className="card"><div ref={shewhartRef} style={{ height: 260 }} /></div>
      <div className="row" style={{ alignItems: 'stretch' }}>
        <div className="card" style={{ flex: 1 }}><div ref={histRef} style={{ height: 240 }} /></div>
        <div className="card" style={{ flex: 1 }}><div ref={cusumRef} style={{ height: 240 }} /></div>
      </div>

      {/* ---------- Гистограмма: мода/медиана (ТЗ) ---------- */}
      {histQuery.data && (
        <div className="muted" style={{ marginBottom: 16 }}>
          Гистограмма {pointCode}: мода ≈ {histQuery.data.mode.toFixed(1)},
          медиана = {histQuery.data.median.toFixed(1)},
          σ = {histQuery.data.std.toFixed(2)}, 3σ = {(histQuery.data.std3 ?? 0).toFixed(2)}
        </div>
      )}

      {/* ---------- Экспорт (ТЗ: Excel/CSV) ---------- */}
      <div className="card">
        <h3>Экспорт выборки</h3>
        <div className="row">
          <a className="btn secondary" style={{ textDecoration: 'none' }}
             href={api.exportCsvUrl(pointCode, minutes)}>Скачать CSV</a>
          <a className="btn" style={{ textDecoration: 'none' }}
             href={api.exportXlsxUrl(pointCode, minutes)}>Скачать Excel</a>
        </div>
      </div>
    </>
  )
}

/** Индекс категории на оси X, ближайшей к значению (для markLine моды). */
function nearestIndex(centers: string[], value: number): number {
  let best = 0
  let bestDiff = Infinity
  centers.forEach((c, i) => {
    const diff = Math.abs(Number(c) - value)
    if (diff < bestDiff) { bestDiff = diff; best = i }
  })
  return best
}
