// Пульт симулятора (учебная фича MVP, только admin — специф §9.3, §16):
// ТОЧЕЧНЫЕ инъекции аварий на выбранный объект завода.
//
// Порядок работы: 1) выбрать объект в дереве; 2) выбрать шаблон аварии;
// 3) запустить. Шаблоны предопределены — придумывать параметры не нужно.
//
// Шаблоны (совпадают с бэкендом):
//   short_circuit — КЗ: сработка МТЗ, отключение ввода, осциллограмма
//   overload      — перегрузка: оранжевые плитки I/P/THDi
//   overvoltage   — перенапряжение: U -> 460 В
//   cable_break   — обрыв кабеля (на вводе = потеря связи всей ТП!)

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type FactoryTree } from '../api'

// Шаблоны аварий: id API -> подпись + пояснение + класс кнопки
const SCENARIOS: { id: string; label: string; hint: string; css: string }[] = [
  { id: 'line_loss_110', label: 'Потеря ввода 110 кВ', hint: 'Плечо обесточено целиком; переброс через секционный QF. Только на вводах 110 кВ!', css: 'danger' },
  { id: 'short_circuit', label: 'КЗ (МТЗ)', hint: 'Ток ×6, отключение ввода, осциллограмма', css: 'danger' },
  { id: 'overload', label: 'Перегрузка', hint: 'Ток/мощность за границы — оранжевые плитки', css: 'warn' },
  { id: 'overvoltage', label: 'Перенапряжение', hint: 'U → 460 В', css: 'warn' },
  { id: 'cable_break', label: 'Обрыв кабеля', hint: 'На вводе = потеря связи всей ТП', css: 'secondary' },
]

export default function SimControlPage() {
  const queryClient = useQueryClient()
  const [message, setMessage] = useState<string | null>(null)

  // Дерево завода для выбора объекта (статичное)
  const { data: tree } = useQuery({
    queryKey: ['objectsTree'],
    queryFn: api.objectsTree,
    staleTime: Infinity,
  })
  const [objectId, setObjectId] = useState('tp1_incomer')
  const [duration, setDuration] = useState(8)

  const faultMutation = useMutation({
    mutationFn: (args: { objectId: string; scenario: string; duration: number }) =>
      api.simFault(args.objectId, args.scenario, args.duration),
    onSuccess: (data, args) => {
      setMessage(`✔ Авария «${args.scenario}» на объекте «${data.object}» запущена на ${args.duration} с`)
      // события появятся через ~1 с — обновим журналы и телеметрию
      setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ['events'] })
        queryClient.invalidateQueries({ queryKey: ['live'] })
      }, 1500)
    },
    onError: (e) => setMessage(e instanceof Error ? e.message : 'Ошибка'),
  })

  return (
    <>
      <h2>Пульт симулятора</h2>
      <div className="muted" style={{ marginBottom: 12 }}>
        Точечные инъекции аварий на выбранный объект. Раздел существует только в MVP —
        при подключении реального оборудования скрывается (специф §13).
      </div>

      <div className="card" style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
        {/* ---------- Шаг 1: выбор объекта ---------- */}
        <div style={{ minWidth: 280 }}>
          <div style={{ fontWeight: 700, marginBottom: 6 }}>1. Объект аварии</div>
          {tree ? (
            <ObjectPicker tree={tree} value={objectId} onChange={setObjectId} />
          ) : (
            <div className="muted">Загрузка дерева объектов…</div>
          )}
        </div>

        {/* ---------- Шаг 2: шаблон + запуск ---------- */}
        <div style={{ flex: 1, minWidth: 300 }}>
          <div style={{ fontWeight: 700, marginBottom: 6 }}>2. Шаблон аварии</div>
          <div style={{ display: 'grid', gap: 8 }}>
            {SCENARIOS.map((s) => (
              <button
                key={s.id}
                className={`btn ${s.css}`}
                disabled={faultMutation.isPending}
                onClick={() => faultMutation.mutate({ objectId, scenario: s.id, duration })}
                style={{ textAlign: 'left' }}
              >
                <b>{s.label}</b>
                <div style={{ fontSize: 12, opacity: 0.8 }}>{s.hint}</div>
              </button>
            ))}
          </div>
          {/* Длительность аварии */}
          <div style={{ marginTop: 12 }}>
            Длительность: <b>{duration} с</b>
            <input
              type="range" min={3} max={30} value={duration}
              onChange={(e) => setDuration(Number(e.target.value))}
              style={{ marginLeft: 10, verticalAlign: 'middle' }}
            />
          </div>
          {message && <div className="ok-msg" style={{ marginTop: 10 }}>{message}</div>}
        </div>
      </div>
    </>
  )
}

// ---------- Выбор объекта из дерева (радио-список с отступами по уровню) ----------

function ObjectPicker({ tree, value, onChange }: {
  tree: FactoryTree
  value: string
  onChange: (id: string) => void
}) {
  // Плоский список узлов с глубиной (для отступов)
  const flat: Array<{ node: FactoryTree; depth: number }> = []
  const walk = (n: FactoryTree, depth: number) => {
    flat.push({ node: n, depth })
    n.children.forEach((c) => walk(c, depth + 1))
  }
  walk(tree, 0)

  return (
    <div style={{ display: 'grid', gap: 2, maxHeight: 340, overflowY: 'auto' }}>
      {flat.map(({ node, depth }) => (
        <label
          key={node.id}
          style={{
            display: 'flex', gap: 8, alignItems: 'center',
            paddingLeft: depth * 18, padding: '4px 6px',
            background: value === node.id ? '#e3f0ff' : 'transparent',
            borderRadius: 6, cursor: 'pointer',
            fontWeight: node.kind === 'group' ? 700 : 400,
          }}
        >
          <input
            type="radio"
            checked={value === node.id}
            onChange={() => onChange(node.id)}
          />
          {node.name}
          {node.kind !== 'group' && (
            <span style={{ fontSize: 11, color: '#889' }}>
              {node.kind === 'incomer' ? '(ввод)' : node.kind === 'feeder' ? '(фидер)' :
                node.kind === 'bus' ? '(секция)' : '(транс)'}
            </span>
          )}
        </label>
      ))}
    </div>
  )
}
