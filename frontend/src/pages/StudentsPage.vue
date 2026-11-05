<script setup>
import { computed, onMounted, ref } from 'vue'
import * as XLSX from 'xlsx'
import { api } from '../api'

const PAGE_SIZE = 20

// ---------- 班级 ----------
const classes = ref([])
const totalStudents = ref(0)
const selectedName = ref('')      // 当前班级名，'' = 全部
const classModal = ref(null)      // { mode: 'create' | 'rename', name }
const classSaving = ref(false)

// ---------- 名单 ----------
const rows = ref([])
const total = ref(0)
const page = ref(1)
const keyword = ref('')
const loading = ref(false)
const error = ref('')
const toast = ref('')
let loadToken = 0

// ---------- 导入 ----------
const fileInput = ref(null)
const importing = ref(false)
const importResult = ref(null)

// ---------- 编辑（表单恒为对象，不出现 null，避免 patch 空对象报错）----------
const editingId = ref(null)
const editForm = ref({ name: '', major: '', class_name: '' })
const saving = ref(false)

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const selectedClass = computed(() => classes.value.find((c) => c.name === selectedName.value) || null)

function flash(text) {
  toast.value = text
  setTimeout(() => { toast.value = '' }, 2200)
}

async function loadClasses() {
  const data = await api.get('/api/classes')
  classes.value = data.items
  totalStudents.value = data.total_students
  if (selectedName.value && !data.items.some((c) => c.name === selectedName.value)) {
    selectedName.value = ''      // 班级被删/改名后回退到全部
  }
}

async function loadStudents(className = selectedName.value) {
  const token = ++loadToken
  loading.value = true
  error.value = ''
  try {
    const query = new URLSearchParams({ page: page.value, size: PAGE_SIZE })
    if (keyword.value.trim()) query.set('keyword', keyword.value.trim())
    if (className) query.set('class_name', className)
    const data = await api.get(`/api/students?${query}`)
    if (token !== loadToken) return false
    // 脏数据自检：返回的学生必须都属于当前班
    if (className && data.items.some((s) => (s.class_name || '') !== className)) {
      return false
    }
    rows.value = data.items
    total.value = data.total
    return true
  } catch (e) {
    if (token === loadToken) error.value = e.message
    return false
  } finally {
    if (token === loadToken) loading.value = false
  }
}

async function refresh() {
  try {
    await loadClasses()
    await loadStudents(selectedName.value)
  } catch (e) {
    error.value = e.message
  }
}

function selectClass(name) {
  if (selectedName.value === name) return
  selectedName.value = name
  page.value = 1
  editingId.value = null
  loadStudents(name)
}

function search() {
  page.value = 1
  loadStudents(selectedName.value)
}

function turn(delta) {
  const next = page.value + delta
  if (next < 1 || next > totalPages.value) return
  page.value = next
  loadStudents(selectedName.value)
}

/* ---------- 班级增删改 ---------- */
function openCreateClass() {
  classModal.value = { mode: 'create', name: '' }
}

function openRenameClass() {
  const target = selectedClass.value
  if (!target) return
  if (!target.id) {
    error.value = '这个班级还没登记成班级记录，先运行后端 scripts/backfill_classes.py'
    return
  }
  classModal.value = { mode: 'rename', name: target.name }
}

async function submitClass() {
  const name = (classModal.value?.name || '').trim()
  if (!name) {
    error.value = '班级名称不能为空'
    return
  }
  classSaving.value = true
  error.value = ''
  try {
    if (classModal.value.mode === 'create') {
      await api.post('/api/classes', { name })
      flash(`已新建班级「${name}」`)
    } else {
      const target = selectedClass.value
      if (!target) return
      await api.patch(`/api/classes/${target.id}`, { name })
      selectedName.value = name      // 改名后跟着走
      flash(`已改名为「${name}」`)
    }
    classModal.value = null
    await refresh()
  } catch (e) {
    error.value = e.message
  } finally {
    classSaving.value = false
  }
}

