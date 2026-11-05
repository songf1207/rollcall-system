<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import ScoreCard from '../components/ScoreCard.vue'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const mode = ref('random')          // 'random' | 'sequential' | 'clock'
const classes = ref([])             // 班级列表
const activeClass = ref('')         // 当前点名的班级；'' = 全部学生
const session = ref(null)
const result = ref(null)            // 后端返回的 rollcall
const progress = ref(null)
const students = ref([])            // 仅用于滚动动画的占位名字
const rolling = ref(false)
const error = ref('')
const display = ref({ name: '', no: '' })
const scoreOpen = ref(false)        // 记分卡是否展开
const lastDelta = ref(null)         // 上一次记分结果，给大屏一行提示
const clockDigit = ref(0)           // 按时间点名：当前分钟个位
const clockList = ref([])           // 按时间点名的候选
const pickedId = ref(null)          // 本次被选中的学生（高亮）
const picking = ref(false)

/** 拉当前班级名单（滚动动画的占位名字取自本班，视觉上不会串班） */
async function loadStudents() {
  const query = new URLSearchParams({ page: '1', size: '200' })
  if (activeClass.value) query.set('class_name', activeClass.value)
  const page = await api.get(`/api/students?${query}`)
  students.value = page.items
}

onMounted(async () => {
  try {
    const data = await api.get('/api/classes')
    classes.value = data.items

    const current = await api.get('/api/sessions/current')
    if (current) {
      session.value = current
      mode.value = current.mode
      activeClass.value = current.class_name || ''
    } else if (classes.value.length) {
      // 没开场次时默认选中第一个班，避免老师误点「全部学生」把两个班混一起
      activeClass.value = classes.value[0].name
    }
    await loadStudents()
    if (mode.value === 'clock') await loadClockCandidates()
  } catch (e) {
    error.value = e.message
  }
})

/** 保证有一个「模式 + 班级」都匹配的进行中场次；换模式或换班级都等于换场次 */
async function ensureSession(forceNew = false) {
  const current = await api.get('/api/sessions/current')
  // 后端场次状态是 open / closed（current 只会返回 open 的），别写成 active
  const usable =
    current &&
    current.status === 'open' &&
    current.mode === mode.value &&
    (current.class_name || '') === activeClass.value
  if (usable && !forceNew) {
    session.value = current
    return current
  }
  const created = await api.post('/api/sessions', {
    mode: mode.value,
    class_name: activeClass.value || null,
  })
  session.value = created
  result.value = null
  progress.value = null
  display.value = { name: '', no: '' }
  scoreOpen.value = false
  lastDelta.value = null
  pickedId.value = null
  clockList.value = []
  await loadStudents()
  return created
}

/** 按时间点名：拉候选（学号尾号 = 当前分钟个位） */
async function loadClockCandidates() {
  if (mode.value !== 'clock') return
  error.value = ''
  try {
    const s = await ensureSession()
    const data = await api.get(`/api/sessions/${s.id}/clock-candidates`)
    clockDigit.value = data.digit
    clockList.value = data.items
    if (!data.items.length) {
      error.value = `学号尾号为 ${data.digit} 的同学本轮都点过了，可以点「下一场」或换个时间`
    }
  } catch (e) {
    error.value = e.message
  }
}

/** 选人：计入随机点名次数，然后照常弹记分卡 */
async function pickStudent(item) {
  if (picking.value || rolling.value) return
  picking.value = true
  lastDelta.value = null
  scoreOpen.value = false
  error.value = ''
  try {
    const s = await ensureSession()
    const data = await api.post(`/api/sessions/${s.id}/pick`, { student_id: item.student_id })
    applyPick(data, item.student_id)
  } catch (e) {
    error.value = e.message
  } finally {
    picking.value = false
  }
}

/** 在这批候选里随机抽一个（权重规则与随机点名一致） */
async function pickRandomInClock() {
  if (picking.value || rolling.value) return
  if (!clockList.value.length) {
    error.value = '没有候选可抽'
    return
  }
  picking.value = true
  lastDelta.value = null
  scoreOpen.value = false
  error.value = ''
  // 滚动动画：视觉过渡，结果仍以后端为准
  const timer = setInterval(() => {
    const pick = clockList.value[Math.floor(Math.random() * clockList.value.length)]
    display.value = { name: pick.name, no: pick.student_no }
  }, 60)
  try {
    const s = await ensureSession()
    const data = await api.post(`/api/sessions/${s.id}/pick-random?digit=${clockDigit.value}`)
    await sleep(650)
    clearInterval(timer)
    applyPick(data, data.rollcall.student_id)
  } catch (e) {
    clearInterval(timer)
    error.value = e.message
  } finally {
    picking.value = false
  }
}

function applyPick(data, highlightId) {
  result.value = data.rollcall
  progress.value = data.progress
  pickedId.value = highlightId
  if (session.value) {
    session.value.draw_count += 1
    session.value.round_no = data.progress.round_no
  }
  display.value = {
    name: data.rollcall.student?.name || '',
    no: data.rollcall.student?.student_no || '',
  }
  scoreOpen.value = true
  loadClockCandidates()      // 刷新候选上的「已点次数」
}

