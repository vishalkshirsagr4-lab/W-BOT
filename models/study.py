from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime, timezone

class NoteBase(BaseModel):
    """Base schema for study materials, PDFs, and PYQs."""
    subject: str = Field(..., description="e.g., 'Data Structures', 'Thermodynamics'")
    topic: str = Field(..., description="What is this note about?")
    resource_url: str = Field(..., description="Link to the PDF or Drive folder")
    type: str = Field(default="note", description="'note', 'pdf', or 'pyq'")
    tags: List[str] = Field(default=[], description="Keywords for easy searching")

class NoteCreate(NoteBase):
    """Payload for uploading a new note."""
    uploader_id: str = Field(..., description="Platform ID of the user uploading it")

class NoteInDB(NoteBase):
    """Schema for a note as it is saved in MongoDB."""
    id: Optional[str] = Field(default=None, alias="_id")
    uploader_id: str
    upload_date: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    upvotes: int = Field(default=0, description="Community upvotes for good notes!")

    model_config = ConfigDict(
        populate_by_name=True,
        json_encoders={datetime: lambda v: v.isoformat()}
    )

class ReminderBase(BaseModel):
    """Base schema for student reminders."""
    title: str = Field(..., description="e.g., 'Submit Physics Assignment'")
    reminder_type: str = Field(default="assignment", description="'assignment', 'exam', 'personal'")
    due_date: datetime = Field(..., description="When is it due?")

class ReminderCreate(ReminderBase):
    """Payload for creating a reminder."""
    user_id: str = Field(..., description="Platform ID of the user setting the reminder")

class ReminderInDB(ReminderBase):
    """Schema for a reminder in MongoDB."""
    id: Optional[str] = Field(default=None, alias="_id")
    user_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_completed: bool = Field(default=False)

    model_config = ConfigDict(
        populate_by_name=True,
        json_encoders={datetime: lambda v: v.isoformat()}
    )