/** Last-resort render guard.
 *
 * Before this existed, a single view that threw while rendering (an unexpected
 * old-task payload shape, a third-party library edge case) unmounted the whole
 * React tree: the researcher was left with a blank page that looked exactly
 * like "opening a history session does nothing". The boundary turns that into
 * a visible, recoverable panel -- no task data is touched, and the researcher
 * can retry the render or reload the page.
 */
import { Component, type ErrorInfo, type ReactNode } from "react";

import { t } from "../i18n";
import "./ErrorBoundary.css";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Keep the diagnostic in the console where the researcher (and we) can
    // read the component stack; never surface a private chain of thought or
    // request bodies here.
    console.error("[Poliscope] view render failed:", error, info.componentStack);
  }

  private readonly retryRender = () => {
    this.setState({ error: null });
  };

  private readonly reloadPage = () => {
    window.location.reload();
  };

  render() {
    const { error } = this.state;
    if (error === null) return this.props.children;
    return (
      <div className="error-boundary" role="alert">
        <strong className="error-boundary__title">
          {t("这个视图在渲染时出错了")}
        </strong>
        <pre className="error-boundary__message">
          {error.message ? String(error.message) : String(error)}
        </pre>
        <p className="error-boundary__hint">
          {t(
            "任务数据没有丢失。可以先尝试重新渲染，或刷新页面；若反复出现，请切换到其他标签页并把这段错误反馈给开发者。",
          )}
        </p>
        <div className="error-boundary__actions">
          <button
            type="button"
            className="error-boundary__button"
            onClick={this.retryRender}
          >
            {t("重新渲染")}
          </button>
          <button
            type="button"
            className="error-boundary__button error-boundary__button--ghost"
            onClick={this.reloadPage}
          >
            {t("刷新页面")}
          </button>
        </div>
      </div>
    );
  }
}
