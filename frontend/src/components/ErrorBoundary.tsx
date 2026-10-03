import { Component, ErrorInfo, ReactNode } from "react";

type Props = { children: ReactNode };
type State = { error: Error | null };

/**
 * Catches render-time crashes anywhere below it so a single thrown error shows a
 * recoverable panel instead of a blank screen. Server data is never touched by a
 * UI crash — reloading re-fetches fresh state.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Keep a breadcrumb in the console for debugging; no external reporting.
    console.error("UI crash:", error, info.componentStack);
  }

  private handleReload = () => {
    this.setState({ error: null });
    window.location.reload();
  };

  render() {
    if (this.state.error) {
      return (
        <div className="shell">
          <div className="panel error error-boundary">
            <h2>Что-то сломалось в интерфейсе</h2>
            <p>Страница упала с ошибкой рендера. Данные на сервере не затронуты — обычно помогает перезагрузка.</p>
            <pre className="error-boundary-detail">{this.state.error.message}</pre>
            <button onClick={this.handleReload}>Перезагрузить</button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
