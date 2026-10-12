# DSH Agent Native 设计（一人一助手）

状态：已按产品确认落地。正式数据仍只在工作台账本；每人一个助手。

## 锁定含义

- 助手是主入口，不是侧栏聊天插件。
- 每人一个助手、一个工作区；PC 与钉钉同一登录名。
- PostgreSQL 是唯一账本。对话文件不是团队数据库。
- A 看不到 B 的待办、周报、对话。
- 项目 / 任务 / 周报 / 案例是同一助手的四项技能，不是四个机器人。
- 负责人仍只有一个助手，仅技能包更宽（`assist.analyze`）。
- **只有一套智能：DSH。** 助手用工具查账本、起草、确认后写库；工具路径不再调用工作台 `ai_platform` 套娃。页内旧「AI 帮填」不作为新系统主路径。

## 个人助手生命周期

1. 开通：账号有 `dsh.access` 且 `is_active`。第一次 SSO 建个人工作区（已有 ticket）。
2. 绑定：工号 = `username` = 工作区主人；ticket 带 `wb_user_id`。
3. 技能包：按角色下发（员工：项目/任务/周报/案例；负责人另加管控只读）。
4. 停用：`is_active=false` 后 introspect 不允许，工具接口 404，无法再办事。
5. 禁止：管理员接管他人会话改周报；全员共用一个 Bot。

## 工具门禁协议

- 调用方：DSH 门禁，机器签名 `x-service-id=dsh-gate`，密钥 `dsh_to_wb_secret`。
- 身份：body 必须有 `wb_user_id`，服务端加载该用户并套网页同一套 RBAC。
- 路径前缀：`POST /api/dsh/tools/*`
- 读：直接返回结构化 JSON。
- 起草：写入 `agent_drafts`，返回 `draft_id`，不改业务责任人。
- 确认写：`confirm-draft` 后走现有 service（换负责人需 `task.assign` + `can_manage_owner`）。
- 禁止：Agent 直连公网业务 API；禁止用管理员身份代查员工周报。
- **禁止工具内部再调大模型。** 检索用库内关键词；分析结论由 DSH 对话完成，工作台只给事实。

第一批工具：`my-todos`、`my-week-report`、`my-projects`、`search-cases`、`skill-pack`、`rank-todos`、`nudge`、`draft-reassign`、`confirm-draft`。

## 四技能职责与禁止

见 `docs/dsh-skills/`。共同禁止：编造进度、偷看他人数据、未确认改派、把正式记录只写在工作区文件里。

**整理后先问再写：** 助手从工作日记等整理出事项后，必须先提示用户确认「是否加入待办」「是否写成任务并挂项目」（可选问周报）；未同意不得 draft/confirm。工作区会种子文件 `工作台账本规则.md` 提醒同一纪律。

**账本 MCP 桥：** DSH 通过 `@deepseek-ai/dsh-mcp-client`（`serverName: workbench`）调用 `scripts/wb_mcp_server.py`，签名请求 `POST /api/dsh/tools/*`。SSO 换票写入 `$DSH_HOME/.wb_identity.json`（含 `wb_user_id`）。会话 `todo_write` / 文档 md 不算进账本。

## 待办 / 逾期按习惯

- 排自己的活：可自动生成今日顺序，不改责任人。
- 改派：只出草稿，有权限的人确认后才改。
- 通知：尊重免打扰；同一 `event_key` 只催一次。
- 交办不可由助手代为拒绝。

## 部署 142

- 机器 `30.251.255.142` 独立库、独立助手运行时。
- 禁止连接 `30.251.255.64` 正式库，禁止导入正式备份。
- 口令不进仓库。
