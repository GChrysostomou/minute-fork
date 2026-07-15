from datetime import datetime
from enum import StrEnum, auto
from typing import TypedDict
from uuid import UUID, uuid4

from sqlalchemy import TIMESTAMP, Column, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped
from sqlalchemy.sql.functions import now
from sqlmodel import Field, Relationship, SQLModel, col, func


class DialogueEntry(TypedDict):
    speaker: str
    text: str
    start_time: float
    end_time: float


# Create factory functions for columns to avoid reusing column objects
def created_datetime_column():
    return Column(TIMESTAMP(timezone=True), nullable=False, server_default=now(), default=None)


def updated_datetime_column():
    return Column(TIMESTAMP(timezone=True), nullable=False, server_default=now(), default=None)


class BaseTableMixin(SQLModel):
    # Note, we can't add created/updated_datetime Columns here, as each table needs its own instance of these Columns
    model_config = {  # noqa: RUF012
        "from_attributes": True,
    }

    id: UUID = Field(
        default_factory=uuid4, primary_key=True, sa_column_kwargs={"server_default": func.gen_random_uuid()}
    )


class JobStatus(StrEnum):
    AWAITING_START = auto()
    IN_PROGRESS = auto()
    COMPLETED = auto()
    FAILED = auto()


class ContentSource(StrEnum):
    MANUAL_EDIT = auto()
    AI_EDIT = auto()
    INITIAL_GENERATION = auto()


class MinuteVersion(BaseTableMixin, table=True):
    __tablename__ = "minute_version"
    created_datetime: datetime = Field(sa_column=created_datetime_column(), default=None)
    updated_datetime: datetime = Field(sa_column=updated_datetime_column(), default=None)
    minute_id: UUID = Field(foreign_key="minute.id", ondelete="CASCADE")
    minute: Mapped["Minute"] = Relationship(back_populates="minute_versions")
    hallucinations: list["Hallucination"] = Relationship(back_populates="minute_version", cascade_delete=True)
    html_content: str = Field(default="", sa_column_kwargs={"server_default": ""})
    status: JobStatus = Field(
        default=JobStatus.AWAITING_START, sa_column_kwargs={"server_default": JobStatus.AWAITING_START.name}
    )
    error: str | None = None
    ai_edit_instructions: str | None = Field(
        default=None, description="If the content source is an AI edit, store the instruction here"
    )

    content_source: ContentSource = Field(
        default=ContentSource.INITIAL_GENERATION,
        sa_column_kwargs={"server_default": ContentSource.INITIAL_GENERATION.name},
    )


class Minute(BaseTableMixin, table=True):
    __tablename__ = "minute"
    created_datetime: datetime = Field(sa_column=created_datetime_column(), default=None)
    updated_datetime: datetime = Field(sa_column=updated_datetime_column(), default=None)
    transcription_id: UUID = Field(foreign_key="transcription.id", ondelete="CASCADE")
    transcription: Mapped["Transcription"] = Relationship(back_populates="minutes")
    template_name: str = Field(default="General")
    user_template_id: UUID | None = Field(
        foreign_key="user_template.id", nullable=True, ondelete="SET NULL", default=None
    )
    user_template: "UserTemplate" = Relationship(back_populates="minutes")
    agenda: str | None = Field(nullable=True, default=None)
    minute_versions: Mapped[list["MinuteVersion"]] = Relationship(
        back_populates="minute",
        cascade_delete=True,
        sa_relationship_kwargs={"order_by": col(MinuteVersion.created_datetime).desc()},
    )


class HallucinationType(StrEnum):
    FACTUAL_FABRICATION = auto()
    NONSENSICAL = auto()
    CONTRADICTION = auto()
    MISLEADING = auto()
    OTHER = auto()


class Hallucination(BaseTableMixin, table=True):
    __tablename__ = "hallucination"
    created_datetime: datetime = Field(sa_column=created_datetime_column(), default=None)
    updated_datetime: datetime = Field(sa_column=updated_datetime_column(), default=None)
    minute_version_id: UUID = Field(foreign_key="minute_version.id", ondelete="CASCADE")
    minute_version: MinuteVersion = Relationship(back_populates="hallucinations")
    hallucination_type: HallucinationType = Field(description="Type of hallucination", default=HallucinationType.OTHER)
    hallucination_text: str | None = Field(description="Text of hallucination", default=None)
    hallucination_reason: str | None = Field(description="Reason for hallucination", default=None)