/** 切换班级 = 换一场，旧的自动结束 */
async function switchClass() {
  if (rolling.value) return
  error.value = ''
  try {
    await ensureSession(true)
    students.value = []
    await loadStudents()
  } catch (e) {
    error.value = e.message
  }
}

async function switchMode(next) {
  if (mode.value === next || rolling.value) return
  mode.value = next
  error.value = ''
  try {
    await ensureSession(true)   // 切换模式 = 新开一场，旧的自动结束
    if (next === 'clock') await loadClockCandidates()
  } catch (e) {
    error.value = e.message
  }
}

/** 手动结束当前场次并开新的一场（下一次课用） */
async function nextSession() {
  if (rolling.value) return
  error.value = ''
  try {
    await ensureSession(true)
  } catch (e) {
    error.value = e.message
  }
}

async function startDraw() {
  if (rolling.value) return
  if (!students.value.length) {
    error.value = '名单是空的，请先导入学生名单'
    return
  }
  error.value = ''
  rolling.value = true
  const startedAt = Date.now()
  lastDelta.value = null
  scoreOpen.value = false

  // 动画：只做视觉过渡，真正的中签者来自后端
  const timer = setInterval(() => {
    const pick = students.value[Math.floor(Math.random() * students.value.length)]
    display.value = { name: pick.name, no: pick.student_no }
  }, 60)

  try {
    const s = await ensureSession()
    const data = await api.post(`/api/sessions/${s.id}/draw`)
    const elapsed = Date.now() - startedAt
    if (elapsed < 900) await sleep(900 - elapsed)   // 动画至少转 0.9 秒，别一闪而过

    clearInterval(timer)
    result.value = data.rollcall
    progress.value = data.progress
    if (session.value) {
      session.value.draw_count += 1
      session.value.round_no = data.progress.round_no
    }
    display.value = {
      name: data.rollcall.student?.name || '未知',
      no: data.rollcall.student?.student_no || '',
    }
    scoreOpen.value = true      // 定格后自动弹出记分卡
  } catch (e) {
    clearInterval(timer)
    error.value = e.message
    display.value = { name: '点名失败', no: '—' }
  } finally {
    rolling.value = false
  }
}

function onScored(data) {
  lastDelta.value = data.point_delta
}
</script>

<template>
  <div class="screen">
    <header class="bar">
      <div class="left">
        <select v-model="activeClass" class="class-select" :disabled="rolling" @change="switchClass">
          <option value="">全部学生</option>
          <option v-for="c in classes" :key="c.name" :value="c.name">
            {{ c.name }} · {{ c.student_count }} 人
          </option>
        </select>
        <div class="tabs">
          <button :class="{ active: mode === 'random' }" @click="switchMode('random')">随机点名</button>
          <button :class="{ active: mode === 'sequential' }" @click="switchMode('sequential')">按学号顺序</button>
          <button :class="{ active: mode === 'clock' }" @click="switchMode('clock')">按时间点名</button>
        </div>
      </div>
      <div class="meta">
        <span v-if="session">场次 #{{ session.id }} · 第 {{ session.round_no }} 轮 · 已点 {{ session.draw_count }} 次</span>
        <span v-else>未开场次</span>
        <span v-if="result" class="dbg">id {{ result.id }}</span>
        <button class="ghost" :disabled="rolling" @click="nextSession">下一场</button>
      </div>
    </header>

    <main class="stage">
      <template v-if="display.name">
        <div class="name" :class="{ rolling }">{{ display.name }}</div>
        <div class="no">{{ display.no }}</div>
      </template>
      <p v-if="progress" class="progress">
        本轮已点 {{ progress.called_in_round }} 人，剩 {{ progress.remaining_in_round }} 人（全班 {{ progress.total_students }} 人）
        <span v-if="progress.is_new_round">· 下一轮已开始</span>
      </p>

      <p v-if="lastDelta !== null" class="delta">已记分 {{ lastDelta > 0 ? '+' : '' }}{{ lastDelta }}</p>

      <!-- 按时间点名：候选列表，点谁点谁 -->
      <section v-if="mode === 'clock'" class="clock">
        <div class="clock-head">
          <span class="clock-tip">
            现在 <b>{{ clockDigit }}</b> 分尾巴 —— 学号尾号为 <b>{{ clockDigit }}</b> 的同学优先
          </span>
          <button class="ghost" :disabled="picking" @click="loadClockCandidates">刷新候选</button>
        </div>
        <div v-if="clockList.length" class="clock-list">
          <button
            v-for="c in clockList"
            :key="c.student_id"
            :class="['cand', { picked: pickedId === c.student_id }]"
            :disabled="picking"
            @click="pickStudent(c)"
          >
            <strong>{{ c.name }}</strong>
            <span>{{ c.student_no }}</span>
            <i v-if="c.called_count" class="times">已点 {{ c.called_count }} 次</i>
          </button>
        </div>
        <p v-else class="clock-empty">没有可选的候选，点「刷新候选」或「下一场」</p>

        <button v-if="clockList.length" class="primary" :disabled="picking" @click="pickRandomInClock">
          {{ picking ? '抽取中…' : '随机抽取' }}
        </button>
      </section>

      <button v-else class="primary" :disabled="rolling" @click="startDraw">
        {{ rolling ? '抽取中…' : '开始点名' }}
      </button>

      <p v-if="error" class="err">{{ error }}</p>
    </main>

    <ScoreCard
      v-if="result && scoreOpen"
      :rollcall="result"
      @close="scoreOpen = false"
      @scored="onScored"
    />
  </div>
