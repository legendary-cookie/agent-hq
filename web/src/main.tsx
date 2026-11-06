import React from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource/geist-sans/400.css';
import '@fontsource/geist-sans/500.css';
import '@fontsource/geist-sans/600.css';
import '@fontsource/geist-mono/500.css';
import { App } from './app/App';
import { applySavedTheme } from './app/theme';
import { bootstrapToken, keepSignIn } from './app/token';
import './theme.css';
applySavedTheme();
keepSignIn(sessionStorage, localStorage);
void Promise.resolve(bootstrapToken(location, localStorage)).then(() => {
  createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
});
