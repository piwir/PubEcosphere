<script setup>
import site from '../data/site.json'

const posts = [...site.ai4s.posts].sort((a, b) => (a.date < b.date ? 1 : -1))
const pad = (n) => String(n).padStart(2, '0')
</script>

<template>
  <div class="wrap">
    <section class="archive">
      <p class="eyebrow">AI4S</p>
      <h1 class="page-title">AI4S 项目推文</h1>
      <p class="page-sub">
        介绍AI4S项目 · 由
        <a :href="site.ai4s.github" target="_blank" rel="noopener">PubAI4S</a>
        生成
      </p>

      <div class="issue-row" v-for="it in posts" :key="it.repo">
        <span class="row-num"><small>NO.</small>{{ pad(it.no) }}</span>
        <span class="row-date">{{ it.date }}</span>
        <span class="row-summary">{{ it.summary || '—' }}</span>
        <a
          v-if="it.url"
          class="row-link sweep-link"
          :href="it.url"
          target="_blank"
          rel="noopener"
        >阅读 →</a>
        <span v-else class="row-soon">即将发布</span>
      </div>
    </section>
  </div>
</template>
