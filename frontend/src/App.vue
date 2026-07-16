<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type UploadFile, type UploadFiles, type UploadRawFile } from 'element-plus'
import { useJobStore } from './stores/job'
import { api, apiError, type EventPatch, type ReviewStatus, type RunRequest } from './api'
import MultiCameraPlayer from './components/MultiCameraPlayer.vue'
import AnnotationTimeline from './components/AnnotationTimeline.vue'
import EventReviewPanel from './components/EventReviewPanel.vue'

const store = useJobStore()
const mcapFiles = ref<File[]>([])
const robotFile = ref<File | null>(null)
const configOpen = ref(false)
const inputPrompt = ref('')
const systemPrompt = ref('')
const currentTime = ref(0)
const reviewPanel = ref<{ hasUnsavedChanges: () => boolean } | null>(null)
const player = ref<{ seek: (time: number) => void } | null>(null)
const selectedEventTopics = ref<string[]>([])
const settings = reactive({
  main_time_topic: '', max_tor_time_sec: 0,
  sudden_window_time_sec: 0, sudden_z_score: 0, sudden_time_sec: 0,
  step_time_sec: 0, zcr_ratio: 0, degree: 0, expansion_coef: 0, min_tor: 0,
  resize_length: 64, resize_width: 64, luminance: 0, image_window_time_sec: 0,
  lap_var: 0, image_z_score: 0, ssim: 0, pixel_mae: 0, moving_area_ratio: 0,
  min_low_quality_time_sec: 0, max_gap_time_sec: 0,
  fixed_frame_len: 1, context_frame_len: 0,
})
let appliedSettings = { ...settings }

const canUpload = computed(() => Boolean(mcapFiles.value.length && robotFile.value && !store.uploading && store.job?.status !== 'running'))
const canRun = computed(() => Boolean(store.job && store.job.job_id === store.currentJobId && ['ready_to_run', 'ready', 'failed'].includes(store.job.status)))
const duration = computed(() => store.result.duration_sec || store.job?.duration_sec || 0)
const timelineEvents = computed(() => store.result.events.filter(event =>
  !selectedEventTopics.value.length || selectedEventTopics.value.includes(event.baseline_camera_key),
))

