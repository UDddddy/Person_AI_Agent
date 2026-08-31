from pydantic_settings import BaseSettings,SettingsConfigDict

class Settings(BaseSettings):
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    model_config = SettingsConfigDict(env_file=".env",
                                      env_file_encoding="utf-8")
setting = Settings()

