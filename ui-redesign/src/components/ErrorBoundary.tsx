import { Component, type ErrorInfo, type ReactNode } from 'react';

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/* 页面级错误边界：任何页面崩溃只影响该页面，不再白屏整站 */

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('[ErrorBoundary]', error, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div className="page page-enter">
          <div className="card workspace-error-panel">
            <span className="eyebrow">页面渲染出错</span>
            <h2 className="display-title">这个页面遇到问题了</h2>
            <p className="artifacts__empty">{this.state.error.message}</p>
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => this.setState({ error: null })}
            >
              重试渲染
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
