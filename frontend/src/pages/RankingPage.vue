<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import { api, downloadFile } from '../api'

const chartEl = ref(null)
const tableRows = ref([])
const classes = ref([])
const activeClass = ref('')
const loading = ref(false)
const exporting = ref(false)
const error = ref('')
const topN = ref(0)          // 0 = 全部（按班级人数自适应）
let chart = null

/** 当前范围内的学生总数：选了班级就是该班人数，否则全部学生人数 */
const scopeSize = computed(() => {
  if (activeClass.value) {
    const hit = classes.value.find((c) => c.name === activeClass.value)
    return hit ? hit.student_count : 0
  }
  return classes.value.reduce((sum, c) => sum + c.student_count, 0)
})

/** 后端 top 上限 100，这里跟着「最大班级人数」走，不截断 */
const effectiveTop = computed(() => {
  if (topN.value > 0) return topN.value
  return Math.min(Math.max(scopeSize.value, 5), 100)
})

async function loadClasses() {
  const data = await api.get('/api/classes')
  classes.value = data.items
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const query = new URLSearchParams({ top: effectiveTop.value })
    if (activeClass.value) query.set('class_name', activeClass.value)
    const data = await api.get(`/api/stats/ranking?${query}`)
    tableRows.value = data.items
    render(data)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function render(data) {
  if (!chartEl.value) return
  if (!chart) chart = echarts.init(chartEl.value, null, { renderer: 'canvas' })
  chart.setOption(
    {
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      legend: { data: ['总积分', '随机点名次数'], textStyle: { color: '#94a3b8' }, top: 0 },
      grid: { left: 48, right: 48, top: 48, bottom: data.categories.length > 12 ? 96 : 64 },
      xAxis: {
        type: 'category',
        data: data.categories,
        axisLabel: { color: '#94a3b8', interval: 0, rotate: data.categories.length > 12 ? 45 : 0, fontSize: 11 },
        axisLine: { lineStyle: { color: '#334155' } },
      },
      yAxis: [
        {
          type: 'value',
          name: '总积分',
          nameTextStyle: { color: '#94a3b8' },
          axisLabel: { color: '#94a3b8' },
          splitLine: { lineStyle: { color: '#1e293b' } },
        },
        {
          type: 'value',
          name: '随机点名次数',
          nameTextStyle: { color: '#94a3b8' },
          minInterval: 1,                     // 次数只能是整数，别出现 0.5 这种刻度
          axisLabel: { color: '#94a3b8', formatter: (v) => (Number.isInteger(v) ? v : '') },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: '总积分',
          type: 'bar',
          data: data.points_series,
          barMaxWidth: 40,
          itemStyle: {
            borderRadius: [6, 6, 0, 0],
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: '#818cf8' },
              { offset: 1, color: '#4f46e5' },
            ]),
          },
          // 人数多时标签会挤在一起，超过 20 人只在下方表格里看数值
          label: {
            show: data.categories.length <= 20,
            position: 'top',
            color: '#cbd5e1',
            fontSize: 11,
          },
        },
        {
          name: '随机点名次数',
          type: 'line',
          yAxisIndex: 1,
          data: data.calls_series,
          smooth: true,
          symbolSize: 8,
          lineStyle: { width: 2, color: '#34d399' },
          itemStyle: { color: '#34d399' },
        },
      ],
    },
    true,
  )
}

async function exportXlsx() {
  if (exporting.value) return
  exporting.value = true
  error.value = ''
  try {
    // 必须带口令 fetch 取 blob；直接打开下载地址不会带 X-Teacher-Key
    const query = activeClass.value
      ? `?class_name=${encodeURIComponent(activeClass.value)}`
      : ''
    await downloadFile(`/api/export/points${query}`, '积分详单.xlsx')
  } catch (e) {
    error.value = e.message
  } finally {
    exporting.value = false
  }
}

function onResize() {
  chart?.resize()
}

onMounted(async () => {
  loadClasses().catch((e) => { error.value = e.message })
  load()
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div class="page">
    <header>
      <h2>积分排行榜</h2>
      <div class="actions">
        <select v-model="activeClass" @change="load">
          <option value="">全部班级</option>
          <option v-for="c in classes" :key="c.name" :value="c.name">{{ c.name }}</option>
        </select>
        <select v-model.number="topN" @change="load">
          <option :value="0">全部（{{ scopeSize }} 人）</option>
          <option :value="10">前 10 名</option>
          <option :value="20">前 20 名</option>
          <option :value="50">前 50 名</option>
        </select>
        <button :disabled="loading" @click="load">{{ loading ? '加载中…' : '刷新' }}</button>
        <button class="export" :disabled="exporting" @click="exportXlsx">
          {{ exporting ? '导出中…' : '导出积分详单' }}
        </button>
      </div>
    </header>

    <p v-if="error" class="err">{{ error }}</p>

    <div ref="chartEl" class="chart"></div>

    <table>
      <thead>
        <tr><th>排名</th><th>学号</th><th>姓名</th><th>专业</th><th>总积分</th><th>随机点名次数</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in tableRows" :key="row.student_id">
          <td>{{ row.rank }}</td>
          <td>{{ row.student_no }}</td>
          <td>{{ row.name }}</td>
          <td>{{ row.major || '-' }}</td>
          <td class="num">{{ row.total_points }}</td>
          <td class="num">{{ row.random_call_count }}</td>
        </tr>
        <tr v-if="!tableRows.length && !loading">
          <td colspan="6" class="empty">还没有积分数据，先去点名并记分</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.page { padding: 24px 32px 48px; max-width: 1200px; margin: 0 auto; }
header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
h2 { font-size: 20px; font-weight: 600; color: #e2e8f0; }
.actions { display: flex; gap: 8px; }
select, .actions button {
  padding: 8px 14px;
  background: #1e293b;
  color: #cbd5e1;
  border: 1px solid #334155;
  border-radius: 8px;
  font-size: 13px;
  cursor: pointer;
}
.actions button.export { background: #4f46e5; border-color: #6366f1; color: #fff; }
.actions button.export:disabled { opacity: 0.7; cursor: default; }
.chart { width: 100%; height: 480px; }
.err { color: #fca5a5; margin-bottom: 12px; }
table { width: 100%; border-collapse: collapse; margin-top: 24px; font-size: 14px; color: #cbd5e1; }
th, td { padding: 10px 8px; border-bottom: 1px solid #1e293b; text-align: left; }
th { color: #94a3b8; font-weight: 500; font-size: 13px; }
.num { font-variant-numeric: tabular-nums; }
.empty { text-align: center; color: #64748b; padding: 24px; }
</style>
