import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const isWin = process.platform === 'win32'
const isMac = process.platform === 'darwin'
const python = path.join(root, 'backend', '.venv', isWin ? 'Scripts/python.exe' : 'bin/python')
const dataSep = isWin ? ';' : ':'
const packageJsonPath = path.join(root, 'package.json')
const versionFilePath = path.join(root, 'build_app', 'VERSION')
// onedir = nhanh (Windows mặc định). ONEFILE=1 để gói 1 file (chậm vì bước PKG).
const oneFile = process.env.ONEFILE === '1' || process.env.ONEFILE === 'true'
const clean = process.env.CLEAN === '1' || process.env.CLEAN === 'true'

function run(command, args) {
  const result = spawnSync(command, args, { cwd: root, stdio: 'inherit', shell: false })
  if (result.status !== 0) process.exit(result.status ?? 1)
}

function pyOk(code) {
  const r = spawnSync(python, ['-c', code], { encoding: 'utf8', shell: false })
  return r.status === 0
}

function ensurePip(pkgs) {
  const missing = pkgs.filter((p) => {
    const mod = p === 'pywebview' ? 'webview' : p.replace(/-/g, '_')
    return !pyOk(`import ${mod}`)
  })
  if (missing.length) {
    run(python, ['-m', 'pip', 'install', '--upgrade', ...missing])
  }
}

function readPackage() {
  return JSON.parse(readFileSync(packageJsonPath, 'utf8'))
}

function parseSemver(v) {
  const m = String(v || '').trim().match(/^(\d+)\.(\d+)\.(\d+)/)
  if (!m) return { major: 1, minor: 0, patch: 0 }
  return { major: Number(m[1]), minor: Number(m[2]), patch: Number(m[3]) }
}

function formatSemver({ major, minor, patch }) {
  return `${major}.${minor}.${patch}`
}

function bumpPatch(version) {
  const s = parseSemver(version)
  s.patch += 1
  return formatSemver(s)
}

if (!existsSync(python)) {
  console.error('Thiếu backend/.venv. Chạy npm run setup trước.')
  process.exit(1)
}

const pkg = readPackage()
const appVersion = formatSemver(parseSemver(pkg.version || '1.0.0'))
writeFileSync(versionFilePath, `${appVersion}\n`, 'utf8')
console.log(`Building VideoClone v${appVersion} (${oneFile ? 'onefile' : 'onedir'}${clean ? ', clean' : ''})`)

run(isWin ? process.env.ComSpec || 'cmd.exe' : 'npm', isWin ? ['/d', '/s', '/c', 'npm run build'] : ['run', 'build'])

// Chỉ cài khi thiếu — không reinstall mỗi lần
ensurePip(['pyinstaller', 'uv', 'pywebview'])

const iconIco = path.join(root, 'build_app', 'app.ico')
const iconIcns = path.join(root, 'build_app', 'app.icns')

const args = [
  '-m', 'PyInstaller',
  '--noconfirm',
  ...(clean ? ['--clean'] : []),
  ...(oneFile ? ['--onefile'] : ['--onedir']),
  '--name', 'VideoClone',
  '--distpath', path.join(root, 'build_app', 'release'),
  '--workpath', path.join(root, 'build_app', '.work'),
  '--specpath', path.join(root, 'build_app'),
  '--paths', path.join(root, 'backend'),
  '--add-data', `${path.join(root, 'frontend', 'dist')}${dataSep}dist`,
  '--add-data', `${path.join(root, 'backend', 'pipeline', 'tts', 'voices_capcut.json')}${dataSep}pipeline/tts`,
  '--add-data', `${path.join(root, 'backend', 'resources', 'voice-ref')}${dataSep}resources/voice-ref`,
  '--add-data', `${versionFilePath}${dataSep}.`,
  ...(existsSync(iconIco) ? ['--add-data', `${iconIco}${dataSep}.`] : []),
  '--collect-all', 'webview',
]

// Các gói AI được cài vào %LOCALAPPDATA%/VideoClone/.venv-runtime ở lần mở đầu tiên.
for (const mod of [
  'faster_whisper', 'ctranslate2', 'tokenizers', 'huggingface_hub',
  'rapidocr_onnxruntime', 'onnxruntime', 'cv2', 'PIL', 'numpy',
  'torch', 'torchaudio', 'transformers', 'datasets', 'accelerate',
  'pandas', 'scipy', 'sklearn', 'tensorflow', 'soundfile', 'librosa',
  'pytest', 'lxml', 'pyarrow', 'matplotlib', 'sympy', 'numba', 'llvmlite',
  'vieneu', 'perth', 'sea_g2p', 'soxr',
]) args.push('--exclude-module', mod)

if (isWin && existsSync(iconIco)) args.push('--icon', iconIco)
else if (isMac && existsSync(iconIcns)) args.push('--icon', iconIcns)

if (isWin || isMac) args.push('--windowed')
if (isWin) {
  const sitePackages = spawnSync(python, ['-c', 'import site; print(site.getsitepackages()[-1])'], {
    encoding: 'utf8', shell: false,
  }).stdout.trim()
  args.push(
    '--additional-hooks-dir', path.join(sitePackages, 'pythonnet', '_pyinstaller'),
    '--collect-all', 'pythonnet',
    '--collect-all', 'clr_loader',
    '--hidden-import', 'clr',
  )
}

const uv = path.join(path.dirname(python), isWin ? 'uv.exe' : 'uv')
if (!existsSync(uv)) {
  console.error(`Không tìm thấy uv: ${uv}`)
  process.exit(1)
}
args.push('--add-binary', `${uv}${dataSep}.`)

for (const tool of ['ffmpeg', 'ffprobe']) {
  const found = spawnSync(isWin ? 'where.exe' : 'which', [tool], { encoding: 'utf8', shell: false })
  const binary = found.status === 0 ? found.stdout.trim().split(/\r?\n/)[0] : ''
  if (binary) args.push('--add-binary', `${binary}${dataSep}.`)
  else console.warn(`Cảnh báo: không tìm thấy ${tool} trên PATH.`)
}

args.push(path.join(root, 'build_app', 'launcher.py'))
run(python, args)

const releaseDir = path.join(root, 'build_app', 'release')
const verName = `VideoClone_v${appVersion}`
let output
if (oneFile) {
  const built = path.join(releaseDir, isWin ? 'VideoClone.exe' : isMac ? 'VideoClone.app' : 'VideoClone')
  output = path.join(releaseDir, isWin ? `${verName}.exe` : isMac ? `${verName}.app` : verName)
  if (existsSync(output)) rmSync(output, { recursive: true, force: true })
  if (existsSync(built)) renameSync(built, output)
} else {
  const builtDir = path.join(releaseDir, 'VideoClone')
  const outDir = path.join(releaseDir, verName)
  if (existsSync(outDir)) rmSync(outDir, { recursive: true, force: true })
  if (existsSync(builtDir)) renameSync(builtDir, outDir)
  output = path.join(outDir, isWin ? 'VideoClone.exe' : 'VideoClone')
}

const nextVersion = bumpPatch(appVersion)
const nextPkg = readPackage()
nextPkg.version = nextVersion
writeFileSync(packageJsonPath, `${JSON.stringify(nextPkg, null, 2)}\n`, 'utf8')
console.log(`\nBuild hoàn tất: ${output}`)
console.log(`Version: v${appVersion} → next build will be v${nextVersion}`)
if (!oneFile) {
  console.log(`Chạy cả thư mục release/${verName}/ (không copy riêng .exe).`)
}