async function removeClass() {
  const target = selectedClass.value
  if (!target?.id) return
  const count = target.student_count || 0
  const tip = count
    ? `确定删除班级「${target.name}」？\n\n该班 ${count} 名学生会一起被删除，他们的点名记录和积分流水也会一起清除，不可恢复。`
    : `确定删除班级「${target.name}」？`
  if (!window.confirm(tip)) return
  error.value = ''
  try {
    const res = await api.delete(`/api/classes/${target.id}${count ? '?force=true' : ''}`)
    const n = (res && res.deleted_students) || 0
    selectedName.value = ''
    flash(n ? `已删除班级「${target.name}」及 ${n} 名学生` : `已删除班级「${target.name}」`)
    await refresh()
  } catch (e) {
    error.value = e.message
  }
}

/* ---------- 导入 ---------- */
function pickFile() {
  fileInput.value?.click()
}

async function onFileChange(event) {
  const file = event.target.files?.[0]
  if (!file) return
  importing.value = true
  error.value = ''
  importResult.value = null
  try {
    const fields = selectedName.value ? { class_name: selectedName.value } : {}
    importResult.value = await api.upload('/api/students/import', file, fields)
    page.value = 1
    await refresh()
  } catch (e) {
    error.value = e.message
  } finally {
    importing.value = false
    event.target.value = ''
  }
}

/* ---------- 学生编辑 ---------- */
function startEdit(student) {
  editingId.value = student.id
  editForm.value = {
    name: student.name,
    major: student.major || '',
    class_name: student.class_name || '',
    student_no: student.student_no,
  }
}

function cancelEdit() {
  editingId.value = null
}

