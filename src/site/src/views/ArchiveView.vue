<script setup>
import site from '../data/site.json'

const issues = [...site.issues].sort((a, b) => b.issue - a.issue)
const pad = (n) => String(n).padStart(2, '0')
</script>

<template>
  <div class="wrap">
    <section class="archive">
      <p class="eyebrow">ARCHIVE</p>
      <h1 class="page-title">往期归档</h1>
      <p class="page-sub">每周三更新 · 共 {{ issues.length }} 期</p>

      <div class="issue-row" v-for="it in issues" :key="it.issue">
        <span class="row-num"><small>NO.</small>{{ pad(it.issue) }}</span>
        <span class="row-date">{{ it.date }}</span>
        <span class="row-summary">{{ it.summary || '—' }}</span>
        <a
          v-if="it.wechatUrl"
          class="row-link sweep-link"
          :href="it.wechatUrl"
          target="_blank"
          rel="noopener"
        >阅读 →</a>
      </div>
    </section>
  </div>
</template>
