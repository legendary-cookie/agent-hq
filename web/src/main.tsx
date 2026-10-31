import React from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './app/App';
import { bootstrapToken } from './app/token';
import './theme.css';
bootstrapToken(location, sessionStorage);
createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
