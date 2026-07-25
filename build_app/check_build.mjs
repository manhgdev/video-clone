/**
 * check_build.mjs — Kiểm tra build output sau khi PyInstaller xong.
 * Chạy: node build_app/check_build.mjs [version]
 */
import { existsSync, readdirSync, statSync, readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const isWin = process.platform === 'win32'
const releaseDir = path.join(root, 'build_app', 'release')

// Đọc version từ arg hoặc package.json
const pkg = JSON.parse(readFileSync(path.join(root, 'package.json'), 'utf8'))
const version = process.argv[2] || pkg.version.match(/^\d+\.\d+\.\d+/)?.[0] || '0.0.0'
const verName = `VideoClone_v${version}`
const distDir = path.join(releaseDir, verName)
const exePath = path.join(distDir, isWin ? 'VideoClone.exe' : 'VideoClone')

let ok = true
function check(label, pass, detail = '') {
  const icon = pass ? '✓' : '✗'
  console.log(`  ${icon} ${label}${detail ? ': ' + detail : ''}`)
  if (!pass) ok = false
}
function size(p) {
  try {
    const s = statSync(p).size
    if (s > 1024 * 1024) return `${(s / 1024 / 1024).toFixed(1)} MB`
    return `${(s / 1024).toFixed(0)} KB`
  } catch { return '?' }
}
function dirSize(dir) {
  if (!existsSync(dir)) return '?'
  let total = 0
  function walk(d) {
    for (const f of readdirSync(d, { withFileTypes: true })) {
      const fp = path.join(d, f.name)
      if (f.isDirectory()) walk(fp)
      else try { total += statSync(fp).size } catch {}
    }
  }
  walk(dir)
  return `${(total / 1024 / 1024).toFixed(1)} MB`
}

console.log(`\nVideoClone Build Check — v${version}`)
console.log(`Release: ${distDir}\n`)

// 1. Thư mục release tồn tại
check('Release dir exists', existsSync(distDir))

// 2. EXE chính
check('VideoClone.exe', existsSync(exePath), size(exePath))

// 3. dist/index.html (frontend build đã được pack)
const internalDir = existsSync(path.join(distDir, '_internal')) ? path.join(distDir, '_internal') : distDir

// 3. dist/index.html (frontend build đã được pack)
const distIndex = path.join(internalDir, 'dist', 'index.html')
check('dist/index.html', existsSync(distIndex))
check(
  'bundled caption font',
  existsSync(path.join(internalDir, 'dist', 'fonts', 'NotoSans-Bold.ttf')),
)

// 4. ffmpeg
const ffmpeg = path.join(internalDir, isWin ? 'ffmpeg.exe' : 'ffmpeg')
check('ffmpeg bundled', existsSync(ffmpeg), size(ffmpeg))

// 5. ffprobe
const ffprobe = path.join(internalDir, isWin ? 'ffprobe.exe' : 'ffprobe')
check('ffprobe bundled', existsSync(ffprobe), size(ffprobe))

// 6. uv
const uv = path.join(internalDir, isWin ? 'uv.exe' : 'uv')
check('uv bundled', existsSync(uv), size(uv))

// 7. pipeline directory (app logic)
const pipeDir = path.join(internalDir, 'pipeline')
check('pipeline/ dir', existsSync(pipeDir))

// 8. resources/voice-ref
const voiceRef = path.join(internalDir, 'resources', 'voice-ref')
check('voice-ref', existsSync(voiceRef))

// 9. VERSION file
const versionFile = path.join(internalDir, 'VERSION')
check('VERSION file', existsSync(versionFile),
  existsSync(versionFile) ? readFileSync(versionFile, 'utf8').trim() : '')

// 10. ZIP archive
const platform = isWin ? 'windows' : process.platform === 'darwin' ? 'macos' : 'linux'
const zipPath = path.join(releaseDir, `${verName}-${platform}-${process.arch}.zip`)
check('ZIP archive', existsSync(zipPath), size(zipPath))

// Summary
console.log(`\nTổng kích thước: ${dirSize(distDir)}`)
if (ok) {
  console.log('\n✓ Build OK\n')
  process.exit(0)
} else {
  console.error('\n✗ Build có vấn đề — xem các mục ✗ trên\n')
  process.exit(1)
}
