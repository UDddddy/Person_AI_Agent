from pydantic_settings import BaseSettings,SettingsConfigDict

class Settings(BaseSettings):
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    llm_provider: str = "openai_compat"  # 阶段7：provider 选择，支持 openai_compat / mock
    model_config = SettingsConfigDict(env_file=".env",
                                      env_file_encoding="utf-8",
                                      extra = "ignore")
setting = Settings()
