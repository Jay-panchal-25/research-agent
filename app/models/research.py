"""Shapes of the JSON the LLM must return at each step."""
from typing import Literal, TypedDict

from pydantic import BaseModel, Field, field_validator


class ResearchState(TypedDict, total=False):
    # input
    job_id: str
    phone: str
    query: str
    skip_clarify: bool
    report_id: str
    # router
    intent: str
    depth: str
    clarifying_question: str
    # planning / research
    brief: str
    sub_questions: list[str]
    pending_questions: list[str]
    iteration: int
    evidence: list[dict]
    critique: dict
    verified: list[dict]
    # output
    reply: str
    needs_clarification: bool
    report: str
    title: str
    sources: list[dict]
    send_pdf: bool


class ResearcherInput(TypedDict):
    job_id: str
    report_id: str
    phone: str
    sub_question: str


def _lower(v):
    return v.strip().lower() if isinstance(v, str) else v


class RouterOut(BaseModel):
    intent: Literal["chat", "research", "followup", "clarify"]
    depth: Literal["quick", "deep"] = "deep"
    clarifying_question: str = ""

    @field_validator("intent", "depth", mode="before")
    @classmethod
    def lower_enums(cls, v):
        return _lower(v)


class PlanOut(BaseModel):
    brief: str
    sub_questions: list[str] = Field(min_length=1)


class Action(BaseModel):
    action: Literal["search", "academic", "read", "finish"]
    query: str = ""
    urls: list[str] = []

    @field_validator("action", mode="before")
    @classmethod
    def lower_action(cls, v):
        return _lower(v)

    @field_validator("urls", mode="before")
    @classmethod
    def urls_to_list(cls, v):
        if isinstance(v, str):
            return [v]
        return v or []


class ExtractItem(BaseModel):
    claim: str
    quote: str
    source: int


class ExtractOut(BaseModel):
    items: list[ExtractItem] = []


class CritiqueOut(BaseModel):
    sufficient: bool = True
    gaps: list[str] = []
    contradictions: list[str] = []
    follow_up_questions: list[str] = []


class Verdict(BaseModel):
    id: int
    verdict: Literal["yes", "partial", "no"]

    @field_validator("verdict", mode="before")
    @classmethod
    def lower_verdict(cls, v):
        return _lower(v)


class VerifyOut(BaseModel):
    verdicts: list[Verdict] = []