</template>

<style scoped>
.screen {
  min-height: calc(100vh - 52px);
  background: #0f172a;
  color: #e2e8f0;
  display: flex;
  flex-direction: column;
  font-family: system-ui, sans-serif;
}
.bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 28px;
  border-bottom: 1px solid #1e293b;
  gap: 16px;
  flex-wrap: wrap;
}
.left { display: flex; align-items: center; gap: 14px; }
.class-select {
  padding: 8px 14px;
  background: #1e293b;
  color: #e2e8f0;
  border: 1px solid #334155;
  border-radius: 10px;
  font-size: 14px;
  max-width: 260px;
  cursor: pointer;
}
.class-select:disabled { opacity: 0.6; cursor: default; }
.tabs { display: flex; gap: 10px; }
.tabs button {
  padding: 8px 18px;
  border: 1px solid #334155;
  background: transparent;
  color: #94a3b8;
  border-radius: 999px;
  cursor: pointer;
  font-size: 14px;
}
.tabs button.active { background: #4f46e5; border-color: #4f46e5; color: #fff; }
.meta { font-size: 13px; color: #64748b; display: flex; align-items: center; gap: 12px; }
.dbg { font-size: 11px; color: #334155; }
.ghost {
  padding: 6px 14px;
  font-size: 13px;
  background: transparent;
  border: 1px solid #334155;
  color: #94a3b8;
  border-radius: 999px;
  cursor: pointer;
}
.ghost:hover:not(:disabled) { border-color: #475569; color: #cbd5e1; }
.ghost:disabled { opacity: 0.5; cursor: default; }
.stage {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 20px;
  padding: 24px;
}
.name {
  font-size: clamp(56px, 12vw, 140px);
  font-weight: 700;
  letter-spacing: 0.08em;
  line-height: 1.1;
  transition: color 0.2s;
}
.name.rolling { color: #a5b4fc; }
.no { font-size: clamp(20px, 3vw, 32px); color: #94a3b8; letter-spacing: 0.2em; }
.progress { color: #64748b; font-size: 14px; }
.delta { color: #34d399; font-size: 15px; font-weight: 600; }
.err { color: #fca5a5; text-align: center; }
.primary {
  padding: 26px 110px;
  min-width: 320px;
  font-size: 30px;
  font-weight: 600;
  letter-spacing: 0.16em;
  border: 0;
  border-radius: 20px;
  background: linear-gradient(180deg, #6366f1, #4f46e5);
  color: #fff;
  cursor: pointer;
  box-shadow: 0 12px 32px rgba(79, 70, 229, 0.45);
  transition: transform 0.12s, box-shadow 0.2s;
}
.primary:hover:not(:disabled) { transform: translateY(-2px); box-shadow: 0 16px 40px rgba(79, 70, 229, 0.55); }
.primary:active:not(:disabled) { transform: translateY(0); }
.primary:disabled { background: #4338ca; cursor: default; opacity: 0.75; box-shadow: none; }
.tip { color: #475569; font-size: 12px; }

/* 按时间点名 */
.clock { width: min(920px, 90vw); display: flex; flex-direction: column; align-items: center; gap: 14px; }
.clock-head { display: flex; align-items: center; gap: 14px; }
.clock-tip { color: #94a3b8; font-size: 15px; }
.clock-tip b { color: #fbbf24; font-size: 18px; }
.clock-list { display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; }
.cand {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  min-width: 108px;
  padding: 12px 16px;
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 14px;
  color: #e2e8f0;
  cursor: pointer;
  transition: transform 0.12s, border-color 0.15s;
}
.cand strong { font-size: 17px; font-weight: 600; }
.cand span { font-size: 12px; color: #94a3b8; letter-spacing: 0.05em; }
.cand .times { font-style: normal; font-size: 10px; color: #fbbf24; margin-top: 3px; }
.cand.picked .times { color: #fde68a; }
.cand:hover:not(:disabled) { transform: translateY(-2px); border-color: #6366f1; }
.cand.picked {
  border-color: #818cf8;
  background: linear-gradient(180deg, #4338ca, #4f46e5);
  box-shadow: 0 0 0 3px rgba(129, 140, 248, 0.35), 0 12px 28px rgba(79, 70, 229, 0.45);
}
.cand.picked span { color: #c7d2fe; }
.cand:disabled { opacity: 0.6; cursor: default; }
.clock-empty { color: #64748b; font-size: 14px; }
</style>
