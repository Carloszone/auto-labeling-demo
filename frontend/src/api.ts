import axios, { AxiosError } from 'axios'

export type ReviewStatus = 'pending' | 'accepted' | 'rejected'

export interface ApiConfig {
  max_upload_bytes: number
  fps: number
  default_input_prompt: string
  pipeline_defaults: Record<string, any>
  capabilities: Record<string, boolean>
}

export interface JobSummary {
  job_id: string
  file_name: string
  file_names: string[]
  mcap_count: number
  file_size_bytes: number
  status: 'validating' | 'ready_to_run' | 'running' | 'ready' | 'failed'
  stage: string
  progress: number
  message: string
  duration_sec: number | null
  camera_count: number
  event_count: number
  pending_event_count: number
  accepted_event_count: number
  rejected_event_count: number
  vlm_completed_count: number
  vlm_total_count: number
  warnings: Array<Record<string, any>>
  error: { code: string; message: string } | null
  available_camera_topics: Array<{ camera_key: string; source_topic: string }>
  main_time_topic: string | null
  segment_manifest: Array<Record<string, any>>
  created_at: string
  completed_at: string | null
  updated_at: string
}

export interface CameraInfo {
  camera_key: string
  source_topic: string
  video_url: string | null
  duration_sec: number
  is_main_camera: boolean
  generation_status: 'ready' | 'failed'
  error: Record<string, any> | null
}

export interface EventView {
  id: string
  topic_key: string
  source_topic: string
  start_sec: number
  end_sec: number
  prompt: string
  description: string
  baseline_camera_key: string
  action_state: -1 | 0 | 1
  review_status: ReviewStatus
}

export interface AnomalyRange {
  anomaly_code: number | string
  anomaly_name: string
  start_sec: number
  end_sec: number
  topics: string
  descs: string[]
}

export interface JobResult {
  job_id: string
  duration_sec: number | null
  main_camera_key: string | null
  cameras: CameraInfo[]
  events: EventView[]
  data_anomaly_ranges: AnomalyRange[]
  image_anomaly_ranges: AnomalyRange[]
}

export interface RunRequest {
  input_prompt: string
  system_prompt: string
  robot_config_overrides: Record<string, any>
  parser_config: Record<string, any>
  data_check_config: Record<string, any>
  event_generation_config?: Record<string, any>
  event_labeling_config: Record<string, any>
}

export type EventPatch = Partial<Pick<EventView,
  'start_sec' | 'end_sec' | 'prompt' | 'description' | 'action_state' | 'review_status'
>>

const http = axios.create({ baseURL: '/api/v1', timeout: 30_000 })

export function apiError(error: unknown): string {
  const response = (error as AxiosError<any>)?.response?.data?.error
  if (response) return `${response.message}（${response.code}，request_id=${response.request_id || '-'}）`
  if (axios.isCancel(error)) return '请求已取消'
  return error instanceof Error ? error.message : '请求失败'
}

export const api = {
  getConfig: (signal?: AbortSignal) => http.get<ApiConfig>('/config', { signal }).then(r => r.data),
  getCurrentJob: (signal?: AbortSignal) => http.get<JobSummary>('/jobs/current', { signal }).then(r => r.data),
  getJobHistory: (signal?: AbortSignal) => http.get<JobSummary[]>('/jobs/history', { signal }).then(r => r.data),
  getJob: (jobId: string, signal?: AbortSignal) => http.get<JobSummary>(`/jobs/${jobId}`, { signal }).then(r => r.data),
  createJob: (
    mcaps: File[],
    robotConfig: File,
    onProgress: (value: number) => void,
    signal?: AbortSignal,
  ) => {
    const form = new FormData()
    for (const mcap of mcaps) form.append('mcaps', mcap)
    form.append('robot_config', robotConfig)
    return http.post<JobSummary>('/jobs', form, {
      signal,
      timeout: 0,
      onUploadProgress: event => {
        if (event.total) onProgress(Math.round(event.loaded / event.total * 100))
      },
    }).then(r => r.data)
  },
  runJob: (jobId: string, request: RunRequest) =>
    http.post<JobSummary>(`/jobs/${jobId}/run`, request).then(r => r.data),
  getResult: (jobId: string, signal?: AbortSignal) =>
    http.get<JobResult>(`/jobs/${jobId}/result`, { signal }).then(r => r.data),
  updateEvent: (jobId: string, eventId: string, patch: EventPatch) =>
    http.patch<EventView>(`/jobs/${jobId}/events/${eventId}`, patch).then(r => r.data),
  deleteJob: (jobId: string) => http.delete(`/jobs/${jobId}`),
  exportUrl: (jobId: string) => `/api/v1/jobs/${jobId}/export`,
}
