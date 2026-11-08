import { useEffect, useRef, useState } from 'react'
import { ArrowUp, CornerUpLeft, Mic, Paperclip, X } from 'lucide-react'
import { api, type Agent, type FileRef } from '../api'
import { AgentAvatar, sizeText } from './bits'
import { can, useStore } from '../store'

type Props = {
  onSend: (text: string, files: FileRef[]) => Promise<unknown> | void
  placeholder?: string
  mentionable?: Agent[]
  disabled?: boolean
  autoFocus?: boolean
  draftKey?: string
  hint?: React.ReactNode
  replyTo?: { id: number; author: string; quote: string } | null
  onCancelReply?: () => void
}

const drafts: Record<string, string> = {}

// Browser speech-to-text (Chrome, Edge, Safari). Chrome and Edge send the audio to Google / Microsoft to transcribe.
const SpeechRec: any = typeof window !== 'undefined' && ((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition)

export function Composer({ onSend, placeholder, mentionable = [], disabled, autoFocus, draftKey, hint, replyTo, onCancelReply }: Props) {
  const { s } = useStore()
  const mayAttach = can(s, 'files.upload')
  const [text, setText] = useState(() => (draftKey && drafts[draftKey]) || '')
  const [files, setFiles] = useState<FileRef[]>([])
  const [uploading, setUploading] = useState(0)
  const [error, setError] = useState('')
  const [mention, setMention] = useState<{ q: string; start: number; i: number } | null>(null)
  const ref = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const recRef = useRef<any>(null)
  const [listening, setListening] = useState(false)

  useEffect(() => () => recRef.current?.abort(), [])

  const toggleMic = () => {
    if (recRef.current) { recRef.current.stop(); return }
    const rec = new SpeechRec()
    rec.continuous = true
    rec.interimResults = true
    rec.lang = navigator.language || 'en-US'
    const base = text ? text.replace(/\s*$/, ' ') : ''
    let done = ''
    rec.onresult = (e: any) => {
      let interim = ''
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript
        if (e.results[i].isFinal) done += t; else interim += t
      }
      setText(base + done + interim)
    }
    rec.onerror = (e: any) => {
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') setError('Microphone blocked: allow it in the browser address bar.')
      else if (e.error !== 'aborted' && e.error !== 'no-speech') setError('Voice input stopped: ' + e.error)
    }
    rec.onend = () => { recRef.current = null; setListening(false); ref.current?.focus() }
    setError('')
    recRef.current = rec
    setListening(true)
    rec.start()
  }

  useEffect(() => { if (draftKey) drafts[draftKey] = text }, [text, draftKey])
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 260) + 'px'
  }, [text])
  useEffect(() => { if (autoFocus) ref.current?.focus() }, [autoFocus, draftKey])
  useEffect(() => { if (replyTo) ref.current?.focus() }, [replyTo?.id, replyTo?.quote])

  const options = mention
    ? [...mentionable.filter((a) => a.handle.toLowerCase().startsWith(mention.q) || a.name.toLowerCase().includes(mention.q)),
       ...('all'.startsWith(mention.q) && mentionable.length > 1 ? [{ id: '@all', handle: 'all', name: 'Everyone in this channel' } as Agent] : [])].slice(0, 8)
    : []

  const updateMention = (value: string, caret: number) => {
    const before = value.slice(0, caret)
    const m = before.match(/(?:^|\s)@([\w.-]*)$/)
    setMention(m && mentionable.length ? { q: m[1].toLowerCase(), start: caret - m[1].length - 1, i: 0 } : null)
  }

  const pick = (a: Agent) => {
    if (!mention) return
    const el = ref.current!
    const caret = el.selectionStart
    const next = text.slice(0, mention.start) + '@' + a.handle + ' ' + text.slice(caret)
    setText(next)
    setMention(null)
    requestAnimationFrame(() => { el.focus(); const p = mention.start + a.handle.length + 2; el.setSelectionRange(p, p) })
  }

  const send = async () => {
    const value = text.trim()
    if ((!value && !files.length) || disabled || uploading) return
    setError('')
    recRef.current?.stop()
    const keep = { text, files }
    setText(''); setFiles([])
    try {
      await onSend(value, keep.files)
    } catch (e) {
      setText(keep.text); setFiles(keep.files)
      setError((e as Error).message)
    }
  }

  const addFiles = async (list: FileList | File[]) => {
    for (const file of Array.from(list)) {
      setUploading((n) => n + 1)
      try {
        const f = await api.upload(file)
        setFiles((fs) => [...fs, f])
      } catch (e) {
        setError((e as Error).message)
      } finally {
        setUploading((n) => n - 1)
      }
    }
  }

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape' && replyTo && !mention) { onCancelReply?.(); return }
    if (mention && options.length) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setMention({ ...mention, i: (mention.i + 1) % options.length }); return }
      if (e.key === 'ArrowUp') { e.preventDefault(); setMention({ ...mention, i: (mention.i - 1 + options.length) % options.length }); return }
      if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pick(options[mention.i]); return }
      if (e.key === 'Escape') { setMention(null); return }
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send() }
  }

  return (
    <div className="composer-wrap">
      {mention && options.length > 0 && (
        <div className="mention-menu">
          {options.map((a, i) => (
            <button key={a.id} className={i === mention.i ? 'on' : ''} onMouseDown={(e) => { e.preventDefault(); pick(a) }}>
              {a.id === '@all' ? <span className="avatar all">@</span> : <AgentAvatar agent={a} size={22} dot />}
              <b>@{a.handle}</b><span className="muted">{a.name}</span>
            </button>
          ))}
        </div>
      )}
      {replyTo && (
        <div className="reply-bar">
          <CornerUpLeft size={15} />
          <div className="reply-bar-text"><b>Replying to {replyTo.author}</b><span>{replyTo.quote}</span></div>
          <button className="icon-btn small" onClick={onCancelReply} aria-label="Cancel reply" title="Cancel reply (Esc)"><X size={14} /></button>
        </div>
      )}
      <div className={'composer' + (disabled ? ' disabled' : '') + (replyTo ? ' replying' : '')}
        onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files) }}>
        {(files.length > 0 || uploading > 0) && (
          <div className="attach-row">
            {files.map((f) => (
              <span key={f.id} className="chip">{f.name} <small>{sizeText(f.size)}</small>
                <button onClick={() => setFiles(files.filter((x) => x.id !== f.id))} aria-label="Remove"><X size={12} /></button></span>
            ))}
            {uploading > 0 && <span className="chip muted">Uploading…</span>}
          </div>
        )}
        <textarea ref={ref} rows={1} value={text} placeholder={placeholder || 'Message'} disabled={disabled}
          onChange={(e) => { setText(e.target.value); updateMention(e.target.value, e.target.selectionStart) }}
          onKeyDown={onKey} onClick={(e) => updateMention(text, (e.target as HTMLTextAreaElement).selectionStart)}
          onPaste={(e) => { if (e.clipboardData.files.length) { e.preventDefault(); addFiles(e.clipboardData.files) } }} />
        <div className="composer-bar">
          {mayAttach && <button className="icon-btn" onClick={() => fileRef.current?.click()} title="Attach files" aria-label="Attach files" disabled={disabled}><Paperclip size={18} /></button>}
          <input ref={fileRef} type="file" multiple hidden onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = '' }} />
          {SpeechRec && <button className={'icon-btn mic-btn' + (listening ? ' on' : '')} onClick={toggleMic} disabled={disabled}
            title={listening ? 'Stop voice input' : 'Voice input'} aria-label={listening ? 'Stop voice input' : 'Voice input'} aria-pressed={listening}><Mic size={18} /></button>}
          <div className="composer-hint">{hint}</div>
          <button className="send-btn" onClick={send} disabled={disabled || uploading > 0 || (!text.trim() && !files.length)} aria-label="Send"><ArrowUp size={18} /></button>
        </div>
      </div>
      {error && <div className="error-text composer-error">{error}</div>}
    </div>
  )
}
