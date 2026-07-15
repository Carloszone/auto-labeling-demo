<script setup lang="ts">
import type { AnomalyRange, EventView } from '../api'

const props = defineProps<{
  duration: number
  currentTime: number
  events: EventView[]
  dataRanges: AnomalyRange[]
  imageRanges: AnomalyRange[]
}>()
const emit = defineEmits<{ select: [event: EventView] }>()

function style(start: number, end: number) {
  const duration = Math.max(props.duration, 0.001)
  return { left: `${start / duration * 100}%`, width: `${Math.max((end - start) / duration * 100, 0.2)}%` }
}
</script>

<template>
  <div class="timeline">
    <div class="timeline-cursor-layer">
      <div class="timeline-cursor" :style="{ left: `${currentTime / Math.max(duration, 0.001) * 100}%` }" />
    </div>
    <div class="track"><label>一级</label><div class="track-body placeholder">暂未实现</div></div>
    <div class="track"><label>Event</label><div class="track-body">
      <button v-for="event in events" :key="event.id" class="range event-range" :class="event.review_status" :style="style(event.start_sec, event.end_sec)" :title="event.prompt" @click="emit('select', event)" />
      <span v-if="!events.length" class="track-empty">当前筛选没有 Event</span>
    </div></div>
    <div class="track"><label>数据异常</label><div class="track-body">
      <span v-for="item in dataRanges" :key="`${item.anomaly_code}-${item.start_sec}`" class="range data-range" :style="style(item.start_sec, item.end_sec)" :title="item.anomaly_name" />
    </div></div>
    <div class="track"><label>图像异常</label><div class="track-body">
      <span v-for="item in imageRanges" :key="`${item.anomaly_code}-${item.start_sec}`" class="range image-range" :style="style(item.start_sec, item.end_sec)" :title="item.anomaly_name" />
    </div></div>
  </div>
</template>
