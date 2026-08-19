import { spawnSync } from 'node:child_process'
import { createWriteStream, existsSync, readdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs'
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
const skipArchive = process.env.SKIP_ARCHIVE === '1' || process.env.SKIP_ARCHIVE === 'true'
const npmCommand = isWin ? process.env.ComSpec || 'cmd.exe' : 'npm'

function npmArgs(...args) {
  return isWin ? ['/d', '/s', '/c', `npm ${args.join(' ')}`] : args
}

function run(command, args, extraEnv = {}) {
  const result = spawnSync(command, args, {
    cwd: root,
    stdio: 'inherit',
    shell: false,
    env: { ...process.env, ...extraEnv },
  })
  if (result.status !== 0) process.exit(result.status ?? 1)
}

function cleanFrontendDist() {
  const dist = path.join(root, 'frontend', 'dist')
  if (!existsSync(dist)) return
  try {
    // Windows Defender/WebView can hold generated assets briefly after APP closes.
    rmSync(dist, { recursive: true, force: true, maxRetries: 12, retryDelay: 250 })
  } catch (error) {
    const stale = path.join(root, 'frontend', `.dist-stale-${process.pid}-${Date.now()}`)
    try {
      renameSync(dist, stale)
      console.warn(`frontend/dist đang bị Windows giữ khóa — đã chuyển bản cũ sang ${path.basename(stale)}.`)
    } catch {
      console.error('Không thể dọn frontend/dist. Hãy đóng cửa sổ VideoClone/Preview rồi chạy build lại.')
      console.error(error instanceof Error ? error.message : error)
      process.exit(1)
    }
  }
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
  // VideoClone uses decimal rollover per component (0..9), not unbounded SemVer minors:
  // 2.9.8 -> 2.9.9 -> 3.0.0 -> 3.0.1.
  const s = parseSemver(version)
  if (s.patch >= 9) {
    s.patch = 0
    s.minor += 1
    if (s.minor >= 10) {
      s.minor = 0
      s.major += 1
    }
  } else {
    s.patch += 1
  }
  return formatSemver(s)
}

for (const [input, expected] of [
  ['2.0.8', '2.0.9'],
  ['2.0.9', '2.1.0'],
  ['2.1.9', '2.2.0'],
  ['2.9.9', '3.0.0'],
]) {
  const got = bumpPatch(input)
  if (got !== expected) {
    console.error(`bumpPatch(${input}) = ${got}, expected ${expected}`)
    process.exit(1)
  }
}

const FF_MIN_BYTES = 2_000_000 // Chocolatey ShimGen ~400KB; Gyan/full là chục–trăm MB.

function findRealToolFile(dir, filename, depth = 0) {
  if (depth > 3 || !existsSync(dir)) return ''
  try {
    const direct = path.join(dir, filename)
    if (existsSync(direct) && statSync(direct).size >= FF_MIN_BYTES) return direct
    for (const ent of readdirSync(dir, { withFileTypes: true })) {
      if (!ent.isDirectory()) continue
      const hit = findRealToolFile(path.join(dir, ent.name), filename, depth + 1)
      if (hit) return hit
    }
  } catch {
    return ''
  }
  return ''
}

function resolveMediaTool(tool, { nextTo } = {}) {
  const exe = isWin ? `${tool}.exe` : tool
  const found = spawnSync(isWin ? 'where.exe' : 'which', [tool], { encoding: 'utf8', shell: false })
  const candidates = found.status === 0
    ? [...new Set(found.stdout.split(/\r?\n/).map((s) => s.trim()).filter(Boolean))]
    : []
  // Chocolatey: ffprobe.exe nằm cạnh ffmpeg.exe trong gói `ffmpeg`, không có gói `ffprobe`.
  if (nextTo) {
    const sibling = path.join(path.dirname(nextTo), exe)
    if (existsSync(sibling)) candidates.unshift(sibling)
  }
  const resolved = []
  for (const c of candidates) {
    if (!c || !existsSync(c)) continue
    let target = c
    try {
      if (statSync(c).size < FF_MIN_BYTES) {
        const dirs = [
          path.resolve(path.dirname(c), '..', 'lib', tool, 'tools'),
          path.resolve(path.dirname(c), '..', 'lib', 'ffmpeg', 'tools'),
        ]
        let hit = ''
        for (const dir of dirs) {
          hit = findRealToolFile(dir, exe)
          if (hit) break
        }
        if (!hit) continue
        target = hit
      }
      if (statSync(target).size >= FF_MIN_BYTES) resolved.push(target)
    } catch {
      continue
    }
  }
  resolved.sort((a, b) => statSync(b).size - statSync(a).size)
  return resolved[0] || ''
}

if (findRealToolFile(path.join(root, '__no_such_ffmpeg_tools__'), isWin ? 'ffmpeg.exe' : 'ffmpeg') !== '') {
  console.error('findRealToolFile false positive')
  process.exit(1)
}

if (!existsSync(python)) {
  console.error('Thiếu backend/.venv. Chạy npm run setup trước.')
  process.exit(1)
}

const pkg = readPackage()
const appVersion = formatSemver(parseSemver(pkg.version || '1.0.0'))
writeFileSync(versionFilePath, `${appVersion}\n`, 'utf8')
console.log(`Building VideoClone v${appVersion} (${oneFile ? 'onefile' : 'onedir'}${clean ? ', clean' : ''})`)

if (
  !existsSync(path.join(root, 'node_modules', '.bin', isWin ? 'tsc.cmd' : 'tsc')) ||
  !existsSync(path.join(root, 'node_modules', 'archiver'))
) {
  console.log('Thiếu dependency frontend — đang cài đặt...')
  run(npmCommand, npmArgs('install', '--no-package-lock'))
}
cleanFrontendDist()
run(npmCommand, npmArgs('run', 'build'))

// Pre-build validation
const distIndex = path.join(root, 'frontend', 'dist', 'index.html')
if (!existsSync(distIndex)) {
  console.error('Thiếu frontend/dist/index.html — chạy npm run build trước.')
  process.exit(1)
}

const ffmpegCheck = resolveMediaTool('ffmpeg')
if (!ffmpegCheck) {
  console.warn('Cảnh báo: ffmpeg thật không tìm thấy trên PATH (bỏ qua Chocolatey shim).')
}

// Chỉ cài khi thiếu — không reinstall mỗi lần
ensurePip(['pyinstaller', 'uv', 'pywebview'])

const iconIco = path.join(root, 'build_app', 'app.ico')
const iconIcns = path.join(root, 'build_app', 'app.icns')
if (isMac && !existsSync(iconIcns)) {
  console.error(`Thiếu icon macOS bắt buộc: ${iconIcns}`)
  process.exit(1)
}

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
  // ── Cross-platform data ────────────────────────────────────────────────────
  '--add-data', `${path.join(root, 'frontend', 'dist')}${dataSep}dist`,
  '--add-data', `${path.join(root, 'backend', 'pipeline')}${dataSep}pipeline`,
  '--add-data', `${path.join(root, 'backend', 'resources', 'voice-ref')}${dataSep}resources/voice-ref`,
  '--add-data', `${versionFilePath}${dataSep}.`,
  '--collect-all', 'webview',
  // Hidden imports: stdlib + third-party hay bị PyInstaller miss
  '--hidden-import', 'timeit',
  '--hidden-import', 'pickletools',
  '--hidden-import', 'filecmp',
  '--hidden-import', 'multiprocessing.synchronize',
  '--hidden-import', 'multiprocessing.pool',
  '--hidden-import', 'email.mime.text',
  '--hidden-import', 'email.mime.multipart',
  '--hidden-import', 'email.mime.base',
  '--hidden-import', 'email.encoders',
  '--hidden-import', 'httpx',
  '--hidden-import', 'setuptools',
  '--hidden-import', 'pkg_resources',
]

