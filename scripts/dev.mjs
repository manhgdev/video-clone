/** Chạy API :8787 + Vite :5173 — một terminal, Ctrl+C dừng cả hai. */
import { spawn, spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const serverDir = path.join(root, 'server')
const isWin = process.platform === 'win32'
const PORTS = [5173, 8787]

function venvPython() {
  const candidates = isWin
    ? [
        path.join(serverDir, '.venv', 'Scripts', 'python.exe'),
        path.join(root, '.venv', 'Scripts', 'python.exe'),
      ]
    : [
        path.join(serverDir, '.venv', 'bin', 'python3'),
        path.join(serverDir, '.venv', 'bin', 'python'),
        path.join(root, '.venv', 'bin', 'python3'),
      ]
  for (const p of candidates) {
    if (existsSync(p)) return p
  }
  return null
}

/** Quote 1 arg cho cmd.exe khi ghép chuỗi (tránh DEP0190: shell+args). */
function shQuote(arg) {
  const s = String(arg)
  if (!/[ \t"&<>|^()]/.test(s)) return s
  return `"${s.replace(/"/g, '""')}"`
}

function shellCmd(cmd, args) {
  return [cmd, ...args].map(shQuote).join(' ')
}

function hasCmd(cmd, args = ['--version']) {
  // npm/bun trên Windows là .cmd → cần shell; 1 chuỗi lệnh = không DEP0190
  if (isWin && (cmd === 'npm' || cmd === 'bun')) {
    return spawnSync(shellCmd(cmd, args), { stdio: 'ignore', shell: true }).status === 0
  }
  return spawnSync(cmd, args, { stdio: 'ignore', shell: false }).status === 0
}

/** Windows: Vite đôi khi LISTEN nhưng không trả HTML (xoay trang mãi) — kill trước khi start. */
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
      spawnSync('taskkill', ['/pid', pid, '/T', '/F'], { stdio: 'ignore' })
      console.log(`Đã giải phóng cổng ${port} (pid ${pid})`)
    }
  }
}

const py = venvPython() ?? (hasCmd('python') ? 'python' : hasCmd('python3') ? 'python3' : null)
if (!py) {
  console.error('Không tìm thấy python — cài Python 3 rồi thử lại.')
  process.exit(1)
}

// ponytail: trên Windows ưu tiên npm — bun đôi khi để Vite treo nhận TCP nhưng không trả trang
const webCmd = isWin ? (hasCmd('npm', ['--version']) ? 'npm' : 'bun') : hasCmd('bun', ['--version']) ? 'bun' : 'npm'
const webArgs = ['run', 'dev']

const children = []

function run(label, cmd, args, cwd) {
  const opts = { cwd, stdio: 'inherit', env: process.env }
  // Windows npm/bun: shell + 1 chuỗi (không truyền args → hết DEP0190)
  const child =
    isWin && (cmd === 'npm' || cmd === 'bun')
      ? spawn(shellCmd(cmd, args), { ...opts, shell: true })
      : spawn(cmd, args, { ...opts, shell: false })
  child.on('exit', (code, signal) => {
    if (signal) return
    if (code && code !== 0) {
      console.error(`[${label}] thoát mã ${code}`)
      shutdown(code ?? 1)
    }
  })
  children.push(child)
  return child
}

function shutdown(code = 0) {
  for (const c of children) {
    if (!c.pid) continue
    if (isWin) {
      spawnSync('taskkill', ['/pid', String(c.pid), '/T', '/F'], { stdio: 'ignore' })
    } else if (!c.killed) {
      c.kill('SIGTERM')
    }
  }
  freePorts(PORTS)
  process.exit(code)
}

process.on('SIGINT', () => shutdown(0))
process.on('SIGTERM', () => shutdown(0))

freePorts(PORTS)

console.log(`API  → http://127.0.0.1:8787  (${py} uvicorn)`)
console.log(`Web  → http://127.0.0.1:5173  (${webCmd} run dev)`)
console.log('Ctrl+C để dừng cả hai.\n')

run('api', py, ['-m', 'uvicorn', 'main:app', '--reload', '--host', '127.0.0.1', '--port', '8787'], serverDir)
run('web', webCmd, webArgs, root)