/** 保存：复用按学号幂等导入，只提交这一行，积分不受影响 */
async function saveEdit() {
  if (editingId.value === null || saving.value) return
  const name = (editForm.value.name || '').trim()
  if (!name) {
    error.value = '姓名不能为空'
    return
  }
  saving.value = true
  error.value = ''
  try {
    const sheet = XLSX.utils.aoa_to_sheet([
      ['学号', '姓名', '专业', '班级'],
      [
        editForm.value.student_no,
        name,
        (editForm.value.major || '').trim(),
        (editForm.value.class_name || '').trim(),
      ],
    ])
    const book = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(book, sheet, '学生名单')
    const buffer = XLSX.write(book, { type: 'array', bookType: 'xlsx' })
    const file = new File([buffer], 'edit.xlsx', {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    })
    await api.upload('/api/students/import', file)
    editingId.value = null
    await refresh()
    flash('已保存')
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

async function removeStudent(student) {
  if (!window.confirm(`确定删除学生「${student.name}（${student.student_no}）」？`)) return
  error.value = ''
  try {
    await api.delete(`/api/students/${student.id}`)
    flash(`已删除「${student.name}」`)
    await refresh()
  } catch (e) {
    error.value = e.message
  }
}

onMounted(refresh)
</script>

<template>
  <div class="layout">
    <!-- 左侧班级 -->
    <aside class="side">
      <div class="side-head">
        <span class="side-title">班级</span>
        <button class="mini" @click="openCreateClass">+ 新建</button>
      </div>

      <button :class="['cls', { on: !selectedName }]" @click="selectClass('')">
        <span>全部学生</span><b>{{ totalStudents }}</b>
      </button>

      <button
        v-for="c in classes"
        :key="c.id + '-' + c.name"
        :class="['cls', { on: selectedName === c.name }]"
        @click="selectClass(c.name)"
      >
        <span>
          {{ c.name }}
          <i v-if="!c.id" class="tag">未登记</i>
        </span>
        <b>{{ c.student_count }}</b>
      </button>

      <p v-if="!classes.length && !loading" class="side-empty">还没有班级，点「+ 新建」</p>
    </aside>

    <!-- 右侧名单 -->
    <section class="main">
      <header>
        <div>
          <div class="title-row">
            <h2>{{ selectedName || '全部学生' }}</h2>
            <span class="pager-inline">
              <button :disabled="page <= 1" @click="turn(-1)">上一页</button>
              <span class="page-num">{{ page }} / {{ totalPages }}</span>
              <button :disabled="page >= totalPages" @click="turn(1)">下一页</button>
            </span>
          </div>
          <p class="sub">共 {{ total }} 人</p>
        </div>
        <div class="actions">
          <input v-model="keyword" class="search" placeholder="搜学号 / 姓名 / 专业" @keyup.enter="search" />
          <button @click="search">搜索</button>
          <div class="divider"></div>
          <button v-if="selectedName" @click="openRenameClass">改名</button>
          <button v-if="selectedName" class="danger" @click="removeClass">删除班级</button>
          <button class="primary" :disabled="importing" @click="pickFile">
            {{ importing ? '导入中…' : (selectedName ? `导入到「${selectedName}」` : '导入 Excel') }}
          </button>
          <input ref="fileInput" type="file" accept=".xlsx,.xlsm" hidden @change="onFileChange" />
        </div>
      </header>

      <p class="hint">
        Excel 需含 <span class="req">学号</span>、<span class="req">姓名</span>，<b>专业</b>、<b>班级</b> 可选。
        <span v-if="selectedName">
          当前在「<b class="cls-name">{{ selectedName }}</b>」内导入：整批学生会归入该班（覆盖 Excel 的班级列）。
        </span>
        <span v-else>按学号幂等导入：重复上传只更新姓名/专业/班级，不会清掉积分。</span>
      </p>

      <p v-if="error" class="err">{{ error }}</p>

      <section v-if="importResult" class="result">
        <div class="line">
          读到 <b>{{ importResult.total_rows }}</b> 行 ·
          新增 <b class="ok">{{ importResult.inserted }}</b> ·
          更新 <b class="ok">{{ importResult.updated }}</b> ·
          失败 <b :class="{ bad: importResult.failed.length }">{{ importResult.failed.length }}</b>
        </div>
        <div v-if="Object.keys(importResult.header_map || {}).length" class="map">
          表头匹配：
          <span v-for="(v, k) in importResult.header_map" :key="k" class="chip">{{ k }} ← {{ v }}</span>
        </div>
        <ul v-if="importResult.failed.length" class="failed">
          <li v-for="(f, i) in importResult.failed" :key="i">第 {{ f.row }} 行：{{ f.reason }}</li>
        </ul>
        <button class="close" @click="importResult = null">知道了</button>
      </section>

      <table>
        <thead>
          <tr>
            <th class="idx">序号</th>
            <th>学号</th><th>姓名</th><th>专业</th><th>班级</th>
            <th class="num">总积分</th><th class="num">被点次数</th><th class="num">随机次数</th>
            <th class="op"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(s, i) in rows" :key="s.id">
            <td class="idx">{{ (page - 1) * PAGE_SIZE + i + 1 }}</td>
            <td>{{ s.student_no }}</td>

            <td>
              <input v-if="editingId === s.id" v-model="editForm.name" class="cell-input" />
              <span v-else>{{ s.name }}</span>
            </td>
            <td>
              <input v-if="editingId === s.id" v-model="editForm.major" class="cell-input" />
              <span v-else>{{ s.major || '-' }}</span>
            </td>
            <td>
              <input v-if="editingId === s.id" v-model="editForm.class_name" class="cell-input" />
              <span v-else>{{ s.class_name || '-' }}</span>
            </td>

            <td class="num">{{ s.total_points }}</td>
            <td class="num">{{ s.call_count }}</td>
            <td class="num">{{ s.random_call_count }}</td>
            <td class="op">
              <button v-if="editingId === s.id" class="edit save" :disabled="saving" @click="saveEdit">保存</button>
              <button v-else class="edit" @click="startEdit(s)">编辑</button>
              <button v-if="editingId === s.id" class="del" :disabled="saving" @click="cancelEdit">取消</button>
              <button v-else class="del" @click="removeStudent(s)">删</button>
            </td>
          </tr>
          <tr v-if="!rows.length && !loading">
            <td colspan="9" class="empty">
              {{ selectedName ? '这个班级下还没有学生，可点右上角导入' : '还没有学生，点右上角「导入 Excel」上传名单' }}
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 新建 / 改名班级 -->
    <div v-if="classModal" class="mask" @click.self="classModal = null">
      <div class="modal small">
        <h3>{{ classModal.mode === 'create' ? '新建班级' : '修改班级名称' }}</h3>
        <label>
          <span>班级名称</span>
          <input v-model="classModal.name" placeholder="例如 周一1,2节" @keyup.enter="submitClass" />
        </label>
        <p v-if="classModal.mode === 'rename'" class="tip">改名会把该班所有学生的班级一起改掉</p>
        <footer>
          <button class="ghost" :disabled="classSaving" @click="classModal = null">取消</button>
          <button class="primary" :disabled="classSaving" @click="submitClass">
            {{ classSaving ? '保存中…' : '确定' }}
          </button>
        </footer>
      </div>
    </div>

    <div v-if="toast" class="toast">{{ toast }}</div>
  </div>
</template>

<style scoped>
.layout { display: flex; min-height: calc(100vh - 52px); color: #cbd5e1; }

/* 左侧班级 */
.side {
  width: 250px;
  flex-shrink: 0;
  border-right: 1px solid #1e293b;
  padding: 18px 12px;
  background: #0b1220;
}
.side-head { display: flex; align-items: center; justify-content: space-between; padding: 0 8px 10px; }
.side-title { font-size: 12px; color: #64748b; letter-spacing: 0.1em; }
.mini {
  font-size: 11px;
  padding: 3px 8px;
  background: transparent;
  border: 1px solid #334155;
  color: #94a3b8;
  border-radius: 999px;
  cursor: pointer;
}
.mini:hover { border-color: #6366f1; color: #c7d2fe; }
.cls {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 9px 12px;
  margin-bottom: 4px;
  background: transparent;
  border: 0;
  border-radius: 8px;
  color: #94a3b8;
  font-size: 14px;
  cursor: pointer;
  text-align: left;
}
.cls:hover { background: #111c30; color: #e2e8f0; }
.cls.on { background: #1e293b; color: #fff; }
.cls b { font-size: 12px; color: #64748b; font-weight: 500; }
.cls.on b { color: #a5b4fc; }
.tag {
  font-style: normal;
  font-size: 10px;
  color: #f59e0b;
  border: 1px solid #78350f;
  border-radius: 4px;
  padding: 0 4px;
  margin-left: 4px;
}
.side-empty { color: #475569; font-size: 13px; padding: 8px 10px; line-height: 1.6; }

/* 右侧 */
.main { flex: 1; padding: 22px 30px 44px; min-width: 0; }
header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 8px; flex-wrap: wrap; }
.title-row { display: flex; align-items: center; gap: 16px; }
.pager-inline { display: flex; align-items: center; gap: 8px; }
.pager-inline button {
  padding: 4px 12px;
  font-size: 12px;
  background: #1e293b;
  border: 1px solid #334155;
  color: #cbd5e1;
  border-radius: 6px;
  cursor: pointer;
}
.pager-inline button:hover:not(:disabled) { border-color: #475569; }
.pager-inline button:disabled { opacity: 0.35; cursor: default; }
.page-num { font-size: 12px; color: #94a3b8; font-variant-numeric: tabular-nums; }
h2 { font-size: 20px; font-weight: 600; color: #e2e8f0; margin: 0; }
.sub { color: #64748b; font-size: 13px; margin: 4px 0 0; }
.actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.divider { width: 1px; height: 22px; background: #1e293b; }
.search {
  padding: 8px 12px;
  width: 200px;
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  color: #e2e8f0;
  font-size: 13px;
}
.actions button {
  padding: 8px 14px;
  background: #1e293b;
  color: #cbd5e1;
  border: 1px solid #334155;
  border-radius: 8px;
  font-size: 13px;
  cursor: pointer;
  white-space: nowrap;
}
.actions button.primary { background: #4f46e5; border-color: #6366f1; color: #fff; }
.actions button.danger { color: #fca5a5; border-color: #7f1d1d; }
.actions button:disabled { opacity: 0.7; cursor: default; }

.hint { color: #64748b; font-size: 13px; margin: 6px 0 14px; line-height: 1.7; }
.hint b { color: #94a3b8; }
.hint .req { color: #f87171; font-weight: 600; }
.hint .cls-name { color: #a5b4fc; }
.err { color: #fca5a5; margin-bottom: 12px; font-size: 13px; }

.result {
  position: relative;
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 12px;
  padding: 14px 16px;
  margin-bottom: 16px;
  font-size: 13px;
}
.result .line b { color: #e2e8f0; }
.result .ok { color: #34d399; }
.result .bad { color: #f87171; }
.map { margin-top: 8px; color: #94a3b8; }
.chip {
  display: inline-block;
  margin: 4px 6px 0 0;
  padding: 2px 8px;
  background: #0f172a;
  border-radius: 999px;
  font-size: 12px;
}
.failed { margin: 8px 0 0; padding-left: 18px; color: #fca5a5; }
.close {
  position: absolute;
  right: 12px;
  top: 12px;
  background: transparent;
  border: 1px solid #334155;
  color: #94a3b8;
  border-radius: 8px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
}

table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { padding: 9px 8px; border-bottom: 1px solid #1e293b; text-align: left; }
th { color: #94a3b8; font-weight: 500; font-size: 13px; }
.num { font-variant-numeric: tabular-nums; text-align: right; }
.idx { width: 60px; color: #64748b; font-variant-numeric: tabular-nums; }
.empty { text-align: center; color: #64748b; padding: 28px; }
.op { width: 104px; text-align: right; white-space: nowrap; }
button.edit, button.del {
  padding: 4px 9px;
  font-size: 12px;
  background: transparent;
  border: 1px solid #334155;
  color: #94a3b8;
  border-radius: 6px;
  cursor: pointer;
}
button.edit:hover { border-color: #6366f1; color: #c7d2fe; }
button.edit.save { background: #4f46e5; border-color: #6366f1; color: #fff; }
button.del { margin-left: 4px; }
button.del:hover { border-color: #b91c1c; color: #fca5a5; }
button.edit:disabled, button.del:disabled { opacity: 0.6; cursor: default; }

.cell-input {
  width: 100%;
  padding: 5px 8px;
  background: #0f172a;
  border: 1px solid #4f46e5;
  border-radius: 6px;
  color: #e2e8f0;
  font-size: 13px;
}

.pager { display: flex; align-items: center; justify-content: space-between; margin-top: 14px; font-size: 13px; color: #64748b; }
.pager button {
  margin-left: 8px;
  padding: 6px 14px;
  background: #1e293b;
  border: 1px solid #334155;
  color: #cbd5e1;
  border-radius: 8px;
  cursor: pointer;
}
.pager button:disabled { opacity: 0.4; cursor: default; }

/* 弹窗 */
.mask {
  position: fixed;
  inset: 0;
  background: rgba(2, 6, 23, 0.72);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
}
.modal {
  width: 380px;
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 16px;
  padding: 20px 22px;
  color: #e2e8f0;
}
.modal.small { width: 340px; }
.modal h3 { font-size: 16px; font-weight: 600; margin-bottom: 14px; }
.modal label { display: block; margin-bottom: 12px; }
.modal label span { display: block; font-size: 12px; color: #94a3b8; margin-bottom: 5px; }
.modal input {
  width: 100%;
  padding: 9px 12px;
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 8px;
  color: #e2e8f0;
  font-size: 14px;
}
.modal .tip { color: #475569; font-size: 11px; margin: -6px 0 12px; }
.modal footer { display: flex; justify-content: flex-end; gap: 8px; margin-top: 6px; }
.modal footer button {
  padding: 9px 20px;
  border-radius: 8px;
  font-size: 14px;
  cursor: pointer;
  border: 1px solid #334155;
  background: #0f172a;
  color: #cbd5e1;
}
.modal footer button.primary { background: #4f46e5; border-color: #6366f1; color: #fff; }
.modal footer button:disabled { opacity: 0.7; cursor: default; }

.toast {
  position: fixed;
  left: 50%;
  bottom: 40px;
  transform: translateX(-50%);
  background: #065f46;
  color: #d1fae5;
  padding: 10px 22px;
  border-radius: 999px;
  font-size: 14px;
  z-index: 60;
}
</style>
