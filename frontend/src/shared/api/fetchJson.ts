/** fetch + timeout — tránh treo khi backend reload / đang cài gói.
 *  Hỗ trợ AbortSignal ngoài (hủy tốc độ cũ khi chọn tốc độ mới).
 */
export async function fetchJson<T>(
  url: string,
  init?: RequestInit,
  timeoutMs = 12_000,
): Promise<T> {
  const ac = new AbortController()
  const external = init?.signal
  const onExtAbort = () => ac.abort()
  if (external) {
    if (external.aborted) ac.abort()
    else external.addEventListener('abort', onExtAbort)
  }
  const t = window.setTimeout(() => ac.abort(), timeoutMs)
  try {
    const { signal: _ignore, ...rest } = init || {}
    const res = await fetch(url, { ...rest, signal: ac.signal })
    if (!res.ok) {
      const err = await res.text()
      throw new Error(err || res.statusText)
    }
    return res.json() as Promise<T>
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      if (external?.aborted) throw e
      const sec = Math.round(timeoutMs / 1000)
      throw new Error(`API timeout (${sec}s) — backend bận hoặc đang cài gói?`)
    }
    throw e
  } finally {
    window.clearTimeout(t)
    external?.removeEventListener('abort', onExtAbort)
  }
}
