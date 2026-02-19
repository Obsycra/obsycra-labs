"""Pydantic models for Quil's memory system."""

from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str
    timestamp: Optional[datetime] = None


class MemoryQuery(BaseModel):
    query: str
    top_k: Optional[int] = 5
    memory_types: Optional[List[str]] = None  # filter by type


class EpisodicMemory(BaseModel):
    id: str
    session_id: str
    timestamp: datetime
    user_message: str
    assistant_response: str
    summary: str
    tags: List[str]
    sentiment: str  # positive | neutral | negative | excited | stressed
    sentiment_score: float  # -1.0 to 1.0


class SemanticFact(BaseModel):
    id: str
    category: str   # e.g. "preference", "project", "value", "habit"
    label: str      # e.g. "Communication style"
    value: str      # e.g. "Prefers concise answers"
    confidence: float
    source_episode_ids: List[str]
    last_updated: datetime


class ProceduralRule(BaseModel):
    id: str
    rule: str           # The behavioral instruction
    reasoning: str      # Why Quil learned this
    evidence_count: int # How many interactions support it
    created_at: datetime


class ProspectiveItem(BaseModel):
    id: str
    content: str
    urgency: str    # "urgent" | "soon" | "later"
    due_hint: Optional[str]  # "Friday", "after meeting"
    done: bool
    created_at: datetime
    source_episode_id: str


class EmotionalEntry(BaseModel):
    date: str
    day_label: str
    score: float    # 0.0 - 1.0 energy/positivity
    dominant_emotion: str


class ContextualPattern(BaseModel):
    id: str
    trigger: str    # "before 8am", "when pasting code"
    pattern: str    # Human-readable insight
    icon: str       # emoji
    confidence: float
    observation_count: int


class MemoryNode(BaseModel):
    id: str
    label: str
    type: str       # project | preference | person | feeling | habit | event
    description: str
    weight: float   # size of node in graph


class MemoryEdge(BaseModel):
    source: str
    target: str
    strength: float  # 0.0 - 1.0


class PromptLayer(BaseModel):
    id: str         # "core" | "learned" | "user"
    title: str
    subtitle: str
    content: str
    badge: str      # "Core" | "Auto-evolved" | "Your words"
    badge_type: str # "core" | "auto" | "user"
    last_updated: Optional[datetime]


class PromptUpdateEntry(BaseModel):
    id: str
    title: str
    date: str
    explanation: str
    diff_remove: Optional[str]
    diff_add: str
