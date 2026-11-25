// Контекстное меню «Создать аварию здесь» (специф §16, решение заказчика):
// ПКМ по объекту мнемосхемы -> меню с шаблонами аварий. Доступно admin.
// Меню позиционируется по координатам курсора, закрывается по клику мимо/Esc.

import { useEffect, useRef } from 'react'
import { api } from '../api'
import { useMutation, useQueryClient } from '@tanstack/react-query'

// Шаблоны меню. Набор зависит от ТИПА объекта (kind):
//   - «Потеря ввода 110 кВ» — только для вводов 110 кВ (incomer110):
//     сценарий означает обрыв ЛЭП, на фидере он бессмыслен;
//   - остальные 4 сценария доступны на любом элементе.
// Длительность: 25 с для потери 110 (чтобы успеть показать переброс),
// 10 с для остальных.
const BASE_SCENARIOS: { id: string; label: string; dur: number }[] = [
  { id: 'short_circuit', label: '⚡ КЗ (сработка МТЗ)', dur: 10 },
  { id: 'overload', label: '🔥 Перегрузка', dur: 10 },
  { id: 'overvoltage', label: '📈 Перенапряжение', dur: 10 },
  { id: 'cable_break', label: '🔌 Обрыв кабеля', dur: 10 },
]
const LINE_LOSS = { id: 'line_loss_110', label: '⚡ Потеря ввода 110 кВ', dur: 25 }

export function FaultContextMenu({ x, y, objectId, objectName, kind, onClose }: {
  x: number
  y: number
  objectId: string
  objectName: string
  kind?: string
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const ref = useRef<HTMLDivElement>(null)

  // Закрытие: клик мимо меню или Esc
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [onClose])

  // Список сценариев для этого объекта: «потеря 110» только у вводов 110 кВ
  const scenarios: { id: string; label: string; dur: number }[] =
    kind === 'incomer110' ? [LINE_LOSS, ...BASE_SCENARIOS] : BASE_SCENARIOS

  // Инъекция выбранного шаблона на этот объект (длительность из шаблона)
  const faultMutation = useMutation({
    mutationFn: (s: { id: string; dur: number }) => api.simFault(objectId, s.id, s.dur),
    onSuccess: (_d, s) => {
      const label = scenarios.find((v) => v.id === s.id)?.label ?? s.id
      alert(`✔ ${label} — на объекте «${objectName}» (${s.dur} с)`)
      queryClient.invalidateQueries({ queryKey: ['events'] })
      queryClient.invalidateQueries({ queryKey: ['live'] })
    },
    onError: (e) => alert(e instanceof Error ? e.message : 'Ошибка запуска аварии'),
  })

  return (
    <div
      ref={ref}
      style={{
        position: 'fixed', left: x, top: y, zIndex: 100,
        background: '#fff', border: '1px solid #ccc', borderRadius: 8,
        boxShadow: '0 4px 16px rgba(0,0,0,.25)', padding: 6, minWidth: 230,
      }}
      onContextMenu={(e) => e.preventDefault()}
    >
      <div style={{ fontSize: 12, fontWeight: 700, color: '#555', padding: '4px 8px' }}>
        Создать аварию: {objectName}
      </div>
      {scenarios.map((s) => (
        <div
          key={s.id}
          onClick={() => { onClose(); faultMutation.mutate(s) }}
          style={{
            padding: '7px 10px', cursor: 'pointer', borderRadius: 6, fontSize: 13,
          }}
          onMouseEnter={(e) => (e.currentTarget.style.background = '#e3f0ff')}
          onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
        >
          {s.label}
        </div>
      ))}
    </div>
  )
}
