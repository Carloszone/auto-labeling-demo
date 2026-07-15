<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import type { AnomalyRange, EventPatch, EventView, ReviewStatus } from '../api'

const props = defineProps<{
  events: EventView[]
  dataRanges: AnomalyRange[]
  imageRanges: AnomalyRange[]
  currentTime: number
}>()
const emit = defineEmits<{
  save: [id: string, patch: EventPatch, done: (success: boolean) => void]
  review: [id: string, status: ReviewStatus]
  seek: [time: number]
  filter: [cameraKeys: string[]]
}>()

const selectedCameras = ref<string[]>([])
const activeTab = ref('events')
const selectedEventId = ref('')
const eventList = ref<HTMLElement | null>(null)
const drafts = reactive<Record<string, EventView>>({})
const dirty = reactive<Record<string, boolean>>({})
const anomalyStatuses = reactive<Record<string, ReviewStatus>>({})
const anomalyTopics = reactive<Record<'data' | 'image', string[]>>({ data: [], image: [] })

const cameras = computed(() => [...new Set(props.events.map(item => item.baseline_camera_key))])
const visibleEvents = computed(() => props.events.filter(item => !selectedCameras.value.length || selectedCameras.value.includes(item.baseline_camera_key)))
const activeEvent = computed(() => visibleEvents.value.find(item =>
  props.currentTime >= item.start_sec && props.currentTime <= item.end_sec,
))
const counts = computed(() => ({
  pending: props.events.filter(item => item.review_status === 'pending').length,
  accepted: props.events.filter(item => item.review_status === 'accepted').length,
  rejected: props.events.filter(item => item.review_status === 'rejected').length,
}))

watch(() => props.events, events => {
  for (const event of events) {
    if (!dirty[event.id]) drafts[event.id] = { ...event }
  }
}, { immediate: true, deep: true })
watch(selectedCameras, value => emit('filter', [...value]), { deep: true })
watch(() => activeEvent.value?.id, async id => {
  if (!id || activeTab.value !== 'events') return
  await nextTick()
  eventList.value?.querySelector<HTMLElement>(`[data-event-id="${CSS.escape(id)}"]`)
    ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
})

function markDirty(id: string) { dirty[id] = true }
function save(event: EventView) {
  const draft = drafts[event.id]
  emit('save', event.id, {
    start_sec: Number(draft.start_sec), end_sec: Number(draft.end_sec), prompt: draft.prompt,
    description: draft.description, action_state: draft.action_state,
  }, success => { if (success) dirty[event.id] = false })
}
function reset(event: EventView) {
  drafts[event.id] = { ...event }
  dirty[event.id] = false
}
function selectEvent(event: EventView, mouseEvent: MouseEvent) {
  const target = mouseEvent.target as HTMLElement
  if (target.closest('button, input, textarea, .el-select, .el-input-number')) return
  selectedEventId.value = event.id
  emit('seek', event.start_sec)
}
function anomalyKey(kind: 'data' | 'image', item: AnomalyRange) {
  return `${kind}:${item.topics}:${item.anomaly_code}:${item.start_sec}:${item.end_sec}`
}
function anomalyStatus(key: string) { return anomalyStatuses[key] || 'pending' }
function anomalyRanges(kind: 'data' | 'image') {
  const ranges = kind === 'data' ? props.dataRanges : props.imageRanges
  const topics = anomalyTopics[kind]
  return ranges.filter(item => !topics.length || topics.includes(item.topics))
}
function availableAnomalyTopics(kind: 'data' | 'image') {
  return [...new Set((kind === 'data' ? props.dataRanges : props.imageRanges).map(item => item.topics))]
}
function beforeUnload(event: BeforeUnloadEvent) {
  if (Object.values(dirty).some(Boolean)) event.preventDefault()
}
function hasUnsavedChanges() { return Object.values(dirty).some(Boolean) }
defineExpose({ hasUnsavedChanges })
window.addEventListener('beforeunload', beforeUnload)
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))
</script>

