import { useEffect, useRef, useState } from 'react';
import { Terminal as XTerm } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';

const TITLE = { watch: 'Watching', control: 'In control of', root: 'Root shell in' } as const;

export function terminalUrl(host: string, mode: string, token: string, location: { protocol: string; host: string }) {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  return `${scheme}://${location.host}/api/terminal?host=${encodeURIComponent(host)}&mode=${mode}&token=${encodeURIComponent(token)}`;
}

/** A live terminal over HQ's WebSocket. Watch mode is read-only on both ends. */
export function Terminal({ host, label, mode, onClose }: { host: string; label: string; mode: 'watch' | 'control' | 'root'; onClose: () => void }) {
  const box = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<'connecting' | 'open' | 'closed'>('connecting');
  useEffect(() => {
    if (!box.current) return;
    const term = new XTerm({ fontFamily: 'Geist Mono, ui-monospace, Consolas, monospace', fontSize: 13, cursorBlink: mode !== 'watch', disableStdin: mode === 'watch', theme: { background: '#0b0b0c' }, scrollback: 5000 });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(box.current);
    fit.fit();
    const socket = new WebSocket(terminalUrl(host, mode, localStorage.getItem('hq-token') ?? '', location));
    socket.binaryType = 'arraybuffer';
    const send = (message: object) => { if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message)); };
    socket.onopen = () => { setState('open'); send({ type: 'resize', cols: term.cols, rows: term.rows }); term.focus(); };
    socket.onmessage = (event) => term.write(new Uint8Array(event.data as ArrayBuffer));
    socket.onclose = () => { setState('closed'); term.write('\r\n\x1b[2m[disconnected]\x1b[0m\r\n'); };
    const typed = term.onData((data) => { if (mode !== 'watch') send({ type: 'input', data }); });
    const resized = term.onResize(({ cols, rows }) => send({ type: 'resize', cols, rows }));
    const onWindow = () => fit.fit();
    window.addEventListener('resize', onWindow);
    return () => { window.removeEventListener('resize', onWindow); typed.dispose(); resized.dispose(); socket.close(); term.dispose(); };
  }, [host, mode]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape' && mode === 'watch') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [mode, onClose]);
  return (
    <div className="terminal-scrim" role="dialog" aria-label={`${TITLE[mode]} ${label}`}>
      <div className="terminal-frame">
        <div className="terminal-head">
          <b>{TITLE[mode]} {label}</b>
          <span className={`terminal-state ${state}`}>{state === 'open' ? (mode === 'watch' ? 'Live, read-only' : 'Live, your keys go to the agent') : state === 'connecting' ? 'Connecting…' : 'Disconnected'}</span>
          {mode === 'control' && <span className="muted">Leaving doesn’t stop the agent: closing this window just detaches.</span>}
          <button type="button" className="plain-button" onClick={onClose}>Close</button>
        </div>
        <div className="terminal-body" ref={box} />
      </div>
    </div>
  );
}
