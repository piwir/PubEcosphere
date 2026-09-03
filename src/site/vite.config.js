import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// base 必须与仓库名一致（GitHub Pages 子路径部署：/PubEcosphere/）
export default defineConfig({
  base: '/PubEcosphere/',
  plugins: [vue()],
})
