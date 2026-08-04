import { render, screen } from '@testing-library/react';

import { App } from './App';

it('renders the Paper Study application landmark', () => {
  render(<App />);

  expect(
    screen.getByRole('application', { name: 'Paper Study 研究工作区' }),
  ).toBeInTheDocument();
});