<template>
  <aside class="review-panel">
    <el-tabs v-model="activeTab" class="review-tabs">
      <el-tab-pane label="Event 复核" name="events" />
      <el-tab-pane label="数据异常复核" name="data" />
      <el-tab-pane label="图像异常复核" name="image" />
    </el-tabs>
    <template v-if="activeTab === 'events'">
    <div class="review-header">
      <div class="count-tags">
        <el-tag type="warning">待审 {{ counts.pending }}</el-tag>
        <el-tag type="success">接受 {{ counts.accepted }}</el-tag>
        <el-tag type="danger">舍弃 {{ counts.rejected }}</el-tag>
      </div>
      <el-checkbox-group v-model="selectedCameras" size="small">
        <el-checkbox-button v-for="camera in cameras" :key="camera" :value="camera">{{ camera }}</el-checkbox-button>
      </el-checkbox-group>
    </div>
    <el-empty v-if="!events.length" description="标注完成后将在此展示 Event" />
    <div ref="eventList" class="event-list">
      <el-card v-for="event in visibleEvents" :key="event.id" :data-event-id="event.id" class="event-card" :class="[event.review_status, { 'playback-active': activeEvent?.id === event.id, selected: selectedEventId === event.id }]" shadow="never" @click="selectEvent(event, $event)">
        <template #header>
          <button class="event-link" @click="selectedEventId = event.id; emit('seek', event.start_sec)">{{ event.id }} · {{ event.baseline_camera_key }}</button>
          <el-tag :type="event.review_status === 'accepted' ? 'success' : event.review_status === 'rejected' ? 'danger' : 'warning'">{{ event.review_status }}</el-tag>
        </template>
        <template v-if="drafts[event.id]">
          <div class="time-fields">
            <el-input-number v-model="drafts[event.id].start_sec" :min="0" :step="0.033333" :precision="6" @change="markDirty(event.id)" />
            <span>—</span>
            <el-input-number v-model="drafts[event.id].end_sec" :min="0" :step="0.033333" :precision="6" @change="markDirty(event.id)" />
          </div>
          <el-input v-model="drafts[event.id].prompt" placeholder="动作摘要" @input="markDirty(event.id)" />
          <el-input v-model="drafts[event.id].description" type="textarea" :rows="3" placeholder="详细描述" @input="markDirty(event.id)" />
          <el-select v-model="drafts[event.id].action_state" @change="markDirty(event.id)">
            <el-option label="成功 (1)" :value="1" /><el-option label="无法判断 (0)" :value="0" /><el-option label="失败 (-1)" :value="-1" />
          </el-select>
          <div class="event-actions">
            <el-button size="small" type="primary" :disabled="!dirty[event.id]" @click="save(event)">保存编辑</el-button>
            <el-button size="small" :disabled="!dirty[event.id]" @click="reset(event)">取消</el-button>
            <el-button size="small" type="success" @click="emit('review', event.id, 'accepted')">接受</el-button>
            <el-button size="small" type="danger" @click="emit('review', event.id, 'rejected')">舍弃</el-button>
            <el-button size="small" @click="emit('review', event.id, 'pending')">恢复待审</el-button>
          </div>
        </template>
      </el-card>
    </div>
    </template>
    <template v-else>
      <div class="review-header">
        <div class="count-tags">
          <el-tag type="warning">待审 {{ anomalyRanges(activeTab as 'data' | 'image').filter(item => anomalyStatus(anomalyKey(activeTab as 'data' | 'image', item)) === 'pending').length }}</el-tag>
        </div>
        <el-checkbox-group v-model="anomalyTopics[activeTab as 'data' | 'image']" size="small">
          <el-checkbox-button v-for="topic in availableAnomalyTopics(activeTab as 'data' | 'image')" :key="topic" :value="topic">{{ topic }}</el-checkbox-button>
        </el-checkbox-group>
      </div>
      <div class="event-list">
        <el-empty v-if="!anomalyRanges(activeTab as 'data' | 'image').length" description="没有异常片段" />
        <el-card v-for="item in anomalyRanges(activeTab as 'data' | 'image')" :key="anomalyKey(activeTab as 'data' | 'image', item)" class="event-card anomaly-card" :class="anomalyStatus(anomalyKey(activeTab as 'data' | 'image', item))" shadow="never">
          <template #header><strong>{{ item.anomaly_name }}</strong><el-tag>{{ anomalyStatus(anomalyKey(activeTab as 'data' | 'image', item)) }}</el-tag></template>
          <div>{{ item.start_sec.toFixed(3) }}s — {{ item.end_sec.toFixed(3) }}s</div>
          <div><strong>Topic：</strong>{{ item.topics }}</div>
          <div v-for="desc in item.descs" :key="desc">{{ desc }}</div>
          <div class="event-actions">
            <el-button size="small" type="success" @click="anomalyStatuses[anomalyKey(activeTab as 'data' | 'image', item)] = 'accepted'">接受</el-button>
            <el-button size="small" type="danger" @click="anomalyStatuses[anomalyKey(activeTab as 'data' | 'image', item)] = 'rejected'">舍弃</el-button>
            <el-button size="small" @click="anomalyStatuses[anomalyKey(activeTab as 'data' | 'image', item)] = 'pending'">恢复待审</el-button>
          </div>
        </el-card>
      </div>
    </template>
  </aside>
</template>
