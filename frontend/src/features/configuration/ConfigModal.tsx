import { useCallback, useEffect, useState } from 'react'
import type { AppConfig, CloudProviderId, SystemChecks } from '@/features/project/project.types'
import { api } from '@/features/project/project.api'
import './ConfigModal.css'

const PROVIDERS: CloudProviderId[] = [
  'openai',
  'gemini',
  'deepseek',
  'openrouter',
  'grok',
]

type Section = 'setup' | 'cloud' | 'tts'
type CloudTab = CloudProviderId

type CloudDraft = Record<
  CloudProviderId,
  { apiKey: string; baseUrl: string; model: string; apiKeySet: boolean; label: string }
>

type Props = {
  open: boolean
  onClose: () => void
  /** Mở thẳng tab Thiết lập (first-run) */
  initialSection?: Section
  /** First-run: thiếu dependency bắt buộc — không đóng bằng overlay */
  forceSetup?: boolean
  onSetupReady?: () => void
  /** Sau lưu config (đặc biệt ElevenLabs key) — App reload /api/voices */
  onSaved?: () => void
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

export default function ConfigModal({
  open,
  onClose,
  initialSection = 'cloud',
  forceSetup = false,
  onSetupReady,
  onSaved,
}: Props) {
  const [section, setSection] = useState<Section>(initialSection)
  const [draft, setDraft] = useState<CloudDraft>(emptyCloud)
  /** Mỗi ô 1 key; '' = ô trống mới / placeholder đã lưu */
  const [elSlots, setElSlots] = useState<string[]>([''])
  const [elSavedCount, setElSavedCount] = useState(0)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')
  const [tab, setTab] = useState<CloudTab>('openai')
  const [checks, setChecks] = useState<SystemChecks | null>(null)
  const [checksLoading, setChecksLoading] = useState(false)
  const [checksErr, setChecksErr] = useState('')
  const [installing, setInstalling] = useState<string | null>(null)

  const loadChecks = useCallback(() => {
    setChecksLoading(true)
    setChecksErr('')
    void api
      .systemChecks()
      .then((c) => {
        setChecks(c)
        if (c.ok) onSetupReady?.()
      })
      .catch((e: Error) => {
        setChecksErr(e.message || 'Không kiểm tra được hệ thống')
        setChecks(null)
      })
      .finally(() => setChecksLoading(false))
  }, [onSetupReady])

  useEffect(() => {
    if (!open) return
    setSection(initialSection)
  }, [open, initialSection])

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

  useEffect(() => {
    if (!open) return
    if (section === 'setup' || forceSetup) loadChecks()
  }, [open, section, forceSetup, loadChecks])

  if (!open) return null

  const cur = draft[tab]
  const canClose = !forceSetup || !!checks?.ok

  async function installAction(kind: 'ocr_cuda' | 'demucs_cuda') {
    setInstalling(kind)
    setChecksErr('')
    try {
      const result =
        kind === 'ocr_cuda' ? await api.installOcrCuda() : await api.installDemucsCuda()
      setMsg(result.message + (result.detail ? ` · ${result.detail}` : ''))
      loadChecks()
    } catch (e) {
      setChecksErr(
        e instanceof Error
          ? e.message
          : kind === 'ocr_cuda'
            ? 'Cài GPU OCR thất bại'
            : 'Cài Demucs thất bại',
      )
    } finally {
      setInstalling(null)
    }
  }

  function tryClose() {
    if (!canClose) return
    onClose()
  }

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
      setMsg(typed.length > 0 ? 'Đã lưu. Đang tải lại danh sách giọng…' : 'Đã lưu.')
      onSaved?.()
    } catch (e) {
      setMsg(e instanceof Error ? e.message : 'Lưu thất bại')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      className="cfg-overlay"
      role="presentation"
      onClick={canClose ? tryClose : undefined}
    >
      <div
        className="cfg-modal cfg-modal-wide"
        role="dialog"
        aria-modal
        aria-label="Cấu hình"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="cfg-head">
          <div>
            <h2>Cấu hình</h2>
            <p>
              {forceSetup && !checks?.ok
                ? 'Cài đủ thành phần bắt buộc để bắt đầu'
                : 'Thiết lập hệ thống · API dịch · ElevenLabs'}
            </p>
          </div>
          {canClose ? (
            <button type="button" className="cfg-close" onClick={tryClose} aria-label="Đóng">
              ×
            </button>
          ) : null}
        </header>

        <div className="cfg-section-tabs">
          <button
            type="button"
            className={section === 'setup' ? 'active' : undefined}
            onClick={() => setSection('setup')}
          >
            Thiết lập
            {checks && !checks.ok ? (
              <span className="cfg-dot cfg-dot-warn" title="Thiếu dependency" />
            ) : checks?.ok ? (
              <span className="cfg-dot" title="Sẵn sàng" />
            ) : null}
          </button>
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

        {loading && section !== 'setup' ? (
          <p className="cfg-msg">Đang tải…</p>
        ) : section === 'setup' ? (
          <div className="cfg-body cfg-setup">
            <div className="cfg-setup-bar">
              <div>
                <strong>{checks?.summary || (checksLoading ? 'Đang kiểm tra…' : '—')}</strong>
                {checks ? (
                  <span className="cfg-setup-meta">
                    {checks.platform} · Python {checks.python}
                  </span>
                ) : null}
              </div>
              <button
                type="button"
                className="cfg-secondary cfg-setup-refresh"
                disabled={checksLoading || !!installing}
                onClick={loadChecks}
              >
                {checksLoading ? 'Đang kiểm tra…' : 'Kiểm tra lại'}
              </button>
            </div>
            {checks?.device ? (
              <div className="cfg-device-card" role="status">
                <div className="cfg-device-title">Phát hiện thiết bị</div>
                <div className="cfg-device-grid">
                  <div>
                    <span className="cfg-device-k">Hệ điều hành</span>
                    <strong>
                      {checks.device.osLabel}
                      {checks.device.appleSilicon ? ' · Apple Silicon' : ''}
                    </strong>
                  </div>
                  <div>
                    <span className="cfg-device-k">Kiến trúc</span>
                    <strong>{checks.device.arch}</strong>
                  </div>
                  <div>
                    <span className="cfg-device-k">GPU</span>
                    <strong>
                      {checks.device.hasGpu
                        ? checks.device.gpuName || checks.device.gpuKind
                        : 'Không có (CPU)'}
                      {checks.device.vramMb ? ` · ${checks.device.vramMb} MB` : ''}
                    </strong>
                  </div>
                  <div>
                    <span className="cfg-device-k">Tăng tốc</span>
                    <strong>{String(checks.device.accel).toUpperCase()}</strong>
                  </div>
                </div>
                <p className="cfg-device-plan">{checks.device.install.summary}</p>
                <p className="cfg-device-hint">{checks.device.install.hint}</p>
                {(checks.device.install.actions?.length ?? 0) > 0 ? (
                  <div className="cfg-device-actions">
                    {checks.device.install.actions!.map((a) => {
                      const done = (checks.items || []).some(
                        (it) =>
                          it.ok &&
                          (it.install === a.id ||
                            (a.id === 'demucs_cuda' && it.id === 'demucs') ||
                            (a.id === 'ocr_cuda' && it.id === 'ocr_cuda')),
                      )
                      return done ? (
                        <span key={a.id} className="cfg-check-installed">
                          {a.label} ✓
                        </span>
                      ) : (
                        <button
                          key={a.id}
                          type="button"
                          className="cfg-check-install"
                          disabled={!!installing}
                          onClick={() =>
                            void installAction(a.id as 'ocr_cuda' | 'demucs_cuda')
                          }
                        >
                          {installing === a.id ? 'Đang cài…' : a.label}
                        </button>
                      )
                    })}
                  </div>
                ) : null}
              </div>
            ) : null}
            {checksErr ? <p className="cfg-msg cfg-msg-err">{checksErr}</p> : null}
            {msg && section === 'setup' ? <p className="cfg-msg">{msg}</p> : null}
            <ul className="cfg-check-list">
              {(checks?.items || []).filter((it) => it.id !== 'device').map((it) => (
                <li
                  key={it.id}
                  className={`cfg-check-item ${it.ok ? 'ok' : it.required ? 'bad' : 'warn'}`}
                >
                  <div className="cfg-check-top">
                    <span className="cfg-check-status" aria-hidden>
                      {it.ok ? '✓' : it.required ? '!' : '·'}
                    </span>
                    <div className="cfg-check-main">
                      <div className="cfg-check-name">
                        {it.name}
                        {it.required ? (
                          <em className="cfg-req">bắt buộc</em>
                        ) : (
                          <em className="cfg-opt">tuỳ chọn</em>
                        )}
                      </div>
                      <div className="cfg-check-detail">{it.detail}</div>
                      {!it.ok ? <div className="cfg-check-hint">{it.hint}</div> : null}
                    </div>
                    {it.ok ? (
                      it.install === 'ocr_cuda' || it.install === 'demucs_cuda' ? (
                        <span className="cfg-check-installed">Đã cài</span>
                      ) : null
                    ) : it.install === 'ocr_cuda' || it.install === 'demucs_cuda' ? (
                      <button
                        type="button"
                        className="cfg-check-install"
                        disabled={!!installing}
                        onClick={() =>
                          void installAction(it.install as 'ocr_cuda' | 'demucs_cuda')
                        }
                      >
                        {installing === it.install
                          ? 'Đang cài…'
                          : it.installLabel ||
                            (it.install === 'demucs_cuda'
                              ? checks?.device?.install.demucsLabel || 'Cài Demucs GPU'
                              : checks?.device?.install.ocrLabel || 'Cài OCR CUDA')}
                      </button>
                    ) : it.install ? (
                      it.install.startsWith('http') ? (
                        <a
                          className="cfg-check-link"
                          href={it.install}
                          target="_blank"
                          rel="noreferrer"
                          title={it.installLabel || it.install}
                        >
                          {it.installLabel || 'Tải'}
                        </a>
                      ) : (
                        <code className="cfg-check-cmd" title={it.installLabel || 'Chạy trong terminal'}>
                          {it.install}
                        </code>
                      )
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>
            <p className="cfg-hint">
              Mọi mục cài đặt theo thiết bị đã phát hiện (Windows / macOS / Linux + GPU).
              Link tải và lệnh pip/brew/apt đổi theo OS; NVIDIA → CUDA; Apple Silicon → Metal.
            </p>
          </div>
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
              <code>backend/data/app_config.json</code>.
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
          {section === 'setup' ? (
            <>
              {canClose ? (
                <button type="button" className="cfg-secondary" onClick={tryClose}>
                  Đóng
                </button>
              ) : (
                <span className="cfg-foot-note">Cài đủ mục bắt buộc để tiếp tục</span>
              )}
              <button
                type="button"
                className="cfg-primary"
                disabled={checksLoading || !checks?.ok}
                onClick={() => {
                  if (checks?.ok) {
                    onSetupReady?.()
                    onClose()
                  } else {
                    loadChecks()
                  }
                }}
              >
                {checks?.ok ? 'Bắt đầu' : checksLoading ? 'Đang kiểm tra…' : 'Kiểm tra lại'}
              </button>
            </>
          ) : (
            <>
              <button type="button" className="cfg-secondary" onClick={tryClose} disabled={!canClose}>
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
            </>
          )}
        </footer>
      </div>
    </div>
  )
}
