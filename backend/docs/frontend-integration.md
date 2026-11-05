# 前端联调说明

本地 Base URL：`http://127.0.0.1:8000`。交互文档：`/docs`。普通响应为 `{"code":0,"msg":"ok","data":...}`；错误检查 HTTP 状态及 `msg`。
导出返回文件二进制，不能调用 `response.json()`。

## 教师管理口令（本轮接口变更）

所有 `/api` 接口（包括名单、统计、导出）都必须带请求头 `X-Teacher-Key`。口令由后端负责人从本地 `.teacher-key.txt` 私下提供，不上传 Git，不写在前端源码或构建变量中。
前端可提供“输入教师口令”弹窗，在当前会话保存后随请求发送；只通过 HTTPS 访问部署后的服务。当前是共享教师口令，不是多教师账号系统。

```javascript
const teacherKey = sessionStorage.getItem('teacherKey'); // 来自教师实际输入
const headers = { 'X-Teacher-Key': teacherKey };
// fetch(url, { headers }); 记分时再添加 Content-Type: application/json。
```

Swagger 调试：打开 `/docs`，点击右上角 **Authorize**，粘贴口令本身，确认后再调用接口。没有口令或口令错误返回 401，后端未配置口令返回 503。`/health`、`/docs`、`/openapi.json` 无需口令。
浏览器导出必须使用带口令的 fetch 获取 blob 后下载，不能直接 window.open 导出链接，因为那样不会携带此请求头。

## 页面与接口

| 页面/操作 | 方法及路径 | 参数/用途 |
| --- | --- | --- |
| 导入 | POST `/api/students/import` | FormData 的 `file`；浏览器自动设置 multipart Content-Type |
| 名单 | GET `/api/students?page=1&size=20&keyword=张` | `data.items` 含 id、学号、姓名、专业、积分和次数 |
| 创建场次 | POST `/api/sessions` | `{"mode":"random"}` 或 `{"mode":"sequential"}`；自动关闭旧场次 |
| 恢复场次 | GET `/api/sessions/current` | `data=null` 表示没有进行中的场次 |
| 点下一位 | POST `/api/sessions/{id}/draw` | `data.rollcall.student` 展示姓名/学号；保存 rollcall.id |
| 记分 | POST `/api/rollcalls/{id}/score` | `{"arrived":true,"repeat_ok":true,"answer_score":2}` |
| 改判 | PATCH `/api/rollcalls/{id}/score` | 与记分相同；写积分差额，重复 POST 会返回 409 |
| 转移 | POST `/api/rollcalls/{id}/transfer` | `{"to_student_id":12}`；用响应中的新 rollcall.id 继续记分 |
| 抽签复核 | GET `/api/rollcalls/{id}/audit` | 原中签者、候选概率、随机键、复算结果及状态 |
| 排行 | GET `/api/stats/ranking?top=10` | `categories` 姓名、`points_series` 积分、`calls_series` **随机**点名次数 |
| 个人积分曲线 | GET `/api/stats/students/{student_id}/points` | `categories` 与 `points_series` 可直接画图；items 含流水解释 |
| 概率盘 | GET `/api/stats/probability` | 可传 `session_id`、`alpha`；默认当前场次 |
| 历史 | GET `/api/stats/history?page=1&size=20` | 可按 session_id 筛选，返回倒序记录 |
| 导出 | GET `/api/export/points` | xlsx；学号、姓名、专业、随机点名次数、总积分 |
| 规则 | GET `/api/stats/config` | 显示分值与转移权规则 |
| 结束 | POST `/api/sessions/{id}/close` | 结束当前课次 |

## 推荐调用顺序

