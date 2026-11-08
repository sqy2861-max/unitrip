from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    llm_api_key: str
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    amap_key: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
