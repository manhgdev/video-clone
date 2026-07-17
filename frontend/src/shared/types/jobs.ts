import type { Step } from '@/features/project/project.types'

export type JobStatus = {
  step: Step | string
  progress: number
  message: string
  running: boolean
  error?: string
  outputRel?: string
  duration?: number
  workClipSec?: number
  bakedPreferVideo?: boolean
  bakedSpeed?: number
}
