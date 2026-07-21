/** fetch + timeout — tránh treo khi backend reload / đang cài gói */
export async function fetchJson<T>(
  url: string,
  init?: RequestInit,
  timeoutMs = 12_000,
): Promise<T> {
  const ac = new AbortController()
  const t = window.setTimeout(() => ac.abort(), timeoutMs)
  try {
    const res = await fetch(url, { ...init, signal: ac.signal })
    if (!res.ok) {
      const err = await res.text()
      throw new Error(err || res.statusText)
    }
    return res.json() as Promise<T>
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      const sec = Math.round(timeoutMs / 1000)
      throw new Error(`API timeout (${sec}s) — backend bận hoặc đang cài gói?`)
    }
    throw e
  } finally {
    window.clearTimeout(t)
  }
}
