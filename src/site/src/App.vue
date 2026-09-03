<script setup>
import { RouterLink } from 'vue-router'
import site from './data/site.json'

const base = import.meta.env.BASE_URL
</script>

<template>
  <div class="page">
    <header class="topbar">
      <div class="topbar-inner wrap">
        <RouterLink to="/" class="brand">
          <img class="brand-icon" :src="base + 'icon.png'" alt="">
          <span class="brand-name">PubEcosphere</span>
        </RouterLink>
        <nav class="nav" aria-label="主导航">
          <RouterLink to="/" exact-active-class="is-active">首页</RouterLink>
          <RouterLink to="/weekly" active-class="is-active">周报</RouterLink>
          <RouterLink to="/ai4s" active-class="is-active">AI4S</RouterLink>
          <RouterLink to="/about" active-class="is-active">关于</RouterLink>
        </nav>
      </div>
    </header>

    <main>
      <RouterView />
    </main>

    <footer class="footer">
      <div class="footer-inner wrap">
        <div class="footer-copy">
          <a :href="site.github" target="_blank" rel="noopener">GitHub</a>
          <span class="footer-dot">·</span>
          <a href="https://github.com/piwir/PubEcosphere/blob/main/LICENSE" target="_blank" rel="noopener">MIT License</a>
          <span class="footer-dot">·</span>
          <span>数据来自 <a href="https://pubpeer.com" target="_blank" rel="noopener">PubPeer</a></span>
        </div>
        <div class="footer-qr">
          <p>微信公众号</p>
          <a :href="base + 'QR.png'" target="_blank" rel="noopener">
            <img :src="base + 'QR.png'" alt="微信公众号二维码（点击查看大图）">
          </a>
        </div>
      </div>
    </footer>
  </div>
</template>
