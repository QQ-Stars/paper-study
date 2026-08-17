import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './app/App';
import './styles/tailwind.css';
import '@cloudflare/kumo/styles';
import './styles/tokens.css';
import './styles/reset.css';
import './styles/global.css';
import './styles/materials.css';
import './styles/motion.css';

const rootElement = document.getElementById('root');

if (!rootElement) {
  throw new Error('Missing #root application mount point.');
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
