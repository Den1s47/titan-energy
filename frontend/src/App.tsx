// Каркас приложения: 4 приложения в отдельных вкладках (MVP_SCADA_Denis).
//
//   /arm/*    — АРМ диспетчера: мнемосхема, ТУ, журналы (dispatcher/admin)
//   /mes/*    — MES: аналитика + журналы, ТОЛЬКО просмотр (все роли)
//   /sim/*    — Пульт симулятора: БЕЗ логина (учебный стенд)
//   /admin/*  — Админка: пользователи + аудит входов (только admin)
//   /         — редирект по роли (admin/dispatcher -> /arm, viewer -> /mes)
//
// Права всё равно проверяет бэкенд на каждом запросе — роутинг лишь удобство.
// Sidebar каждого приложения содержит перекрёстные ссылки на другие приложения
// (открываются в отдельных вкладках браузера — как требовал заказчик).

import { useEffect } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from './authStore'
import LoginPage from './pages/LoginPage'
import EventsPage from './pages/EventsPage'
import AdminPage from './pages/AdminPage'
import SimControlPage from './pages/SimControlPage'
import MnemoPage from './pages/MnemoPage'
import AnalyticsPage from './pages/AnalyticsPage'
import AlarmBar from './pages/AlarmBar'
import ErrorBoundary from './ErrorBoundary'

// Подписи приложений для перекрёстных ссылок
const APP_LABELS: Record<string, string> = {
  arm: '↗ АРМ диспетчера',
  mes: '↗ MES (аналитика)',
  sim: '↗ Пульт симулятора',
  admin: '↗ Админка',
}

// Пункт навигации внутри текущего приложения
function NavItem({ to, label, visible }: { to: string; label: string; visible: boolean }) {
  if (!visible) return null
  return <NavLink to={to} className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>{label}</NavLink>
}

// Перекрёстные ссылки на ДРУГИЕ приложения (отдельные вкладки)
function CrossAppLinks({ apps }: { apps: string[] }) {
  return (
    <div style={{ padding: '8px 12px', borderTop: '1px solid var(--border)', marginTop: 8 }}>
      <div style={{ fontSize: 11, color: '#889', marginBottom: 6 }}>ДРУГИЕ ПРИЛОЖЕНИЯ</div>
      {apps.map((app) => (
        <a
          key={app}
          href={`/${app}`}
          target="_blank"
          rel="noopener"
          style={{
            display: 'block', padding: '6px 10px', marginBottom: 4,
            color: '#4d9fff', textDecoration: 'none', fontSize: 13,
            borderRadius: 6, background: 'rgba(77,159,255,.08)',
          }}
        >
          {APP_LABELS[app] ?? app}
        </a>
      ))}
    </div>
  )
}

// ---------- АРМ диспетчера ----------
function ArmApp() {
  const user = useAuth((s) => s.user)
  const logout = useAuth((s) => s.logout)
  const navigate = useNavigate()
  const isAdmin = user?.role === 'admin'
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">⚡ АРМ диспетчера</div>
        <NavItem to="/arm" label="Мнемосхема" visible />
        <NavItem to="/arm/events" label="Журналы" visible />
        <CrossAppLinks apps={['mes', 'sim', ...(isAdmin ? ['admin'] : [])]} />
        <div className="spacer" />
        <div className="user-box">
          <b>{user?.username}</b>
          роль: {user?.role}
          <button className="btn secondary" style={{ marginTop: 8, width: '100%' }}
            onClick={() => { logout(); navigate('/login') }}>Выйти</button>
        </div>
      </aside>
      <main className="content" style={{ paddingBottom: 190 }}>
        <Routes>
          <Route path="/arm" element={<ErrorBoundary label="Мнемосхема"><MnemoPage /></ErrorBoundary>} />
          <Route path="/arm/events" element={<ErrorBoundary label="Журналы"><EventsPage /></ErrorBoundary>} />
          <Route path="/arm/*" element={<Navigate to="/arm" replace />} />
        </Routes>
      </main>
      <AlarmBar />
    </div>
  )
}

