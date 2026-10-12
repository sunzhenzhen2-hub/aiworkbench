from sqlalchemy import text

from app.core.database import engine


async def run_migrations() -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                "must_change_password BOOLEAN NOT NULL DEFAULT TRUE"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                "job_title VARCHAR(64) NOT NULL DEFAULT ''"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE project_members ADD COLUMN IF NOT EXISTS "
                "member_role VARCHAR(16) NOT NULL DEFAULT 'member'"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE blockers ADD COLUMN IF NOT EXISTS "
                "lead_acked_at TIMESTAMPTZ"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE blockers ADD COLUMN IF NOT EXISTS "
                "lead_acked_by_id INTEGER"
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS llm_models (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(128) NOT NULL,
                    provider VARCHAR(32) NOT NULL DEFAULT 'openai_compatible',
                    api_base VARCHAR(512) NOT NULL,
                    api_key VARCHAR(512) NOT NULL DEFAULT '',
                    model_name VARCHAR(128) NOT NULL,
                    purpose VARCHAR(32) NOT NULL DEFAULT 'report_coach',
                    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    is_default BOOLEAN NOT NULL DEFAULT FALSE,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE progress_reports ADD COLUMN IF NOT EXISTS period_end DATE"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE progress_report_items ADD COLUMN IF NOT EXISTS "
                "task_title VARCHAR(120) NOT NULL DEFAULT ''"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE progress_report_items ALTER COLUMN project_id DROP NOT NULL"
            )
        )
        await conn.execute(
            text(
                """
                UPDATE progress_reports
                SET period_end = period_start
                WHERE period_end IS NULL
                """
            )
        )
        # 已有项目：把 owner 同步为主负责人
        await conn.execute(
            text(
                """
                UPDATE project_members pm
                SET member_role = 'lead'
                FROM projects p
                WHERE pm.project_id = p.id
                  AND pm.user_id = p.owner_id
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS oauth_state (
                    state VARCHAR(64) PRIMARY KEY,
                    redirect_uri VARCHAR(512) NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
        )
        # 任务交办：负责人字段扩展 + 辅助人 + 流水账
        await conn.execute(
            text(
                "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS "
                "created_by_id INTEGER"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS "
                "assignment_type VARCHAR(16) NOT NULL DEFAULT 'self'"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS "
                "result_text TEXT NOT NULL DEFAULT ''"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS "
                "updated_at TIMESTAMPTZ DEFAULT NOW()"
            )
        )
        await conn.execute(
            text(
                """
                UPDATE tasks
                SET created_by_id = assignee_id
                WHERE created_by_id IS NULL
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS task_assistants (
                    id SERIAL PRIMARY KEY,
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (task_id, user_id)
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS task_events (
                    id SERIAL PRIMARY KEY,
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    actor_id INTEGER NOT NULL REFERENCES users(id),
                    action VARCHAR(32) NOT NULL,
                    payload JSONB,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
        )
        # 周报任务链：跨周串联 + 可选挂交办
        await conn.execute(
            text(
                "ALTER TABLE progress_report_items ADD COLUMN IF NOT EXISTS "
                "chain_id VARCHAR(36)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_progress_report_items_chain_id "
                "ON progress_report_items (chain_id)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE progress_report_items ADD COLUMN IF NOT EXISTS "
                "linked_task_id INTEGER"
            )
        )
        # 方案库
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS solutions (
                    id SERIAL PRIMARY KEY,
                    title VARCHAR(256) NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    original_filename VARCHAR(256) NOT NULL,
                    stored_path VARCHAR(512) NOT NULL,
                    mime_type VARCHAR(128) NOT NULL DEFAULT '',
                    file_size INTEGER NOT NULL DEFAULT 0,
                    uploader_id INTEGER NOT NULL REFERENCES users(id),
                    status VARCHAR(16) NOT NULL DEFAULT 'pending',
                    error_message TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS solution_tags (
                    id SERIAL PRIMARY KEY,
                    solution_id INTEGER NOT NULL REFERENCES solutions(id) ON DELETE CASCADE,
                    name VARCHAR(64) NOT NULL,
                    source VARCHAR(16) NOT NULL DEFAULT 'ai',
                    UNIQUE (solution_id, name)
                )
                """
            )
        )
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_solution_tags_name ON solution_tags (name)")
        )
        # 周报任务卡工作标签（JSON 数组）
        await conn.execute(
            text(
                "ALTER TABLE progress_report_items ADD COLUMN IF NOT EXISTS "
                "tags JSONB NOT NULL DEFAULT '[]'::jsonb"
            )
        )
        # 任务级别：P0～P4（历史 critical/watch/normal 迁移）
        await conn.execute(
            text(
                "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS "
                "priority VARCHAR(16) NOT NULL DEFAULT 'p3'"
            )
        )
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_tasks_priority ON tasks (priority)")
        )
        await conn.execute(
            text(
                """
                UPDATE tasks SET priority = CASE priority
                    WHEN 'critical' THEN 'p0'
                    WHEN 'watch' THEN 'p1'
                    WHEN 'normal' THEN 'p3'
                    ELSE priority
                END
                WHERE priority IN ('critical', 'watch', 'normal')
                """
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE tasks ALTER COLUMN priority SET DEFAULT 'p3'"
            )
        )
        # 项目分级：普通 / 重点 / 日常（历史 red/yellow/green 迁移）
        await conn.execute(
            text(
                """
                UPDATE projects SET level = CASE level
                    WHEN 'red' THEN 'key'
                    WHEN 'yellow' THEN 'key'
                    WHEN 'green' THEN 'normal'
                    ELSE level
                END
                WHERE level IN ('red', 'yellow', 'green')
                """
            )
        )
        await conn.execute(
            text(
                "UPDATE projects SET level = 'daily' "
                "WHERE name = '日常任务' AND level <> 'daily'"
            )
        )
        await conn.execute(
            text("ALTER TABLE projects ALTER COLUMN level SET DEFAULT 'normal'")
        )
        # 交办接收与拆解
        await conn.execute(
            text(
                "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS "
                "acceptance_status VARCHAR(16) NOT NULL DEFAULT 'accepted'"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS "
                "accept_by TIMESTAMPTZ"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS "
                "accepted_at TIMESTAMPTZ"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS "
                "checklist JSONB"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_tasks_acceptance_status "
                "ON tasks (acceptance_status)"
            )
        )
        # 历史交办未接收标记：delegated 且无 accepted_at 的保持 pending 需人工；默认已 accepted
        await conn.execute(
            text(
                """
                UPDATE tasks
                SET acceptance_status = 'accepted',
                    accepted_at = COALESCE(accepted_at, created_at, NOW())
                WHERE acceptance_status IS NULL
                   OR acceptance_status = ''
                """
            )
        )
        # 周报一阶段评价落库
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS progress_report_reviews (
                    id SERIAL PRIMARY KEY,
                    report_id INTEGER NOT NULL REFERENCES progress_reports(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    period_type VARCHAR(8) NOT NULL DEFAULT 'week',
                    period_start DATE NOT NULL,
                    phase INTEGER NOT NULL DEFAULT 1,
                    summary TEXT NOT NULL DEFAULT '',
                    strengths JSONB NOT NULL DEFAULT '[]'::jsonb,
                    risks JSONB NOT NULL DEFAULT '[]'::jsonb,
                    completion_comment TEXT NOT NULL DEFAULT '',
                    source VARCHAR(16) NOT NULL DEFAULT 'rule',
                    model_name VARCHAR(128) NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (report_id, phase)
                )
                """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_progress_report_reviews_user "
                "ON progress_report_reviews (user_id, period_start DESC)"
            )
        )
        # 周报下周计划 → 本人待办溯源
        await conn.execute(
            text(
                "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS "
                "source_report_id INTEGER REFERENCES progress_reports(id)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS "
                "source_ref VARCHAR(64)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_tasks_source_report_id "
                "ON tasks (source_report_id)"
            )
        )
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_tasks_source_ref ON tasks (source_ref)")
        )
        # 清理早期「复制给管理员」的镜像待办（assignment_type=assigned 仅用于该实验）
        await conn.execute(
            text(
                "DELETE FROM task_events WHERE task_id IN ("
                "SELECT id FROM tasks WHERE source = 'report_next' "
                "AND assignment_type = 'assigned')"
            )
        )
        await conn.execute(
            text(
                "DELETE FROM task_assistants WHERE task_id IN ("
                "SELECT id FROM tasks WHERE source = 'report_next' "
                "AND assignment_type = 'assigned')"
            )
        )
        await conn.execute(
            text(
                "UPDATE progress_report_items SET linked_task_id = NULL "
                "WHERE linked_task_id IN ("
                "SELECT id FROM tasks WHERE source = 'report_next' "
                "AND assignment_type = 'assigned')"
            )
        )
        await conn.execute(
            text(
                "DELETE FROM tasks WHERE source = 'report_next' "
                "AND assignment_type = 'assigned'"
            )
        )
        # 任务目标 + 任务评价
        await conn.execute(
            text(
                "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS "
                "goal TEXT NOT NULL DEFAULT ''"
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS task_reviews (
                    id SERIAL PRIMARY KEY,
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    summary TEXT NOT NULL DEFAULT '',
                    strengths JSONB NOT NULL DEFAULT '[]'::jsonb,
                    risks JSONB NOT NULL DEFAULT '[]'::jsonb,
                    suggestions JSONB NOT NULL DEFAULT '[]'::jsonb,
                    source VARCHAR(16) NOT NULL DEFAULT 'rule',
                    model_name VARCHAR(128) NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    CONSTRAINT uq_task_reviews_task UNIQUE (task_id)
                )
                """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_task_reviews_user "
                "ON task_reviews (user_id)"
            )
        )
        # 项目类型收敛：software / integration / fusion
        await conn.execute(
            text(
                """
                UPDATE projects SET project_type = 'fusion'
                WHERE name LIKE '%智安校园%'
                   OR project_type = 'operation'
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE projects SET project_type = 'integration'
                WHERE project_type IN (
                    'ops', 'engineering', 'procurement', 'hardware'
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE projects SET project_type = 'software'
                WHERE project_type NOT IN ('software', 'integration', 'fusion')
                """
            )
        )
        # 交办闭环：任务佐证附件
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS task_attachments (
                    id SERIAL PRIMARY KEY,
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    uploader_id INTEGER NOT NULL REFERENCES users(id),
                    original_filename VARCHAR(256) NOT NULL,
                    stored_path VARCHAR(512) NOT NULL,
                    mime_type VARCHAR(128) NOT NULL DEFAULT '',
                    file_size INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_task_attachments_task_id "
                "ON task_attachments (task_id)"
            )
        )
        # 周报/日报证明材料附件
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS progress_report_attachments (
                    id SERIAL PRIMARY KEY,
                    report_id INTEGER NOT NULL REFERENCES progress_reports(id) ON DELETE CASCADE,
                    uploader_id INTEGER NOT NULL REFERENCES users(id),
                    original_filename VARCHAR(256) NOT NULL,
                    stored_path VARCHAR(512) NOT NULL,
                    mime_type VARCHAR(128) NOT NULL DEFAULT '',
                    file_size INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_progress_report_attachments_report_id "
                "ON progress_report_attachments (report_id)"
            )
        )
        # 周报附件可挂到具体任务条目
        await conn.execute(
            text(
                "ALTER TABLE progress_report_attachments ADD COLUMN IF NOT EXISTS "
                "item_id INTEGER REFERENCES progress_report_items(id) ON DELETE SET NULL"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_progress_report_attachments_item_id "
                "ON progress_report_attachments (item_id)"
            )
        )
        # 任务可选归属进度阶段
        await conn.execute(
            text(
                "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS "
                "phase_id INTEGER REFERENCES phases(id) ON DELETE SET NULL"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_tasks_phase_id ON tasks (phase_id)"
            )
        )
        # 通知模板 + 发送日志
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS notification_templates (
                    id SERIAL PRIMARY KEY,
                    event_key VARCHAR(64) NOT NULL UNIQUE,
                    name VARCHAR(128) NOT NULL,
                    description VARCHAR(256) NOT NULL DEFAULT '',
                    title_template VARCHAR(256) NOT NULL,
                    body_template TEXT NOT NULL,
                    channel_inbox BOOLEAN NOT NULL DEFAULT TRUE,
                    channel_dingtalk BOOLEAN NOT NULL DEFAULT TRUE,
                    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    schedule_cron VARCHAR(64) NOT NULL DEFAULT '',
                    max_per_day INTEGER NOT NULL DEFAULT 0,
                    quiet_start TIME,
                    quiet_end TIME,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
        )
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_notification_templates_event_key "
                "ON notification_templates (event_key)"
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS notification_delivery_logs (
                    id SERIAL PRIMARY KEY,
                    event_key VARCHAR(64) NOT NULL,
                    template_id INTEGER REFERENCES notification_templates(id) ON DELETE SET NULL,
                    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    channel VARCHAR(16) NOT NULL,
                    title VARCHAR(256) NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    status VARCHAR(16) NOT NULL DEFAULT 'success',
                    error_message TEXT NOT NULL DEFAULT '',
                    ref_type VARCHAR(32) NOT NULL DEFAULT '',
                    ref_id VARCHAR(64) NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_notification_delivery_logs_created_at "
                "ON notification_delivery_logs (created_at DESC)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_notification_delivery_logs_event_key "
                "ON notification_delivery_logs (event_key, created_at DESC)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_notification_delivery_logs_user_id "
                "ON notification_delivery_logs (user_id, created_at DESC)"
            )
        )
        # 确保删除用户时发送日志 user_id 置空（兼容 create_all 无 ondelete 的情况）
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                  IF EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE table_name = 'notification_delivery_logs'
                      AND constraint_name = 'notification_delivery_logs_user_id_fkey'
                  ) THEN
                    ALTER TABLE notification_delivery_logs
                      DROP CONSTRAINT notification_delivery_logs_user_id_fkey;
                  END IF;
                END $$;
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE notification_delivery_logs
                  ADD CONSTRAINT notification_delivery_logs_user_id_fkey
                  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS assist_guidance_notes (
                    id SERIAL PRIMARY KEY,
                    target_user_id INTEGER NOT NULL
                        REFERENCES users(id) ON DELETE CASCADE,
                    project_id INTEGER
                        REFERENCES projects(id) ON DELETE SET NULL,
                    period_start DATE,
                    content TEXT NOT NULL DEFAULT '',
                    action_items JSONB NOT NULL DEFAULT '[]'::jsonb,
                    source VARCHAR(16) NOT NULL DEFAULT 'admin',
                    created_by_id INTEGER NOT NULL REFERENCES users(id),
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_assist_guidance_notes_target "
                "ON assist_guidance_notes (target_user_id, created_at DESC)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_assist_guidance_notes_project "
                "ON assist_guidance_notes (project_id)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE assist_guidance_notes "
                "ADD COLUMN IF NOT EXISTS read_at TIMESTAMPTZ"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE assist_guidance_notes "
                "ADD COLUMN IF NOT EXISTS improved_at TIMESTAMPTZ"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE assist_guidance_notes "
                "ADD COLUMN IF NOT EXISTS improve_note TEXT"
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS agent_user_prefs (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    quiet_start TIME,
                    quiet_end TIME,
                    digest_mode VARCHAR(16) NOT NULL DEFAULT 'instant',
                    channel_inbox BOOLEAN NOT NULL DEFAULT TRUE,
                    channel_dingtalk BOOLEAN NOT NULL DEFAULT TRUE,
                    channel_dsh BOOLEAN NOT NULL DEFAULT TRUE,
                    prefer_p0 BOOLEAN NOT NULL DEFAULT TRUE,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS agent_drafts (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    kind VARCHAR(32) NOT NULL DEFAULT 'reassign',
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    status VARCHAR(16) NOT NULL DEFAULT 'pending',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    confirmed_at TIMESTAMPTZ
                )
                """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_agent_drafts_user "
                "ON agent_drafts (user_id, status)"
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS agent_nudge_logs (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    event_key VARCHAR(128) NOT NULL UNIQUE,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_agent_nudge_logs_user "
                "ON agent_nudge_logs (user_id)"
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS agent_memory_notes (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    kind VARCHAR(32) NOT NULL DEFAULT 'fact',
                    title VARCHAR(256) NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    source VARCHAR(32) NOT NULL DEFAULT 'agent',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_agent_memory_notes_user "
                "ON agent_memory_notes (user_id, kind)"
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS agent_memory_chunks (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    source_type VARCHAR(32) NOT NULL DEFAULT 'session',
                    source_ref VARCHAR(512) NOT NULL DEFAULT '',
                    text TEXT NOT NULL DEFAULT '',
                    embedding DOUBLE PRECISION[],
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    CONSTRAINT uq_memory_chunk_src UNIQUE (user_id, source_type, source_ref)
                )
                """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_agent_memory_chunks_user "
                "ON agent_memory_chunks (user_id, source_type)"
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS system_settings (
                    key VARCHAR(64) PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
        )