function selectFile(kind: 'mcap' | 'robot', uploadFile: UploadFile, files: UploadFiles) {
  if (!uploadFile.raw) return
  if (kind === 'mcap') {
    const max = store.config?.max_upload_bytes || 50 * 1024 ** 3
    const selected = files.map(item => item.raw).filter((item): item is UploadRawFile => Boolean(item))
    if (selected.length > 32) {
      ElMessage.error('MCAP 文件数量超过限制')
      return
    }
    if (selected.reduce((sum, item) => sum + item.size, 0) > max) {
      ElMessage.error(`MCAP 文件总大小超过 ${(max / 1024 ** 3).toFixed(0)} GiB`)
      return
    }
    mcapFiles.value = selected
  } else {
    if (uploadFile.raw.size > 2 * 1024 ** 2) {
      ElMessage.error('Robot Config 超过 2 MiB')
      return
    }
    robotFile.value = uploadFile.raw
  }
}
function onMcapChange(file: UploadFile, files: UploadFiles) { selectFile('mcap', file, files) }
function onMcapRemove(_file: UploadFile, files: UploadFiles) {
  mcapFiles.value = files.map(item => item.raw).filter((item): item is UploadRawFile => Boolean(item))
}
function onRobotChange(file: UploadFile, files: UploadFiles) { selectFile('robot', file, files) }
async function upload() {
  if (!mcapFiles.value.length || !robotFile.value) return
  if (reviewPanel.value?.hasUnsavedChanges() && !await ElMessageBox.confirm('存在未保存的 Event 编辑，创建新工作项会丢失这些修改，是否继续？', '未保存修改', { type: 'warning' }).catch(() => false)) return
  if (store.job && !await ElMessageBox.confirm('创建新工作项会清理当前结果，是否继续？', '确认上传', { type: 'warning' }).catch(() => false)) return
  try {
    await store.createJob(mcapFiles.value, robotFile.value)
    selectedEventTopics.value = []
    inputPrompt.value ||= store.config?.default_input_prompt || ''
    settings.main_time_topic = store.job?.main_time_topic || ''
    appliedSettings = { ...settings }
    ElMessage.success('上传和校验完成')
  } catch { /* store already contains a detailed error */ }
}
function runRequest(): RunRequest {
  return {
    input_prompt: inputPrompt.value,
    system_prompt: systemPrompt.value,
    robot_config_overrides: { main_time_topic: settings.main_time_topic },
    parser_config: { insert: { max_tor_time_sec: settings.max_tor_time_sec } },
    data_check_config: {
      image_detection: {
        resize_length: settings.resize_length, resize_width: settings.resize_width,
        luminance: settings.luminance, window_time_sec: settings.image_window_time_sec, lap_var: settings.lap_var,
        z_score: settings.image_z_score, pixel_mae: settings.pixel_mae,
        SSIM: settings.ssim, moving_area_ratio: settings.moving_area_ratio,
      },
      data_detection: {
        sudden_change_config: {
          window_time_sec: settings.sudden_window_time_sec, z_score: settings.sudden_z_score,
          sudden_time_sec: settings.sudden_time_sec, step_time_sec: settings.step_time_sec,
          zcr_ratio: settings.zcr_ratio,
        },
        extreme_value_config: {
          degree: settings.degree, expansion_coef: settings.expansion_coef, min_tor: settings.min_tor,
        },
      },
      merge_policy: {
        min_low_quality_time_sec: settings.min_low_quality_time_sec,
        max_gap_time_sec: settings.max_gap_time_sec,
      },
    },
    event_labeling_config: {
      sampling: { params: { fixed_frame_len: settings.fixed_frame_len, context_frame_len: settings.context_frame_len } },
    },
  }
}
async function run() {
  if (!canRun.value) return
  if (reviewPanel.value?.hasUnsavedChanges() && !await ElMessageBox.confirm('重跑会丢失未保存的 Event 编辑，是否继续？', '未保存修改', { type: 'warning' }).catch(() => false)) return
  if (store.job?.status === 'ready' && !await ElMessageBox.confirm('重跑会清除当前人工复核结果，是否继续？', '确认重跑', { type: 'warning' }).catch(() => false)) return
  try { await store.run(runRequest()); ElMessage.success('自动标注已启动') } catch { /* handled by store */ }
}
async function saveEvent(id: string, patch: EventPatch, done: (success: boolean) => void) {
  try { await store.patchEvent(id, patch); done(true); ElMessage.success('Event 已保存') }
  catch (error) { done(false); ElMessage.error(apiError(error)) }
}
async function reviewEvent(id: string, status: ReviewStatus) {
  try { await store.patchEvent(id, { review_status: status }); ElMessage.success('复核状态已更新') }
  catch (error) { ElMessage.error(apiError(error)) }
}
function seek(time: number) {
  currentTime.value = time
  player.value?.seek(time)
}
function resetPrompt() { inputPrompt.value = store.config?.default_input_prompt || '' }
function resetSystemPrompt() {
  systemPrompt.value = store.config?.pipeline_defaults?.event_labeling_config?.vlm_params?.system_prompt || ''
}
function download() {
  if (!store.job) return
  if (!store.job.accepted_event_count && !window.confirm('当前没有 accepted event，仍然导出空结果吗？')) return
  window.location.href = api.exportUrl(store.job.job_id)
}
function onVisibility() { if (store.job?.status === 'running') store.schedulePoll(0) }
async function selectHistory(jobId: string) {
  if (!jobId || jobId === store.job?.job_id) return
  try { await store.selectJob(jobId) } catch (error) { ElMessage.error(apiError(error)) }
}

function loadDefaults() {
  const defaults = store.config?.pipeline_defaults
  if (!defaults) return
  const data = defaults.data_check_config
  const sudden = data.data_detection.sudden_change_config
  const extreme = data.data_detection.extreme_value_config
  const image = data.image_detection
  const merge = data.merge_policy
  const sample = defaults.event_labeling_config.sampling.params
  Object.assign(settings, {
    main_time_topic: store.job?.main_time_topic || '',
    max_tor_time_sec: defaults.parser_config.insert.max_tor_time_sec,
    sudden_window_time_sec: sudden.window_time_sec, sudden_z_score: sudden.z_score,
    sudden_time_sec: sudden.sudden_time_sec, step_time_sec: sudden.step_time_sec,
    zcr_ratio: sudden.zcr_ratio, degree: extreme.degree,
    expansion_coef: extreme.expansion_coef, min_tor: extreme.min_tor,
    resize_length: image.resize_length, resize_width: image.resize_width,
    luminance: image.luminance, image_window_time_sec: image.window_time_sec,
    lap_var: image.lap_var, image_z_score: image.z_score, ssim: image.SSIM,
    pixel_mae: image.pixel_mae, moving_area_ratio: image.moving_area_ratio,
    min_low_quality_time_sec: merge.min_low_quality_time_sec,
    max_gap_time_sec: merge.max_gap_time_sec,
    fixed_frame_len: sample.fixed_frame_len, context_frame_len: sample.context_frame_len,
  })
}
function openConfig() {
  Object.assign(settings, appliedSettings)
  configOpen.value = true
}
function applyConfig() {
  appliedSettings = { ...settings }
  configOpen.value = false
}
function cancelConfig() {
  Object.assign(settings, appliedSettings)
  configOpen.value = false
}
function restoreDefaults() {
  const mainTopic = settings.main_time_topic
  loadDefaults()
  settings.main_time_topic = mainTopic || store.job?.main_time_topic || ''
}

