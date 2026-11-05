<script setup>
import { computed, ref } from 'vue'
import { api } from '../api'

const props = defineProps({
  rollcall: { type: Object, required: true },
})
const emit = defineEmits(['close', 'scored'])

const arrived = ref(true)
const repeat = ref('ok')     // 'ok' 准确复述 | 'fail' 没复述对 | 'none' 未提问
const answer = ref(null)     // 0 ~ 3 分，null 表示未作答
const submitting = ref(false)
const error = ref('')
const saved = ref(false)
const delta = ref(0)

const answerOptions = [0, 0.5, 1, 1.5, 2, 2.5, 3]

// 本地预估，方便老师提交前确认；最终以后端返回的 point_delta 为准
const preview = computed(() => {
  if (!arrived.value) return 0
  let v = 1
  if (repeat.value === 'ok') v += 0.5
  else if (repeat.value === 'fail') v -= 1
  if (answer.value !== null) v += answer.value
  return Math.round(v * 10) / 10
})

async function submit() {
  if (submitting.value) return
  submitting.value = true
  error.value = ''
  try {
    // 缺席时必须把复述与答题都清空，否则后端返回 400
    const payload = {
      arrived: arrived.value,
      repeat_ok: arrived.value && repeat.value !== 'none' ? repeat.value === 'ok' : null,
      answer_score: arrived.value ? answer.value : null,
    }
    const data = await api.post(`/api/rollcalls/${props.rollcall.id}/score`, payload)
    delta.value = data.point_delta
    saved.value = true
    emit('scored', data)
  } catch (e) {
    error.value = e.message
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <aside class="card">
    <header>
      <div>
        <strong>{{ rollcall.student?.name }}</strong>
        <span class="no">{{ rollcall.student?.student_no }}</span>
      </div>
      <button class="close" :disabled="submitting" @click="emit('close')">×</button>
    </header>

    <template v-if="!saved">
      <section>
        <div class="label">到场</div>
        <div class="row">
          <button :class="{ on: arrived }" @click="arrived = true">到场 +1</button>
          <button :class="{ on: !arrived }" @click="arrived = false">缺席 0</button>
        </div>
      </section>

      <template v-if="arrived">
        <section>
          <div class="label">准确复述所提问题</div>
          <div class="row">
            <button :class="{ on: repeat === 'ok' }" @click="repeat = 'ok'">复述对 +0.5</button>
            <button :class="{ on: repeat === 'fail' }" @click="repeat = 'fail'">没复述对 −1</button>
            <button :class="{ on: repeat === 'none' }" @click="repeat = 'none'">未提问</button>
          </div>
        </section>

        <section>
          <div class="label">回答问题得分（0.5 ~ 3）</div>
          <div class="row wrap">
            <button
              v-for="opt in answerOptions"
              :key="opt"
              :class="{ on: answer === opt }"
              @click="answer = opt"
            >{{ opt }}</button>
            <button :class="{ on: answer === null }" @click="answer = null">未作答</button>
          </div>
        </section>
      </template>

      <p v-if="error" class="err">{{ error }}</p>

      <footer>
        <span class="preview">本次预计 <b>{{ preview > 0 ? '+' : '' }}{{ preview }}</b> 分</span>
        <button class="submit" :disabled="submitting" @click="submit">
          {{ submitting ? '保存中…' : '确认记分' }}
        </button>
      </footer>
    </template>

    <div v-else class="done">
      <p>已记录：<b>{{ delta > 0 ? '+' : '' }}{{ delta }}</b> 分</p>
      <button class="submit" @click="emit('close')">关闭</button>
    </div>
  </aside>
</template>

<style scoped>
.card {
  position: fixed;
  right: 24px;
  bottom: 24px;
  width: 380px;
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 16px;
  padding: 18px;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.5);
  color: #e2e8f0;
  z-index: 10;
}
header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; }
header strong { font-size: 20px; }
.no { margin-left: 8px; color: #94a3b8; font-size: 13px; }
.close { background: none; border: 0; color: #64748b; font-size: 22px; cursor: pointer; line-height: 1; }
section { margin-bottom: 14px; }
.label { font-size: 12px; color: #94a3b8; margin-bottom: 6px; }
.row { display: flex; gap: 6px; }
.row.wrap { flex-wrap: wrap; }
.row button {
  flex: 1;
  min-width: 64px;
  padding: 8px 6px;
  font-size: 13px;
  background: #0f172a;
  border: 1px solid #334155;
  color: #cbd5e1;
  border-radius: 8px;
  cursor: pointer;
}
.row button.on { background: #4f46e5; border-color: #6366f1; color: #fff; }
footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 4px; }
.preview { font-size: 13px; color: #94a3b8; }
.preview b { color: #a5b4fc; font-size: 18px; }
.submit {
  padding: 10px 20px;
  border: 0;
  border-radius: 10px;
  background: #4f46e5;
  color: #fff;
  cursor: pointer;
  font-size: 14px;
}
.submit:disabled { opacity: 0.7; cursor: default; }
.err { color: #fca5a5; font-size: 13px; margin-bottom: 8px; }
.done { text-align: center; padding: 10px 0; }
.done p { font-size: 16px; margin-bottom: 12px; }
.done b { color: #34d399; font-size: 22px; }
</style>
