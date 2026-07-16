import { defineStore } from 'pinia'
import { api, apiError, type ApiConfig, type EventPatch, type JobResult, type JobSummary, type RunRequest } from '../api'

const EMPTY_RESULT: JobResult = {
  job_id: '', duration_sec: null, main_camera_key: null,
  cameras: [], events: [], data_anomaly_ranges: [], image_anomaly_ranges: [],
}

export const useJobStore = defineStore('job', {
  state: () => ({
    config: null as ApiConfig | null,
    job: null as JobSummary | null,
    currentJobId: '' as string,
    history: [] as JobSummary[],
    result: { ...EMPTY_RESULT } as JobResult,
    uploadProgress: 0,
    uploading: false,
    loading: false,
    error: '',
    uploadController: null as AbortController | null,
    pollTimer: 0 as number,
    failureCount: 0,
  }),
  actions: {
    async initialize() {
      this.error = ''
      try {
        this.config = await api.getConfig()
        await this.restore()
        await this.loadHistory()
      } catch (error) {
        this.error = apiError(error)
      }
    },
    async restore() {
      try {
        const job = await api.getCurrentJob()
        this.job = job
        this.currentJobId = job.job_id
        sessionStorage.setItem('current_job_id', job.job_id)
        if (job.progress >= 40 || job.status === 'ready') await this.loadResult(job.job_id)
        if (job.status === 'running') this.schedulePoll()
      } catch (error: any) {
        if (error?.response?.status === 404) {
          sessionStorage.removeItem('current_job_id')
          return
        }
        throw error
      }
    },
    async createJob(mcaps: File[], robotConfig: File) {
      this.stopPolling()
      this.uploading = true
      this.uploadProgress = 0
      this.error = ''
      this.uploadController = new AbortController()
      try {
        const job = await api.createJob(
          mcaps, robotConfig, value => { this.uploadProgress = value }, this.uploadController.signal,
        )
        this.job = job
        this.currentJobId = job.job_id
        this.result = { ...EMPTY_RESULT, job_id: job.job_id }
        sessionStorage.setItem('current_job_id', job.job_id)
      } catch (error) {
        this.error = apiError(error)
        throw error
      } finally {
        this.uploading = false
        this.uploadController = null
      }
    },
    cancelUpload() {
      this.uploadController?.abort()
    },
    async run(request: RunRequest) {
      if (!this.job) return
      this.error = ''
      try {
        this.job = await api.runJob(this.job.job_id, request)
        this.result = { ...EMPTY_RESULT, job_id: this.job.job_id }
        this.schedulePoll(0)
      } catch (error) {
        this.error = apiError(error)
        throw error
      }
    },
    async refresh() {
      if (!this.job) return
      const expected = this.job.job_id
      try {
        const job = await api.getJob(expected)
        if (this.job?.job_id !== expected) return
        this.job = job
        this.failureCount = 0
        if (job.progress >= 40 || job.status === 'ready') await this.loadResult(expected)
        if (job.status === 'running') this.schedulePoll()
        if (job.status === 'ready') await this.loadHistory()
      } catch (error) {
        this.failureCount += 1
        this.error = apiError(error)
        if (this.failureCount < 10) this.schedulePoll()
      }
    },
    schedulePoll(delay?: number) {
      this.stopPolling()
      const backoff = this.failureCount < 3 ? (document.hidden ? 5000 : 1000) : Math.min(15_000, 2 ** (this.failureCount - 2) * 1000)
      this.pollTimer = window.setTimeout(() => this.refresh(), delay ?? backoff)
    },
    stopPolling() {
      if (this.pollTimer) window.clearTimeout(this.pollTimer)
      this.pollTimer = 0
    },
    async loadResult(jobId?: string) {
      const expected = jobId || this.job?.job_id
      if (!expected) return
      const result = await api.getResult(expected)
      if (this.job?.job_id === expected) this.result = result
    },
    async loadHistory() {
      this.history = await api.getJobHistory()
    },
    async selectJob(jobId: string) {
      this.stopPolling()
      this.error = ''
      this.job = await api.getJob(jobId)
      await this.loadResult(jobId)
      sessionStorage.setItem('current_job_id', jobId)
    },
    async patchEvent(eventId: string, patch: EventPatch) {
      if (!this.job) return
      const updated = await api.updateEvent(this.job.job_id, eventId, patch)
      const index = this.result.events.findIndex(item => item.id === eventId)
      if (index >= 0) this.result.events[index] = updated
      await this.refreshSummaryOnly()
    },
    async refreshSummaryOnly() {
      if (this.job) this.job = await api.getJob(this.job.job_id)
    },
    async removeJob() {
      if (!this.job) return
      await api.deleteJob(this.job.job_id)
      this.stopPolling()
      this.job = null
      this.currentJobId = ''
      this.result = { ...EMPTY_RESULT }
      await this.loadHistory()
      sessionStorage.removeItem('current_job_id')
    },
  },
})
