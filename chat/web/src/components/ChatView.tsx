import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Activity, AlertTriangle, AudioLines, Check, CornerUpLeft, Mic, Copy, Hash, Lock, MessageSquare, Pencil, RotateCcw, Settings2, Square, Trash2, Download } from 'lucide-react'
import { api, type Conversation, type Delivery, type FileRef, type Message, type Task } from '../api'
import { authorName, can, clock, convAgents, convTitle, dayKey, dayLabel, fullTime, go, useStore } from '../store'
import { AgentAvatar, Empty, Markdown, sizeText, UserAvatar } from './bits'
import { Composer } from './Composer'
import { AgentPicker } from './AgentPicker'
import { ChannelPanel } from './ChannelPanel'
import { ActivityPanel, ExportMenu, useCanWatch } from './Activity'
import { TASK_COLUMNS } from './Tasks'

type Loaded = { messages: Message[]; deliveries: Delivery[]; more: boolean }

export function ChatView({ id }: { id: number }) {
  const { s, dispatch } = useStore()
  const c = s.convs[id]
  const [missing, setMissing] = useState('')
  const [panel, setPanel] = useState(false)
  const [watch, setWatch] = useState(false)
  const [tapped, setTapped] = useState(0)
  const [replyTo, setReplyTo] = useState<{ id: number; author: string; quote: string; part: boolean } | null>(null)
  useEffect(() => setReplyTo(null), [id])
  const messages = s.messages[id]
  const listRef = useRef<HTMLDivElement>(null)
  const stick = useRef(true)
  const loadingOlder = useRef(false)

  useEffect(() => {
    api.get<Loaded>(`/api/conversations/${id}/messages`).then((r) => dispatch({ t: 'loaded', conv: id, ...r }))
      .catch((e) => setMissing(e.message))
    if (!c) api.get<Conversation>(`/api/conversations/${id}`).then((x) => dispatch({ t: 'conv', c: x })).catch(() => {})
  }, [id])

  // Mark as read whenever the newest message changes while this chat is open.
  const lastId = messages?.length ? messages[messages.length - 1].id : 0
  useEffect(() => {
    if (!lastId || !c?.joined) return
    if ((c.last_read_id || 0) >= lastId && !c.unread) return
    dispatch({ t: 'read', conv: id, last: lastId })
    api.post(`/api/conversations/${id}/read`, { last_id: lastId }).catch(() => {})
  }, [lastId, c?.joined])

  const active = s.deliveries[id] || []
  const failed = (s.failed[id] || []).filter((d) => !messages?.some((m) => m.delivery_id === d.id && m.status === 'done'))
  const streamingText = messages?.filter((m) => m.status === 'streaming').map((m) => m.text.length).join(',')

  useLayoutEffect(() => {
    const el = listRef.current
    if (el && stick.current) el.scrollTop = el.scrollHeight
  }, [messages?.length, streamingText, active.length])

  const onScroll = async () => {
    const el = listRef.current
    if (!el) return
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    if (el.scrollTop < 60 && s.more[id] && !loadingOlder.current && messages?.length) {
      loadingOlder.current = true
      const height = el.scrollHeight
      try {
        const r = await api.get<Loaded>(`/api/conversations/${id}/messages?before=${messages[0].id}`)
        dispatch({ t: 'loaded', conv: id, ...r, older: true })
        requestAnimationFrame(() => { el.scrollTop = el.scrollHeight - height })
      } finally { loadingOlder.current = false }
    }
  }

  const send = useCallback(async (text: string, files: FileRef[]) => {
    stick.current = true
    const reply = replyTo
    const m = await api.post<Message>(`/api/conversations/${id}/messages`, { text, files: files.map((f) => f.id),
      ...(reply ? { reply_to: reply.id, reply_quote: reply.part ? reply.quote : '' } : {}) })
    setReplyTo(null)
    dispatch({ t: 'message', m })
  }, [id, replyTo])

  // Highlight text in a message to reply to just that part.
  const [pick, setPick] = useState<{ id: number; author: string; quote: string; x: number; y: number } | null>(null)
  useEffect(() => {
    const onUp = () => setTimeout(() => {
      const sel = window.getSelection()
      const text = sel?.toString().trim() || ''
      if (!sel || !text || sel.rangeCount === 0) { setPick(null); return }
      const node = sel.anchorNode instanceof Element ? sel.anchorNode : sel.anchorNode?.parentElement
      const row = node?.closest('[data-mid]') as HTMLElement | null
      if (!row || !listRef.current?.contains(row)) { setPick(null); return }
      const rect = sel.getRangeAt(0).getBoundingClientRect()
      const box = listRef.current.getBoundingClientRect()
      setPick({ id: Number(row.dataset.mid), author: row.dataset.author || '', quote: text.slice(0, 1000),
        x: Math.min(Math.max(rect.left + rect.width / 2 - box.left, 40), box.width - 40), y: rect.top - box.top + listRef.current.scrollTop - 6 })
    }, 0)
    document.addEventListener('mouseup', onUp)
    document.addEventListener('keyup', onUp)
    return () => { document.removeEventListener('mouseup', onUp); document.removeEventListener('keyup', onUp) }
  }, [id])
  const jumpTo = async (mid: number) => {
    const find = () => listRef.current?.querySelector(`[data-mid="${mid}"]`) as HTMLElement | null
    for (let tries = 0; !find() && tries < 20; tries++) {  // load older pages until it's there
      const first = (s.messages[id] || [])[0]
      if (!first || !s.more[id]) break
      const r = await api.get<{ messages: Message[]; deliveries: never[]; more: boolean }>(`/api/conversations/${id}/messages?before=${first.id}&limit=200`)
      dispatch({ t: 'loaded', conv: id, ...r, older: true })
      await new Promise((ok) => setTimeout(ok, 60))
    }
    const el = find()
    if (!el) return
    stick.current = false
    el.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
    el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash')
  }
  const startReply = (m: Message, quote?: string) => {
    const full = m.text.replace(/\s+/g, ' ').trim()
    setReplyTo({ id: m.id, author: authorName(m, s), quote: quote || (full.length > 160 ? full.slice(0, 160) + '…' : full) || 'Attachment', part: !!quote })
    setPick(null)
    window.getSelection()?.removeAllRanges()
  }

  const agents = c ? convAgents(c, s) : []
  // The agent whose live activity the Live button shows: the chat's agent, a channel's lead, a task's agent.
  const leadId = c ? (c.kind === 'dm' || c.kind === 'telegram' ? agents[0]?.id : c.kind === 'channel' ? c.lead_agent
    : Object.values(s.tasks).find((t) => t.conv_id === c.id)?.assignee) : undefined
  const leadAgent = leadId ? s.agents[leadId] : undefined
  const watchAgent = useCanWatch(leadAgent) ? leadAgent : undefined
  const mentionable = useMemo(() => {
    if (!c || c.kind === 'dm') return []
    const inside = new Set(agents.map((a) => a.id))
    const rest = c.kind === 'channel' ? Object.values(s.agents).filter((a) => !inside.has(a.id) && !a.disabled) : []
    return [...agents, ...rest]
  }, [c, s.agents])

  if (missing && !c) return <Empty icon={<MessageSquare size={32} />} title="Can't open this chat">{missing}</Empty>
  if (!c) return <div className="chat"><div className="chat-loading">Loading…</div></div>

  const task = c.kind === 'task' ? Object.values(s.tasks).find((t) => t.conv_id === c.id) : undefined
  const isDm = c.kind === 'dm'
  const isTg = c.kind === 'telegram'
  // Who answers spoken messages here: the chat's agent, a channel's lead, or a task's agent.
  const talkAgent = isTg ? undefined : isDm ? agents[0] : c.kind === 'channel' ? (c.lead_agent ? s.agents[c.lead_agent] : undefined)
    : task?.assignee ? s.agents[task.assignee] : undefined
  const dmAgent = isDm ? agents[0] : undefined
  const placeholder = isDm ? `Message ${dmAgent?.name || 'agent'}…` : c.kind === 'task' ? 'Add a comment or instructions…'
    : c.reply_mode === 'mention' ? `Message #${c.name}. @mention an agent to get an answer`
    : c.reply_mode === 'all' ? `Message #${c.name}. Every agent here answers`
    : `Message #${c.name}. ${c.lead_agent && s.agents[c.lead_agent] ? s.agents[c.lead_agent].name + ' answers unless you @mention someone' : '@mention an agent'}`

  const groups = groupMessages(messages || [])

  return (
    <div className={'chat' + (isDm ? ' dm' : ' room')}>
      <header className="chat-head">
        <div className="chat-title">
          {c.kind === 'channel' && (c.private ? <Lock size={17} /> : <Hash size={17} />)}
          {isDm && dmAgent && <AgentAvatar agent={dmAgent} size={26} dot />}
          <div className="chat-title-text">
            <h2>{convTitle(c, s)}</h2>
            {c.kind === 'channel' && <small className="muted">{c.topic || `${agents.length} agents · ${c.members.filter((m) => m.type === 'user').length} people`}</small>}
            {isDm && dmAgent && <small className="muted">{dmAgent.online ? 'Online' : 'Offline'} · @{dmAgent.handle}</small>}
            {isTg && agents[0] && <small className="muted">Copied from {agents[0].name}'s Telegram · read-only</small>}
            {task && <small className="muted">Task #{task.id}{task.assignee ? ' · ' + (s.agents[task.assignee]?.name || '') : ''}</small>}
          </div>
        </div>
        <div className="chat-actions">
          {talkAgent && can(s, 'voice.use') && !c.archived && (c.kind !== 'channel' || c.joined) && <button className="btn talk-open" onClick={() => go(`/talk/c/${c.id}`)}
            title={`Talk to ${talkAgent.name} out loud in this ${c.kind === 'channel' ? 'channel' : c.kind === 'task' ? 'task' : 'chat'}`}>
            <AudioLines size={16} /> <span>Talk</span></button>}
          {isDm && <AgentPicker agents={Object.values(s.agents)} value={dmAgent?.id} align="right" label="Switch agent"
            onChange={(agent) => api.patch(`/api/conversations/${id}`, { agent }).catch((e) => alert(e.message))} />}
          {c.kind === 'channel' && <div className="avatar-stack" onClick={() => setPanel(true)}>{agents.slice(0, 5).map((a) => <AgentAvatar key={a.id} agent={a} size={26} />)}</div>}
          {task && <TaskStatus task={task} />}
          {watchAgent && <button className={'btn live-btn' + (watch ? ' on' : '')} onClick={() => { setWatch(!watch); setPanel(false) }}
            title={`See what ${watchAgent.name} is doing right now`} aria-pressed={watch}><Activity size={16} /> <span>Live</span></button>}
          {can(s, 'chats.export') && <ExportMenu convId={c.id} />}
          <button className="icon-btn" onClick={() => { setPanel(!panel); setWatch(false) }} title="Details" aria-label="Details"><Settings2 size={18} /></button>
        </div>
      </header>

      <div className="chat-body">
        <div className={'messages' + (tapped ? ' has-tap' : '')} ref={listRef} onScroll={onScroll} onClick={(e) => {
          // Phones: tap a message to show its Reply / Copy buttons.
          if (!matchMedia('(hover: none)').matches || (e.target as HTMLElement).closest('.msg-actions')) return
          const row = (e.target as HTMLElement).closest('[data-mid]') as HTMLElement | null
          const mid = row ? Number(row.dataset.mid) : 0
          setTapped(mid && mid !== tapped ? mid : 0)
        }}>
          {tapped > 0 && <style>{`[data-mid="${tapped}"] .msg-actions { opacity: 1 }`}</style>}
          <div className="messages-inner">
            {messages && !messages.length && <ChatIntro c={c} />}
            {s.more[id] && <div className="muted center small pad">Scroll up for older messages</div>}
            {groups.map((g, i) => <div key={g[0].id}>
              {(i === 0 || dayKey(groups[i - 1][0].created) !== dayKey(g[0].created)) && <div className="day-divider" role="separator"><span>{dayLabel(g[0].created)}</span></div>}
              <MessageGroup msgs={g} dm={isDm} onReply={isTg || c.archived ? undefined : startReply} onJump={jumpTo} />
            </div>)}
            {pick && !isTg && !c.archived && <button className="quote-pop" style={{ left: pick.x, top: pick.y }} onMouseDown={(e) => e.preventDefault()}
              onClick={() => { const m = (messages || []).find((x) => x.id === pick.id); if (m) startReply(m, pick.quote) }}><CornerUpLeft size={14} /> Reply</button>}
            {active.map((d) => <Thinking key={d.id} d={d} />)}
            {failed.map((d) => <Failed key={d.id} d={d} />)}
          </div>
        </div>
        {panel && <ChannelPanel c={c} task={task} onClose={() => setPanel(false)} />}
        {watch && watchAgent && <ActivityPanel key={watchAgent.id} agent={watchAgent} onClose={() => setWatch(false)} />}
      </div>

      <footer className="chat-foot">
        {c.kind === 'channel' && !c.joined ? (
          <div className="join-bar">You are viewing #{c.name}.
            <button className="btn primary" onClick={() => api.post<Conversation>(`/api/conversations/${id}/members`, { users: [s.me.id] }).then((x) => dispatch({ t: 'conv', c: { ...x, joined: true } }))}>Join channel</button>
          </div>
        ) : isTg ? (
          <div className="join-bar">This is {agents[0]?.name || 'the agent'}'s Telegram history, kept up to date. Reply on Telegram, or
            {agents[0] && <button className="btn primary" onClick={() => api.post<Conversation>('/api/conversations', { kind: 'dm', agent_id: agents[0].id })
              .then((x) => { dispatch({ t: 'conv', c: { ...x, joined: true } }); go(`/c/${x.id}`) }).catch((e) => alert(e.message))}>Chat with {agents[0].name} here</button>}
          </div>
        ) : c.archived ? <div className="join-bar">This conversation is archived.</div> : (
          <Composer key={id} onSend={send} mentionable={mentionable} placeholder={placeholder} autoFocus draftKey={'c' + id}
            replyTo={replyTo} onCancelReply={() => setReplyTo(null)}
            hint={!isDm && c.kind === 'channel' ? <span>Type @ to bring in an agent</span> : null} />
        )}
      </footer>
    </div>
  )
}

function ChatIntro({ c }: { c: Conversation }) {
  const { s } = useStore()
  const agents = convAgents(c, s)
  if (c.kind === 'dm' && agents[0]) {
    const a = agents[0]
    return (
      <div className="intro">
        <AgentAvatar agent={a} size={56} dot />
        <h3>{a.name}</h3>
        <p className="muted">{a.title || a.grp}{a.owner_name ? ` · ${a.owner_name}'s agent` : ''}</p>
        {!a.online && <p className="warn-text">This agent is offline right now. Your message waits until it is back.</p>}
      </div>
    )
  }
  if (c.kind === 'telegram') return <div className="intro"><h3>{c.name}</h3><p className="muted">Messages appear here as they happen on Telegram.</p></div>
  return <div className="intro"><h3>{c.kind === 'channel' ? `#${c.name}` : c.name}</h3><p className="muted">{c.topic || 'This is the start of the conversation.'}</p></div>
}

function groupMessages(msgs: Message[]) {
  const out: Message[][] = []
  for (const m of msgs) {
    const last = out[out.length - 1]
    const prev = last?.[last.length - 1]
    if (prev && m.author_type !== 'system' && prev.author_type === m.author_type && prev.author_id === m.author_id && m.created - prev.created < 300
        && dayKey(prev.created) === dayKey(m.created) && !m.reply) last.push(m)
    else out.push([m])
  }
  return out
}

type ReplyFn = ((m: Message, quote?: string) => void) | undefined
type JumpFn = (id: number) => void

function MessageGroup({ msgs, dm, onReply, onJump }: { msgs: Message[]; dm: boolean; onReply: ReplyFn; onJump: JumpFn }) {
  const { s } = useStore()
  const first = msgs[0]
  if (first.author_type === 'system') return <div className="system-msg">{first.text} <span>{clock(first.created)}</span></div>
  const mine = first.author_type === 'user' && first.author_id === String(s.me.id)
  const agent = first.author_type === 'agent' ? s.agents[first.author_id] : undefined
  if (dm && mine) {
    return <div className="group mine">{msgs.map((m) => <div key={m.id} className="bubble-row" data-mid={m.id} data-author={authorName(m, s)}>
      <MessageActions m={m} onReply={onReply} /><div className="bubble"><Body m={m} onJump={onJump} /><span className="msg-time" title={fullTime(m.created)}>{clock(m.created)}</span></div></div>)}</div>
  }
  return (
    <div className={'group' + (agent ? ' from-agent' : '')}>
      <div className="group-avatar">{agent ? <AgentAvatar agent={agent} size={32} /> : first.author_type === 'external'
        ? <span className="avatar tg-avatar" style={{ width: 32, height: 32 }}>{authorName(first, s).slice(0, 2).toUpperCase()}</span>
        : <UserAvatar user={s.users[Number(first.author_id)]} size={32} />}</div>
      <div className="group-main">
        <div className="group-head"><b>{authorName(first, s)}</b>{agent && <span className="tag">{agent.grp === 'Department' ? 'Dept' : 'Agent'}</span>}<span className="muted small" title={fullTime(first.created)}>{clock(first.created)}</span></div>
        {msgs.map((m, i) => <div key={m.id} className="msg-row" data-mid={m.id} data-author={authorName(m, s)}>
          {i > 0 && <span className="msg-time side" title={fullTime(m.created)}>{clock(m.created)}</span>}
          <Body m={m} onJump={onJump} /><MessageActions m={m} onReply={onReply} /></div>)}
      </div>
    </div>
  )
}

function Body({ m, onJump }: { m: Message; onJump?: JumpFn }) {
  return (
    <div className={'msg-body' + (m.status === 'streaming' ? ' streaming' : '') + (m.status === 'error' ? ' errored' : '')}>
      {m.reply && <button className="reply-quote" onClick={() => onJump?.(m.reply!.id)} title={m.reply.gone ? 'That message was deleted' : 'Go to the message'} disabled={m.reply.gone}>
        <b>{m.reply.author || 'Deleted message'}</b><span>{m.reply.quote}</span></button>}
      {m.text && <Markdown text={m.text} />}
      {m.files.length > 0 && <div className="files">{m.files.map((f) => <FileCard key={f.id} f={f} />)}</div>}
      {m.edited && <span className="muted small"> (edited)</span>}
      {m.spoken && <span className="spoken-mark" title="Said out loud (talk mode or Siri)"><Mic size={11} /> spoken</span>}
      {m.status === 'streaming' && <span className="cursor" />}
    </div>
  )
}

function FileCard({ f }: { f: FileRef }) {
  const url = `/api/files/${f.id}`
  if (f.mime.startsWith('image/') && f.mime !== 'image/svg+xml') return <a href={url} target="_blank" rel="noreferrer"><img className="file-img" src={url} alt={f.name} /></a>
  return <a className="file-card" href={url}><Download size={16} /><span>{f.name}<small>{sizeText(f.size)}</small></span></a>
}

function MessageActions({ m, onReply }: { m: Message; onReply?: ReplyFn }) {
  const { s } = useStore()
  const [copied, setCopied] = useState(false)
  const mine = m.author_type === 'user' && m.author_id === String(s.me.id)
  const copy = () => { navigator.clipboard.writeText(m.text); setCopied(true); setTimeout(() => setCopied(false), 1200) }
  const edit = async () => {
    const text = prompt('Edit message', m.text)
    if (text && text !== m.text) await api.patch(`/api/messages/${m.id}`, { text }).catch((e) => alert(e.message))
  }
  const del = async () => { if (confirm('Delete this message?')) await api.del(`/api/messages/${m.id}`).catch((e) => alert(e.message)) }
  const retry = () => api.post(`/api/messages/${m.id}/retry`).catch((e) => alert(e.message))
  return (
    <div className="msg-actions">
      {onReply && m.author_type !== 'system' && <button onClick={() => onReply(m)} title="Reply"><CornerUpLeft size={14} /></button>}
      <button onClick={copy} title="Copy">{copied ? <Check size={14} /> : <Copy size={14} />}</button>
      {(mine || (m.author_type === 'agent' && m.delivery_id)) && <button onClick={retry} title={mine ? 'Ask again' : 'Regenerate'}><RotateCcw size={14} /></button>}
      {mine && <button onClick={edit} title="Edit"><Pencil size={14} /></button>}
      {(mine || s.me.role === 'admin') && <button onClick={del} title="Delete"><Trash2 size={14} /></button>}
    </div>
  )
}

function Thinking({ d }: { d: Delivery }) {
  const { s } = useStore()
  const a = s.agents[d.agent_id]
  const streaming = s.messages[d.conv_id]?.some((m) => m.delivery_id === d.id && m.status === 'streaming')
  if (streaming) return null
  const what = d.state === 'queued' ? (a?.online ? 'Sending to' : 'Waiting for') : d.state === 'sent' ? 'Delivered to' : ''
  return (
    <div className="thinking">
      <AgentAvatar agent={a} size={26} />
      <span>{what ? <>{what} <b>{a?.name}</b>{!a?.online && d.state === 'queued' ? ' (offline)' : ''}</> : <><b>{a?.name}</b> is working</>}</span>
      <span className="dots"><i /><i /><i /></span>
      <button className="link-btn" onClick={() => api.post(`/api/deliveries/${d.id}/cancel`)} title="Stop"><Square size={12} /> Stop</button>
    </div>
  )
}

function Failed({ d }: { d: Delivery }) {
  const { s } = useStore()
  const a = s.agents[d.agent_id]
  return (
    <div className="failed">
      <AlertTriangle size={16} />
      <span><b>{a?.name}</b> couldn't answer: {d.error || 'unknown error'}</span>
      <button className="link-btn" onClick={() => api.post(`/api/deliveries/${d.id}/retry`)}><RotateCcw size={13} /> Try again</button>
    </div>
  )
}

function TaskStatus({ task }: { task: Task }) {
  return (
    <select className={'status-select st-' + task.status} value={task.status}
      onChange={(e) => api.patch(`/api/tasks/${task.id}`, { status: e.target.value }).catch((err) => alert(err.message))}>
      {TASK_COLUMNS.map((col) => <option key={col.id} value={col.id}>{col.title}</option>)}
    </select>
  )
}

export function openChatWith(agentId: string, dispatch: ReturnType<typeof useStore>['dispatch']) {
  return api.post<Conversation>('/api/conversations', { kind: 'dm', agent_id: agentId }).then((c) => { dispatch({ t: 'conv', c }); go(`/c/${c.id}`) })
}
