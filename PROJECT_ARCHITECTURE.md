# 项目支撑工作台 — 架构说明

## 部署隔离原则

```
┌─────────────────────────────────────────────────────────┐
│                    现有应用（不修改）                      │
│  learn-flow:7000（直连，不经 nginx）                       │
│  talent-profile:3000（直连，不经 nginx）                   │
│  MariaDB:3306                                              │
│  nginx:80 仅托管工作台 /aiworkbench/（本机142）               │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│              项目支撑工作台（独立隔离）                     │
│  nginx:80/aiworkbench → dist + proxy → backend:8100     │
│  开发时 web-pc:5180 ──proxy──> backend:8100 ──> PG:5432  │
│  库名 project_workbench，用户 workbench                   │
│  本机(142)代码目录 /home/aiworkbench；现网(64)为 /home/workbench │
│  mp-dingtalk / mp-wechat 独立小程序，调同一 API            │
└─────────────────────────────────────────────────────────┘
```

## 后端模块

| 模块 | 路径 | 职责 |
|------|------|------|
| API | `backend/app/api/` | REST 路由 |
| RBAC | `backend/app/rbac/` | 权限校验 |
| Models | `backend/app/models/` | SQLAlchemy 模型 |
| Services | `backend/app/services/` | 业务逻辑 |
| WebSocket | `backend/app/websocket/` | 站内信推送 |
| 方案库 | `backend/app/api/solutions.py` + `services/solution_*` | 历史方案上传、抽取、AI 打标 |

## 数据模型

User / Role / Permission → Project / Phase / Blocker / Task / WeeklyReport / Notification / AuditLog / LlmModel / Solution / SolutionTag

## 开发约定

- 所有开发服务绑定 `127.0.0.1`，不占用 7000/3000/5173
- 使用 `scripts/start-dev.sh` 启动，自动检测端口冲突
- 使用 `scripts/stop-dev.sh` 停止，仅杀本项目进程
- 生产上线需单独配置 nginx 子路径或子域名，不与现有站点混配

## AI 深度参与与低负担（应用级约束）

1. **人能不介入就不介入**：少表单、能默认则默认、能 AI 填则 AI 填；人只做「确认 / 改一处 / 拍板」。
2. **大模型深度参与全链路**：任务、周报、项目、卡点、方案库、总览统一走 `services/ai_platform.py`（按 purpose 路由模型）。
3. **落库前可确认**：AI 只出草稿；业务写库走原有 POST/PATCH。入口见 `api/ai_assist.py` 与任务上的 `/tasks/ai/*`。
4. **无模型时规则兜底**，不挡业务。
5. **交办轻量接收**：交办任务 `acceptance_status=pending`，责任人一键接收后才能改进度；不可拒绝；超时催办/改派。
