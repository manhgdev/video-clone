export type DownloadQuality = 'best' | '1080' | '720' | '480' | 'audio'

export type DownloadJob = {
  id: string
  url: string
  title?: string
  quality: DownloadQuality
  status: 'queued' | 'running' | 'done' | 'error'
  progress: number
  message?: string
  outputPath?: string
  createdAt: string
}