1. 上传 Excel，显示 inserted/updated 和 failed 错误行。再次上传不会清零积分。
2. 新建或恢复场次，获取下一次点名概率。
3. 点击“开始点名”后只请求一次 draw；用服务端返回的学生作为动画最终结果。动画名单不决定中签者。
4. 弹出记分；复述未提问时传 null，未回答传 0/null，回答得分允许 0.5～3。缺席必须将 repeat_ok 清为 null、answer_score 清为 0/null，否则返回 400。原始回答分数先验证范围，再保存一位小数。
5. 记分成功后刷新名单、排行榜、概率盘和历史；再允许下一次点名。请求期间禁用重复点击。
6. 改判后刷新个人积分曲线；旧流水保留，新的 manual 流水表示差额，不是第二次获分。

## 概率显示约定

- `probability` 是 0～1 的比例，显示百分比时乘 100。
- `round_no` 表示**下一次点名所属轮次**；`is_new_round=true` 表示下次点名将换轮。
- 查询概率不修改场次；全班点完后，预览会重新把所有学生纳入下一轮。
- 随机模式排除本轮已点过者；顺序模式下一位概率为 1，其余为 0。概率为 0 不一定代表 excluded（也可能只是顺序尚未到）。
- 明确指定不存在/已关闭的场次会返回 404/409。无进行中场次且未指定 id 时，显示全名单随机模式的预览。
- alpha 仅对本次请求生效；如果界面可调 alpha，draw 与 probability 必须传同一个值，默认统一使用服务端配置。

## 个人积分曲线

```javascript
const response = await fetch(`${baseURL}/api/stats/students/${studentId}/points`, {
  headers: { 'X-Teacher-Key': sessionStorage.getItem('teacherKey') }
});
const body = await response.json();
if (!response.ok || body.code !== 0) throw new Error(body.msg);
const option = {
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'category', data: body.data.categories },
  yAxis: { type: 'value', name: '积分' },
  series: [{ name: '累计积分', type: 'line', data: body.data.points_series }]
};
```

初始点为 0；每笔账本条目新增一个点，到场、复述、回答可能属于同一次点名。悬浮解释使用 items，对应图表下标减 1；初始点没有流水。

## 当前边界

次数在点名成功时立即计入，记分/改判不会重复计数。转移的目标增加总次数，原中签者保留随机次数，目标不增加随机次数。页面中建议完成本次记分后再点下一位。
数据按一个班级共享，使用共享教师口令。数据库中的 write_locks 单行锁配合 MySQL/TiDB 的 READ COMMITTED 读取串行化写事务，已覆盖并发记分、点名、开课、改判及转移；不是多班级或多教师权限系统。
转移只允许当前开启场次的当前轮次；已结束场次可保留历史补记分/改判，不能再转移。
相同点名的重复记分返回 409；数据库临时不可用返回 503。前端应显示 msg 并刷新状态，避免遇到错误后盲目连续重复提交。
跨域时将前端真实 Origin 写入后端 CORS_ORIGINS（逗号分隔），重启生效。

## 抽签复核面板

点名成功后用 rollcall.id 调用 `/api/rollcalls/{id}/audit`，可以做“查看本次抽签”面板：

- `winner_id` 是原始抽签中签者；发生转移后不一定等于 `rollcall_student_id`。
- `candidates` 展示候选的 weight、probability、rand_u、stored_key、recomputed_key 和 key_matches。
- `status=verified` 表示保存的键值和中签结果均可复算一致；`mismatch` 表示记录不完整或不一致。
- `not_applicable` 是顺序模式，`unavailable` 是非标准算法，二者 verified 为 null，不能显示为“随机已验证”。
- `precision_limited` 表示中签者复算一致，但保存的键值只能近似复现（例如旧版 FLOAT 快照）；verified 为 null，建议显示“历史记录精度受限”，不能显示为精确验证通过。新记录采用 DOUBLE。
- 概率由历史权重归一化，不使用当前积分重新计算；姓名、学号是当前名单信息。
- 面板说明应保留 limitation：这是保存记录的一致性核对，不是独立的密码学公平性保证。