// Các gói AI được cài vào %LOCALAPPDATA%/VideoClone/.venv-runtime ở lần mở đầu tiên.
// Exclude thêm dev-only packages để giảm kích thước bundle.
for (const mod of [
  'faster_whisper', 'ctranslate2', 'tokenizers', 'huggingface_hub',
  'rapidocr_onnxruntime', 'onnxruntime', 'cv2', 'PIL', 'numpy',
  'torch', 'torchaudio', 'transformers', 'datasets', 'accelerate',
  'pandas', 'scipy', 'sklearn', 'tensorflow', 'soundfile', 'librosa',
  'pytest', 'unittest', 'doctest', 'pdb', 'profile', 'cProfile',
  'lxml', 'pyarrow', 'matplotlib', 'sympy', 'numba', 'llvmlite',
  'vieneu', 'perth', 'sea_g2p', 'soxr',
  'webview.platforms.android', 'pycparser.lextab', 'pycparser.yacctab',
  'IPython', 'ipykernel', 'notebook', 'jupyterlab',
]) args.push('--exclude-module', mod)

// ── Icon ──────────────────────────────────────────────────────────────────────
if (isWin && existsSync(iconIco)) {
  args.push('--icon', iconIco)
  args.push('--add-data', `${iconIco}${dataSep}.`)  // dùng trong webview tray
} else if (isMac && existsSync(iconIcns)) {
  args.push('--icon', iconIcns)
}

