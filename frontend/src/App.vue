<script setup>
import { ref } from 'vue'
import { getKey, setKey } from './api'
import RollCallScreen from './pages/RollCallScreen.vue'
import RankingPage from './pages/RankingPage.vue'
import StudentsPage from './pages/StudentsPage.vue'

const hasKey = ref(!!getKey())
const keyInput = ref('')
const tab = ref('screen')       // 'screen' 点名大屏 | 'rank' 排行榜

function unlock() {
  setKey(keyInput.value)
  hasKey.value = !!getKey()
}

function lock() {
  sessionStorage.removeItem('teacherKey')
  hasKey.value = false
  keyInput.value = ''
}
</script>

<template>
  <template v-if="hasKey">
    <nav class="nav">
      <button :class="{ on: tab === 'screen' }" @click="tab = 'screen'">点名大屏</button>
      <button :class="{ on: tab === 'rank' }" @click="tab = 'rank'">排行榜</button>
      <button :class="{ on: tab === 'students' }" @click="tab = 'students'">名单</button>
      <span class="spacer"></span>
      <button class="quit" @click="lock">退出</button>
    </nav>

    <RollCallScreen v-if="tab === 'screen'" />
    <RankingPage v-else-if="tab === 'rank'" />
    <StudentsPage v-else />
  </template>

  <div v-else class="gate">
    <h1>课堂随机点名系统</h1>
    <p class="hint">请输入教师口令（在 backend/.teacher-key.txt 里）</p>
    <div class="row">
      <input v-model="keyInput" type="password" placeholder="教师口令" @keyup.enter="unlock" />
      <button @click="unlock">进入</button>
    </div>
  </div>
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
.nav .quit { color: #64748b; border-color: #1e293b; }

.gate {
  min-height: 100vh;
  background: #0f172a;
  color: #e2e8f0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  font-family: system-ui, sans-serif;
}
h1 { font-size: 24px; font-weight: 600; }
.hint { color: #64748b; font-size: 13px; }
.row { display: flex; gap: 8px; }
input {
  padding: 10px 14px;
  width: 340px;
  border: 1px solid #334155;
  border-radius: 10px;
  background: #1e293b;
  color: #e2e8f0;
}
.row button {
  padding: 10px 22px;
  border: 0;
  border-radius: 10px;
  background: #4f46e5;
  color: #fff;
  cursor: pointer;
}
</style>
