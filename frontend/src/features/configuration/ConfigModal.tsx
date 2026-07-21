import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { AppConfig, CloudProviderId, SystemChecks } from '@/features/project/project.types'
import { api } from '@/features/project/project.api'
import ProgressPopup from '@/shared/components/ProgressPopup'
import './ConfigModal.css'

const PROVIDERS: CloudProviderId[] = [
  'openai',
  'gemini',
  'deepseek',
  'openrouter',
  'grok',
]

type InstallKind = 'ai_runtime' | 'ocr_cuda' | 'demucs_cuda'

const INSTALL_LABELS: Record<InstallKind, string> = {
  ai_runtime: 'gói AI (Whisper · OCR · VieNeu)',
  ocr_cuda: 'OCR CUDA',
  demucs_cuda: 'Demucs',
}

const INSTALL_ORDER: InstallKind[] = ['ai_runtime', 'ocr_cuda', 'demucs_cuda']

function installLabel(kind: string): string {
  return INSTALL_LABELS[kind as InstallKind] || kind
}

function nextAutoInstall(checks: SystemChecks): InstallKind | null {
  for (const id of INSTALL_ORDER) {
    const it = checks.items.find((i) => !i.ok && i.install === id)
    if (it?.required) return id
  }
  return null
}

type Section = 'setup' | 'cloud' | 'tts' | 'logs'
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
  const [installProgressMinimized, setInstallProgressMinimized] = useState(false)
  const [installPopupError, setInstallPopupError] = useState('')
  const [pendingRestart, setPendingRestart] = useState(false)
  const [restarting, setRestarting] = useState(false)
  const [logText, setLogText] = useState('')
  const [logPath, setLogPath] = useState('')
  const [logLoading, setLogLoading] = useState(false)
  const [logErr, setLogErr] = useState('')
  const [logCopied, setLogCopied] = useState(false)
  const autoSetupLock = useRef(false)

  const loadLogs = useCallback(() => {
    setLogLoading(true)
    setLogErr('')
    void api
      .getAppLogs(1200)
      .then((r) => {
        setLogText(r.text || '(trống)')
        setLogPath(r.path || '')
      })
      .catch((e: Error) => {
        setLogErr(e.message || 'Không đọc được log')
        setLogText('')
      })
      .finally(() => setLogLoading(false))
  }, [])

  const loadChecks = useCallback((refresh = false, deep = false) => {
    setChecksLoading(true)
    setChecksErr('')
    void api
      .systemChecks(refresh, deep)
      .then((c) => {
        setChecks(c)
      })
      .catch((e: Error) => {
        setChecksErr(e.message || 'Không kiểm tra được hệ thống')
        setChecks(null)
      })
      .finally(() => setChecksLoading(false))
  }, [forceSetup])

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
    if (section === 'setup' || forceSetup) loadChecks(false, false)
    if (section === 'logs') loadLogs()
  }, [open, section, forceSetup, loadChecks, loadLogs])

  useEffect(() => {
    if (!open || section !== 'setup') return
    let cancelled = false
    const syncInstall = async () => {
      try {
        const st = await api.installStatus()
        if (cancelled) return
        if (st.running && st.kind) {
          setInstalling(st.kind)
          setMsg(`Đang cài ${installLabel(st.kind)}…`)
        }
      } catch {
        /* backend chưa sẵn sàng */
      }
    }
    void syncInstall()
    const id = window.setInterval(() => void syncInstall(), 2000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [open, section])

  const cur = draft[tab]
  const canClose = !forceSetup || !!checks?.ok

  const installAction = useCallback(async (kind: InstallKind) => {
    setInstalling(kind)
    setInstallProgressMinimized(false)
    setInstallPopupError('')
    setChecksErr('')
    try {
      const result = kind === 'ai_runtime'
        ? await api.installAiRuntime()
        : kind === 'ocr_cuda'
          ? await api.installOcrCuda()
          : await api.installDemucsCuda()
      setMsg(result.message + (result.detail ? ` · ${result.detail}` : ''))
      if (result.needsRestart) setPendingRestart(true)
      loadChecks(true, false)
    } catch (e) {
      const message = e instanceof Error
          ? e.message
          : kind === 'ai_runtime'
            ? 'Cài gói AI thất bại'
            : kind === 'ocr_cuda'
            ? 'Cài GPU OCR thất bại'
            : 'Cài Demucs thất bại'
      setChecksErr(message)
      setInstallPopupError(message)
    } finally {
      setInstalling(null)
      autoSetupLock.current = false
    }
  }, [loadChecks])

  const restartApp = useCallback(async () => {
    setRestarting(true)
    setChecksErr('')
    try {
      await api.restartApp()
    } catch (e) {
      setChecksErr(e instanceof Error ? e.message : 'Không khởi động lại được app')
      autoSetupLock.current = false
    } finally {
      setRestarting(false)
    }
  }, [])

  useEffect(() => {
    if (!open || section !== 'setup') return
    if (checksLoading || installing || restarting || !checks) return
    if (autoSetupLock.current) return
    const shouldAuto = forceSetup || !checks.ok
    if (!shouldAuto) return

    const run = async () => {
      const next = nextAutoInstall(checks)
      if (next) {
        autoSetupLock.current = true
        await installAction(next)
        autoSetupLock.current = false
        return
      }
    }

    void run()
  }, [
    open,
    forceSetup,
    section,
    checks,
    checksLoading,
    installing,
    restarting,
    pendingRestart,
    installAction,
    onSetupReady,
  ])

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

  if (!open) return null

  return createPortal(
    <div
      className="cfg-overlay"
      role="presentation"
      onClick={canClose ? tryClose : undefined}
    >
      <div
        className={`cfg-modal cfg-modal-wide${section === 'setup' ? ' cfg-modal-setup' : ''}`}
        role="dialog"
        aria-modal
        aria-label="Cấu hình"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="cfg-head">
          <div>
            <h2>Cấu hình</h2>
            <p>
              {installing
                ? `Đang cài ${installLabel(installing)}…`
                : forceSetup && !checks?.ok
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
          {!forceSetup ? (
            <>
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
              <button
                type="button"
                className={section === 'logs' ? 'active' : undefined}
                onClick={() => setSection('logs')}
              >
                Log
              </button>
            </>
          ) : null}
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

        {loading && section !== 'setup' && section !== 'logs' ? (
          <p className="cfg-msg">Đang tải…</p>
        ) : section === 'setup' ? (
          <div className="cfg-body cfg-setup">
            <div className="cfg-setup-bar">
              <div className="cfg-setup-info">
                <strong>
                  {installing
                    ? `Đang cài ${installLabel(installing)}…`
                    : checks?.summary || (checksLoading ? 'Đang tải…' : '—')}
                </strong>
                {checks ? (
                  <span className="cfg-setup-meta">
                    {checks.platform} · Python {checks.python}
                    {checks.device?.accel
                      ? ` · ${String(checks.device.accel).toUpperCase()}`
                      : ''}
                    {checks.device?.gpuName ? ` · ${checks.device.gpuName}` : ''}
                    {checks.device?.vramMb ? ` · ${checks.device.vramMb} MB` : ''}
                  </span>
                ) : null}
              </div>
              <div className="cfg-setup-actions">
                {(checks?.device?.install.actions?.length ?? 0) > 0
                  ? checks?.device?.install.actions!.map((a) => {
                      const done = (checks?.items || []).some(
                        (it) =>
                          it.ok &&
                          (it.install === a.id ||
                            (a.id === 'demucs_cuda' && it.id === 'demucs') ||
                            (a.id === 'ocr_cuda' && it.id === 'ocr_cuda')),
                      )
                      return done ? (
                        <span key={a.id} className="cfg-check-installed cfg-setup-chip">
                          {a.label} ✓
                        </span>
                      ) : (
                        <button
                          key={a.id}
                          type="button"
                          className="cfg-check-install cfg-check-install-sm"
                          disabled={!!installing}
                          onClick={() =>
                            void installAction(a.id as 'ocr_cuda' | 'demucs_cuda')
                          }
                        >
                          {installing === a.id ? '…' : a.label}
                        </button>
                      )
                    })
                  : null}
                <button
                  type="button"
                  className="cfg-secondary cfg-setup-refresh"
                  disabled={checksLoading || !!installing}
                  onClick={() => loadChecks(true, false)}
                >
                  {checksLoading ? '…' : 'Kiểm tra lại'}
                </button>
              </div>
            </div>
            {checksErr ? <p className="cfg-msg cfg-msg-err">{checksErr}</p> : null}
            {pendingRestart ? (
              <p className="cfg-msg cfg-msg-restart">
                Đã cài gói cần reload — cài tiếp các mục còn lại rồi bấm{' '}
                <strong>Khởi động lại</strong>.
              </p>
            ) : null}
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
                      ['ai_runtime', 'ocr_cuda', 'demucs_cuda'].includes(it.install) ? (
                        <span className="cfg-check-installed">Đã cài</span>
                      ) : null
                    ) : ['ai_runtime', 'ocr_cuda', 'demucs_cuda'].includes(it.install) ? (
                      <button
                        type="button"
                        className="cfg-check-install"
                        disabled={!!installing}
                        onClick={() =>
                          void installAction(it.install as 'ai_runtime' | 'ocr_cuda' | 'demucs_cuda')
                        }
                      >
                        {installing === it.install
                          ? 'Đang cài…'
                          : it.installLabel ||
                            (it.install === 'ai_runtime'
                              ? 'Cài gói AI'
                              : it.install === 'demucs_cuda'
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
            <details className="cfg-hint-details">
              <summary>Ghi chú cài đặt theo thiết bị</summary>
              <p className="cfg-hint">
                Link tải và lệnh pip/brew/apt đổi theo OS; NVIDIA → CUDA; Apple Silicon → Metal.
              </p>
            </details>
          </div>
        ) : section === 'cloud' ? (
          <div className="cfg-body cfg-body-grid">
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
        ) : section === 'tts' ? (
          <div className="cfg-body">
            <div className="cfg-el-grid">
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
        ) : section === 'logs' ? (
          <div className="cfg-log-panel">
            <p className="cfg-hint">
              Lỗi job (Dịch / Lồng tiếng / Xuất), warm-models, crash hook. Copy gửi AI để sửa.
              {logPath ? (
                <>
                  {' '}
                  File: <code className="cfg-log-path">{logPath}</code>
                </>
              ) : null}
            </p>
            {logErr ? <p className="cfg-msg cfg-msg-err">{logErr}</p> : null}
            <pre className="cfg-log-pre" tabIndex={0}>
              {logLoading ? 'Đang tải…' : logText || '(trống)'}
            </pre>
            <div className="cfg-log-actions">
              <button type="button" className="cfg-secondary" disabled={logLoading} onClick={() => loadLogs()}>
                {logLoading ? 'Đang tải…' : 'Tải lại'}
              </button>
              <button
                type="button"
                className="cfg-secondary"
                disabled={!logText || logLoading}
                onClick={() => {
                  void navigator.clipboard.writeText(logText).then(() => {
                    setLogCopied(true)
                    window.setTimeout(() => setLogCopied(false), 1600)
                  })
                }}
              >
                {logCopied ? 'Đã copy' : 'Copy log'}
              </button>
              <button
                type="button"
                className="cfg-secondary"
                disabled={logLoading}
                onClick={() => {
                  if (!window.confirm('Xóa toàn bộ file log?')) return
                  void api.clearAppLogs().then(() => loadLogs()).catch((e: Error) => setLogErr(e.message))
                }}
              >
                Xóa log
              </button>
            </div>
          </div>
        ) : null}

        {msg && section !== 'logs' ? <p className="cfg-msg">{msg}</p> : null}

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
              {pendingRestart ? (
                <button
                  type="button"
                  className="cfg-secondary cfg-restart-btn"
                  disabled={restarting || !!installing}
                  onClick={() => void restartApp()}
                >
                  {restarting ? 'Đang khởi động lại…' : 'Khởi động lại'}
                </button>
              ) : null}
              <button
                type="button"
                className="cfg-primary"
                disabled={checksLoading || !checks?.ok}
                onClick={() => {
                  if (checks?.ok) onSetupReady?.()
                  else loadChecks(true, false)
                }}
              >
                {checks?.ok ? 'Bắt đầu' : checksLoading ? 'Đang tải…' : 'Tải lại'}
              </button>
            </>
          ) : section === 'logs' ? (
            <button type="button" className="cfg-secondary" onClick={tryClose} disabled={!canClose}>
              Đóng
            </button>
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
      <div onClick={(e) => e.stopPropagation()}>
        <ProgressPopup
          active={Boolean(installing || installPopupError)}
          minimized={installProgressMinimized}
          running={Boolean(installing)}
          title={
            installPopupError
              ? 'Cài đặt thất bại'
              : installing === 'ai_runtime'
                ? 'Đang cài gói AI'
                : installing === 'ocr_cuda'
                  ? 'Đang cài GPU OCR'
                  : 'Đang cài Demucs'
          }
          message={
            installing === 'ai_runtime'
              ? 'Đang tải Whisper, OCR, zmAI và VieNeu Local. Vui lòng không tắt ứng dụng.'
              : installing
                ? 'Đang tải và cài các thành phần cần thiết. Vui lòng chờ.'
                : installPopupError
          }
          progress={installing ? 35 : 0}
          error={installPopupError || null}
          onMinimize={() => {
            if (installing) setInstallProgressMinimized(true)
            else setInstallPopupError('')
          }}
          onRestore={() => setInstallProgressMinimized(false)}
        />
      </div>
    </div>,
    document.body,
  )
}