// ---------- MES (только просмотр: аналитика + журналы) ----------
function MesApp() {
  const user = useAuth((s) => s.user)
  const logout = useAuth((s) => s.logout)
  const navigate = useNavigate()
  const isAdmin = user?.role === 'admin'
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">📊 MES — аналитика</div>
        <NavItem to="/mes" label="Аналитика" visible />
        <NavItem to="/mes/events" label="Журналы (просмотр)" visible />
        <div className="muted" style={{ padding: '4px 12px', fontSize: 11 }}>
          Режим «только просмотр»: телеуправление недоступно
        </div>
        <CrossAppLinks apps={['arm', 'sim', ...(isAdmin ? ['admin'] : [])]} />
        <div className="spacer" />
        <div className="user-box">
          <b>{user?.username}</b>
          роль: {user?.role}
          <button className="btn secondary" style={{ marginTop: 8, width: '100%' }}
            onClick={() => { logout(); navigate('/login') }}>Выйти</button>
        </div>
      </aside>
      <main className="content" style={{ paddingBottom: 190 }}>
        <Routes>
          <Route path="/mes" element={<ErrorBoundary label="Аналитика"><AnalyticsPage /></ErrorBoundary>} />
          <Route path="/mes/events" element={<ErrorBoundary label="Журналы"><EventsPage /></ErrorBoundary>} />
          <Route path="/mes/*" element={<Navigate to="/mes" replace />} />
        </Routes>
      </main>
      <AlarmBar />
    </div>
  )
}

// ---------- Админка ----------
function AdminApp() {
  const user = useAuth((s) => s.user)
  const logout = useAuth((s) => s.logout)
  const navigate = useNavigate()
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">⚙ Админка</div>
        <NavItem to="/admin" label="Пользователи и аудит" visible />
        <CrossAppLinks apps={['arm', 'mes', 'sim']} />
        <div className="spacer" />
        <div className="user-box">
          <b>{user?.username}</b>
          роль: {user?.role}
          <button className="btn secondary" style={{ marginTop: 8, width: '100%' }}
            onClick={() => { logout(); navigate('/login') }}>Выйти</button>
        </div>
      </aside>
      <main className="content" style={{ paddingBottom: 40 }}>
        <Routes>
          <Route path="/admin" element={<ErrorBoundary label="Админка"><AdminPage /></ErrorBoundary>} />
          <Route path="/admin/*" element={<Navigate to="/admin" replace />} />
        </Routes>
      </main>
    </div>
  )
}

// ---------- Корневой компонент: выбор приложения по пути ----------
export default function App() {
  const user = useAuth((s) => s.user)
  const loading = useAuth((s) => s.loading)
  const restore = useAuth((s) => s.restore)
  const location = useLocation()

  // При старте проверяем сохранённый токен (авто-вход)
  useEffect(() => { restore() }, [restore])

  if (loading) return <div className="login-wrap"><div className="muted">Загрузка...</div></div>

  // Пульт симулятора: БЕЗ логина (учебный стенд — безопасность не важна)
  if (location.pathname.startsWith('/sim')) {
    return <SimControlPage />
  }

  // Все остальные приложения требуют входа
  if (!user) return <LoginPage />

  const isAdmin = user.role === 'admin'
  const isDispatcher = user.role === 'dispatcher' || isAdmin

  // АРМ: только dispatcher/admin (viewer идёт в MES — «только просмотр»)
  if (location.pathname.startsWith('/arm')) {
    return isDispatcher ? <ArmApp /> : <Navigate to="/mes" replace />
  }

  // Админка: только admin
  if (location.pathname.startsWith('/admin')) {
    return isAdmin ? <AdminApp /> : <Navigate to="/arm" replace />
  }

  // MES: все вошедшие
  if (location.pathname.startsWith('/mes')) {
    return <MesApp />
  }

  // / и /login после входа: редирект по роли
  const dest = isAdmin || isDispatcher ? '/arm' : '/mes'
  return <Navigate to={dest} replace />
}
