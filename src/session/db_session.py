from sqlalchemy import URL
from sqlalchemy.engine import Engine
from sqlmodel import create_engine

from configurations.app_config import Datasource
from factory.database_factory import DatabaseFactory


class DBSession:
    def __init__(self, config: Datasource):
        self._engine: Engine | None = None
        self._config: Datasource = config

    @property
    def engine(self) -> Engine | None:
        """
        Returns the database engine. If the engine is not created yet, it creates a new one.
        :return: The database engine.
        """
        if self._engine is None:
            self._get_connection()
            self._engine = create_engine(
                self._get_connection(),
                echo=self._config.options.echo
            )

        return self._engine

    @engine.setter
    def engine(self, db_engine: Engine | None) -> None:
        """
        Sets the database engine. This method can be used to set a custom database engine.
        :param db_engine: The database engine to set.
        """
        self._engine = db_engine

    def _get_connection(self) -> str | URL:
        """
        Loads the database driver and creates a connection URL.
        """
        driver = DatabaseFactory().create_driver(
            self._config.driver.package,
            self._config.driver.driver_class
        )

        return driver(self._config).get_connection()
