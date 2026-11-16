from pydantic import BaseModel, ConfigDict, Field


class WAText(BaseModel):
    body: str


class WAMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    sender: str = Field(alias="from")
    type: str
    text: WAText | None = None


class WAValue(BaseModel):
    model_config = ConfigDict(extra="ignore")
    messages: list[WAMessage] = []  # delivery "statuses" are ignored


class WAChange(BaseModel):
    model_config = ConfigDict(extra="ignore")
    field: str = ""
    value: WAValue = WAValue()


class WAEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    changes: list[WAChange] = []


class WebhookPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    entry: list[WAEntry] = []

    def all_messages(self) -> list[WAMessage]:
        return [m for e in self.entry for c in e.changes for m in c.value.messages]
