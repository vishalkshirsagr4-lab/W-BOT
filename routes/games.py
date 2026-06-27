import logging
import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import List
from app.database.connection import get_db
from app.models.games import QuizQuestionInDB

logger = logging.getLogger(__name__)

# Create the router
router = APIRouter(prefix="/entertainment", tags=["Games & Entertainment 🎮"])

# Request model for submitting a quiz answer
class QuizSubmit(BaseModel):
    user_id: str = Field(..., description="Platform ID of the player")
    question_id: str = Field(..., description="ID of the quiz question")
    selected_option: int = Field(..., description="Index of the chosen answer (0-3)")

# ==========================================
# 🎭 ENTERTAINMENT (MEMES & JOKES)
# ==========================================

@router.get("/meme")
async def get_random_meme():
    """
    Fetches a random safe-for-work meme! 😂
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("https://meme-api.com/gimme/wholesomememes")
            data = response.json()
            return {
                "title": data.get("title", "Here's a meme!"),
                "url": data.get("url"),
                "author": data.get("author")
            }
    except Exception as e:
        logger.error(f"Failed to fetch meme: {e}")
        raise HTTPException(status_code=503, detail="The meme generator is taking a nap! 😴 Try again.")

@router.get("/joke")
async def get_random_joke():
    """
    Fetches a random programming or college-friendly joke! 😆
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("https://v2.jokeapi.dev/joke/Programming,Miscellaneous,Pun?safe-mode&type=single")
            data = response.json()
            return {
                "joke": data.get("joke", "Why do programmers prefer dark mode? Because light attracts bugs! 🪲")
            }
    except Exception as e:
        logger.error(f"Failed to fetch joke: {e}")
        raise HTTPException(status_code=503, detail="I forgot the punchline! 😅 Try again.")

# ==========================================
# 🧠 GAMES (QUIZ BATTLE)
# ==========================================

@router.get("/quiz/random", response_model=List[QuizQuestionInDB])
async def get_random_quiz(limit: int = 1, db = Depends(get_db)):
    """
    Fetch random quiz questions using MongoDB's $sample aggregation.
    """
    collection = db["quizzes"]
    
    # Use aggregation to get random documents
    pipeline = [{"$sample": {"size": limit}}]
    cursor = collection.aggregate(pipeline)
    questions = await cursor.to_list(length=limit)
    
    if not questions:
        # If DB is empty, return a dummy question so the bot doesn't crash
        return [{
            "_id": "dummy123",
            "category": "Programming",
            "question": "What is the best programming language for AI?",
            "options": ["Java", "Python", "C++", "HTML"],
            "correct_option": 1,
            "xp_reward": 10,
            "coins_reward": 5
        }]
        
    for q in questions:
        q["_id"] = str(q["_id"])
        
    return questions

@router.post("/quiz/submit")
async def submit_quiz_answer(submission: QuizSubmit, db = Depends(get_db)):
    """
    Submit an answer, check if it's correct, and award XP/Coins! 🏆
    """
    from bson import ObjectId
    
    quiz_col = db["quizzes"]
    user_col = db["users"]
    
    # 1. Fetch the question to check the answer
    try:
        question = await quiz_col.find_one({"_id": ObjectId(submission.question_id)})
    except:
        question = None # Handles invalid dummy IDs or malformed ObjectIds

    # Fallback for the dummy question logic above
    if not question and submission.question_id == "dummy123":
        question = {"correct_option": 1, "xp_reward": 10, "coins_reward": 5}

    if not question:
        raise HTTPException(status_code=404, detail="Question not found in the database! 🤔")
        
    # 2. Check if the answer is correct
    is_correct = submission.selected_option == question["correct_option"]
    
    if is_correct:
        # 3. Award the user! 💰✨
        xp_gained = question.get("xp_reward", 10)
        coins_gained = question.get("coins_reward", 5)
        
        await user_col.update_one(
            {"platform_id": submission.user_id},
            {"$inc": {"xp": xp_gained, "coins": coins_gained}}
        )
        
        # Level up logic could go here!
        
        return {
            "status": "correct",
            "message": "Bingo! You nailed it! 🎉",
            "xp_gained": xp_gained,
            "coins_gained": coins_gained
        }
    else:
        return {
            "status": "incorrect",
            "message": "Oh no, that's wrong! 😭 Better luck next time!",
            "correct_option_index": question["correct_option"]
        }