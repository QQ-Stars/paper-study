import { Component, type ErrorInfo, type ReactNode } from 'react';

interface WorkspaceSlotBoundaryProps {
  readonly children: ReactNode;
  readonly label: string;
}

interface WorkspaceSlotBoundaryState {
  readonly failed: boolean;
}

export class WorkspaceSlotBoundary extends Component<
  WorkspaceSlotBoundaryProps,
  WorkspaceSlotBoundaryState
> {
  state: WorkspaceSlotBoundaryState = { failed: false };

  static getDerivedStateFromError(): WorkspaceSlotBoundaryState {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error(`Workspace ${this.props.label} failed`, error, info);
  }

  render() {
    if (this.state.failed) {
      return (
        <div className="workspace-slot-error" role="alert">
          <strong>{this.props.label}暂时不可用</strong>
          <span>主工作区仍可继续使用；切换页面后可重新加载此区域。</span>
        </div>
      );
    }

    return this.props.children;
  }
}
