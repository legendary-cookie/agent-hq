import { useEffect, useState } from 'react';
import { Sidebar } from '../components/Sidebar';
import { useApi } from '../api/client';
import { useRoute } from './router';
import { TokenHelp } from './TokenHelp';
export function App() { const [route, navigate] = useRoute(); const [forbidden, setForbidden] = useState(false); const { error } = useApi('/api/overview'); useEffect(() => { const handler = () => setForbidden(true); window.addEventListener('hq-forbidden', handler); return () => window.removeEventListener('hq-forbidden', handler); }, []); if (forbidden || error?.status === 403) return <TokenHelp/>; return <div className="shell"><Sidebar route={route} navigate={navigate}/><main><h1>{route[0].toUpperCase()+route.slice(1)}</h1></main></div>; }
