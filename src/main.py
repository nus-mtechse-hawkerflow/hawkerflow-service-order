from configurations.app_config import AppConfig
from endpoints.order_routes import order_router

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from lifecycle.lifespan import startup


class HawkerFlowOrder:
    def __init__(self):
        self._app: FastAPI | None = None
        self._config = AppConfig()

        self._init_app()

    def start(self):
        """
        Starting point for the app.
        1. Loads all configurations.
        2. Initialise the app with configurations loaded.
        """

        uvicorn.run(
            self._app,
            host=self._config.service.host,
            port=self._config.service.port
        )


    def _init_app(self):
        """
        Initialise and configures the FastAPI application
        """
        self._app = FastAPI(
            title="Hawkerflow Service Order",
            docs_url=self._config.service.docs_url,
            redoc_url=self._config.service.redoc_url,
            root_path=self._config.service.root_path,
            lifespan=startup
        )

        self._add_middleware()
        self._include_routers()

    def _add_middleware(self):
        self._app.add_middleware(TrustedHostMiddleware)
        self._app.add_middleware(
            CORSMiddleware,
            allow_origins=self._config.service.allow_origins,
            allow_credentials=self._config.service.credentials,
            allow_methods=self._config.service.methods,
            allow_headers=self._config.service.headers,
        )

    def _include_routers(self):
        self._app.include_router(order_router)


if __name__ == "__main__":
    HawkerFlowOrder().start()
