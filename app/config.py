from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    google_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    database_url: str = "postgresql+asyncpg://legal:legal@localhost:5434/legal"
    jwt_secret: str = "dev-secret-change-me"


settings = Settings()
