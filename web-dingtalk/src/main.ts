import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { applyProductDocumentTitle } from './productLine'
import './styles/theme.css'

applyProductDocumentTitle()
createApp(App).use(createPinia()).use(router).mount('#app')
