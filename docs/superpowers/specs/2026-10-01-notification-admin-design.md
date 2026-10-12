# 通知管理与模板配置 — 设计规格

> 状态：已确认（用户 2026-10-01 同意：范围 1B + 仅站内/钉钉）

## 目标

在「系统管理」中新增：

1. **通知日志**：查看每次发送记录（对象、渠道、内容摘要、成功/失败）
2. **通知模板**：编辑格式、渠道开关、频率（定时类）

## 范围

### 渠道

- 站内推送（WebSocket；同时写入既有 `notifications` 表便于留存）
- 钉钉工作通知
- 不做邮件 / 微信

### 事件类型（模板 event_key）

**即时类**

| event_key | 说明 |
|-----------|------|
| `task_assigned` | 交办任务 |
| `task_accepted` | 任务已接收 |
| `task_remind` | 催办 |
| `task_completed` | 交办完成 |
| `task_priority_p0` | P0 重点 |
| `project_role_assigned` | 项目角色指派 |
| `blocker_declared` | 辅助人申报卡点 |

**定时类（PRD）**

| event_key | 说明 | 默认频率 |
|-----------|------|----------|
| `blocker_follow_up_overdue` | 卡点跟进到期 | 每日 09:00；同一卡点同一天最多 1 次 |
| `phase_overdue` | 阶段计划逾期 | 每日 09:00；同一阶段同一天最多 1 次 |
| `weekly_report_overdue` | 周报未交 | 每周一 12:00 |

### 非目标（本期）

- 员工端消息中心 UI
- 邮件/微信通道
- WebSocket JWT 加固（可另开任务）

## 数据模型

### `notification_templates`

- `id`, `event_key`（唯一）, `name`, `description`
- `title_template`, `body_template`（`{var}` 占位）
- `channel_inbox` bool, `channel_dingtalk` bool
- `is_enabled` bool
- `schedule_cron` varchar（定时类：如 `0 9 * * *`；即时类空）
- `max_per_day` int（防刷，默认 1；即时类可为 0 表示不限）
- `quiet_start` / `quiet_end` time nullable（安静时段）
- `updated_at`, `created_at`

### `notification_delivery_logs`

- `id`, `event_key`, `template_id` nullable
- `user_id`, `channel`（`inbox` / `dingtalk`）
- `title`, `content`
- `status`（`success` / `skipped` / `failed`）
- `error_message` text
- `ref_type` / `ref_id`（如 task/blocker/phase）
- `created_at`

索引：`(created_at DESC)`, `(event_key, created_at)`, `(user_id, created_at)`

## 统一发送管道

`services/notify_pipeline.py`：

1. 按 `event_key` 读模板；禁用则跳过并可选记 log
2. 渲染 title/body
3. 检查安静时段 / 当日次数（定时类）
4. 按渠道开关：站内（WS + 写 `notifications`）+ 钉钉
5. 每渠道路径写一条 `notification_delivery_logs`

现有 `task_notify.notify_users` 改为走管道（保留函数签名兼容）。

## 定时调度

- 后端进程内用 APScheduler（或轻量 asyncio 循环）按模板 `schedule_cron` 触发扫描
- 扫描逻辑扩展 `reminders.py`：卡点 / 阶段 / 周报未交
- 保留手动 `POST /api/admin/reminders/...` 便于联调

## 权限与 API

- 权限码：`admin.notify`（默认挂 admin 角色）
- `GET /api/admin/notification-logs` 筛选分页
- `GET/PATCH /api/admin/notification-templates`
- 改模板写 `AuditLog`

## 前端

在 `AdminView.vue` 增加两个 Tab（与现有审计/模型一致）：

- 通知日志：表格 + 事件/渠道/状态筛选
- 通知模板：列表 + 编辑抽屉（文案、渠道、频率）

## 种子数据

启动 `init_rbac` / 新 init 函数写入上表默认模板（不覆盖管理员已改过的行：按 event_key 仅 insert-if-missing）。
