import { RouterProvider, type DataRouter } from 'react-router-dom';

import { AppProviders } from './providers/AppProviders';
import { createWorkspaceBrowserRouter } from './router';
import type { QueryClient } from '@tanstack/react-query';

let browserRouter: DataRouter | undefined;

function getBrowserRouter(): DataRouter {
  browserRouter ??= createWorkspaceBrowserRouter();
  return browserRouter;
}

interface AppProps {
  router?: DataRouter;
  queryClient?: QueryClient;
}

export function App({ router, queryClient }: AppProps) {
  return (
    <AppProviders queryClient={queryClient}>
      <RouterProvider router={router ?? getBrowserRouter()} />
    </AppProviders>
  );
}
