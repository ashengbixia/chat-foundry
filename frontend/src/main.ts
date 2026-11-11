import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './AuthRoot.vue'
import './styles/theme.css'

const app = createApp(App)

app.use(createPinia())

app.mount('#app')
