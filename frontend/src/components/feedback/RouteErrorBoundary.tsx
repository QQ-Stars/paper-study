import { isRouteErrorResponse, useNavigate, useRouteError } from 'react-router-dom';

function describeRouteError(error: unknown): string {
  if (isRouteErrorResponse(error)) {
    return error.status === 404
      ? '没有找到这个工作区页面。'
      : `页面请求失败（${error.status}）。`;
  }

  if (error instanceof Error && error.message.trim()) {
    return error.message;
  }

  return '页面暂时无法载入。你的其他研究数据不受影响。';
}

export function RouteErrorBoundary() {
  const error = useRouteError();
  const navigate = useNavigate();

  return (
    <section className="route-error" role="alert" aria-labelledby="route-error-title">
      <p className="route-error__label">当前页面已隔离</p>
      <h2 id="route-error-title">无法打开此视图</h2>
      <p>{describeRouteError(error)}</p>
      <button type="button" onClick={() => void navigate(0)}>
        重试此页面
      </button>
    </section>
  );
}
