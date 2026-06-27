import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from app.ai.chat import generate_chat_response

logger = logging.getLogger(__name__)

# Create the router
router = APIRouter(prefix="/chat", tags=["AI Features"])

# Request Models
class ChatMessage(BaseModel):
    role: str = Field(..., description="'user' or 'model'")
    parts: List[str] = Field(..., description="The actual text message")

class ChatRequest(BaseModel):
    platform_id: str = Field(..., description="User's unique ID")
    message: str = Field(..., description="The message they are sending to the bot")
    history: Optional[List[ChatMessage]] = Field(default=[], description="Previous conversation context")

class StudyRequest(BaseModel):
    subject: str = Field(..., description="e.g., 'Data Structures', 'Thermodynamics'")
    question: str = Field(..., description="The academic question")

# Endpoints
@router.post("/ask")
async def chat_with_bot(request: ChatRequest):
    """
    Talk to the College Community Bot! It remembers the history passed to it. 🗣️
    """
    try:
        # Convert Pydantic history to the dict format Gemini expects
        formatted_history = [{"role": msg.role, "parts": msg.parts} for msg in request.history]
        
        # Generate the response
        reply = await generate_chat_response(request.message, formatted_history)
        
        return {
            "status": "success",
            "reply": reply
        }
    except Exception as e:
        logger.error(f"Chat Route Error: {e}")
        raise HTTPException(status_code=500, detail="My brain needs a quick reboot! 🔄 Try again.")

@router.post("/study-helper")
async def ai_study_helper(request: StudyRequest):
    """
    A specialized prompt to help students with their homework and coding! 📚
    """
    study_prompt = f"""
    The user needs help with {request.subject}. 
    Question: {request.question}
    
    Rule 1: Explain it simply, like a smart senior teaching a junior. 
    Rule 2: If it involves code, explain it step-by-step.
    Rule 3: Keep the encouraging anime persona! Say things like "You got this!" or "Easy peasy!"
    """
    
    try:
        # We don't need history for a one-off homework question
        reply = await generate_chat_response(study_prompt, chat_history=[])
        return {
            "status": "success",
            "reply": reply
        }
    except Exception as e:
        logger.error(f"Study Helper Error: {e}")
        raise HTTPException(status_code=500, detail="The library is closed right now! 😭 (API Error)")