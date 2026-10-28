from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "BeatleXML"
    app_env: str = "development"
    secret_key: str = "dev-secret-key"
    database_url: str = "postgresql://usuario:senha@localhost:5432/beatlexml"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60


settings = Settings()
