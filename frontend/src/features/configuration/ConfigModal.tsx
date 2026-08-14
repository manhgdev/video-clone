import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { AppConfig, CloudProviderId, SystemChecks } from '@/features/project/project.types'
import { api } from '@/features/project/project.api'
import ProgressPopup from '@/shared/components/ProgressPopup'
import LicensePage from '@/features/license/LicensePage'
import type { LicenseStatus } from '@/features/license/license.api'
import { localize, useLocale } from '@/app/i18n'
import './ConfigModal.css'

const PROVIDERS: CloudProviderId[] = [
  'openai',
  'gemini',
  'deepseek',
  'openrouter',
  'grok',
  'nvidia',
]

type InstallKind = 'ai_runtime' | 'ai_runtime_ocr' | 'ai_runtime_vieneu' | 'ocr_cuda' | 'demucs_cuda' | 'nvm'

const INSTALL_LABELS: Record<InstallKind, string> = {
  ai_runtime: 'gói AI',
  ai_runtime_ocr: 'gói AI',
  ai_runtime_vieneu: 'gói AI',
  ocr_cuda: 'OCR CUDA',
  demucs_cuda: 'Demucs',
  nvm: 'NVM + Node.js LTS',
}

const INSTALL_ORDER: InstallKind[] = ['ai_runtime', 'ai_runtime_ocr', 'ai_runtime_vieneu', 'ocr_cuda', 'demucs_cuda']

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

