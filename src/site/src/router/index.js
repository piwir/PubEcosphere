import { createRouter, createWebHashHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'
import ArchiveView from '../views/ArchiveView.vue'
import Ai4sView from '../views/Ai4sView.vue'
import AboutView from '../views/AboutView.vue'

// hash 路由：GitHub Pages 子路径下刷新/深链不 404
const routes = [
  { path: '/', name: 'home', component: HomeView },
  { path: '/weekly', name: 'weekly', component: ArchiveView },
  { path: '/archive', redirect: '/weekly' },
  { path: '/ai4s', name: 'ai4s', component: Ai4sView },
  { path: '/about', name: 'about', component: AboutView },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
})