# Main models with table=True for DB tables
class User(BaseTableMixin, table=True):
    __tablename__ = "user"
    # Case-insensitive uniqueness on email — emails are stored lowercased
    # (see backend/api/dependencies/get_current_user.py), but this guards
    # against accidental case-variant duplicates regardless.
    __table_args__ = (Index("ix_user_email_lower", text("lower(email)"), unique=True),)
    created_datetime: datetime = Field(sa_column=created_datetime_column(), default=None)
    updated_datetime: datetime = Field(sa_column=updated_datetime_column(), default=None)
    email: str = Field(index=True)
    data_retention_days: int | None = Field(default=30, sa_column_kwargs={"server_default": "30"})
    transcriptions: list["Transcription"] = Relationship(back_populates="user")


class Recording(BaseTableMixin, table=True):
    __tablename__ = "recording"
    created_datetime: datetime = Field(sa_column=created_datetime_column(), default=None)
    user_id: UUID = Field(foreign_key="user.id", nullable=False)
    s3_file_key: str
    transcription_id: UUID | None = Field(default=None, foreign_key="transcription.id", ondelete="SET NULL")
    transcription: "Transcription" = Relationship(back_populates="recordings")


class Chat(BaseTableMixin, table=True):
    __tablename__ = "chat"
    created_datetime: datetime = Field(sa_column=created_datetime_column(), default=None)
    updated_datetime: datetime = Field(sa_column=updated_datetime_column(), default=None)
    transcription_id: UUID = Field(foreign_key="transcription.id", ondelete="CASCADE")
    transcription: Mapped["Transcription"] = Relationship(back_populates="chat")
    user_content: str = Field(default=None)
    assistant_content: str | None = Field(default=None)
    status: JobStatus = Field(
        default=JobStatus.AWAITING_START, sa_column_kwargs={"server_default": JobStatus.AWAITING_START.name}
    )
    error: str | None = Field(default=None)


class Transcription(BaseTableMixin, table=True):
    __tablename__ = "transcription"
    created_datetime: datetime = Field(sa_column=created_datetime_column(), default=None)
    updated_datetime: datetime = Field(sa_column=updated_datetime_column(), default=None)
    title: str | None = Field(default=None)
    dialogue_entries: list[DialogueEntry] | None = Field(default=None, sa_column=Column(JSONB))
    status: JobStatus = Field(
        default=JobStatus.AWAITING_START, sa_column_kwargs={"server_default": JobStatus.AWAITING_START.name}
    )
    error: str | None = Field(default=None)
    user: User | None = Relationship(back_populates="transcriptions")
    user_id: UUID | None = Field(default=None, foreign_key="user.id")
    minutes: list[Minute] = Relationship(
        back_populates="transcription",
        cascade_delete=True,
        sa_relationship_kwargs={"order_by": col(Minute.created_datetime).desc()},
    )

    # Kept old minute versions so we can migrate them
    legacy_minute_versions: list[dict] | None = Field(sa_column=Column(name="minute_versions", type_=JSONB), default=[])

    recordings: Mapped[list[Recording]] = Relationship(
        back_populates="transcription",
        sa_relationship_kwargs={"order_by": col(Recording.created_datetime).desc()},
    )
    chat: list[Chat] = Relationship(
        back_populates="transcription",
        cascade_delete=True,
        sa_relationship_kwargs={"order_by": col(Chat.created_datetime).desc()},
    )
    workflow_runs: list["WorkflowRun"] = Relationship(
        back_populates="transcription",
        cascade_delete=True,
    )


class WorkflowStatus(StrEnum):
    """Lifecycle status for a WorkflowRun.

    Tracks the overall progress of a single workflow execution against a
    transcription, from initial queuing through to worker processing,
    human confirmation, and final outcome.
    """

    AWAITING_START = auto()
    IN_PROGRESS = auto()
    AWAITING_CONFIRMATION = auto()
    COMPLETED = auto()
    FAILED = auto()


class WorkflowActionStatus(StrEnum):
    """Per-action status for a WorkflowAction within a WorkflowRun.

    Each action starts as PENDING after the LLM proposes it.  The user then
    approves or rejects it individually before the execute stage runs.
    """

    PENDING = auto()
    APPROVED = auto()
    REJECTED = auto()
    COMPLETED = auto()
    FAILED = auto()


class TemplateType(StrEnum):
    DOCUMENT = auto()
    FORM = auto()


class TemplateQuestion(BaseTableMixin, table=True):
    __tablename__ = "template_question"

    position: int
    title: str
    description: str

    user_template_id: UUID = Field(foreign_key="user_template.id", ondelete="CASCADE")
    user_template: "UserTemplate" = Relationship(back_populates="questions")


