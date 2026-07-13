import { useEffect, useState } from 'react'
import type { AppConfig, CloudProviderId } from '../types'
import { api } from '../services/api'
import './ConfigModal.css'

const PROVIDERS: CloudProviderId[] = [
  'openai',
  'gemini',
  'deepseek',
  'openrouter',
  'grok',
]

type Section = 'cloud' | 'tts'
type CloudTab = CloudProviderId

type CloudDraft = Record<
  CloudProviderId,
  { apiKey: string; baseUrl: string; model: string; apiKeySet: boolean; label: string }
>

type Props = {
  open: boolean
  onClose: () => void
}

function emptyCloud(): CloudDraft {
  return {
    openai: {
      apiKey: '',
      baseUrl: 'https://api.openai.com/v1',
      model: 'gpt-4o-mini',
      apiKeySet: false,
      label: 'OpenAI',
    },
    gemini: {
      apiKey: '',
      baseUrl: 'https://generativelanguage.googleapis.com/v1beta',
      model: 'gemini-2.0-flash',
      apiKeySet: false,
      label: 'Gemini',
    },
    deepseek: {
      apiKey: '',
      baseUrl: 'https://api.deepseek.com',
      model: 'deepseek-chat',
      apiKeySet: false,
      label: 'DeepSeek',
    },
    openrouter: {
      apiKey: '',
      baseUrl: 'https://openrouter.ai/api/v1',
      model: 'google/gemini-2.0-flash-001',
      apiKeySet: false,
      label: 'OpenRouter',
    },
    grok: {
      apiKey: '',
      baseUrl: 'https://api.x.ai/v1',
      model: 'grok-3-mini',
      apiKeySet: false,
      label: 'Grok',
    },
  }
}

