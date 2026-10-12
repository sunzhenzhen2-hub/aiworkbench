# 周报工作台全面重构设计（方案二）

日期：2026-09-13

## 目标

将「周报看板」升级为人员周报工作台：

1. 主页卡片式，不展示周报全文
2. 点人查看本期/历史周报、提交概览、任务完成度、一阶段 LLM 评价
3. 同一任务展示进度链（默认展开）与事件链（可折叠）
4. 本期与历史提交/未提交名单，便于考核催办
5. PC 与钉钉 H5 同步

## 产品决策

- 主页：人员卡片 + 已交/未交总览条
- 详情：抽屉（PC）/ 全屏页（钉钉）
- LLM：点开自动生成并落库；`force` 可重评
- 原按项目对齐迁到「项目」Tab

## 后端

- `GET /board?mode=summary|full`：summary 不嵌全文，附带近 N 周提交率
- `GET /submission-matrix`：周期 × 人员矩阵
- `GET /users/{id}/workbench`：详情聚合 + 自动 phase1 评价
- `GET /users/{id}/timelines`：进度链 + 可选任务事件链
- `POST /{report_id}/ai-review`：强制重评
- 表 `progress_report_reviews`，purpose `report_review`

## 前端

- PC：`ReportBoardView` 拆为人员板、项目板、人员抽屉、双链组件
- 钉钉：卡片墙 + `/report-board/person/:userId`

## 非目标

- Roster 配置化、二阶段人工打分 UI、旧 weekly_reports 合并、钉钉催办消息
