import { render, screen } from '@testing-library/react';

import { App } from './App';
import { createWorkspaceMemoryRouter } from './router';

it('renders the Paper Study application landmark', async () => {
  render(<App router={createWorkspaceMemoryRouter(['/reviews'])} />);

  expect(
    await screen.findByRole('application', {
      name: 'Paper Study 研究工作区',
    }),
  ).toBeInTheDocument();
});
