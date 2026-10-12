# 技能：任务

用途：口语变任务、看待办、接收交办提醒、拆解。

- 读待办：`mcp__workbench__my_todos`（不是会话 `todo_write`）。
- **写待办**：`mcp__workbench__draft_todo` → 确认 → `confirm_draft`（默认挂「日常任务」，可一次 `items` 多条）。
- 可见项目先 `mcp__workbench__my_projects`。
- 交办不可拒绝；改派走账本工具（需权限确认）。
- 截止日期、P 级、负责人未确认不得擅自改账本。
- 新建立项任务：先用 `ask_user_question` 弹出选择，再选项目，再 `mcp__workbench__draft_task`（project_id、title、due_date），再用选择框确认后 `mcp__workbench__confirm_draft`。
- 拆解、润色不要走工作台页内 AI，在对话里改完再请人确认写库。

## 写库前门禁（必做）

- 用户贴工作日记或让你「整理任务」时：先产出清单，再**调用 `ask_user_question` 弹出选择框**（见个人助手技能），不要让用户打字「待办是/否」。
- 选「加入待办」→ `mcp__workbench__draft_todo`（不必选项目）。
- 选「写成任务」并选了项目 → `mcp__workbench__draft_task`。
- 用户只说「好看」「收到」「继续整理」≠ 同意写库。
- 文档写在工作区 ≠ 任务/待办已入库；入库只能靠 MCP draft → 选择框确认 → confirm_draft。
- 工具失败时如实告知，禁止假装已创建。
