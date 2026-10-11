import { Component, type ErrorInfo, type ReactNode } from "react";

/** Błąd renderowania jednej strony nie zostawia pustego okna (w wersji instalowanej nie ma odświeżania F5). */
export default class ErrorBoundary extends Component<{ children: ReactNode; resetKey: string }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Błąd widoku", error, info.componentStack);
  }

  componentDidUpdate(prev: { resetKey: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="page error-screen">
        <div className="card stack">
          <h2>Ten widok się nie wyświetlił</h2>
          <p className="muted">Twoje plany i ustawienia są bezpieczne — to tylko błąd wyświetlania tej strony.</p>
          <div className="alert error small">{this.state.error.message}</div>
          <div className="row">
            <button className="btn primary" onClick={() => { window.location.hash = "/start"; this.setState({ error: null }); }}>Wróć do pulpitu</button>
            <button className="btn" onClick={() => window.location.reload()}>Wczytaj ponownie</button>
          </div>
        </div>
      </div>
    );
  }
}
