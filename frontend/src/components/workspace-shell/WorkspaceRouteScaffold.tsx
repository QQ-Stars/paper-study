interface WorkspaceRouteScaffoldProps {
  description: string;
  detail?: string;
}

export function WorkspaceRouteScaffold({
  description,
  detail,
}: WorkspaceRouteScaffoldProps) {
  return (
    <section className="workspace-route-scaffold" aria-label={description}>
      <p>{description}</p>
      {detail ? <strong>{detail}</strong> : null}
    </section>
  );
}

export function WorkspaceNotFoundRoute() {
  return (
    <section className="workspace-route-scaffold" role="alert">
      <p>没有找到这个工作区页面。</p>
    </section>
  );
}