class UserTemplate(BaseTableMixin, table=True):
    __tablename__ = "user_template"
    created_datetime: datetime = Field(sa_column=created_datetime_column(), default=None)
    updated_datetime: datetime = Field(sa_column=updated_datetime_column(), default=None)

    name: str
    content: str
    description: str = ""

    type: TemplateType = TemplateType.DOCUMENT

    user_id: UUID | None = Field(default=None, foreign_key="user.id")

    minutes: list[Minute] = Relationship(back_populates="user_template")

    questions: list[TemplateQuestion] = Relationship(
        back_populates="user_template",
        passive_deletes="all",
        sa_relationship_kwargs={"order_by": TemplateQuestion.position},
    )


class WorkflowRun(BaseTableMixin, table=True):
    """Tracks a single execution of a workflow against a transcription.

    Progresses through WorkflowStatus from initial queuing, through LLM-driven
    action proposal, user confirmation, and final execution against the external service.
    """

    __tablename__ = "workflow_run"
    created_datetime: datetime = Field(sa_column=created_datetime_column(), default=None)
    updated_datetime: datetime = Field(sa_column=updated_datetime_column(), default=None)
    transcription_id: UUID = Field(
        foreign_key="transcription.id",
        ondelete="CASCADE",
        description="The transcription this workflow run is operating on.",
    )
    transcription: Mapped["Transcription"] = Relationship(back_populates="workflow_runs")
    user_id: UUID = Field(
        foreign_key="user.id",
        ondelete="CASCADE",
        description="The user who initiated this workflow run.",
    )
    workflow_name: str = Field(
        description="Term identifying which Workflow implementation to use, e.g. 'github_projects'.",
    )
    config: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB),
        description="Per-workflow configuration submitted by the user, e.g. GitHub project ID and repo.",
    )
    status: WorkflowStatus = Field(
        default=WorkflowStatus.AWAITING_START,
        sa_column_kwargs={"server_default": WorkflowStatus.AWAITING_START.name},
        description="Current lifecycle stage of this run.",
    )
    error: str | None = Field(
        default=None,
        description="Human-readable error message if the run reached FAILED status.",
    )
    actions: list["WorkflowAction"] = Relationship(
        back_populates="workflow_run",
        cascade_delete=True,
        sa_relationship_kwargs={"order_by": "WorkflowAction.position"},
    )


class WorkflowAction(BaseTableMixin, table=True):
    """A single proposed or executed action within a WorkflowRun.

    Created by the worker's prepare stage; presented to the user for approval or
    rejection before the execute stage carries them out against the external service.
    """

    __tablename__ = "workflow_action"
    created_datetime: datetime = Field(sa_column=created_datetime_column(), default=None)
    updated_datetime: datetime = Field(sa_column=updated_datetime_column(), default=None)
    workflow_run_id: UUID = Field(
        foreign_key="workflow_run.id",
        ondelete="CASCADE",
        description="The run this action belongs to.",
    )
    workflow_run: Mapped["WorkflowRun"] = Relationship(back_populates="actions")
    position: int = Field(
        description="Display order within the run (ascending).",
    )
    action_type: str = Field(
        description="Language understood by the workflow implementation, e.g. 'create_ticket'.",
    )
    description: str = Field(
        description="Human-readable summary of what this action will do, shown in the confirmation UI.",
    )
    payload: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB),
        description="All data needed to execute the action, e.g. ticket title, body, and labels.",
    )
    status: WorkflowActionStatus = Field(
        default=WorkflowActionStatus.PENDING,
        sa_column_kwargs={"server_default": WorkflowActionStatus.PENDING.name},
        description="Current state of this action, progressing from user decision through execution.",
    )
    result_url: str | None = Field(
        default=None,
        description="Link to the created or updated resource after successful execution, e.g. a GitHub issue URL.",
    )
    error: str | None = Field(
        default=None,
        description="Human-readable error message if this action reached FAILED status.",
    )


class WorkflowCredential(BaseTableMixin, table=True):
    """Stores a per-user OAuth token or PAT for an external service.

    The (user_id, service_name) pair is unique — one active credential per service per user.
    """

    __tablename__ = "workflow_credential"
    created_datetime: datetime = Field(sa_column=created_datetime_column(), default=None)
    updated_datetime: datetime = Field(sa_column=updated_datetime_column(), default=None)
    user_id: UUID = Field(
        foreign_key="user.id",
        ondelete="CASCADE",
        description="The user this credential belongs to.",
    )
    service_name: str = Field(
        description="External service this credential grants access to, e.g. 'github'.",
    )
    encrypted_token: str = Field(
        description="AES-256 encrypted OAuth token or PAT stored at rest.",
    )
    scopes: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB),
        description="OAuth scopes granted by this credential.",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="Expiry time of the token, or None if it does not expire.",
    )
