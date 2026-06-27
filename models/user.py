from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional
from datetime import datetime, timezone

class UserBase(BaseModel):
    """The base properties of a user."""
    platform_id: str = Field(..., description="Unique ID from Telegram/Discord/WhatsApp")
    username: Optional[str] = Field(None, description="User's handle")
    first_name: str = Field(..., description="User's first name")
    language: str = Field(default="en", description="Preferred language (en, hi, kn)")

class UserCreate(UserBase):
    """Payload for registering a new user."""
    pass

class UserInDB(UserBase):
    """The complete user schema as it is stored in MongoDB."""
    id: Optional[str] = Field(default=None, alias="_id")
    
    # Community & Progression
    join_date: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_admin: bool = Field(default=False)
    is_banned: bool = Field(default=False)

    
    # Economy & Gamification
    coins: int = Field(default=100, description="Welcome bonus coins")
    xp: int = Field(default=0)
    level: int = Field(default=1)
    badges: List[str] = Field(default=["Newbie ✨"])
    
    # Daily Features
    streak: int = Field(default=0)
    last_daily_claim: Optional[datetime] = None
    
    # Study & Customization
    college_name: Optional[str] = None
    branch: Optional[str] = None
    semester: Optional[int] = None

    model_config = ConfigDict(
        populate_by_name=True,
        json_encoders={datetime: lambda v: v.isoformat()}
    )