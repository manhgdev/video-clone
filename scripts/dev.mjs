/** Chạy API :8787 + Vite :5173 — một terminal, Ctrl+C dừng cả hai.
 *  API crash (WinError 10055, reload worker…) KHÔNG tắt Vite / đóng terminal.
 */
import { spawn, spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const backendDir = path.join(root, 'backend')
const frontendDir = path.join(root, 'frontend')
const isWin = process.platform === 'win32'
const PORTS = [5173, 8787]

function venvPython() {
  const candidates = isWin
    ? [
        path.join(backendDir, '.venv', 'Scripts', 'python.exe'),
        path.join(root, '.venv', 'Scripts', 'python.exe'),
      ]
    : [
        path.join(backendDir, '.venv', 'bin', 'python3'),
        path.join(backendDir, '.venv', 'bin', 'python'),
        path.join(root, '.venv', 'bin', 'python3'),
      ]
  for (const p of candidates) {
    if (existsSync(p)) return p
  }
  return null
}

function shQuote(arg) {
  const s = String(arg)
  if (!/[ \t"&<>|^()]/.test(s)) return s
  return `"${s.replace(/"/g, '""')}"`
}

function shellCmd(cmd, args) {
  return [cmd, ...args].map(shQuote).join(' ')
}

function hasCmd(cmd, args = ['--version']) {
  if (isWin && (cmd === 'npm' || cmd === 'bun')) {
    return spawnSync(shellCmd(cmd, args), { stdio: 'ignore', shell: true }).status === 0
  }
  return spawnSync(cmd, args, { stdio: 'ignore', shell: false }).status === 0
}

/** Chỉ kill PID đang LISTEN cổng — không quét lan. */
function freePorts(ports) {
  if (!isWin) return
  for (const port of ports) {
    const out = spawnSync('cmd', ['/c', `netstat -ano | findstr :${port}`], {
      encoding: 'utf8',
    })
    const text = `${out.stdout || ''}${out.stderr || ''}`
    const pids = new Set()
    for (const line of text.split(/\r?\n/)) {
      if (!/LISTENING/i.test(line)) continue
      const m = line.trim().match(/(\d+)\s*$/)
      if (m) pids.add(m[1])
    }
    for (const pid of pids) {
      // Không /T — tránh giết nhầm parent shell / terminal VS Code
      spawnSync('taskkill', ['/pid', pid, '/F'], { stdio: 'ignore' })
      console.log(`Đã giải phóng cổng ${port} (pid ${pid})`)
    }
  }
}

const py = venvPython() ?? (hasCmd('python') ? 'python' : hasCmd('python3') ? 'python3' : null)
if (!py) {
  console.error('Không tìm thấy python — cài Python 3 rồi thử lại.')
  process.exitCode = 1
  // Không process.exit — để shell còn mở
  console.error('Giữ terminal. Sửa Python rồi chạy lại: npm start')
  process.stdin.resume()
} else {
  const webCmd = isWin
    ? hasCmd('npm', ['--version'])
      ? 'npm'
      : 'bun'
    : hasCmd('bun', ['--version'])
      ? 'bun'
      : 'npm'
  const webArgs = ['run', 'dev']

  /** @type {Map<string, import('node:child_process').ChildProcess>} */
  const children = new Map()
  let shuttingDown = false
  const restartAt = new Map() // label → last restart ms
  const RESTART_COOLDOWN_MS = 2500

  function spawnOne(label, cmd, args, cwd) {
    const opts = {
      cwd,
      stdio: 'inherit',
      env: process.env,
      // Windows: tách khỏi job object của terminal khi có thể
      detached: false,
    }
    const child =
      isWin && (cmd === 'npm' || cmd === 'bun')
        ? spawn(shellCmd(cmd, args), { ...opts, shell: true })
        : spawn(cmd, args, { ...opts, shell: false })

    children.set(label, child)

    child.on('error', (err) => {
      console.error(`[${label}] spawn error:`, err.message)
    })

    child.on('exit', (code, signal) => {
      children.delete(label)
      if (shuttingDown) return
      if (signal === 'SIGTERM' || signal === 'SIGINT') return

      const bad = code !== 0 && code != null
      if (bad) {
        console.error(`[${label}] thoát mã ${code} — KHÔNG tắt terminal / Vite.`)
      } else {
        console.log(`[${label}] đã dừng (0).`)
      }

      // Tự restart API (uvicorn crash / WinError 10055); Vite hiếm khi cần
      if (label === 'api' || (label === 'web' && bad)) {
        const now = Date.now()
        const last = restartAt.get(label) || 0
        if (now - last < RESTART_COOLDOWN_MS) {
          console.error(`[${label}] crash liên tục — chờ ${RESTART_COOLDOWN_MS}ms rồi thử lại…`)
        }
        const wait = Math.max(0, RESTART_COOLDOWN_MS - (now - last))
        setTimeout(() => {
          if (shuttingDown) return
          restartAt.set(label, Date.now())
          console.log(`[${label}] khởi động lại…`)
          if (label === 'api') freePorts([8787])
          if (label === 'web') freePorts([5173])
          spawnOne(label, cmd, args, cwd)
        }, wait + 400)
      }
    })

    return child
  }

  function shutdown() {
    if (shuttingDown) return
    shuttingDown = true
    console.log('\nĐang dừng API + Vite…')
    for (const [, c] of children) {
      if (!c.pid) continue
      try {
        if (isWin) {
          // Chỉ process con, không /T để tránh kill terminal host
          spawnSync('taskkill', ['/pid', String(c.pid), '/F'], { stdio: 'ignore' })
        } else if (!c.killed) {
          c.kill('SIGTERM')
        }
      } catch {
        /* ignore */
      }
    }
    // Thoát 0 — VS Code không báo "terminated with exit code 5"
    setTimeout(() => process.exit(0), 200)
  }

  process.on('SIGINT', shutdown)
  process.on('SIGTERM', shutdown)
  // Uncaught trong script dev không được sập im lặng
  process.on('uncaughtException', (err) => {
    console.error('[dev] uncaughtException:', err)
  })
  process.on('unhandledRejection', (err) => {
    console.error('[dev] unhandledRejection:', err)
  })

  freePorts(PORTS)

  console.log(`API  → http://127.0.0.1:8787  (${py} uvicorn)`)
  console.log(`Web  → http://127.0.0.1:5173  (${webCmd} run dev)`)
  console.log('Ctrl+C để dừng. API crash sẽ tự restart — terminal không đóng.\n')

  // Reload làm chậm boot (2 process) + hay miss worker trên Windows.
  // Bật lại: set VIDEO_CLONE_RELOAD=1
  const wantReload = /^(1|true|yes)$/i.test(String(process.env.VIDEO_CLONE_RELOAD || ''))
  const apiArgs = wantReload
    ? [
        '-m',
        'uvicorn',
        'main:app',
        '--reload',
        '--reload-dir',
        backendDir,
        '--reload-exclude',
        '.venv/*',
        '--reload-exclude',
        'data/*',
        '--reload-exclude',
        'public/*',
        '--host',
        '127.0.0.1',
        '--port',
        '8787',
      ]
    : ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8787']

  spawnOne('api', py, apiArgs, backendDir)
  if (!wantReload) {
    console.log('API reload tắt (nhanh hơn). VIDEO_CLONE_RELOAD=1 để bật.\n')
  }

  // /api/health — không import torch / engines (tránh đợi warm)
  const API_READY_URL = 'http://127.0.0.1:8787/api/health'
  const API_WAIT_MS = 30_000
  const API_POLL_MS = 200

  async function waitForApi() {
    const t0 = Date.now()
    const deadline = t0 + API_WAIT_MS
    let warned = false
    while (Date.now() < deadline) {
      if (shuttingDown) return false
      try {
        const res = await fetch(API_READY_URL, { signal: AbortSignal.timeout(800) })
        if (res.ok || res.status < 500) {
          console.log(`API sẵn sàng sau ${((Date.now() - t0) / 1000).toFixed(1)}s`)
          return true
        }
      } catch {
        if (!warned) {
          console.log('Đang chờ API sẵn sàng…')
          warned = true
        }
      }
      await new Promise((r) => setTimeout(r, API_POLL_MS))
    }
    console.error('API chưa sẵn sàng sau 30s — vẫn mở Vite (proxy có thể lỗi tạm).')
    return false
  }

  void waitForApi().then((ok) => {
    if (shuttingDown) return
    if (ok) console.log('Mở Vite.\n')
    spawnOne('web', webCmd, webArgs, root)
  })

  // Giữ event loop sống dù cả hai child die tạm thời (đang restart)
  setInterval(() => {}, 60_000)
}
