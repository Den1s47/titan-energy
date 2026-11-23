// Error Boundary — перехват ошибок рендера React (специф: устойчивый UI).
//
// Без boundary любая ошибка в компоненте (неожиданный null, битый ответ API,
// баг в разметке) уносит ВЕСЬ root -> «чёрный экран», помогает только F5.
// С boundary падает только сломанный блок, остальной UI жив, и есть кнопка
// «Повторить» (сброс состояния компонента без перезагрузки страницы).

import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  /** Подпись блока для диагностики («Журналы», «Аналитика»...). */
  label?: string
}

interface State {
  error: Error | null
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Лог в консоль для диагностики (в проде можно отправлять на сервер)
    console.error(`[ErrorBoundary${this.props.label ? `: ${this.props.label}` : ''}]`, error, info)
  }

  render() {
    if (this.state.error) {
      return (
        <div className="card" style={{ borderColor: 'var(--bad)' }}>
          <h3 style={{ color: 'var(--bad)' }}>
            Ошибка в блоке {this.props.label ?? 'интерфейса'}
          </h3>
          <div className="muted" style={{ margin: '8px 0', wordBreak: 'break-word' }}>
            {this.state.error.message}
          </div>
          {/* Сброс ошибки -> React заново рендерит блок (данные Query ещё кешированы) */}
          <button className="btn" onClick={() => this.setState({ error: null })}>
            Повторить
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
