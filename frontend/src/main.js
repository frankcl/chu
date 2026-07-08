import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './styles/theme.css'
import './styles/cards.css'
import './styles/markdown.css'
import App from './App.vue'

createApp(App).use(ElementPlus).mount('#app')
