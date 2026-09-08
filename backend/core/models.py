from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)


class AssistantState(StrEnum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    THINKING = "THINKING"
    STREAMING = "STREAMING"
    EXECUTING = "EXECUTING"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    SPEAKING = "SPEAKING"
    PERSISTENT_ACTIVE = "PERSISTENT_ACTIVE"
    ERROR = "ERROR"


class ChatRequest(BaseModel):
    request_id: UUID
    message: str = Field(min_length=1, max_length=20_000)
    conversation_id: str = "default"


class ActionTrace(BaseModel):
    tool: str
    status: Literal[
        "pending",
        "running",
        "started",
        "needs_confirmation",
        "awaiting_confirmation",
        "confirmation_required",
        "needs_input",
        "completed",
        "failed",
        "cancelled",
        "timed_out",
    ]
    detail: str = ""
    action_id: str | None = None
    label: str | None = None


class ConfirmationPreviewDetail(BaseModel):
    label: str
    value: str


class ConfirmationPreview(BaseModel):
    title: str
    summary: str
    details: list[ConfirmationPreviewDetail] = Field(default_factory=list)


class ChatResponse(BaseModel):
    request_id: str
    message: str
    display_text: str = ""
    speech_text: str = ""
    state: AssistantState = AssistantState.IDLE
    actions: list[ActionTrace] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    confirmation_id: str | None = None
    confirmation_preview: ConfirmationPreview | None = None
    route: str = ""
    agent: str = ""
    model: str = ""
    engine: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def synchronize_display_fields(self) -> "ChatResponse":
        if not self.display_text:
            self.display_text = self.message
        if not self.message:
            self.message = self.display_text
        return self


class ConfirmationRequest(BaseModel):
    confirmation_id: str
    approved: bool


class _SettingsSection(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OllamaSettingsPatch(_SettingsSection):
    url: StrictStr | None = Field(default=None, min_length=1, max_length=500)
    chat_model: StrictStr | None = Field(default=None, max_length=200)
    vision_model: StrictStr | None = Field(default=None, max_length=200)
    embedding_model: StrictStr | None = Field(default=None, max_length=200)
    context_size: StrictInt | None = Field(default=None, ge=512, le=131_072)
    max_output_tokens: StrictInt | None = Field(default=None, ge=1, le=32_768)
    request_timeout_seconds: StrictInt | StrictFloat | None = Field(default=None, ge=1, le=600)
    temperature: StrictInt | StrictFloat | None = Field(default=None, ge=0, le=2)
    think: StrictBool | None = None
    keep_alive: StrictStr | None = Field(default=None, max_length=80)


class VoiceSettingsPatch(_SettingsSection):
    enabled: StrictBool | None = None
    wake_word_enabled: StrictBool | None = None
    always_listening: StrictBool | None = None
    push_to_talk: StrictBool | None = None
    wake_word: StrictStr | None = Field(default=None, min_length=1, max_length=100)
    sensitivity: StrictInt | StrictFloat | None = Field(default=None, ge=0.05, le=0.9)
    wake_vad_threshold: StrictInt | None = Field(default=None, ge=50, le=20_000)
    microphone: StrictStr | None = Field(default=None, max_length=500)
    stt_executable: StrictStr | None = Field(default=None, max_length=1_000)
    stt_model: StrictStr | None = Field(default=None, max_length=1_000)
    stt_language: StrictStr | None = Field(default=None, min_length=1, max_length=20)
    stt_prompt: StrictStr | None = Field(default=None, max_length=5_000)
    piper_executable: StrictStr | None = Field(default=None, max_length=1_000)
    piper_model: StrictStr | None = Field(default=None, max_length=1_000)
    speech_rate: StrictInt | StrictFloat | None = Field(default=None, ge=0.5, le=2)
    volume: StrictInt | StrictFloat | None = Field(default=None, ge=0, le=1)
    hands_free: StrictBool | None = None
    special_wake_phrase: StrictStr | None = Field(default=None, max_length=300)
    special_wake_music: StrictStr | None = Field(default=None, max_length=1_000)
    special_wake_greeting: StrictStr | None = Field(default=None, max_length=1_000)
    special_wake_music_volume: StrictInt | None = Field(default=None, ge=0, le=100)
    stop_phrases: list[StrictStr] | None = Field(default=None, min_length=1, max_length=30)


class SearchSettingsPatch(_SettingsSection):
    searxng_url: StrictStr | None = Field(default=None, max_length=500)
    timeout_seconds: StrictInt | StrictFloat | None = Field(default=None, ge=1, le=120)


class AgentSettingsPatch(_SettingsSection):
    max_steps: StrictInt | None = Field(default=None, ge=1, le=32)
    history_messages: StrictInt | None = Field(default=None, ge=1, le=100)
    tool_timeout_seconds: StrictInt | StrictFloat | None = Field(default=None, ge=0.1, le=600)


class InterfaceSettingsPatch(_SettingsSection):
    always_on_top: StrictBool | None = None
    start_with_windows: StrictBool | None = None
    minimize_to_tray: StrictBool | None = None
    animations: StrictBool | None = None


class PrivacySettingsPatch(_SettingsSection):
    memory_enabled: StrictBool | None = None
    history_enabled: StrictBool | None = None
    structured_logs: StrictBool | None = None
    keep_screenshots: StrictBool | None = None


class ProactiveSettingsPatch(_SettingsSection):
    enabled: StrictBool | None = None
    interval_seconds: StrictInt | None = Field(default=None, ge=5, le=86_400)
    ram_warning_percent: StrictInt | StrictFloat | None = Field(default=None, ge=1, le=100)
    disk_warning_percent: StrictInt | StrictFloat | None = Field(default=None, ge=1, le=100)


class UserSettingsPatch(_SettingsSection):
    language: Literal["pt-BR", "en-US"] | None = None
    form_of_address: StrictStr | None = Field(default=None, min_length=1, max_length=80)
    default_location: StrictStr | None = Field(default=None, max_length=300)


class StorageSettingsPatch(_SettingsSection):
    artifact_directory: StrictStr | None = Field(default=None, max_length=1_000)


class SettingsPatch(_SettingsSection):
    ollama: OllamaSettingsPatch | None = None
    voice: VoiceSettingsPatch | None = None
    search: SearchSettingsPatch | None = None
    agent: AgentSettingsPatch | None = None
    interface: InterfaceSettingsPatch | None = None
    privacy: PrivacySettingsPatch | None = None
    proactive: ProactiveSettingsPatch | None = None
    permissions: dict[StrictStr, Literal["SAFE", "CONFIRM", "RESTRICTED"]] | None = None
    user: UserSettingsPatch | None = None
    storage: StorageSettingsPatch | None = None


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settings: SettingsPatch

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, value: Any) -> Any:
        def find_null(item: Any, path: str) -> str | None:
            if item is None:
                return path
            if isinstance(item, dict):
                for key, nested in item.items():
                    found = find_null(nested, f"{path}.{key}")
                    if found:
                        return found
            if isinstance(item, list):
                for index, nested in enumerate(item):
                    found = find_null(nested, f"{path}[{index}]")
                    if found:
                        return found
            return None

        found = find_null(value.get("settings", {}), "settings") if isinstance(value, dict) else None
        if found:
            raise ValueError(f"{found} não pode ser null.")
        return value


class MemoryCreate(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=10_000)
    category: str = Field(default="general", max_length=80)
    explicit: bool = True


class VoiceTranscriptionResponse(BaseModel):
    text: str
    engine: str
