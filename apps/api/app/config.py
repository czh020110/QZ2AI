from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"

    embedding_provider: str = "dashscope"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-v4"
    embedding_dimensions: int | None = Field(default=None, ge=1)
    embedding_text_type: str = "document"

    rerank_provider: str = "dashscope"
    rerank_api_key: str = ""
    rerank_model: str = "qwen3-rerank"
    rerank_top_n: int | None = Field(default=None, ge=1, le=500)
    rerank_enabled: bool = True
    rerank_instruct: str = ""

    chroma_dir: str = "/data/chroma"
    notes_dir: str = "/data/notes"
    docstore_path: str = "/data/docstore.json"
    db_path: str = "/data/blog.db"
    chroma_collection: str = "blog"

    # 远程同步配置：支持 COS 或 GitHub
    remote_type: str = Field(default="cos", pattern="^(cos|github)$")

    # COS 笔记同步：notes_cos_prefix 指定 OSS 中的笔记文件夹路径
    # sync.sh 会根据 cos_bucket + notes_cos_prefix 拼出完整 COS_SYNC_SOURCE
    cos_secret_id: str = ""
    cos_secret_key: str = ""
    cos_region: str = "ap-shanghai"
    cos_endpoint: str = "cos.ap-shanghai.myqcloud.com"
    cos_bucket: str = ""
    notes_cos_prefix: str = ""

    # GitHub 笔记同步：只读拉取，不允许本地提交推送
    github_repo_url: str = ""
    github_branch: str = "main"
    notes_github_prefix: str = ""  # 仓库内笔记子目录，如 blog/online（留空表示根目录）
    github_token: str = ""  # GitHub Personal Access Token（私有仓库需要）
    git_proxy: str = ""  # git clone/pull 代理，如 http://172.17.0.1:7890（留空=直连）
    git_accelerator: str = ""  # GitHub 加速镜像前缀，如 https://ghfast.top（留空=不加速）

    # 附件文件夹名列表（逗号分隔），这些文件夹不会被索引为博客文章
    # 但仍会被同步下载和 Quartz 构建复制，保证文章中的图片正常显示
    notes_assets_folders: str = "assets"

    # 自动同步：COS 文件变动时通过 SCF webhook 触发自动拉取 + reindex + Quartz 重建
    auto_sync_enabled: bool = True
    webhook_secret: str = ""
    debounce_seconds: int = Field(default=30, ge=5, le=300)
    # 定时同步间隔（秒），0 表示禁用定时同步，仅靠 webhook 触发
    sync_interval_seconds: int = Field(default=1800, ge=0)

    # 反馈邮件通知
    notify_enabled: bool = False
    # 同步失败邮件通知：sync_failed/reindex_failed/build_failed 时触发
    sync_notify_enabled: bool = False
    notify_interval_seconds: int = Field(default=1800, ge=60, le=86400)  # 检查间隔，默认30分钟
    notify_email: str = ""  # 接收通知的邮箱
    mail_server: str = ""
    mail_port: int = 465
    mail_username: str = ""
    mail_password: str = ""  # SMTP 授权码/应用专用密码

    similarity_top_k: int = 5
    llm_mock: bool = False
    reindex_token: str = ""
    admin_token: str = ""
    allowed_origins: str = "http://localhost"

    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    embedding_timeout_seconds: float = Field(default=60.0, gt=0)
    rerank_timeout_seconds: float = Field(default=60.0, gt=0)
    http_max_retries: int = Field(default=2, ge=0, le=5)
    http_retry_backoff_seconds: float = Field(default=1.0, ge=0)
    embedding_batch_size: int = Field(default=10, ge=1, le=100)

    @property
    def allowed_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def assets_folder_set(self) -> set[str]:
        return {f.strip() for f in self.notes_assets_folders.split(",") if f.strip()}

    @property
    def sensitive_keys(self) -> set[str]:
        return {
            "llm_api_key",
            "embedding_api_key",
            "rerank_api_key",
            "reindex_token",
            "admin_token",
            "cos_secret_id",
            "cos_secret_key",
            "webhook_secret",
            "github_token",
            "mail_password",
        }



@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    """清除配置缓存，使下次调用 get_settings() 重新读取 .env"""
    get_settings.cache_clear()
