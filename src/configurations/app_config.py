import os
from pathlib import Path

from pydantic import SecretStr, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource
)


class Service(BaseSettings):
    title: str
    root_path: str
    port: int
    host: str
    allow_origins: list[str]
    methods: list[str]
    headers: list[str]
    docs_url: str
    redoc_url: str
    reload: bool
    scheme: str
    credentials: bool


class Database(BaseSettings):
    connection_url: str
    driver_name: str
    name: str
    host: str
    port: int


class Driver(BaseSettings):
    package: str
    driver_class: str


class DatabaseOptions(BaseSettings):
    user: SecretStr = Field(alias="postgres.user")
    password: SecretStr = Field(alias="postgres.password")
    echo: bool

    model_config = SettingsConfigDict(secrets_dir=Path(os.getenv('PROJECT_ROOT') or ".") / "vault")


class Datasource(BaseSettings):
    driver: Driver
    database: Database
    options: DatabaseOptions


class SqsConfig(BaseSettings):
    enabled: bool = True
    queue_url: str = ""
    region_name: str = ""
    endpoint_url: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    wait_time_seconds: int = 20
    max_number_of_messages: int = 10
    visibility_timeout: int = 60


class AppConfig(BaseSettings):
    service: Service
    datasource: Datasource
    sqs: SqsConfig | None = None

    model_config = SettingsConfigDict(yaml_file=Path((os.getenv('PROJECT_ROOT')) or '.') / 'resources' / "config.yml")

    @classmethod
    def settings_customise_sources(
            cls,
            settings_cls: type[BaseSettings],
            init_settings: PydanticBaseSettingsSource,
            env_settings: PydanticBaseSettingsSource,
            dotenv_settings: PydanticBaseSettingsSource,
            file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
            init_settings,
        )

