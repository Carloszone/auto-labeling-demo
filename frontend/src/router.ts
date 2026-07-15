import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [{ path: '/', name: 'auto-labeling-demo', component: App }],
})
