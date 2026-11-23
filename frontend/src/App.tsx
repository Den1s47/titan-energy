// Каркас приложения: сайдбар-навигация + роутинг с guard'ами по ролям.
// Права всё равно проверяет бэкенд — guard лишь прячет недоступные пункты меню.

import { useEffect } from 'react'
import { NavLink, Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { useAuth } from './authStore'
import LoginPage from './pages/LoginPage'
import LivePage from './pages/LivePage'
import EventsPage from './pages/EventsPage'
import AdminPage from './pages/AdminPage'
import SimControlPage from './pages/SimControlPage'
import MnemoPage from './pages/MnemoPage'
import AlarmBar from './pages/AlarmBar'
import AnalyticsPage from './pages/AnalyticsPage'

// Пункт сайдбара
function NavItem({ to, label, visible }: { to: string; label: string; visible: boolean }) {
  if (!visible) return null
  return <NavLink to={to} className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>{label}</NavLink>
}

function Layout() {
  const user = useAuth((s) => s.user)
  const logout = useAuth((s) => s.logout)
  const navigate = useNavigate()

  const isAdmin = user?.role === 'admin'

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">⚡ Титан-Энергия</div>
        <NavItem to="/" label="Мнемосхема" visible />
        <NavItem to="/live" label="Телеметрия" visible />
        <NavItem to="/events" label="Журналы" visible />
        <NavItem to="/analytics" label="Аналитика" visible />
        <NavItem to="/admin" label="Администрирование" visible={isAdmin} />
        <NavItem to="/sim" label="Пульт симулятора" visible={isAdmin} />
        <div className="spacer" />
        <div className="user-box">
          <b>{user?.username}</b>
          роль: {user?.role}
          <button
            className="btn secondary"
            style={{ marginTop: 8, width: '100%' }}
            onClick={() => { logout(); navigate('/login') }}
          >
            Выйти
          </button>
        </div>
      </aside>
      <main className="content" style={{ paddingBottom: 190 }}>
        <Routes>
          <Route path="/" element={<MnemoPage />} />
          <Route path="/live" element={<LivePage />} />
          <Route path="/events" element={<EventsPage />} />
          <Route path="/analytics" element={<AnalyticsPage />} />
          {/* Admin-зоны: не-admin редиректит на главную (бэкенд ещё и 403 вернёт) */}
          <Route path="/admin" element={isAdmin ? <AdminPage /> : <Navigate to="/" replace />} />
          <Route path="/sim" element={isAdmin ? <SimControlPage /> : <Navigate to="/" replace />} />
        </Routes>
      </main>
      {/* Аларм-бар внизу всех экранов (ТЗ 3.3.3) */}
      <AlarmBar />
    </div>
  )
}

export default function App() {
  const user = useAuth((s) => s.user)
  const loading = useAuth((s) => s.loading)
  const restore = useAuth((s) => s.restore)

  // При старте проверяем сохранённый токен (авто-вход)
  useEffect(() => { restore() }, [restore])

  if (loading) return <div className="login-wrap"><div className="muted">Загрузка...</div></div>

  // Не вошёл -> логин; вошёл -> каркас с навигацией
  return user ? <Layout /> : <LoginPage />
}
