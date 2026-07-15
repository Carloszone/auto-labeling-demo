<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import type { CameraInfo } from '../api'

const props = defineProps<{
  cameras: CameraInfo[]
  duration: number
  mainCameraKey: string | null
}>()
const emit = defineEmits<{ time: [value: number] }>()

const videoRefs = new Map<string, HTMLVideoElement>()
const currentTime = ref(0)
const playing = ref(false)
const playbackState = ref<'ready' | 'playing' | 'buffering' | 'error'>('ready')
let pendingSeek: number | null = null
let clockFrame = 0
let lastClockUpdate = 0

const readyCameras = computed(() => props.cameras.filter(item => item.generation_status === 'ready' && item.video_url))

function applySeekToReadyVideos(target: number): boolean {
  const videos = readyCameras.value.map(camera => videoRefs.get(camera.camera_key))
  if (!videos.length || videos.some(video => !video || video.readyState < HTMLMediaElement.HAVE_METADATA)) {
    return false
  }
  for (const video of videos as HTMLVideoElement[]) {
    video.currentTime = Math.min(target, video.duration || target)
  }
  return true
}

function applyPendingSeek() {
  if (pendingSeek !== null && applySeekToReadyVideos(pendingSeek)) pendingSeek = null
}

function setVideoRef(key: string, element: unknown) {
  if (element instanceof HTMLVideoElement) {
    videoRefs.set(key, element)
    applyPendingSeek()
  } else {
    videoRefs.delete(key)
  }
}
function mainVideo() {
  return videoRefs.get(props.mainCameraKey || '') || videoRefs.values().next().value as HTMLVideoElement | undefined
}
async function playAll() {
  const main = mainVideo()
  if (!main) return
  const target = currentTime.value
  videoRefs.forEach(video => {
    if (video.readyState >= HTMLMediaElement.HAVE_METADATA && Math.abs(video.currentTime - target) > 0.1) {
      video.currentTime = Math.min(target, video.duration || target)
    }
  })
  try {
    await main.play()
    playing.value = true
    playbackState.value = 'playing'
    for (const video of videoRefs.values()) {
      if (video !== main) void video.play().catch(() => undefined)
    }
  } catch {
    playing.value = false
    playbackState.value = 'error'
  }
}
function pauseAll() {
  playing.value = false
  videoRefs.forEach(video => video.pause())
  playbackState.value = 'ready'
}
function seek(value: number) {
  const target = Math.max(0, Math.min(props.duration || 0, value))
  pendingSeek = target
  applyPendingSeek()
  currentTime.value = target
  emit('time', target)
}
function onLoadedMetadata() { applyPendingSeek() }
function onMainTime(event: Event) {
  const video = event.target as HTMLVideoElement
  currentTime.value = video.currentTime
  emit('time', video.currentTime)
}
function updateMainClock(timestamp: number) {
  const main = mainVideo()
  if (!main || main.paused || main.ended) return
  if (timestamp - lastClockUpdate >= 100) {
    currentTime.value = main.currentTime
    emit('time', main.currentTime)
    lastClockUpdate = timestamp
  }
  clockFrame = window.requestAnimationFrame(updateMainClock)
}
function startMainClock() {
  window.cancelAnimationFrame(clockFrame)
  lastClockUpdate = 0
  clockFrame = window.requestAnimationFrame(updateMainClock)
}
function stopMainClock() {
  window.cancelAnimationFrame(clockFrame)
  clockFrame = 0
}
function onMainPlaying() {
  playing.value = true
  playbackState.value = 'playing'
  startMainClock()
}
function onMainWaiting() { playbackState.value = 'buffering' }
function onMainPause() {
  stopMainClock()
  playing.value = false
  if (playbackState.value !== 'error') playbackState.value = 'ready'
  videoRefs.forEach(video => {
    if (video !== mainVideo()) video.pause()
  })
}
function onMainError() {
  stopMainClock()
  playing.value = false
  playbackState.value = 'error'
}
function step(direction: number) {
  pauseAll()
  seek(currentTime.value + direction / 30)
}
function onSlider(value: number | number[]) { seek(Number(value)) }

defineExpose({ seek })
onBeforeUnmount(stopMainClock)
</script>

<template>
  <div class="player-shell">
    <div class="video-grid" :class="`count-${Math.min(readyCameras.length, 4)}`">
      <div v-for="camera in cameras" :key="camera.camera_key" class="camera-card">
        <div class="camera-title">
          <strong>{{ camera.camera_key }}</strong>
          <span>{{ camera.source_topic }}</span>
          <el-tag v-if="camera.is_main_camera" size="small">主相机</el-tag>
        </div>
        <video
          v-if="camera.video_url"
          :ref="element => setVideoRef(camera.camera_key, element)"
          :src="camera.video_url"
          :preload="camera.is_main_camera ? 'auto' : 'metadata'"
          @loadedmetadata="onLoadedMetadata"
          @timeupdate="camera.is_main_camera && onMainTime($event)"
          @playing="camera.is_main_camera && onMainPlaying()"
          @waiting="camera.is_main_camera && onMainWaiting()"
          @pause="camera.is_main_camera && onMainPause()"
          @ended="camera.is_main_camera && onMainPause()"
          @error="camera.is_main_camera && onMainError()"
        />
        <el-result v-else icon="warning" title="视频生成失败" :sub-title="camera.error?.message || ''" />
      </div>
      <el-empty v-if="!cameras.length" description="视频生成后将在此展示" />
    </div>
    <div class="playback-controls">
      <el-button @click="step(-1)">上一帧</el-button>
      <el-button type="primary" @click="playing ? pauseAll() : playAll()">{{ playing ? '暂停' : '播放' }}</el-button>
      <el-button @click="step(1)">下一帧</el-button>
      <el-slider :model-value="currentTime" :max="duration || 0" :step="1 / 30" @input="onSlider" />
      <span>{{ currentTime.toFixed(3) }} / {{ (duration || 0).toFixed(3) }} s</span>
      <el-tag v-if="playbackState === 'buffering'" type="warning">缓冲中</el-tag>
      <el-tag v-else-if="playbackState === 'error'" type="danger">播放失败</el-tag>
    </div>
  </div>
</template>
