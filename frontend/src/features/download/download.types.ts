export type DownloadQuality = 'best' | '2160' | '1440' | '1080' | '720' | '480' | 'audio'

export type DownloadFormat = 'mp4' | 'mkv' | 'webm' | 'mp3'

export type DownloadOpts = {
  format: DownloadFormat
  writeSubs: boolean
  writeInfoJson: boolean
  writeThumbnail: boolean
  mergeAv: boolean
  preferFreeFormats: boolean
  folderBySource: boolean
}

export type DownloadJob = {
  id: string
  url: string
  title?: string
  quality: DownloadQuality
  format?: DownloadFormat
  status: 'queued' | 'running' | 'done' | 'error'
  progress: number
  message?: string
  outputPath?: string
  downloadUrl?: string
  createdAt: string
  log?: string[]
}

export type JobFilter = 'all' | 'active' | 'done' | 'error'
