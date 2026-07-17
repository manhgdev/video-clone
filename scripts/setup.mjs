import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const isWin = process.platform === 'win32'
const venvPython = path.join(root, 'server', '.venv', isWin ? 'Scripts/python.exe' : 'bin/python')

function run(command, args) {
  const result = spawnSync(command, args, { cwd: root, stdio: 'inherit', shell: false })
  if (result.status !== 0) process.exit(result.status ?? 1)
}

function findPython() {
  for (const command of isWin ? ['python', 'py'] : ['python3', 'python']) {
    if (spawnSync(command, ['--version'], { stdio: 'ignore', shell: false }).status === 0) return command
  }
  console.error('Không tìm thấy Python 3. Hãy cài Python rồi chạy lại npm run setup.')
  process.exit(1)
}

console.log('Cài frontend dependencies...')
run(isWin ? process.env.ComSpec || 'cmd.exe' : 'npm', isWin ? ['/d', '/s', '/c', 'npm install'] : ['install'])

if (!existsSync(venvPython)) {
  console.log('Tạo server/.venv...')
  run(findPython(), ['-m', 'venv', path.join(root, 'server', '.venv')])
}

console.log('Cài backend dependencies...')
run(venvPython, ['-m', 'pip', 'install', '--upgrade', 'pip'])
run(venvPython, ['-m', 'pip', 'install', '-r', path.join(root, 'server', 'requirements.txt')])

console.log('\nĐã cài xong. Chạy: npm run dev:all')
