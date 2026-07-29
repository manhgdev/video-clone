import { useState } from 'react'
import { licenseApi, type LicenseStatus } from './license.api'
import './LicensePage.css'

type Props = {
  status: LicenseStatus
  gate?: boolean
  onStatusChange: (status: LicenseStatus) => void
}

function errorText(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error)
  try {
    const parsed = JSON.parse(raw)
    return parsed.detail || parsed.message || raw
  } catch {
    return raw
  }
}

export default function LicensePage({ status, gate = false, onStatusChange }: Props) {
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function activate() {
    if (!key.trim() || busy) return
    setBusy(true)
    setError('')
    try {
      const next = await licenseApi.activate(key.trim())
      onStatusChange(next)
      setKey('')
    } catch (exc) {
      setError(errorText(exc))
    } finally {
      setBusy(false)
    }
  }

  const expiry = status.expiresAt
    ? new Date(status.expiresAt).toLocaleString('vi-VN')
    : status.remainingDay === -1 ? 'Không giới hạn' : '—'

  return (
    <main className={`license-page${gate ? ' license-gate' : ''}`}>
      <section className="license-card">
        <div className="license-brand">
          <strong>ZM TOOL</strong>
          <span>Kích hoạt bản quyền sử dụng</span>
        </div>
        <div className={`license-state${status.valid ? ' is-valid' : ' is-invalid'}`}>
          <strong>{status.valid ? 'Đã kích hoạt' : 'Chưa kích hoạt'}</strong>
          <span>{status.message}</span>
        </div>
        {status.configured && (
          <dl className="license-details">
            <div><dt>Key</dt><dd>{status.keyMasked}</dd></div>
            <div><dt>Thời hạn</dt><dd>{status.remainingDay === -1 ? 'Không giới hạn' : `Còn ${status.remainingDay} ngày`}</dd></div>
            <div><dt>Hết hạn</dt><dd>{expiry}</dd></div>
            <div><dt>Lượt kích hoạt còn lại</dt><dd>{status.activationLimit}</dd></div>
          </dl>
        )}
        <label className="license-input-label" htmlFor="license-key">
          {status.valid ? 'Nhập key khác' : 'Nhập key ZM Tool để tiếp tục'}
        </label>
        <div className="license-form">
          <input
            id="license-key"
            value={key}
            onChange={(event) => setKey(event.target.value)}
            onKeyDown={(event) => { if (event.key === 'Enter') void activate() }}
            placeholder="Nhập key kích hoạt"
            autoComplete="off"
            disabled={busy}
          />
          <button type="button" onClick={() => void activate()} disabled={busy || !key.trim()}>
            {busy ? 'Đang kiểm tra…' : 'Kích hoạt'}
          </button>
        </div>
        {error && <p className="license-error">{error}</p>}
      </section>
    </main>
  )
}
