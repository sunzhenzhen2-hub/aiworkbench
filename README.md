# 项目支撑工作台

面向 10-20 人中小团队的多项目管理工作台。详见 [项目支撑工作台-PRD.md](./项目支撑工作台-PRD.md)。

**需求基线（2026-09-13 已确认）**：[docs/需求核对-已确认.md](./docs/需求核对-已确认.md)（角色映射、任务自动进周报草稿、员工可看本人评价）。

## 全局规范（重要）

### 红线：正式业务数据不能动 + 定期备份

- **禁止**删除或改写真实员工、真实立项、「日常任务」、正式周报/任务
- 任何可能改库的操作（含清理测试数据、迁移）之前，先备份：
  ```bash
  bash scripts/backup.sh
  ```
- 备份目录：`backups/project_workbench_*.sql.gz`（只读导出，不改库内数据）；约保留 30 天
- 已启用每日定时备份：`workbench-backup.timer`（约每天 02:30）
  ```bash
  systemctl status workbench-backup.timer
  ls -lt backups/
  ```
- Cursor 规则：[`.cursor/rules/protect-production-data.mdc`](.cursor/rules/protect-production-data.mdc)

### 测试完成后必须清理测试数据与测试账号

凡联调、E2E、手工验收、临时造数写入了正式库或共享开发库，**会话收尾前必须删除**测试账号与测试数据，禁止把脏数据留给下一轮。

1. **先备份**（见上一节），再预览删除（在 `backend` 目录）：
   ```bash
   bash ../scripts/backup.sh
   PYTHONPATH=. .venv/bin/python ../scripts/purge_test_data.py --dry-run
   # 确认名单无真实员工、真实业务项目后：
   PYTHONPATH=. .venv/bin/python ../scripts/purge_test_data.py
   ```
2. **保留**真实业务数据：尤其不要删「日常任务」及正式立项项目；宁可留脏测试数据，也不误删正式数据
3. 若测试全程在独立测试库或事务回滚中完成、未污染共享库，可不额外 purge
4. 在 README 会话总结中写明删除了多少测试账号/项目

识别习惯：测试账号常见前缀 `t_` / `ta_` / `test_` / `e2e_` / `pv_` / `pd_` / `pr_` / `ra_` / `cl_` / `af_`；测试项目名称含「测试」或以 `proj_` / `role_` / `cl_proj_` 等假前缀开头。项目内 Cursor 规则见 [`.cursor/rules/purge-test-data-after-testing.mdc`](.cursor/rules/purge-test-data-after-testing.mdc)。

## 隔离说明（重要）

### 两台机器：文件夹与网址同名、互不混用

本仓库 **只维护 142**。64 是另一套系统、另一份代码，这里不再为 64 写登录/工作区兼容分支。

| 机器 | 代码目录 | 浏览器入口 |
|------|----------|------------|
| **本机 30.251.255.142（本仓库）** | `/home/aiworkbench` | `http://30.251.255.142:8080/aiworkbench/`（80 给安全监控，工作台走 8080） |
| **现网 30.251.255.64（别的仓库）** | `/home/workbench` | `http://30.251.255.64/workbench/` |

本机网址前缀与文件夹同名：`/aiworkbench/`。旧地址 `/workbench/` 会跳到新地址。现网 64 仍用 `/workbench/`。

本项目**独立部署**，不影响服务器上已有应用：

| 已有应用 | 端口 | 本项目 |
|---------|------|--------|
| learn-flow 进程 | 7000 | **不经本 nginx**，请直连 `:7000` |
| talent-profile 进程 | 3000 | **不经本 nginx**，请直连 `:3000` |
| MariaDB | 3306 | **不使用**（本项目用独立 PostgreSQL 库） |
| 安全监控 | **80** | **不经本 nginx**，直连 `:80` |
| 本机工作台 nginx | **8080** | 工作台在 `/aiworkbench/`；助手对话经本机门禁（按登录名开个人工作区） |

本机防火墙（ufw）默认只放行了 80。工作台走 8080 时，需对内网 `30.251.255.0/24` 放行 8080，否则同事电脑打不开（本机自己访问看起来是通的）。

本项目专用端口：

| 服务 | 地址 | 说明 |
|------|------|------|
| 后端 API | `127.0.0.1:8100` | FastAPI，仅本机访问（不变） |
| 对外入口 | `nginx :8080` | `/aiworkbench/` 静态 + API 反代到 8100 |
| PostgreSQL | `127.0.0.1:5432` | 独立库 `project_workbench`，与用户 `workbench` |

## 开机自启（systemd，推荐）

已配置 systemd 服务，**服务器重启后自动启动**，不影响 learn-flow / talent-profile 等现有服务。

```bash
# 首次安装自启（已执行可跳过）
bash scripts/install-systemd.sh

# 日常管理
systemctl status workbench-backend workbench-frontend   # 查看状态
systemctl restart workbench-backend workbench-frontend  # 重启
systemctl stop workbench-backend workbench-frontend     # 停止
journalctl -u workbench-backend -f                      # 查看后端日志
```

日志文件：`logs/backend.log`、`logs/frontend.log`

## 手动开发模式

开发调试时可用脚本启停（与 systemd 二选一，勿同时运行）：

```bash
# 1. 首次安装依赖
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd ../web-pc && npm install

# 2. 初始化演示数据（可选，仅本地演示；正式环境可不跑）
cd backend && PYTHONPATH=. .venv/bin/python ../scripts/seed_demo.py

# 3. 手动启动/停止
systemctl stop workbench-backend workbench-frontend  # 先停 systemd
bash scripts/start-dev.sh
bash scripts/stop-dev.sh
```

访问：**http://30.251.255.142:8080/aiworkbench/**

登录后，左侧「我的助手」会打开 DeepSeek Harness（**DSH 是唯一智能**）。只给有 `dsh.access` 权限的人看；第一次点进去会自动建个人工作区。助手办事走签名工具 `POST /api/dsh/tools/*`（查待办/周报/项目/案例、按习惯排序与催办、改派须确认），不再套娃调用工作台另一套模型。管理员可在 **系统管理 → 助手使用**（需 `admin.model`）查看全员打开次数、进出时长、对话轮数与 token。

系统管理员与部门负责人另有侧栏 **「分析管控」**（权限 `assist.analyze` 或 `stats.view`）：同一页可切换「团队本周 / 按项目 / 按人员」结论催办（需 `assist.analyze`）与「数据看板」人员提交率、项目卡点分布（需 `stats.view`）；旧路径 `/stats` 会跳到 `/analyze?tab=stats`。催办带问题性质标签（人的问题 / 外部 / 流程 / 待核实）。可「在智能助手中继续问」。员工在「看评价」可查看收到的指导并标记已读/已改进。助手门禁可凭签名调用 `/api/dsh/context/team|project|person` 拉同一套分析摘要（需具备 `assist.analyze`）。
  
正式环境用钉钉同步的员工账号登录。本地演示可先跑 `scripts/seed_demo.py` 再使用下方演示账号。

> 说明：后端 8100 仅本机可用；外网请通过 nginx **8080** 端口 `/aiworkbench/` 路径访问。人才画像 `:3000`、学习流 `:7000` 均不经本 nginx。本机 80 是安全监控，不要占用。

## 目录结构

```
backend/       FastAPI 后端 + RBAC + WebSocket
web-pc/        Vue3 + Element Plus PC 端
web-dingtalk/  钉钉手机网页应用 H5（正式移动端入口）
mp-dingtalk/   钉钉小程序（保留，能力已同步到 H5）
mp-wechat/     微信小程序（P1，独立）
scripts/       启动/停止/种子数据/备份脚本
docs/          Excel 导入模板
```

访问地址：

| 端 | URL | 说明 |
|----|-----|------|
| PC | http://30.251.255.142:8080/aiworkbench/ | 浏览器扫码 / 账号密码 |
| 钉钉手机 H5 | http://30.251.255.142:8080/aiworkbench/m/ | 开放平台「移动端首页」填此地址；钉钉内免登 |

## API 文档

后端启动后访问：http://127.0.0.1:8100/docs

## 演示账号

> 仅在执行 `scripts/seed_demo.py` 后存在。**正式环境启动不再自动创建**这些账号；不需要时可在用户管理删除。

| 用户名 | 初始密码 | 角色 |
|--------|----------|------|
| admin | InitAdmin2026 | 系统管理员 + 部门负责人 |
| manager | InitManager2026 | 部门负责人 |
| owner1 | InitOwner2026 | 项目负责人 |
| member1 | InitMember2026 | 普通成员 |
| member2 | InitMember22026 | 普通成员 |

> **首次登录强制改密**：使用初始密码登录后，须先设置新密码才能进入系统。  
> 新密码要求：8 位以上，须含大写字母、小写字母、数字混合。

## Excel 批量导入

模板：`docs/project_import_template.xlsx`  
API：`POST /api/projects/import/excel`（需 `project.create` 权限）

**项目负责人匹配（硬规则）：** 不得一律挂管理员（孙振振 / `admin`）。优先用导入表「负责人」列按姓名/工号匹配；若由周报任务卡自动建档，负责人取该周报作者；若项目下已有交办任务，取任务执行人（`assignee_id`）出现最多者；匹配不到则跳过并记日志，禁止静默落到 `admin`。

## 周报 / 日报（轻量三问）

填写标准见 [`docs/周报填写标准.md`](./docs/周报填写标准.md)。

- 员工与管理者：PC 侧栏统一入口 **周报管理**（`/reports`）→ 页内 Tab：**看周报/日报** / **写周报/日报**
- **看**：普通成员只看本人历史周期；有 `report.view.all`（管理员、部门负责人）可看全员工作台（提交情况、需升级、主辅对齐）；「未交（催办）」仅针对周报；**系统管理员（admin）不计入应提交名单**；查看全部 ≠ 可改他人（他人周报只读）
- **写**：默认三问（做成了什么 / 有无卡点 / 下一步哪天完成）；有卡点再展开；**仅本人可编辑修改自己的周报/日报**
- **日报**：当天工作记录，看板显示「已记/未记」，**不催办、不考核**；周末汇总到周报（自动汇总下阶段）
- **周报**：正式汇总
- 旧路径兼容：`/progress-report` → 写 Tab；`/report-board` → 看 Tab；人员详情 `/reports/person/:userId`
- 项目详情 **成员角色**：指定主负责人与辅助人（需 `project.members`）
- AI 辅导：配置模型或 `OPENAI_*` 后可用；未配置则用规则提醒。**模型管理**：填 API Base + Key 后可点「获取模型列表」下拉选择模型 ID（OpenAI 兼容 `GET /models`）；拉不到时仍可手填
- **AI 结构化 + 任务链**：填写概述/做成了什么后停顿约 1.2s 自动建议（拆字段润色、猜项目、匹配历史链/交办）；点「采用」才写入；可查看跨周时间线。接口：`POST /api/progress-reports/ai-structure`、`GET /api/progress-reports/chains/{chain_id}`。PC 与钉钉 H5 均已接入。

## 方案库（PC）

像共享资料柜：同事在电脑端上传历史方案（PDF / Word / PPT / 图片），系统用大模型整理摘要并打标签，方便按标签检索学习。

- 入口：PC 侧栏 **方案库**（需 `solution.view`；上传需 `solution.upload`，默认角色均已开通）
- **存储（本期）**：本机目录 `backend/data/solutions/`（配置项 `solution_upload_dir`），库表只存相对路径与元数据，不入库二进制；单文件上限 20MB
- **对象存储（延后）**：后续可改为 S3 兼容（如 MinIO）：配置 endpoint / bucket / AccessKey·SecretKey，将 `solution_storage` 读写改为对象键；库表可增加 `storage_backend` 或统一用对象路径，旧文件用脚本回填迁移。本期不接入远端 MinIO / 数据仓库，密钥不进代码仓库
- AI 用途：推荐在 **系统管理 → 模型管理** 添加用途为 **通用**（`general`）的模型即可覆盖方案打标等全站能力；也可单独配 **方案打标**（`solution_tag`）。建议配置支持识图的多模态模型以便处理截图/扫描件。解析顺序：专用用途 → 通用 → `.env` 的 `OPENAI_*`
- 主要接口：`GET/POST /api/solutions`、`POST /api/solutions/upload`、`GET /api/solutions/tags`、`POST /api/solutions/{id}/reprocess`
- 本期仅 PC；钉钉 H5 / 按项目隔离 / 向量检索未做

## 钉钉手机网页应用（H5，正式移动端）

开放平台创建**网页应用**后，「移动端首页」请填写：

`http://30.251.255.142:8080/aiworkbench/m/`

- **免登**：钉钉 JSAPI 取授权码 → `POST /aiworkbench/api/auth/dingtalk`（与小程序同一路径）
- **不要**走 PC 扫码 OAuth（`dingtalk-scan` / 回调页）
- 未绑定 → 绑定页；首次须改密 → 改密页
- 能力：首页统计、待办/自建与交办、任务详情、三问周报、登记卡点、**智能助手**（底栏「助手」，需 `dsh.access`；与 PC 同一账号工作区）
- 源码目录：`web-dingtalk/`；构建后由 nginx 托管；`systemctl restart workbench-frontend` 会同时构建 PC + H5

本地开发：

```bash
cd web-dingtalk && npm install && npm run dev
# 默认 http://127.0.0.1:5181/aiworkbench/m/ ，API 反代到 8100
```

## 钉钉小程序（免登 + 后台授权）

员工在钉钉里打开小程序后，系统去问钉钉「这是哪位同事」，**不用每次输密码**。谁能进来，由电脑端「系统管理」发门禁卡。

> 说明：小程序目录 `mp-dingtalk/` 仍保留；**本机钉钉入口以网页应用 H5（`/aiworkbench/m/`）为准**。

### 管理员怎么授权

1. 打开 PC 工作台 → **系统管理** → **角色权限**，给角色勾选 **钉钉免登**
2. 在 **用户管理** 里给该员工绑钉钉用户 ID；也可以先开权限，让员工自己用系统账号绑一次
3. 取消勾选「钉钉免登」= 收回门禁。没权限的人不能自己绑定

密钥只放在服务器 `backend/.env`（`DINGTALK_APP_KEY` / `DINGTALK_APP_SECRET` / `DINGTALK_CORP_ID`），不要写进代码仓库。

### 开发者工具预览

1. 用钉钉开发者工具打开目录 `mp-dingtalk/`
2. 开发阶段可勾选「不校验合法域名」
3. 正式给全员用时，请求域名须是 HTTPS 备案域名（当前站点仍是 HTTP，仅适合预览）

## PC 钉钉扫码登录（DTFrameLogin）

登录页默认「钉钉扫码」，可切「账号密码」。扫码后系统用工号当登录名：没有账号就当场开普通成员并建助手屋子。

**与小程序免登的红线：** PC 扫码用 OAuth 授权码；小程序用 JSAPI 免登码。两套后端兑码路径隔离，禁止混用。

### 员工怎么用

1. 打开 http://30.251.255.142:8080/aiworkbench/ → 默认看到钉钉二维码  
2. 手机钉钉扫码确认  
3. 通讯录有工号 → 直接进（登录名=工号）；第一次会自动建个人助手屋子  
4. 没有工号或工号已被别人占用 → 请管理员处理

### 开放平台必配（运维）

在钉钉开放平台「登录回调 / 重定向 URL」白名单加入（与访问地址协议/主机/端口一致，**一字不差**）：

```text
http://30.251.255.142:8080/aiworkbench/api/auth/dingtalk-callback?embed=1
```

若以后改 HTTPS 或域名，协议与主机必须同步改。复用现有 AppKey/AppSecret（与小程序同一应用）。

### 相关接口

| 接口 | 说明 |
|------|------|
| `GET /api/auth/dingtalk-config` | 返回 DTFrameLogin 入参（含 state） |
| `GET /api/auth/dingtalk-callback?embed=1` | 嵌入回调页，postMessage 给登录页 |
| `POST /api/auth/dingtalk-scan` | OAuth 兑码 → 按工号开账号或登录并发 JWT |
| `POST /api/auth/dingtalk/bind` | 未绑定时账号密码绑定（复用） |

### 主机上怎么确认能访问钉钉

必须在 **142 工作台服务器**上测，不能在自己电脑上测（电脑能出网、服务器不能出网时，扫码仍会失败）。

一条命令：

```bash
bash /home/aiworkbench/scripts/check_dingtalk_net.sh
```

或手动：

```bash
# 1）域名能不能解析出 IP（5 秒内应打出一串地址）
timeout 5 getent hosts api.dingtalk.com
timeout 5 getent hosts oapi.dingtalk.com

# 2）HTTPS 能不能连上（http=000 就是不通；400/404 也算出网正常）
curl -sS -o /dev/null -w "http=%{http_code} connect=%{time_connect}\n" \
  --connect-timeout 5 --max-time 8 \
  https://api.dingtalk.com/v1.0/oauth2/accessToken

curl -sS -o /dev/null -w "http=%{http_code} connect=%{time_connect}\n" \
  --connect-timeout 5 --max-time 8 \
  https://oapi.dingtalk.com/gettoken
```

**正常：** 能解析出 IP，且 `http=` 后面不是 `000`。  
**现在这台机：** 解析超时，`http=000`，所以扫码换票会 502。请网管放行本机访问这两个域名的 DNS 和 443。


## 工作交办（PC ↔ 钉钉同步）

负责人即执行人；员工可**自建**（自己是负责人，可选辅助人）；部门负责人 / 项目主负责人可**交办**（指定负责人 + 可选辅助人）。负责人可后期加辅助人，管理者可换负责人。每条操作写入流水账（何时、谁、做了什么、结果）。

| 入口 | 说明 |
|------|------|
| PC 项目详情 → **任务待办** | 列表 / 自建 / 交办 / 详情时间线 |
| PC 个人工作台 | 我的待办（负责或辅助） |
| 钉钉「待办」 | 同上，轻量自建/交办；点进详情改状态与结果 |
| 钉钉「周报」 | 与电脑端对齐的三问，走 `/api/progress-reports` |

提醒骨架：`POST /api/admin/reminders/blocker-overdue`（需审计权限）；配置可选 `DINGTALK_AGENT_ID` 后才实发工作通知。

交办相关接口：`POST /api/tasks`、`GET /api/projects/{id}/tasks`、`GET|POST /api/tasks/{id}`、`PATCH /api/tasks/{id}/owner`、`POST|DELETE /api/tasks/{id}/assistants`。

### 常见问题

| 现象 | 处理 |
|------|------|
| 不合法的临时授权码 | 核对开放平台白名单与 `redirect_uri` 是否一字不差 |
| redirect_uri 与登录页不同源 | VPN/映射访问时允许不同源出码；但扫码后要能跳转，须让后端返回的 `redirect_uri` 与浏览器访问源一致（配 `DINGTALK_OAUTH_PUBLIC_ORIGIN`） |
| 扫码后无跳转 | 常见原因：后端仍返回 `http://内网IP/...`，HTTPS 页无法加载 HTTP 回调。配公开源并**发布**开放平台版本 |
| state 无效 | 点「刷新二维码」 |
| 无权限 | 系统管理给角色勾选「钉钉免登」 |
| iframe 空白 | 检查能否加载 `g.alicdn.com`；若站点加了 CSP，需允许 `script-src` 含该域名、`frame-src` 含 `login.dingtalk.com` |

---


## 会话总结（2026-08-27）

### 会话的主要目的
阅读 PRD 并搭建「项目支撑工作台」P0 项目骨架，同时确保不影响服务器现有应用。

### 完成的主要任务
- 完成 15 轮需求 Ask，确认技术选型与多端策略
- 搭建 Monorepo：FastAPI 后端 + Vue3 PC 端 + 钉钉小程序骨架
- 实现 P0 核心：项目总览、详情（阶段+卡点）、个人工作台、RBAC、系统管理后台
- 演示数据种子 + Excel 导入模板
- **隔离改造**：停止占用 8000/5174 的进程，改用独立端口 8100/5180，仅绑定 127.0.0.1

### 关键决策和解决方案
- 后端 Python+FastAPI，前端 Vue3+Element Plus，数据库 PostgreSQL（独立库）
- 三端独立开发不复用；钉钉 P0，微信 P1
- 预设三角色 + 可扩展 RBAC 权限点
- **不影响现有应用**：专用端口 8100/5180，不修改 nginx/MariaDB/learn-flow/talent-profile

### 使用的技术栈
Python 3.12、FastAPI、SQLAlchemy、PostgreSQL 16、Vue 3、Element Plus、Pinia、Vitest、pytest

### 修改了哪些文件
- 新建 `backend/`、`web-pc/`、`mp-dingtalk/`、`scripts/`、`docs/` 全套项目文件
- 新建 `README.md`、`.env.ports`、`scripts/start-dev.sh`、`scripts/stop-dev.sh`
- 调整 `web-pc/vite.config.ts` 使用独立端口 5180 与后端代理 8100

---

## 会话总结（2026-08-27 下午 — 开机自启）

### 会话的主要目的
配置项目支撑工作台 systemd 开机自启，且不影响现有应用。

### 完成的主要任务
- 创建 `workbench-backend.service`、`workbench-frontend.service`
- 编写 `scripts/install-systemd.sh` 一键安装并 enable
- 前端改为 build + vite preview 生产模式自启
- 验证 learn-flow / talent-profile 仍正常运行

### 关键决策和解决方案
- 后端依赖 `postgresql.service`，前端依赖后端，按顺序启动
- 仍使用独立端口 8100/5180，仅绑定 127.0.0.1
- 手动 `start-dev.sh` 与 systemd 二选一，避免端口冲突

### 使用的技术栈
systemd、vite preview 生产预览

### 修改了哪些文件
- 新建 `deploy/systemd/workbench-backend.service`
- 新建 `deploy/systemd/workbench-frontend.service`
- 新建 `scripts/install-systemd.sh`
- 更新 `README.md`

---

## 会话总结（2026-08-27 下午 — 外网访问）

### 会话的主要目的
解决通过服务器 IP `30.251.255.64` 无法访问项目的问题。

### 完成的主要任务
- 分析原因：服务仅绑定 127.0.0.1，外网无法直连 5180/8100
- 新增 nginx 反代：`/workbench/` → 前端，`/workbench/api/` → 后端
- 前端配置 base 路径 `/workbench/`，适配子路径部署
- 编写 `scripts/install-nginx.sh` 一键安装反代

### 关键决策和解决方案
- 参照 learn-flow 的 `/learnflow/` 模式，不修改现有站点根路径
- 外网访问地址：**http://30.251.255.64/workbench/**

### 使用的技术栈
nginx 反向代理、Vite base path

### 修改了哪些文件
- 新建 `deploy/nginx/workbench.conf`、`scripts/install-nginx.sh`
- 修改 `web-pc/vite.config.ts`、`web-pc/src/api/index.ts`、`web-pc/src/router/index.ts`
- 修改 `/etc/nginx/sites-enabled/talent-profile.conf`（新增 include）
- 更新 `README.md`

---

## 会话总结（2026-08-27 — 强密码策略）

### 会话的主要目的
落实「密码必须强密码」要求，新建/重置密码时强制校验。

### 完成的主要任务
- 后端强密码校验（8 位+大小写+数字+特殊字符+弱密码黑名单）
- 新增修改密码、管理员重置密码、创建用户 API
- 演示账号密码升级为强密码并重置数据库
- 登录页/管理后台展示密码规则，重置密码时前端校验

### 关键决策和解决方案
- 登录时不校验复杂度（仅验证正确性），设置/重置密码时强制强密码
- 演示密码统一为 `{Role}@2026!Wb` 格式

### 使用的技术栈
Pydantic field_validator、前后端同步校验规则

### 修改了哪些文件
- 新建 `backend/app/core/password_policy.py`、`backend/tests/test_password_policy.py`
- 修改 `auth.py`、`admin.py`、`init_service.py`、前端 LoginView/AdminView
- 新建 `scripts/reset_demo_passwords.py`，更新 `README.md`

---

## 会话总结（2026-08-27 — 首次登录强制改密）

### 会话的主要目的
实现首次登录必须重置初始密码，密码强度 8 位以上且含数字、大小写字母混合。

### 完成的主要任务
- 用户表新增 `must_change_password` 字段
- 首次登录跳转改密页，改密完成前禁止访问业务 API
- 管理员重置密码后用户须再次改密
- 密码策略调整为：8 位+大写+小写+数字（取消特殊字符强制要求）

### 关键决策和解决方案
- 初始密码与正式密码使用同一强度规则
- 演示初始密码格式：`Init{Role}2026`

### 使用的技术栈
FastAPI 403 拦截、Vue 路由守卫、PostgreSQL 列迁移

### 修改了哪些文件
- `backend/app/models/__init__.py`、`core/password_policy.py`、`core/migrations.py`、`rbac/deps.py`、`api/auth.py`
- `web-pc/src/views/ChangePasswordView.vue`、`router/index.ts`、`stores/user.ts`
- 更新 `README.md`

---

## 会话总结（2026-08-27 — UI 风格改版）

### 会话的主要目的
去除「AI 模板感」，参考用户提供的产品图，统一为极简工具风界面。

### 完成的主要任务
- 新增全局主题 `web-pc/src/styles/theme.css`（浅灰侧栏、白底主区、薄荷绿点缀）
- 字体改为 Inter + PingFang SC + 微软雅黑
- 重做侧栏、登录页、改密页、总览/详情/个人/管理页样式
- 去除蓝色渐变背景和 emoji 标签，改用轻量文字标签

### 关键决策和解决方案
- 主按钮深色 (#222)，激活态薄荷绿背景 (#e8f5ef)，与参考图一致
- Element Plus 通过 CSS 变量全局覆盖，保持组件一致性

### 使用的技术栈
Vue 3、CSS 变量、Inter 字体、Element Plus 主题覆盖

### 修改了哪些文件
- 新建 `web-pc/src/styles/theme.css`
- 修改 `MainLayout.vue`、`LoginView.vue`、`ChangePasswordView.vue`、`OverviewView.vue`、`ProjectDetailView.vue`、`PersonalView.vue`、`AdminView.vue`、`index.html`、`main.ts`

---

## 会话总结（2026-08-27 — 修复外网访问）

### 会话的主要目的
排查并修复无法打开 http://30.251.255.64/workbench/ 的问题。

### 完成的主要任务
- 确认 nginx 80 端口正常，服务端本地与 IP 访问均返回 200
- 改为 nginx 直接托管 `web-pc/dist` 静态文件，不再依赖 vite preview（5180）
- 去掉 Google Fonts 外链，避免内网环境加载卡住
- 前端 systemd 改为 oneshot 仅负责构建发布

### 关键决策和解决方案
- 生产静态资源由 nginx 托管更稳定
- API 仍反代到 `127.0.0.1:8100`

### 使用的技术栈
nginx alias/try_files、systemd oneshot、Vite build

### 修改了哪些文件
- `deploy/nginx/workbench.conf`、`deploy/systemd/workbench-frontend.service`
- `web-pc/index.html`、`web-pc/src/styles/theme.css`、`README.md`

---

## 会话总结（2026-08-31 — 钉钉小程序免登与后台授权）

### 会话的主要目的
把钉钉小程序做成可实际使用的手机工作台：打开即可免登，风格与 PC 一致；谁能进来由电脑端系统管理授权。

### 完成的主要任务
- 后端接入钉钉免登 / 绑定；未授权、未绑定会明确拒绝
- 新增权限点「钉钉免登」；角色权限可在后台勾选，重启不再冲掉授权
- 用户管理可绑定 / 解绑钉钉身份
- 小程序补全首页、待办（含改任务状态）、新增卡点、周报、绑定、改密
- 密钥只写入服务器本地环境配置，不进代码仓库

### 关键决策和解决方案
- 免登像刷门禁：钉钉确认身份 + 后台发过「钉钉免登」门禁卡 + 账号已绑定
- 启动时只补全新权限点，不再整表覆盖角色授权
- 小程序配色对齐 PC：白底、浅灰卡片、薄荷绿点缀、主按钮深色

### 使用的技术栈
FastAPI、httpx、钉钉开放平台免登、钉钉小程序（axml/acss）、Vue 3、Element Plus、pytest、Vitest

### 修改了哪些文件
- 后端：`config.py`、`permissions.py`、`init_service.py`、`auth.py`、`admin.py`、`personal.py`、`deps.py`、`schemas`，新建 `services/dingtalk.py` 与相关测试
- PC：`AdminView.vue`（角色可勾选授权、用户可绑钉钉），并重新构建 `web-pc/dist`
- 钉钉小程序：`mp-dingtalk/` 页面、主题、免登与绑定流程
- `README.md`、`backend/.env.example`

---

## 会话总结（2026-09-06 — 轻量周报/日报）

### 会话的主要目的
治理流水式周报：做成「理清思路」轻量工具，同时让管理者能看进度、控风险、取数考核；支持项目主负责人/辅助人。

### 完成的主要任务
- 落地《周报填写标准》（默认三问 + 卡点/风险按需展开）
- 项目成员角色：唯一主负责人 + 多名辅助人（同步 owner）
- 结构化周报/日报模型与 API；硬拦仅空话/卡点缺项/需升级缺协调
- PC「写周报」单页三问 + 预填 + 可选 AI 辅导（无密钥则规则提醒）
- 管理者「周报看板」：按人提交、按项目主辅对齐与卡点确认

### 关键决策和解决方案
- 双目标同等重要，硬约束「不能太重」：无卡点一键跳过，软提示不挡提交
- 周报卡点可写回 blockers，辅助人申报提醒主责；主责可一键已知悉
- 指派任务与人工评价留到下一阶段

### 使用的技术栈
FastAPI、SQLAlchemy、PostgreSQL、Vue 3、Element Plus、httpx（可选 OpenAI 兼容）、pytest

### 修改了哪些文件
- 新建 `docs/周报填写标准.md`
- 后端：`models`、`migrations`、`permissions`、`schemas`、`report_quality.py`、`report_ai_coach.py`、`api/progress_reports.py`、`api/projects.py`（成员角色）、相关测试
- PC：`ProgressReportView.vue`、`ReportBoardView.vue`、`ProjectDetailView.vue`、`PersonalView.vue`、路由与侧栏；重建 `web-pc/dist`
- `README.md`

---

## 会话总结（2026-09-06 — 修复登录白屏）

### 会话的主要目的
排查无法登录 / 白屏问题。

### 完成的主要任务
- 重置演示账号密码为初始密码
- 修复路由守卫重复拼接 `/workbench`，导致地址变成 `/workbench/workbench/login` 白屏
- 重新构建并发布前端

### 关键决策和解决方案
- Vue Router 已配置 `base=/workbench/`，守卫重定向只能返回 `/login` 这类 path，不能再拼 base

### 使用的技术栈
Vue Router、Vite build

### 修改了哪些文件
- `web-pc/src/router/index.ts`、`web-pc/dist`、`README.md`

---

## 会话总结（2026-09-06 — 修复周报 403）

### 会话的主要目的
排查写周报接口 403 Forbidden。

### 完成的主要任务
- 给部门负责人、系统管理员补上 `report.submit`（及看板相关）权限
- 写周报页对 403 做友好提示，避免控制台未捕获异常刷屏
- 重启后端并验证 prefill 返回 200

### 关键决策和解决方案
- 403 主因是管理员角色原先只能管系统、不能交周报；升级时用「只补缺不覆盖」方式授权
- 浏览器 `VM13 startTime` 报错来自扩展/注入脚本，与业务无关

### 使用的技术栈
FastAPI RBAC、Vue 3

### 修改了哪些文件
- `backend/app/rbac/permissions.py`、`backend/app/services/init_service.py`
- `web-pc/src/views/ProgressReportView.vue`、`web-pc/dist`、`README.md`

---

## 会话总结（2026-09-06 — 系统管理·模型管理）

### 会话的主要目的
在系统管理中新增「模型管理」，让管理员在页面配置 OpenAI 兼容 LLM，周报 AI 辅导优先读库中默认模型。

### 完成的主要任务
- 新建 `llm_models` 表与迁移；权限点 `admin.model`（默认给管理员）
- 实现 `/api/admin/models` 增删改查与连通测试；列表密钥脱敏
- `llm_resolver` 按用途取默认启用模型，无配置时回退 `.env`
- 周报 AI 辅导改为通过 resolver 取模型
- AdminView 增加「模型管理」Tab（添加/编辑/测试/删除）
- pytest 通过；前端重建；后端已重启并跑迁移

### 关键决策和解决方案
- 本期只做 OpenAI 兼容 `chat/completions`；用途以周报辅导为主；同用途仅一个默认
- 密钥与现网钉钉密钥同级明文落库，接口只返回脱敏字段；更新时密钥留空表示不改

### 使用的技术栈
FastAPI、SQLAlchemy、PostgreSQL、Vue 3、Element Plus、httpx、pytest

### 修改了哪些文件
- 后端：`models`、`migrations`、`schemas`、`permissions`、`init_service`、`llm_resolver.py`、`llm_client.py`、`report_ai_coach.py`、`api/admin.py`、`api/progress_reports.py`、`tests/test_llm_models.py`
- 前端：`web-pc/src/views/AdminView.vue`、重建 `web-pc/dist`
- `README.md`

---

## 会话总结（2026-09-06 — 配置华数 Token 模型）

### 会话的主要目的
把用户提供的 OpenAI 兼容接口写入「模型管理」，供周报 AI 辅导使用。

### 完成的主要任务
- 探测接口可用模型列表并连通测试通过
- 写入默认周报辅导模型：`deepseek-v4-flash`，API Base `https://token.wasu.cn/v1`
- 密钥仅存库，接口脱敏展示；未写入代码仓库与 README 明文

### 关键决策和解决方案
- 未指定模型名时，选用网关中的 `deepseek-v4-flash`（聊天、速度快，适合周报辅导）
- 设为 `report_coach` 用途的默认启用项

### 使用的技术栈
httpx 连通探测、SQLAlchemy、llm_models 表

### 修改了哪些文件
- 数据库 `llm_models`（新增默认配置）
- `README.md`（本总结；不含密钥明文）

---

## 会话总结（2026-09-06 — 优化周报页面）

### 会话的主要目的
优化写周报/日报：时间范围、任务概述多条、项目非必选。

### 完成的主要任务
- 周报可选开始～结束日期（默认周一～周日）；日报单日
- 「本周主题」改为多张「任务卡片」的「任务概述」；报表 theme 由概述拼接
- 去掉项目下拉必选；`project_id` 可空；看板增加「未归类任务」
- 更新填写标准文档、pytest、前端构建并重启后端

### 关键决策和解决方案
- 不再强制把周报日期对齐到周一保存
- 项目归属留给后期 LLM；无项目时不写系统卡点表
- 唯一键仍为 user + period_type + period_start

### 使用的技术栈
FastAPI、SQLAlchemy、Vue 3、Element Plus、pytest

### 修改了哪些文件
- 后端：`models`、`migrations`、`schemas`、`report_quality.py`、`api/progress_reports.py`、相关测试
- 前端：`ProgressReportView.vue`、`ReportBoardView.vue`、`web-pc/dist`
- `docs/周报填写标准.md`、`README.md`

---

## 会话总结（2026-09-06 — PC 钉钉扫码登录）

### 会话的主要目的
在工作台登录页加入钉钉扫码登录，实现对齐本机人才画像 DTFrameLogin 正式链路。

### 完成的主要任务
- 后端新增 OAuth 兑码（与小程序 JSAPI 隔离）、`oauth_state` 表、接口：`dingtalk-config` / `dingtalk-callback` / `dingtalk-scan`
- 登录页默认「钉钉扫码」Tab，可切账号密码；未绑定走系统账号自助绑定
- 前端移植 DTFrameLogin composable；扫码成功发 JWT，复用 `dingtalk.login` 权限与绑定接口
- 单元测试 16 通过；前端已构建；后端已重启；nginx 无 CSP 拦截（无需改）

### 关键决策和解决方案
- 方案：DTFrameLogin + OAuth（不用自建二维码中转）
- 复用现有 AppKey/Secret；回调白名单须为 `http://<主机>/workbench/api/auth/dingtalk-callback?embed=1`
- 双触发去重：前端 Set + 后端 auth_code 短缓存；state 校验与撤销分离

### 使用的技术栈
FastAPI、SQLAlchemy、PostgreSQL、Vue 3、Element Plus、钉钉 h5-dingtalk-login SDK、pytest

### 修改了哪些文件
- 后端：`app/services/dingtalk.py`、`app/services/oauth_state.py`、`app/api/auth.py`、`app/models/__init__.py`、`app/core/migrations.py`、`app/core/config.py`、`app/schemas/__init__.py`、`tests/test_dingtalk_auth.py`、`.env.example`
- 前端：`web-pc/src/composables/dingtalkQrLogin.ts`、`views/LoginView.vue`、`stores/user.ts`、`api/index.ts`、`web-pc/dist`
- `README.md`

---

## 会话总结（2026-09-06 — 钉钉扫码对抗性审查修复）

### 会话的主要目的
对 PC 钉钉扫码登录做对抗性审查，并直接修复高/中危问题后回归测试。

### 完成的主要任务
- 扫码结果缓存绑定 `state`：仅凭 `auth_code` 无法偷 JWT；双触发同 state 仍可用缓存
- `redirect_uri` 不再信任 `X-Forwarded-Host`，只信 `Host` + `X-Forwarded-Proto`
- `dingtalk-config` / `dingtalk-scan` 进程内滑动窗口限流（429）
- `bind_ticket` 增加 `jti` 一次性消费；SDK URL 白名单；钉钉兑码错误对前端脱敏
- 新增回归测试；`tests/test_dingtalk_auth.py` 20 passed；后端已重启

### 关键决策和解决方案
- 缓存键仍为 auth_code，但命中必须 state 一致（兼顾安全与 DTFrameLogin 双触发）
- 限流用进程内实现，适配当前单实例 systemd 部署

### 使用的技术栈
FastAPI、pytest、进程内 SlidingWindowLimiter

### 修改了哪些文件
- `backend/app/api/auth.py`、`backend/app/services/rate_limit.py`、`backend/tests/test_dingtalk_auth.py`、`README.md`

---

## 会话总结（2026-09-06 — 安全修复继续测试）

### 会话的主要目的
对钉钉扫码安全修复做第二轮自动化 + 线上冒烟验证。

### 完成的主要任务
- `pytest tests/test_dingtalk_auth.py`：20 passed
- 线上冒烟：Host 污染忽略、nginx config、callback HTML、无效 state 400、登录页 200、health ok
- 运行中 uvicorn 调试日志确认 `xff_host=evil.example` 时 `redirect_host` 仍为本机

### 关键决策和解决方案
- 本轮仅验证，不改业务代码；调试埋点暂留待确认后清理

### 使用的技术栈
pytest、curl、systemd 后端

### 修改了哪些文件
- 无代码变更（仅测试与 README 本条总结）
- `README.md`

---

## 会话总结（2026-09-06 — 确认钉钉应用配置）

### 会话的主要目的
确认并将钉钉 AppKey / AppSecret / CorpId 配置到工作台后端。

### 完成的主要任务
- 核对 `backend/.env` 中三项已与用户提供值一致（无需改写）
- 重启 `workbench-backend`；`GET /api/auth/dingtalk-config` 返回正确 client_id、corp_id 与回调地址

### 关键决策和解决方案
- 密钥仅保留在服务器 `.env`，不写入代码仓库与 README 正文
- 提醒运维仍须在钉钉开放平台配置登录回调白名单

### 使用的技术栈
systemd、FastAPI 环境变量

### 修改了哪些文件
- 无业务代码变更；`README.md`（本条总结）

---

## 会话总结（2026-09-06 — 钉钉轻端完善 + 工作交办）

### 会话的主要目的
完善钉钉轻量端，并新增与 PC 同步的工作交办（负责人=执行人；自建/交办；辅助人；操作流水）。

### 完成的主要任务
- 扩展任务模型：负责人、创建人、交办类型、结果、辅助人表、事件流水；权限 `task.create.self` / `task.assign`
- 后端任务 API + 个人工作台待办含「负责或辅助」；单测 3 passed
- PC：项目详情「任务待办」Tab、个人台交办面板与时间线
- 钉钉：待办/详情/自建与交办；周报改为三问走 progress-reports；卡点可改状态
- 提醒骨架：卡点跟进逾期扫描 + 可选工作通知；演示种子含交办任务

### 关键决策和解决方案
- 不单独建「交办单」，扩展现有 Task；负责人即执行人
- 管理者交办：部门负责人（全局）或项目 lead；负责人可加辅助人，管理者可换负责人
- 完成任务须填结果；PC/钉钉共用同一 API
- 钉钉小程序更新接口用 POST 兼容（兼 PATCH）

### 使用的技术栈
FastAPI、SQLAlchemy、PostgreSQL、Vue3、Element Plus、钉钉小程序、pytest

### 修改了哪些文件
- 后端：`models/__init__.py`、`migrations.py`、`permissions.py`、`init_service.py`、`schemas`、`services/task_service.py`、`services/reminders.py`、`services/dingtalk.py`、`api/tasks.py`、`api/personal.py`、`api/blockers.py`、`api/admin.py`、`main.py`、`config.py`、`.env.example`、`tests/test_tasks_assign.py`、`tests/conftest.py`
- PC：`components/TaskAssignPanel.vue`、`views/ProjectDetailView.vue`、`views/PersonalView.vue`、`web-pc/dist`
- 钉钉：`mp-dingtalk` 待办/周报/卡点 + `task-detail`/`task-create`
- `scripts/seed_demo.py`、`README.md`

---

## 会话总结（2026-09-06 — 同步政企市场部用户）

### 会话的主要目的
保留 admin，清空其他账号；从钉钉「政企市场部」拉取成员写入用户管理。

### 完成的主要任务
- 钉钉通讯录：按部门名查找、含子部门成员分页拉取（姓名/工号/手机/userid）
- 同步接口：删除非 admin（处理外键）→ 按工号导入；初始密码=工号+手机号；首次强制改密；绑定钉钉 userid
- 用户管理列表展示「姓名」「工号」；增加「从钉钉同步政企市场部」按钮与二次确认
- 单测 `tests/test_user_sync.py` 通过；前端已构建

### 关键决策和解决方案
- 工号即登录名（username）；缺工号或手机号则跳过并返回原因（手机号脱敏）
- 默认角色 member；项目负责人等外键在删用户前改挂 admin
- 钉钉应用需具备通讯录只读权限

### 使用的技术栈
FastAPI、SQLAlchemy、钉钉 topapi 通讯录、Vue3、Element Plus、pytest

### 修改了哪些文件
- `backend/app/services/dingtalk.py`、`backend/app/services/user_sync.py`
- `backend/app/api/admin.py`、`backend/app/schemas/__init__.py`、`backend/app/rbac/deps.py`
- `backend/tests/test_user_sync.py`
- `web-pc/src/views/AdminView.vue`、`web-pc/dist`
- `README.md`

---

## 会话总结（2026-09-06 — 钉钉手机 H5 网页应用）

### 会话的主要目的
新建独立 Vue3 手机 H5（web-dingtalk），把小程序能力同步过来，供钉钉开放平台「移动端首页」使用。

### 完成的主要任务
- 脚手架：`web-dingtalk/`（Vite + Vue3 + TS + Router + Pinia + Axios，base=`/workbench/m/`）
- 钉钉 JSAPI 免登 → `POST /auth/dingtalk`；绑定页、强制改密页
- 业务页：首页 / 待办 / 任务详情与新建（自建·交办）/ 三问周报 / 登记卡点
- nginx 增加 `/workbench/m/` 静态托管；`workbench-frontend` 同时构建 PC + H5；文档更新

### 关键决策和解决方案
- 正式钉钉入口为网页应用 H5，小程序目录保留不删
- 免登与小程序同后端路径，与 PC 扫码 OAuth 隔离
- UI 手机优先底部 Tab，主题色 `#5bb98c`，不引入 Element Plus
- 开放平台移动端首页填写：`http://30.251.255.64/workbench/m/`

### 使用的技术栈
Vue3、Vite、TypeScript、Vue Router、Pinia、Axios、钉钉 JSAPI、nginx、systemd

### 修改了哪些文件
- 新增：`web-dingtalk/`（含 dist 构建产物）
- `deploy/nginx/workbench.conf`、`deploy/systemd/workbench-frontend.service`、`scripts/install-nginx.sh`
- `README.md`

---

## 会话总结（2026-09-06 — 周报 AI 结构化 + 任务链）

### 会话的主要目的
写周报时调用模型做结构化整理，并识别历史相似任务提示加入任务链；结合项目归属，跨周串成进度时间线。PC 与钉钉端均接入。

### 完成的主要任务
- `progress_report_items` 增加 `chain_id`、`linked_task_id`（迁移 + 模型 + schema + 保存）
- 新接口：`POST /ai-structure`（LLM 优先 `report_structure` 回退 `report_coach`，无模型规则兜底）、`GET /chains/{id}` 时间线、`POST /chains/link-history` 历史补链
- PC 写周报：停顿防抖建议面板、采用/忽略、任务链抽屉
- 钉钉 H5 三问周报：同步 AI 建议与任务链查看
- 更新 `docs/周报填写标准.md` 与本 README

### 关键决策和解决方案
- 方案 3：周报链为主轴 + 可选挂项目/交办；不自动创建交办；采用前不改字段
- 历史卡片无 chain_id 时生成新 UUID，采用时通过 link-history 回写种子卡片
- 首期不做向量检索；钉钉与 PC 共用同一套 API

### 使用的技术栈
FastAPI、SQLAlchemy、httpx、Vue3、Element Plus、钉钉 H5（Vite）

### 修改了哪些文件
- `backend/app/models/__init__.py`、`backend/app/core/migrations.py`、`backend/app/schemas/__init__.py`
- `backend/app/api/progress_reports.py`、`backend/app/services/report_ai_structure.py`（新）
- `web-pc/src/views/ProgressReportView.vue`
- `web-dingtalk/src/views/ReportView.vue`
- `docs/周报填写标准.md`、`README.md`

---

## 会话总结（2026-09-06 — 任务链功能测试验证）

### 会话的主要目的
对「周报 AI 结构化 + 任务链」做自动化测试与线上联调冒烟，确认可交付。

### 完成的主要任务
- 新增 `backend/tests/test_report_ai_structure.py`（规则匹配单测 + 提交/补链/时间线/权限 API 流）
- pytest 相关用例全部通过（4/4）；联调冒烟 `SMOKE_OK`（ai-structure → link-history → 双周时间线 2 节点）
- 前端 dist 已含 `ai-structure` / 「任务链」文案（PC + 钉钉）
- 规则匹配增强：短项目名与任务概述的关键词共现

### 关键决策和解决方案
- 集成测试用唯一标题隔离，避免库内历史脏数据干扰
- 演示账号若仍 `must_change_password`，冒烟改用测试账号 `t_chain_a`
- 全量 pytest 中 `test_dingtalk_auth::test_assignee_can_update_own_task_status` 失败与本次无关（完成任务须填结果说明）

### 使用的技术栈
pytest、httpx ASGITransport、systemd 后端 8100 冒烟

### 修改了哪些文件
- `backend/tests/test_report_ai_structure.py`（新）
- `backend/app/services/report_ai_structure.py`（项目匹配微调）
- `README.md`

---

## 会话总结（2026-09-06 — 钉钉身份绑定管理员）

### 会话的主要目的
将钉钉用户标识 FY0354 绑定到系统管理员账号，便于钉钉免登进入管理员身份。

### 完成的主要任务
- 确认 `admin`（id=1）原 `dingtalk_user_id` 为空，且 FY0354 未被其他用户占用
- 写入绑定：`admin.dingtalk_user_id = FY0354`，并记审计日志 `bind_dingtalk`

### 关键决策和解决方案
- 直接库内绑定（等价于后台「用户管理 → 绑定钉钉」），不改代码
- 绑定字段为钉钉 userid；免登仍需账号具备 `dingtalk.login` 权限（admin 角色已有）

### 使用的技术栈
PostgreSQL、SQLAlchemy、审计日志

### 修改了哪些文件
- 数据库 `users` / `audit_logs`（无业务代码变更）
- `README.md`

---

## 会话总结（2026-09-06 — 修正管理员钉钉 userid 绑定）

### 会话的主要目的
孙振振钉钉免登仍提示绑定系统账号：排查并修正错误绑定。

### 完成的主要任务
- 根因：先前把**工号** `FY0354` 误当作钉钉 `userid` 写入
- 从通讯录确认：姓名孙振振、工号 FY0354，真实 `userid=65102223285497`
- 已将 `admin.dingtalk_user_id` 更正为真实 userid，并写审计日志

### 关键决策和解决方案
- 免登匹配的是钉钉 userid，不是工号；后台绑定时须填 userid
- 手机号等敏感信息不入库说明、不对外展示

### 使用的技术栈
钉钉通讯录 API、SQLAlchemy

### 修改了哪些文件
- 数据库 `users` / `audit_logs`
- `README.md`

---

## 会话总结（2026-09-06 — 政企市场部同步为普通成员）

### 会话的主要目的
将钉钉「政企市场部」全部人员导入系统，角色为普通成员，并绑定钉钉免登。

### 完成的主要任务
- 调用既有 `sync_department_users`：保留 admin，清空其他，按工号导入
- 结果：删除 5 人旧账号；导入 38 人；跳过 0；其中 37 人已绑钉钉 userid
- 孙振振（工号 FY0354）账号保留为普通成员，但其钉钉 userid 仍挂在 `admin`，保证管理员免登

### 关键决策和解决方案
- 登录名=工号，初始密码=工号+手机号，首次强制改密；角色一律 `member`
- 同一钉钉 userid 不能绑两人：孙振振免登走 admin，FY0354 成员账号可账号密码登录

### 使用的技术栈
钉钉通讯录 API、`user_sync.sync_department_users`、SQLAlchemy

### 修改了哪些文件
- 数据库 `users` / `user_roles` / `audit_logs`（无业务代码变更）
- `README.md`

---

## 会话总结（2026-09-06 — 清空测试数据）

### 会话的主要目的
清理库内自动化测试/演示产生的业务脏数据，保留真实人员账号。

### 完成的主要任务
- 清空：项目 57、任务 42、卡点 3、阶段 10、任务流水 51、OAuth state 6、审计日志等
- 保留：用户 39（admin + 政企市场部 38）、角色权限、管理员钉钉绑定

### 关键决策和解决方案
- 只清业务表，不动 `users` / RBAC；用 `TRUNCATE … CASCADE` 保证外键干净
- 清理后写入一条 `purge_test_data` 审计记录

### 使用的技术栈
PostgreSQL TRUNCATE、SQLAlchemy

### 修改了哪些文件
- 数据库业务表（无应用代码变更）
- `README.md`

---

## 会话总结（2026-09-06 — 8/31–9/4 周报录入）

### 会话的主要目的
整理 `docs/8月31-9月4日周报汇总/` 下 Excel 周报，优化为三问结构并写入系统，作者绑定到已有成员账号。

### 完成的主要任务
- 新增导入脚本 `scripts/import_week_reports_20260831.py`
- 成功导入 **11/11** 人、`period=2026-08-31～09-04`、`status=submitted`，共 **155** 条任务卡片
- 作者均按姓名匹配工号账号，钉钉绑定保持不变；`project_id` 留空（项目名在任务概述）
- 特例：王捷驻点表、金滨 `.xls` 运维周志（取 2026 表「8月31日至9月4日」行）

### 关键决策和解决方案
- 空话/无进展标 `no_action` 或跳过；有卡点写入需协调；默认下一步完成日 2026-09-11
- 不批量建项目；同周期重复导入会整表替换（幂等）

### 使用的技术栈
openpyxl、xlrd、SQLAlchemy、progress_reports 模型

### 修改了哪些文件
- `scripts/import_week_reports_20260831.py`（新）
- 数据库 `progress_reports` / `progress_report_items`
- `README.md`

---

## 会话总结（2026-09-07 — 管理员周报看板看全文）

### 会话的主要目的
管理员需要在周报看板看到全部人员、全部任务内容，而不仅是「已交/未交」。

### 完成的主要任务
- 看板 API：返回每人完整 `report`；列出 `available_periods`；本周无数据时自动切到最近有提交的周期
- 未归类列表包含无实质动作卡片；已交人员优先排序
- 看板 UI：周期快捷切换、默认展开每人全部任务（做成了/卡点/下一步）、展开/收起

### 关键决策和解决方案
- 导入周报 `period_start=8/31`，与「本周一」不一致导致空白；用 auto_switch + 周期芯片解决
- 权限本身已有 `report.view.all`，问题在展示与默认周期

### 使用的技术栈
FastAPI、Vue3、Element Plus

### 修改了哪些文件
- `backend/app/api/progress_reports.py`、`backend/app/schemas/__init__.py`
- `web-pc/src/views/ReportBoardView.vue`、`web-pc/dist`
- `README.md`

---

## 会话总结（2026-09-07 — PC 首次改密 / 钉钉免登不改密）

### 会话的主要目的
区分登录方式：PC 账号密码首次仍强制改密；钉钉 H5 免登与 PC 扫码验证企业身份后不强制改密，并解除管理员被卡住的改密标记。

### 完成的主要任务
- 后端：钉钉免登 `_login_by_dingtalk_userid`、绑定 `POST /auth/dingtalk/bind` 成功后清除 `must_change_password`
- 数据：确认 `admin` 及已绑钉钉用户强制改密标记已清除
- 钉钉 H5：路由/登录/绑定/首页不再因该字段跳改密页（兜底）
- PC：扫码登录写入 token 时不强制跳改密；账号密码登录逻辑不变
- 冒烟：钉钉路径返回 `must_change_password=false`；未改密账号密码路径仍为 `true`

### 关键决策和解决方案
- 不改 `POST /auth/login`，保证 PC 初始密码仍强制改密
- 以后端清标记为主，前端钉钉端仅做小改动兜底，避免旧会话残留

### 使用的技术栈
FastAPI、SQLAlchemy、Vue3、Pinia、Vite

### 修改了哪些文件
- `backend/app/api/auth.py`
- `web-dingtalk/src/router/index.ts`、`stores/auth.ts`、`views/HomeView.vue`、`views/BindView.vue`、`dist/`
- `web-pc/src/stores/user.ts`、`dist/`
- `README.md`

---

## 会话总结（2026-09-07 — 找不到 8/31～9/4 周报）

### 会话的主要目的
用户反馈工作台看不到已导入的 `2026-08-31～09-04` 周报。

### 完成的主要任务
- 核实库中数据完好：11 人已交、155 条任务；看板 API 默认会自动切到该周期
- 根因偏体验：默认打开「本周」(9/7)，个人「写周报」无本周内容；「按项目」因导入未绑 `project_id` 为空，易误判没数据
- 新增 `GET /progress-reports/my-periods`；`/mine` 支持周期内日期命中
- PC 写周报：本周空时自动切到本人最近有数据的周期 + 周期芯片；看板文案提示看「按人/未归类」
- 钉钉写周报同样自动切最近一期；修复本地日期时区（避免 `toISOString` 偏一天）

### 关键决策和解决方案
- 数据不重导；以后端/前端「自动切有数据周期」降低找不到的概率
- 管理员看全员仍走「周报看板」；成员看自己走「写周报」自动切周期

### 使用的技术栈
FastAPI、Vue3、Element Plus、Vite

### 修改了哪些文件
- `backend/app/api/progress_reports.py`、`backend/app/schemas/__init__.py`
- `web-pc/src/views/ProgressReportView.vue`、`ReportBoardView.vue`、`dist/`
- `web-dingtalk/src/views/ReportView.vue`、`dist/`
- `README.md`

---

## 会话总结（2026-09-07 — H5 看不到周报）

### 会话的主要目的
用户反馈钉钉 H5 端看不到 8/31～9/4 周报。

### 完成的主要任务
- 根因：H5 原先只有「写周报」表单，没有全员看板；管理员本人无该周个人周报，打开即为空表
- 新增 H5「周报看板」页，调用 `/progress-reports/board`，自动切到有数据周期并默认展开全文
- 管理员底栏「周报」默认进看板；首页增加「周报看板（全员）」入口
- 「写周报」页增加本人周期全文列表 + 跳转看板按钮

### 关键决策和解决方案
- 有 `report.view.all` 权限进看板；普通成员仍进写周报并展示本人已有周期全文
- 静态资源已重新 build 到 `web-dingtalk/dist`（nginx `/workbench/m/`）

### 使用的技术栈
Vue3、Vite、FastAPI 看板 API

### 修改了哪些文件
- `web-dingtalk/src/views/ReportBoardView.vue`（新）
- `web-dingtalk/src/views/ReportView.vue`、`HomeView.vue`、`MainLayout.vue`、`router/index.ts`、`dist/`
- `README.md`

---

## 会话总结（2026-09-11 — 方案库模块）

### 会话的主要目的
在 PC 端增加「方案库」：上传历史方案便于学习共享，并结合多模态/大模型自动整理摘要与标签。

### 完成的主要任务
- 新建 `solutions` / `solution_tags` 表与权限点 `solution.view` / `solution.upload`（默认角色全开）
- 后端：本地落盘上传、PDF/Word/PPT 抽文本、图片多模态打标、列表检索/下载/编辑/重新整理
- PC 侧栏「方案库」页面：上传、标签筛选、详情抽屉、整理中轮询
- 系统管理模型用途增加「方案打标」；单测与 README / 架构说明已更新

### 关键决策和解决方案
- 文件类型：PDF、docx、pptx、常见图片；单文件 20MB；存 `backend/data/solutions/`
- 权限：全员可上传可查阅；本人或管理员可删改
- AI 用途 `solution_tag`，未配置时回退周报辅导模型 / `OPENAI_*`；本期不做 H5、项目隔离、向量检索

### 使用的技术栈
FastAPI、SQLAlchemy、httpx、pypdf、python-docx、python-pptx、Vue3、Element Plus、Vite

### 修改了哪些文件
- 后端：`models`、`migrations`、`permissions`、`init_service`、`config`、`schemas`、`main.py`、`requirements.txt`
- 新建：`api/solutions.py`、`services/solution_storage.py`、`solution_extract.py`、`solution_ai_tag.py`、`tests/test_solutions.py`
- PC：`SolutionLibraryView.vue`、`MainLayout.vue`、`router/index.ts`、`AdminView.vue`、`dist/`
- `.gitignore`、`PROJECT_ARCHITECTURE.md`、`README.md`

---

## 会话总结（2026-09-11 — 钉钉绑定改下拉）

### 会话的主要目的
系统管理里绑定钉钉时，不再手填 userid，改为下拉选择需要绑定的钉钉人员。

### 完成的主要任务
- 新增 `GET /api/admin/dingtalk/candidates`：从「政企市场部」通讯录拉取可选人员，默认排除已被其他人绑定的账号
- 绑定弹窗改为可搜索的下拉框（显示姓名 · 工号 · 脱敏手机号）；清空保存即解绑

### 关键决策和解决方案
- 下拉数据来自钉钉部门通讯录实时拉取，与同步用户同一部门
- 正在编辑的用户现有绑定仍会出现在列表中，便于保留或更换

### 使用的技术栈
FastAPI、钉钉通讯录 API、Vue3、Element Plus Select

### 修改了哪些文件
- `backend/app/api/admin.py`、`backend/app/schemas/__init__.py`
- `web-pc/src/views/AdminView.vue`、`web-pc/dist/`
- `README.md`

---

## 会话总结（2026-09-11 — 钉钉绑定搜不到人）

### 会话的主要目的
修复「绑定钉钉」下拉无法搜到对应人员（如孙振振 FY0354）。

### 完成的主要任务
- 根因：孙振振的钉钉 userid 被误绑在 `admin` 上，且下拉只显示未绑定人员，导致被过滤掉
- 已清除 `admin` 上的错误钉钉绑定
- 下拉改为显示通讯录全部人员；已被其他人占用的项灰色不可选，并显示「已绑 xxx」
- 工号与当前系统账号一致的人自动置顶并预选

### 关键决策和解决方案
- 默认 `unbound_only=false`，避免「人在通讯录但看不见」
- 占用账号不可选，防止误绑冲突

### 使用的技术栈
FastAPI、钉钉通讯录 API、Vue3 Element Plus

### 修改了哪些文件
- `backend/app/api/admin.py`（及数据库中 admin 的 dingtalk_user_id）
- `web-pc/src/views/AdminView.vue`、`web-pc/dist/`
- `README.md`

---

## 会话总结（2026-09-11 — 删除账号）

### 会话的主要目的
在系统管理用户列表中增加「删除账号」能力。

### 完成的主要任务
- 新增 `DELETE /api/admin/users/{id}`：处理外键后硬删除账号
- 用户管理操作列增加「删除」按钮（二次确认）
- 同步删除逻辑一并支持方案库上传人改挂，避免外键失败

### 关键决策和解决方案
- 禁止删除 `admin` 与当前登录账号
- 项目负责人、任务负责人等改挂到 `admin`；个人周报/通知删除；方案库上传人改挂 admin

### 使用的技术栈
FastAPI、SQLAlchemy、Vue3、Element Plus

### 修改了哪些文件
- `backend/app/services/user_sync.py`、`backend/app/api/admin.py`
- `backend/tests/test_delete_user.py`
- `web-pc/src/views/AdminView.vue`、`web-pc/dist/`
- `README.md`

---

## 会话总结（2026-09-12 — 方案库继续本机存储）

### 会话的主要目的
确认方案库本期仍存工作台本机，对象存储（MinIO/数据仓库）延后改造。

### 完成的主要任务
- 在 README「方案库」小节写明：本期本机落盘；后续可改 S3 兼容的约定；不接入远端、密钥不进仓库
- 未改代码与配置，不碰远端主机

### 关键决策和解决方案
- 先本机、后改造；本期只做文档约定

### 使用的技术栈
文档（Markdown）

### 修改了哪些文件
- `README.md`

---

## 会话总结（2026-09-12 — 批量删除账号）

### 会话的主要目的
按用户提供的姓名列表删除系统账号。

### 完成的主要任务
- 按姓名匹配后删除 31 个账号（含部分演示账号）
- 跳过并保留「系统管理员」admin
- 名单外 11 名员工未删，现共剩余 12 个账号

### 关键决策和解决方案
- 复用 `delete_user_account` 硬删除并处理外键改挂
- 不删除 admin；不处理名单外用户

### 使用的技术栈
既有用户删除服务（SQLAlchemy）

### 修改了哪些文件
- 数据库用户数据（无业务代码变更）
- `README.md`

---

## 会话总结（2026-09-12 — 管理员不参与周报提交统计）

### 会话的主要目的
周报看板中系统管理员不出现在应提交/未交名单。

### 完成的主要任务
- 看板接口拉取人员时排除 `username=admin`
- README 周报说明同步更新

### 关键决策和解决方案
- 仅从「应提交」统计排除 admin；管理员仍可查看看板，不强制交周报

### 使用的技术栈
FastAPI

### 修改了哪些文件
- `backend/app/api/progress_reports.py`
- `README.md`

---

## 会话总结（2026-09-12 — 移除测试演示账号）

### 会话的主要目的
去掉周报看板里的测试账号（张主管/李项目/王成员/赵成员），并防止重启后自动回来。

### 完成的主要任务
- 删除 manager / owner1 / member1 / member2 四个演示账号
- 启动流程不再调用 `ensure_demo_users`；需要时才跑 `scripts/seed_demo.py`
- README 注明正式环境不自动创建演示账号

### 关键决策和解决方案
- 根因：后端 lifespan 每次启动会重建演示用户；正式环境改为仅 init_rbac

### 使用的技术栈
FastAPI lifespan、既有用户删除服务

### 修改了哪些文件
- `backend/app/main.py`
- 数据库用户数据
- `README.md`

---

## 会话总结（2026-09-12 — 添加模型拉取远端列表）

### 会话的主要目的
添加/编辑模型时，填好 Base 与 Key 后可拉取远端模型列表并下拉选择。

### 完成的主要任务
- 后端 `list_openai_compatible_models` + `POST /api/admin/models/discover`
- 前端模型名改为可搜索下拉（可手填）+「获取模型列表」按钮
- 单测与构建部署完成

### 关键决策和解决方案
- 走 OpenAI 兼容 `GET {base}/models`；编辑时可复用库中已存密钥
- 失败不清空已填模型名；`allow-create` 兜底手填

### 使用的技术栈
FastAPI、httpx、Vue3 Element Plus Select

### 修改了哪些文件
- `backend/app/services/llm_client.py`、`api/admin.py`、`schemas/__init__.py`
- `backend/tests/test_llm_discover.py`
- `web-pc/src/views/AdminView.vue`、`web-pc/dist/`
- `README.md`

---

## 会话总结（2026-09-12 — 9月7–11日周报 Excel 整理）

### 会话的主要目的
读取 docs 下 9月7日–11日周报汇总 Excel，对照工作台三问标准做结构化整理并指出问题。

### 完成的主要任务
- 解析 10 份周报（含 xls）；系统库内该周无 progress_reports
- 按人整理质量与卡点；映射旧模板列到三问字段
- 产出 Canvas 分析页供并排查看

### 关键决策和解决方案
- 旧「周进度汇表」与系统脱节；建议迁工作台；标出热成像/停车场/污水零直排/家医停机等应升级项

### 使用的技术栈
openpyxl、xlrd、Cursor Canvas

### 修改了哪些文件
- `canvases/weekly-report-sep7-11-review.canvas.tsx`（分析产物）
- `README.md`

---

## 会话总结（2026-09-12 — Excel 周报 LLM 入库）

### 会话的主要目的
将 9月7日–11日 Excel 周报先入库，并用 LLM 结构化整理为工作台「三问」卡片。

### 完成的主要任务
- 编写一次性导入脚本：解析 xlsx/xls → MiniMax（report_structure）整理 → 写入 `progress_reports` / `progress_report_items`
- 成功导入 10 人本周已提交周报，共 95 条任务卡；俞烨涛文件夹无 Excel，已跳过
- 看板周期：`period_start=2026-09-07`，`period_end=2026-09-11`，状态 `submitted`

### 关键决策和解决方案
- 复用库内 `report_structure` 模型（MiniMax-M2.7-highspeed），失败自动重试 1 次
- 跳过投标/客户侧等历史表；每人最多 12 条；含糊原文标注「原文信息不足」不编造
- 金滨 `.xls` 列错乱仍用全文交给 LLM 抽取；主题年份笔误已手工改为 2026
- 对象可重复跑脚本覆盖同一周同一人周报（upsert）

### 使用的技术栈
Python、SQLAlchemy、openpyxl、xlrd、httpx、MiniMax OpenAI 兼容接口、PostgreSQL

### 修改了哪些文件
- `backend/scripts/import_excel_week_reports.py`（新增）
- 数据库：`progress_reports` / `progress_report_items`（本周 10 份）
- `README.md`


---

## 会话总结（2026-09-12 — 周报工作标签）

### 会话的主要目的
给周报任务卡打上业务标签（售前、售中、售后、运维、项目管理、流程跟进、客户对接、方案汇报等），并完成本周已入库数据的打标。

### 完成的主要任务
- 周报卡片新增 `tags` 字段（JSONB）；固定词表 + 规范化
- 填写页可多选标签；看板可展示并按标签筛选
- 用 LLM 为本周（2026-09-07）95 条任务卡全部打标
- 导入脚本同步支持写入 tags

### 关键决策和解决方案
- 词表固定 8 类，每卡 1～3 个；同义词映射（如「售前支持」→「售前」）
- LLM 失败时用关键词规则兜底，保证不空标

### 使用的技术栈
FastAPI、SQLAlchemy JSONB、MiniMax、Vue3 Element Plus Select

### 修改了哪些文件
- `backend/app/services/work_tags.py`、`models/__init__.py`、`core/migrations.py`
- `backend/app/schemas/__init__.py`、`api/progress_reports.py`
- `backend/scripts/tag_week_report_items.py`、`import_excel_week_reports.py`
- `backend/tests/test_work_tags.py`
- `web-pc/src/views/ProgressReportView.vue`、`ReportBoardView.vue`、`web-pc/dist/`
- `README.md`


---

## 会话总结（2026-09-12 — 周报模型自动打标）

### 会话的主要目的
让填写周报时，AI 结构化整理同步自动打上工作标签，无需人工逐条点选。

### 完成的主要任务
- `ai_structure` 提示词与返回结构增加 `tags`；模型优先，规则兜底
- 填写页：AI 整理返回后自动写入卡片标签；建议区展示标签；点「采用」也会带上标签
- 补充 `guess_work_tags` / `resolve_item_tags` 与单测

### 关键决策和解决方案
- 正文润色仍需点「采用」；标签属于分类信息，返回后立即填入，用户可改
- 无模型时规则关键词猜测，保证不空标

### 使用的技术栈
FastAPI、MiniMax report_structure、Vue3

### 修改了哪些文件
- `backend/app/services/work_tags.py`、`report_ai_structure.py`
- `backend/app/schemas/__init__.py`
- `backend/tests/test_work_tags.py`
- `web-pc/src/views/ProgressReportView.vue`、`web-pc/dist/`
- `README.md`


---

## 会话总结（2026-09-12 — 优化周报 AI 检查提示）

### 会话的主要目的
优化「检查结果」提示词与后处理，避免输出英文字段名、研发黑话和恐吓式空话。

### 完成的主要任务
- 重写 `report_ai_coach` 系统提示：只盯三问、白话、禁字段名
- 送给模型的内容改为中文键；返回结果清洗与括号截断修复（兼容 MiniMax 思考标签）
- 改写建议展示中文栏位名；空周报直接给短提示不长篇说教
- 前端改写建议显示「做成了什么」等中文标签

### 关键决策和解决方案
- 风险=正常不逼写说明；「写得虚」进清晰度而非风险警报
- 工作标签只能推荐固定 8 类词表

### 使用的技术栈
FastAPI、MiniMax-M3、Vue3

### 修改了哪些文件
- `backend/app/services/report_ai_coach.py`
- `backend/tests/test_report_ai_coach.py`
- `web-pc/src/views/ProgressReportView.vue`、`web-pc/dist/`
- `README.md`


---

## 会话总结（2026-09-12 — 周报 AI 检查对抗性测试）

### 会话的主要目的
对优化后的周报 AI 检查做对抗性测试，验证捣乱输入下是否仍保持白话、安全、有用。

### 完成的主要任务
- 新增 `scripts/adversarial_coach_test.py`：6 类攻击（空报、字段名诱饵、空话、研发黑话、正常风险、漏报卡点）
- 实测 6/6 通过；发现 F 用例曾因 think 内截断 JSON + 坏引号解析失败
- 增强 `_extract_json_obj`：去 fence、截断修补、宽松字段抽取；F 用例后测不再回退

### 关键决策和解决方案
- 展示文案禁止英文字段名/人天 UAT；规则兜底保留停机→卡点提示
- MiniMax 思考标签内不完整 JSON 用宽松解析，避免整单失败

### 使用的技术栈
Python、MiniMax-M3、pytest、debug NDJSON

### 修改了哪些文件
- `backend/scripts/adversarial_coach_test.py`
- `backend/app/services/report_ai_coach.py`
- `backend/tests/test_report_ai_coach.py`
- `README.md`


---

## 会话总结（2026-09-12 — 项目总览改造：任务进度 + 周报提交/质量）

### 会话的主要目的
解决项目总览「像空壳」：补齐任务进度、本周周报提交与质量；周报仍可不选项目，用名称相似度做展示期归类并标出未归类。

### 完成的主要任务
- 扩展总览 stats / 项目列表字段：任务完成率、周报提交率、质量告警、未归类条数；项目行展示任务进度与周报优/中/差
- 抽出 `project_match.guess_project_id`（阈值 0.45，不写库）；看板与周报出参支持 `inferred_project_id`
- PC 总览页新统计卡、未归类横幅、表格新列；项目详情周报 Tab 支持推断归属
- 钉钉 H5 首页追加任务完成率 / 周报提交 / 质量告警
- 构建并发布 `web-pc` / `web-dingtalk` dist；重启后端

### 关键决策和解决方案
- 用户确认 1B+2B：有项目但缺运营指标；周报可不绑项目，展示期猜测 + 未归类
- 质量不落库：软提示 + 升级/卡点类条目合计为告警；项目维优/中/差
- 无 `report.view.all` 时周报相关字段为 0，前端隐藏卡片，避免成员看全员提交

### 使用的技术栈
FastAPI、SQLAlchemy、Vue3、Element Plus、钉钉 H5、pytest

### 修改了哪些文件
- `backend/app/schemas/__init__.py`
- `backend/app/services/project_match.py`（新）
- `backend/app/services/project_service.py`
- `backend/app/services/report_ai_structure.py`
- `backend/app/api/progress_reports.py`
- `backend/tests/test_project_match.py`（新）
- `web-pc/src/views/OverviewView.vue`、`ProjectDetailView.vue`、`web-pc/dist/`
- `web-dingtalk/src/views/HomeView.vue`、`web-dingtalk/dist/`
- `README.md`

---

## 会话总结（2026-09-12 — 孙振振钉钉绑定管理员）

### 会话的主要目的
将工号 FY0354（孙振振）的钉钉账号绑定为系统管理员，便于钉钉免登进入管理员身份。

### 完成的主要任务
- 从钉钉「政企市场部」通讯录核对：姓名孙振振、工号 FY0354、userid=`65102223285497`
- 确认系统中暂无独立 FY0354 用户账号；`admin` 原钉钉绑定为空
- 将 `admin.dingtalk_user_id` 设为上述 userid，并写入审计日志

### 关键决策和解决方案
- 沿用既有做法：钉钉免登匹配的是 userid（不是工号），挂在 `admin` 账号上即管理员身份
- 手机号已脱敏，不落日志明文

### 使用的技术栈
钉钉通讯录 API、SQLAlchemy、PostgreSQL

### 修改了哪些文件
- 数据库 `users` / `audit_logs`（admin 钉钉绑定）
- `README.md`

---

## 会话总结（2026-09-12 — 钉钉 H5 免登进入）

### 会话的主要目的
确认孙振振可用钉钉免登进入 H5 工作台，并加强取码兼容。

### 完成的主要任务
- 校验：userid `65102223285497` 已绑 admin，具备 `dingtalk.login`，后端 `_login_by_dingtalk_userid` 可直接发 token
- H5 取码增加 `dd.runtime.permission.requestAuthCode`（企业内 H5 常用），并保留 requestAuthCode / getAuthCode
- 首页补充「须在钉钉 App 内打开」提示；重新构建 `web-dingtalk/dist`

### 关键决策和解决方案
- 免登只在钉钉客户端内有效；开放平台「移动端首页」填 `http://30.251.255.64/workbench/m/`
- 不走 PC 扫码 OAuth

### 使用的技术栈
钉钉 JSAPI、Vue3、FastAPI

### 修改了哪些文件
- `web-dingtalk/src/utils/dingtalkAuth.ts`
- `web-dingtalk/src/vite-env.d.ts`
- `web-dingtalk/src/views/HomeView.vue`
- `web-dingtalk/dist/`
- `README.md`

---

## 会话总结（2026-09-12 — 聚焦团队 11 人）

### 会话的主要目的
明确只需关注钱波等 11 名团队成员；校正此前误停用。

### 完成的主要任务
- 启用：钱波、罗琳、凌亮、徐初、孙聪、俞烨涛、朱高见、王捷、金滨、许金斌、周永祺（均已绑钉钉）
- 保留 admin；测试账号 t_chain_* 保持停用
- 周报应提交人数按活跃非 admin 计，约为 11

### 关键决策和解决方案
- 「钱波这11个人」= 钱波 + 名单 10 人；此前只留钱波/罗琳属误解，已改回

### 使用的技术栈
SQLAlchemy、PostgreSQL

### 修改了哪些文件
- 数据库 `users.is_active`、`audit_logs`
- `README.md`

---

## 会话总结（2026-09-12 — 周报统计固定 11 人）

### 会话的主要目的
周报提交率 / 看板「按人」统计只计算指定 11 名团队成员。

### 完成的主要任务
- 新增 `report_roster.py` 固定名单（钱波等 11 人，不含 admin）
- 总览 `report_expected` / `report_submitted`、质量告警与未归类仅统计名单内
- 周报看板人员列表改为只显示这 11 人
- 重启后端；实测应交 11、已交 10

### 关键决策和解决方案
- 用工号白名单，避免以后钉钉同步把其他人重新算进应交人数

### 使用的技术栈
FastAPI、SQLAlchemy、pytest

### 修改了哪些文件
- `backend/app/services/report_roster.py`（新）
- `backend/app/services/project_service.py`
- `backend/app/api/progress_reports.py`
- `backend/tests/test_report_roster.py`（新）
- `README.md`

---

## 会话总结（2026-09-12 — 项目自建 + 周报/任务必须关联项目）

### 会话的主要目的
团队可自建真实立项项目；写周报必须选项目；无立项归属统一挂「日常任务」，便于进度把控。

### 完成的主要任务
- 启动时确保内置项目「日常任务」，活跃成员自动加入；历史 `project_id` 为空的周报卡已回填
- `member` / `project_owner` 补 `project.create`；总览增加「新建项目」弹窗
- 周报硬拦：每条任务卡必须选项目；PC / 钉钉 H5 项目改为必选
- 自建/交办任务默认倾向「日常任务」；相关单测已更新

### 关键决策和解决方案
- 杂项不造假项目名，统一选「日常任务」
- 提交不静默改项目：前端必选 + 后端硬拦；历史数据一次性写库回填

### 使用的技术栈
FastAPI、SQLAlchemy、Vue3、Element Plus、钉钉 H5、pytest

### 修改了哪些文件
- `backend/app/services/project_service.py`、`init_service.py`、`report_quality.py`、`main.py`
- `backend/app/rbac/permissions.py`、`backend/app/api/progress_reports.py`
- `backend/tests/test_report_quality.py`、`test_progress_reports.py`
- `web-pc/src/views/OverviewView.vue`、`ProgressReportView.vue`、`TaskAssignPanel.vue`、`web-pc/dist/`
- `web-dingtalk/src/views/ReportView.vue`、`TaskCreateView.vue`、`web-dingtalk/dist/`
- `README.md`

---

## 会话总结（2026-09-12 — 纠正误挂「日常任务」）

### 会话的主要目的
明显不是日常杂项的周报任务，不要落户到「日常任务」。

### 完成的主要任务
- 回填/纠正逻辑改为：先匹配已有立项 → 像立项则按概述自动建档 → 仅开票报销等杂项进日常
- 纠正「原周报导入」套话导致误判日常的问题
- 实测：误挂日常的条目从约 250 条降到仅杂项（如开票报销）

### 关键决策和解决方案
- 标题含项目/工程/系统等，或专名够长 → 不进日常
- 自动建档项目带说明「请核对是否与立项名称一致」，团队可后续改名

### 使用的技术栈
FastAPI、SQLAlchemy、pytest

### 修改了哪些文件
- `backend/app/services/project_match.py`
- `backend/app/services/project_service.py`
- `backend/app/main.py`
- `backend/tests/test_project_reclassify.py`
- `README.md`

- 额外修复：「原周报导入」套话误触「周报」日常词；「家医」短称呼映射到「家庭医生」

---

## 会话总结（2026-09-12 — 总览卡片对齐与质量告警可打开）

### 会话的主要目的
项目总览指标卡片大小/间距对齐；「本周质量告警」可点开查看明细。

### 完成的主要任务
- 指标区改为 4 列等宽网格，卡片等高（预留日期行占位）
- 补「周报看板」入口卡，消除第二行空缺
- 点击「质量告警」弹出明细列表（质量提示/需升级/卡点），可跳转对应周报或看板
- 看板接口为每人周报附带 soft_warnings，便于展示质量提示

### 关键决策和解决方案
- 用 CSS Grid 替代 el-row/el-col，避免高度参差与空隙
- 告警先弹窗速览，再提供「去周报看板」完整查看

### 使用的技术栈
Vue3、Element Plus、FastAPI

### 修改了哪些文件
- `web-pc/src/views/OverviewView.vue`
- `web-pc/src/views/ReportBoardView.vue`
- `backend/app/api/progress_reports.py`
- `README.md`

---

## 会话总结（2026-09-13 — 打开应用链接）

### 会话的主要目的
打开项目支撑工作台访问链接。

### 完成的主要任务
- 确认并打开 PC 端地址：http://30.251.255.64/workbench/
- 同步告知钉钉 H5 地址备查

### 关键决策和解决方案
优先打开 PC 总览入口；钉钉内用 H5 路径。

### 使用的技术栈
无代码变更

### 修改了哪些文件
- `README.md`（仅追加本总结）

---

## 会话总结（2026-09-13 — 排查与其它应用冲突）

### 会话的主要目的
确认项目支撑工作台是否与同机其它应用（端口/路径/数据库）冲突。

### 完成的主要任务
- 检查监听端口、systemd 服务、nginx 路径、PostgreSQL/MariaDB 归属
- 对 `/`、`/learnflow/`、`/workbench/`、本机 7000/3000/8100 做 HTTP 冒烟

### 关键决策和解决方案
结论：**无硬冲突，隔离正常**。同机并存应用分工如下：

| 应用 | 服务 | 端口 | 外网路径 | 数据库 |
|------|------|------|----------|--------|
| 人才画像 talent-profile | talent-profile.service | 3000 + nginx80 | `/`、`/api/`、`/h5/` 等 | MariaDB `talent_profile` |
| 学习流 learn-flow | learnflow.service | 7000 | `/learnflow/` | （其自身配置，非本库） |
| 项目支撑工作台 | workbench-backend | **8100（仅本机）** | **`/workbench/`、`/workbench/m/`、`/workbench/api/`** | PostgreSQL `project_workbench` |

注意点（非冲突）：
- 三者共用 nginx:80，但路径前缀不同，互不抢路由
- 工作台 API 不走根路径 `/api/`（那是人才画像），必须走 `/workbench/api/`
- 钉钉回调各自独立路径；工作台为 `/workbench/api/auth/dingtalk-callback...`
- `workbench-frontend.service` 为构建发布（exited）属正常，不表示前端挂了

### 使用的技术栈
ss、systemctl、nginx、curl、PostgreSQL/MariaDB 只读检查

### 修改了哪些文件
- `README.md`（仅追加本总结）

---

## 会话总结（2026-09-13 — 澄清误进人才画像登录页）

### 会话的主要目的
用户访问 `http://30.251.255.64/login?redirect=/dashboard`，需确认是否工作台。

### 完成的主要任务
- 实测该地址页面标题为「人才画像平台 - 管理后台」，属 talent-profile
- 对照确认工作台登录为 `/workbench/login`，标题「项目支撑工作台」
- 重新打开正确入口 `/workbench/`

### 关键决策和解决方案
根路径 `/login` ≠ 工作台；工作台必须带前缀 `/workbench/`。

### 使用的技术栈
curl 只读核对

### 修改了哪些文件
- `README.md`（仅追加本总结）

---

## 会话总结（2026-09-13 — 项目改名/阶段规范/运维运营类型）

### 会话的主要目的
支持手工修正项目名；规范阶段；新增「运维」「运营」项目类型；周报标题「项目-任务」拆分。

### 完成的主要任务
- 项目类型新增 `ops`（运维）、`operation`（运营）；采购标签改为「采购」以免与运营混淆
- 规范阶段词表：需求分析→立项→设计→实施/开发→测试→上线→运维保障→验收→收尾；新建项目自动写入阶段时间线
- 项目详情增加「修正项目信息」（名称/类型/阶段/状态/分级）
- 智能拆分：`智安校园-…录入及家长账号配置` → 项目智安校园 + 任务后半段 + 类型运营；`学校城域网终端中毒处理` → 类型运维
- 写周报任务概述失焦时，若匹配已有项目则自动归入并改写任务名

### 关键决策和解决方案
- 运维/运营采用**项目类型 A 方案**（非固定筐）：运维偏技术底层，运营偏平台操作
- 管理员/普通成员补充 `project.edit`，便于纠偏自动建档名称

### 使用的技术栈
FastAPI、Vue3、Element Plus、pytest

### 修改了哪些文件
- `backend/app/models/enums.py`
- `backend/app/services/project_match.py`、`project_service.py`、`project_phases.py`（新）
- `backend/app/api/projects.py`、`backend/app/rbac/permissions.py`
- `backend/tests/test_project_reclassify.py`
- `web-pc/src/constants/projectMeta.ts`（新）
- `web-pc/src/views/OverviewView.vue`、`ProjectDetailView.vue`、`ProgressReportView.vue`
- `README.md`

---

## 会话总结（2026-09-13 — 重点任务级别与看板推送）

### 会话的主要目的
交办任务可定义级别；总览展示重点任务；管理者可调级别；重点任务推送给责任人。

### 完成的主要任务
- 任务增加三级 `priority`：重点 critical / 关注 watch / 普通 normal
- 权限 `task.level.change` 赋予部门负责人、项目负责人、管理员
- 总览新增「重点任务」卡片与明细接口 `/projects/overview/key-tasks`
- 创建为重点或调升到重点：站内 WebSocket + 钉钉工作通知（无 agent 则跳过不阻断）
- PC 任务面板与钉钉 H5 创建/详情支持选级别与管理者改级别

### 关键决策和解决方案
- 新建时可自定级别；事后改级别需管理者权限
- 推送在事务提交后发送，避免与 ORM 会话冲突
- 未配置钉钉 agent 时仅记日志，业务仍成功

### 使用的技术栈
FastAPI、SQLAlchemy、Vue3、Element Plus、钉钉工作通知骨架、pytest

### 修改了哪些文件
- `backend/app/models/enums.py`、`models/__init__.py`
- `backend/app/schemas/__init__.py`、`rbac/permissions.py`、`core/migrations.py`
- `backend/app/services/task_service.py`、`task_notify.py`（新）、`project_service.py`
- `backend/app/api/tasks.py`、`projects.py`
- `backend/tests/test_tasks_assign.py`
- `web-pc/src/views/OverviewView.vue`、`components/TaskAssignPanel.vue`
- `web-dingtalk/src/views/TaskCreateView.vue`、`TaskDetailView.vue`、`utils/api.ts`
- `README.md`

---

## 会话总结（2026-09-13 — 交办轻量接收与全应用 AI P0–P2）

### 会话的主要目的
完成交办轻量接收闭环，并按「人能不介入就不介入」原则落地统一 AI 平台，覆盖任务/周报/项目/卡点/方案库/总览（P0–P2）。

### 完成的主要任务
- 统一 AI 平台层 `ai_platform.run_structured`（按 purpose 调模型，无模型规则兜底）
- 交办：待接收 → 一键接收 → 才能改进度；催办/改派重置 pending；钉钉/站内通知
- 任务 AI：一句话草稿、接收后 AI 拆解清单（PC + 钉钉）
- P1/P2 挂钩：立项草稿、卡点草稿、总览「今天该看什么」、方案库「AI 帮找」、周报「粘贴原文一键成卡」；Admin 可配多 purpose 模型
- 架构文档写入 AI 深度参与与低负担约束；pytest 8 通过；前后端已构建并重启后端

### 关键决策和解决方案
- AI 只出草稿，用户确认后才走原有写库接口
- 交办不可拒绝；自建任务直接已接收
- 改派后重新待接收并重推

### 使用的技术栈
FastAPI、SQLAlchemy、Vue3、Element Plus、钉钉 H5、pytest、systemd

### 修改了哪些文件
- `backend/app/services/ai_platform.py`、`task_ai.py`、`assist_ai.py`、`task_notify.py`、`task_service.py`
- `backend/app/api/ai_assist.py`（新）、`tasks.py`、`main.py`、`schemas`、`models`、`migrations`
- `backend/tests/test_task_acceptance.py`（新）、`test_tasks_assign.py`
- `web-pc`：`TaskAssignPanel`、`OverviewView`、`ProjectDetailView`、`ProgressReportView`、`SolutionLibraryView`、`AdminView`
- `web-dingtalk`：`TaskCreateView`、`TaskDetailView`
- `PROJECT_ARCHITECTURE.md`、`README.md`

---

## 会话总结（2026-09-13 — 自动应用验收）

### 会话的主要目的
用户要求自动完成应用测试（交办接收 + AI P0–P2），无需手工点页面。

### 完成的主要任务
- 编写并执行 `scripts/auto_e2e_accept_ai.py` 线上全链路冒烟
- pytest `test_task_acceptance` 4 通过；E2E `fails: []`
- 浏览器自动登录 admin，确认总览出现「今天该看什么」AI 摘要

### 关键决策和解决方案
- 用临时责任人账号完成：交办→门禁拦截→催办→改密→接收→改进度→AI拆解
- AI 偶发 `source=error` 时规则兜底仍返回 200，不挡业务

### 使用的技术栈
httpx 冒烟脚本、pytest、cursor-ide-browser、nginx 静态资源探测

### 修改了哪些文件
- `scripts/auto_e2e_accept_ai.py`（新）
- 调试埋点：`debug_ndjson.py`、`tasks.py`、`task_service.py`、`ai_assist.py`、PC 前端若干（待确认后可拆除）
- `README.md`

---

## 会话总结（2026-09-13 — 任务 P0–P4 / 项目普通·重点·日常）

### 会话的主要目的
按产品要求调整分级：任务用 P0～P4；项目用「普通 / 重点 / 日常」。

### 完成的主要任务
- 任务 `priority`：`p0`～`p4`（默认 `p3`）；旧值 critical→p0、watch→p1、normal→p3 自动迁移
- P0 创建或调升会推送责任人；总览「重点任务」统计 P0+P1
- 项目 `level`：`normal` / `key` / `daily`；旧 red/yellow→key、green→normal；「日常任务」项目强制 `daily`
- PC / 钉钉创建与改级别界面同步；元数据接口 `/projects/meta/types` 已更新
- 拆除本轮调试埋点；pytest 8 通过；前后端已构建；后端已重启

### 关键决策和解决方案
- 任务优先级数字越小越重要（P0 最高）
- 项目三分法替代原红黄绿；统计卡改为「重点项目 / 日常项目」
- API 仍暂时回填 `red_count`/`yellow_count` 兼容旧前端字段

### 使用的技术栈
FastAPI、SQLAlchemy 迁移、Vue3、Element Plus、钉钉 H5、pytest

### 修改了哪些文件
- `backend/app/models/enums.py`、`models/__init__.py`、`core/migrations.py`、`schemas`
- `backend/app/services/task_service.py`、`task_notify.py`、`task_ai.py`、`assist_ai.py`、`project_service.py`
- `backend/app/api/tasks.py`、`projects.py`
- `web-pc`：`projectMeta.ts`、`OverviewView`、`TaskAssignPanel`、`ProjectDetailView`、测试
- `web-dingtalk`：`TaskCreateView`、`TaskDetailView`
- `README.md`

---

## 会话总结（2026-09-13 — 周报工作台全面重构方案二）

### 会话的主要目的
将「周报看板」重构为人员周报工作台：卡片主页 + 点人详情，覆盖 PC 与钉钉，并支持提交考核、进度/事件链、一阶段 LLM 评价落库。

### 完成的主要任务
- 主页改为人员卡片 + 已交/未交总览条，不再内联全文；项目对齐迁到「项目」Tab
- 新增人员详情：本期/历史周报、提交率与兑现率、进度链（默认）+ 事件链（可折叠）
- 点开详情自动生成一阶段评价并写入 `progress_report_reviews`；支持强制重评
- 新增提交矩阵、board `mode=summary|full`、workbench/timelines/ai-review API
- PC / 钉钉两端同步改造并完成生产构建

### 关键决策和解决方案
- 采用方案二完整工作台形态，但 Roster 配置化与二阶段人工打分留作后续
- summary 主页轻量；总览告警与项目详情仍用 `mode=full` 取全文
- 无 LLM 时规则兜底评价，保证考核链路可用

### 使用的技术栈
FastAPI、SQLAlchemy、pytest、Vue3、Element Plus、钉钉 H5、统一 AI 平台 `report_review`

### 修改了哪些文件
- 设计：`docs/superpowers/specs/2026-09-13-report-workbench-design.md`
- 后端：`models`、`migrations`、`schemas`、`progress_reports.py`、`ai_platform.py`、新建 `report_ai_review.py`、测试
- PC：`ReportBoardView.vue`、`components/report-board/*`、`OverviewView`、`ProjectDetailView`、`MainLayout`
- 钉钉：`ReportBoardView.vue`、新建 `ReportPersonView.vue`、路由
- `README.md`

---

## 会话总结（2026-09-13 — 全模块端到端测试）

### 会话的主要目的
按 M1–M10 对整个应用做自动端到端验收（含周报工作台与交办专项），并修掉阻塞全绿的单测。

### 完成的主要任务
- 修复单测：完成任务须带 `result_text`；链测试用户恢复 `is_active`
- 新增 `scripts/auto_e2e_all_modules.py`（认证/项目/卡点/任务/个人/周报工作台/AI/方案库/管理/前端）
- 强化 `auto_e2e_report_workbench.py`、`auto_e2e_accept_ai.py`（专用 `e2e_runner`、前端 bundle 探测）
- 结果：**pytest 96 passed**；全模块 Live E2E **ok**；周报专项 **ok**；交办+AI 专项 **ok**

### 关键决策和解决方案
- 正式环境 `admin` 密码可能已改，E2E 用专用账号 `e2e_runner`（admin+dept_head），不改正式账号密码
- 允许创建带 `E2E-` 前缀的临时项目/任务/周报/卡点
- 前端 SPA 从 `web-pc/dist/assets` 探测 ReportBoardView / TaskAssignPanel，避免写死 hash

### 使用的技术栈
httpx Live 冒烟、pytest、FastAPI、PostgreSQL、nginx 静态资源

### 修改了哪些文件
- `scripts/auto_e2e_all_modules.py`（新）
- `scripts/auto_e2e_report_workbench.py`、`scripts/auto_e2e_accept_ai.py`
- `backend/tests/test_dingtalk_auth.py`、`test_report_ai_structure.py`
- `README.md`

### 如何重跑
```bash
cd /home/workbench/backend && .venv/bin/pytest tests/ -q
backend/.venv/bin/python ../scripts/auto_e2e_all_modules.py
backend/.venv/bin/python ../scripts/auto_e2e_report_workbench.py
backend/.venv/bin/python ../scripts/auto_e2e_accept_ai.py
```

---

## 会话总结（2026-09-13 — 按白名单同步团队并设孙振振为管理员）

### 会话的主要目的
只保留钉钉同步的指定人员；孙振振设为系统管理员。

### 完成的主要任务
- 白名单同步 12 人：孙振振、周永祺、许金斌、金滨、王捷、朱高见、俞烨涛、孙聪、徐初、凌亮、罗琳、钱波
- 删除部门内其余 26 个非名单账号
- 孙振振（FY0354）角色设为 `admin` + `dept_head`；系统 `admin` 名称改为孙振振，并绑定其钉钉 userid 便于免登
- 周报应交名单仍为上述除孙振振外的 11 人
- 新增 API：`POST /api/admin/users/sync-dingtalk-team`；脚本 `scripts/sync_team_allowlist.py`

### 关键决策和解决方案
- 钉钉免登挂在系统账号 `admin`（显示名孙振振）；工号账号 FY0354 同样具备管理员角色，可用账号密码登录
- 全量部门同步改为可按姓名白名单同步，避免再次导入无关人员

### 使用的技术栈
钉钉通讯录 API、SQLAlchemy、FastAPI

### 修改了哪些文件
- `backend/app/services/user_sync.py`、`report_roster.py`
- `backend/app/api/admin.py`
- `scripts/sync_team_allowlist.py`（新）
- `README.md`

---

## 会话总结（2026-09-13 — 清理测试数据）

### 会话的主要目的
删除端到端/单测产生的全部测试数据，避免污染正式库。

### 完成的主要任务
- 删除测试用户 6（`e2e_runner`、`e2e_*`、假 roster `FY0395/RosterE2E` 等）
- 删除测试项目 13（`E2E-*`、`task-proj-*`、`周报测试项目`）
- 删除测试任务 16、卡点 3、周报/评价相关记录
- 复核：无残留 E2E/自动验收/测试项目字符串

### 关键决策和解决方案
- 仅按命名规则与账号模式识别测试数据；不删正式业务项目
- 级联清理成员、阶段、任务流水、卡点历史、周报条目与评价

### 使用的技术栈
SQLAlchemy 异步会话、PostgreSQL

### 修改了哪些文件
- 仅数据库数据清理（无业务代码改动）
- `README.md`

---

## 会话总结（2026-09-13 — 大模型「通用」用途全覆盖）

### 会话的主要目的
让管理员配一次大模型即可覆盖全站 AI 用途，不必为每种用途重复添加同一套 API。

### 完成的主要任务
- 新增用途 `general`（通用）：解析时「专用 → 通用 → 环境变量」
- 管理端用途下拉增加「通用」，新建默认选通用；补齐「周报评价」
- 补充通用覆盖、专用优先、默认互斥单测

### 关键决策和解决方案
- 采用「通用」单用途兜底，而非多选或一键复制多行
- 同用途仍仅一个默认；通用与其它用途的默认互不影响
- 不改表结构，`purpose` 仍为字符串字段

### 使用的技术栈
FastAPI、SQLAlchemy、Vue3 + Element Plus、pytest

### 修改了哪些文件
- `backend/app/services/llm_resolver.py`
- `backend/app/services/ai_platform.py`
- `backend/tests/test_llm_models.py`
- `web-pc/src/views/AdminView.vue`
- `README.md`

---

## 会话总结（2026-09-13 — 通用用途自动测试与清理）

### 会话的主要目的
对「通用」用途做自动验证，并在测完后删除测试数据；同时避免单测误删正式模型配置。

### 完成的主要任务
- `pytest tests/test_llm_models.py`：6 passed
- 运行时验证：只配 general 可覆盖 `report_coach`/`solution_tag`；`task_draft` 专用优先
- 删除全部 `__auto_test_*` / example 测试模型；库内无残留测试行
- 修正单测清理逻辑：只删测试模型，不再 `DELETE` 全部 `LlmModel`

### 关键决策和解决方案
- 解析优先级：专用 → 通用 → 环境变量（运行日志已证实）
- 发现此前旧清理逻辑曾清空正式模型表；现已改为按名称/example 域名识别后删除
- 当前模型表为空，需管理员在系统管理中重新添加「通用」模型（如 MiniMax）

### 使用的技术栈
pytest、SQLAlchemy、调试 NDJSON 日志

### 修改了哪些文件
- `backend/app/services/llm_resolver.py`（含临时调试埋点）
- `backend/tests/test_llm_models.py`
- `README.md`

---

## 会话总结（2026-09-13 — 配置 MiniMax 通用模型）

### 会话的主要目的
将用户提供的 MiniMax 接口写入系统，用途设为「通用」，一次覆盖全站 AI。

### 完成的主要任务
- 拉取模型列表并选定 `MiniMax-M3`
- 写入模型管理：用途 `general`、启用、默认；连通测试通过
- 验证 `report_coach` / `solution_tag` / `task_draft` 等解析均回落到该通用配置
- 移除此前调试埋点；重建前端以展示「通用」选项
- 密钥仅存数据库，不写入文档（展示为脱敏）

### 关键决策和解决方案
- 模型 ID 以接口返回的 `MiniMax-M3` 为准（用户口头「M3」）
- 解析顺序仍为：专用 → 通用 → 环境变量

### 使用的技术栈
FastAPI、SQLAlchemy、OpenAI 兼容 HTTP、Vue 前端构建

### 修改了哪些文件
- `backend/app/services/llm_resolver.py`（清理调试埋点）
- 数据库 `llm_models`（新增通用配置）
- `web-pc/dist/*`（前端构建产物）
- `README.md`

---

## 会话总结（2026-09-13 — 清空总览并导入历史周报）

### 会话的主要目的
清空项目总览脏数据（负责人曾全是孙振振），并从 `docs/` 重新导入两周历史 Excel 周报。

### 完成的主要任务
- 清空项目域：98 项目 / 45 任务 / 相关子表；保留用户 13、LLM 模型 1
- 导入 8/31–9/4 周报：**11/11** 成功
- 导入 9/7–9/11 周报：LLM **9/10** + 金滨 `.xls` 专项补导 **1**，合计 **10/10**（俞烨涛该周无文件，跳过）
- 按周报任务卡自动建档/归类项目；负责人按**周报作者**匹配（「日常任务」仍由系统账号挂名）
- 终态：周报 21 份、条目 243、项目 33、交办任务 0

### 关键决策和解决方案
- 校验已强制要求所属项目：历史导入先挂「日常任务」，再 `backfill_null_report_projects`（新建项目 `owner_id`=周报作者，禁止一律 admin）
- 金滨 9/7 `.xls` 全文抽取过噪，改用运维周志日期行专项解析
- README 写明后续项目 Excel 导入的负责人匹配硬规则

### 使用的技术栈
SQLAlchemy、openpyxl/xlrd、MiniMax-M3（通用用途）、既有周报导入脚本

### 修改了哪些文件
- `backend/app/services/project_service.py`（建档负责人=周报作者）
- `scripts/import_week_reports_20260831.py`
- `backend/scripts/import_excel_week_reports.py`
- 数据库：`projects` / `progress_reports` / `progress_report_items` 等
- `README.md`

---

## 会话总结（2026-09-13 — 金滨/王捷驻场运维项目归档）

### 会话的主要目的
为数据局驻场（金滨）、教育局驻场（王捷）各建正式运维项目，把周报任务卡从错误拆分的伪项目中收拢归档。

### 完成的主要任务
- 新建「富阳区电子政务网驻场运维」（负责人金滨）、「富阳区教育城域网驻场运维」（负责人王捷）
- 阶段均为标准词「运维保障」，类型 `ops`；驻场条目打标「售后」「运维」
- LLM 分类：金滨 25 条全部驻场；王捷 16 条驻场、1 条日常（协助文旅卡推广）
- 清理无人引用的伪项目空壳 8 个

### 关键决策和解决方案
- 阶段用系统标准「运维保障」对应售后运维语义，不改词表
- 拿不准默认归驻场；仅明确非驻场进「日常任务」
- 一次性脚本 `backend/scripts/reassign_onsite_projects.py`，可幂等复跑

### 使用的技术栈
SQLAlchemy、MiniMax-M3（general）、httpx

### 修改了哪些文件
- `backend/scripts/reassign_onsite_projects.py`（新）
- 数据库 `projects` / `progress_report_items` / `phases` / `project_members`
- `README.md`

---

## 会话总结（2026-09-13 — 日报去催办、定位每日记录）

### 会话的主要目的
把日报定位为当天个人工作记录，取消看板日报模式的「催办/考核」语义；考核催办仍只针对周报。

### 完成的主要任务
- 看板日报模式：「已记/今日未记」，隐藏近几周提交率，不拉历史提交矩阵
- 周报模式保留「未交（催办）」
- 写日报页增加提示：不考核催办，周末汇总到周报
- 侧栏改为「写周报/日报」；更新填写标准与 README

### 关键决策和解决方案
- 本阶段只改 UI 语义，不改保存 API / 校验
- 日报汇总进周报留作下一阶段

### 使用的技术栈
Vue3 + Element Plus

### 修改了哪些文件
- `web-pc/src/components/report-board/ReportPeopleBoard.vue`
- `web-pc/src/views/ReportBoardView.vue`
- `web-pc/src/views/ProgressReportView.vue`
- `web-pc/src/layouts/MainLayout.vue`
- `docs/周报填写标准.md`
- `README.md`
- `web-pc/dist/*`（构建产物）

---

## 会话总结（2026-09-13 — 周报「下周计划」同步本人待办）

### 会话的主要目的
按已确认 MVP：仅在周报**提交**时，把「下周计划」规范成待办，写入个人工作台「我的待办」，草稿/日报不建。

### 完成的主要任务
- Task 增加溯源字段 `source_report_id`、`source_ref`；`source=report_next`
- 新建 `report_next_plan`：LLM 规范（做什么 / 做成什么样·做多少 / 截止日）+ 规则兜底；同步为自建 Task
- 提交周报成功后调用同步；接口返回 `next_plan_synced`；PC 提示同步条数
- 同周再提交按 `source_ref` 更新，不重复堆叠
- 单测通过；后端重启迁移；PC 前端已 build

### 关键决策和解决方案
- 无项目 → 挂「日常任务」；成功标准写入 checklist 首条
- LLM 用途：`report_structure` → `general`；同步失败不阻断周报提交
- 填写引导：placeholder 与《周报填写标准》第三问说明已同步

### 使用的技术栈
FastAPI、SQLAlchemy、pytest、Vue3、MiniMax（可选，失败走规则）

### 修改了哪些文件
- `backend/app/models/__init__.py`
- `backend/app/core/migrations.py`
- `backend/app/services/report_next_plan.py`（新）
- `backend/app/api/progress_reports.py`
- `backend/app/schemas/__init__.py`
- `backend/tests/test_report_next_plan.py`（新）
- `web-pc/src/views/ProgressReportView.vue`
- `web-pc/src/views/PersonalView.vue`
- `docs/周报填写标准.md`
- `README.md`
- `web-pc/dist/*`


---

## 会话总结（2026-09-13 — 下周计划待办同步管理员孙振振）

### 会话的主要目的
周报提交生成的「下周计划」待办，除作者本人外，同步一份到管理员孙振振，便于催办与总览。

### 完成的主要任务
- 同步逻辑增加管理员镜像：负责人=`username=admin`（孙振振）
- 镜像标题带【作者姓名】；类型为交办（assigned）；作者本人即管理员时不重复建
- 提交提示条数仍只计作者侧；同周再提交两边都不翻倍
- 单测覆盖管理员镜像；后端重启；PC 前端已 build

### 关键决策和解决方案
- 用管理员账号 `admin`，不用钉钉员工号同名账号，避免分叉
- 按 (assignee_id, source_ref) 幂等更新，同一周报可同时存在作者/管理员两条

### 使用的技术栈
FastAPI、SQLAlchemy、pytest、Vue3

### 修改了哪些文件
- `backend/app/services/report_next_plan.py`
- `backend/tests/test_report_next_plan.py`
- `web-pc/src/views/PersonalView.vue`
- `README.md`
- `web-pc/dist/*`


---

## 会话总结（2026-09-13 — 部门待办汇总 + 个人待办）

### 会话的主要目的
管理员 / 部门负责人在个人工作台看「部门待办」（成员未完成待办汇总）；自己建的任务仍在「个人待办」。取消给管理员复制镜像任务。

### 完成的主要任务
- 去掉周报下周计划同步时给 admin 的镜像 Task
- workspace API：`my_tasks`（个人）+ `department_tasks` + `can_view_department_todos`
- 可见角色：`admin`、`dept_head`（团队管理员按部门负责人）
- PC：个人工作台分区展示；部门模式只读汇总、不显示自建/交办按钮
- 迁移清理历史 `assignment_type=assigned` 的 report_next 镜像
- 单测通过；前后端已重启/构建

### 关键决策和解决方案
- 无部门表时，部门范围=全部在职用户；部门待办排除查看者本人负责的，避免与个人待办重复
- 个人待办含：自建 + 交办给我的 + 我辅助的（成员必须能干活）
- 「团队管理员」映射现有角色 `dept_head`，不新增角色

### 使用的技术栈
FastAPI、SQLAlchemy、Vue3、pytest

### 修改了哪些文件
- `backend/app/services/report_next_plan.py`
- `backend/app/services/task_service.py`
- `backend/app/api/personal.py`
- `backend/app/schemas/__init__.py`
- `backend/app/core/migrations.py`
- `backend/tests/test_report_next_plan.py`
- `web-pc/src/views/PersonalView.vue`
- `web-pc/src/components/TaskAssignPanel.vue`
- `README.md`
- `web-pc/dist/*`


---

## 会话总结（2026-09-13 — 钉钉端首页与「我的」）

### 会话的主要目的
优化钉钉 H5 首页：管理员看部门级事项，个人看自己相关事项；底部导航增加「我的」个人信息页。

### 完成的主要任务
- 底部导航改为四 Tab：首页 / 待办 / 周报 / 我的
- 首页按权限分「管理视图」与「个人视图」
  - 管理：部门待办、周报看板摘要、交办事项、项目卡点与风险
  - 个人：我的项目、任务、风险与卡点、质量告警与任务完成
- 新建「我的」页：姓名、工号、角色、钉钉绑定、改密、退出
- 待办页同步展示「部门待办」汇总（与 PC 个人工作台对齐）
- 放开钉钉端主动进入改密页（不再一律拦回首页）

### 关键决策和解决方案
- 只改 `web-dingtalk`（正式入口）；不改小程序 / PC / 后端新接口
- 管理视图判定：`report.view.all` 或 `project.view.all`，或 `can_view_department_todos`
- 部门待办优先用 `/personal/workspace` 的 `department_tasks`；无数据时回退重点任务列表
- 角色中文映射放在 `utils/roles.ts`，便于「我的」展示

### 使用的技术栈
Vue3、Vue Router、Pinia、Axios、Vite、TypeScript

### 修改了哪些文件
- `web-dingtalk/src/views/MainLayout.vue`
- `web-dingtalk/src/router/index.ts`
- `web-dingtalk/src/stores/auth.ts`
- `web-dingtalk/src/utils/roles.ts`（新建）
- `web-dingtalk/src/views/HomeView.vue`
- `web-dingtalk/src/views/ProfileView.vue`（新建）
- `web-dingtalk/src/views/TodoView.vue`
- `web-dingtalk/src/views/PasswordView.vue`
- `web-dingtalk/src/styles/theme.css`
- `web-dingtalk/dist/*`（构建产物）
- `README.md`

---

## 会话总结（2026-09-13 — 删除测试数据）

### 会话的主要目的
清理库内自动化测试产生的假账号、假项目与相关任务；并固化「测试完成后必须删除测试数据」的习惯。

### 完成的主要任务
- 新增可重复使用的清理脚本 `scripts/purge_test_data.py`（支持 `--dry-run`）
- 已删除测试账号 **13** 个（`t_*` / `ta_*`）
- 已删除测试项目 **7** 个（`下周计划测试项目`、`proj_*`）及关联任务 6、任务流水 18、成员关系 16
- 保留：`admin`、钉钉同步的真实员工账号（如 FY*）、真实业务项目

### 关键决策和解决方案
- 只删明显测试数据（用户名前缀 / 项目名含「测试」或 `proj_`），不动正式业务
- 测试项目关联的周报卡片仅解除 `project_id`，不删真实周报正文
- **工作习惯（请记住）**：联调/冒烟/E2E 造完数据，验收通过后必须跑清理或手工删掉；正式员工与真实项目禁止误删。当前环境 Privacy Mode 无法写入 Cursor 用户规则，故写在 README 备忘。

### 使用的技术栈
Python、SQLAlchemy、既有 `user_sync._detach_and_delete_users`

### 修改了哪些文件
- `scripts/purge_test_data.py`（新建）
- 数据库：测试用户 / 测试项目 / 任务及相关表
- `README.md`

---

## 会话总结（2026-09-13 — 钉钉首页卡片点击进详情）

### 会话的主要目的
钉钉 H5 首页每一张业务卡片可点击，进入对应列表/详情页查看完整信息。

### 完成的主要任务
- 新建项目列表页 `/projects`、项目详情页 `/projects/:id`
- 顶部统计卡（项目/卡点/任务完成/周报/质量告警等）全部可点跳转
- 周报摘要卡、质量告警与完成卡整卡可点
- 项目/风险/卡点列表行进入项目详情；任务行进入任务详情
- 登记卡点支持从项目详情带入 `project_id`
- 构建通过

### 关键决策和解决方案
- 不改后端，复用 `GET /projects`、`GET /projects/{id}`
- 卡点概览进 `?filter=blocker` 过滤列表，再进项目详情看卡点全文
- 质量告警：有已升级卡点优先进待办，否则进周报看板/写周报

### 使用的技术栈
Vue3、Vue Router、Axios、Vite

### 修改了哪些文件
- `web-dingtalk/src/views/ProjectListView.vue`（新）
- `web-dingtalk/src/views/ProjectDetailView.vue`（新）
- `web-dingtalk/src/views/HomeView.vue`
- `web-dingtalk/src/views/BlockerView.vue`
- `web-dingtalk/src/views/MainLayout.vue`
- `web-dingtalk/src/router/index.ts`
- `web-dingtalk/dist/*`
- `README.md`

---

## 会话总结（2026-09-13 — 钉钉首页快捷入口排版）

### 会话的主要目的
优化钉钉 H5 首页快捷入口：不要竖排长条，文案简化，并增加自建任务入口。

### 完成的主要任务
- 快捷入口改为两行三列宫格：我的待办 / 周报 / 写周报 / 写任务 / 项目列表 / 登记卡点
- 「周报看板（全员）」改名为「周报」，全员可见（有权限进看板，否则进写周报）
- 新增「写任务」→ `/tasks/new?mode=self`
- H5 构建通过

### 关键决策和解决方案
- 用 CSS Grid `repeat(3, 1fr)`，主色留给「我的待办」，其余 ghost 灰底

### 使用的技术栈
Vue3、CSS Grid、Vite

### 修改了哪些文件
- `web-dingtalk/src/views/HomeView.vue`
- `web-dingtalk/dist/*`
- `README.md`

---

## 会话总结（2026-09-13 — 钉钉端风格统一）

### 会话的主要目的
统一钉钉 H5 视觉风格：周报相关页从蓝色 Ant Design 风改为与全局一致的薄荷绿主题。

### 完成的主要任务
- `theme.css` 补充实心主色按钮 `.btn-solid`、全局 `.tip` / `.link-text`
- `ReportView` / `ReportBoardView` / `ReportPersonView` 提示条、按钮、芯片、链接、输入框焦点色全部改为 `--wb-mint*` 变量
- 周报页去掉覆盖底部 Tab 的 padding 覆盖，输入框边框与首页一致
- 快捷入口宫格改动此前已完成；本次构建一并确认
- H5 构建通过

### 关键决策和解决方案
- 品牌主色统一为 mint（#5bb98c），不再混用 #1a5fb4 蓝
- 主按钮实心 mint，次要按钮用 mint 浅底或灰底

### 使用的技术栈
Vue3、CSS 变量、Vite

### 修改了哪些文件
- `web-dingtalk/src/styles/theme.css`
- `web-dingtalk/src/views/ReportView.vue`
- `web-dingtalk/src/views/ReportBoardView.vue`
- `web-dingtalk/src/views/ReportPersonView.vue`
- `web-dingtalk/dist/*`
- `README.md`

---

## 会话总结（2026-09-13 — 钉钉风格复查与修复）

### 会话的主要目的
检查钉钉 H5 风格是否统一，修复仍不一致的地方。

### 完成的主要任务
- 全局主按钮由黑底改为薄荷绿实心，与周报页一致
- theme 补充 warn/surface/label/圆角变量；输入框统一 focus 描边
- 周报看板 / 人员详情 / 写周报页去掉蓝色残留，改用 CSS 变量
- 快捷入口圆角与卡片对齐；复查构建通过
- 误覆盖后已从会话记录恢复周报看板与人员页逻辑

### 关键决策和解决方案
- 主色统一 mint；次要浅底 `btn-mint`；灰底 `btn-ghost`
- 警告色用 `--wb-warn*`，危险色用 `--wb-danger*`

### 使用的技术栈
Vue3、CSS 变量、Vite

### 修改了哪些文件
- `web-dingtalk/src/styles/theme.css`
- `web-dingtalk/src/views/ReportView.vue`
- `web-dingtalk/src/views/ReportBoardView.vue`
- `web-dingtalk/src/views/ReportPersonView.vue`
- `web-dingtalk/src/views/HomeView.vue`
- `web-dingtalk/src/views/ProjectDetailView.vue`
- `web-dingtalk/dist/*`
- `README.md`

## 会话总结（2026-09-13）— PC/钉钉风格联调与修复

### 会话的主要目的
对照检查 PC 端与钉钉 H5 的薄荷绿品牌风格是否一致，用运行时证据定位不一致点并修复。

### 完成的主要任务
- 用服务端浏览器采集计算样式（因远程客户端无法把日志打到本机 `127.0.0.1:7460`）
- 确认钉钉端主题已正确为薄荷绿；PC 主按钮曾强制黑色、周报「已交」用 Element 默认绿
- 将 PC 主按钮与成功色对齐到品牌薄荷绿，并重建 PC 前端

### 关键决策和解决方案
- **假设 A（PC 主色仍是蓝）**：拒绝 — 运行时 `--el-color-primary` / `--wb-accent` 均为 `#5bb98c`
- **假设 B（PC 黑主按钮 vs 钉钉绿主按钮）**：确认 — 修复前主按钮 `rgb(34,34,34)`，钉钉 `.btn` 为 `rgb(91,185,140)`；已把 `.el-button--primary` 改为 `var(--wb-accent)`
- **假设 C（周报已交 `#67c23a`）**：确认 — 改为 `var(--wb-accent)`，并把 `--el-color-success` 系列对齐薄荷
- **假设 D（钉钉 mint 未生效）**：确认生效 — `--wb-mint:#5bb98c`，按钮绿
- **假设 E（旧蓝缓存）**：拒绝 — 服务端已下发薄荷主题

### 使用的技术栈
Vue3、Element Plus、CSS 变量、Chrome DevTools（CDP）运行时探针、Vite 构建

### 修改了哪些文件
- `web-pc/src/styles/theme.css`
- `web-pc/src/views/ReportBoardView.vue`
- `web-pc/src/components/report-board/ReportPeopleBoard.vue`
- `web-pc/dist/*`（重建）
- `README.md`
- （调试埋点仍保留）`web-pc/src/App.vue`、`web-dingtalk/src/App.vue` 等

---

## 会话总结（2026-09-13 — 需求再次核对并落档）

### 会话的主要目的
再次核对产品需求（员工任务/周报、可见范围、大模型贯穿、管理员能力），与现状对齐并确认歧义点。

### 完成的主要任务
- 对照 PRD、RBAC、任务/周报实现，整理 7 条需求满足度
- 确认角色映射：管理员=`admin`；部门/团队管理员=`dept_head`（不新建）
- 确认任务计入周报：**方案 A**（保存任务后自动写入本周周报草稿，人可改）
- 确认评价可见性：**员工可看**对自己任务/周报的评价
- 将已确认基线写入 `docs/需求核对-已确认.md`，并回链 README / PRD

### 关键决策和解决方案
- 不拆三个管理角色；口语「团队管理员」继续映射 `dept_head`
- `project_owner` 保留，但不算「管理员」三类
- 待开发缺口优先：任务→周报草稿自动同步、本人可读周报/任务评价、任务目标字段与任务级 AI 评价补齐

### 使用的技术栈
需求文档（Markdown）、现有 FastAPI RBAC / 周报评价模型对照（本会话未改代码逻辑）

### 修改了哪些文件
- `docs/需求核对-已确认.md`（新建）
- `项目支撑工作台-PRD.md`（角色表与基线链接）
- `README.md`

---

## 会话总结（2026-09-13 — 任务进周报草稿 + 本人可读评价）

### 会话的主要目的
按已确认需求落地：任务保存自动进本周周报草稿；员工可看对自己周报/任务的评价；补强任务目标字段。

### 完成的主要任务
- 新建 `report_task_sync`：创建任务后按 `linked_task_id` 去重写入负责人本周草稿（已提交则跳过）
- 任务增加 `goal` 字段；AI 草稿可填目标
- 新建 `task_reviews` + `GET /tasks/{id}/review`（执行人/辅助人/全量管理者可读）
- 周报：`GET /mine` 附带 `phase1_review`；新增 `GET /{id}/my-review` 供本人查看
- PC/钉钉：创建任务含目标与提示；任务详情与写周报页展示评价
- 单测 `test_task_report_sync.py` 3 项通过；前后端已构建/重启

### 关键决策和解决方案
- 同步对象是任务**负责人**的本周草稿（自建=自己，交办=被指派人）
- `source=report_next` 不反向再写周报，避免与「下周计划→待办」打架
- 本周已提交则不改已交周报；评价对草稿提示「提交后可查看」

### 使用的技术栈
FastAPI、SQLAlchemy、Vue3、Element Plus、pytest

### 修改了哪些文件
- `backend/app/services/report_task_sync.py`（新建）
- `backend/app/services/task_ai_review.py`（新建）
- `backend/app/services/task_ai.py`、`ai_platform.py`、`task_service.py`
- `backend/app/api/tasks.py`、`progress_reports.py`
- `backend/app/models/__init__.py`、`schemas/__init__.py`、`core/migrations.py`
- `backend/tests/test_task_report_sync.py`（新建）
- `web-pc/.../TaskAssignPanel.vue`、`ProgressReportView.vue`、`AdminView.vue`
- `web-dingtalk/.../TaskCreateView.vue`、`TaskDetailView.vue`、`ReportView.vue`
- `docs/需求核对-已确认.md`、`README.md`
- `web-pc/dist/*`、`web-dingtalk/dist/*`

---

## 会话总结（2026-09-13 — 删除测试数据）

### 会话的主要目的
清理本轮单测/联调留下的假账号、假项目与关联任务、评价。

### 完成的主要任务
- 运行 `scripts/purge_test_data.py` 删除测试账号 **16**、测试项目 **13**
- 关联清理：任务 15、任务流水 33、任务评价 3、成员关系 21 等
- 脚本补充识别「任务周报联调-*」项目，并删除 `task_reviews`
- 用户硬删时同步清理 `TaskReview`

### 关键决策和解决方案
- 只删 `t_*` / `ta_*` / `e2e_*` 等测试账号与测试项目名；不动真实员工与业务项目
- 项目名规则增加「任务周报联调」，避免漏删本次联调项目

### 使用的技术栈
Python、SQLAlchemy、既有 `purge_test_data.py`

### 修改了哪些文件
- `scripts/purge_test_data.py`
- `backend/app/services/user_sync.py`
- `README.md`

---

## 会话总结（2026-09-13 — 统一写任务：PC+钉钉，自建/交办/辅助人/通知）

### 会话的主要目的
两端统一「写任务」：员工自建并可选辅助人；管理员可选责任人（不选=自己，选他人=交办）；交办须钉钉通知并进入责任人待办与部门待办；任务自动进周报草稿。

### 完成的主要任务
- 后端创建回执增加 `synced_to_week_draft`、`dingtalk_notified`；交办通知返回是否钉钉实发
- PC 新增 `/write-task` 页、侧栏与个人工作台入口；待办面板「写任务」跳转统一页
- 钉钉改造写任务页（取消 mode 分叉）；首页/待办入口合并为「写任务」
- 单测覆盖：自建进周报、交办进个人/部门待办并尝试钉钉通知
- 双端前端已重新构建；后端已重启

### 关键决策和解决方案
- 用 `task.assign` 控制是否显示责任人；辅助人对全体写任务用户可见
- 交办通知依赖 `DINGTALK_AGENT_ID` + 责任人 `dingtalk_user_id`；失败时任务仍创建，响应如实反馈
- 部门待办仍仅管理者可见（不向全员群发钉钉）

### 使用的技术栈
FastAPI、Vue3、Element Plus、钉钉工作通知、pytest

### 修改了哪些文件
- `backend/app/api/tasks.py`、`schemas/__init__.py`、`services/task_notify.py`
- `backend/tests/test_tasks_assign.py`、`test_task_report_sync.py`
- `web-pc/src/views/WriteTaskView.vue`（新建）
- `web-pc/src/router/index.ts`、`layouts/MainLayout.vue`、`views/PersonalView.vue`、`components/TaskAssignPanel.vue`
- `web-dingtalk/src/views/TaskCreateView.vue`、`HomeView.vue`、`TodoView.vue`
- `web-pc/dist/*`、`web-dingtalk/dist/*`、`README.md`

---

## 会话总结（2026-09-13 — 优化 PC 端个人工作台）

### 会话的主要目的
优化 PC 端个人工作台：嵌入精简项目总览、按权限展示模块快捷入口、展示个人任务/周报/项目/待办概览；完整「项目总览」页保留在侧栏。

### 完成的主要任务
- 后端 `GET /personal/workspace` 增加 `summary`：任务总数/完成/逾期、本周周报状态、项目数、待办数等
- 重构 PC `PersonalView`：顶部精简项目总览（复用 overview/stats）+ 权限快捷入口 + 我的概览卡片 + 原有待办/卡点/项目列表
- 补充单测 `test_personal_workspace_includes_summary`

### 关键决策和解决方案
- 采用方案 2：个人工作台嵌入精简总览，侧栏完整「项目总览」保留
- 快捷入口按权限过滤（周报工作台 / 方案库 / 系统管理；部门待办看 can_view_department_todos）
- 个人待办数 = 未完成任务 + 未解决卡点

### 使用的技术栈
FastAPI、Pydantic、Vue3、Element Plus、pytest

### 修改了哪些文件
- `backend/app/api/personal.py`
- `backend/app/schemas/__init__.py`
- `backend/tests/test_tasks_assign.py`
- `web-pc/src/views/PersonalView.vue`
- `README.md`

---

## 会话总结（2026-09-13 — 员工快捷能力：补齐看评价）

### 会话的主要目的
核对员工写任务/看待办/写周报/看评价；前三项已具备，补齐「看评价」独立入口与聚合页（PC + 钉钉）。

### 完成的主要任务
- 新增 `GET /personal/reviews`：聚合本人最近周报评价 + 负责任务/辅助任务评价
- PC：`/my-reviews` 页、侧栏「看评价」、个人工作台快捷入口
- 钉钉：`/reviews` 页、首页宫格「看评价」
- 单测覆盖个人评价聚合可读；双端已构建，后端已重启

### 关键决策和解决方案
- 只读已有评价，不强制重评；无评价时空态提示先提交周报/完成任务
- 不新增权限码，登录用户均可看本人评价

### 使用的技术栈
FastAPI、Vue3、Element Plus、pytest

### 修改了哪些文件
- `backend/app/api/personal.py`、`schemas/__init__.py`
- `backend/tests/test_task_report_sync.py`
- `web-pc/src/views/MyReviewsView.vue`（新建）
- `web-pc/src/router/index.ts`、`layouts/MainLayout.vue`、`views/PersonalView.vue`
- `web-dingtalk/src/views/ReviewsView.vue`（新建）
- `web-dingtalk/src/router/index.ts`、`views/HomeView.vue`
- `web-pc/dist/*`、`web-dingtalk/dist/*`、`README.md`

---

## 会话总结（2026-09-13 — 删除测试数据）

### 会话的主要目的
清理本轮单测留下的假账号与假项目，保留真实员工与业务数据。

### 完成的主要任务
- 运行 `scripts/purge_test_data.py`：删除测试账号 **6**、测试项目 **4**
- 关联清理：任务 5、任务流水 7、任务评价 1、成员关系 6、周报条目解绑 5 等

### 关键决策和解决方案
- 仅删 `t_*` / `ta_*` 等测试账号与 `proj_*` /「任务周报联调」项目；不动真实业务数据

### 使用的技术栈
Python、既有 `purge_test_data.py`

### 修改了哪些文件
- 无代码变更（仅数据库清理）
- `README.md`


---

## 会话总结（2026-09-14 — 对外入口改为 nginx:8080）

### 会话的主要目的
避免端口冲突；澄清需求后：后端端口不动，前端走 nginx，将 nginx 对外监听从 80 改为 8080。

### 完成的主要任务
- 撤回误改的后端 8100→8080 配置，后端仍为 `127.0.0.1:8100`
- 将 nginx `listen 80` 改为 `listen 8080` 并 `systemctl restart nginx`
- 验证：`http://127.0.0.1:8080/workbench/` 与 `/workbench/api/` 返回 200；80 端口已关闭

### 关键决策和解决方案
- 前端静态页与 `/workbench/api/` 均由 nginx 托管/反代，改对外端口即可
- 同机 talent-profile / learn-flow 也共用该 nginx，访问入口一并变为 `:8080`
- 钉钉开放平台回调/移动端首页需同步带上 `:8080`

### 使用的技术栈
nginx、systemd、curl、ss

### 修改了哪些文件
- `/etc/nginx/sites-enabled/talent-profile.conf`（listen 8080）
- `/etc/nginx/sites-available/talent-profile.conf`（listen 8080）
- `scripts/install-nginx.sh`
- `PROJECT_ARCHITECTURE.md`
- `README.md`
- （中间曾改过后端相关文件，已全部撤回为 8100）

---

## 会话总结（2026-09-14 — nginx 取消映射人才画像）

### 会话的主要目的
同机人才画像进程不要再经 nginx 对外映射；工作台继续走 nginx:8080。

### 完成的主要任务
- 重写 nginx 站点：仅保留 `/workbench/`、`/learnflow/`；去掉人才画像静态根、`/api/`、`/h5/` 等反代
- 根路径 `/` 302 跳转到 `/workbench/`
- 验证：工作台 200；`/api/`、`/assets/` 经 nginx 为 404；人才画像直连 `:3000` 仍 200

### 关键决策和解决方案
- 人才画像继续用自身 Node 进程（端口 3000）直连访问，不再挂在 nginx
- nginx:8080 专供工作台（及学习流路径）；旧站点配置已备份到 `/etc/nginx/backup/`

### 使用的技术栈
nginx、systemd、curl

### 修改了哪些文件
- `deploy/nginx/site-8080.conf`（新建）
- `scripts/install-nginx.sh`
- `/etc/nginx/sites-enabled/talent-profile.conf`（内容已替换为工作台站点）
- `PROJECT_ARCHITECTURE.md`、`README.md`

---

## 会话总结（2026-09-14 — 工作台回 80，解绑 learnflow）

### 会话的主要目的
工作台对外改回 nginx:80；学习流从 nginx 解除映射。

### 完成的主要任务
- nginx 监听改回 `80`，仅 `include workbench.conf`
- 移除 `learnflow.conf` 引用；`/learnflow/` 经 nginx 返回 404
- 验证：工作台 `/workbench/` 与 API 200；学习流直连 `:7000` 200；人才画像直连 `:3000` 200；`:8080` 已关闭

### 关键决策和解决方案
- nginx:80 专供工作台；人才画像 `:3000`、学习流 `:7000` 均直连各自进程
- 配置模板更名为 `deploy/nginx/site-80.conf`

### 使用的技术栈
nginx、systemd、curl

### 修改了哪些文件
- `deploy/nginx/site-80.conf`（由 site-8080 更名并改 listen 80）
- `scripts/install-nginx.sh`
- `/etc/nginx/sites-enabled/talent-profile.conf`
- `PROJECT_ARCHITECTURE.md`、`README.md`

---

## 会话总结（2026-09-14 — 放宽钉钉扫码同源，适配 VPN）

### 会话的主要目的
排查并修复 VPN 访问登录页时，钉钉扫码提示「redirect_uri 必须与登录页同源」导致二维码无法展示的问题。

### 完成的主要任务
- 去掉前端强制同源拦截：不同源仅 console.warn，仍正常挂载 DTFrameLogin
- 登录页接受本页源或本次 `redirect_uri` 源的 postMessage
- 回调页 `postMessage` 改为 `'*'`，保证跨源可投递（鉴权仍靠 state + 后端兑码）
- 重新构建 `web-pc` dist，并重启 `workbench-backend`

### 关键决策和解决方案
- VPN/内网穿透场景下登录页与回调地址可以不同源；安全靠 OAuth `state` 与后端兑码，不靠浏览器同源硬拦
- 不改钉钉开放平台白名单规则：`redirect_uri` 仍须与白名单一字不差

### 使用的技术栈
Vue 3、DTFrameLogin、FastAPI、systemd

### 修改了哪些文件
- `web-pc/src/composables/dingtalkQrLogin.ts`
- `web-pc/src/views/LoginView.vue`
- `web-pc/dist/*`（重新构建）
- `backend/app/api/auth.py`
- `README.md`

---

## 会话总结（2026-09-14 — 修复 VPN 扫码无跳转）

### 会话的主要目的
白名单已配置，但扫码后不跳转：控制台显示 `redirect_uri=http://内网IP`，登录页为 `https://域名:18090`。

### 完成的主要任务
- 根因：反代后 Host/`$scheme` 变成内网 HTTP，HTTPS 页无法加载 HTTP 回调（混合内容）
- 新增 `DINGTALK_OAUTH_PUBLIC_ORIGIN`，固定对外回调源
- `_build_redirect_uri` 优先：公开源 → Origin/Referer → Host
- nginx：`Host $http_host`（保留端口）、透传 `X-Forwarded-Proto`
- 补充单测 3 条并通过；重启后端与 reload nginx

### 关键决策和解决方案
- 开放平台白名单保留完整回调：`https://app.fywasu.com:18090/workbench/api/auth/dingtalk-callback?embed=1`
- 若开放平台提示「版本发布后才生效」，需在开发者后台发布后再测

### 使用的技术栈
FastAPI、nginx、pytest、systemd

### 修改了哪些文件
- `backend/app/api/auth.py`
- `backend/app/core/config.py`
- `backend/.env` / `.env.example`
- `backend/tests/test_dingtalk_auth.py`
- `deploy/nginx/workbench.conf`、`/etc/nginx/snippets/workbench.conf`
- `README.md`

---

## 会话总结（2026-09-14 — 项目管理页优化）

### 会话的主要目的
将「项目总览」改为「项目管理」：成员可看全部项目；仅负责人与管理员可改；项目类型收敛为三类并精准识别。

### 完成的主要任务
- 侧栏/页标题/个人台文案：「项目总览」→「项目管理」（路由仍为 `/overview`）
- `member` / `project_owner` 增加 `project.view.all`；需求基线同步为员工可看全部项目
- `PATCH /projects/{id}`：仅项目负责人（`owner_id`）或管理员（`admin`/`dept_head`）可改；列表提供「编辑」弹窗改名称与类型
- 项目类型收敛为 `software`（软件开发）/ `integration`（系统集成）/ `fusion`（集众融合）；智安校园 → fusion；存量类型迁移

### 关键决策和解决方案
- 看全部 ≠ 改全部：普通成员只读全部项目，改自己负责的；管理员可改任意
- 类型优先级：fusion > integration > software；旧 ops/工程/采购/硬件 → integration，operation → fusion

### 使用的技术栈
FastAPI、SQLAlchemy、Vue3、Element Plus、pytest

### 修改了哪些文件
- `backend/app/rbac/permissions.py`、`deps.py`、`services/init_service.py`
- `backend/app/models/enums.py`、`services/project_match.py`、`services/assist_ai.py`
- `backend/app/api/projects.py`、`core/migrations.py`
- `backend/tests/test_project_reclassify.py`、`test_project_visibility_edit.py`（新）
- `backend/scripts/reassign_onsite_projects.py`
- `web-pc/src/constants/projectMeta.ts`、`stores/user.ts`
- `web-pc/src/layouts/MainLayout.vue`、`views/OverviewView.vue`、`ProjectDetailView.vue`、`PersonalView.vue`
- `docs/需求核对-已确认.md`、`README.md`

---

## 会话总结（2026-09-14 — PC 个人工作台作为首页）

### 会话的主要目的
PC 端默认进入个人工作台；「今天该看什么」迁入个人页；按「以我为主」重构个人工作台界面。

### 完成的主要任务
- 侧栏「个人工作台」置顶；根路径/登录/改密后默认进入 `/personal`
- 「今天该看什么」（`/ai/overview/brief`）从项目管理移到个人工作台顶部
- 个人页去掉团队项目精简与入口宫格；保留我的概览、少量常用按钮、与我相关的待办/卡点/项目；部门待办标明「团队」
- PC 前端已构建

### 关键决策和解决方案
- 方案 A：首页以「我」为主，打开就知道今天干什么
- 项目管理页专注列表与统计，不再展示 AI 今日摘要

### 使用的技术栈
Vue3、Element Plus、Vue Router

### 修改了哪些文件
- `web-pc/src/layouts/MainLayout.vue`
- `web-pc/src/router/index.ts`
- `web-pc/src/views/LoginView.vue`、`ChangePasswordView.vue`
- `web-pc/src/views/PersonalView.vue`、`OverviewView.vue`
- `README.md`


## 会话总结（2026-09-14 — 钉钉端周报入口改造）

### 会话的主要目的
理顺钉钉 H5 周报相关入口：看自己的历史周报、写周报、看评价、底部导航各自职责清晰。

### 完成的主要任务
- 新增「我的周报」页（`/my-reports`）：历史周期 + 正文 + 评价 +「写周报」按钮
- 首页宫格「周报」改名为「看周报」，进入我的周报；「写周报」仍直达写报页；移除宫格「看评价」
- 底部「周报」Tab 固定进入我的周报（不再分流到全员看板或写报页）
- 「看评价」入口移到「我的」页

### 关键决策和解决方案
- 历史页标题定为「我的周报」；底部 Tab 文案仍为「周报」
- 全员看板保留：有权限者仍可通过首页「进入看板」、写周报页内按钮进入
- 复用现有接口 `/progress-reports/my-periods` 与 `/progress-reports/mine`，不改后端

### 使用的技术栈
Vue3、Vue Router、TypeScript、钉钉 H5（web-dingtalk）

### 修改了哪些文件
- `web-dingtalk/src/views/MyReportsView.vue`（新增）
- `web-dingtalk/src/router/index.ts`
- `web-dingtalk/src/views/HomeView.vue`
- `web-dingtalk/src/views/MainLayout.vue`
- `web-dingtalk/src/views/ProfileView.vue`
- `README.md`


## 会话总结（2026-09-14 — 钉钉 H5 重新构建发布）

### 会话的主要目的
用户反馈钉钉端页面未变更；确认源码已改但未构建上线，需重新 build 发布。

### 完成的主要任务
- 在 `web-dingtalk` 执行 `npm run build`，生成含「看周报 / 我的周报 /my-reports」的新静态包
- 重启 `workbench-frontend`，nginx 已指向新 `index-CYzmKSUq.js` 等资源

### 关键决策和解决方案
- 问题根因是只改了源码未发布 dist；修复方式为构建 + 重启前端服务
- 请用户在钉钉内强制刷新或清缓存后再打开 `/workbench/m/`

### 使用的技术栈
Vite、npm、systemd、nginx

### 修改了哪些文件
- `web-dingtalk/dist/**`（构建产物）
- `README.md`


## 会话总结（2026-09-14 — 删除测试数据）

### 会话的主要目的
清理库内自动化测试留下的假项目，避免污染正式业务数据。

### 完成的主要任务
- 先 `--dry-run` 预览，再执行 `scripts/purge_test_data.py`
- 删除测试项目 **2** 个（`proj_b93e59c5`、`proj_2a9e0d7c`）及成员关系 2 条
- 无测试账号需删；正式员工与真实项目未动

### 关键决策和解决方案
- 沿用既有清理脚本：仅按用户名前缀 / 项目名含「测试」或 `proj_` 等规则识别

### 使用的技术栈
Python、SQLAlchemy、`purge_test_data.py`

### 修改了哪些文件
- 仅数据库数据清理（无业务代码改动）
- `README.md`


## 会话总结（2026-09-14 — 删除截图中的测试项目）

### 会话的主要目的
按用户截图删除未命中自动规则的测试项目。

### 完成的主要任务
- 删除 4 个项目：`管理员改名`、`负责人改名`、`可见项目_ff3aea`、`可见项目_b83e24`（及成员关系 4 条）
- 扩展 `scripts/purge_test_data.py`：识别 `可见项目_*` 以及「管理员改名」「负责人改名」

### 关键决策和解决方案
- 先按精确名称删除，再补规则，避免下次同类联调数据漏清

### 使用的技术栈
Python、SQLAlchemy、`purge_test_data.py`

### 修改了哪些文件
- `scripts/purge_test_data.py`
- 数据库（删除上述 4 个项目及相关成员）
- `README.md`

## 会话总结（2026-09-14 — 辅助人多选下拉 + 删除测试账号）

### 会话的主要目的
把钉钉写任务页「辅助人」从一排标签改成可多选的下拉框，并清掉列表里的测试账号。

### 完成的主要任务
- 钉钉 H5「写任务」：辅助人改为点击展开的下拉多选（勾选），展示已选姓名
- 从数据库删除联调残留测试账号 **14** 个（`pv_*`，含 `pv_bd10392ed3`、`pv_e815d7d268` 及负责人甲/路人成员等）
- 「日常任务」项目成员从 27 人减到 13 人（仅正式员工）
- 清理脚本补充识别 `pv_` 前缀，避免下次联调后再漏清
- 已重新构建钉钉 H5 前端

### 关键决策和解决方案
- 不用原生 `<select multiple>`（手机端体验差），改成「下拉触发器 + 勾选列表」，与 PC 端多选能力对齐
- 测试账号按用户名前缀 `pv_` 识别删除，不按中文显示名（避免误伤同名真实员工）

### 使用的技术栈
Vue 3、钉钉 H5（web-dingtalk）、Python、SQLAlchemy、`purge_test_data.py`

### 修改了哪些文件
- `web-dingtalk/src/views/TaskCreateView.vue`
- `web-dingtalk/dist/`（构建产物）
- `scripts/purge_test_data.py`
- 数据库（删除 14 个 `pv_*` 测试账号及关联）
- `README.md`


## 会话总结（2026-09-14 — PC 顶栏姓名菜单）

### 会话的主要目的
PC 端提供账号入口：点右上角姓名可改密码、退出，并预留今后扩展。

### 完成的主要任务
- 顶栏姓名改为下拉菜单：修改密码 / 退出 /「更多功能即将开放」（禁用预留）
- 放开自愿进入改密页；强制首登改密流程保留
- 改密页按强制/自愿切换文案与成功后跳转
- 构建并发布 `web-pc` 静态包

### 关键决策和解决方案
- 不新增侧栏「我的」页，采用顶栏姓名下拉（更符合用户指定方案）
- 自愿改密成功后优先 `router.back()`，否则回个人工作台

### 使用的技术栈
Vue3、Element Plus（el-dropdown）、Vue Router、Vite

### 修改了哪些文件
- `web-pc/src/layouts/MainLayout.vue`
- `web-pc/src/router/index.ts`
- `web-pc/src/views/ChangePasswordView.vue`
- `web-pc/dist/**`（构建产物）
- `README.md`

---

## 会话总结（2026-09-14 — 管理角色概览显示全部）

### 会话的主要目的
PC 个人工作台「我的概览」：部门负责人、超级管理员看全部口径；其他人（含项目负责人）仍只看自己。

### 完成的主要任务
- `GET /personal/workspace` 增加 `summary_scope`（`self` / `all`）
- `admin` / `dept_head` 返回全量任务、项目、卡点与团队周报提交人数
- PC「我的概览」在全量时改为「团队概览」文案：全部任务 / 团队周报 / 全部项目 / 部门待办
- 单测覆盖 member/owner=`self`、admin=`all`；构建并发布 `web-pc`

### 关键决策和解决方案
- 方案 B：同一排卡片改口径，不另加团队总览行
- 项目负责人不开放部门待办 / 全员周报（与已确认角色基线一致）
- 全量任务/周报复用 `get_overview_stats`，与项目管理页口径对齐

### 使用的技术栈
FastAPI、SQLAlchemy、Vue3、Element Plus、pytest、Vite

### 修改了哪些文件
- `backend/app/schemas/__init__.py`
- `backend/app/api/personal.py`
- `backend/tests/test_tasks_assign.py`
- `web-pc/src/views/PersonalView.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-14 — 今天该看什么流式输出）

### 会话的主要目的
PC「今天该看什么」去掉转圈等待，改为 SSE 流式显示摘要文字。

### 完成的主要任务
- 新增 `GET /ai/overview/brief/stream`（SSE：start / delta / done）
- `run_text_stream` 对接 OpenAI 兼容 `stream=true`；无模型或失败立刻规则兜底
- PC `PersonalView` 用 fetch 读流，边收边显；去掉 `v-loading` 转圈，保留闪烁光标提示
- 保留原 `GET /ai/overview/brief` 供脚本/E2E

### 关键决策和解决方案
- 真流式而非前端假打字机；流式阶段只展示第一段摘要，结束时解析 `- 关注点` 列表
- 仅改 PC，不改钉钉及其他 AI 接口

### 使用的技术栈
FastAPI StreamingResponse、httpx stream、Vue3 fetch ReadableStream、SSE

### 修改了哪些文件
- `backend/app/services/ai_platform.py`
- `backend/app/services/assist_ai.py`
- `backend/app/api/ai_assist.py`
- `web-pc/src/views/PersonalView.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-14 — 今日摘要屏蔽思考过程）

### 会话的主要目的
修复「今天该看什么」流式输出把模型英文思考 / `<think>` / 统计复述列表直接显示给用户的问题。

### 完成的主要任务
- 流式与定稿前剥离 `<think>` / `<thinking>` 等思考块（含未闭合）
- 识别英文草稿，思考阶段不推 delta（前端继续「正在整理…」）
- 过滤「可见项目/重点项目…」类统计复述 focus；无可用白话时回退规则文案
- 收紧流式 system 提示：禁止思考过程、直接输出中文正文

### 关键决策和解决方案
- 不把思考当内容上屏；宁可短暂「正在整理…」，也不展示英文分析
- 后端过滤为主，前端无需改动

### 使用的技术栈
Python 正则清洗、FastAPI SSE、现有 Vue 流式读取

### 修改了哪些文件
- `backend/app/services/assist_ai.py`
- `README.md`

---

## 会话总结（2026-09-14 — 团队概览补重点与质量告警）

### 会话的主要目的
PC 个人工作台「团队概览」补充重点项目、重点任务、质量告警，与项目管理页关键指标对齐。

### 完成的主要任务
- `PersonalSummary` 增加 `key_project_count` / `key_task_count` / `report_quality_alerts`（全量口径）
- 团队概览新增三张可点卡片：重点项目→项目管理；重点任务/质量告警→弹窗明细
- 构建 `web-pc` 并重启后端

### 关键决策和解决方案
- 仅 `summary_scope=all`（部门负责人/超管）展示；质量告警仍要求 `report.view.all`
- 明细复用既有 `/projects/overview/key-tasks` 与周报 board 接口

### 使用的技术栈
FastAPI、Vue3、Element Plus Dialog

### 修改了哪些文件
- `backend/app/schemas/__init__.py`
- `backend/app/api/personal.py`
- `web-pc/src/views/PersonalView.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-14 — 编写使用手册 HTML/PDF）

### 会话的主要目的
为项目支撑工作台编写面向员工的使用手册，覆盖 PC 端与钉钉端，输出 HTML 与 PDF。

### 完成的主要任务
- 撰写《项目支撑工作台 · 使用手册》，含登录、角色、PC/钉钉功能、场景与 FAQ
- 登录入口写明：`https://app.fywasu.com:18090/`（PC `/workbench/`，钉钉 `/workbench/m/`）
- 生成 HTML + PDF 至 `docs/manuals/`

### 关键决策和解决方案
- 文案面向非技术用户，用步骤与表格；权限差异单独说明
- PDF 由 `wkhtmltopdf` 从同一 HTML 导出，便于以后改 HTML 再出 PDF

### 使用的技术栈
HTML/CSS、wkhtmltopdf

### 修改了哪些文件
- `docs/manuals/项目支撑工作台-使用手册.html`（新增）
- `docs/manuals/项目支撑工作台-使用手册.pdf`（新增）
- `README.md`

---

## 会话总结（2026-09-14 — 写周报/写任务缺项目去创建并回选）

### 会话的主要目的
写周报或写任务时，若下拉没有对应项目，可跳转项目管理新建；创建完成后自动回到原表单并选中新项目。

### 完成的主要任务
- 项目管理页支持 `?create=1&returnTo=` 自动打开新建弹窗，创建成功按 returnTo 回跳并带上 `selectProjectId`
- 写周报「所属项目」旁增加「没有？去新建」：先存草稿再跳转，回来后自动选中新项目
- 写任务同样入口：用 sessionStorage 暂存表单，回来恢复并选中新项目
- 无 `project.create` 权限不显示入口；从侧栏直接新建仍跳项目详情

### 关键决策和解决方案
- 复用总览新建弹窗 + query 回跳协议，不新增独立创建页、不改后端 API
- 周报靠草稿接口保内容；写任务靠 `wb:write-task-draft` sessionStorage
- `returnTo` 白名单仅 `/progress-report`、`/write-task`

### 使用的技术栈
Vue3、Vue Router、Element Plus、sessionStorage

### 修改了哪些文件
- `web-pc/src/views/OverviewView.vue`
- `web-pc/src/views/ProgressReportView.vue`
- `web-pc/src/views/WriteTaskView.vue`
- `web-pc/dist/**`（已重新构建）
- `README.md`

---

## 会话总结（2026-09-15 — 交办任务形成闭环）

### 会话的主要目的
把交办任务从「派出去」补到「做完有回执、有证据、能进周报」，形成创建→接收→完成的最小闭环。

### 完成的主要任务
- 责任人完成交办任务时，站内 + 钉钉通知交办人（自建/自己交办自己不通知）
- 完成时把结果写回责任人本周周报草稿；本周已提交则写入下周草稿并打「交办完成」标签
- 任务佐证附件：上传 / 列表 / 下载 / 删除（PC + 钉钉 H5）；建议上传、不强制
- 单测覆盖通知、周报回写、附件权限；相关回归通过

### 关键决策和解决方案
- 本期不做强制「交办人验收」、自动逾期催办、申请改派（留后续）
- 附件存本机目录 `data/task_attachments/`，库表只存元数据（对齐方案库）
- 完成仍须填写结果说明；附件增强佐证

### 使用的技术栈
FastAPI、SQLAlchemy、Vue3、Element Plus、钉钉工作通知、pytest

### 修改了哪些文件
- `backend/app/services/task_notify.py`
- `backend/app/services/report_task_sync.py`
- `backend/app/services/task_attachment_storage.py`（新建）
- `backend/app/api/tasks.py`、`backend/app/api/personal.py`
- `backend/app/models/__init__.py`、`backend/app/schemas/__init__.py`
- `backend/app/core/config.py`、`backend/app/core/migrations.py`
- `backend/tests/test_task_closed_loop.py`（新建）
- `web-pc/src/components/TaskAssignPanel.vue`
- `web-dingtalk/src/views/TaskDetailView.vue`
- `README.md`

---

## 会话总结（2026-09-15 — 删除交办闭环测试数据）

### 会话的主要目的
清理 pytest 产生的交办闭环等测试账号、项目与任务，避免污染正式环境。

### 完成的主要任务
- 识别并删除用户名前缀为 `cl_` / `af_` / `ta_` / `t_` 的 45 个测试账号
- 删除关联测试项目 21 个、任务 20 条及周报等关联数据
- 正式账号 13 个全部保留（含 admin 与钉钉同步工号账号）

### 关键决策和解决方案
- 仅按明确测试用户名前缀删除，不碰正式人员
- 先删测试项目及其任务/附件，再硬删测试用户（复用改挂 admin 的清理逻辑）

### 使用的技术栈
Python、SQLAlchemy、PostgreSQL

### 修改了哪些文件
- 仅数据库数据清理，无业务代码改动
- `README.md`（本总结）

---

## 会话总结（2026-09-15 — 任务详情改为全屏页）

### 会话的主要目的
PC 端编辑任务时，把窄侧边抽屉改为独立全屏页面，方便填写结果、目标与附件。

### 完成的主要任务
- 新增路由 `/tasks/:id` 与全屏页 `TaskDetailView.vue`（左编辑、右流水；宽屏分栏）
- 支持页内编辑并保存标题/目标；结果说明加大文本框
- 任务列表点击行跳转详情页；去掉 `el-drawer`
- PC 前端已重新构建并重启

### 关键决策和解决方案
- 挂在 MainLayout 下，与「写任务」一致；返回用 `router.back()`，无历史则回个人工作台
- 保留接收/催办、级别、附件、状态、清单、改派、评价、流水等原有能力

### 使用的技术栈
Vue3、Vue Router、Element Plus

### 修改了哪些文件
- `web-pc/src/views/TaskDetailView.vue`（新建）
- `web-pc/src/router/index.ts`
- `web-pc/src/components/TaskAssignPanel.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-15 — 工作台列表区块可折叠）

### 会话的主要目的
个人工作台列表太长，让待办、卡点、项目、部门待办都能各自折叠，腾出屏幕空间。

### 完成的主要任务
- 四个区块标题左侧加箭头，点击标题可收起/展开
- 标题旁显示条数；「查看全部项目」单独可点
- 折叠状态分别写入 localStorage，刷新后仍保留

### 关键决策和解决方案
- 仅改 PC 个人工作台；各部门独立记忆键 `wb:personal-collapse:*`
- 默认展开；用 `v-show` 隐藏正文，避免反复销毁组件

### 使用的技术栈
Vue3、localStorage

### 修改了哪些文件
- `web-pc/src/views/PersonalView.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-15 — 任务目标/结果 AI 润色）

### 会话的主要目的
在任务详情里，对「目标」和「结果说明」提供 AI 自动润色，写得更清楚、更好验收。

### 完成的主要任务
- 新增接口 `POST /api/tasks/{id}/ai/polish`（field=goal|result_text），只返回建议文本，不自动保存
- PC / 钉钉任务详情增加「AI 润色目标」「AI 润色结果」按钮；确认后再点保存
- 无模型时规则兜底；管理端增加用途 `task_polish`
- 单测 3 通过；前后端已构建重启

### 关键决策和解决方案
- 润色不直接写库，避免误覆盖；结果润色仍受「先接收」门禁
- purpose=`task_polish`，失败回退 task_draft / report_structure

### 使用的技术栈
FastAPI、Vue3、Element Plus、LLM structured JSON

### 修改了哪些文件
- `backend/app/services/task_ai.py`、`backend/app/services/ai_platform.py`
- `backend/app/api/tasks.py`、`backend/app/schemas/__init__.py`
- `backend/tests/test_task_polish.py`（新建）
- `web-pc/src/views/TaskDetailView.vue`、`web-pc/src/views/AdminView.vue`
- `web-dingtalk/src/views/TaskDetailView.vue`
- `web-pc/dist/**`、`web-dingtalk/dist/**`
- `README.md`

---

## 会话总结（2026-09-19 — 创建人可修改任务与周报日报）

### 会话的主要目的
让任务、周报、日报的创建人可以修改自己建的内容；别人看别人的内容仍然不能改。

### 完成的主要任务
- 任务：创建人即使不是负责人，也可以改标题、目标、进度等（和负责人能改的范围一样）
- 周报/日报：从看板点进自己的记录时不再锁成只读，可以保存草稿或重新提交
- 补了测试：创建人改标题成功，旁观者仍被拒绝
- 已重启后端，并重新构建电脑端页面

### 关键决策和解决方案
- 只在「能不能改任务」里加一条：当前用户就是创建人就放行。交办权限、负责人、辅助人规则不动
- 还没被接收的交办任务，改进度仍要先接收
- 周报/日报不新开接口，仍用原来的保存。电脑端根据作者是不是当前登录人决定只读还是可编辑
- 管理者看别人的周报/日报继续只读

### 使用的技术栈
FastAPI、Vue3、Element Plus、pytest

### 修改了哪些文件
- `backend/app/services/task_service.py`
- `backend/tests/test_tasks_assign.py`
- `web-pc/src/views/ProgressReportView.vue`
- `web-pc/dist/**`（已重新构建）
- `README.md`

---

## 会话总结（2026-09-19 — 创建人可修改代码审计）

### 会话的主要目的
核对「任务、周报、日报创建人可修改」有没有改过头：该改的人能不能改，不该改的人会不会被拦住。

### 完成的主要任务
用真实接口跑了七项检查，结果都符合预期，没有再改代码。

### 关键决策和解决方案
- 创建人可以改已接收任务的标题（接口返回成功）
- 任务还没被接收时，创建人改进度会被拒绝，提示先接收
- 旁观者改任务会被拒绝
- 日报作者编号是数字，和登录人比对不会对错
- 旁观者看不到别人的日报；就算自己提交同一天，也不会改掉别人的内容

### 使用的技术栈
FastAPI 接口实测

### 修改了哪些文件
- 无业务代码改动
- `README.md`

---

## 会话总结（2026-09-19 — 创建者删除任务并记审计日志）

### 会话的主要目的
让任务创建人可以删除自己建的任务（包括交办给别人的），删除后审计日志仍能看出删了什么。

### 完成的主要任务
- 新增删除接口：只有创建人能删；先写审计日志，再清掉流水、辅助人、评价、附件和周报挂接
- 管理后台审计日志增加「说明」列
- 电脑端和钉钉任务详情，创建人可确认后删除
- 测试通过：执行人删会被拒绝，创建人删除后日志里有标题和「交办」

### 关键决策和解决方案
- 不把删除记在任务流水上，因为任务一删流水也没了；记在独立的审计日志里
- 负责人、辅助人不能删，只有创建人可以

### 使用的技术栈
FastAPI、Vue3、Element Plus、pytest

### 修改了哪些文件
- `backend/app/api/tasks.py`
- `backend/tests/test_tasks_assign.py`
- `web-pc/src/views/TaskDetailView.vue`
- `web-pc/src/views/AdminView.vue`
- `web-dingtalk/src/views/TaskDetailView.vue`
- `web-pc/dist/**`、`web-dingtalk/dist/**`（已重新构建）
- `README.md`

---

## 会话总结（2026-09-19 — 删除任务需输入密码）

### 会话的主要目的
删除任务前必须输入当前登录密码，避免误删。

### 完成的主要任务
- 删除接口增加密码校验：密码不对就拒绝，任务还在
- 电脑端弹出密码框；钉钉端要填密码再确认
- 测试：密码错误不删；密码正确才删；执行人即使密码对也不能删

### 关键决策和解决方案
- 先确认是创建人，再验密码。密码不会写入审计日志

### 使用的技术栈
FastAPI、bcrypt、Vue3、Element Plus

### 修改了哪些文件
- `backend/app/api/tasks.py`
- `backend/app/schemas/__init__.py`
- `backend/tests/test_tasks_assign.py`
- `web-pc/src/views/TaskDetailView.vue`
- `web-dingtalk/src/views/TaskDetailView.vue`
- `web-pc/dist/**`、`web-dingtalk/dist/**`（已重新构建）
- `README.md`

---

## 会话总结（2026-09-19 — 删除任务需输入密码）

### 会话的主要目的
删除任务前必须输入当前登录密码，避免误删。

### 完成的主要任务
- 删除接口增加密码校验：密码不对就拒绝，任务还在
- 电脑端弹出密码框；钉钉端要填密码再确认
- 测试：密码错误不删；密码正确才删；执行人即使密码对也不能删

### 关键决策和解决方案
- 先确认是创建人，再验密码。密码不会写入审计日志

### 使用的技术栈
FastAPI、bcrypt、Vue3、Element Plus

### 修改了哪些文件
- `backend/app/api/tasks.py`
- `backend/app/schemas/__init__.py`
- `backend/tests/test_tasks_assign.py`
- `web-pc/src/views/TaskDetailView.vue`
- `web-dingtalk/src/views/TaskDetailView.vue`
- `web-pc/dist/**`、`web-dingtalk/dist/**`（已重新构建）
- `README.md`

---

## 会话总结（2026-09-19 — 删除测试数据）

### 会话的主要目的
清理项目列表中的联调假项目（截图中的 `proj_*` 等），避免污染正式库。

### 完成的主要任务
- 先 `--dry-run` 预览，再执行 `scripts/purge_test_data.py`
- 删除测试账号 **17** 个（全部 `ta_*`）
- 删除测试项目 **6** 个（`proj_f2a859f3`、`proj_ea9872b8`、`proj_5e0a5e71`、`proj_05a24d3e`、`proj_dedde948`、`我的测试`）
- 级联清理：任务 4、任务流水 2、成员关系 30、阶段 9；周报条目解绑 3
- 再次 dry-run 确认：待删列表为空

### 关键决策和解决方案
- 沿用既有清理脚本：按用户名前缀 / 项目名含「测试」或 `proj_` 等规则识别，不动真实业务数据

### 使用的技术栈
Python、SQLAlchemy、`purge_test_data.py`

### 修改了哪些文件
- `README.md`（仅追加本总结；业务代码未改）

---

## 会话总结（2026-09-19 — 周报详情改为整页）

### 会话的主要目的
电脑端周报工作台点开某人时，不再用右侧抽屉，改成独立整页查看。

### 完成的主要任务
- 新增整页「人员周报/日报详情」，内容与原抽屉一致
- 工作台点人跳转到 `/report-board/person/:userId`
- 旧链接带 `?user=` 会自动转到新整页
- 删除抽屉组件并重新构建电脑端

### 关键决策和解决方案
- 地址带上周期与周报/日报类型，返回工作台时仍能回到原来的周期
- 钉钉端本来就是整页，本次不改

### 使用的技术栈
Vue3、Vue Router、Element Plus

### 修改了哪些文件
- `web-pc/src/views/ReportPersonView.vue`（新建）
- `web-pc/src/views/ReportBoardView.vue`
- `web-pc/src/router/index.ts`
- `web-pc/src/components/report-board/ReportPersonDrawer.vue`（已删除）
- `web-pc/dist/**`
- `README.md`


---

## 会话总结（2026-09-19 — 事件链动作改成中文）

### 会话的主要目的
事件链里「从周报创建/更新」显示成英文 key，改成中文方便阅读。

### 完成的主要任务
- 为 `created_from_report` →「从周报创建」、`updated_from_report` →「从周报更新」补中文映射
- PC 事件链组件、钉钉人员页事件链、两端任务详情事件文案同步补全
- 已重新构建 `web-pc`、`web-dingtalk` 静态资源

### 关键决策和解决方案
- 只改前端展示映射，数据库仍存英文 action，不改后端

### 使用的技术栈
Vue 3

### 修改了哪些文件
- `web-pc/src/components/report-board/ReportEventChain.vue`
- `web-pc/src/views/TaskDetailView.vue`
- `web-dingtalk/src/views/ReportPersonView.vue`
- `web-dingtalk/src/views/TaskDetailView.vue`
- `web-pc/dist/**`、`web-dingtalk/dist/**`（已重新构建）
- `README.md`

---

## 会话总结（2026-09-19 — 事件时间显示为正常时间）

### 会话的主要目的
把 `2026-09-18T02:05:11.413567Z` 这类 UTC 时间改成正常人看得懂的北京时间。

### 完成的主要任务
- 新增 `formatDateTime`：统一显示为 `YYYY-MM-DD HH:mm:ss`（Asia/Shanghai）
- 用于 PC 事件链、PC/钉钉任务操作流水
- 已重新构建两端静态资源

### 关键决策和解决方案
- 按北京时间展示；不改后端存库格式

### 使用的技术栈
Vue 3、Intl.DateTimeFormat

### 修改了哪些文件
- `web-pc/src/utils/datetime.ts`（新建）
- `web-dingtalk/src/utils/datetime.ts`（新建）
- `web-pc/src/components/report-board/ReportEventChain.vue`
- `web-pc/src/views/TaskDetailView.vue`
- `web-dingtalk/src/views/TaskDetailView.vue`
- `web-pc/dist/**`、`web-dingtalk/dist/**`
- `README.md`

---

## 会话总结（2026-09-19 — 周报日报与任务附件上传）

### 会话的主要目的
周报、日报支持上传证明材料（挂在整份报告上）；任务完成时若无附件给出可选提醒。不强制必须有附件才能提交或完成。

### 完成的主要任务
- 新增报告附件表与迁移、本地存储服务、CRUD 接口；`ProgressReportOut` 带回附件列表
- PC / 钉钉写周报日报页：上传、删除、下载；无报告编号时先自动存草稿再上传
- 人员详情页只读展示并可下载附件
- 任务详情点「完成」且尚无附件时弹一次提醒，可跳过
- 接口测试通过；两端前端已构建

### 关键决策和解决方案
- 附件挂在整份报告上，不挂在单条卡片（保存会重建卡片）
- 作者可上传/删除；作者或有 `report.view.all` 可列表/下载
- 复用任务附件同类校验（PDF / Office / 图片 / 文本，单文件约 20MB）
- 任务附件接口不重做，仅加完成时可选提醒

### 使用的技术栈
FastAPI、SQLAlchemy、PostgreSQL、Vue 3、Element Plus、pytest

### 修改了哪些文件
- `backend/app/models/`（报告附件模型）
- `backend/app/core/migrations.py`、`config.py`
- `backend/app/schemas/`、`backend/app/api/progress_reports.py`
- `backend/app/services/report_attachment_storage.py`（新建）
- `backend/tests/test_report_attachments.py`（新建）
- `web-pc/src/views/ProgressReportView.vue`、`ReportPersonView.vue`、`TaskDetailView.vue`
- `web-dingtalk/src/views/ReportView.vue`、`ReportPersonView.vue`、`TaskDetailView.vue`
- `web-pc/dist/**`、`web-dingtalk/dist/**`
- `README.md`

---

## 会话总结（2026-09-19 — 附件区文案调整）

### 会话的主要目的
把附件区域的描述改成「附件上传」，去掉「建议上传，不强制」。

### 完成的主要任务
- 任务详情、写周报/日报、人员报告页相关标题统一为「附件上传」
- 删除「建议上传，不强制」提示文案
- 重新构建电脑端与钉钉前端

### 关键决策和解决方案
- 仅改展示文案，上传逻辑与「可跳过」完成提醒不变

### 使用的技术栈
Vue 3

### 修改了哪些文件
- `web-pc/src/views/TaskDetailView.vue`、`ProgressReportView.vue`、`ReportPersonView.vue`
- `web-dingtalk/src/views/TaskDetailView.vue`、`ReportView.vue`、`ReportPersonView.vue`
- `web-pc/dist/**`、`web-dingtalk/dist/**`
- `README.md`

---

## 会话总结（2026-09-19 — PC 任务管理导航）

### 会话的主要目的
电脑端增加一级「任务管理」，把「写任务」收进去；列表只显示和自己有关的任务。

### 完成的主要任务
- 侧栏「写任务」改为「任务管理」，下面是「任务列表」和「写任务」
- 新接口只返回自己创建的、自己负责的、自己当辅助人的任务
- 任务管理页可切换未完成 / 已完成 / 全部；我派给别人的任务显示为「创建人」
- 接口测试通过；电脑端已重新构建

### 关键决策和解决方案
- 个人工作台和项目详情的任务列表不改，避免和部门待办混在一起
- 「写任务」仍用原来的地址，草稿和从项目管理返回不受影响
- 新接口写在「按编号查任务」前面，避免被当成任务编号

### 使用的技术栈
Vue 3、Element Plus、FastAPI、SQLAlchemy、pytest

### 修改了哪些文件
- `web-pc/src/layouts/MainLayout.vue`
- `web-pc/src/router/index.ts`
- `web-pc/src/views/TaskManageView.vue`（新建）
- `web-pc/src/components/TaskAssignPanel.vue`
- `backend/app/services/task_service.py`
- `backend/app/api/tasks.py`
- `backend/tests/test_tasks_assign.py`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-19 — 任务管理菜单可折叠）

### 会话的主要目的
让侧栏「任务列表」「写任务」可以收起来，侧栏不那么占地方。

### 完成的主要任务
- 「任务管理」右侧增加折叠箭头：收起后只留一级菜单，再点箭头展开
- 折叠状态记在浏览器里，刷新后还保持
- 点「任务管理」文字仍进入任务列表

### 关键决策和解决方案
- 折叠按钮和菜单文字分开，避免一点就既跳转又收起
- 默认展开，和原来的用法一致

### 使用的技术栈
Vue 3

### 修改了哪些文件
- `web-pc/src/layouts/MainLayout.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-19 — 管理员可看全部任务）

### 会话的主要目的
系统管理员和部门负责人在任务管理里能看到全部任务，不只看和自己有关的。

### 完成的主要任务
- 这两个角色打开任务列表时，不再按「创建人 / 负责人 / 辅助人」过滤
- 其他人仍只看和自己有关的任务
- 页面说明会区分：管理者写「可以查看全部任务」
- 接口测试通过；电脑端已重新构建

### 关键决策和解决方案
- 沿用现有角色判断：系统管理员（admin）、部门负责人（dept_head）
- 项目负责人和普通成员不放开
- 未完成 / 已完成 / 全部这组筛选仍然有效

### 使用的技术栈
Vue 3、FastAPI、SQLAlchemy、pytest

### 修改了哪些文件
- `backend/app/services/task_service.py`
- `backend/app/api/tasks.py`
- `backend/tests/test_tasks_assign.py`
- `web-pc/src/views/TaskManageView.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-20 — 项目角色按人和岗位）

### 会话的主要目的
项目成员里，项目经理和项目支撑都必填，并且按指定的人和钉钉岗位限制人选。

### 完成的主要任务
- 用户表增加岗位；同步钉钉通讯录时写入岗位名称
- 项目经理只能选指定的两个人；项目支撑只能选岗位为「技术经理」的人，并排除指定管理员
- 辅助人仍可多选、可不填；普通成员不再出现在分配表单
- 接口测试通过；电脑端已重新构建

### 关键决策和解决方案
- 项目经理仍用原来的负责人角色码，交办和卡点确认权限不变
- 项目支撑是新角色；老项目能看，下次保存必须补上项目支撑
- 已有账号要再同步一次钉钉，项目支撑名单才会出现

### 使用的技术栈
Vue 3、Element Plus、FastAPI、SQLAlchemy、PostgreSQL、pytest

### 修改了哪些文件
- `backend/app/models/__init__.py`、`backend/app/core/migrations.py`
- `backend/app/services/user_sync.py`、`backend/app/services/project_roles.py`（新建）
- `backend/app/schemas/__init__.py`、`backend/app/api/projects.py`、`backend/app/rbac/deps.py`
- `backend/tests/test_project_members.py`（新建）、`backend/tests/test_progress_reports.py`
- `web-pc/src/views/ProjectDetailView.vue`
- `web-pc/src/components/report-board/ReportProjectBoard.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-20 — 成员角色下拉去重并补齐项目支撑）

### 会话的主要目的
修好成员角色里两个下拉框：项目经理选项重复，项目支撑选不到人。

### 完成的主要任务
- 项目经理下拉去掉测试账号，同名只保留一个
- 从钉钉补回已有账号的岗位（只更新岗位，不删人）
- 项目支撑按钉钉真实岗位「政企技术经理」匹配，含驻点写法
- 电脑端已重新构建，接口已重启，并在页面上核对过两个下拉

### 关键决策和解决方案
- 钉钉里的岗位名称不是单独三个字「技术经理」，而是「政企技术经理」，所以按“名称里包含技术经理”来认
- 测试账号用户名以 pr_、ta_、t_ 开头，不进入人选名单，避免和真人重名
- 没有跑会删除人员的全量同步，只回填岗位

### 使用的技术栈
Vue 3、Element Plus、FastAPI、钉钉通讯录接口、PostgreSQL、pytest

### 修改了哪些文件
- `backend/app/services/dingtalk.py`
- `backend/app/services/user_sync.py`
- `backend/app/services/project_roles.py`
- `backend/app/api/projects.py`
- `web-pc/src/views/ProjectDetailView.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-20 — 项目支撑增加指定人选）

### 会话的主要目的
项目支撑下拉里，除了岗位为技术经理的人，再增加一位指定同事。

### 完成的主要任务
- 按姓名把这位同事加入项目支撑可选名单
- 保存时同样允许选他，不再因为岗位不是技术经理而被拦住
- 接口测试通过；电脑端已重新构建，并在页面上确认下拉能看到他

### 关键决策和解决方案
- 这位同事的钉钉岗位是「经理助理」，不改岗位规则，只加姓名例外
- 原来排除的管理员仍然不能当项目支撑

### 使用的技术栈
Vue 3、Element Plus、FastAPI、pytest

### 修改了哪些文件
- `backend/app/services/project_roles.py`
- `backend/app/api/projects.py`
- `backend/tests/test_project_members.py`
- `web-pc/src/views/ProjectDetailView.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-20 — 新建项目时选定角色并通知）

### 会话的主要目的
新建项目时就要选项目经理和项目支撑，创建完成后给这两个人发钉钉消息。

### 完成的主要任务
- 「新建立项项目」增加项目经理、项目支撑两个必填下拉，人选规则与成员角色一致
- 创建时写入这两个角色，项目负责人改为所选项目经理
- 创建成功后给两人发站内提醒和钉钉工作通知
- 补上工作通知所需的应用编号，否则钉钉发不出去
- 接口测试通过；电脑端已重新构建，并在新建弹窗里核对过两个下拉

### 关键决策和解决方案
- 项目经理、项目支撑仍按既定名单，不能选同一个人
- 通知失败不挡住项目创建，避免钉钉临时出错导致项目建不出来
- 没有真正新建项目去打扰同事，通知是否发出用测试替身确认

### 使用的技术栈
Vue 3、Element Plus、FastAPI、钉钉工作通知、pytest

### 修改了哪些文件
- `backend/app/schemas/__init__.py`
- `backend/app/api/projects.py`
- `backend/app/services/task_notify.py`
- `backend/tests/test_project_members.py`
- `backend/.env`（只增加工作通知应用编号，未改其他密钥）
- `web-pc/src/utils/projectRoles.ts`（新建）
- `web-pc/src/views/OverviewView.vue`
- `web-pc/src/views/ProjectDetailView.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-20 — 任务管理一级菜单 + Tab + 统一按钮）

### 会话的主要目的
优化电脑端任务管理：侧栏只留一级「任务管理」，页内用 Tab 切换列表与写任务；相关操作按钮统一为默认尺寸。

### 完成的主要任务
- 去掉侧栏「任务列表 / 写任务」二级折叠，仅保留一级「任务管理」
- 任务管理页增加 Tab：任务列表、写任务（需写任务权限才显示）
- 旧地址 `/write-task` 重定向到 `/tasks?tab=write`，个人工作台等入口仍可用
- 从写任务去新建项目后，回跳到写任务 Tab 并选中新项目
- 列表状态筛选、AI 生成草稿等按钮改为默认尺寸
- 电脑端已重新构建

### 关键决策和解决方案
- 用 `?tab=write` 同步 Tab，切换时用 `replace` 避免历史堆叠
- 写任务表单通过 `embedded` 嵌进 Tab，避免重复页面标题
- 新建项目回跳白名单增加 `/tasks`，并用 `returnTab` 带回写任务 Tab

### 使用的技术栈
Vue 3、Vue Router、Element Plus

### 修改了哪些文件
- `web-pc/src/layouts/MainLayout.vue`
- `web-pc/src/views/TaskManageView.vue`
- `web-pc/src/views/WriteTaskView.vue`
- `web-pc/src/router/index.ts`
- `web-pc/src/views/OverviewView.vue`
- `web-pc/src/components/TaskAssignPanel.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-20 — 管理员删除项目 + 密码校验）

### 会话的主要目的
为项目管理管理员增加「删除项目」能力，删除前须校验登录密码，防止误删。

### 完成的主要任务
- 新增权限点 `project.delete`，仅授权给系统管理员 `admin` 与部门负责人 `dept_head`
- 后端新增 `DELETE /api/projects/{id}`，请求体需带当前登录密码；校验通过后写审计日志并硬删除项目
- 级联清理成员、阶段、任务、卡点、附件等；人本周报条目仅解绑项目/任务，保留文字历史
- PC 端项目列表与项目详情增加删除按钮，交互对齐任务删除的密码输入确认
- 补充单测 4 条全部通过；运维脚本 `purge_test_data.py` 改为复用同一级联删除逻辑

### 关键决策和解决方案
- 不给普通项目负责人删除权，避免业务负责人误删全局数据
- 密码校验复用任务删除模式（`verify_password` + `ElMessageBox.prompt`）
- 内置「日常任务」项目禁止删除
- 级联删除抽到 `project_service.delete_projects_cascade`，API 与运维脚本共用，避免两套逻辑漂移

### 使用的技术栈
FastAPI、SQLAlchemy async、RBAC、Vue 3、Element Plus、pytest

### 修改了哪些文件
- `backend/app/rbac/permissions.py`
- `backend/app/services/init_service.py`
- `backend/app/schemas/__init__.py`
- `backend/app/services/project_service.py`
- `backend/app/api/projects.py`
- `backend/tests/test_project_delete.py`（新建）
- `scripts/purge_test_data.py`
- `web-pc/src/views/OverviewView.vue`
- `web-pc/src/views/ProjectDetailView.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-20 — 清理项目管理测试数据）

### 会话的主要目的
清理项目管理列表中的联调/自动化测试脏数据，保留真实业务项目。

### 完成的主要任务
- 扩展 `purge_test_data.py` 识别规则：账号增加 `pd_`，项目增加 `role_` / `新建角色_` 前缀
- 执行清理：删除测试账号 20 个、测试项目 13 个（含 `proj_del_*`、`测试`、`我的测试`、`role_*` 等）
- 真实业务项目与「日常任务」均保留；残留测试账号为 0

### 关键决策和解决方案
- 用运维脚本批量清理，避免页面上手动逐条删并输密码
- 清理规则按命名前缀与「测试」关键字匹配，不碰正式立项名

### 使用的技术栈
Python、SQLAlchemy async、既有级联删除服务

### 修改了哪些文件
- `scripts/purge_test_data.py`
- `README.md`

---

## 会话总结（2026-09-20 — 周报管理一级栏目合并）

### 会话的主要目的
将 PC 端「写周报/日报」与「周报工作台」合并为一级栏目「周报管理」，页内用看/写 Tab，并明确本人可改、管理者只看全部。

### 完成的主要任务
- 新增统一页 `ReportManageView`：看周报/日报 + 写周报/日报 Tab（query `tab=write`）
- 有 `report.view.all` 时「看」嵌入原工作台；普通成员「看」仅本人周期列表
- 「写」嵌入原填写页；他人周报 `?view=` 只读；后端仍只按当前用户保存
- 侧栏合并为「周报管理」；旧路径 `/progress-report`、`/report-board` 重定向兼容
- 同步个人工作台、项目管理、看评价、项目详情等入口文案与跳转

### 关键决策和解决方案
- 只改 PC；钉钉 H5 / 小程序不动
- 管理者可看全部、不可改他人（与任务管理 Tab 模式对齐）
- 不新增权限码，继续用 `report.submit` / `report.view.all`

### 使用的技术栈
Vue 3、Vue Router、Element Plus、既有 progress-reports API

### 修改了哪些文件
- `web-pc/src/views/ReportManageView.vue`（新建）
- `web-pc/src/views/ProgressReportView.vue`
- `web-pc/src/views/ReportBoardView.vue`
- `web-pc/src/views/ReportPersonView.vue`
- `web-pc/src/views/PersonalView.vue`
- `web-pc/src/views/OverviewView.vue`
- `web-pc/src/views/MyReviewsView.vue`
- `web-pc/src/views/ProjectDetailView.vue`
- `web-pc/src/layouts/MainLayout.vue`
- `web-pc/src/router/index.ts`
- `README.md`

---

## 会话总结（2026-09-20 — 发布周报管理前端）

### 会话的主要目的
把已改好的 PC「周报管理」页面发布到线上，替换仍在使用的旧侧栏。

### 完成的主要任务
- 执行 `systemctl restart workbench-frontend`，重新构建并发布 PC 与钉钉 H5
- 确认打包产物包含「周报管理」（`ReportManageView`、`MainLayout`）

### 关键决策和解决方案
- 沿用现有 systemd 前端服务发布，不手工拷贝 dist

### 使用的技术栈
systemd、npm、Vite 构建

### 修改了哪些文件
- `web-pc/dist/**`（构建产物）
- `web-dingtalk/dist/**`（构建产物，随服务一并构建）
- `README.md`

---

## 会话总结（2026-09-20 — 去掉周报重复内容）

### 会话的主要目的
看周报时同一条任务连着出现很多遍，需要合并重复内容并避免以后再写重。

### 完成的主要任务
- 查出重复是库里真的存了多份，不是页面画了两遍
- 已合并完全相同的周报卡片（同一份周报里相同标题、做成了什么、下一步只留一条）
- 保存周报时自动丢掉内容完全一样的重复卡片

### 关键决策和解决方案
- 展示以数据库为准，先清历史重复，再在保存接口里去重
- 内容多一点点的那条（例如多了数量）保留，空的那条删掉

### 使用的技术栈
PostgreSQL、FastAPI、SQLAlchemy

### 修改了哪些文件
- `backend/app/api/progress_reports.py`
- `backend/tests/test_report_workbench.py`
- `README.md`

---

## 会话总结（2026-09-20 — 任务按上下文自动归类项目）

### 会话的主要目的
周报里没挂项目或挂在「日常任务」上的卡片，按项目名和同事近期任务自动归到对应项目；写任务时根据标题自动建议项目，人仍可以改。

### 完成的主要任务
- 归类时除了对比项目名，还会看本人和同项目同事已经归好的任务，上下文相近才归入
- 保存周报时，空项目或「日常任务」会尝试自动归类；明显是报销、开会这类杂事仍留在日常
- 历史未归类卡片在后端启动时按同一规则重扫
- 写任务页标题停顿后请求建议，自动选中项目并提示原因；手动改选后不再覆盖
- 已重新构建电脑端，并重启后端

### 关键决策和解决方案
- 已经挂在真实立项上的卡片不改
- 写任务只能建议自己加入过的项目，避免选了却保存失败
- 开票、报销等杂事不因偶然相似被拉走

### 使用的技术栈
FastAPI、SQLAlchemy、Vue 3、Element Plus

### 修改了哪些文件
- `backend/app/services/project_match.py`
- `backend/app/services/project_service.py`
- `backend/app/services/task_ai.py`
- `backend/app/api/tasks.py`
- `backend/app/api/progress_reports.py`
- `backend/app/schemas/__init__.py`
- `backend/tests/test_project_match.py`
- `web-pc/src/views/WriteTaskView.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-20 — PC 工作台页面改版）

### 会话的主要目的
只把电脑端工作台这一页改成截图那种首页排版，侧栏和配色保持不动。

### 完成的主要任务
- 页面大标题改为「工作台」，并加上今天的日期
- 上方四张数字卡：项目、今日待办、逾期任务、本周周报
- 中间左边是项目总览（状态、阶段、进度、待办/逾期/本周任务），右边是今天到期的任务
- 下面三块：本周周报、按真实阶段分布的项目、最近更新的任务
- 右上角「快速记录」可去写任务或写周报
- 今天该看什么、重点项目/任务/质量告警、卡点、部门待办仍留在页面下方
- 已重新构建电脑端，登录页标题仍是「项目支撑工作台」

### 关键决策和解决方案
- 侧栏名称和菜单完全不改
- 不做天气、日历、文件资料、本地模式；阶段用系统里的真实阶段，不用「施工/交付」
- 勾选完成任务仍要先有结果说明，否则会打开任务详情去补
- 今日待办只统计截止日期是今天的任务，任务没有具体钟点

### 使用的技术栈
Vue 3、Element Plus、Vite

### 修改了哪些文件
- `web-pc/src/views/PersonalView.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-20 — 今天该看什么放到最前）

### 会话的主要目的
打开工作台时，先看到「今天该看什么」，再看下面的数字和项目。

### 完成的主要任务
- 把「今天该看什么」从页面下方挪到标题正下方、四张数字卡之前
- 已重新构建电脑端

### 关键决策和解决方案
- 只改这一块的位置，标题、侧栏和其他卡片都不动
- 没有内容时这一块仍然不显示，避免空框占位

### 使用的技术栈
Vue 3、Element Plus、Vite

### 修改了哪些文件
- `web-pc/src/views/PersonalView.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-20 — 团队待办并入今日待办）

### 会话的主要目的
管理员和部门负责人看团队待办时，不用再滚到页面最底下。

### 完成的主要任务
- 团队待办挪进右侧「今日待办」栏，显示任务名、负责人和截止日期
- 删掉页面底部单独的部门待办表格
- 点「逾期任务」会滚到这一栏里的团队待办
- 已重新构建电脑端

### 关键决策和解决方案
- 上方「今日待办」数字仍只算自己今天到期的任务，团队条数写在栏内小标题上
- 别人的任务不能在这里勾完成，点开进任务详情
- 普通成员看不到团队段

### 使用的技术栈
Vue 3、Element Plus、Vite

### 修改了哪些文件
- `web-pc/src/views/PersonalView.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-20 — 项目进度改成横向进度条）

### 会话的主要目的
打开项目后，不用从上到下读完一列阶段，就能看出做到哪一步。

### 完成的主要任务
- 项目详情「进度阶段」从竖向名单改成横向格子
- 已完成用浅薄荷绿，进行中用更深的薄荷绿，未开始用灰色
- 有计划完成日才显示日期，逾期仍用红字
- 已重新构建电脑端

### 关键决策和解决方案
- 不做日历甘特图，因为阶段大多没有开始和结束日期，硬画会空着或编出假时间
- 只改项目详情这一页，工作台首页的阶段小卡不动

### 使用的技术栈
Vue 3、Element Plus、Vite

### 修改了哪些文件
- `web-pc/src/views/ProjectDetailView.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-20 — 阶段与任务分层进度）

### 会话的主要目的
项目进度要像甘特图左边那样：阶段下面挂任务，一眼看清关系；配色仍用薄荷绿。

### 完成的主要任务
- 任务增加可选「所属阶段」；写任务、任务详情都能选
- 项目详情「进度阶段」保留横排总览，下面增加可展开的「阶段与任务」分层名单
- 右侧用相对进度条（按状态和截止日），不做日历轴和假工期
- 未选阶段的任务归到「未归入阶段」
- 已重启后端、重建电脑端

### 关键决策和解决方案
- 不做完整日历甘特图（多数没有开始日，硬画会编日期）
- 配色只用薄荷绿 / 灰 / 逾期红，不用参考图里的绿橙蓝
- 「任务待办」页签保留，方便写任务和看全表

### 使用的技术栈
FastAPI、SQLAlchemy、Vue 3、Element Plus、Vite

### 修改了哪些文件
- `backend/app/models/__init__.py`
- `backend/app/schemas/__init__.py`
- `backend/app/core/migrations.py`
- `backend/app/services/task_service.py`
- `backend/app/api/tasks.py`
- `web-pc/src/views/WriteTaskView.vue`
- `web-pc/src/views/TaskDetailView.vue`
- `web-pc/src/views/ProjectDetailView.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-20 — 周报「做成了什么」同步为阶段已完成任务）

### 会话的主要目的
提交周报后，「做成了什么」也要变成对应项目节点上的任务，只是状态是已经完成的历史记录；「下一步」仍是待办。

### 完成的主要任务
- 新增阶段猜测工具：根据标题/结果文字推断挂到哪一阶段
- 提交周报时：下周计划 → 待办任务并挂阶段；做成了什么 → 已完成任务并挂阶段
- 两类任务用不同来源标记，避免互相覆盖，也不再回写进周报草稿打架
- 提交成功提示会说明同步了几条
- 单测通过；已重启后端、重建电脑端

### 关键决策和解决方案
- 「做成了什么」与「下一步」都是节点任务，只是历史完成 vs 未来待办
- 做成了什么单独存（不占用下一步的关联字段），重复提交可更新同一条
- 阶段猜不准时允许为空，用户仍可在任务详情里改阶段

### 使用的技术栈
FastAPI、SQLAlchemy、pytest、Vue 3、Element Plus、Vite

### 修改了哪些文件
- `backend/app/services/phase_match.py`（新建）
- `backend/app/services/report_next_plan.py`
- `backend/app/services/report_task_sync.py`
- `backend/app/api/progress_reports.py`
- `backend/app/schemas/__init__.py`
- `backend/tests/test_report_next_plan.py`
- `web-pc/src/views/ProgressReportView.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-20 — 修复周报未挂到项目任务/进度）

### 会话的主要目的
项目详情里能看到周报「做成了 / 下一步」，但「任务待办」「进度阶段」里没有对应任务。

### 完成的主要任务
- 查清原因：旧周报提交时，部分任务被写进「日常任务」且没有阶段；「做成了什么」当时还不会生成已完成任务
- 同步时若条目落在日常/空项目，会按文字再归一次真实项目，并写回周报条目
- 下一步待办标题改为优先用「下一步」文案；阶段猜测避免「手表测试」误挂「测试」
- 已把俞烨涛该周周报回填：手表项目下出现「手表正常推广」（待办）和本周做成事项（已完成），都挂在「需求分析」
- 单测通过并重启后端

### 关键决策和解决方案
- 对已提交周报做规则回填（不用每次调大模型，避免又慢又卡住）
- 短阶段名（如「测试」）不做随便子串匹配，猜不到就挂当前进行中的阶段

### 使用的技术栈
FastAPI、SQLAlchemy、pytest、PostgreSQL

### 修改了哪些文件
- `backend/app/services/report_next_plan.py`
- `backend/app/services/phase_match.py`
- `backend/tests/test_report_next_plan.py`
- `README.md`

---

## 会话总结（2026-09-20 — 纠正手表项目负责人与归属）

### 会话的主要目的
分清两个手表项目：老年人手表归谁负责；徐初的手表工作应挂哪个项目。

### 完成的主要任务
- 「智慧康养-老年人手表」：项目经理俞烨涛、项目支撑罗琳
- 新建「智安校园-小学生手表项目」，负责人徐初
- 把徐初误挂在老年人手表上的周报条目和已完成任务，挪到小学生手表项目
- 徐初从老年人手表成员里移除，减少以后再挂错

### 关键决策和解决方案
- 两个「负责人」对应系统里的项目经理 + 项目支撑
- 小学生手表是新立项名，与老年人手表分开，避免都叫「手表」混在一起

### 使用的技术栈
PostgreSQL、SQLAlchemy、FastAPI 数据修正

### 修改了哪些文件
- 数据库项目/成员/任务/周报条目（无代码文件变更）
- `README.md`

---

## 会话总结（2026-09-20 — 创建人≠项目经理，支撑可多人）

### 会话的主要目的
项目创建人可以不是项目经理，也可以当项目支撑；项目经理可先空着；支撑可多人。

### 完成的主要任务
- 新建项目：经理可选空，支撑可多选；项目归属人=创建人
- 成员角色：同样支持经理清空、多名支撑
- 「智慧康养-老年人手表」：项目经理未指定，项目支撑为俞烨涛、罗琳
- 单测通过，已重启后端并重建电脑端

### 关键决策和解决方案
- 创建人与项目经理脱钩：谁点「创建」谁是归属人，角色另选
- 界面不再用归属人姓名冒充项目经理

### 使用的技术栈
FastAPI、Vue 3、Element Plus、pytest

### 修改了哪些文件
- `backend/app/schemas/__init__.py`
- `backend/app/api/projects.py`
- `backend/tests/test_project_members.py`
- `web-pc/src/views/OverviewView.vue`
- `web-pc/src/views/ProjectDetailView.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-21 — 询问用户初始密码）

### 会话的主要目的
确认系统用户初始密码规则。

### 完成的主要任务
- 查阅 README 与同步/演示账号逻辑并答复

### 关键决策和解决方案
- 钉钉同步员工：初始密码 = 工号 + 手机号；演示管理员：InitAdmin2026
- 未改代码

### 使用的技术栈
无

### 修改了哪些文件
- `README.md`

---

## 会话总结（2026-09-21 — 删除测试数据）

### 会话的主要目的
清掉单测和联调留在正式库里的假账号、假项目，不动真实员工和业务项目。

### 完成的主要任务
- 清理规则补上账号前缀 `pr_`、`ra_`，以及项目名前缀 `无经理_`
- 删除测试账号 42 个、测试项目 7 个（含「我的测试」「下周计划测试项目」「role_」「新建角色_」「无经理_」）
- 复核：测试账号和带「测试」的项目名为 0；正式工号账号与业务项目保留

### 关键决策和解决方案
- 只按账号前缀和项目名规则删，不按姓名删（避免误删真的许金斌、周永祺、钱波、孙振振）
- 测试账号若挂了正式项目，脚本会把归属改到管理员，这次没有这种情况

### 使用的技术栈
Python、SQLAlchemy、既有级联删除

### 修改了哪些文件
- `scripts/purge_test_data.py`
- `README.md`

---

## 会话总结（2026-09-21 — 周报详情显示全部含已挂项目）

### 会话的主要目的
周报详情要列出该人该周期全部任务；已经挂项目的条目也要显示，并标出项目名。

### 完成的主要任务
- 人员周报详情：正文显示全部条目，标题旁标项目名（已选或推断），推断加「推断归属」；标题显示「共 N 项」
- 按项目看板：匹配改为已选项目或推断归属，避免推断条目两边都看不到
- 核对凌亮 2026-09-14 周报：1 条，项目「数智富春150U主机托管」；已重建电脑端

### 关键决策和解决方案
- 详情页本来就有全量条目，缺的是项目名展示
- 看板与项目详情页的归属判断对齐

### 使用的技术栈
Vue 3、Element Plus、Vite

### 修改了哪些文件
- `web-pc/src/views/ReportPersonView.vue`
- `web-pc/src/components/report-board/ReportProjectBoard.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-21 — 周报详情不看全部周期）

### 会话的主要目的
用户明确不要「全部周期」汇总；详情只看当前选中的那一周。

### 完成的主要任务
- 去掉「全部」芯片和跨周期汇总加载
- 恢复单周期正文；该周全部条目仍显示，挂了项目的标项目名
- 已重建电脑端

### 关键决策和解决方案
- 「显示全部」指同一周内的全部任务条目（含已挂项目），不是把所有历史周拼在一起

### 使用的技术栈
Vue 3、Vite

### 修改了哪些文件
- `web-pc/src/views/ReportPersonView.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-21 — 写周报每卡附件）

### 会话的主要目的
写周报时，每个任务卡片有独立附件区，附件挂到具体任务条目，不再只挂在整份周报底部。

### 完成的主要任务
- 附件表增加可空字段 `item_id`（外键任务条目；条目重建删除时置空，保存后再按卡片序号挂回）
- 上传接口支持表单字段 `item_id`，并校验条目属于该周报；返回结构按条目分发 `attachments`
- PC 写周报：每张任务卡片内可上传/下载/删除；无草稿或无条目 id 时先自动存草稿再传
- 页面底部仅保留「整份周报附件（历史）」只读展示区（旧数据 `item_id` 为空）
- 人员周报详情：每条任务下展示该条附件；旧整份附件单独一小段
- 已重建电脑端并重启后端

### 关键决策和解决方案
- 保存周报会清空并重建条目，因此外键用 `ON DELETE SET NULL`，再按 `sort_order` 把附件挂回新条目，避免文件丢失
- 新附件必须带 `item_id`；旧无条目附件继续在报告级展示，避免历史文件消失

### 使用的技术栈
FastAPI、SQLAlchemy、PostgreSQL、Vue 3、Element Plus、Vite

### 修改了哪些文件
- `backend/app/models/__init__.py`
- `backend/app/core/migrations.py`
- `backend/app/schemas/__init__.py`
- `backend/app/api/progress_reports.py`
- `web-pc/src/views/ProgressReportView.vue`
- `web-pc/src/views/ReportPersonView.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-21 — 钉钉端看为主、写为轻量）

### 会话的主要目的
手机端以查看为主：管理者一眼看到重点项目、周报提交、待办和卡点；记任务、写周报只留最少字段。

### 完成的主要任务
- 钉钉首页改成管理四块（重点项目进度、本周未交名单、逾期/今天待办、卡点风险）和成员短首页；从后台回到前台会重新拉数据
- 项目详情增加只读横向阶段条，手机上不能改阶段
- 记任务默认只填标题、项目、截止日期，标题停顿后自动建议项目，阶段可选；目标、辅助人、一句话生成收进「更多」
- 写周报默认只留三问和提交；已写内容、整理建议、任务链和附件收进「更多」

### 关键决策和解决方案
- 不新做接口，沿用电脑端已有的项目列表、周报看板、个人工作台和猜项目接口
- 重点项目沿用级别 key / red / yellow
- 方案库和系统管理仍只在电脑端

### 使用的技术栈
Vue 3、TypeScript、Vite、钉钉 H5（web-dingtalk）

### 修改了哪些文件
- `web-dingtalk/src/views/HomeView.vue`
- `web-dingtalk/src/views/ProjectDetailView.vue`
- `web-dingtalk/src/views/TaskCreateView.vue`
- `web-dingtalk/src/views/ReportView.vue`
- `web-dingtalk/dist/**`
- `README.md`

---

## 会话总结（2026-09-23 — 任务管理搜索与筛选）

### 会话的主要目的
优化 PC 端任务管理：支持按关键词搜索，并按负责人、任务级别筛选。

### 完成的主要任务
- 后端 `GET /api/tasks/related` 增加可选参数：`search`（标题模糊）、`assignee_id`（负责人）、`priority`（p0–p4）
- PC 任务列表工具栏增加：负责人下拉、级别下拉、搜索框与查询按钮（交互对齐总览项目筛选）
- 负责人选项从当前可见任务去重得到，避免普通成员无「候选人」接口权限；已选负责人时缓存选项不被筛空
- 补充接口测试；重建电脑端并重启后端

### 关键决策和解决方案
- 本轮只做第 1 点；「人」= 负责人（assignee），「级别」= priority（P0–P4）
- 筛选在原有可见性规则之上叠加，普通用户仍看不到无关任务
- 钉钉端、个人/部门待办、项目内任务列表本轮不改

### 使用的技术栈
FastAPI、SQLAlchemy、Vue 3、Element Plus、pytest、Vite

### 修改了哪些文件
- `backend/app/api/tasks.py`
- `backend/app/services/task_service.py`
- `backend/tests/test_tasks_assign.py`
- `web-pc/src/components/TaskAssignPanel.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-24 — 修复周报保存失败却多出多条记录）

### 会话的主要目的
排查并修复写周报时界面提示「保存失败」，却在「我的三句话预览」里多出多条任务卡片的问题。

### 完成的主要任务
- 后端保存周报改为：同一周期加行锁（FOR UPDATE）→ 先提交卡片正文 → 再同步下周待办 /「做成了什么」
- PC 写周报页增加保存中锁与按钮 loading，禁止连点；提交超时 120 秒、草稿 30 秒；超时提示改为「请刷新查看是否已保存」
- 待办同步创建 Task 前按 source / source_ref / 负责人再查一次，降低并发双建风险

### 关键决策和解决方案
- 根因：提交时在 commit 前同步调用最长约 90 秒的 LLM，前端 axios 仅 15 秒超时误报失败；用户重试与未完成请求并发，整表 clear+insert 叠出双倍卡片
- 本轮不做独立异步队列、不加数据库唯一约束（避免历史脏数据迁移失败）；用先落库 + 行锁 + 前端防重解决「失败却翻倍」

### 使用的技术栈
FastAPI、SQLAlchemy、Vue 3、Element Plus、axios

### 修改了哪些文件
- `backend/app/api/progress_reports.py`
- `backend/app/services/report_next_plan.py`
- `web-pc/src/views/ProgressReportView.vue`
- `web-pc/dist/**`（构建产物）
- `README.md`

---

## 会话总结（2026-09-24 — 修复周报错落到上一周期）

### 会话的主要目的
排查并修复「今天写的周报落到之前周期」：截图中本周（约 09-21）无芯片，却在已交的 09-14 周期里出现 09-23/09-24 的下一步日期。

### 完成的主要任务
- 去掉 PC / 钉钉写周报页「本周无内容 → 自动切到最近历史周期」逻辑，本周为空时留在本周并走预填
- 周期芯片前端补插「本周未写」条目，避免只能点到历史周
- 历史已交周期默认只读，需点「解锁修改」；后端 `save_report` 对历史已交要求 `force_edit_submitted=true`，否则 409
- 补充回归测试；重建 PC / 钉钉前端产物并重启后端

### 关键决策和解决方案
- 根因不是周一/时区算错，而是写路径误用了「看历史」的 auto-switch，保存带着切过去的 `period_start`
- 看板只读 auto-switch 保留；本周已交仍可直接改；历史已交需显式解锁，防止误写

### 使用的技术栈
FastAPI、Pydantic、Vue 3、Element Plus、pytest、Vite

### 修改了哪些文件
- `web-pc/src/views/ProgressReportView.vue`
- `web-dingtalk/src/views/ReportView.vue`
- `backend/app/api/progress_reports.py`
- `backend/app/schemas/__init__.py`
- `backend/tests/test_report_period_lock.py`（新建）
- `backend/tests/test_progress_reports.py`
- `web-pc/dist/**`
- `web-dingtalk/dist/**`
- `README.md`

---

## 会话总结（2026-09-24 — 周报先保存再后台 AI 同步）

### 会话的主要目的
把周报提交后的 AI 待办同步从「同请求内等待」改为「先保存立刻返回，再后台修正」。

### 完成的主要任务
- `save_report` 卡片 commit 后通过 FastAPI `BackgroundTasks` 排队 `_sync_submitted_report_todos`
- 后台任务用独立 `async_session` 跑下周计划 /「做成了什么」同步（可含 LLM）
- PC 提交成功提示改为「正在后台写入待办」；本接口超时统一 30 秒

### 关键决策和解决方案
- 对齐方案库 `process_solution` 模式，避免用户干等 AI
- 响应里 `next_plan_synced` / `done_synced` 本次固定为 0（不同步等待完成条数）
- 草稿保存不触发后台任务

### 使用的技术栈
FastAPI BackgroundTasks、SQLAlchemy async_session、Vue 3、Element Plus

### 修改了哪些文件
- `backend/app/api/progress_reports.py`
- `web-pc/src/views/ProgressReportView.vue`
- `web-pc/dist/**`
- `README.md`

---

## 会话总结（2026-09-24 — 误落周报迁回本周）

### 会话的主要目的
把因写周报「自动切历史周期」而误写入上一周（09-14）的条目，迁回正确的本周（09-21～09-27）。

### 完成的主要任务
- 编写一次性迁移脚本 `scripts/migrate_misplaced_week_reports.py`（默认 dry-run，`--apply` 写入）
- 判定：上周周报在本周被提交/更新，且非测试账号
- 已执行迁移：
  - 罗**：整份 5 条周报周期从 09-14 改为 09-21～09-27
  - 王**：3 条重复误写去重后并入本周已有周报，并删除上周空壳

### 关键决策和解决方案
- 不用「下一步日期 ≥ 本周一」粗筛（会误伤正常跨周计划），改用「本周内动过的上周周报」
- 本周无周报 → 直接改 `period_start`；本周已有 → 去重并入并删空壳
- 姓名等在日志中脱敏展示

### 使用的技术栈
Python、SQLAlchemy、asyncio、PostgreSQL

### 修改了哪些文件
- `scripts/migrate_misplaced_week_reports.py`（新建）
- 数据库中相关 `progress_reports` / `progress_report_items` / `progress_report_reviews` 记录
- `README.md`

---

## 会话总结（2026-09-24 — 清理看板错误周期日期）

### 会话的主要目的
周报看板「有数据的周期」出现错误日期：`2099-03-02`、误标的下周 `09-28`，以及测试账号污染人数统计。

### 完成的主要任务
- 删除测试账号周报（含 2099、`t_assist` 的 09-01 等）
- 将正式成员误写到「下周 09-28」的条目并回本周 09-21
- 看板 `available_periods` 只统计名单内正式成员，并过滤离谱未来周期（超过下周一）

### 关键决策和解决方案
- `2099` / 部分重叠段来自自动化测试残留，不是业务真实周期
- `09-28` 芯片是本周误把日期选到下周；内容已并入本周周报
- `08-31~09-04`、`09-07~09-11` 为历史导入的工作日区间，属正常历史数据，未改

### 使用的技术栈
FastAPI、SQLAlchemy、PostgreSQL

### 修改了哪些文件
- `backend/app/api/progress_reports.py`（看板周期过滤）
- 数据库相关周报记录（清理/合并）
- `README.md`

---

## 会话总结（2026-09-24 — 删除测试数据与工作台布局优化）

### 会话的主要目的
清理个人工作台里残留的测试项目，并解决三列看板（本周周报 / 项目阶段 / 最近更新）左右空白、中间过长的高度失衡。

### 完成的主要任务
- 先 `--dry-run` 预览，再执行 `scripts/purge_test_data.py`
- 删除测试账号 **9** 个、测试项目 **7** 个（含「我的测试」「周报测试项目」「周期锁测试项目」「proj_bfffcc21」）及关联任务 6、成员关系 29 等
- 优化工作台 `.bottom-grid`：左右列按内容高度；项目阶段区限高 360px 可滚动；窄屏单列堆叠
- 重建 PC 前端并发布 `web-pc/dist`

### 关键决策和解决方案
- 复用既有清理脚本，不改匹配规则；「日常任务」作为杂项归口保留，未删除
- 布局只做 CSS：`align-items: start` + `.phase-board` 限高滚动，不改三列信息结构

### 使用的技术栈
Vue 3、Vite、Python、SQLAlchemy、`purge_test_data.py`

### 修改了哪些文件
- `web-pc/src/views/PersonalView.vue`（布局 CSS）
- `web-pc/dist/**`（重建）
- 数据库（测试账号/项目及级联数据）
- `README.md`

---

## 会话总结（2026-09-24 — 固化「测完清数据」规则）

### 会话的主要目的
把「测试完成后必须删除测试数据和测试账号」写成长期约定，避免脏数据再次出现在工作台。

### 完成的主要任务
- 新增 Cursor 项目规则：测完先 dry-run，再跑 `purge_test_data.py`，并在 README 记录清理结果
- 确认上一轮清理与布局优化计划已全部完成，无需重复执行

### 关键决策和解决方案
- 规则设为 `alwaysApply: true`，每次会话都会生效
- 清理统一走既有脚本，保留「日常任务」等真实业务数据

### 使用的技术栈
Cursor Rules（`.mdc`）、`purge_test_data.py`

### 修改了哪些文件
- `.cursor/rules/purge-test-data-after-testing.mdc`（新建）
- `README.md`

---

## 会话总结（2026-09-24 — 重置徐初密码）

### 会话的主要目的
应要求重置员工「徐初」的登录密码。

### 完成的主要任务
- 确认账号：用户名 `fy0583`，姓名徐初，钉钉已绑定
- 重置为临时强密码，并设置 `must_change_password=True`（首次登录须改密）
- 写入 `reset_password` 审计记录；接口登录校验通过

### 关键决策和解决方案
- 按系统强密码规则设置临时密码；不恢复「工号+手机号」旧约定，避免手机号不在用户表时算错
- 临时密码仅当面告知，不写入本 README 正文，降低泄露面

### 使用的技术栈
Python、SQLAlchemy、`hash_password`、审计日志

### 修改了哪些文件
- 数据库 `users`（fy0583 密码哈希 / must_change_password）与 `audit_logs`
- `README.md`

---

## 会话总结（2026-09-25 — 智能助手使用查看）

### 会话的主要目的
在系统管理中增加「助手使用」查看，让管理员（`admin.model`）看到全员智能助手打开次数、进出明细与对话/token 用量。

### 完成的主要任务
- 助手门禁（dsh-gate）新增签名接口：`/internal/usage/summary|visits|sessions`，汇总审计日志与会话 `tokenUsage`
- 工作台新增 `GET /api/admin/dsh-usage/*`，转发助手接口并补全姓名
- AdminView 增加「助手使用」Tab（日期范围、按人汇总、进出明细、会话用量）
- 单测 `tests/test_dsh_usage.py` 通过；已重建前端并重启后端/前端服务
- 测试收尾清理：删除测试账号 1 个（`t_dsh_usage_m`）、测试项目 1 个（「我的测试」）

### 关键决策和解决方案
- 用量数据以助手机本地审计 + 会话文件为准，工作台不做日志镜像
- v1 只展示 token/轮数，不算人民币费用
- 权限与「模型管理」共用 `admin.model`

### 使用的技术栈
Node.js（dsh-gate）、FastAPI、Vue3、Element Plus、机器签名（service-auth）、pytest

### 修改了哪些文件
- 助手机：`/opt/dsh-gate/usage-stats.js`、`usage-stats.test.js`、`server.js`；`/opt/readme.md`
- 工作台：`backend/app/api/dsh.py`、`backend/app/api/admin.py`、`backend/app/schemas/__init__.py`、`backend/tests/test_dsh_usage.py`、`web-pc/src/views/AdminView.vue`、`README.md`

---

## 会话总结（2026-09-25 — 项目阶段闭环与分类型模板）

### 会话的主要目的
诊断并补齐项目管理「阶段」半闭环：让「当前阶段」与阶段时间线状态一致，并按软件/系统集成区分默认阶段。

### 完成的主要任务
- 后端按类型播种阶段：软件/融合用「开发…」，系统集成用「方案设计 / 采购到货 / 现场实施 / 联调测试…」
- 实现 `sync_project_phase_progress`：改当前阶段或点格子状态时，自动完成之前阶段、激活当前、同步 `current_phase`
- 改类型时仅在「默认模板且未改过」时替换阶段时间线
- PC 项目详情进度格子可下拉改未开始/进行中/已完成；编辑表单阶段下拉按类型过滤
- 周报猜阶段扩展集成类别名，并按项目实际阶段名择优
- 单测 `tests/test_project_phases.py` 通过；purge 识别规则增加 `phase_` 前缀
- 测试收尾清理：删除测试账号 5 个（`pr_*`）、测试项目 6 个（`phase_*`）

### 关键决策和解决方案
- 先打通推进闭环，再换按类型词表；不做「任务全完成自动进下一阶段」
- 双向同步规则：设进行中=之前已完成+当前进行中+之后未开始；设已完成则推进到下一阶段
- 钉钉端阶段条仍只读，PC 先闭环

### 使用的技术栈
FastAPI、SQLAlchemy、Vue3、Element Plus、pytest

### 修改了哪些文件
- `backend/app/services/project_phases.py`、`project_service.py`、`phase_match.py`、`assist_ai.py`
- `backend/app/api/projects.py`
- `backend/tests/test_project_phases.py`（新建）、`test_report_next_plan.py`
- `web-pc/src/constants/projectMeta.ts`、`views/ProjectDetailView.vue`、`views/PersonalView.vue`
- `scripts/purge_test_data.py`、`.cursor/rules/purge-test-data-after-testing.mdc`、`README.md`

## 会话总结（2026-09-25 — 任务×周报闭环体验补齐）

### 会话的主要目的
对照已确认需求评估任务管理与周报闭环后，补齐三项体验缺口：周报跳任务、钉钉 H5 换负责人、本周已交未写入草稿的明确提示。

### 完成的主要任务
- PC / 钉钉：周报条目与人员详情上的 `linked_task_id` 可一键打开任务详情
- 钉钉任务详情：支持更换负责人、移除辅助人（对齐 PC 与后端接口）
- 写任务 / 完成任务：本周周报已交时给出明确「未写入草稿」提示（完成场景说明可写下周）
- 修复 `test_report_next_plan`：周期改用当前周；适配提交后后台同步待办（响应计数为 0）
- 相关单测 24 通过；已重建 PC 与钉钉 H5 前端
- 测试收尾清理：删除测试账号 112 个、测试项目 56 个（未动「日常任务」）

### 关键决策和解决方案
- 主闭环早已按基线落地；本轮只补体验，不做人工二阶段评价、评价回流等非目标项
- 提交周报后的待办同步保持后台执行；单测显式调用后台函数再断言库内结果

### 使用的技术栈
Vue3、Element Plus、FastAPI、pytest

### 修改了哪些文件
- `web-pc/src/views/ProgressReportView.vue`、`ReportPersonView.vue`、`WriteTaskView.vue`、`TaskDetailView.vue`、`PersonalView.vue`、`ProjectDetailView.vue`
- `web-dingtalk/src/views/TaskDetailView.vue`、`TaskCreateView.vue`、`ReportView.vue`、`ReportPersonView.vue`
- `backend/tests/test_report_next_plan.py`
- `web-pc/dist/*`、`web-dingtalk/dist/*`（重建）
- `README.md`

## 会话总结（2026-09-25 — 正式数据红线与每日备份）

### 会话的主要目的
落实红线：已有业务数据不能动；需要定期保存数据库。

### 完成的主要任务
- 核对正式数据仍在：「日常任务」存在；真实员工账号未受影响（当前约 58 用户 / 75 项目）
- 立即执行一次只读备份：`backups/project_workbench_20260925_221136.sql.gz`
- 加固 `scripts/backup.sh`（从 `.env` 读连接、写日志、保留约 30 天）
- 启用 systemd 每日备份：`workbench-backup.timer`（约 02:30）
- 新增 Cursor 规则 `protect-production-data.mdc`；更新 purge 规则为「先备份再 dry-run」
- README 全局规范增加红线与备份说明

### 关键决策和解决方案
- 备份只做 `pg_dump`，不改库
- 清理测试数据允许，但必须先备份 + dry-run 确认无真实数据；宁可留脏测试数据也不误删正式数据

### 使用的技术栈
bash、pg_dump、systemd timer、Cursor Rules

### 修改了哪些文件
- `scripts/backup.sh`
- `/etc/systemd/system/workbench-backup.service`、`workbench-backup.timer`
- `.cursor/rules/protect-production-data.mdc`（新建）
- `.cursor/rules/purge-test-data-after-testing.mdc`
- `README.md`
- `backups/`（新建备份文件，只读导出）

## 会话总结（2026-09-27 — 续：备份核验 + 周报文档对齐）

### 会话的主要目的
在「正式数据红线」之后继续收口：确认每日备份已跑通，并修正周报文档与代码硬拦不一致。

### 完成的主要任务
- 核验备份：9/25 手工、9/26 与 9/27 定时备份均成功；下次触发约 9/28 02:30；`daemon-reload` 消除 timer 配置告警
- 修正 `docs/周报填写标准.md`：所属项目改为必选（杂项选「日常任务」），硬拦截条目与 `report_quality.py` 对齐
- **未改动**任何业务库数据（仅文档与 systemd reload）

### 关键决策和解决方案
- 以代码硬拦为准改文档，不改产品规则、不放开「可不选项目」
- 备份继续只读 `pg_dump`，正式数据不动

### 使用的技术栈
systemd、Markdown 文档

### 修改了哪些文件
- `docs/周报填写标准.md`
- `README.md`

## 会话总结（2026-09-27 — 钉钉 H5 周报团队/个人双 Tab）

### 会话的主要目的
优化钉钉 H5 周报入口：管理者可看团队周报，也可写自己的周报，团队与个人分开。

### 完成的主要任务
- 新增周报壳页 `/reports`：有 `report.view.all` 时顶部「团队周报 | 我的周报」，默认进团队
- 普通员工无团队 Tab，直接「我的周报」
- 底栏「周报」改走壳页；旧路径 `/report-board`、`/my-reports` 重定向到对应 Tab
- 去掉看板「去写周报」、写页「查看全员看板」等交叉入口；首页/人员详情返回对齐壳页
- 已 `npm run build` 通过并更新 `web-dingtalk/dist`

### 关键决策和解决方案
- 用壳页 + `?tab=team|mine` 分离，不改后端（复用 board / mine API）
- 写报仍独立 `/report`，不把表单塞进 Tab，避免一次加载过重

### 使用的技术栈
Vue3、Vue Router、钉钉 H5（web-dingtalk）

### 修改了哪些文件
- `web-dingtalk/src/views/ReportsHubView.vue`（新建）
- `web-dingtalk/src/router/index.ts`、`MainLayout.vue`
- `web-dingtalk/src/views/ReportBoardView.vue`、`MyReportsView.vue`、`ReportView.vue`、`HomeView.vue`、`ReportPersonView.vue`
- `web-dingtalk/dist/*`（重建）
- `README.md`

## 会话总结（2026-09-27 — 钉钉 H5 周报 Tab 吸顶固定）

### 会话的主要目的
让管理者周报页「团队周报 / 我的周报」两个 Tab 位置固定，下滑看名单时不会滚走。

### 完成的主要任务
- 周报壳页标题、说明与双 Tab 做成吸顶条，列表在下方滚动
- 左侧始终是「团队周报」，右侧始终是「我的周报」
- 已重建钉钉 H5 前端

### 关键决策和解决方案
- 用 `position: sticky` 钉在屏幕顶部，不改底栏、不改后端

### 使用的技术栈
Vue3、CSS sticky、钉钉 H5

### 修改了哪些文件
- `web-dingtalk/src/views/ReportsHubView.vue`
- `web-dingtalk/dist/*`（重建）
- `README.md`

## 会话总结（2026-09-27 — 钉钉 H5 全量样式改版）

### 会话的主要目的
按效果图全量重做钉钉 H5 视觉与布局，紫色换成现有薄荷绿；周报按角色分流。

### 完成的主要任务
- 底栏改为：首页 / 任务 / 周报 / 项目；选中态黑圆；「我的」从首页头像进入
- 首页：问候、搜索、本周周报黄条、今日待办勾选、项目协同进度、薄荷绿 FAB
- 周报：管理者「团队 | 我的」吸顶；员工仅状态列表（全部/草稿/已交/已通过=已交且有评价）
- 写周报：顶栏+统计+底栏存草稿/提交；写报页隐藏底栏
- 项目协同大卡 + 轻量「最近动态」；任务页勾选风格对齐
- `npm run build` 通过并更新 dist

### 关键决策和解决方案
- 不做后端审批流；「已通过」= submitted + phase1_review
- CTA/FAB/勾选统一 `--wb-mint`，不用效果图紫色
- 写周报仍按任务三问模型，仅换皮

### 使用的技术栈
Vue3、Vue Router、CSS 变量、钉钉 H5

### 修改了哪些文件
- `web-dingtalk/src/styles/theme.css`
- `web-dingtalk/src/views/MainLayout.vue`、`HomeView.vue`、`ReportsHubView.vue`、`MyReportsView.vue`、`ReportBoardView.vue`、`ReportView.vue`、`ProjectListView.vue`、`TodoView.vue`
- `web-dingtalk/dist/*`（重建）
- `README.md`

## 会话总结（2026-09-27 — 清理测试数据）

### 会话的主要目的
按红线流程清理共享库中的测试账号与测试项目。

### 完成的主要任务
- 先备份：`backups/project_workbench_20260927_143939.sql.gz`
- dry-run：`purge_test_data.py --dry-run` 结果为 **0 个测试账号、0 个测试项目**
- 未执行正式 purge（无需删除）；正式业务数据未动

### 关键决策和解决方案
- 按规则先备份再 dry-run；名单为空则不写库

### 使用的技术栈
bash backup.sh、purge_test_data.py、PostgreSQL

### 修改了哪些文件
- `backups/project_workbench_20260927_143939.sql.gz`（只读导出）
- `README.md`

## 会话总结（2026-09-27 — H5 布局交互审查与修复）

### 会话的主要目的
对钉钉 H5 全量改版做代码审查，重点交互与页面布局，并用运行时证据验证。

### 完成的主要任务
- Bugbot + 浏览器实测（390×844）
- 确认并修复：`/mine` 误高亮「首页」；首页 FAB 遮挡末尾卡片
- 验证：周报吸顶 Tab 正常；写报页隐藏底栏正常
- 埋点仍保留待你确认后拆除

### 关键决策和解决方案
- `MainLayout`：mine/reviews/password 不点亮任一底栏
- `HomeView`：额外底部 padding 为 FAB 留空

### 使用的技术栈
Vue3、Chrome CDP、debug NDJSON ingest

### 修改了哪些文件
- `web-dingtalk/src/views/MainLayout.vue`、`HomeView.vue`（含 debug 埋点）
- `ReportView.vue`、`ReportsHubView.vue`（仅埋点）
- `web-dingtalk/dist/*`（重建）
- `README.md`

## 会话总结（2026-09-27 — H5 交互自测完成）

### 会话的主要目的
自行完成钉钉 H5 布局/交互验证，确认修复有效并拆除 debug 埋点。

### 完成的主要任务
- 390×844 浏览器实测：首页 FAB、我的页高亮、周报双 Tab/状态筛、写报底栏、任务/项目底栏
- 员工角色：周报无「团队」Tab，仅状态筛选
- 拆除全部 ingest 埋点并重建 dist

### 关键决策和解决方案
- 保留 MainLayout 与 HomeView 的布局修复；写报/吸顶无需再改

### 使用的技术栈
Chrome CDP、Vue3 H5

### 修改了哪些文件
- `MainLayout.vue`、`HomeView.vue`、`ReportsHubView.vue`、`ReportView.vue`（拆埋点）
- `web-dingtalk/dist/*`
- `README.md`

## 会话总结（2026-09-27 — 清除 cl_/af_ 测试脏数据）

### 会话的主要目的
清除联调残留的 `cl_`/`af_` 测试账号与 `cl_proj_*` 假项目。

### 完成的主要任务
- 备份：`backups/project_workbench_20260927_150054.sql.gz`
- 扩展 purge 规则：账号前缀 `cl_`/`af_`，项目前缀 `cl_proj_`
- dry-run 确认无正式员工、「日常任务」等业务立项
- 正式 purge：**删除测试账号 45 个、测试项目 12 个**（并清理关联任务等）
- 再 dry-run 为空

### 关键决策和解决方案
- 只按假前缀清除；真实工号账号与业务项目名一律保留

### 使用的技术栈
bash backup.sh、purge_test_data.py、PostgreSQL

### 修改了哪些文件
- `scripts/purge_test_data.py`
- `.cursor/rules/purge-test-data-after-testing.mdc`
- `backups/project_workbench_20260927_150054.sql.gz`
- `README.md`

## 会话总结（2026-09-27 — 任务链补全 + 金滨项目归类）

### 会话的主要目的
优化任务管理：①「继续推进」等延续类事项串进任务链，避免无头无尾；②金滨相关事项归入「富阳区电子政务网驻场运维」。

### 完成的主要任务
- **写周报规则增强**：延续前缀去噪、历史窗口约 12 周、明确「继续推进/继续跟进」降阈值到 0.40；套话「继续政务外网…」不降阈值；LLM 提示优先串历史链
- **历史补链脚本** `backend/scripts/backfill_continuation_chains.py`：dry-run 后 apply；自动补链 8 组（约 15 处 `chain_id`），含金滨视联网/VPN/浙政钉/垃圾分类及罗琳家医、周永祺天网与水电、凌亮心理测评；低置信 4 条未写
- **金滨项目归类** `backend/scripts/reassign_jinbin_egov.py`：dry-run 确认 26 条周报与交办**已全部在**「富阳区电子政务网驻场运维」，无需再迁
- 单测 `test_report_ai_structure.py` 6 通过；修复历史周期日期与 purge 时 `tasks.source_report_id` 外键
- 备份：`backups/project_workbench_20260927_183324.sql.gz`、`…_183415.sql.gz`
- 清理测试数据：账号 3（`t_chain_*`）、测试项目 5；再 dry-run 为空

### 关键决策和解决方案
- 任务链仍挂在周报卡片 `chain_id`，不为交办 Task 新建父子表
- 单独「继续」太宽（金滨全员套话下一步），拆成 hard（继续推进等）与 soft（仅「继续…」+ 标题相似≥0.70）两档
- 金滨目标项目沿用正式名「富阳区电子政务网驻场运维」，不改名

### 使用的技术栈
FastAPI、SQLAlchemy、pytest、PostgreSQL、systemd

### 修改了哪些文件
- `backend/app/services/report_ai_structure.py`
- `backend/tests/test_report_ai_structure.py`
- `backend/scripts/backfill_continuation_chains.py`（新建）
- `backend/scripts/reassign_jinbin_egov.py`（新建）
- `backend/app/services/user_sync.py`（删用户前断开 `source_report_id`）
- `scripts/purge_test_data.py`（识别 `天网接入专项-`）
- `docs/周报填写标准.md`
- `README.md`

## 会话总结（2026-09-27 — 相同任务去重 + 持续推进提示）

### 会话的主要目的
创建交办/周报时拦截空泛「继续推进」与高度相似重复任务；清理库内已有重复未完成交办。

### 完成的主要任务
- 新增 `task_dedupe`：空泛续推阻断、相似硬档≥0.85 / 软档≥0.55
- API：`POST /tasks/check-similar`；创建任务支持 `force_create`，硬重复未放行返回 409
- PC 写任务 / 钉钉写任务：提交前弹窗（打开已有 / 仍要新建二次确认 / 软提醒）
- 周报 AI 返回 `continuation_warning`；保存前空泛续推确认；同报告高度相似卡可去掉重复
- 脚本 `merge_duplicate_tasks.py`：备份后 apply，**合并 25 条**重复未完成交办，周报 `linked_task_id` 改挂 22 处（含金滨/王捷等多条套话待办）
- 单测 `test_task_dedupe.py` + 原结构测试共 10 通过
- 清理测试账号 5、测试项目 4；PC/钉钉前端已 build，后端已重启
- 备份：`backups/project_workbench_20260927_184712.sql.gz`

### 关键决策和解决方案
- 不物理删除交办，重复条目标已完成并注明合并至主任务 id
- 空泛续推前后端双拦；硬重复默认引导打开已有，避免再造无头无尾的「继续推进」单

### 使用的技术栈
FastAPI、Vue3、Element Plus、pytest、PostgreSQL

### 修改了哪些文件
- `backend/app/services/task_dedupe.py`（新建）
- `backend/app/services/report_ai_structure.py`
- `backend/app/api/tasks.py`、`backend/app/schemas/__init__.py`
- `backend/scripts/merge_duplicate_tasks.py`（新建）
- `backend/tests/test_task_dedupe.py`（新建）
- `web-pc/src/views/WriteTaskView.vue`、`ProgressReportView.vue`
- `web-dingtalk/src/views/TaskCreateView.vue`、`ReportView.vue`、`utils/api.ts`
- `docs/周报填写标准.md`、`README.md`
- `scripts/purge_test_data.py`（沿用既有规则）

## 会话总结（2026-09-27 — 周报关联待办标题清晰化）

### 会话的主要目的
解决「从周报创建」的待办标题不清晰（如只有「完善采购，确定最终采购金额」、看不出项目/事项）。

### 完成的主要任务
- 计划内去重/续推提示能力此前已落地，本次核对保留
- 新增 `compose_clear_task_title`：概述 + 下一步拼标题；空泛续推带概述；套话「继续政务外网运维」不抢标题；缺语境时补项目简称
- 周报同步待办写入时使用清晰标题；LLM 空泛标题回退规则
- 回填脚本更新未完成 `report_next` 待办标题 **70** 条（含示例任务带上统建监控续租等项目语境）
- 单测 `test_report_task_titles.py`；清理测试账号 3、测试项目 5
- 备份：`backups/project_workbench_20260927_210718.sql.gz`

### 关键决策和解决方案
- 目标字段仍保留「下一步」原文便于验收；标题负责说清楚「哪件事」
- 不物理删任务，只改标题文案

### 使用的技术栈
Python、SQLAlchemy、pytest

### 修改了哪些文件
- `backend/app/services/report_next_plan.py`
- `backend/scripts/backfill_report_task_titles.py`（新建）
- `backend/tests/test_report_task_titles.py`（新建）
- `README.md`

## 会话总结（2026-09-27 — 售前标准化待办溯源与标题改写）

### 会话的主要目的
查清「继续推进「售前标准化」相关工作」来自哪份周报，并改成可读标题。

### 完成的主要任务
- 溯源：罗琳周报 `report#43`，周期 **2026-08-31～2026-09-04**，主题含售前标准化；卡片 item#554 → 待办 #495
- 识别空壳「继续推进「xxx」相关工作」，用「做成了什么」未完成要点拼标题/目标
- 回填同类模板标题约 30 条；#495 现为「售前标准化：跟进5g新通话网络调试、跟进一体化平台应急预案收集」

### 关键决策和解决方案
- 空壳下一步不再原样进待办标题；从本周结果抽待跟进事项

### 使用的技术栈
Python、SQLAlchemy、pytest

### 修改了哪些文件
- `backend/app/services/report_next_plan.py`
- `backend/scripts/backfill_report_task_titles.py`
- `backend/tests/test_report_task_titles.py`
- `README.md`

## 会话总结（2026-09-28 — 放行会议任务管理访问本机）

### 会话的主要目的
允许「会议任务管理」主机 `30.251.255.142` 访问本机 `30.251.255.64`。

### 完成的主要任务
- 防火墙 INPUT 增加来源 `30.251.255.142` 全流量放行，并单独标明 HTTP 80
- 规则已写入 `/etc/iptables/rules.v4`（重启后仍生效）
- 保留原 SSH 22 放行；更新 `/etc/hosts.allow`、sshd 注释说明用途为会议任务管理
- 本机自检：`http://30.251.255.64/workbench/` 返回 200；与 `.142` 互通正常

### 关键决策和解决方案
- 本机 INPUT 默认已是 ACCEPT，网页本身可达；按运维习惯补显式白名单，避免以后收紧防火墙时误挡
- 未改 nginx / 后端业务白名单（DSH introspect 仍仅 `.132`）；若会议系统要调专用签名接口再另开

### 使用的技术栈
iptables、TCP wrappers（hosts.allow）、nginx（仅验证）

### 修改了哪些文件
- `/etc/iptables/rules.v4`（运行时 iptables 规则）
- `/etc/hosts.allow`
- `/etc/ssh/sshd_config.d/99-security-monitor.conf`（注释）
- `README.md`

## 会话总结（2026-09-28 — 确认 .142 可通过 SSH 访问）

### 会话的主要目的
确认会议任务管理主机 `30.251.255.142` 可通过 SSH 访问本机 `30.251.255.64`。

### 完成的主要任务
- 核对已具备三层放行：iptables（全通 + tcp/22）、`/etc/hosts.allow`（`sshd: 30.251.255.142`）、sshd 监听 `0.0.0.0:22`
- 本机探测 22 端口可达；无 `hosts.deny` / AllowUsers 等额外拦截
- 无需再改配置

### 关键决策和解决方案
- 现状已满足「.142 可通过 SSH 访问」，保持现有规则即可

### 使用的技术栈
iptables、OpenSSH、TCP wrappers

### 修改了哪些文件
- 无业务代码变更；仅追加本 README 记录

---

## 会话总结（2026-09-28 — 智能助手预览文件可下载）

### 会话的主要目的
让智能助手生成的文件在预览时可以直接下载到本地电脑。

### 完成的主要任务
- 助手机 dsh-gate 注入预览「下载到本地」按钮：新增 `team-file-download.js`，首页 HTML 自动挂载
- 点击后调用官方 `workspaceFiles/readAll`，浏览器另存为（受官方约 32MB 上限约束）
- 工作台 nginx 增加 `/__team_file_download.js` 同域反代
- 已验证：脚本可访问、首页已注入、`readAll` 可读出个人目录文件内容

### 关键决策和解决方案
- 下载能力放在助手机门禁层（与用量统计同款补丁方式），不改官方 DSH npm 包
- 预览入口选「标题栏按钮」（方案 A），不改文件树与对话气泡
- 用稳定 DOM 属性 `data-textpreview-path` 定位标题栏，避免依赖会变的 CSS hash

### 使用的技术栈
Node.js（dsh-gate）、浏览器 Fetch/Blob、nginx 反代、Vue 工作台嵌入（未改业务逻辑）

### 修改了哪些文件
- 助手机：`/opt/dsh-gate/team-file-download.js`（新）、`/opt/dsh-gate/server.js`（补丁）、`/opt/dsh-gate/patch_server_download.py`、`/opt/dsh-gate/fix_accept_encoding.py`、`/opt/readme.md`
- 工作台：`deploy/nginx/dsh-embed.conf`、`/etc/nginx/snippets/dsh-embed.conf`、`README.md`
- 未写入正式业务库；无 purge

---

## 会话总结（2026-09-28 — 配置 DeepSeek 搜索钥匙）

### 会话的主要目的
智能助手已配 MiniMax 仍失败：补上网页搜索所需的 DeepSeek API 钥匙。

### 完成的主要任务
- 在助手机 `/root/.dsh/.env` 增加 `DEEPSEEK_API_KEY`（与现有 `MINIMAX_CN_API_KEY` 并列）
- 重启 `dsh-web`、`dsh-gate`，确认进程环境已加载该变量
- 未把密钥写入仓库或说明正文

### 关键决策和解决方案
- MiniMax 只管对话模型；网页搜索工具单独要 DeepSeek 钥匙，二者分开配置
- 密钥只放服务器 `.env`（权限 600），不进 README / git

### 使用的技术栈
systemd（dsh-web / dsh-gate）、DeepSeek Harness 环境变量

### 修改了哪些文件
- 助手机：`/root/.dsh/.env`（及时间戳备份）、服务重启
- 工作台：`README.md`（本总结）

---

## 会话总结（2026-09-28 — 排查 MiniMax TRANSPORT 失败）

### 会话的主要目的
智能助手仍报 `Anthropic stream ended without a stop reason`（TRANSPORT），查明原因并稳住。

### 完成的主要任务
- 核对失败会话：已选 MiniMax-M3，但带 `reasoningEffort: high`；五次重试均 0 token 后 TRANSPORT
- 助手机直连 MiniMax Anthropic 兼容接口（含工具/长 system）当前可正常返回 `end_turn`
- 将默认推理强度改为关闭：`/root/.dsh/settings.yaml` 中 `reasoning: off` 与 `reasoningEffort: off`
- 重启 `dsh-web` / `dsh-gate`

### 关键决策和解决方案
- 报错里的 Anthropic 指协议兼容层，不是换成了 Claude
- 先关 high 推理提高长任务稳定性；请用户新开对话先发短消息验证

### 使用的技术栈
DeepSeek Harness、MiniMax Anthropic-compatible API、systemd

### 修改了哪些文件
- 助手机：`/root/.dsh/settings.yaml`（及备份）
- 工作台：`README.md`（本总结）

---

## 会话总结（2026-09-28 — 修复预览下载识别不到文件）

### 会话的主要目的
预览已打开且有「下载到本地」按钮，但点击提示「还没识别到当前预览的文件」。

### 完成的主要任务
- 修复助手机 `/opt/dsh-gate/team-file-download.js`：优先从预览路径栏 `title`（完整绝对路径）与 `data-textpreview-url` / localStorage 取会话 ID，不再只依赖拦截 fetch

### 关键决策和解决方案
- 官方预览把完整路径放在 `[data-textpreview-path]` 的 `title` 上，比拦截 RPC 更可靠

### 使用的技术栈
浏览器 DOM + workspaceFiles/readAll

### 修改了哪些文件
- 助手机：`/opt/dsh-gate/team-file-download.js`
- 工作台：`README.md`

---

## 会话总结（2026-09-29 — 钉钉 H5 智能助手落地）

### 会话的主要目的
在钉钉手机 H5 落地完整智能助手（对话 + 文件），与 PC 同账号内容一致。

### 完成的主要任务
- 新增 H5 页：`POST /dsh/ticket` 取嵌入地址后 iframe 打开助手
- 底栏增加「助手」入口（仅有 `dsh.access` 时显示）；无权限访问 `/dsh` 回首页
- 退出登录时调用 `POST /dsh/session-end`（失败忽略）
- `web-dingtalk` 生产构建已通过；静态资源可由 `/workbench/m/` 访问
- 未写入正式库造数，未执行 purge

### 关键决策和解决方案
- 不新建同步服务：SSO ticket 带同一 `username`，助手个人工作区 PC/H5 共用
- 后端 ticket/nginx 助手反代复用现有能力，只改 H5 前端

### 使用的技术栈
Vue3、Vue Router、Pinia、Axios、DeepSeek Harness SSO（dsh-gate）

### 修改了哪些文件
- `web-dingtalk/src/views/DshView.vue`（新）
- `web-dingtalk/src/views/MainLayout.vue`
- `web-dingtalk/src/views/ProfileView.vue`
- `web-dingtalk/src/router/index.ts`
- `web-dingtalk/src/stores/auth.ts`
- `web-dingtalk/dist/`（构建产物）
- `README.md`

---

## 会话总结（2026-09-29 — 再查 MiniMax TRANSPORT）

### 会话的主要目的
用户在「会议纪要 Agent 设计方案」长会话里再次出现 `Anthropic stream ended without a stop reason`（TRANSPORT），已切换 MiniMax-M3 仍失败。

### 完成的主要任务
- 核对助手机配置：`reasoning` / `reasoningEffort` 已是 `off`，默认模型 `MiniMax-M3`
- 直连 MiniMax Anthropic 兼容接口：短请求与约 0.5MB 含工具请求均可正常 `end_turn`
- 分析失败会话：同会话内多次 `529 overloaded_error`（高峰），随后大量 0 token 的 TRANSPORT；换 M2.7-highspeed / M3 都救不了该超长上下文

### 关键决策和解决方案
- 根因主要是 MiniMax 高峰过载 + 超长会话上下文，不是工作台 H5 嵌入、也不是默认推理强度
- 处理建议：等 1–5 分钟后**新开对话**，用 `@` 引用工作区已有 `meeting-minutes-agent-design.html/.pdf` 等成品继续改；不要在原会话反复点「继续」
- 未改密钥；未 purge（无正式库造数）

### 使用的技术栈
DeepSeek Harness 会话日志、MiniMax Anthropic-compatible API、ssh 助手机排查

### 修改了哪些文件
- `README.md`（本总结）

---

## 会话总结（2026-09-29 — 长会话 TRANSPORT 加固）

### 会话的主要目的
用户在「会议纪要 Agent 设计方案」里发「测试」仍 TRANSPORT；需给出可落地的稳住办法。

### 完成的主要任务
- 确认失败会话上下文约 100 万字符 / 估 50 万 token，MiniMax 夹杂 529 过载与空流 TRANSPORT
- 短请求 MiniMax / DeepSeek 均正常；问题在超长会话 + MiniMax 高峰，不是 H5
- 助手机 `settings.yaml`：启用 `llm-deepseek`（`deepseek-flash`，推理关）；新对话默认 DeepSeek；MiniMax 重试加到 8 次；保留 MiniMax 可选手动切回
- 重启 `dsh-web` 验证两家接口短请求 OK

### 关键决策和解决方案
- 旧长会话请新开对话或先 `/compact`，并用 `@` 引用已有 html/pdf；不要在原会话硬点「继续」
- 默认改 DeepSeek 降低高峰空流；Token Plan 需要时再切 MiniMax-M3

### 使用的技术栈
DeepSeek Harness settings、MiniMax / DeepSeek API、systemd

### 修改了哪些文件
- 助手机：`/root/.dsh/settings.yaml`（及时间戳备份）
- 工作台：`README.md`


## 会话总结（2026-09-30 — 写周报单条分页）

### 会话的主要目的
优化 PC 周报管理「写周报」：写好一条保存一条，每条单独一页，不再一页堆多张任务卡。

### 完成的主要任务
- 写周报默认改为本周期**条目列表**（已写条数、摘要、编辑入口）
- **新建一条** / 点已有条目进入单卡编辑页（路由 `edit=new` / `edit=下标`）
- **保存本条**：把当前条合并进整份 `items` 再调现有 `POST /progress-reports`，避免清掉其它卡；成功后回列表
- 预填任务改为「待写入建议」，点某一条再进新建页带入，不再一次铺多张大表单
- 粘贴原文、提交整份周报、附件/AI/去新建项目回跳参数一并适配
- 重建 `web-pc/dist`

### 关键决策和解决方案
- 不改后端保存接口：仍整份覆盖；单条保存靠前端合并已有条目
- 列表页统一「提交整份周报」；编辑页只「保存本条」
- 钉钉 H5 本会话未改

### 使用的技术栈
Vue 3、Vue Router query、Element Plus、现有 progress-reports API

### 修改了哪些文件
- `web-pc/src/views/ProgressReportView.vue`
- `web-pc/src/views/ReportManageView.vue`
- `web-pc/src/views/OverviewView.vue`（回跳透传 `edit`）
- `web-pc/dist/`
- `README.md`（本总结）

### 测试数据
- 本会话未写入正式库测试账号/项目，无需 purge


## 会话总结（2026-10-01）— 任务管理选项目支持模糊匹配

### 会话的主要目的
任务管理写任务时，选择项目支持输入关键字模糊匹配，方便项目多时快速定位。

### 完成的主要任务
- PC 写任务页项目下拉启用可搜索、可清空
- 提示文案改为「输入关键字搜索项目」

### 关键决策和解决方案
- 与周报页一致：Element Plus `el-select` 的 `filterable` + `clearable`，按项目名本地包含匹配
- 不改后端（`my_projects` 已一次加载）；钉钉端本会话未改

### 使用的技术栈
Vue 3、Element Plus

### 修改了哪些文件
- `web-pc/src/views/WriteTaskView.vue`
- `README.md`（本总结）

### 测试数据
- 本会话未写入正式库测试账号/项目，无需 purge

## 会话总结（2026-10-01）— 选项目模糊匹配对抗性审计

### 会话的主要目的
对抗性审计：确认写任务「选项目可搜索」是否真正对用户生效，而不是只改了源码。

### 完成的主要任务
- 发现并修复：源码已加 `filterable`，但线上仍下发旧 `dist`（占位符仍是「选择项目」）
- 重建 `web-pc/dist` 并验证用户操作正常（约 66 个项目可搜、可选、可清空）
- 移除调试埋点后再次构建发布

### 关键决策和解决方案
- 根因：只改 Vue 源码未 `npm run build`，nginx `/workbench/` 仍服务旧 hash 资源
- 验收以线上 bundle 文案 + 实际点选为准；不引入自定义 `filter-method`（会绕过 EP 默认过滤）

### 使用的技术栈
Vue 3、Element Plus、Vite build、nginx 静态托管

### 修改了哪些文件
- `web-pc/src/views/WriteTaskView.vue`（功能保留，去掉审计埋点）
- `web-pc/dist/`（重建）
- `README.md`（本总结）

### 测试数据
- 本会话未写入正式库测试账号/项目，无需 purge

## 会话总结（2026-10-01）— 全局可变长下拉统一可搜索

### 会话的主要目的
全项目对齐「可变长选项可关键字模糊匹配」：PC / 钉钉 H5 / 小程序统一体验；固定枚举不加搜索。

### 完成的主要任务
- **PC**：周报工作标签、看板标签筛选、管理后台用户角色补 `filterable`（写任务责任人/辅助人/阶段、任务详情阶段此前已齐）
- **钉钉 H5**：新增 `SearchableSelect`，接入写任务（项目/责任人 + 辅助人面板搜索）、写周报项目、卡点项目、任务详情辅助人/改派
- **小程序**：新增 `searchable-picker`，接入写任务（项目/负责人 + 辅助人筛选）、周报项目、卡点项目、任务详情添加辅助人
- 重建 `web-pc/dist`、`web-dingtalk/dist` 并重启 frontend

### 关键决策和解决方案
- **约定**：选项会变多的下拉必须可搜（名称包含匹配）；级别/类型/状态等固定枚举保持普通下拉
- PC 用 Element Plus `filterable`；钉钉无 EP，自研轻量「输入 + 过滤列表」
- 改前端后必须 `npm run build`，否则 nginx 仍发旧包

### 使用的技术栈
Vue 3、Element Plus、Vite、钉钉 H5 自研组件、钉钉小程序自定义组件

### 修改了哪些文件
- `web-pc/src/views/ProgressReportView.vue`、`ReportBoardView.vue`、`AdminView.vue`（及已有 filterable 的 WriteTask/TaskDetail）
- `web-dingtalk/src/components/SearchableSelect.vue`
- `web-dingtalk/src/views/TaskCreateView.vue`、`ReportView.vue`、`BlockerView.vue`、`TaskDetailView.vue`
- `mp-dingtalk/components/searchable-picker/*`
- `mp-dingtalk/pages/task-create/*`、`report/*`、`blocker/*`、`task-detail/*`
- `web-pc/dist/`、`web-dingtalk/dist/`、`README.md`

### 测试数据
- 本会话未写入正式库测试账号/项目，无需 purge

## 会话总结（2026-10-01）— 项目详情显示全部关联周报

### 会话的主要目的
项目详情「周报」Tab 原先只看本周且仅按已挂/空项目推断过滤，关联周报看不到；需要跨周展示全部关联周报，并标出时间节点。

### 完成的主要任务
- 新增接口 `GET /api/progress-reports/by-project/{project_id}`：跨周拉取与项目相关的已提交周报
- 三种关联都纳入：已挂项目、空/日常任务展示期推断、关联待办任务属本项目
- 按周期时间节点返回；前端纵向时间线展示，条目打标签（已挂项目 / 推断归属 / 任务关联）
- 空状态改为「暂无该项目相关周报」；重建 PC 前端并重启服务

### 关键决策和解决方案
- 不改全员看板 `/board` 口径，避免牵动周报管理页
- 推断仅展示、不写库；已挂其它真实立项的条目不因文案误推，仅任务关联可额外命中
- 匹配逻辑抽到 `project_related_reports.match_item_to_project`，便于单测

### 使用的技术栈
FastAPI、SQLAlchemy、Pydantic、Vue 3、Element Plus、pytest

### 修改了哪些文件
- `backend/app/services/project_related_reports.py`（新建）
- `backend/app/api/progress_reports.py`
- `backend/app/schemas/__init__.py`
- `backend/tests/test_project_related_reports.py`（新建）
- `web-pc/src/views/ProjectDetailView.vue`
- `web-pc/dist/`
- `README.md`（本总结）

### 测试数据
- 本会话仅跑内存单测，未写入正式库测试账号/项目，无需 purge

## 会话总结（2026-10-01）— 成员角色按创建人身份自动赋值

### 会话的主要目的
创建/编辑项目时：项目支撑创建则本人为支撑，项目经理创建则本人为经理；团队规则明确为许金斌、周永祺=项目经理，孙振振=管理员，其余=项目支撑。顺带修复成员角色下拉只显示数字 ID 的问题。

### 完成的主要任务
- 支撑人选规则改为：除项目经理（许金斌/周永祺）与管理员（孙振振）外，其他同事都可当项目支撑（不再依赖钉钉「技术经理」岗位）
- 新建项目：后端按创建人身份自动带上经理或支撑；前端打开弹窗也预填
- 下拉补上当前已选人，历史脏数据也能显示姓名，不再只显示 ID（如 564）
- 更新单测；清理测试账号/项目；重建 PC 前端并重启服务

### 关键决策和解决方案
- 自动赋值在后端兜底（前端未传也会补），避免漏选
- 经理与支撑仍互斥；管理员不默认进角色
- purge 识别补充 `双支撑_` / `经理自建_` 前缀；删账号前需先从「日常任务」等非测试项目解绑成员

### 使用的技术栈
FastAPI、Vue 3、Element Plus、pytest

### 修改了哪些文件
- `backend/app/services/project_roles.py`
- `backend/app/api/projects.py`
- `backend/tests/test_project_members.py`
- `web-pc/src/utils/projectRoles.ts`
- `web-pc/src/views/OverviewView.vue`
- `web-pc/src/views/ProjectDetailView.vue`
- `scripts/purge_test_data.py`
- `web-pc/dist/`、`README.md`

### 测试数据
- 已备份后 purge：删除测试账号约 13+3 个（`pr_` 前缀），测试项目含 `role_`/`新建角色_`/`无经理_`/`双支撑_`/`经理自建_` 及名称含「测试」的「我的测试」；正式员工与正式立项未动

## 会话总结（2026-10-01）— 存量项目成员角色纠偏

### 会话的主要目的
按新规则把历史上错标成「项目经理」的人改成项目支撑，存量数据一次性修好。

### 完成的主要任务
- 先备份正式库，再 dry-run / 正式执行 `scripts/fix_project_roles.py`
- 51 名非法「项目经理」降为「项目支撑」；合法经理（许金斌/周永祺）保留
- 跳过「日常任务」；纠偏后非法经理剩余 0
- 例：实验中学项目现为经理=许金斌、支撑=徐初；家庭医生现为支撑=俞烨涛（不再当经理）

### 关键决策和解决方案
- 非法经理且符合支撑资格 → `lead` 改为 `support`
- 不擅自指定新的项目经理；原仅非法经理的项目经理位可为空
- 降级后已有支撑则不再用归属人重复补支撑

### 使用的技术栈
Python、SQLAlchemy、PostgreSQL

### 修改了哪些文件
- `scripts/fix_project_roles.py`（新建）
- `README.md`（本总结）
- 数据库 `project_members`（存量角色纠偏）

### 测试数据
- 未新增测试账号；操作前已备份 `backups/project_workbench_20261001_155750.sql.gz`

## 会话总结（2026-10-01）— 任务/详情页打开变快

### 会话的主要目的
解决「打开任务与详情页面较慢」的体感问题。

### 完成的主要任务
- 定位主因：任务详情首屏串行等待，且 `GET /tasks/{id}/review` 无缓存时同步调 LLM（最长约 45 秒）
- 后端评价改为：默认先返回规则评价，后台再尝试升级为 AI；`force=true` 仍同步走 LLM
- PC/钉钉任务详情：主任务信息先出，成员/阶段/附件/评价并行加载，不再整页死等
- PC 项目详情：主信息（项目/阶段/卡点/成员/任务）就绪即解除 loading，周报与角色候选人改为后置加载

### 关键决策和解决方案
- 首屏速度优先于「第一次就看到 AI 评价」；用户先看到规则评价，后台升级后下次打开即为 AI
- 不改列表全量分页（另案），本次只打「点开详情慢」这条最痛链路

### 使用的技术栈
FastAPI BackgroundTasks、Vue 3、Element Plus、pytest

### 修改了哪些文件
- `backend/app/services/task_ai_review.py`
- `backend/app/api/tasks.py`
- `web-pc/src/views/TaskDetailView.vue`
- `web-pc/src/views/ProjectDetailView.vue`
- `web-dingtalk/src/views/TaskDetailView.vue`
- `web-pc/dist/`、`web-dingtalk/dist/`（已重建）
- `README.md`（本总结）

### 测试数据
- 未写入测试账号/项目；跑过 `test_member_can_read_own_task_review`（通过）

## 会话总结（2026-10-01）— 管理员可删除任务

### 会话的主要目的
允许「任务创建人」和「管理员」删除任务。

### 完成的主要任务
- 后端删除接口：创建人或 `admin` / `dept_head` 可删（仍须校验登录密码）
- PC / 钉钉任务详情：删除按钮对创建人与管理员显示
- 补充单测：管理员与部门负责人可删他人任务，普通成员仍 403
- 重建前端并重启服务

### 关键决策和解决方案
- 「管理员」沿用现有口径：`admin` + `dept_head`（与 `is_project_manager` 一致）
- 执行人不是创建人时仍不可删

### 使用的技术栈
FastAPI、Vue 3、pytest

### 修改了哪些文件
- `backend/app/api/tasks.py`
- `backend/tests/test_tasks_assign.py`
- `web-pc/src/views/TaskDetailView.vue`
- `web-dingtalk/src/views/TaskDetailView.vue`
- `web-pc/dist/`、`web-dingtalk/dist/`
- `README.md`（本总结）

### 测试数据
- 已备份后 purge：删除测试账号 9 个（`ta_` / `t_task_review`），测试项目 4 个；正式数据未动

## 会话总结（2026-10-01）— 对抗审查两处修复

### 会话的主要目的
修复 Bugbot 对抗审查发现的：任务详情切换竞态、评价 LLM 升级重复触发。

### 完成的主要任务
- PC/钉钉任务详情：增加 `loadSeq`，过期并行请求结果丢弃不写回
- 评价后台升级：`_upgrade_inflight` 去重，同一任务同时只跑一路 LLM
- 保留调试埋点待用户验证后再清理

### 关键决策和解决方案
- 用请求代数防竞态，而不是重新串行化加载
- 进程内 inflight 集合去重（单实例部署足够）

### 使用的技术栈
Vue 3、FastAPI BackgroundTasks

### 修改了哪些文件
- `web-pc/src/views/TaskDetailView.vue`
- `web-dingtalk/src/views/TaskDetailView.vue`
- `backend/app/services/task_ai_review.py`
- `backend/app/api/tasks.py`
- `web-pc/dist/`、`web-dingtalk/dist/`
- `README.md`

### 测试数据
- 已备份后 purge 本轮单测账号/项目（`ta_` / `t_task_review`）

## 会话总结（2026-10-01）— 系统管理：通知日志与通知模板

### 会话的主要目的
在系统管理中新增通知管理能力：查看发送日志，并配置通知格式、渠道与频率；同时落地 PRD 三条定时提醒（卡点/阶段/周报）。

### 完成的主要任务
- 新增表 `notification_templates`、`notification_delivery_logs` 与迁移
- 权限点 `admin.notify`（默认给系统管理员）
- 统一发送管道：读模板 → 站内+钉钉 → 写发送日志；任务通知改走管道
- 定时扫描：卡点跟进逾期、阶段逾期、周报未交（进程内按分钟匹配 cron）
- 系统管理两个页签：通知日志、通知模板（可编辑文案/渠道/频率，可手动触发扫描）
- 单测覆盖模板 API、发送日志、禁用跳过；修复交办单测 mock 路径
- 前端已重新构建；后端已重启

### 关键决策和解决方案
- 渠道本期仅站内推送 + 钉钉工作通知
- 即时通知不受安静时段限制；定时类默认 22:00–08:00 安静，且每日上限 1
- 模板按 event_key 仅首次种子插入，不覆盖管理员已改配置
- 删除用户时发送日志 `user_id` 置空，避免挡清理

### 使用的技术栈
FastAPI、SQLAlchemy、asyncio 调度、Vue3、Element Plus、钉钉工作通知、pytest

### 修改了哪些文件
- 规格/计划：`docs/superpowers/specs/2026-10-01-notification-admin-design.md`、`docs/superpowers/plans/2026-10-01-notification-admin.md`
- 后端：`models/__init__.py`、`migrations.py`、`schemas`、`permissions.py`、`init_service.py`、`notify_pipeline.py`、`notification_templates_seed.py`、`notify_scheduler.py`、`task_notify.py`、`reminders.py`、`api/admin.py`、`api/progress_reports.py`、`main.py`、`user_sync.py`
- 前端：`web-pc/src/views/AdminView.vue`、`web-pc/dist/`
- 测试：`tests/test_notify_admin.py`、`tests/test_tasks_assign.py`
- `README.md`

### 测试数据
- 已备份后 purge：本轮删除测试账号 6 个（`t_notify_*` / `ta_*`）、测试项目 2 个；此前另清理一批联调残留（约 39 账号 / 11 项目）；正式业务数据未动

## 会话总结（2026-10-01）— 合并重复项目「电子政务网大运维三期」

### 会话的主要目的
列表出现两条同名项目，合并为一条，避免进度与周报分散。

### 完成的主要任务
- 备份后将项目 `393`（负责人孙振振，1 个任务）并入 `217`（负责人凌亮，原 7 个任务）
- 任务「大运维三期需求方案整理优化」迁入保留项目并挂到「需求分析」阶段
- 孙振振加入保留项目成员；删除空壳项目 393
- 写审计日志 `merge_project`

### 关键决策和解决方案
- 保留数据更全的一条（凌亮 / 217）：任务多、有周报
- 阶段名称不完全一致时按同义映射到保留项目阶段，不另复制一套阶段

### 使用的技术栈
PostgreSQL、SQLAlchemy、既有 `delete_project_cascade`

### 修改了哪些文件
- 仅数据库数据变更（无代码改动）；`README.md` 追加本总结
- 备份：`backups/project_workbench_20261001_212342.sql.gz`

### 测试数据
- 未造测试数据；操作的是正式同名立项合并，正式其它数据未删

## 会话总结（2026-10-03）— 扩展智能助手：分析管控 + 助手串联（轻量考核）

### 会话的主要目的
扩展智能助手使用范围：分析整理项目/任务/周报并串联，控制进度与质量，对相关人员做轻量考核指导（不做正式打分）；双入口（分析管控页 + 智能助手），仅管理员可用。

### 完成的主要任务
- 新增权限 `assist.analyze`，仅绑定系统管理员
- 后端编排服务 `assist_analyze`：团队 / 项目 / 人员分析、催办清单、指导备注落库
- PC「分析管控」页三 Tab；可复制提问稿并带到智能助手
- DSH `introspect` 增加 `can_analyze`；签名只读上下文接口 `/api/dsh/context/*`
- 「看评价」展示本人收到的指导
- 单测 4 项通过；PC 前端已构建发布；清理测试账号

### 关键决策和解决方案
- 产品定位为轻量版：结论 + 指导话术 + 催办清单，无分数/等级
- 分析能力与通用聊天分离：普通成员仍可用 DSH，但读不到业务分析接口
- 助手侧暂用「提问稿复制」兜底（门禁预填对话若未就绪仍可用）
- AI purpose `assist_analyze`，无模型时规则拼装结论

### 使用的技术栈
FastAPI、SQLAlchemy、Vue3、Element Plus、既有 `ai_platform` LLM 路由、pytest

### 修改了哪些文件
- 后端：`rbac/permissions.py`、`init_service.py`、`ai_platform.py`、`models/__init__.py`、`migrations.py`、`schemas/__init__.py`、新 `services/assist_analyze.py`、`api/ai_assist.py`、`api/personal.py`、`api/dsh.py`、`tests/test_assist_analyze.py`
- 前端 PC：`AnalyzeControlView.vue`（新）、`MainLayout.vue`、`router/index.ts`、`DshView.vue`、`MyReviewsView.vue`
- `README.md`（本总结）；备份 `backups/project_workbench_20261003_110747.sql.gz`

### 测试数据
- 已备份后 purge：删除测试账号 1 个（`t_af_analyze_m`）、测试项目 1 个（名称含「测试」）；正式业务数据未动

## 会话总结（2026-10-03）— 修复智能助手 nginx：open-in-app 404 / plugins 流式中断

### 会话的主要目的
排查控制台报错：`/open-in-app/apps` 404、`/plugins/events` `ERR_INCOMPLETE_CHUNKED_ENCODING`。

### 完成的主要任务
- 确认助手上游（3080）本身有这些路径；本机 nginx 未转发 `/open-in-app`，`/plugins` 未关闭缓冲
- 更新 `deploy/nginx/dsh-embed.conf` 并同步到 `/etc/nginx/snippets/dsh-embed.conf`，已 `nginx reload`
- `install-nginx.sh` 增加同步 dsh-embed 片段，避免以后安装丢配置

### 关键决策和解决方案
- 新增 `location ^~ /open-in-app` 反代到助手门禁
- `/plugins` 增加长超时 + `proxy_buffering off`，避免事件流被截断
- 本机验证：`/open-in-app/apps` 由 404 变为 302（与上游一致）

### 使用的技术栈
nginx 反代

### 修改了哪些文件
- `deploy/nginx/dsh-embed.conf`、`/etc/nginx/snippets/dsh-embed.conf`、`scripts/install-nginx.sh`、`README.md`

### 测试数据
- 无

## 会话总结（2026-10-03）— 分析管控改为流式输出

### 会话的主要目的
分析管控页面输出过慢，改为流式输出，先出骨架再边生成结论。

### 完成的主要任务
- 后端 SSE：`/api/ai/analyze/{team|project|person}/stream`（`start` → `partial` → `narrative_start`/`delta` → `done`）
- 先推规则统计与催办清单，再流式写 AI 结论
- PC「分析管控」页用 fetch 读 SSE，结论区打字机效果
- nginx `/workbench/api/ai/analyze/` 关缓冲、加长超时
- 单测覆盖流式 `partial` 事件

### 关键决策和解决方案
- 复用 `run_text_stream`；白话格式（结论 +「- 关注点」）便于边生成边展示
- 非流式接口仍保留，供 DSH 上下文等同步调用

### 使用的技术栈
FastAPI SSE、httpx stream、Vue3 fetch ReadableStream、nginx

### 修改了哪些文件
- `backend/app/services/assist_analyze.py`、`backend/app/api/ai_assist.py`、`backend/tests/test_assist_analyze.py`
- `web-pc/src/views/AnalyzeControlView.vue`
- `deploy/nginx/workbench.conf`、`/etc/nginx/snippets/workbench.conf`
- `README.md`

### 测试数据
- 已备份后 purge：删除测试账号 1 个（`t_af_analyze_m`）、测试项目 1 个；正式业务数据未动

## 会话总结（2026-10-03）— 案例对照补强：归因 / 催办闭环 / 数据分析

### 会话的主要目的
对照飞书项目、Asana、PingCode 等同类产品，把「管理一目了然 + 分清人的问题还是其他问题」等缺口补齐到可落地能力。

### 完成的主要任务
- **P0 卡点归因看板**：总览新增按责任方类型（第三方/客户/内部/审批）汇总与明细，重点项目高亮；接口 `GET /api/projects/overview/blocker-attribution`
- **P0 问题性质 + 三问摘要**：分析管控每条催办/关注点带 `issue_nature`（person/external/process/unclear）；顶部 snapshot 回答是否按时 / 最大风险 / 需要决策
- **P0 权限**：部门负责人默认具备 `assist.analyze`；管理员补上 `stats.view`；启动 `init_rbac` 只增不删补绑
- **P1 反馈闭环**：分析页「催一下」→ `POST /api/ai/analyze/nudge`；指导已读 / 我已改进（`read_at` / `improved_at`）；总览置顶重点项目与重点任务
- **P2 数据分析页**：`GET /api/stats/people|projects` + PC「数据分析」侧栏（`stats.view`）

### 关键决策和解决方案
- 不做正式打分；归因用既有 `party_type` + 规则标签，避免引入重考核
- 驾驶舱「三问」并入分析管控 snapshot，不另开大模块
- 一键催办：待接收任务复用 `notify_task_remind`，其余走站内通知

### 使用的技术栈
FastAPI、SQLAlchemy、Vue3、Element Plus、pytest

### 修改了哪些文件
- 后端：`assist_analyze.py`、`project_service.py`、`ai_assist.py`、`personal.py`、`projects.py`、`permissions.py`、`init_service.py`、`migrations.py`、`models/__init__.py`、`schemas/__init__.py`、`main.py`；新增 `stats_service.py`、`api/stats.py`；`tests/test_assist_analyze.py`
- 前端：`OverviewView.vue`、`AnalyzeControlView.vue`、`MyReviewsView.vue`、`MainLayout.vue`、`router/index.ts`；新增 `StatsView.vue`
- `README.md`

### 测试数据
- 已备份后 purge：删除测试账号 2 个（`t_af_analyze_m`、`t_af_analyze_dh`）、测试项目 1 个；正式业务数据未动
- 单测：`tests/test_assist_analyze.py` 非流式用例 6 passed

## 会话总结（2026-10-03）— 对抗性审查缺陷修复

### 会话的主要目的
对案例补强改动做对抗性审查后，按 P1/P2 直接修复。

### 完成的主要任务
- 催办：仅允许白名单类型；周报名册/任务/卡点校验；通知标题固定防钓鱼
- 人员 snapshot：识别 `this_week_submitted`；人员维缺周报即可偏黄
- 内部卡点 `party_type=internal` → `issue_nature=person`
- 指导话术不再吞并状态复读类 focus
- 归因/统计改为轻量 `list_visible_project_rows`，避免整表 `list_projects` N+1

### 关键决策和解决方案
- 以运行时日志验证：未知催办类型 400；internal→person；人员缺周报偏黄
- 调试埋点暂留在 `assist_analyze.py`（`_dbg`），确认后再清

### 使用的技术栈
FastAPI、pytest、NDJSON 调试日志

### 修改了哪些文件
- `backend/app/services/assist_analyze.py`、`project_service.py`、`stats_service.py`
- `backend/tests/test_assist_analyze.py`
- `README.md`

### 测试数据
- 未新增共享库脏数据；单测用临时账号规则内清理流程沿用既有 purge 习惯

## 会话总结（2026-10-03）— 数据分析并入分析管控

### 会话主要目的
- 侧栏只留「分析管控」一个入口，把原「数据分析」做成同页「数据看板」Tab，避免两处分头看

### 完成的主要任务
- 抽出 `StatsBoardPanel`，迁入原 StatsView 人员/项目表与卡点分布
- `AnalyzeControlView` 增加「数据看板」Tab；按 `assist.analyze` / `stats.view` 裁切 Tab；支持 `?tab=stats`
- 侧栏去掉「数据分析」；有任一权限即显示「分析管控」
- `/stats` 重定向到 `/analyze?tab=stats`；路由守卫改为两权限都没有才踢回
- 删除 `StatsView.vue`；清理 `assist_analyze.py` 调试 `_dbg` 埋点

### 关键决策和解决方案
- Tab 分离、API 不变：结论流与轻量统计不一次加载，避免过重
- 仅有 `stats.view` 时默认进数据看板并提示结论 Tab 未开放

### 使用的技术栈
- Vue3 + Element Plus、Vue Router、FastAPI（未改 stats API）

### 修改了哪些文件
- 新增：`web-pc/src/components/analyze/StatsBoardPanel.vue`
- 修改：`web-pc/src/views/AnalyzeControlView.vue`、`web-pc/src/layouts/MainLayout.vue`、`web-pc/src/router/index.ts`、`backend/app/services/assist_analyze.py`、`README.md`
- 删除：`web-pc/src/views/StatsView.vue`

### 测试数据
- 本会话未写入共享库测试账号/项目，无需 purge

## 会话总结（2026-10-03）— 项目总览重点排前

### 会话主要目的
- 项目管理总览列表里，重点项目固定排在前面

### 完成的主要任务
- 修正 `list_projects` 排序：原先仍按旧分级 `red/yellow/green`，与现行 `key/normal/daily` 对不上，导致重点不靠前
- 改为 `key → normal → daily`，同级再按最近更新时间

### 关键决策和解决方案
- 只改后端列表排序（总览表格直接用 `/api/projects`），不改前端二次排序

### 使用的技术栈
- FastAPI / SQLAlchemy（PostgreSQL `array_position`）

### 修改了哪些文件
- `backend/app/services/project_service.py`
- `README.md`

### 测试数据
- 未写入测试账号/项目，无需 purge

## 会话总结（2026-10-03）— 任务管理列表排序

### 会话主要目的
- 任务管理列表把更该先看的任务顶到前面

### 完成的主要任务
- `list_related_tasks` 默认排序改为：逾期（未完成且截止日已过）→ 重点（P0/P1）→ 最近更新 → 截止日期 / id

### 关键决策和解决方案
- 采用「先催最急」：逾期优先于重点；「最近」按 `updated_at`；仅改任务管理相关列表 API，个人/部门待办排序暂不动

### 使用的技术栈
- FastAPI / SQLAlchemy `case` 排序

### 修改了哪些文件
- `backend/app/services/task_service.py`
- `README.md`

### 测试数据
- 未写入测试账号/项目，无需 purge

## 会话总结（2026-10-03）— 任务列表排序修正（重点不被逾期淹没）

### 会话主要目的
- 用户反馈任务管理排序「没变化」：首屏仍全是 P3

### 完成的主要任务
- 根因：未完成里逾期普通任务很多，原先「逾期优先于重点」会把 P0/P1 压到几十条之后
- 调整为：逾期且重点 → 重点 → 逾期 → 最近更新 → 截止日期

### 关键决策和解决方案
- 保留「逾期重点最优先」，同时让未逾期的重点也顶到前面，避免被大量逾期普通任务淹没

### 修改了哪些文件
- `backend/app/services/task_service.py`、`README.md`

## 会话总结（2026-10-03）— 任务管理显示所属项目

### 会话主要目的
- 任务管理列表需要一眼看到任务属于哪个项目

### 完成的主要任务
- `TaskAssignPanel`：在「任务管理 / related」模式也显示「项目」列（接口本就有 `project_name`，原先仅个人/部门待办显示）

### 关键决策和解决方案
- 项目详情内任务列表仍不显示项目列（当前页已在该项目下，避免重复）

### 使用的技术栈
- Vue3 + Element Plus

### 修改了哪些文件
- `web-pc/src/components/TaskAssignPanel.vue`、`README.md`

### 测试数据
- 未写入测试账号/项目，无需 purge

## 会话总结（2026-10-03）— 看任务时突出所属项目

### 会话主要目的
- 打开任务时能一眼看清这条任务属于哪个项目

### 完成的主要任务
- 任务详情页顶部增加「所属项目」醒目条，可点进项目页
- 任务管理列表把「所属项目」列挪到最前，列名更明确

### 关键决策和解决方案
- 项目从拥挤的 meta 小字里拆出，单独成条；列表列顺序：项目 → 任务

### 修改了哪些文件
- `web-pc/src/views/TaskDetailView.vue`、`web-pc/src/components/TaskAssignPanel.vue`、`README.md`

## 会话总结（2026-10-03）— 对齐周报→任务：总结已完成 / 仅下一步待办

### 会话主要目的
- 用户确认：从周报来的总结性任务应为已完成；只有「下一步计划」才是未完成待办。按选项 2 排查并对齐

### 完成的主要任务
- 根因 1：部分「下一步」只是复述本周结果或占位文案（如「原文未写下周计划」），却建成未完成待办，看起来像总结却挂在未完成
- 根因 2：同步查重未带 `source_report_id`，不同周报的 `idx:N` 会互相抢同一条任务；回填时曾污染数据，已从备份恢复后按修复逻辑重同步
- 规则：`should_skip_next_plan_item` 跳过空壳/复述；已有错误未完成则收成已完成
- 历史回填：`report_done` 补齐；列表增加「周报总结 / 周报下一步」来源标签

### 关键决策和解决方案
- 真正下一步仍为 `report_next`+未完成；「做成了什么」为 `report_done`+已完成
- 查重必须限定本周报 `source_report_id`，同 ref 多条时收口

### 使用的技术栈
- FastAPI / SQLAlchemy、Vue3、pytest、pg_dump 备份恢复

### 修改了哪些文件
- `backend/app/services/report_next_plan.py`
- `backend/tests/test_report_task_titles.py`
- `web-pc/src/components/TaskAssignPanel.vue`
- `README.md`

### 测试数据
- 已备份后 purge：删除测试账号约 3 个（`t_af_*` / `t_nextplan`）、测试项目约 2 个；未动正式业务数据与「日常任务」

## 会话总结（2026-10-03）— 项目/任务/周报/待办同类项确认

### 会话主要目的
- 确认项目、任务、周报、待办是否存在概念重叠或库内真重复

### 完成的主要任务
- 审计结论已交付（本轮按计划不改代码、不合并数据）
- 结论要点：
  1. 任务 ≈ 待办（同一张 `tasks`；待办多指未完成）
  2. 周报 ≠ 任务；提交后衍生 `report_done`（总结·已完成）与 `report_next`（下一步·未完成），成对属设计
  3. 真重复很少：同人同标题未完成待办约 3 组；同名项目约 2 组（营业厅装修、智安校园电子学生证项目）

### 关键决策和解决方案
- 本轮只确认不清理；若以后清理上述少量真重复，须先备份并 dry-run

### 使用的技术栈
- 只读 SQL 审计（PostgreSQL）

### 修改了哪些文件
- `README.md`（本条总结）

### 测试数据
- 未写入/未 purge；未改正式业务数据

## 会话总结（2026-10-03）— 合并清理同名重复项目

### 会话主要目的
- 合并审计中发现的 2 组同名重复项目

### 完成的主要任务
- 先备份 `project_workbench_20261003_140245.sql.gz`
- 「营业厅装修」：保留 #127（成员/任务更多），并入 #222；保留侧升级为重点 `key`；迁移任务 3、周报条目 1
- 「智安校园电子学生证项目」：保留 #204，并入 #269；迁移任务 1、周报条目 1、成员 +2
- 阶段按名称映射后改挂；删除重复项目后库内同名重复为 0

### 关键决策和解决方案
- 保留较早、引用更多的一侧；阶段按同名映射，避免任务 phase 悬空

### 使用的技术栈
- PostgreSQL / SQLAlchemy 一次性合并脚本

### 修改了哪些文件
- 仅数据库数据；`README.md`（本条）

### 测试数据
- 未新增测试账号；未 purge；未动「日常任务」与其它正式立项

## 会话总结（2026-10-03）— 项目管理页去掉钉选明细、点击重点项目看列表

### 会话主要目的
- 两张「重点项目 / 重点任务」明细卡片不再罗列；点统计卡「重点项目」弹出列表

### 完成的主要任务
- 移除顶部 pin 双栏明细
- 「重点项目」统计卡可点击，弹窗列出重点项目（点行进详情）
- 重点任务仍走原有「点击查看」弹窗

### 修改了哪些文件
- `web-pc/src/views/OverviewView.vue`、`README.md`

## 会话总结（2026-10-03）— 项目管理总览筛选与统计卡点通

### 会话主要目的
- 按已确认计划：总览轻量优化（统计卡点通、状态/负责人筛选）；不改项目挂周报/卡点/任务

### 完成的主要任务
- 「日常项目」统计卡可点 → 列表筛 `level=daily` 并滚到表格
- 「待处理卡点」统计卡可点 → 滚到卡点归因区（无数据则提示）
- 工具栏新增「状态」「负责人」筛选；后端 `/api/projects` 增加 `status` 查询参数（`owner_id` 原本已有）
- 周报/卡点/任务相关列与挂接逻辑未改

### 关键决策和解决方案
- 负责人下拉从历次列表结果累积，首屏带筛选时再静默拉一次全量补全选项
- 卡点点通只做定位，不改归因/挂接数据结构

### 使用的技术栈
- Vue3 + Element Plus；FastAPI；pytest

### 修改了哪些文件
- `web-pc/src/views/OverviewView.vue`
- `backend/app/api/projects.py`
- `backend/app/services/project_service.py`
- `backend/tests/test_project_list_filters.py`（新增）
- `scripts/purge_test_data.py`（识别 `pf_` / `pf_proj_`）
- `README.md`（本条）

### 测试数据
- 先备份 `project_workbench_20261003_141736.sql.gz`
- 清理本轮筛选单测账号 3、项目 3（`pf_*` / `pf_proj_*`）；并清理遗留假项目「我的测试」1 个
- 未动「日常任务」与正式业务立项

## 会话总结（2026-10-03）— 任务列表搜索栏与分页优化

### 会话主要目的
- 优化任务管理「任务列表」工具栏：去写任务按钮与顶部状态分段，状态下拉默认全部，并加分页

### 完成的主要任务
- 去掉列表上方「未完成/已完成/全部」分段与工具栏「写任务」按钮（顶部 Tab「写任务」保留）
- 工具栏增加状态下拉（未完成/已完成，清空=全部），默认全部
- `/api/tasks/related` 改为分页响应 `TaskListOut`，默认 `status=all`、`page_size=10`，前端可选 10/50/100
- 更新相关单测并补充分页用例

### 关键决策和解决方案
- 分页对齐方案库：`items/total/page/page_size`；排序规则保持逾期+重点优先
- 可见性单测改为按标题搜索 + `page_size=100`，避免默认 10 条漏检

### 使用的技术栈
- Vue3 + Element Plus；FastAPI；pytest

### 修改了哪些文件
- `web-pc/src/views/TaskManageView.vue`
- `web-pc/src/components/TaskAssignPanel.vue`
- `backend/app/schemas/__init__.py`
- `backend/app/api/tasks.py`
- `backend/app/services/task_service.py`
- `backend/tests/test_tasks_assign.py`
- `README.md`（本条）

### 测试数据
- 先备份 `project_workbench_20261003_142748.sql.gz`
- purge：测试账号 26、测试项目 9（含本轮 `ta_*` / `proj_*` 及遗留「我的测试」）
- 未动「日常任务」与正式业务立项

## 会话总结（2026-10-03）— 项目管理列表分页（对齐任务管理）

### 会话主要目的
- 项目列表按任务管理同款逻辑分页：默认每页 10，可选 50/100，可翻页

### 完成的主要任务
- `GET /api/projects` 改为返回 `ProjectListOut`（`items/total/page/page_size`），默认 `page_size=10`
- 总览表格底部加分页；筛选/查询回第 1 页
- 周报、分析管控、钉钉项目列表等调用方改为读 `items`，下拉拉取用 `page_size=100`
- 补充分页单测；备份后 purge 本轮测试数据

### 关键决策和解决方案
- 内部 `list_projects` 仍可无分页拉全量（统计等）；对外 API 走 `list_projects_page`
- 不改项目挂周报/卡点/任务逻辑

### 使用的技术栈
- Vue3 + Element Plus；FastAPI；pytest

### 修改了哪些文件
- `backend/app/schemas/__init__.py`、`backend/app/api/projects.py`、`backend/app/services/project_service.py`
- `backend/tests/test_project_list_filters.py`、`backend/tests/test_project_visibility_edit.py`
- `web-pc/src/views/OverviewView.vue`、`AnalyzeControlView.vue`、`ProgressReportView.vue`
- `web-dingtalk/src/views/ProjectListView.vue`、`HomeView.vue`
- `README.md`（本条）

### 测试数据
- 先备份 `project_workbench_20261003_152314.sql.gz`
- purge：测试账号 8、测试项目 17（`pf_*` / `pv_*` / `pf_proj_*` /「我的测试」等）
- 未动「日常任务」与正式业务立项

## 会话总结（2026-10-03）— 周报切换周期显示无数据周

### 会话主要目的
- 周报看板「切换周期」：无提交的周期也要显示并可点选

### 完成的主要任务
- `GET /progress-reports/board` 的 `available_periods` 改为近 N 周日历（含 0 人），并合并库中已有提交周期
- 仅在未显式传 `period_start` 时，才自动切到最近有数据周；点选空周会停留
- PC / 钉钉文案改为「切换周期」
- 补单测 `test_board_available_periods_include_empty_weeks`

### 关键决策和解决方案
- 空周用标准周一～周日补齐；有提交的非对齐周期仍追加进列表
- 不改周报挂项目/任务逻辑

### 使用的技术栈
- FastAPI；Vue3；pytest

### 修改了哪些文件
- `backend/app/api/progress_reports.py`
- `backend/tests/test_report_workbench.py`
- `web-pc/src/views/ReportBoardView.vue`
- `web-dingtalk/src/views/ReportBoardView.vue`
- `README.md`（本条）

### 测试数据
- 未写库造数；单测复用 `wb_mgr` / roster 账号，未 purge 正式业务数据

## 会话总结（2026-10-03）— 切换周期从 9 月 1 日起

### 会话主要目的
- 周报看板可选周期起点改为从 9 月 1 日开始（含无数据周）

### 完成的主要任务
- `available_periods` 改为从当年 9/1（未到 9 月则上一年 9/1）到本周/下周
- 去掉原先「近 12 周截断」限制
- PC/钉钉文案注明「自 9 月 1 日起」；单测同步

### 关键决策和解决方案
- 含 9/1 所在自然周（周一可能早于 9/1）

### 使用的技术栈
- FastAPI；Vue3；pytest

### 修改了哪些文件
- `backend/app/api/progress_reports.py`
- `backend/tests/test_report_workbench.py`
- `web-pc/src/views/ReportBoardView.vue`
- `web-dingtalk/src/views/ReportBoardView.vue`
- `README.md`（本条）

### 测试数据
- 未额外造数；未动正式业务数据

## 会话总结（2026-10-03）— 全局安装 Answer me with HTML

### 会话主要目的
- 给当前 Cursor Agent 全局安装 `answer-me-with-html` skill，并生成一页「TCP 三次握手」解释页做验收

### 完成的主要任务
- 用 `-a cursor` 全局安装 skill（本机是 Cursor，不是 Claude Code）
- 直连 GitHub `git clone` 会卡住，改用 zip 下载后 `npx skills add` 本地路径安装
- 按 SKILL.md：只写 Markdown 内容稿，用 `scripts/am.mjs render` 出单文件 HTML
- 生成成功：7 个面板、时序图 + 状态流图，STE 0 条警告

### 关键决策和解决方案
- Agent 标识用 `cursor`（CLI 示例：`--agent claude-code cursor`）
- 安装落点：`~/.agents/skills/answer-me-with-html`，并复制到 `~/.cursor/skills/answer-me-with-html`
- 页面路径：`/root/.answer-me-with-html/pages/TCP-三次握手-20261003-163353.html`
- 内置浏览器禁 `file://` 且访问不了本机临时 HTTP，用读 HTML / curl 验收，未把试页写进业务仓库

### 使用的技术栈
- skills.sh CLI（`npx skills add`）；Node.js `am.mjs`；扩展 Markdown（sequence / flow / callout）

### 修改了哪些文件
- 全局 skill：`~/.agents/skills/answer-me-with-html/`、`~/.cursor/skills/answer-me-with-html/`
- 生成页：`~/.answer-me-with-html/pages/TCP-三次握手-20261003-163353.html`
- `README.md`（本条）

### 测试数据
- 未写库、未造测试账号；未动正式业务数据

## 会话总结（2026-10-03）— 允许浏览器打开 answer-me HTML 页

### 会话主要目的
- 让 `http://30.251.255.64/root/.answer-me-with-html/pages/TCP-三次握手-….html` 可在浏览器打开

### 完成的主要任务
- nginx 增加只读 location：`/root/.answer-me-with-html/` → `/var/www/answer-me-with-html/`
- 用 bind mount 把 `/root/.answer-me-with-html` 挂到 `/var/www/...`（不放开整个 `/root`）
- systemd 开机自动挂载；`curl` 验证该 URL 返回 200

### 关键决策和解决方案
- `/root` 为 700，直接 alias 源路径时 `www-data` 进不去；也不宜 `chmod o+x /root`（会暴露其它可读文件）
- 对外只暴露 answer-me 生成页目录；配置写入 `deploy/nginx/`，避免重装 nginx 丢失

### 使用的技术栈
- nginx；systemd bind mount；curl 验收

### 修改了哪些文件
- `deploy/nginx/answer-me-with-html.conf`（新建）
- `deploy/nginx/site-80.conf`
- `scripts/install-nginx.sh`
- `/etc/nginx/snippets/answer-me-with-html.conf`、`sites-enabled/talent-profile.conf`
- systemd：`var-www-answer\x2dme\x2dwith\x2dhtml.mount`
- `README.md`（本条）

### 测试数据
- 未写库；未动正式业务数据

## 会话总结（2026-10-03）— 关闭 answer-me HTML 对外映射

### 会话主要目的
- 关闭上一轮开通的 `http://…/root/.answer-me-with-html/…` nginx 映射

### 完成的主要任务
- 去掉 site 中的 answer-me include，删除 snippet，reload nginx（该 URL 已 404）
- 停止并删除 bind mount 的 systemd unit，卸载 `/var/www/answer-me-with-html`
- 从 `install-nginx.sh` / `site-80.conf` 去掉相关配置

### 关键决策和解决方案
- 本地生成页文件仍保留在 `~/.answer-me-with-html/pages/`，只是不再经 80 端口对外提供

### 使用的技术栈
- nginx；systemd

### 修改了哪些文件
- `deploy/nginx/site-80.conf`
- `scripts/install-nginx.sh`
- 删除 `deploy/nginx/answer-me-with-html.conf`
- live：`/etc/nginx/sites-enabled/talent-profile.conf`、去掉 snippet 与 mount unit
- `README.md`（本条）

### 测试数据
- 未写库；未动正式业务数据

## 会话总结（2026-10-04）— DSH Agent Native：一人一助手

### 会话的主要目的
把工作台做成 **DSH AI Native**：每人一个助手当主入口，项目/周报/任务/案例由助手办事；账本仍在 PostgreSQL。不要两套 AI。试验系统独立放在 30.251.255.142，不迁 64 正式数据。

### 完成的主要任务
- 写明规格与四技能说明（每人一助手、工具门禁、习惯催办、改派须确认）
- 新增签名工具：`POST /api/dsh/tools/*`（待办/周报/项目/案例、排序催办、改派草稿）
- 案例检索只查库内关键词，不调用工作台另一套模型
- 登录后默认进「我的助手」；钉钉底栏助手置顶
- 142 本机独立库 `project_workbench_agent` + 演示种子；接口听本机 8100
- 单测 7 项在本机与 142 均通过

### 关键决策和解决方案
- 智能只发生在 DSH 对话；工作台工具只给事实和确认写库
- 142 装依赖时外网解析失败，改为拷贝 Python 3.12 与现成依赖，不把 64 的环境文件和备份拷过去
- 改派必须确认；交办仍不可拒绝

### 使用的技术栈
FastAPI、SQLAlchemy、PostgreSQL、Vue3、pytest、rsync

### 修改了哪些文件
- 规格：`docs/superpowers/specs/2026-10-04-dsh-agent-native-design.md`、`docs/dsh-skills/`、`backend/app/agent_skills/`
- 后端：`models`、`migrations`、`api/dsh.py`、`services/agent_native.py`、`agent_skills.py`、`task_notify.py`、`tests/test_dsh_agent_native.py`
- 前端：`web-pc` 路由与侧栏、`web-dingtalk` 路由与底栏
- 脚本：`scripts/assert_isolated_db.py`、`bootstrap_agent_native.sh`、`deploy_agent_native_142.sh`
- `README.md`（本条）

### 测试数据
- 64：先备份 `backups/project_workbench_20261004_223043.sql.gz`，再清理测试账号 39 个（`t_ag_*`）、测试项目 13 个；正式业务数据未删
- 142：独立库保留演示账号，另清 `t_ag_*`；未导入 64 正式备份

## 会话总结（2026-10-05）— 142 本机安装 DSH

### 会话的主要目的
在 30.251.255.142 安装 DeepSeek Harness，只听本机，不连现网助手机。

### 完成的主要任务
- 在 64 下载 npm 包后拷到 142 `/opt/dsh-offline`（避免 142 在线安装过慢）
- 启用 systemd `dsh-web`，监听 `127.0.0.1:3080`（无口令访问返回 401 属门禁正常）
- 工作台 `.env` 指向本机 `DSH_INTERNAL_URL=http://127.0.0.1:3080`

### 关键决策和解决方案
- 不从现网 132 拷模型钥匙；需在 142 的 `/root/.dsh/.env` 自行填写后 `systemctl restart dsh-web`

### 使用的技术栈
Node.js、@deepseek-ai/dsh 0.2.0-rc.2、systemd、rsync

### 修改了哪些文件
- `scripts/install_dsh_on_142.sh`；142 本机 `/opt/dsh-offline`、`/etc/systemd/system/dsh-web.service`、`/root/.dsh/.env`（空钥匙模板）
- `README.md`（本条）

### 测试数据
- 未写业务库


## 会话总结（2026-10-05）— 把 Cursor 插件直接拷到 142

### 会话的主要目的
142 在线装 Cursor 插件太慢，改为从现网工作台机器直接复制。

### 完成的主要任务
- 拷贝 Cursor 扩展：中文语言包、Claude Code
- 补齐公开插件缓存（9 个）和 Agent 技能目录（31 项）
- 未走应用市场在线安装

### 关键决策和解决方案
- 用 rsync 局域网拷贝，避免 142 拉外网
- 不拷密钥类配置

### 使用的技术栈
rsync、Cursor Remote Server 扩展目录

### 修改了哪些文件
- 仅 142 本机 `~/.cursor-server/extensions`、`~/.cursor/plugins`、`~/.cursor/skills-cursor`；仓库代码未改业务
- `README.md`（本条）

### 测试数据
- 未写业务库

## 会话总结（2026-10-05）— 142 本机安装 DSH

### 会话的主要目的
在 30.251.255.142 安装 DeepSeek Harness，只听本机，不连现网助手机。

### 完成的主要任务
- 在工作台机器下载 npm 包后拷到 142 `/opt/dsh-offline`
- 启用 systemd `dsh-web`，监听 `127.0.0.1:3080`
- 工作台配置指向本机助手地址

### 关键决策和解决方案
- 不从现网拷模型钥匙；需在 142 `/root/.dsh/.env` 自行填写后 `systemctl restart dsh-web`

### 使用的技术栈
Node.js、@deepseek-ai/dsh、systemd、rsync

### 修改了哪些文件
- `scripts/install_dsh_on_142.sh`
- 142：`/opt/dsh-offline`、`dsh-web.service`、`/root/.dsh/.env` 模板
- `README.md`（本条）

### 测试数据
- 未写业务库

## 会话总结（2026-10-05）— 排查 Cursor 连 142 卡住

### 会话的主要目的
Cursor 连 30.251.255.142 一直停在「打开远程中」，查原因并处理。

### 完成的主要任务
- 日志显示远程进程能起来，但会去应用市场装中文包并超时；还会探测云厂商元数据，空等数秒
- 关掉扩展自动更新；语言包已在本地
- 元数据地址改为立即拒绝；清掉残留 cursor-server

### 关键决策和解决方案
- 142 访问 marketplace.cursorapi.com 会超时，不要在连远程时在线装扩展
- 请在本机 Cursor 里重新连一次 142

### 使用的技术栈
Cursor Remote SSH、iptables

### 修改了哪些文件
- 仅 142：`~/.cursor-server/data/*/settings.json`、iptables OUTPUT、结束残留进程
- `README.md`（本条）

### 测试数据
- 未写业务库

## 会话总结（2026-10-05）— 离线拷贝 Cursor 远程服务端到 142

### 会话的主要目的
从本机复制 Cursor 远程客户端到 142，避免因外网下载卡住。

### 完成的主要任务
- 发现 142 上当前版本目录只有约 2.4MB 残缺压缩包，本机完整包约 448MB
- 已用内网 rsync 拷完整服务端，并补了本机另两套完整版本和扩展

### 关键决策和解决方案
- 不从 downloads.cursor.com 在线安装
- 不拷本机 5.9GB 的 data 缓存

### 使用的技术栈
rsync、Cursor Remote SSH

### 修改了哪些文件
- 仅 142：`~/.cursor-server/bin/linux-x64/` 若干版本目录与扩展
- `README.md`（本条）

### 测试数据
- 未写业务库

## 会话总结（2026-10-05）— 本机固定 Cursor 离线包

### 会话的主要目的
以后连新机器时，直接从本机文件夹拷 Cursor 远程程序，不走外网。

### 完成的主要任务
- 建立离线目录并同步完整版本
- 写同步脚本、拷机脚本，以及 Cursor 规则

### 关键决策和解决方案
- 大文件不进 git
- 残缺下载（小于约 100MB）不同步

### 使用的技术栈
rsync、Cursor 规则

### 修改了哪些文件
- `offline/cursor-remote/README.md`
- `scripts/sync_cursor_offline_bundle.sh`
- `scripts/copy_cursor_server_to_host.sh`
- `.cursor/rules/copy-cursor-server-offline.mdc`
- `.gitignore`
- `README.md`（本条）

### 测试数据
- 未写业务库

## 会话总结（2026-10-05）— 142 仍卡在打开远程

### 会话的主要目的
Cursor 连 142 仍显示正在打开远程，继续排查。

### 完成的主要任务
- 确认远程进程约 1 秒就能起来，随后去应用市场装中文包超时，约 15 秒客户端断开
- 远程改为英文 locale，应用市场指到本机立即失败，避免空等

### 关键决策和解决方案
- 语言包版本与 Cursor 对不上时每次都会查应用市场；142 外网不通就必须跳过

### 使用的技术栈
Cursor Remote SSH、/etc/hosts

### 修改了哪些文件
- 仅 142：`~/.cursor-server/data/argv.json`、User/Machine settings、/etc/hosts
- `scripts/copy_cursor_server_to_host.sh`
- `.cursor/rules/copy-cursor-server-offline.mdc`
- `README.md`（本条）

### 测试数据
- 未写业务库

## 会话总结（2026-10-05）— 改为在 142 开发

### 会话的主要目的
确认之后在 142 上直接开发 Agent Native。

### 完成的主要任务
- Cursor 远程已连上 142
- 项目在 `/home/aiworkbench`，并做了 `/home/workbench` 软链
- 写明：142 用隔离库，不动 64 正式数据

### 关键决策和解决方案
- 以后改代码请在 142 远程窗口里进行

### 使用的技术栈
Cursor Remote SSH、rsync

### 修改了哪些文件
- `.cursor/rules/dev-on-142-agent-native.mdc`
- 142：`/home/workbench` → `/home/aiworkbench`
- `README.md`（本条）

### 测试数据
- 未写业务库

## 会话总结（2026-10-05）— 网址与文件夹同名

### 会话主要目的
- 本机网址改成和文件夹一样：`/aiworkbench/`，方便和现网 64 的 `/workbench/` 区分

### 完成的主要任务
- PC 入口：`http://30.251.255.142:8080/aiworkbench/`
- 钉钉 H5：`http://30.251.255.142:8080/aiworkbench/m/`
- 旧地址 `/workbench/` 自动跳到 `/aiworkbench/`
- 本机 80 仍给安全监控用，工作台 nginx 听 8080（避免抢端口）

### 关键决策和解决方案
- 只改网址前缀和本机入口，不改数据库名、不改 64 现网
- 80 已被占用时安装脚本自动改听 8080
- 钉钉回调路径改为 `/aiworkbench/api/auth/dingtalk-callback?embed=1`（若要用扫码登录，开放平台白名单需同步）

### 使用的技术栈
- Vue Vite `VITE_BASE`；nginx 子路径；FastAPI 回调路径

### 修改了哪些文件
- `web-pc/.env.*`、`web-pc/vite.config.ts`、`web-dingtalk/.env.production`、`web-dingtalk/vite.config.ts` 及相关 API 默认路径
- `deploy/nginx/workbench.conf`、`site-80.conf`、`site-8080.conf`、`dsh-embed.conf`、`websocket-map.conf`
- `scripts/install-nginx.sh`、E2E 脚本、`backend/app/api/auth.py`
- `README.md`、`PROJECT_ARCHITECTURE.md`
- 重新构建 `web-pc/dist`、`web-dingtalk/dist`

### 测试数据
- 未写库；未跑 purge；未动正式业务数据
- 本机 curl：`/aiworkbench/`、`/aiworkbench/m/`、`/aiworkbench/api/health` 为 200；`/workbench/` 为 301
- Cursor 内置浏览器访问不了本机 8080（环境限制），请用自己的浏览器打开上面地址

## 会话总结（2026-10-05）— 配置 DSH MiniMax M Plan

### 会话主要目的
- 给本机智能助手（DSH）配上 MiniMax M Plan 对话模型

### 完成的主要任务
- 钥匙只写入本机 `/root/.dsh/.env`（权限 600），不进代码仓库、不进 README 正文
- 默认模型改为 `MiniMax-M3.1-Flash-Preview`（M Plan 文档指定）
- 重启 `dsh-web`；直连 MiniMax 接口短对话测试通过

### 关键决策和解决方案
- M Plan 用中国区网关 `api.minimax.cn`，模型名按官方文档填写
- DSH 自带目录原先没有 Flash Preview，已在本机模型清单中补上
- 钥匙曾出现在聊天里，建议之后在 MiniMax 控制台轮换一把新钥匙

### 使用的技术栈
- DeepSeek Harness（`MINIMAX_CN_API_KEY`）；MiniMax Anthropic 兼容接口

### 修改了哪些文件
- `/root/.dsh/.env`（及时间戳备份）
- `/root/.dsh/profiles/web/cordis.patch.yml`
- `/opt/dsh-offline/node_modules/@earendil-works/pi-ai/dist/providers/data/minimax-cn.json`
- `README.md`（本条）

### 测试数据
- 未写业务库；未跑 purge

## 会话总结（2026-10-05）— 配置 DSH DeepSeek 钥匙

### 会话主要目的
- 给本机智能助手补上 DeepSeek API 钥匙（网页搜索等能力用）

### 完成的主要任务
- 在本机 `/root/.dsh/.env` 增加 `DEEPSEEK_API_KEY`（与已有 `MINIMAX_CN_API_KEY` 并列，权限 600）
- 重启 `dsh-web`；直连 DeepSeek `/models` 返回 200

### 关键决策和解决方案
- MiniMax 管对话模型，DeepSeek 管搜索等工具钥匙，两把钥匙分开配置
- 钥匙只放服务器 `.env`，不进 README / git；聊天里出现过完整钥匙，建议之后在 DeepSeek 控制台轮换

### 使用的技术栈
- DeepSeek Harness 环境变量；systemd `dsh-web`

### 修改了哪些文件
- `/root/.dsh/.env`（及时间戳备份）
- `README.md`（本条）

### 测试数据
- 未写业务库；未跑 purge

## 会话总结（2026-10-05）— 旧 AI 全部改为 AI native

### 会话的主要目的
在 142 一人一助手产品上，把「页内 AI 帮填 / 工作台自己调模型」全部改成：只在「我的助手」里说话，确认后再写入账本。64 正式库不动。

### 完成的主要任务
- 助手工具补齐写库：周报 / 任务 / 项目 / 案例 / 卡点先起草，确认后再写入
- 打开 `AGENT_NATIVE=true`：旧的页内 AI 接口返回 410；评价与分析结论不再走工作台大模型
- 电脑端和手机端去掉「AI 生成草稿 / 润色 / 拆解 / 帮找」按钮，改成去「我的助手」
- 单测助手写库 + 旧 AI 用例仍可通过（单测默认不当作 142 新产品，避免误伤）

### 关键决策和解决方案
- 智能只发生在助手对话里；工作台只给账本事实，写库必须等人点头
- 立项确认仍要有项目支撑（同事创建会自动带上自己）；「日常任务」名称禁止占用
- DSH 网关若还要点到新工具，需在 142 网关登记新路径（本仓库登记不了网关）

### 使用的技术栈
FastAPI、SQLAlchemy、Vue 3、pytest、Vite

### 修改了哪些文件
- 后端：`config.py`、`.env` / `.env.example`、`agent_native.py`、`api/dsh.py`、`api/ai_assist.py`、`api/tasks.py`、`api/progress_reports.py`、评价与分析服务、单测
- 技能：`docs/dsh-skills/`、`backend/app/agent_skills/`
- 前端：电脑端 + 钉钉相关页面与提示组件
- `README.md`（本条）

### 测试数据
- 单测库里的测试前缀账号/项目，未动 64 正式业务数据；未跑 purge

## 会话总结（2026-10-05）— 8080 打不开

### 会话的主要目的
修好 `http://30.251.255.142:8080/aiworkbench/` 从同事电脑打不开。

### 完成的主要任务
- 查清页面在本机是通的，是防火墙挡住了 8080
- 对内网 `30.251.255.0/24` 放行 8080（和 80 同样范围，不向全网敞开）
- 安装 nginx 脚本以后走 8080 时会自动放行

### 关键决策和解决方案
- 80 给安全监控，工作台继续用 8080；不通是「门没开」，不是网站挂了

### 使用的技术栈
ufw、nginx

### 修改了哪些文件
- `scripts/install-nginx.sh`
- `README.md`（隔离说明 + 本条）

### 测试数据
- 未写业务库；未跑 purge

## 会话总结（2026-10-05）— 主页改成 AI Native 风格

### 会话的主要目的
把 142 打开网站后的第一眼，从「项目支撑工作台」改成「我的助手」：登录页像对话产品，登录后默认进助手对话。

### 完成的主要任务
- 登录页标题/文案/深色卡片；扫码和密码登录方式不变
- 登录和首次改密后跳到助手（有助手权限时），不再进个人工作台
- 侧栏品牌改为「我的助手」，账本入口分组；助手页侧栏收窄、对话铺满
- 钉钉底栏「助手」置顶，「首页」改成「看板」

### 关键决策和解决方案
- 只改 `VITE_AGENT_NATIVE` 产品线，64 老工作台不改
- 无助手权限仍回账本页，避免空白

### 使用的技术栈
Vue 3、Vite

### 修改了哪些文件
- `web-pc`：`productLine.ts`、`main.ts`、`router/index.ts`、`LoginView.vue`、`ChangePasswordView.vue`、`MainLayout.vue`、`DshView.vue`
- `web-dingtalk`：`productLine.ts`、`main.ts`、`MainLayout.vue`、`DshView.vue`、`styles/theme.css`
- `README.md`（本条）

### 测试数据
- 未写业务库；未跑 purge

## 会话总结（2026-10-05）— 钉钉 400 与助手 503

### 会话的主要目的
修好登录页钉钉扫码报 400、登录后助手入场券报 503。

### 完成的主要任务
- 钉钉：本机未配 AppKey，登录页默认改用账号密码，避免反复请求扫码配置
- 助手：142 内网 http 也允许签发嵌入地址；nginx 把助手页面转到本机 3080（不再指到 132）
- 本机还没有助手门禁，对话 SSO 换票仍会失败，错误提示改成白话

### 关键决策和解决方案
- 扫码要配钉钉钥匙才能用；现阶段用账号密码进
- 对话进不去是门禁进程没装，不是账本坏了

### 使用的技术栈
FastAPI、nginx、Vue 3

### 修改了哪些文件
- `backend/app/api/dsh.py`、`tests/test_dsh_agent_native.py`
- `deploy/nginx/dsh-embed.conf`
- `web-pc/src/views/LoginView.vue`
- `README.md`（本条）

### 测试数据
- 未写业务库；未跑 purge

## 会话总结（2026-10-05）— 本机助手对话能打开

### 会话的主要目的
让 142 上「助手」页真正进对话，不再停在「门禁还没装好」。

### 完成的主要任务
- 本机没有独立门禁进程，改用助手网页自己的开门令牌：登录后工作台发给嵌页根路径
- 启动包装把令牌写到本机 `/run`（不进仓库），并声明内网主机可访问助手接口
- nginx 把根路径 `/` 转到本机助手网页；工作台仍走 `/aiworkbench/`
- 单测覆盖：有令牌发嵌页地址、没有令牌返回 503

### 关键决策和解决方案
- 不从 64/132 拷门禁、不共用正式库
- 助手网页只认根路径种登录饼干，所以 `/` 给助手、`/aiworkbench/` 给工作台
- 现阶段共用一台本机助手网页（还不是每人独立工作区）；够先对话

### 使用的技术栈
FastAPI、nginx、systemd、DeepSeek Harness web、pytest

### 修改了哪些文件
- `backend/app/api/dsh.py`、`backend/app/core/config.py`、`backend/tests/test_dsh_agent_native.py`
- `scripts/run_dsh_web.py`、`scripts/install_dsh_on_142.sh`
- `deploy/nginx/dsh-embed.conf`、`deploy/nginx/site-8080.conf`、`deploy/nginx/site-80.conf`
- 本机 `dsh-web.service`
- `README.md`（本条）

### 测试数据
- 未写业务库；未跑 purge

## 会话总结（2026-10-05）— 按用户名自动建助手工作区

### 会话的主要目的
第一次打开助手时，按登录名自动建个人工作区，A 与 B 的对话文件分开。

### 完成的主要任务
- 新增本机轻量门禁：换票、按登录名建磁盘屋子、按需拉起个人助手网页、空闲回收进程（屋子保留）
- 工作台入场券改回 `/sso?code=`；nginx 与内部地址指向门禁 3081
- 停用全员共用的一台助手网页，避免两人进同一间屋
- 单测：非法登录名拒绝、换票建目录、两人代理互不串房；入场券改为换票码

### 关键决策和解决方案
- 助手网页一份进程只能服务一个人，所以每人独立家目录 + 独立进程
- 模型钥匙仍只从本机共享环境文件读进进程，不写进用户目录和文档
- 不从 64 拷门禁、不碰正式库；本阶段不做用量统计全套接口

### 使用的技术栈
Python 标准库 HTTP 门禁、systemd、nginx、FastAPI、pytest、DeepSeek Harness web

### 修改了哪些文件
- 新：`dsh_local_gate/`、`deploy/systemd/dsh-gate.service`、`scripts/install_dsh_gate.sh`
- `scripts/run_dsh_web.py`、`scripts/install_dsh_on_142.sh`、`scripts/bootstrap_agent_native.sh`
- `backend/app/api/dsh.py`、`backend/app/core/config.py`、`backend/.env.example`
- `backend/tests/test_dsh_local_gate.py`、`backend/tests/test_dsh_agent_native.py`
- `deploy/nginx/dsh-embed.conf`
- `README.md`（本条）

### 测试数据
- 未在业务库新建测试账号；单测用临时目录上的 `t_ws_a` / `t_ws_b`
- 未跑 purge


### 会话的主要目的
让 142 上「助手」页真正进对话，不再停在「门禁还没装好」。

### 完成的主要任务
- 本机没有独立门禁进程，改用助手网页自己的开门令牌：登录后工作台发给嵌页根路径
- 启动包装把令牌写到本机 `/run`（不进仓库），并声明内网主机可访问助手接口
- nginx 把根路径 `/` 转到本机助手网页；工作台仍走 `/aiworkbench/`
- 单测覆盖：有令牌发嵌页地址、没有令牌返回 503

### 关键决策和解决方案
- 不从 64/132 拷门禁、不共用正式库
- 助手网页只认根路径种登录饼干，所以 `/` 给助手、`/aiworkbench/` 给工作台
- 现阶段共用一台本机助手网页（还不是每人独立工作区）；够先对话

### 使用的技术栈
FastAPI、nginx、systemd、DeepSeek Harness web、pytest

### 修改了哪些文件
- `backend/app/api/dsh.py`、`backend/app/core/config.py`、`backend/tests/test_dsh_agent_native.py`
- `scripts/run_dsh_web.py`、`scripts/install_dsh_on_142.sh`
- `deploy/nginx/dsh-embed.conf`、`deploy/nginx/site-8080.conf`、`deploy/nginx/site-80.conf`
- 本机 `dsh-web.service`
- `README.md`（本条）

### 测试数据
- 未写业务库；未跑 purge

## 会话总结（2026-10-05）— 自动建默认干活文件夹

### 会话的主要目的
修好「无法自动创建目录」：打开助手后默认工作区文件夹建不出来。

### 完成的主要任务
- 每人屋子里补上「文档/deepseek-harness/default-workspace」
- 写明文档目录，避免系统把家目录当成文档目录而拒绝创建
- 若以前误把家目录登记成工作区，清掉标记以便下次自动建默认夹

### 关键决策和解决方案
- 助手网页认的是「文档」下的默认干活夹，不是只有登录名空目录

### 使用的技术栈
Python 门禁、xdg 文档目录、DeepSeek Harness workspace-controller

### 修改了哪些文件
- `dsh_local_gate/homes.py`、`dsh_local_gate/runtime.py`
- `backend/tests/test_dsh_local_gate.py`
- `README.md`（本条）

### 测试数据
- 未写业务库；未跑 purge

## 会话总结（2026-10-05）— 配置 142 钉钉 AppKey / Secret / CorpId

### 会话的主要目的
把钉钉开放平台的应用编号、密钥、企业编号配进 142 本机工作台，让扫码和免登能连上钉钉。

### 完成的主要任务
- 备份后在服务器 `backend/.env` 写入三项：`DINGTALK_APP_KEY`、`DINGTALK_APP_SECRET`、`DINGTALK_CORP_ID`（权限 600）
- 本机没有 `workbench-backend` systemd 服务，改为重启当时的 uvicorn（8100）
- 核验 `GET /api/auth/dingtalk-config`（本机 8100 与 nginx `:8080/aiworkbench/`）返回的 `client_id`、`corp_id` 与所配编号一致；接口 JSON **不含** Secret

### 关键决策和解决方案
- 密钥只放服务器 `.env`，不进 git、不进 README 正文
- 未改 64 现网；未配 `DINGTALK_AGENT_ID`（工作通知仍只扫描不实发）
- 密钥曾出现在对话中，建议在钉钉开放平台择机轮换 Secret
- 开放平台回调白名单仍须是：`http://30.251.255.142:8080/aiworkbench/api/auth/dingtalk-callback?embed=1`

### 使用的技术栈
FastAPI、uvicorn、nginx、钉钉开放平台环境变量

### 修改了哪些文件
- `backend/.env`（及本地时间戳备份，不进 git）
- `README.md`（本条）

### 测试数据
- 配置接口会写一条短期 oauth state；未造测试账号/项目；未跑 purge

## 会话总结（2026-10-05）— 询问管理员密码

### 会话的主要目的
确认演示管理员账号的初始密码。

### 完成的主要任务
- 按 README「演示账号」与初始化脚本答复：用户名 `admin`，初始密码 `InitAdmin2026`

### 关键决策和解决方案
- 这是种子演示账号的初始密码；若已强制改密，旧初始密码会失效
- 未改代码、未查库、未重置密码

### 使用的技术栈
无

### 修改了哪些文件
- `README.md`（本条）

### 测试数据
- 未写业务库；未跑 purge

## 会话总结（2026-10-05）— 钉钉登录按工号自动开账号和工作区

### 会话的主要目的
让 142 上钉钉扫码/免登后，用工号自动开工作台账号，并立刻建个人助手屋子，不用再输密码绑定。

### 完成的主要任务
- 钉钉通讯录增加按 userid 取姓名/工号/岗位（不把手机号带进登录）
- 仅 142（`agent_native`）：未绑定则按工号开普通成员或自动绑已有账号，成功后预建磁盘屋子
- 现网 64 仍返回「请用系统账号绑定」
- 登录页默认钉钉扫码；成员角色补上钉钉免登权限
- 门禁新增只建屋、不拉起对话进程的签名接口
- 单测通过；已重启本机门禁与后端，并重新打包电脑端

### 关键决策和解决方案
- 门牌号 = 工号 = 登录名；新账号不是管理员
- 工号缺失、不能当文件夹名、或已被别人占用：拒绝并说明
- 建屋失败不挡登录；点「我的助手」时仍会再建一次
- 不改管理员已占用的钉钉身份

### 使用的技术栈
FastAPI、钉钉开放平台通讯录、Python 门禁、pytest、Vue 3

### 修改了哪些文件
- `backend/app/services/dingtalk.py`、`backend/app/services/dingtalk_jit.py`（新）、`backend/app/api/auth.py`、`backend/app/api/dsh.py`、`backend/app/services/init_service.py`
- `dsh_local_gate/server.py`
- `backend/tests/test_dingtalk_auth.py`、`backend/tests/test_dsh_local_gate.py`
- `web-pc/src/views/LoginView.vue`、`web-pc/dist`
- `README.md`（本条）

### 测试数据
- 单测用工号前缀 `t_jit_` 的账号已在用例内删除
- 未跑 purge（未留下需清理的 `t_` 账号）

## 会话总结（2026-10-05）— 本仓库只服务 142

### 会话的主要目的
确认两套系统分开：这里只改 142，以后不再为 64 留兼容开关。

### 完成的主要任务
- 钉钉登录按工号开账号、建屋子，成为本系统默认路径（不再看 `agent_native` 才走）
- 规则与 README 写明：本仓库只维护 142；64 是另一套代码
- 单测改为验收「未绑定就按工号开账号」；绑定接口仍可用，但不再当作登录主路径

### 关键决策和解决方案
- 64 与 142 两套系统，这里不再写「现网 64 仍要先绑定」
- 登录成功后一律尝试预建助手屋子；门禁没起来也不挡登录

### 使用的技术栈
FastAPI、pytest、Cursor 规则

### 修改了哪些文件
- `.cursor/rules/dev-on-142-agent-native.mdc`
- `backend/app/api/auth.py`、`backend/tests/test_dingtalk_auth.py`
- `README.md`（本条及登录说明）

### 测试数据
- 单测 `t_jit_` / `t_jits_` 账号已在用例内删除；未跑 purge

## 会话总结（2026-10-05）— 钉钉进助手必须用工号开屋

### 会话的主要目的
修好截图里工作区叫 `home`、没有按工号建屋的问题。

### 完成的主要任务
- 钉钉登录一律读通讯录工号当门牌；系统账号占用钉钉身份时让开，再开/进入工号账号
- 磁盘干活夹改用工号当文件夹名并登记到助手，避免显示成 `home`
- 已备份、重启门禁与后端

### 关键决策和解决方案
- 根因：钉钉身份挂在系统账号上，助手屋子建在 `admin` 的家目录
- 工号账号与系统账号分开：扫码进工号屋；系统管理仍可用系统账号密码

### 使用的技术栈
FastAPI、钉钉通讯录、Python 门禁、pytest

### 修改了哪些文件
- `backend/app/api/auth.py`、`backend/app/services/dingtalk_jit.py`
- `dsh_local_gate/homes.py`、`dsh_local_gate/runtime.py`
- `backend/tests/test_dingtalk_auth.py`、`backend/tests/test_dsh_local_gate.py`
- `README.md`（本条）

### 测试数据
- 单测 `t_fy_` 等账号已在用例内删除；未跑 purge
- 备份：`backups/project_workbench_20261005_215704.sql.gz`

## 会话总结（2026-10-05）— 扫码 500 与助手退出 404

### 会话的主要目的
看懂控制台里扫码失败、退出 404、助手连不上会话的原因并修好。

### 完成的主要任务
- 连钉钉超时不再变成服务器 500，改成「连不上，请刷新二维码」
- 助手页退出地址由门禁自己处理：清登录饼干、停本人工序，不再转去网页进程（那边本来就没有这个地址）
- 相关单测已通过；已重启门禁与后端

### 关键决策和解决方案
- 扫码 500：出口访问钉钉超时，原先没包一层，浏览器只看到内部错误
- 退出 404：浏览器走 GET `/logout`，门禁以前原样转发
- `sessionController is unavailable`：退出时网页进程已经乱了；修好退出并重新从工作台打开助手后会再拉起。若仍连不上，关掉助手页再点一次「我的助手」

### 使用的技术栈
FastAPI、httpx、Python 门禁、pytest

### 修改了哪些文件
- `backend/app/services/dingtalk.py`、`backend/app/api/auth.py`
- `dsh_local_gate/server.py`
- `backend/tests/test_dingtalk_auth.py`、`backend/tests/test_dsh_local_gate.py`
- `README.md`（本条）

### 测试数据
- 未写入正式库业务数据；未跑 purge

## 会话总结（2026-10-05）— 二维码 timeout of 15000ms

### 会话的主要目的
看懂登录页红字「timeout of 15000ms exceeded」并尽量修好。

### 完成的主要任务
- 把这句英文超时改成中文提示；扫码换票最多等 45 秒
- 钉钉框自己 15 秒超时后，最多自动换两次新码
- 已重新打包电脑端；相关小测通过

### 关键决策和解决方案
- 红字来自浏览器 15 秒等接口，不是「工号没建好」
- 这台 142 机器当前查不到外网域名（DNS 全超时），服务器换不了钉钉登录票。二维码能显示是因为同事自己的电脑能打开钉钉；扫完后还是要这台机器去问钉钉
- 在网管放行 DNS/HTTPS 到钉钉之前，请用「账号密码」登录

### 使用的技术栈
Vue 3、axios、vitest、httpx

### 修改了哪些文件
- `web-pc/src/composables/dingtalkQrLogin.ts`、`web-pc/src/composables/dingtalkQrLogin.spec.ts`
- `web-pc/src/views/LoginView.vue`、`web-pc/dist`
- `backend/app/services/dingtalk.py`
- `README.md`（本条）

### 测试数据
- 未改库、未跑 purge

## 会话总结（2026-10-05）— 扫码成功后不跳转

### 会话的主要目的
排查钉钉扫码显示成功，但工作台还停在登录页的问题。

### 完成的主要任务
- 换票过程中不再被二维码超时刷新打断
- 扫码登录成功后用整页跳转进入助手，避免钉钉框挡住 Vue 路由
- 扫码成功时盖住「授权完成，请返回登录页」，改成「正在进入」
- 电脑端已重新打包

### 关键决策和解决方案
- 手机钉钉「登录成功」只表示扫码框完成，工作台还要换票；这台机器连不上钉钉时换票会失败，看起来像没跳转
- 账号密码登录是通的（能拿到助手票）
- 线路通了之后，整页跳转才能稳稳离开登录页

### 使用的技术栈
Vue 3、Vue Router、vitest

### 修改了哪些文件
- `web-pc/src/views/LoginView.vue`、`web-pc/src/productLine.ts`、`web-pc/src/productLine.spec.ts`
- `web-pc/dist`
- `README.md`（本条）

### 测试数据
- 未改库、未跑 purge

## 会话总结（2026-10-05）— 卡住不是工号屋子没建成

### 会话的主要目的
确认「扫码成功一直不跳转」是不是工号工作区创建失败。

### 完成的主要任务
- 查日志：扫码换票一直失败，还没走到建屋子
- 磁盘上还没有工号那间屋子（只有系统账号那一间）
- 页面改成说明「正在向钉钉确认身份，不是在建工作区」；换票最多等约 12 秒就报错，不再一直转圈
- 电脑端已重新打包并重启后端

### 关键决策和解决方案
- 不是建屋堵住登录：建屋在换票成功之后，失败也不挡登录
- 根因仍是这台机器连不上钉钉，换票失败
- 线路不通时请用账号密码

### 使用的技术栈
FastAPI、Vue 3、pytest、vitest

### 修改了哪些文件
- `backend/app/api/auth.py`
- `web-pc/src/views/LoginView.vue`、`web-pc/dist`
- `README.md`（本条）

### 测试数据
- 未改库、未跑 purge

## 会话总结（2026-10-05）— 自己走一遍登录交互

### 会话的主要目的
按用户要求自己做交互测试，确认扫码卡住和账号密码进助手的真实结果。

### 完成的主要任务
- 先备份；本机没有可用的图形浏览器（Chromium 包不在），改为走与页面相同的 8080 接口
- 登录页、扫码配置、假扫码、测试账号密码登录、领取助手票，全部实测
- 测试账号已删除，库里 `t_itest_` 剩余 0

### 关键决策和解决方案
- 假扫码约 8 秒返回「连不上钉钉」，不是建工号屋子卡住
- 测试账号密码登录 200，本人信息 200，助手票 200 且有嵌入地址，说明跳转链路是通的
- 页面脚本已包含「不是在建工作区」和 12 秒超时

### 使用的技术栈
httpx、nginx :8080、Playwright（未能启动浏览器）

### 修改了哪些文件
- 未改业务代码（测试脚本在临时目录）
- `README.md`（本条）

### 测试数据
- 备份：`backups/project_workbench_20261005_222943.sql.gz`
- 新建并删除 `t_itest_` 账号；剩余 0；未跑全量 purge

## 会话总结（2026-10-05）— 扫码 502 仍无法跳转

### 会话的主要目的
同事扫码仍 502、不跳转，确认是否账号问题，并尽量让页面说清楚。

### 完成的主要任务
- 复核出口：本机网关通，但公网 DNS/HTTPS 被拒绝，工作台服务器问不了钉钉
- 控制台 502 与 login.js 提示不是账号错误；谁扫码都会换票失败
- 失败时弹出中文说明，并提供「改用账号密码登录」；已重新打包电脑端

### 关键决策和解决方案
- 不能在程序里绕过网关去访问钉钉
- 需网管放行本机访问钉钉开放平台的域名解析和 HTTPS
- 在此之前请用账号密码登录

### 使用的技术栈
Vue 3、nginx、系统网络排查

### 修改了哪些文件
- `web-pc/src/views/LoginView.vue`、`web-pc/dist`
- `README.md`（本条）

### 测试数据
- 未改库、未跑 purge

## 会话总结（2026-10-05）— 主机如何测钉钉出口

### 会话的主要目的
说明在工作台服务器上怎样判断「能不能访问钉钉」。

### 完成的主要任务
- 写了本机检测脚本，并在登录说明里写了手动命令
- 强调必须在 142 上测，不能用自己电脑代替

### 关键决策和解决方案
- 解析出 IP 且 HTTPS 有状态码（哪怕 400）算出网正常；`http=000` 或解析超时就不行
- 不在检测里带应用密钥

### 使用的技术栈
bash、curl、getent

### 修改了哪些文件
- `scripts/check_dingtalk_net.sh`（新）
- `README.md`（检测说明 + 本条）

### 测试数据
- 未改库、未跑 purge

## 会话总结（2026-10-05）— 查看主机防火墙是否放通

### 会话的主要目的
确认本机防火墙有没有拦住访问钉钉。

### 完成的主要任务
- 查看 ufw：入站默认拒绝、出站默认放行；8080 已对内网放行
- 出站规则没有拦钉钉；钉钉域名解析仍然超时

### 关键决策和解决方案
- 不是本机防火墙没放通出口；问题在更外层（网关/校园网 DNS 或 HTTPS）
- 不改防火墙规则

### 使用的技术栈
ufw、iptables、curl、getent

### 修改了哪些文件
- `README.md`（本条）

### 测试数据
- 未改库、未跑 purge

## 会话总结（2026-10-09）— 142 外网已开放复测

### 会话的主要目的
用户告知 142 外网已开放，复测钉钉出口与登录相关服务。

### 完成的主要任务
- `check_dingtalk_net.sh`：DNS/HTTPS 全通（4/4）
- 发现后端 8100 未在跑，已重新拉起；门禁与 nginx 正常
- 假扫码已从「网络 502」变为「授权失败 401」（说明已能连上钉钉，假码本身无效属预期）
- OAuth 回调地址在真实 Host 下为 `http://30.251.255.142:8080/.../dingtalk-callback?embed=1`

### 关键决策和解决方案
- 出口已通；请同事 Ctrl+F5 后重新扫码
- 扫码成功后应按工号建屋并跳转；若仍失败再看页面红字

### 使用的技术栈
bash 检测脚本、curl、uvicorn

### 修改了哪些文件
- `README.md`（本条）
- 运行态：重启本机 uvicorn :8100（非代码变更）

### 测试数据
- 未改库、未跑 purge

## 会话总结（2026-10-09）— 扫码后助手页黑屏

### 会话的主要目的
同事扫码已进工作台左侧栏，但中间助手是黑屏。

### 完成的主要任务
- 确认登录与建屋成功（已有工号屋子），助手票与 SSO 能出 HTML
- 加固助手页布局，避免中间区域高度塌成黑块；黑屏过久可点「刷新助手」
- 重启门禁并清掉卡住的助手进程；重新打包电脑端

### 关键决策和解决方案
- 黑屏不是「没登录成功」，而是助手嵌入页没正常铺开/进程需重开
- 请 Ctrl+F5 后再点左侧「助手」；若仍黑，点页面上的「刷新助手」

### 使用的技术栈
Vue 3、nginx 嵌入、dsh-gate

### 修改了哪些文件
- `web-pc/src/views/DshView.vue`、`web-pc/src/layouts/MainLayout.vue`、`web-pc/dist`
- `README.md`（本条）
- 运行态：重启 `dsh-gate`，停止工号助手旧进程

### 测试数据
- 未改库、未跑 purge

## 会话总结（2026-10-09）— 工作区不显示工号

### 会话的主要目的
助手页能开了，但「选择工作区」里看不到工号。

### 完成的主要任务
- 查日志：工号屋子登记缺 `createdAt`/`updatedAt`，助手工作区插件校验失败
- 补齐登记字段，修好现网工号屋子数据；避免同一个人并发开两个助手进程抢端口
- 门禁单测通过；已重启门禁

### 关键决策和解决方案
- 磁盘上其实已有工号文件夹，但登记表不合规矩，列表就空了
- 请 Ctrl+F5 后点「刷新助手」或重新进「助手」，下拉里应出现工号名

### 使用的技术栈
Python 门禁、DSH workspace schema、pytest

### 修改了哪些文件
- `dsh_local_gate/homes.py`、`dsh_local_gate/runtime.py`
- `backend/tests/test_dsh_local_gate.py`
- `README.md`（本条）
- 运行态：修复 `/var/lib/dsh/users/*/home/storages/workspace.json`，重启 `dsh-gate`

### 测试数据
- 未改业务库、未跑 purge

## 会话总结（2026-10-09）— 助手会不会自动写任务周报

### 会话的主要目的
回答：随便把工作记录扔给助手，会不会自动总结并写入任务、周报、项目。

### 完成的主要任务
- 按产品设计说明：可以口述整理，但正式写入账本前必须你确认

### 关键决策和解决方案
- 助手先起草（draft），你说「确认」后再写入；不会悄悄改账本

### 使用的技术栈
无代码变更

### 修改了哪些文件
- `README.md`（本条）

### 测试数据
- 无

## 会话总结（2026-10-09）— 修复 MiniMax NO_ADAPTER

### 会话的主要目的
用户截图出现 `NO_ADAPTER`（minimax-cn），并提供 M Plan 文档与新钥匙。

### 完成的主要任务
- 核对钥匙：与本机已有 `MINIMAX_CN_API_KEY` 相同；直连 `api.minimax.cn` 短对话返回 200
- 根因：`llm-pi-ai` 默认零路由，只设了默认模型未登记 `minimax-cn` 提供方
- 在 profile 补上 `providers.minimax-cn.apiKeyEnv: MINIMAX_CN_API_KEY`，并重启工号助手进程
- `--dump-config` 已可见该路由；助手页 token 登录 303 正常

### 关键决策和解决方案
- 钥匙只写 `/root/.dsh/.env`（600），不进仓库/README
- 聊天里出现过完整钥匙，建议在 MiniMax 控制台轮换
- 模型名旁的 `off` 是推理强度关闭，不是模型关掉

### 使用的技术栈
- DeepSeek Harness `llm-pi-ai`；MiniMax 中国区 Anthropic 兼容接口；dsh-gate 按工号进程

### 修改了哪些文件
- `/root/.dsh/.env`（备份后核对，内容未变）
- `/root/.dsh/profiles/web/cordis.patch.yml`
- `README.md`（本条）

### 测试数据
- 未改业务库、未跑 purge

## 会话总结（2026-10-09）— MiniMax 禁止关闭思考

### 会话的主要目的
修复 400：`MiniMax-M3.1-Flash-Preview` 要求自适应思考，不允许 `thinking.type=disabled` / `reasoningEffort=off`。

### 完成的主要任务
- 默认 `reasoningEffort` 从 `off` 改为 `high`
- 工号已有会话里的 `off` 一并改成 `high`
- 重启助手进程；直连 API（enabled thinking）返回 200

### 关键决策和解决方案
- M Plan Flash 模型不能关思考；界面上不要再选 `off`
- 钥匙未改动

### 使用的技术栈
- DSH agent-default-model；MiniMax Anthropic 兼容接口

### 修改了哪些文件
- `/root/.dsh/profiles/web/cordis.patch.yml`
- 工号会话缓存 2 个 json（reasoningEffort off→high）
- `README.md`（本条）

### 测试数据
- 未改业务库、未跑 purge

## 会话总结（2026-10-09）— 工作日记有没有进账本

### 会话的主要目的
回答：助手整理出来的工作日记，有没有挂项目、进任务、入待办。

### 完成的主要任务
- 查库：该工号任务/项目成员/周报/进度报告/草稿均为 0
- 对照技能规则：须 draft → 用户说确认 → confirm-draft；当前只生成了文档，未写账本

### 关键决策和解决方案
- 聊天任务表 ≠ 左侧账本；要进待办需明确让助手起草并说「确认」

### 使用的技术栈
- PostgreSQL 只读核对；dsh-skills 写库流程

### 修改了哪些文件
- `README.md`（本条）

### 测试数据
- 只读查询，未改库、未跑 purge

## 会话总结（2026-10-09）— 助手整理后先问再写库

### 会话的主要目的
产品要求：助手整理工作记录后，要主动提示「是否加入待办」「是否写入任务」。

### 完成的主要任务
- 更新个人助手/任务/周报/项目技能：整理后必须先问，未同意不 draft
- 每人干活夹种子文件 `工作台账本规则.md`；已写入当前工号工作区
- 单测覆盖种子文件存在

### 关键决策和解决方案
- 只总结默认不写库；用户用「待办是/任务是」等明确答复后再起草，再说「确认」才入库
- 技能双份同步：`docs/dsh-skills/` 与 `backend/app/agent_skills/`

### 使用的技术栈
- Markdown 技能包；dsh_local_gate 家目录种子；pytest

### 修改了哪些文件
- `docs/dsh-skills/00-personal-assistant.md`、`01-project.md`、`02-task.md`、`03-weekly-report.md`
- `backend/app/agent_skills/` 对应文件
- `dsh_local_gate/homes.py`、`backend/tests/test_dsh_local_gate.py`
- `docs/superpowers/specs/2026-10-04-dsh-agent-native-design.md`
- 工号工作区 `工作台账本规则.md` / `个人助手技能.md`
- `README.md`（本条）

### 测试数据
- 未改业务库、未跑 purge；`test_ensure_user_home_reuses_directory` 通过

## 会话总结（2026-10-09）— 接通工作台账本 MCP

### 会话的主要目的
助手声称已建任务，但待办/任务/项目/周报无数据；查明并打通写库路径。

### 完成的主要任务
- 根因：DSH 未挂工作台工具，只写了 Documents / 会话 todo_write，库内 FY0354 草稿与任务均为 0
- 新增 `scripts/wb_mcp_server.py`（stdio MCP → 签名调用 `/api/dsh/tools/*`）
- profile `insert` 挂载 `mcp-workbench`；SSO 写入 `.wb_identity.json`
- 冒烟：`my_todos`/`my_projects`/`draft_task` 成功；测试草稿已清理
- 技能文案改为必须用 `mcp__workbench__*`，禁止假装入库

### 关键决策和解决方案
- 新插件用 `insert:`，不能只用 `id:`（否则 patch entry not found）
- MCP env 显式传入 `DSH_TO_WB_SECRET` / `DSH_HOME`（stdio 不继承全部环境）

### 使用的技术栈
- DSH mcp-client；Python stdio MCP；service_auth 签名；pytest

### 修改了哪些文件
- `scripts/wb_mcp_server.py`（新）
- `/root/.dsh/profiles/web/cordis.patch.yml`
- `dsh_local_gate/homes.py`、`server.py`
- `docs/dsh-skills/*`、`backend/app/agent_skills/*`
- `backend/tests/test_wb_mcp_server.py`
- `docs/superpowers/specs/2026-10-04-dsh-agent-native-design.md`
- `README.md`（本条）

### 测试数据
- 曾创建并删除 `t_mcp_bridge_smoke` 草稿；未跑 purge；未改正式业务任务

## 会话总结（2026-10-09）— 账本确认改为弹出选择框

### 会话的主要目的
用户要求「待办/任务/周报」确认用弹出选择框，不要打字回复。

### 完成的主要任务
- 技能改为强制调用 DSH 自带 `ask_user_question`（聊天旁选择卡片）
- 写成任务时再弹项目列表；最终入库再弹确认
- 注明子智能体不能弹窗，须由主对话弹
- 同步工作区《工作台账本规则》与技能文件

### 关键决策和解决方案
- 复用 DSH 已有提问 UI，不自研前端弹窗
- 无 `ask_user_question` 时才允许退回文字提问

### 使用的技术栈
- DSH `ask_user_question` + user-questions 卡片；技能 Markdown

### 修改了哪些文件
- `docs/dsh-skills/00-personal-assistant.md`、`02-task.md`
- `backend/app/agent_skills/` 对应文件
- `dsh_local_gate/homes.py`（规则文案）
- 工号工作区规则/技能 md
- `README.md`（本条）

### 测试数据
- 未改业务库、未跑 purge

## 会话总结（2026-10-09）— 清空任务/待办与测试项目

### 会话的主要目的
用户要求清空系统所有项目、任务、待办。

### 完成的主要任务
- 先备份 `project_workbench_agent` 到 `backups/project_workbench_agent_20261009_130743.sql.gz`
- 删除全部任务 35、草稿 9、进度报告 11、测试项目 29
- **保留**正式项目：日常任务、家庭医生签约系统、民政展厅装修、办公设备采购（红线不可删）
- 验证：待办 0；可见项目仅剩上述 4 个

### 关键决策和解决方案
- 不能无筛选清空「所有项目」——正式业务立项与「日常任务」必须保留
- 先断开 tasks↔progress_reports 外键再删

### 使用的技术栈
- pg_dump；asyncpg 事务删除

### 修改了哪些文件
- 数据库 `project_workbench_agent`（写库）
- `backups/project_workbench_agent_*.sql.gz`（新备份）
- `README.md`（本条）

### 测试数据
- 已清测试项目与全部任务；未跑 purge 脚本；未动正式账号

## 会话总结（2026-10-09）— 用户同意后删除正式项目

### 会话的主要目的
用户明确「同意删除正式项目」，清空剩余正式立项。

### 完成的主要任务
- 删除前再备份：`backups/project_workbench_agent_*_before_formal_wipe.sql.gz`
- 删除剩余 4 个项目：日常任务、家庭医生签约系统、民政展厅装修、办公设备采购及成员/阶段/卡点
- 验证：项目 0、任务 0、待办 0；用户账号 161 未删

### 关键决策和解决方案
- 仅在用户书面同意后删除正式项目；不动员工账号

### 使用的技术栈
- pg_dump；asyncpg

### 修改了哪些文件
- 数据库 `project_workbench_agent`
- `backups/project_workbench_agent_*_before_formal_wipe.sql.gz`
- `README.md`（本条）

### 测试数据
- 项目/任务已空；未删正式员工账号


## 会话总结（2026-10-09）— 加入 MCP 写待办工具 draft_todo

### 会话的主要目的
工作台 MCP 原先只有只读 `my_todos`，没有写待办工具；待办只能靠任务/交办间接进账本。补上专用写待办能力。

### 完成的主要任务
- 新增 `POST /api/dsh/tools/draft-todo` 与 `agent_native.draft_todo` / `_confirm_todo`
- MCP 暴露 `draft_todo`（单条或 `items` 多条）；确认后默认挂「日常任务」，出现在 `my_todos`
- 更新个人助手/任务技能：选「加入待办」走 `draft_todo`，与 `draft_task` 区分
- 单测通过；重启后端 8100 与 `dsh-gate`

### 关键决策和解决方案
- 待办仍是本人未完成 Task；写待办 = 自建 self 任务，默认可挂「日常任务」（`ensure_daily_project`）
- 仍走 draft → 用户确认 → `confirm_draft`，不直接落库
- 同一事项不要既 `draft_todo` 又 `draft_task`（会重复）

### 使用的技术栈
- FastAPI；MCP stdio（`wb_mcp_server.py`）；pytest；systemd `dsh-gate`

### 修改了哪些文件
- `backend/app/services/agent_native.py`
- `backend/app/api/dsh.py`
- `scripts/wb_mcp_server.py`
- `docs/dsh-skills/00-personal-assistant.md`、`02-task.md`
- `backend/app/agent_skills/00-personal-assistant.md`、`02-task.md`
- `backend/tests/test_dsh_agent_native.py`、`test_wb_mcp_server.py`
- `README.md`（本条）

### 测试数据
- 单测会创建 `t_ag_*` 账号与「日常任务」；未对正式业务做 purge。若库中残留测试账号，收尾可用 `purge_test_data.py --dry-run` 后再清。


## 会话总结（2026-10-09）— 说明每账号 Agent 记忆系统设计

### 会话的主要目的
讲清楚 142「一人一助手」里，每个登录账号的记忆是怎么分层存的（不是向量库那种「记忆库」）。

### 完成的主要任务
- 对照规格 `docs/superpowers/specs/2026-10-04-dsh-agent-native-design.md`、门禁 `dsh_local_gate/homes.py` / `runtime.py`、账本表 `agent_user_prefs` / `agent_drafts` / `agent_nudge_logs`、技能包与本机磁盘布局 `/var/lib/dsh/users/<工号>/`
- 用通俗结构说明：磁盘屋子、对话会话、工作区文件、技能规则、PostgreSQL 账本与习惯偏好五层，以及「对话文件 ≠ 团队账本」

### 关键决策和解决方案
- 本会话仅解释现有设计，未改代码、未改库
- 结论：当前没有独立向量/长期记忆库；跨会话「记得住」主要靠个人工作区文件 + 会话 transcript + 账本事实 + 习惯表

### 使用的技术栈
- DeepSeek Harness（DSH）个人家目录与 session 文件；Python 门禁；PostgreSQL 账本；MCP skill-pack

### 修改了哪些文件
- `README.md`（本条）

### 测试数据
- 仅只读查看本机用户目录结构；未写业务库、未 purge

## 会话总结（2026-10-09）— Agent 长期记忆 D+C（V1 运行 / V2 预埋）

### 会话的主要目的
落地每人长期记忆：偏好 + 工作摘要 + 旧对话可检索；线上默认关键词（V1），向量（BGE-M3）代码写好但配置为空不启用。

### 完成的主要任务
- 规格：`docs/superpowers/specs/2026-10-09-agent-long-term-memory-design.md`
- 表：`agent_memory_notes` / `agent_memory_chunks`（`embedding DOUBLE PRECISION[]` 可空）
- 服务：`agent_memory.py`、`embedding_client.py`；配置三项 `EMBEDDING_*` + `memory_vector_enabled`
- 工具/MCP：`memory_get` / `memory_search` / `memory_upsert` / `memory_ingest_session`
- 种子：`长期记忆说明.md`；技能 `00-personal-assistant.md` 同步
- 单测 6 项通过；本机 `.env` 未填向量模型，仍跑 V1
- 收紧 `purge_test_data`：避免误删「老年手表测试反馈分析」等真实立项名

### 关键决策和解决方案
- 不在 DSH 配向量模型；工作台 `.env` 填 `EMBEDDING_MODEL`+`EMBEDDING_BASE_URL` 才开 V2
- 向量存 float 数组 + 应用内余弦；pgvector 扩展可另装，不挡 V1
- 会话索引优先 `excerpt.txt` / 工具传摘录，不解压 zstd

### 使用的技术栈
FastAPI、SQLAlchemy、PostgreSQL、httpx embeddings 客户端、MCP stdio、pytest

### 修改了哪些文件
- `docs/superpowers/specs/2026-10-09-agent-long-term-memory-design.md`
- `docs/dsh-skills/00-personal-assistant.md`、`backend/app/agent_skills/00-personal-assistant.md`
- `backend/app/core/config.py`、`migrations.py`、`models/__init__.py`
- `backend/app/services/agent_memory.py`、`embedding_client.py`
- `backend/app/api/dsh.py`、`backend/.env.example`
- `scripts/wb_mcp_server.py`、`scripts/purge_test_data.py`
- `dsh_local_gate/homes.py`
- `backend/tests/test_agent_memory.py`、`test_dsh_local_gate.py`、`test_wb_mcp_server.py`
- `README.md`（本条）

### 测试数据
- 先备份：`/home/workbench/backups/project_workbench_20261009_143247.sql.gz`
- 仅删除本会话 `t_ag_*` 测试账号 47 个；**未删**真实立项「老年手表测试反馈分析」

## 会话总结（2026-10-09）— 系统设置：向量模型 + 权限（方案 A）

### 会话的主要目的
在现有管理页增加向量模型配置与更醒目的权限设置；修好 142 侧栏看不到「系统管理」的问题。

### 完成的主要任务
- 侧栏统一文案「系统设置」；有 `admin.user|role|model` 任一即可显示
- 页签：「权限设置」（原角色权限）、新增「向量模型」、对话「模型管理」区分说明
- 表 `system_settings` + API `GET/PUT/POST .../embedding-settings[/test]`；库配置优先于 `.env`
- `embedding_client` 读进程缓存；保存后即时开关 V2
- 给 `FY0354` 补挂 `admin` 角色以便本人可见入口
- 前端 `npm run build`；单测 7 项通过；清理 `t_ag_*` 7 个

### 关键决策和解决方案
- 不新建独立路由树，沿用 `/admin`
- 密钥脱敏返回；留空密钥保留原值；`clear_api_key` 清空库密钥
- 看不到入口根因：账号仅有 member，无 admin.*

### 使用的技术栈
Vue3 Element Plus、FastAPI、PostgreSQL、httpx、pytest

### 修改了哪些文件
- `web-pc/src/layouts/MainLayout.vue`、`web-pc/src/views/AdminView.vue`（含 dist 构建）
- `backend/app/models/__init__.py`、`core/migrations.py`、`main.py`
- `backend/app/services/embedding_settings.py`、`embedding_client.py`
- `backend/app/api/admin.py`
- `backend/tests/test_embedding_settings_admin.py`、`test_agent_memory.py`
- `README.md`（本条）

### 测试数据
- 删除单测账号 `t_ag_*` 7 个；未动正式业务立项

## 会话总结（2026-10-09）— 模型管理整合向量配置

### 会话的主要目的
把「向量模型」与「模型管理」合并为一个页签，避免两处入口。

### 完成的主要任务
- `AdminView`：去掉独立「向量模型」页签；在「模型管理」内分两块——长期记忆向量配置 + 对话 LLM 列表
- 默认 tab：仅有 `admin.model` 时落到 `models`
- 重新构建前端 dist

### 关键决策和解决方案
- 后端 API 不变（仍用 embedding-settings）；只改前端信息架构

### 使用的技术栈
Vue3、Element Plus、Vite

### 修改了哪些文件
- `web-pc/src/views/AdminView.vue`、`web-pc/dist/*`
- `README.md`（本条）

### 测试数据
- 未写业务库

## 会话总结（2026-10-10）— 保留工作台对话模型（案例打标）

### 会话的主要目的
确认 142 上「添加对话模型」是否还需要：选 C，保留，专供案例库等后台能力。

### 完成的主要任务
- 产品结论：助手对话模型在 DSH；工作台 LlmModel 仍留给方案库自动打标等
- `AdminView` 文案改为「工作台 · 对话模型（案例打标等）」，避免与 DSH 混淆
- 重新构建前端 dist

### 关键决策和解决方案
- 不删除对话模型 CRUD；不改后端 `resolve_llm` / 案例打标链路

### 使用的技术栈
Vue3、Vite

### 修改了哪些文件
- `web-pc/src/views/AdminView.vue`、`web-pc/dist/*`
- `README.md`（本条）

### 测试数据
- 未写业务库

## 会话总结（2026-10-10）— 配置 OpenRouter 向量模型

### 会话的主要目的
把长期记忆向量检索（V2）接到 OpenRouter 免费 embeddings，在本机 142 启用。

### 完成的主要任务
- 备份后写入 `system_settings`：模型 `nvidia/nemotron-3-embed-1b:free`，Base `https://openrouter.ai/api/v1`，密钥仅存库（脱敏可见）
- 探活成功：向量维度 2048；重启后端使进程缓存生效
- 更新 `.env.example` 与管理页占位提示（不含真实密钥）；重建 `web-pc` dist

### 关键决策和解决方案
- 沿用现有 OpenAI 兼容 `embedding_client`，不改协议；库配置优先于 `.env`
- 密钥不进 git / README；建议在 OpenRouter 控制台轮换已在聊天中暴露过的 Key

### 使用的技术栈
FastAPI、httpx、PostgreSQL `system_settings`、OpenRouter Embeddings API、Vue3

### 修改了哪些文件
- 库表 `system_settings`（embedding_* 三项，本机 `project_workbench_agent`）
- `backend/.env.example`
- `web-pc/src/views/AdminView.vue`、`web-pc/dist/*`
- `README.md`（本条）
- 备份：`/home/aiworkbench/backups/project_workbench_agent_20261010_before_openrouter_embed.sql.gz`

### 测试数据
- 未造测试账号/项目；未 purge

## 会话总结（2026-10-12）— 同步到 GitHub（受阻待确认）

### 会话的主要目的
把本机 `/home/aiworkbench`（142）代码同步推送到 GitHub。

### 完成的主要任务
- 检查：目录**尚未**初始化 Git（无 `.git`）；已有 `.gitignore`
- 检查：本机可访问 `github.com`；本机 SSH 公钥**未**登记到 GitHub（`Permission denied (publickey)`）
- 检查：未安装 `gh`、无 GitHub Token/凭证、项目内无已知远程仓库地址
- **未执行** `git init` / 提交 / 推送（缺目标仓库与登录方式）

### 关键决策和解决方案
- 等用户提供：① GitHub 仓库地址（或是否新建）；② 登录方式（在 GitHub 添加本机 SSH 公钥，或提供可用的 Personal Access Token / 登录）
- 敏感信息不进仓库；推送前会再核对 `.gitignore`（排除 `.env`、`.venv`、`node_modules`、`backups` 等）

### 使用的技术栈
Git、SSH、GitHub

### 修改了哪些文件
- `README.md`（本条）

### 测试数据
- 无

## 会话总结（2026-10-12）— 同步到 GitHub（完成）

### 会话的主要目的
把 142 `/home/aiworkbench` 推到 GitHub；仓库公开，但周报原文不公开。

### 完成的主要任务
- 用户添加本机 SSH 公钥后认证成功（账号 `sunzhenzhen2-hub`）
- `git init` + 首次提交；`.gitignore` 排除密钥、依赖、备份、运行数据
- 按用户要求：**公开仓库**，并从提交中移除 `docs/*周报汇总/` 下员工周报 Excel（本机仍保留）
- 用户在 GitHub 新建空仓库后，已 `git push -u origin main` 成功

### 关键决策和解决方案
- 远程：`git@github.com:sunzhenzhen2-hub/aiworkbench.git`（Public）
- 周报汇总目录与 `*周报*.xlsx/xls` 写入 `.gitignore`，避免以后误传
- 密钥仍只留服务器（`backend/.env` 等不进仓库）

### 使用的技术栈
Git、SSH、GitHub

### 修改了哪些文件
- `.gitignore`
- `README.md`（本条及上条受阻记录）
- 本地 Git 仓库与远程 `origin/main`

### 测试数据
- 无；未改业务库、未 purge

### 仓库地址
- https://github.com/sunzhenzhen2-hub/aiworkbench
