import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import App from './App';
import './styles/tokens.css';
import './styles/base.css';
import './styles/shell.css';
import './styles/pages.css';
import './styles/pages2.css';
import './styles/pages3.css';
import './styles/pages4.css';
import './styles/reader.css';

const rootElement = document.getElementById('root');

if (!rootElement) {
  throw new Error('Missing #root application mount point.');
}

const root = createRoot(rootElement);
const prototypeVariant = import.meta.env.DEV
  ? new URLSearchParams(window.location.search).get('repro-prototype')
  : null;

if (prototypeVariant === 'a' || prototypeVariant === 'b' || prototypeVariant === 'c') {
  void import('./prototype/ReproductionPrototype').then(({ ReproductionPrototype }) => {
    root.render(
      <StrictMode>
        <ReproductionPrototype initialVariant={prototypeVariant} />
      </StrictMode>,
    );
  });
} else {
  root.render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
