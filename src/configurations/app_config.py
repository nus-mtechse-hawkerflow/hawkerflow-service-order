import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
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
    name: str = os.getenv("DB_NAME", "hawkerflow")
    host: str = os.getenv("DB_HOST", "localhost")
    port: int = int(os.getenv("DB_PORT", 5432))


class Driver(BaseSettings):
    package: str
    driver_class: str


class DatabaseOptions(BaseSettings):
    user: str = os.getenv("DB_USERNAME", "user")
    password: str = os.getenv("DB_PASSWORD", "root")
    echo: bool


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


class EventsConfig(BaseSettings):
    enabled: bool = True
    topic_arn: str | None = None
    notification_queue_url: str = ""
    region_name: str = "ap-southeast-1"
    endpoint_url: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None


class AppConfig(BaseSettings):
    service: Service
    datasource: Datasource
    sqs: SqsConfig | None = None
    events: EventsConfig = Field(default_factory=EventsConfig)

    model_config = SettingsConfigDict(
        yaml_file=Path((os.getenv('PROJECT_ROOT')) or '.') / 'resources' / "config.yml"
    )

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

