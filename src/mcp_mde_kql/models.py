"""Public, JSON-native hunting result contracts."""

from pydantic import BaseModel, Field, JsonValue


class HuntingColumn(BaseModel):
    name: str
    type: str | None = None


class QueryResult(BaseModel):
    columns: list[HuntingColumn]
    rows: list[list[JsonValue]]
    returned_rows: int
    fetched_rows: int = Field(
        description="Rows in this upstream response, not the total matching count."
    )
    elapsed_ms: int = Field(description="Local wall time for the upstream request in milliseconds.")
    truncated: bool
    warnings: list[str] = Field(default_factory=list)


class PresenceProgress(BaseModel):
    status: str
    window: str = "P7D"
    total_tables: int
    checked_tables: int
    pending_tables: int
    empty_tables: int
    unknown_tables: int


class SchemaResult(BaseModel):
    table_name: str | None = None
    tables: list[str] = Field(default_factory=list)
    columns: list[HuntingColumn] = Field(default_factory=list)
    presence: PresenceProgress | None = None
    source: str
    complete: bool
    warnings: list[str] = Field(default_factory=list)