type Section = 'setup' | 'cloud' | 'tts' | 'license' | 'logs'
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
  licenseStatus?: LicenseStatus
  onLicenseStatusChange?: (status: LicenseStatus) => void
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
    nvidia: {
      apiKey: '',
      baseUrl: 'https://integrate.api.nvidia.com/v1',
      model: 'nvidia/riva-translate-4b-instruct-v2',
      apiKeySet: false,
      label: 'NVIDIA NIM',
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
  licenseStatus,
  onLicenseStatusChange,
}: Props) {
  const { locale } = useLocale()
  const t = (vietnamese: string, english: string) => localize(locale, vietnamese, english)
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
  const [installLog, setInstallLog] = useState('')
  const [pendingRestart, setPendingRestart] = useState(false)
  const [restarting, setRestarting] = useState(false)
  const [logText, setLogText] = useState('')
  const [logPath, setLogPath] = useState('')
  const [logLoading, setLogLoading] = useState(false)
  const [logErr, setLogErr] = useState('')
  const [logCopied, setLogCopied] = useState(false)
  const autoSetupLock = useRef(false)
  const restartRequested = useRef(false)

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
    void (async () => {
      for (let attempt = 0; attempt < 60; attempt += 1) {
        const result = await api.systemChecks(refresh && attempt === 0, deep)
        if (!result.loading) return result
        await new Promise((resolve) => window.setTimeout(resolve, 500))
      }
      throw new Error('Ứng dụng chuẩn bị quá lâu. Vui lòng mở lại APP.')
    })()
      .then(setChecks)
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
            apiKey: c.apiKey || '',
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
    let errCount = 0
    let timerId: number

    const syncInstall = async () => {
      try {
        const st = await api.installStatus()
        if (cancelled) return
        errCount = 0  // reset on success
        if (st.running && st.kind) {
          setInstalling(st.kind)
          setMsg(`Đang cài ${installLabel(st.kind)}…`)
        }
      } catch {
        /* backend chưa sẵn sàng — backoff */
        errCount++
      }
      if (cancelled) return
      // backoff: 2s → 4s → 8s → dừng sau 5 lỗi liên tiếp
      if (errCount >= 5) return
      const delay = errCount > 0 ? Math.min(2000 * Math.pow(2, errCount - 1), 16000) : 2000
      timerId = window.setTimeout(() => void syncInstall(), delay)
    }

    void syncInstall()
    return () => {
      cancelled = true
      window.clearTimeout(timerId)
    }
  }, [open, section])

  const cur = draft[tab]
  const canClose = !forceSetup || !!checks?.ok

  const installAction = useCallback(async (kind: InstallKind) => {
    setInstalling(kind)
    setInstallProgressMinimized(false)
    setInstallPopupError('')
    setInstallLog('')
    setChecksErr('')
    const onLog = (log: string) => setInstallLog(log)
    try {
      const result = kind === 'ai_runtime'
        ? await api.installAiRuntime(onLog)
        : kind === 'ocr_cuda'
          ? await api.installOcrCuda(onLog)
          : kind === 'demucs_cuda'
            ? await api.installDemucsCuda(onLog)
            : await api.installNvm(onLog)
      setMsg(result.detail || result.message)
      if (result.needsRestart) setPendingRestart(true)
      loadChecks(true, false)
      autoSetupLock.current = false
    } catch (e) {
      const message = e instanceof Error
          ? e.message
          : kind === 'ai_runtime'
            ? 'Cài gói AI thất bại'
            : kind === 'ocr_cuda'
            ? 'Cài GPU OCR thất bại'
            : kind === 'demucs_cuda'
              ? 'Cài Demucs thất bại'
              : 'Cài NVM + Node.js LTS thất bại'
      setChecksErr(message)
      setInstallPopupError(message)
      // ponytail: giữ lock=true khi fail — tránh auto-retry vô tận.
      // User phải bấm nút thủ công để thử lại.
    } finally {
      setInstalling(null)
    }
  }, [loadChecks])

  const restartApp = useCallback(async () => {
    if (restartRequested.current) return
    restartRequested.current = true
    setPendingRestart(false)
    setRestarting(true)
    setChecksErr('')
    try {
      await api.restartApp()
    } catch (e) {
      restartRequested.current = false
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
      if (forceSetup && checks.ok) {
        onSetupReady?.()
        return
      }
      const next = nextAutoInstall(checks)
      if (next) {
        autoSetupLock.current = true
        await installAction(next)
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

  useEffect(() => {
    if (!forceSetup || !pendingRestart || installing || restarting) return
    void restartApp()
  }, [forceSetup, pendingRestart, installing, restarting, restartApp])

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
      if (typed.length > 0) body.tts = { elevenlabs: { apiKeys: typed.join(',') } }

      const cfg = await api.saveConfig(body)
      const next = emptyCloud()
      for (const id of PROVIDERS) {
        const c = cfg.cloud?.[id]
        if (!c) continue
        next[id] = {
          apiKey: c.apiKey || '',
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
    >
      <div
        className={`cfg-modal cfg-modal-wide${section === 'setup' ? ' cfg-modal-setup' : ''}`}
        role="dialog"
        aria-modal
        aria-label={t('Cấu hình', 'Settings')}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="cfg-head">
          <div>
            <h2>{t('Cấu hình', 'Settings')}</h2>
            <p>
              {installing
                ? `Đang cài ${installLabel(installing)}…`
                : forceSetup && !checks?.ok
                  ? 'Cài đủ thành phần bắt buộc để bắt đầu'
                  : t('Thiết lập hệ thống · API dịch · ElevenLabs', 'System settings · Translation API · ElevenLabs')}
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
            {t('Thiết lập', 'Settings')}
            {checks && !checks.ok ? (
              <span className="cfg-dot cfg-dot-warn" title="Thiếu dependency" />
            ) : checks?.ok ? (
              <span className="cfg-dot" title={t('Sẵn sàng', 'Ready')} />
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
                className={section === 'license' ? 'active' : undefined}
                onClick={() => setSection('license')}
              >
                Kích hoạt
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
                    {checks.platform}
                    {checks.device?.accel
                      ? ` · ${String(checks.device.accel).toUpperCase()}`
                      : ''}
                    {checks.device?.gpuName ? ` · ${checks.device.gpuName}` : ''}
                    {checks.device?.vramMb ? ` · ${checks.device.vramMb} MB` : ''}
                  </span>
                ) : null}
              </div>
              <div className="cfg-setup-actions">
                {(checks?.device?.install?.actions?.length ?? 0) > 0
                  ? checks?.device?.install?.actions!.map((a) => {
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
                          onClick={() => {
                            autoSetupLock.current = false
                            void installAction(a.id as 'ocr_cuda' | 'demucs_cuda')
                          }}
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
                  {checksLoading ? '…' : t('Kiểm tra lại', 'Check again')}
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
              {(checks?.items || []).filter((it) => it.id !== 'device' && it.id !== 'httpx').map((it) => (
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
                      ['ai_runtime', 'ai_runtime_ocr', 'ai_runtime_vieneu', 'ocr_cuda', 'demucs_cuda', 'nvm'].includes(it.install) ? (
                        <span className="cfg-check-installed">{t('Đã cài', 'Installed')}</span>
                      ) : null
                    ) : ['ai_runtime', 'ai_runtime_ocr', 'ai_runtime_vieneu', 'ocr_cuda', 'demucs_cuda', 'nvm'].includes(it.install) ? (
                      <button
                        type="button"
                        className="cfg-check-install"
                        disabled={!!installing}
                        onClick={() => {
                          autoSetupLock.current = false
                          // ai_runtime_ocr / ai_runtime_vieneu → cùng endpoint ai_runtime
                          const kind = it.install.startsWith('ai_runtime')
                            ? 'ai_runtime'
                            : it.install as 'ocr_cuda' | 'demucs_cuda' | 'nvm'
                          void installAction(kind)
                        }}
                      >
                        {installing === it.install || (it.install.startsWith('ai_runtime') && installing === 'ai_runtime')
                          ? 'Đang cài…'
                          : it.installLabel ||
                            (it.install.startsWith('ai_runtime')
                              ? t('Cài gói AI', 'Install AI packages')
                              : it.install === 'demucs_cuda'
                              ? checks?.device?.install?.demucsLabel || t('Cài Demucs GPU', 'Install Demucs (GPU)')
                              : checks?.device?.install?.ocrLabel || t('Cài OCR CUDA', 'Install OCR (CUDA)'))}
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
                Backend tăng tốc và hướng dẫn cài được chọn theo thiết bị và runtime thực tế.
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
              {t('Chọn provider ở sidebar → Công cụ dịch. Key lưu ', 'Select a provider in the sidebar → Translation tools. Keys are stored in ')}
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
              {t(
                'Giọng ElevenLabs ở sidebar. Nhiều key → xoay khi 401/429. Để trống ô đã lưu = giữ nguyên; gõ key mới = thay / thêm.',
                'ElevenLabs voices are available in the sidebar. Multiple keys rotate after 401/429. Leave a saved field empty to keep it; enter a new key to replace or add one.',
              )}
            </p>
          </div>
        ) : section === 'license' ? (
          licenseStatus && onLicenseStatusChange ? <LicensePage status={licenseStatus} embedded onStatusChange={onLicenseStatusChange} /> : null
        ) : section === 'logs' ? (
          <div className="cfg-log-panel">
            <p className="cfg-hint">
              {t(
                'Lỗi job (Dịch / Lồng tiếng / Xuất), warm-models, crash hook. Copy gửi AI để sửa.',
                'Job errors (translation, dubbing, export), warm-models, and crash hooks. Copy this for AI troubleshooting.',
              )}
              {logPath ? (
                <>
                  {' '}
                  File: <code className="cfg-log-path">{logPath}</code>
                </>
              ) : null}
            </p>
            {logErr ? <p className="cfg-msg cfg-msg-err">{logErr}</p> : null}
            <pre className="cfg-log-pre" tabIndex={0}>
              {logLoading ? t('Đang tải…', 'Loading…') : logText || t('(trống)', '(empty)')}
            </pre>
            <div className="cfg-log-actions">
              <button type="button" className="cfg-secondary" disabled={logLoading} onClick={() => loadLogs()}>
                {logLoading ? t('Đang tải…', 'Loading…') : t('Tải lại', 'Reload')}
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
                {logCopied ? t('Đã copy', 'Copied') : 'Copy log'}
              </button>
              <button
                type="button"
                className="cfg-secondary"
                disabled={logLoading}
                onClick={() => {
                  if (!window.confirm(t('Xóa toàn bộ file log?', 'Delete all log files?'))) return
                  void api.clearAppLogs().then(() => loadLogs()).catch((e: Error) => setLogErr(e.message))
                }}
              >
                {t('Xóa log', 'Delete logs')}
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
                <span className="cfg-foot-note">Ứng dụng đang tự chuẩn bị các thành phần cần thiết</span>
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
                {checks?.ok ? t('Bắt đầu', 'Start') : checksLoading ? t('Đang chuẩn bị…', 'Preparing…') : t('Thử lại', 'Retry')}
              </button>
            </>
          ) : section === 'logs' || section === 'license' ? (
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
                  : installing === 'demucs_cuda'
                    ? 'Đang cài Demucs'
                    : 'Đang cài NVM + Node.js LTS'
          }
          message={
            installing
              ? `Đang cài ${installLabel(installing)}. Vui lòng không tắt ứng dụng.`
              : installPopupError || undefined
          }
          progress={installing ? 35 : 0}
          error={installPopupError || null}
          log={installLog || undefined}
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
