from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional
from datetime import datetime, timezone

# ==========================================
# 🤫 ANONYMOUS CONFESSIONS
# ==========================================

class ConfessionBase(BaseModel):
    """Base schema for an anonymous confession."""
    content: str = Field(..., min_length=10, max_length=1000, description="The spicy tea ☕")

class ConfessionInDB(ConfessionBase):
    """Schema for a confession stored in MongoDB."""
    id: Optional[str] = Field(default=None, alias="_id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    upvotes: int = Field(default=0)
    is_approved: bool = Field(default=True, description="Admins can hide inappropriate ones")

    model_config = ConfigDict(
        populate_by_name=True,
        json_encoders={datetime: lambda v: v.isoformat()}
    )

# ==========================================
# 📊 POLLS SYSTEM
# ==========================================

class PollOption(BaseModel):
    """A single option in a poll."""
    text: str = Field(..., description="The choice text")
    votes: int = Field(default=0)

class PollCreate(BaseModel):
    """Payload to create a new poll."""
    question: str = Field(..., description="What are we asking the community?")
    options: List[str] = Field(..., min_length=2, max_length=5, description="List of choices")
    creator_id: str = Field(..., description="Platform ID of the user creating the poll")

class PollInDB(BaseModel):
    """Schema for a poll stored in MongoDB."""
    id: Optional[str] = Field(default=None, alias="_id")
    question: str
    options: List[PollOption]
    creator_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = Field(default=True)
    voted_users: List[str] = Field(default=[], description="List of user IDs who already voted")

    model_config = ConfigDict(
        populate_by_name=True,
        json_encoders={datetime: lambda v: v.isoformat()}
    )