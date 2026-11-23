// Контекстное меню «Создать аварию здесь» (специф §16, решение заказчика):
// ПКМ по объекту мнемосхемы -> меню с шаблонами аварий. Доступно admin.
// Меню позиционируется по координатам курсора, закрывается по клику мимо/Esc.

import { useEffect, useRef } from 'react'
import { api } from '../api'
import { useMutation, useQueryClient } from '@tanstack/react-query'

// Шаблоны меню — те же 4 сценария, что в пульте симулятора
const MENU_SCENARIOS: { id: string; label: string }[] = [
  { id: 'short_circuit', label: '⚡ КЗ (сработка МТЗ)' },
  { id: 'overload', label: '🔥 Перегрузка' },
  { id: 'overvoltage', label: '📈 Перенапряжение' },
  { id: 'cable_break', label: '🔌 Обрыв кабеля' },
]

export function FaultContextMenu({ x, y, objectId, objectName, onClose }: {
  x: number
  y: number
  objectId: string
  objectName: string
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

  // Инъекция выбранного шаблона на этот объект
  const faultMutation = useMutation({
    mutationFn: (scenario: string) => api.simFault(objectId, scenario, 10),
    onSuccess: (_d, scenario) => {
      const label = MENU_SCENARIOS.find((s) => s.id === scenario)?.label ?? scenario
      alert(`✔ ${label} — на объекте «${objectName}» (10 с)`)
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
      {MENU_SCENARIOS.map((s) => (
        <div
          key={s.id}
          onClick={() => { onClose(); faultMutation.mutate(s.id) }}
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
