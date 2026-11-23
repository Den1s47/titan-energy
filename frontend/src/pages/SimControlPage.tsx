// Пульт симулятора (учебная фича MVP, только admin — специф §9.3):
// кнопки инъекций аварий для демонстрации реакций системы.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'

// Кнопки сценариев: имя API -> подпись на кнопке
const SCENARIOS: { id: string; label: string; css: string }[] = [
  { id: 'short_circuit', label: 'КЗ (сработка МТЗ)', css: 'danger' },
  { id: 'overload', label: 'Перегрузка', css: 'warn' },
  { id: 'overvoltage', label: 'Перенапряжение', css: 'warn' },
  { id: 'actuator_fail', label: 'Отказ привода ТУ', css: 'secondary' },
  { id: 'comm_loss', label: 'Потеря связи', css: 'secondary' },
]

export default function SimControlPage() {
  const queryClient = useQueryClient()
  const [message, setMessage] = useState<string | null>(null)

  const faultMutation = useMutation({
    mutationFn: (scenario: string) => api.simFault(scenario),
    onSuccess: (_data, scenario) => {
      setMessage(`Сценарий "${scenario}" запущен на 8 с`)
      // события появятся через ~1 с — обновим журналы
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ['events'] }), 1500)
    },
    onError: (e) => setMessage(e instanceof Error ? e.message : 'Ошибка'),
  })

  return (
    <>
      <h2>Пульт симулятора</h2>
      <div className="muted" style={{ marginBottom: 12 }}>
        Инъекции аварий в «оборудование» для проверки реакций системы.
        Раздел существует только в MVP — при подключении реального оборудования скрывается.
      </div>
      <div className="card">
        <div className="row">
          {SCENARIOS.map((s) => (
            <button
              key={s.id}
              className={`btn ${s.css}`}
              disabled={faultMutation.isPending}
              onClick={() => faultMutation.mutate(s.id)}
            >
              {s.label}
            </button>
          ))}
        </div>
        {message && <div className="ok-msg">{message}</div>}
      </div>
    </>
  )
}