onMounted(async () => {
  await store.initialize()
  loadDefaults()
  inputPrompt.value = store.config?.default_input_prompt || ''
  resetSystemPrompt()
  settings.main_time_topic = store.job?.main_time_topic || ''
  appliedSettings = { ...settings }
  document.addEventListener('visibilitychange', onVisibility)
})
onBeforeUnmount(() => { store.stopPolling(); document.removeEventListener('visibilitychange', onVisibility) })
</script>

<template>
  <main class="page-shell">
    <el-alert title="内部算法演示 Demo，请勿上传敏感数据" type="warning" :closable="false" show-icon />
    <section class="toolbar panel">
      <div class="upload-row">
        <el-upload :auto-upload="false" :limit="32" multiple accept=".mcap" :disabled="store.uploading || store.job?.status === 'running'" :show-file-list="true" :on-change="onMcapChange" :on-remove="onMcapRemove">
          <el-button>选择 MCAP（可多选）</el-button>
        </el-upload>
        <el-upload :auto-upload="false" :limit="1" accept=".json" :disabled="store.uploading || store.job?.status === 'running'" :show-file-list="true" :on-change="onRobotChange">
          <el-button>选择 Robot Config</el-button>
        </el-upload>
        <el-button type="primary" :disabled="!canUpload" :loading="store.uploading" @click="upload">上传并创建</el-button>
        <el-button v-if="store.uploading" type="danger" @click="store.cancelUpload()">取消上传</el-button>
        <el-button :disabled="!store.job" @click="openConfig">参数配置</el-button>
        <el-button type="success" :disabled="!canRun" @click="run">开始自动标注</el-button>
        <el-button :disabled="store.job?.status !== 'ready'" @click="download">导出 JSON</el-button>
        <el-select :model-value="store.job?.job_id || ''" placeholder="历史标注 Job ID" style="width: 260px" @change="selectHistory">
          <el-option v-for="item in store.history" :key="item.job_id" :label="`${item.job_id} · ${item.file_name}`" :value="item.job_id" />
        </el-select>
      </div>
      <div class="prompt-row">
        <div class="prompt-heading">
          <strong>VLM User Prompt</strong>
          <span>作为每次 VLM 请求中 <code>input[0].content</code> 的整体任务描述</span>
          <el-button link type="primary" :disabled="store.job?.status === 'running'" @click="resetPrompt">恢复默认</el-button>
        </div>
        <el-input v-model="inputPrompt" type="textarea" :rows="3" maxlength="8000" show-word-limit :disabled="store.job?.status === 'running'" />
        <el-collapse class="prompt-details">
          <el-collapse-item title="编辑 VLM System Prompt">
            <div class="prompt-heading">
              <span>留空时由后端使用默认 System Prompt</span>
              <el-button link type="primary" :disabled="store.job?.status === 'running'" @click="resetSystemPrompt">恢复默认</el-button>
            </div>
            <el-input v-model="systemPrompt" type="textarea" :rows="10" maxlength="20000" show-word-limit :disabled="store.job?.status === 'running'" />
          </el-collapse-item>
        </el-collapse>
      </div>
      <el-progress v-if="store.uploading" :percentage="store.uploadProgress" :stroke-width="12" />
      <div v-if="store.job" class="job-status">
        <strong>{{ store.job.job_id }}</strong><span>{{ store.job.mcap_count }} 个 MCAP · {{ store.job.file_names?.join('、') || store.job.file_name }}</span>
        <el-tag>{{ store.job.status }}</el-tag><span>{{ store.job.message }}</span>
        <el-progress :percentage="store.job.progress" :status="store.job.status === 'failed' ? 'exception' : store.job.status === 'ready' ? 'success' : undefined" />
      </div>
      <div v-if="store.job?.segment_manifest?.length" class="segment-manifest">
        <strong>按 MCAP 主相机时间戳确定的处理顺序：</strong>
        <el-tag v-for="(segment, index) in store.job.segment_manifest" :key="segment.source_mcap" type="info">
          {{ index + 1 }}. {{ segment.source_mcap }} · 接受 {{ segment.accepted_frame_count }} 帧 · 重叠丢弃 {{ segment.overlap_dropped_frame_count }} 帧
        </el-tag>
      </div>
      <el-alert v-if="store.error" :title="store.error" type="error" show-icon :closable="false" />
      <el-alert v-if="store.job?.error" :title="`${store.job.error.message}（${store.job.error.code}）`" type="error" show-icon :closable="false" />
      <el-alert v-for="warning in store.job?.warnings || []" :key="`${warning.code}-${warning.camera_key || ''}`" :title="warning.message" type="warning" show-icon :closable="false" />
    </section>

    <section class="workspace">
      <div class="media-column panel">
        <MultiCameraPlayer ref="player" :cameras="store.result.cameras" :duration="duration" :main-camera-key="store.result.main_camera_key" @time="value => currentTime = value" />
        <AnnotationTimeline :duration="duration" :current-time="currentTime" :events="timelineEvents" :data-ranges="store.result.data_anomaly_ranges" :image-ranges="store.result.image_anomaly_ranges" @select="event => seek(event.start_sec)" />
      </div>
      <EventReviewPanel ref="reviewPanel" :events="store.result.events" :data-ranges="store.result.data_anomaly_ranges" :image-ranges="store.result.image_anomaly_ranges" :current-time="currentTime" @save="saveEvent" @review="reviewEvent" @seek="seek" @filter="topics => selectedEventTopics = topics" />
    </section>

    <el-drawer v-model="configOpen" title="算法参数" size="520px">
      <el-form label-position="top">
        <el-form-item label="主时间 Topic">
          <el-select v-model="settings.main_time_topic" filterable>
            <el-option v-for="camera in store.job?.available_camera_topics || []" :key="camera.source_topic" :label="`${camera.camera_key} · ${camera.source_topic}`" :value="camera.source_topic" />
          </el-select>
        </el-form-item>
        <div class="config-grid">
          <el-form-item label="对齐容差 (s)"><el-input-number v-model="settings.max_tor_time_sec" :min="0" :step="0.01" /></el-form-item>
          <el-form-item label="突变窗口 (s)"><el-input-number v-model="settings.sudden_window_time_sec" :min="0" :step="0.01" /></el-form-item>
          <el-form-item label="突变 Z-score"><el-input-number v-model="settings.sudden_z_score" :min="0" :step="0.1" /></el-form-item>
          <el-form-item label="突变时长 (s)"><el-input-number v-model="settings.sudden_time_sec" :min="0" :step="0.01" /></el-form-item>
          <el-form-item label="台阶时长 (s)"><el-input-number v-model="settings.step_time_sec" :min="0" :step="0.01" /></el-form-item>
          <el-form-item label="ZCR 比例"><el-input-number v-model="settings.zcr_ratio" :min="0" :max="1" :step="0.01" /></el-form-item>
          <el-form-item label="极值 Degree"><el-input-number v-model="settings.degree" :min="0" :max="1" :step="0.01" /></el-form-item>
          <el-form-item label="极值扩展系数"><el-input-number v-model="settings.expansion_coef" :min="0" :step="0.01" /></el-form-item>
          <el-form-item label="极值最小容差"><el-input-number v-model="settings.min_tor" :min="0" :step="0.0001" /></el-form-item>
          <el-form-item label="Resize 长"><el-input-number v-model="settings.resize_length" :min="64" :max="4096" /></el-form-item>
          <el-form-item label="Resize 宽"><el-input-number v-model="settings.resize_width" :min="64" :max="4096" /></el-form-item>
          <el-form-item label="亮度阈值"><el-input-number v-model="settings.luminance" :min="0" /></el-form-item>
          <el-form-item label="图像窗口 (s)"><el-input-number v-model="settings.image_window_time_sec" :min="0" :step="0.1" /></el-form-item>
          <el-form-item label="Lap 方差"><el-input-number v-model="settings.lap_var" :min="0" /></el-form-item>
          <el-form-item label="图像 Z-score"><el-input-number v-model="settings.image_z_score" :min="0" :step="0.1" /></el-form-item>
          <el-form-item label="SSIM"><el-input-number v-model="settings.ssim" :min="0" :max="1" :step="0.01" /></el-form-item>
          <el-form-item label="像素 MAE"><el-input-number v-model="settings.pixel_mae" :min="0" /></el-form-item>
          <el-form-item label="运动区域比例"><el-input-number v-model="settings.moving_area_ratio" :min="0" :max="1" :step="0.01" /></el-form-item>
          <el-form-item label="最短低质量区间 (s)"><el-input-number v-model="settings.min_low_quality_time_sec" :min="0" :step="0.01" /></el-form-item>
          <el-form-item label="最大合并间隔 (s)"><el-input-number v-model="settings.max_gap_time_sec" :min="0" :step="0.01" /></el-form-item>
          <el-form-item label="Event 图片数"><el-input-number v-model="settings.fixed_frame_len" :min="1" :max="20" /></el-form-item>
          <el-form-item label="上下文图片数"><el-input-number v-model="settings.context_frame_len" :min="0" :max="10" /></el-form-item>
        </div>
        <el-button type="primary" @click="applyConfig">应用</el-button>
        <el-button @click="restoreDefaults">恢复默认</el-button>
        <el-button @click="cancelConfig">取消</el-button>
      </el-form>
    </el-drawer>
  </main>
</template>
