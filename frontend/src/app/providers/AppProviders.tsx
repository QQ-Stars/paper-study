import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';

import { createWorkspaceQueryClient } from './queryClient';

interface AppProvidersProps {
  children: ReactNode;
  queryClient?: QueryClient;
}

export function AppProviders({ children, queryClient }: AppProvidersProps) {
  const [ownedQueryClient] = useState(createWorkspaceQueryClient);

  return (
    <QueryClientProvider client={queryClient ?? ownedQueryClient}>
      {children}
    </QueryClientProvider>
  );
}