// ── Windowed (ẩn terminal) ────────────────────────────────────────────────────
if (isWin || isMac) args.push('--windowed')

// ── WINDOWS-SPECIFIC ─────────────────────────────────────────────────────────
if (isWin) {
  // pythonnet / clr_loader — Windows COM interop (không có trên macOS/Linux)
  const sitePackages = spawnSync(python, ['-c', 'import site; print(site.getsitepackages()[-1])'], {
    encoding: 'utf8', shell: false,
  }).stdout.trim()
  args.push(
    '--additional-hooks-dir', path.join(sitePackages, 'pythonnet', '_pyinstaller'),
    '--collect-all', 'pythonnet',
    '--collect-all', 'clr_loader',
    '--hidden-import', 'clr',
  )

  // python3.dll (stable ABI) — cần cho cv2.pyd trong .venv-runtime.
  // PyInstaller chỉ bundle python312.dll; cv2.pyd link với python3.dll.
  // _internal/ đã được bootloader đăng ký add_dll_directory → cv2 tìm thấy.
  // Trên macOS, dylib được resolve qua @rpath → không cần bước này.
  const whereResult = spawnSync('where.exe', ['python3.dll'], { encoding: 'utf8', shell: false })
  let py3dll = whereResult.status === 0 ? whereResult.stdout.trim().split(/\r?\n/)[0].trim() : ''
  if (!py3dll || !existsSync(py3dll)) {
    const basePy = spawnSync(python, [
      '-c', 'import sys, os; exe=getattr(sys,"_base_executable",sys.executable); print(os.path.join(os.path.dirname(exe),"python3.dll"))'
    ], { encoding: 'utf8', shell: false }).stdout.trim()
    if (basePy && existsSync(basePy)) py3dll = basePy
  }
  if (py3dll && existsSync(py3dll)) {
    args.push('--add-binary', `${py3dll}${dataSep}.`)
    console.log(`[win] Bundling python3.dll: ${py3dll}`)
  } else {
    console.warn('[win] Cảnh báo: không tìm thấy python3.dll — cv2.pyd có thể fail trong APP.')
  }
}