export default function ConfigModal({ open, onClose }: Props) {
  const [section, setSection] = useState<Section>('cloud')
  const [draft, setDraft] = useState<CloudDraft>(emptyCloud)
  /** Mỗi ô 1 key; '' = ô trống mới / placeholder đã lưu */
  const [elSlots, setElSlots] = useState<string[]>([''])
  const [elSavedCount, setElSavedCount] = useState(0)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')
  const [tab, setTab] = useState<CloudTab>('openai')

  useEffect(() => {
    if (!open) return
    setLoading(true)
    setMsg('')
    void api
      .getConfig()
      .then((cfg: AppConfig) => {
        const next = emptyCloud()
        for (const id of PROVIDERS) {
          const c = cfg.cloud?.[id]
          if (!c) continue
          next[id] = {
            apiKey: '',
            baseUrl: c.baseUrl || next[id].baseUrl,
            model: c.model || next[id].model,
            apiKeySet: !!c.apiKeySet,
            label: c.label || next[id].label,
          }
        }
        setDraft(next)
        const el = cfg.tts?.elevenlabs
        const n = Math.max(1, Number(el?.keyCount || 0) || (el?.apiKeySet ? 1 : 0))
        setElSavedCount(el?.apiKeySet ? n : 0)
        // Ô trống = giữ key đã lưu; user gõ = thay / thêm
        setElSlots(Array.from({ length: Math.max(1, n) }, () => ''))
      })
      .catch((e: Error) => setMsg(e.message || 'Không tải được cấu hình'))
      .finally(() => setLoading(false))
  }, [open])

  if (!open) return null

  const cur = draft[tab]

  function setElSlot(i: number, value: string) {
    setElSlots((prev) => {
      const next = [...prev]
      next[i] = value
      return next
    })
  }

  function addElSlot() {
    setElSlots((prev) => [...prev, ''])
  }

  function removeElSlot(i: number) {
    setElSlots((prev) => {
      if (prev.length <= 1) return ['']
      return prev.filter((_, idx) => idx !== i)
    })
    // Xóa ô đã lưu (placeholder) → giảm đếm hiển thị; lưu mới sẽ ghi đè list
    if (i < elSavedCount) {
      setElSavedCount((c) => Math.max(0, c - 1))
    }
  }

  async function onSave() {
    setSaving(true)
    setMsg('')
    try {
      const cloud: Record<string, { apiKey?: string; baseUrl?: string; model?: string }> =
        {}
      for (const id of PROVIDERS) {
        const d = draft[id]
        cloud[id] = {
          baseUrl: d.baseUrl,
          model: d.model,
          ...(d.apiKey.trim() ? { apiKey: d.apiKey.trim() } : {}),
        }
      }
      const body: {
        cloud: typeof cloud
        tts?: { elevenlabs: { apiKeys?: string } }
      } = { cloud }

      // Chỉ gửi TTS khi user gõ key mới / thay — ô trống = giữ nguyên server
      const typed = elSlots.map((s) => s.trim()).filter(Boolean)
      if (typed.length > 0) {
        body.tts = { elevenlabs: { apiKeys: typed.join(',') } }
      }

      const cfg = await api.saveConfig(body)
      const next = emptyCloud()
      for (const id of PROVIDERS) {
        const c = cfg.cloud?.[id]
        if (!c) continue
        next[id] = {
          apiKey: '',
          baseUrl: c.baseUrl || next[id].baseUrl,
          model: c.model || next[id].model,
          apiKeySet: !!c.apiKeySet,
          label: c.label || next[id].label,
        }
      }
      setDraft(next)
      const el = cfg.tts?.elevenlabs
      const n = Math.max(1, Number(el?.keyCount || 0) || (el?.apiKeySet ? 1 : 0))
      setElSavedCount(el?.apiKeySet ? n : 0)
      setElSlots(Array.from({ length: Math.max(1, n) }, () => ''))
      setMsg('Đã lưu.')
    } catch (e) {
      setMsg(e instanceof Error ? e.message : 'Lưu thất bại')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="cfg-overlay" role="presentation" onClick={onClose}>
      <div
        className="cfg-modal"
        role="dialog"
        aria-modal
        aria-label="Cấu hình"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="cfg-head">
          <div>
            <h2>Cấu hình</h2>
            <p>API dịch cloud · ElevenLabs TTS</p>
          </div>
          <button type="button" className="cfg-close" onClick={onClose} aria-label="Đóng">
            ×
          </button>
        </header>

        <div className="cfg-section-tabs">
          <button
            type="button"
            className={section === 'cloud' ? 'active' : undefined}
            onClick={() => setSection('cloud')}
          >
            API dịch
          </button>
          <button
            type="button"
            className={section === 'tts' ? 'active' : undefined}
            onClick={() => setSection('tts')}
          >
            ElevenLabs
            {elSavedCount > 0 ? <span className="cfg-dot" title="Đã có key" /> : null}
          </button>
        </div>

        {section === 'cloud' && (
          <div className="cfg-tabs">
            {PROVIDERS.map((id) => (
              <button
                key={id}
                type="button"
                className={tab === id ? 'active' : undefined}
                onClick={() => setTab(id)}
              >
                {draft[id].label}
                {draft[id].apiKeySet ? <span className="cfg-dot" title="Đã có key" /> : null}
              </button>
            ))}
          </div>
        )}

        {loading ? (
          <p className="cfg-msg">Đang tải…</p>
        ) : section === 'cloud' ? (
          <div className="cfg-body">
            <label>
              <span>API key {cur.apiKeySet ? '(đã lưu — nhập để thay)' : ''}</span>
              <input
                type="password"
                autoComplete="off"
                placeholder={cur.apiKeySet ? '••••••••' : 'sk-…'}
                value={cur.apiKey}
                onChange={(e) =>
                  setDraft((d) => ({
                    ...d,
                    [tab]: { ...d[tab], apiKey: e.target.value },
                  }))
                }
              />
            </label>
            <label>
              <span>Base URL</span>
              <input
                type="text"
                value={cur.baseUrl}
                onChange={(e) =>
                  setDraft((d) => ({
                    ...d,
                    [tab]: { ...d[tab], baseUrl: e.target.value },
                  }))
                }
              />
            </label>
            <label>
              <span>Model</span>
              <input
                type="text"
                value={cur.model}
                onChange={(e) =>
                  setDraft((d) => ({
                    ...d,
                    [tab]: { ...d[tab], model: e.target.value },
                  }))
                }
              />
            </label>
            <p className="cfg-hint">
              Chọn provider ở sidebar → <strong>Công cụ dịch</strong>. Key lưu{' '}
              <code>server/data/app_config.json</code>.
            </p>
          </div>
        ) : (
          <div className="cfg-body">
            <div className="cfg-el-list">
              {elSlots.map((val, i) => {
                const saved = i < elSavedCount && !val
                return (
                  <div key={i} className="cfg-el-row">
                    <label>
                      <span>
                        Key {i + 1}
                        {saved ? ' (đã lưu)' : ''}
                      </span>
                      <input
                        type="password"
                        autoComplete="off"
                        placeholder={saved ? '••••••••  — nhập để thay' : 'sk_…'}
                        value={val}
                        onChange={(e) => setElSlot(i, e.target.value)}
                      />
                    </label>
                    <button
                      type="button"
                      className="cfg-el-remove"
                      onClick={() => removeElSlot(i)}
                      disabled={elSlots.length <= 1 && !val && elSavedCount === 0}
                      title="Xóa ô"
                      aria-label={`Xóa key ${i + 1}`}
                    >
                      ×
                    </button>
                  </div>
                )
              })}
            </div>
            <button type="button" className="cfg-el-add" onClick={addElSlot}>
              + Thêm key
            </button>
            <p className="cfg-hint">
              Giọng <strong>ElevenLabs</strong> ở sidebar. Nhiều key → xoay khi 401/429.
              Để trống ô đã lưu = giữ nguyên; gõ key mới = thay / thêm.
            </p>
          </div>
        )}

        {msg ? <p className="cfg-msg">{msg}</p> : null}

        <footer className="cfg-foot">
          <button type="button" className="cfg-secondary" onClick={onClose}>
            Đóng
          </button>
          <button
            type="button"
            className="cfg-primary"
            disabled={saving || loading}
            onClick={onSave}
          >
            {saving ? 'Đang lưu…' : 'Lưu'}
          </button>
        </footer>
      </div>
    </div>
  )
}
