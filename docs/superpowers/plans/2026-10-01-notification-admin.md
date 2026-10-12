# 通知管理 Implementation Plan

> **For agentic workers:** Implement task-by-task. User rule: do not git commit unless asked.

**Goal:** 系统管理增加通知日志 + 通知模板；统一发送管道；PRD 三条定时提醒。

**Architecture:** `notify_pipeline` 读模板→渲染→站内/钉钉→写 delivery log；`reminders` 扩展扫描；asyncio 分钟级调度；AdminView 两 Tab。

**Tech Stack:** FastAPI, SQLAlchemy, Vue3, Element Plus, 钉钉工作通知

## Global Constraints

- 渠道仅 inbox + dingtalk
- 不碰正式业务删除；改库前可备份
- 权限 `admin.notify`
- 模板按 event_key insert-if-missing

## Tasks

1. Models + migrations + schemas
2. permissions + seed templates in init_service
3. notify_pipeline + refactor task_notify
4. reminders: phase + weekly report + pipeline
5. scheduler in lifespan
6. admin API
7. AdminView tabs
8. tests + README