// ── MACOS-SPECIFIC ───────────────────────────────────────────────────────────
if (isMac) {
  // Bundle identifier cho .app (macOS yêu cầu reverse-DNS)
  args.push('--osx-bundle-identifier', 'com.videoclone.app')
  // Universal2: build cho cả Apple Silicon + Intel (nếu cross-compile được)
  // Bỏ comment dòng dưới nếu build trên máy hỗ trợ universal2:
  // args.push('--target-arch', 'universal2')
}

// ── Cross-platform: uv + ffmpeg/ffprobe ──────────────────────────────────────
const uv = path.join(path.dirname(python), isWin ? 'uv.exe' : 'uv')
if (!existsSync(uv)) {
  console.error(`Không tìm thấy uv: ${uv}`)
  process.exit(1)
}
args.push('--add-binary', `${uv}${dataSep}.`)
args.push('--upx-exclude', isWin ? 'ffmpeg.exe' : 'ffmpeg')
args.push('--upx-exclude', isWin ? 'ffprobe.exe' : 'ffprobe')

let bundledFfmpeg = ''
for (const tool of ['ffmpeg', 'ffprobe']) {
  const binary = resolveMediaTool(tool, { nextTo: bundledFfmpeg })
  if (binary && tool === 'ffmpeg') bundledFfmpeg = binary
  if (binary) {
    console.log(`[bundle] ${tool}: ${binary} (${(statSync(binary).size / 1024 / 1024).toFixed(1)} MB)`)
    args.push('--add-binary', `${binary}${dataSep}.`)
  } else {
    console.error(`Không tìm thấy ${tool} thật trên PATH (bỏ qua Chocolatey ShimGen ~400KB).`)
    process.exit(1)
  }
}

args.push(path.join(root, 'build_app', 'launcher.py'))
const buildHome = path.join(root, 'build_app', '.build-home')
run(python, args, {
  VIDEO_CLONE_HOME: buildHome,
  VIDEO_CLONE_DATA: path.join(buildHome, 'data'),
  VIDEO_CLONE_PUBLIC_DATA: path.join(buildHome, 'public_data'),
})

const releaseDir = path.join(root, 'build_app', 'release')
const verName = `VideoClone_v${appVersion}`
let output
let packageTarget = ''
if (oneFile) {
  const built = path.join(releaseDir, isWin ? 'VideoClone.exe' : isMac ? 'VideoClone.app' : 'VideoClone')
  output = path.join(releaseDir, isWin ? `${verName}.exe` : isMac ? `${verName}.app` : verName)
  if (existsSync(output)) rmSync(output, { recursive: true, force: true })
  if (existsSync(built)) renameSync(built, output)
  packageTarget = output
} else {
  const builtDir = path.join(releaseDir, 'VideoClone')
  const outDir = path.join(releaseDir, verName)
  if (existsSync(outDir)) rmSync(outDir, { recursive: true, force: true })
  if (existsSync(builtDir)) renameSync(builtDir, outDir)
  packageTarget = outDir
  output = path.join(outDir, isWin ? 'VideoClone.exe' : 'VideoClone')
}

if (packageTarget && !skipArchive) {
  const { default: archiver } = await import('archiver')
  const platform = isWin ? 'windows' : isMac ? 'macos' : 'linux'
  const archivePath = path.join(releaseDir, `${verName}-${platform}-${process.arch}.zip`)
  if (existsSync(archivePath)) rmSync(archivePath, { force: true })
  await new Promise((resolve, reject) => {
    const output = createWriteStream(archivePath)
    const archive = archiver('zip', { zlib: { level: 6 } })  // level 6: ~85% của 9 nhưng 3x nhanh
    output.on('close', resolve)
    output.on('error', reject)
    archive.on('error', reject)
    archive.pipe(output)
    if (statSync(packageTarget).isDirectory()) archive.directory(packageTarget, oneFile ? path.basename(packageTarget) : false)
    else archive.file(packageTarget, { name: path.basename(packageTarget) })
    archive.finalize()
  })
  console.log(`Bản ZIP: ${archivePath}`)
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
