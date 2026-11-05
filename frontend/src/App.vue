<script setup>
import { ref } from 'vue'
import RollCallScreen from './pages/RollCallScreen.vue'
import RankingPage from './pages/RankingPage.vue'
import StudentsPage from './pages/StudentsPage.vue'

// 课堂演示模式：不再需要教师口令，打开即用。
// （后端用 OPEN_ACCESS=1 关闭鉴权；若要恢复口令校验，把这行改成 true 并保留下面的口令弹窗）
const NEED_KEY = false

const tab = ref('screen')       // 'screen' 点名大屏 | 'rank' 排行榜 | 'students' 名单
</script>

<template>
  <nav class="nav">
    <button :class="{ on: tab === 'screen' }" @click="tab = 'screen'">点名大屏</button>
    <button :class="{ on: tab === 'rank' }" @click="tab = 'rank'">排行榜</button>
    <button :class="{ on: tab === 'students' }" @click="tab = 'students'">名单</button>
    <span class="spacer"></span>
    <span class="brand">课堂随机点名系统</span>
  </nav>

  <RollCallScreen v-if="tab === 'screen'" />
  <RankingPage v-else-if="tab === 'rank'" />
  <StudentsPage v-else />
</template>

<style>
html, body, #app { margin: 0; padding: 0; height: 100%; }
</style>

<style scoped>
.nav {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 52px;
  padding: 0 24px;
  background: #0b1220;
  border-bottom: 1px solid #1e293b;
}
.nav button {
  padding: 6px 16px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: transparent;
  color: #94a3b8;
  font-size: 14px;
  cursor: pointer;
}
.nav button.on { background: #1e293b; color: #e2e8f0; }
.nav .spacer { flex: 1; }
.nav .brand { color: #475569; font-size: 13px; letter-spacing: 0.08em; }
</style>
