import { createApp, h } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import { RouterView } from 'vue-router'
import 'element-plus/dist/index.css'
import { router } from './router'
import './styles.css'

createApp({ setup: () => () => h(RouterView) })
  .use(createPinia())
  .use(router)
  .use(ElementPlus)
  .mount('#app')
