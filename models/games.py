from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional
from datetime import datetime, timezone

class QuizQuestionBase(BaseModel):
    """Base schema for a trivia or academic quiz question."""
    category: str = Field(..., description="e.g., 'Anime', 'Programming', 'General'")
    question: str = Field(..., description="The actual question text")
    options: List[str] = Field(..., min_length=2, max_length=4, description="List of possible answers")
    correct_option: int = Field(..., ge=0, le=3, description="Index of the correct answer in the options array")
    xp_reward: int = Field(default=10, description="XP gained for answering correctly")
    coins_reward: int = Field(default=5, description="Coins gained for answering correctly")

class QuizQuestionInDB(QuizQuestionBase):
    """Schema for a quiz question stored in MongoDB."""
    id: Optional[str] = Field(default=None, alias="_id")

    model_config = ConfigDict(
        populate_by_name=True
    )

class GameSessionBase(BaseModel):
    """Tracks an active or finished game for the leaderboard."""
    user_id: str = Field(..., description="Platform ID of the player")
    game_type: str = Field(..., description="e.g., 'tictactoe', 'quiz_battle', 'guess_number'")
    status: str = Field(default="active", description="'active', 'won', 'lost', 'draw'")
    xp_earned: int = Field(default=0)
    coins_earned: int = Field(default=0)

class GameSessionInDB(GameSessionBase):
    """Schema for a game session in MongoDB."""
    id: Optional[str] = Field(default=None, alias="_id")
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None

    model_config = ConfigDict(
        populate_by_name=True,
        json_encoders={datetime: lambda v: v.isoformat()}
    )