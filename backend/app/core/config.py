from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql+asyncpg://workbench:workbench_dev@localhost:5432/project_workbench"
    secret_key: str = "dev-secret-key"
    access_token_expire_minutes: int = 60 * 24
    dingtalk_app_key: str = ""
    dingtalk_app_secret: str = ""
    dingtalk_corp_id: str = ""
    dingtalk_agent_id: str = ""
    dingtalk_login_sdk_url: str = (
        "https://g.alicdn.com/dingding/h5-dingtalk-login/0.30.1/ddlogin.js"
    )
    # VPN/反代时 Host 可能是内网 IP：固定扫码回调对外源（如 https://app.fywasu.com:18090）
    dingtalk_oauth_public_origin: str = ""
    openai_api_base: str = ""
    openai_api_key: str = ""
    # 方案库：本地上传目录与单文件上限（字节）
    solution_upload_dir: str = "data/solutions"
    solution_max_upload_bytes: int = 20 * 1024 * 1024
    # 任务佐证附件
    task_attachment_upload_dir: str = "data/task_attachments"
    task_attachment_max_upload_bytes: int = 20 * 1024 * 1024
    # 周报/日报证明材料
    report_attachment_upload_dir: str = "data/report_attachments"
    report_attachment_max_upload_bytes: int = 20 * 1024 * 1024
    # DSH 智能助手：一次性登录票。密钥不写文档。
    dsh_sso_secret: str = ""
    dsh_embed_origin: str = "https://app.fywasu.com:18090"
    dsh_internal_url: str = "http://127.0.0.1:3081"
    wb_to_dsh_secret: str = ""
    dsh_to_wb_secret: str = ""
    dsh_service_allowlist: str = "30.251.255.64,30.251.255.132,30.251.255.142,127.0.0.1"
    # 142 一人一助手：为 true 时工作台不再自己调大模型，智能只在 DSH
    agent_native: bool = False
    # 本机 dsh-web 启动时写下的开门令牌路径（不进 git）
    dsh_launch_token_file: str = "/run/dsh-launch-token"
    # 长期记忆 V2：向量模型（空 = 线上仍跑 V1 关键词检索）
    embedding_model: str = ""
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    # 每人 DSH 磁盘屋子根目录（只读抽会话进记忆索引）
    dsh_users_root: str = "/var/lib/dsh/users"

    @property
    def memory_vector_enabled(self) -> bool:
        return bool((self.embedding_model or "").strip() and (self.embedding_base_url or "").strip())


settings = Settings()
