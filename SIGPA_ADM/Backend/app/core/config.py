import logging

from pydantic_settings import BaseSettings, SettingsConfigDict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


class Settings(BaseSettings):
    PROJECT_NAME: str = "SIGPA"

    META_WHATSAPP_TOKEN: str = ""
    META_PHONE_NUMBER_ID: str = ""
    META_WABA_ID: str = ""
    META_VERIFY_TOKEN: str = ""
    META_API_URL: str = ""

    DATABASE_URL: str = ""

    OPENAI_API_KEY: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
