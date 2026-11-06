import React from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './app/App';
import { bootstrapToken, keepSignIn } from './app/token';
import './theme.css';
keepSignIn(sessionStorage, localStorage);
void Promise.resolve(bootstrapToken(location, localStorage)).then(() => {
  createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
});
