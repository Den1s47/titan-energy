// Admin-страница: управление пользователями (специф §9.3: список, создание,
// смена роли, деактивация) + журнал действий операторов.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../api'

export default function AdminPage() {
  const queryClient = useQueryClient()

  // --- список пользователей ---
  const usersQuery = useQuery({ queryKey: ['users'], queryFn: api.users })
  const actionsQuery = useQuery({
    queryKey: ['actions'],
    queryFn: api.actions,
    refetchInterval: 5000,     // журнал оператора обновляется раз в 5 с
  })

  // --- создание пользователя ---
  const [newUsername, setNewUsername] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [newRole, setNewRole] = useState('viewer')
  const [createError, setCreateError] = useState<string | null>(null)

  const createMutation = useMutation({
    mutationFn: () => api.createUser(newUsername, newPassword, newRole),
    onSuccess: () => {
      setNewUsername('')
      setNewPassword('')
      setCreateError(null)
      queryClient.invalidateQueries({ queryKey: ['users'] })
    },
    onError: (e) => setCreateError(e instanceof Error ? e.message : 'Ошибка создания'),
  })

  function onCreate(e: FormEvent) {
    e.preventDefault()
    createMutation.mutate()
  }

  // --- смена роли / деактивация ---
  const updateMutation = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: { role?: string; is_active?: boolean } }) =>
      api.updateUser(id, patch),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['users'] }),
  })

  return (
    <>
      <h2>Администрирование</h2>

      {/* ---------- Пользователи ---------- */}
      <div className="card">
        <h3>Пользователи</h3>
        <table>
          <thead>
            <tr><th>Логин</th><th>Роль</th><th>Активен</th><th>Действия</th></tr>
          </thead>
          <tbody>
            {usersQuery.data?.users.map((u) => (
              <tr key={u.id}>
                <td>{u.username}</td>
                <td>
                  <select
                    value={u.role}
                    onChange={(e) =>
                      updateMutation.mutate({ id: u.id, patch: { role: e.target.value } })
                    }
                  >
                    <option value="admin">admin</option>
                    <option value="dispatcher">dispatcher</option>
                    <option value="viewer">viewer</option>
                  </select>
                </td>
                <td>{u.is_active ? 'да' : 'нет'}</td>
                <td>
                  <button
                    className={u.is_active ? 'btn danger' : 'btn secondary'}
                    onClick={() =>
                      updateMutation.mutate({ id: u.id, patch: { is_active: !u.is_active } })
                    }
                  >
                    {u.is_active ? 'Деактивировать' : 'Активировать'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ---------- Создание пользователя ---------- */}
      <div className="card">
        <h3>Новый пользователь</h3>
        <form className="row" onSubmit={onCreate}>
          <input placeholder="Логин" value={newUsername}
                 onChange={(e) => setNewUsername(e.target.value)} />
          <input placeholder="Пароль" type="password" value={newPassword}
                 onChange={(e) => setNewPassword(e.target.value)} />
          <select value={newRole} onChange={(e) => setNewRole(e.target.value)}>
            <option value="viewer">viewer</option>
            <option value="dispatcher">dispatcher</option>
            <option value="admin">admin</option>
          </select>
          <button className="btn" disabled={!newUsername || !newPassword}>
            Создать
          </button>
        </form>
        {createError && <div className="error-msg">{createError}</div>}
      </div>

      {/* ---------- Аудит входов (кто/когда входил, неудачные попытки) ---------- */}
      <div className="card">
        <h3>Аудит входов в систему (последние 20)</h3>
        <table>
          <thead>
            <tr><th>Время</th><th>Кто</th><th>Результат</th><th>Детали</th></tr>
          </thead>
          <tbody>
            {actionsQuery.data?.actions
              .filter((a) => a.action === 'login')
              .slice(0, 20)
              .map((a, i) => (
                <tr key={`l${i}`} style={a.result === 'failed' ? { background: '#fff2f0' } : undefined}>
                  <td>{a.time.slice(11, 23)}</td>
                  <td><b>{a.username}</b></td>
                  <td className={a.result === 'success' ? 'ok-msg' : 'error-msg'}>
                    {a.result === 'success' ? '✓ вход' : '✗ отказ'}
                  </td>
                  <td className="muted">{a.details ?? (a.result === 'success' ? 'успешный вход' : '')}</td>
                </tr>
              ))}
          </tbody>
        </table>
        <div className="muted" style={{ marginTop: 6, fontSize: 12 }}>
          Все входы (успешные и неудачные) логируются автоматически — специф §11 «Аудит».
        </div>
      </div>

      {/* ---------- Журнал действий ---------- */}
      <div className="card">
        <h3>Журнал действий операторов (последние)</h3>
        <table>
          <thead>
            <tr><th>Время</th><th>Кто</th><th>Действие</th><th>Результат</th><th>Детали</th></tr>
          </thead>
          <tbody>
            {actionsQuery.data?.actions.slice(0, 20).map((a, i) => (
              <tr key={i}>
                <td>{a.time.slice(11, 19)}</td>
                <td>{a.username}</td>
                <td>{a.action}</td>
                <td className={a.result === 'success' ? 'ok-msg' : 'error-msg'}>{a.result}</td>
                <td className="muted">{a.details}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
