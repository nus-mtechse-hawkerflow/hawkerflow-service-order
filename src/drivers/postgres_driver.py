import os
from pathlib import Path

from sqlalchemy import URL

from configurations.app_config import Datasource
from drivers.driver import Driver


class PostgresDriver(Driver):
    def __init__(self, config: Datasource):
        super().__init__()
        self._config = config
        self._root = os.getenv("PROJECT_PATH", os.getcwd())

    def get_connection(self) -> str | URL:
        """
        Constructs a PostgreSQL connection
        :return: The connection URL
        """
        if self._connection_url is None:
            self._connection_url = URL.create(
                self._config.database.driver_name,
                self._config.options.user.get_secret_value(),
                self._config.options.password.get_secret_value(),
                self._config.database.host,
                self._config.database.port,
                self._config.database.name
            )

        return self._connection_url
