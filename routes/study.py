import logging
from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional
from datetime import datetime, timezone
from app.database.connection import get_db
from app.models.study import NoteCreate, NoteInDB, ReminderCreate, ReminderInDB

logger = logging.getLogger(__name__)

# Create the router
router = APIRouter(prefix="/study", tags=["Study & Productivity"])

# ==========================================
# 📚 STUDY MATERIALS (NOTES, PDFS, PYQS)
# ==========================================

@router.post("/notes", response_model=NoteInDB, status_code=status.HTTP_201_CREATED)
async def upload_note(note_data: NoteCreate, db = Depends(get_db)):
    """
    Upload a study resource. Sharing is caring! ✨
    """
    collection = db["notes"]
    
    # Create the database schema
    new_note = NoteInDB(**note_data.model_dump())
    note_dict = new_note.model_dump(by_alias=True, exclude={"id"})
    
    # Insert into MongoDB
    result = await collection.insert_one(note_dict)
    note_dict["_id"] = str(result.inserted_id)
    
    logger.info(f"New study material uploaded for {note_data.subject}! 📖")
    return note_dict

@router.get("/notes/{subject}", response_model=List[NoteInDB])
async def get_notes(subject: str, type: Optional[str] = None, db = Depends(get_db)):
    """
    Fetch study materials for a specific subject. Let's get that 10 CGPA! 🎓
    Optionally filter by type (note, pdf, pyq).
    """
    collection = db["notes"]
    
    query = {"subject": {"$regex": f"^{subject}$", "$options": "i"}} # Case-insensitive search
    if type:
        query["type"] = type
        
    cursor = collection.find(query).sort("upvotes", -1) # Highest upvotes first!
    notes = await cursor.to_list(length=50)
    
    if not notes:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="No notes found for this subject yet. Be the first to upload! 🥺"
        )
        
    for note in notes:
        note["_id"] = str(note["_id"])
        
    return notes

# ==========================================
# ⏰ REMINDER SYSTEM
# ==========================================

@router.post("/reminders", response_model=ReminderInDB, status_code=status.HTTP_201_CREATED)
async def create_reminder(reminder_data: ReminderCreate, db = Depends(get_db)):
    """
    Set a reminder for an assignment or exam. Never miss a deadline again! ⏳
    """
    collection = db["reminders"]
    
    # Ensure the due date is in the future
    if reminder_data.due_date <= datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You can't set a reminder for the past! Time travel isn't invented yet. 🕰️"
        )
    
    new_reminder = ReminderInDB(**reminder_data.model_dump())
    reminder_dict = new_reminder.model_dump(by_alias=True, exclude={"id"})
    
    result = await collection.insert_one(reminder_dict)
    reminder_dict["_id"] = str(result.inserted_id)
    
    logger.info(f"Reminder set for user {reminder_data.user_id}: {reminder_data.title} ⏰")
    return reminder_dict

@router.get("/reminders/{user_id}", response_model=List[ReminderInDB])
async def get_active_reminders(user_id: str, db = Depends(get_db)):
    """
    Get all active (uncompleted) reminders for a specific user.
    """
    collection = db["reminders"]
    
    query = {
        "user_id": user_id,
        "is_completed": False,
        "due_date": {"$gt": datetime.now(timezone.utc)}
    }
    
    cursor = collection.find(query).sort("due_date", 1) # Closest deadlines first
    reminders = await cursor.to_list(length=20)
    
    for reminder in reminders:
        reminder["_id"] = str(reminder["_id"])
        
    return reminders