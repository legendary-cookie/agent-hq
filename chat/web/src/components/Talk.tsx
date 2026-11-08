import { useCallback, useEffect, useRef, useState } from 'react'
import { Mic, MicOff, X, MessageSquare } from 'lucide-react'
import { api, type Conversation, type Delivery, type Message } from '../api'
import { go, useStore } from '../store'
import { AgentAvatar } from './bits'

// Talk mode: a hands-free spoken conversation with one agent, like ChatGPT's voice mode.
// The browser listens (Web Speech API), sends what you said when you pause, and reads the agent's reply out loud
// sentence by sentence. Tap the orb while it speaks to cut in. Everything is also in the person's "Voice" chat.

type Phase = 'start' | 'listening' | 'thinking' | 'speaking' | 'paused' | 'unsupported'
type Loaded = { messages: Message[]; deliveries: Delivery[]; more: boolean }

const SILENCE_MS = 1300 // pause that ends what you're saying
const SpeechRec: any = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition

export function Talk({ agentId, convId }: { agentId?: string; convId?: number }) {
  const { s, dispatch } = useStore()
  const [conv, setConv] = useState<Conversation | null>(null)
  // Talking in a chat: the agent who answers there (the chat's agent, or a channel's lead). Else the one named in the link.
  const convAgent = conv ? (conv.lead_agent || conv.members.find((m) => m.type === 'agent')?.id) : undefined
  const agent = convId !== undefined
    ? (convAgent ? s.agents[convAgent] : undefined)
    : (s.agents[agentId || ''] || Object.values(s.agents).find((a) => a.handle === agentId))
  const where = conv && convId !== undefined ? (conv.kind === 'channel' ? '#' + conv.name : conv.name || 'this chat') : ''
  const [error, setError] = useState('')
  const [phase, setPhaseState] = useState<Phase>(SpeechRec ? 'start' : 'unsupported')
  const [heard, setHeard] = useState('')
  const [said, setSaid] = useState('')
  const [reply, setReply] = useState('')

  const phaseRef = useRef<Phase>(phase)
  const setPhase = (p: Phase) => { phaseRef.current = p; setPhaseState(p) }
  const rec = useRef<any>(null)
  const finalText = useRef('')
  const silence = useRef<number | undefined>(undefined)
  const trigger = useRef(0)        // id of the message we're waiting on
  const spokenUpTo = useRef<Record<number, number>>({})
  const speaking = useRef(0)       // utterances queued
  const audio = useRef<AudioContext | null>(null)
  const wake = useRef<any>(null)

  // ------------------------------------------------------------ the voice chat
  useEffect(() => {
    if (convId === undefined && !agent) return
    api.post<Conversation>('/api/voice/conversation', convId !== undefined ? { conversation: convId } : { agent: agent!.id }).then((c) => {
      dispatch({ t: 'conv', c })
      setConv(c)
      return api.get<Loaded>(`/api/conversations/${c.id}/messages`).then((r) => dispatch({ t: 'loaded', conv: c.id, ...r }))
    }).catch((e) => setError(e.message))
  }, [convId, convId === undefined ? agent?.id : undefined])

  // ------------------------------------------------------------ speaking
  const voice = useRef<SpeechSynthesisVoice | null>(null)
  useEffect(() => {
    const pick = () => {
      const vs = speechSynthesis.getVoices()
      const lang = (navigator.language || 'en').slice(0, 2)
      const mine = vs.filter((v) => v.lang.startsWith(lang))
      voice.current = mine.find((v) => /premium|enhanced|siri/i.test(v.name)) || mine.find((v) => /samantha|daniel|google uk|google us|aria|jenny/i.test(v.name))
        || mine.find((v) => v.default) || mine[0] || null
    }
    pick()
    speechSynthesis.addEventListener?.('voiceschanged', pick)
    return () => speechSynthesis.removeEventListener?.('voiceschanged', pick)
  }, [])

  const say = (text: string) => {
    const t = toSpeech(text)
    if (!t) return
    const u = new SpeechSynthesisUtterance(t)
    if (voice.current) u.voice = voice.current
    u.rate = 1.05
    speaking.current += 1
    setPhase('speaking')
    const done = () => {
      speaking.current = Math.max(0, speaking.current - 1)
      afterRef.current()
    }
    u.onend = done
    u.onerror = done
    speechSynthesis.speak(u)
  }

  const chime = (up: boolean) => {
    const ctx = audio.current
    if (!ctx) return
    const o = ctx.createOscillator(), g = ctx.createGain()
    o.frequency.setValueAtTime(up ? 660 : 520, ctx.currentTime)
    o.frequency.exponentialRampToValueAtTime(up ? 880 : 390, ctx.currentTime + 0.14)
    g.gain.setValueAtTime(0.0001, ctx.currentTime)
    g.gain.exponentialRampToValueAtTime(0.08, ctx.currentTime + 0.02)
    g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.22)
    o.connect(g).connect(ctx.destination)
    o.start(); o.stop(ctx.currentTime + 0.25)
  }

  // ------------------------------------------------------------ listening
  const listen = useCallback(() => {
    if (!SpeechRec || phaseRef.current === 'paused') return
    try { rec.current?.abort() } catch { /* already stopped */ }
    finalText.current = ''
    setHeard('')
    const r = new SpeechRec()
    r.lang = navigator.language || 'en-US'
    r.continuous = true
    r.interimResults = true
    r.onresult = (e: any) => {
      let interim = ''
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const piece = e.results[i][0].transcript
        if (e.results[i].isFinal) finalText.current += piece + ' '
        else interim += piece
      }
      setHeard((finalText.current + interim).trim())
      clearTimeout(silence.current)
      silence.current = window.setTimeout(() => sendRef.current((finalText.current + interim).trim()), SILENCE_MS)
    }
    r.onerror = (e: any) => {
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
        setError('The microphone is blocked. Allow it for this site in the browser settings, then tap the orb.')
        setPhase('paused')
      }
    }
    r.onend = () => {
      // Browsers end recognition after a while, or on a pause; keep listening while we're meant to.
      if (rec.current === r && phaseRef.current === 'listening') setTimeout(() => phaseRef.current === 'listening' && listenRef.current(), 150)
    }
    rec.current = r
    setPhase('listening')
    try { r.start() } catch { /* starting twice */ }
  }, [])

  const listenRef = useRef(listen); listenRef.current = listen

  const stopListening = () => {
    clearTimeout(silence.current)
    const r = rec.current
    rec.current = null
    try { r?.abort() } catch { /* already stopped */ }
  }

  const send = async (text: string) => {
    if (!text || phaseRef.current !== 'listening' || !conv) return
    stopListening()
    setSaid(text); setHeard(''); setReply('')
    setPhase('thinking')
    chime(true)
    try {
      const m = await api.post<Message>(`/api/conversations/${conv.id}/messages`, { text, spoken: true })
      trigger.current = m.id
      dispatch({ t: 'message', m })
    } catch (e: any) {
      setError(e.message)
      say('Sorry, that did not send. ' + e.message)
    }
  }

  const sendRef = useRef(send); sendRef.current = send

  // ------------------------------------------------------------ the agent's reply
  const messages = conv ? s.messages[conv.id] || [] : []
  const active = conv ? (s.deliveries[conv.id] || []).filter((d) => d.trigger_id === trigger.current) : []
  const failed = conv ? (s.failed[conv.id] || []).find((d) => d.trigger_id === trigger.current) : undefined

  useEffect(() => {
    if (!trigger.current || phaseRef.current === 'paused') return
    const replies = messages.filter((m) => m.id > trigger.current && m.author_type === 'agent')
    if (replies.length && phaseRef.current === 'thinking') stopListening()
    for (const m of replies) {
      const from = spokenUpTo.current[m.id] || 0
      let upTo = m.text.length
      if (m.status === 'streaming') {
        const cut = lastSentenceEnd(m.text, from)
        if (cut <= from) continue
        upTo = cut
      }
      if (upTo > from) {
        spokenUpTo.current[m.id] = upTo
        say(m.text.slice(from, upTo))
      }
    }
    if (replies.length) setReply(replies.map((m) => m.text).join('\n\n'))
  }, [messages])

  useEffect(() => {
    if (failed && trigger.current && phaseRef.current === 'thinking') {
      trigger.current = 0
      say(`${agent?.name || 'The agent'} could not answer. ${failed.error || ''}`)
    }
  }, [failed?.id])

  // Back to listening once the agent is done and everything has been read out.
  const afterSpeech = () => {
    if (speaking.current > 0 || phaseRef.current === 'paused') return
    const still = conv && (s.deliveries[conv.id] || []).some((d) => d.trigger_id === trigger.current)
    if (still && trigger.current) { setPhase('thinking'); return }
    chime(false)
    listen()
  }
  const afterRef = useRef(afterSpeech); afterRef.current = afterSpeech
  useEffect(() => {
    // The last delivery finished while nothing was left to read: listen again.
    if (phaseRef.current === 'thinking' && trigger.current && !active.length && speaking.current === 0
        && messages.some((m) => m.id > trigger.current && m.author_type === 'agent' && m.status === 'done')) afterRef.current()
  }, [active.length, messages])

  // ------------------------------------------------------------ controls
  const start = () => {
    // iOS only allows sound and speech that start from a tap, so unlock both here.
    try {
      const Ctx = (window as any).AudioContext || (window as any).webkitAudioContext
      audio.current = audio.current || new Ctx()
      audio.current?.resume()
    } catch { /* no sound effects */ }
    speechSynthesis.cancel()
    speechSynthesis.speak(new SpeechSynthesisUtterance(''))
    ;(navigator as any).wakeLock?.request('screen').then((l: any) => { wake.current = l }).catch(() => {})
    setError('')
    setPhase('listening')
    listen()
  }

  const tapOrb = () => {
    const p = phaseRef.current
    if (p === 'start' || p === 'paused') return start()
    if (p === 'speaking' || p === 'thinking') {
      // Cut in: stop reading, stop waiting, listen.
      speechSynthesis.cancel()
      speaking.current = 0
      const cut = messages.filter((m) => m.id > trigger.current && m.author_type === 'agent')
      cut.forEach((m) => { spokenUpTo.current[m.id] = Number.MAX_SAFE_INTEGER })
      if (p === 'thinking') trigger.current = 0
      listen()
      return
    }
    if (p === 'listening') {
      const text = heard.trim()
      if (text) send(text)
    }
  }

  const pause = () => {
    stopListening()
    speechSynthesis.cancel()
    speaking.current = 0
    setPhase('paused')
  }

  const leave = () => {
    pause()
    wake.current?.release?.()
    if (conv) go(`/c/${conv.id}`)
    else go('/')
  }

  useEffect(() => () => {
    stopListening()
    speechSynthesis.cancel()
    wake.current?.release?.()
  }, [])

  if (error && !conv) return <div className="talk"><p className="talk-status">{error}</p><button className="talk-btn" onClick={() => history.back()}>Back</button></div>
  if (!agent) return <div className="talk"><p className="talk-status">{convId !== undefined ? (conv ? 'No agent answers in this chat.' : 'Loading…') : `No agent called ${agentId}.`}</p></div>

  const status = {
    start: `Tap to talk to ${agent.name}`,
    listening: heard ? 'Listening…' : `Go ahead, ${agent.name} is listening`,
    thinking: active.some((d) => d.state === 'working') ? `${agent.name} is working on it…` : `${agent.name} is thinking…`,
    speaking: `${agent.name} is speaking. Tap to cut in`,
    paused: 'Paused. Tap to carry on',
    unsupported: 'This browser cannot listen. Use Safari on iPhone, or Chrome or Edge.',
  }[phase]

  return (
    <div className={'talk ' + phase}>
      <header className="talk-head">
        <div className="talk-who">
          <AgentAvatar agent={agent} size={30} dot />
          <div><b>{agent.name}</b><small>{agent.online ? 'Online' : 'Offline'} · Talk mode{where ? ' · in ' + where : ''}</small></div>
        </div>
        <div className="talk-actions">
          {conv && <button className="icon-btn" onClick={() => { pause(); go(`/c/${conv.id}`) }} title="Open the chat" aria-label="Open the chat"><MessageSquare size={20} /></button>}
          <button className="icon-btn" onClick={leave} title="End" aria-label="End talk mode"><X size={22} /></button>
        </div>
      </header>

      <div className="talk-stage">
        <button className="orb" onClick={tapOrb} disabled={phase === 'unsupported' || !conv} aria-label={status}>
          <span className="orb-core" />
          <span className="orb-ring" />
          <span className="orb-ring two" />
        </button>
        <p className="talk-status" aria-live="polite">{status}</p>
        {error && <p className="talk-error">{error}</p>}
        <div className="talk-lines">
          {phase === 'listening' && heard && <p className="talk-heard">{heard}</p>}
          {phase !== 'listening' && said && <p className="talk-said">“{said}”</p>}
          {phase !== 'listening' && reply && <p className="talk-reply">{toSpeech(reply).slice(0, 400)}{toSpeech(reply).length > 400 ? '…' : ''}</p>}
        </div>
      </div>

      <footer className="talk-foot">
        {(phase === 'listening' || phase === 'thinking' || phase === 'speaking')
          ? <button className="talk-btn" onClick={pause}><MicOff size={18} /> Pause</button>
          : phase !== 'unsupported' && <button className="talk-btn primary" onClick={start} disabled={!conv}><Mic size={18} /> {phase === 'paused' ? 'Carry on' : 'Start'}</button>}
      </footer>
    </div>
  )
}

function lastSentenceEnd(text: string, from: number) {
  const re = /[.!?](?=\s)|\n/g
  re.lastIndex = from
  let end = from, m
  while ((m = re.exec(text))) end = m.index + 1
  return end
}

/** Markdown to plain sentences for the speech voice (same rules as the server's Siri replies). */
export function toSpeech(text: string) {
  return text
    .replace(/```[\s\S]*?```/g, ' (code is in the chat) ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/https?:\/\/\S+/g, 'a link in the chat')
    .replace(/^[ \t]*\|?[ \t]*:?-{3,}.*$/gm, '')
    .replace(/^[ \t]*\|(.*)\|[ \t]*$/gm, (_, row: string) => row.split('|').map((x) => x.trim()).filter(Boolean).join(', ') + '.')
    .replace(/^\s{0,3}#{1,6}\s*/gm, '')
    .replace(/^\s*(?:[-*+•]|\d+[.)])\s+/gm, '')
    .replace(/(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1/g, '$2')
    .replace(/^\s*>\s?/gm, '')
    .replace(/\[task:\w+\]/g, '')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n{2,}/g, '\n')
    .trim()
}
