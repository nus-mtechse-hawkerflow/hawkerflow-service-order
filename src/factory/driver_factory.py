import logging
from abc import ABC, abstractmethod
from typing import TypeVar

T = TypeVar("T")


class DriverFactory[T](ABC):
    def __init__(self):
        self._log = logging.getLogger("hawkerflow")

    @abstractmethod
    def create_driver(self, package: str, class_name: str) -> T:
        """
        Creates the driver object.
        """
