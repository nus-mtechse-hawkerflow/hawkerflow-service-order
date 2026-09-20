from pydantic import BaseModel


class StallOrderUpdate(BaseModel):
    status: str
