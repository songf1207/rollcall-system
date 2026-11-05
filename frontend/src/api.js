/**
 * 后端请求封装。
 * 所有 /api 接口都必须带请求头 X-Teacher-Key（后端教师口令鉴权）。
 * 统一响应格式：{ code: 0, msg: "ok", data: ... }
 *
 * 地址策略：
 * - 开发（npm run dev）：直连本机后端 http://127.0.0.1:8000
 * - 打包上线（npm run build）：走相对路径，前后端同一个域名（后端直接用 FastAPI 托管 dist）
 * - 需要分离部署时，用 VITE_API_BASE 显式指定后端地址
 */
const DEFAULT_BASE = import.meta.env.DEV ? 'http://127.0.0.1:8000' : ''
const BASE = (import.meta.env.VITE_API_BASE ?? DEFAULT_BASE).replace(/\/$/, '')

const KEY_NAME = 'teacherKey'

export function getKey() {
  return sessionStorage.getItem(KEY_NAME) || ''
}

export function setKey(value) {
  sessionStorage.setItem(KEY_NAME, (value || '').trim())
}

export function clearKey() {
  sessionStorage.removeItem(KEY_NAME)
}

async function request(path, { method = 'GET', body, form } = {}) {
  const headers = { 'X-Teacher-Key': getKey() }
  let payload
  if (form) {
    payload = form // FormData：交给浏览器自己设 multipart 边界，别手动设 Content-Type
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }

  const res = await fetch(BASE + path, { method, headers, body: payload, cache: 'no-store' })

  if (res.status === 401) throw new Error('教师口令不对或未填写（401）')
  if (res.status === 503) throw new Error('后端未配置口令或数据库不可用（503）')

  const isJson = (res.headers.get('content-type') || '').includes('application/json')
  if (!isJson) throw new Error(`请求失败：HTTP ${res.status}`)

  const json = await res.json()
  if (!res.ok || json.code !== 0) throw new Error(json.msg || `请求失败：HTTP ${res.status}`)
  return json.data
}

export const api = {
  get: (path) => request(path),
  post: (path, body) => request(path, { method: 'POST', body }),
  patch: (path, body) => request(path, { method: 'PATCH', body }),
  delete: (path) => request(path, { method: 'DELETE' }),
  upload: (path, file, fields = {}) => {
    const form = new FormData()
    form.append('file', file)
    for (const [key, value] of Object.entries(fields)) {
      if (value !== undefined && value !== null && value !== '') form.append(key, value)
    }
    return request(path, { method: 'POST', form })
  },
}

/**
 * 导出下载：必须用带口令的 fetch 拿 blob。
 * 直接 window.open 导出地址不会带 X-Teacher-Key，会 401。
 */
export async function downloadFile(path, fallbackName = 'download.xlsx') {
  const res = await fetch(BASE + path, { headers: { 'X-Teacher-Key': getKey() } })
  if (!res.ok) throw new Error(`导出失败：HTTP ${res.status}`)

  // 从 Content-Disposition 里取中文文件名（后端按 RFC 5987 编码）
  let filename = fallbackName
  const cd = res.headers.get('content-disposition') || ''
  const match = /filename\*=UTF-8''([^;]+)/i.exec(cd)
  if (match) filename = decodeURIComponent(match[1])

  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
