export function WorkspaceHydrationFallback() {
  return (
    <div className="workspace-bootstrap" role="status" aria-live="polite">
      <strong>正在启动研究工作区</strong>
      <span>正在载入当前研究任务…</span>
    </div>
  );
}
