import logging
from typing import List

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from database.connection import get_db
from models.games import QuizQuestionInDB

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/entertainment", tags=["Games & Entertainment 🎮"])


class QuizSubmit(BaseModel):
    user_id: str = Field(..., description="Platform ID of the player")
    question_id: str = Field(..., description="ID of the quiz question")
    selected_option: int = Field(..., description="Index of the chosen answer (0-3)")


@router.get("/meme")
async def get_random_meme(request: Request):
    """Fetch a safe meme using the shared HTTP client."""
    client = getattr(request.app.state, "http_client", None)
    if client is None:
        raise HTTPException(status_code=503, detail="HTTP client unavailable")

    try:
        response = await client.get("https://meme-api.com/gimme/wholesomememes", timeout=3.0)
        response.raise_for_status()
        data = response.json()
        return {
            "title": data.get("title", "Here's a meme!"),
            "url": data.get("url"),
            "author": data.get("author"),
        }
    except httpx.HTTPError:
        logger.exception("Failed to fetch meme")
        raise HTTPException(status_code=503, detail="The meme generator is taking a nap! 😴 Try again.")


@router.get("/joke")
async def get_random_joke(request: Request):
    """Fetch a joke using the shared HTTP client."""
    client = getattr(request.app.state, "http_client", None)
    if client is None:
        raise HTTPException(status_code=503, detail="HTTP client unavailable")

    try:
        response = await client.get(
            "https://v2.jokeapi.dev/joke/Programming,Miscellaneous,Pun?safe-mode&type=single",
            timeout=3.0,
        )
        response.raise_for_status()
        data = response.json()
        return {
            "joke": data.get("joke", "Why do programmers prefer dark mode? Because light attracts bugs! 🪲")
        }
    except httpx.HTTPError:
        logger.exception("Failed to fetch joke")
        raise HTTPException(status_code=503, detail="I forgot the punchline! 😅 Try again.")


@router.get("/quiz/random", response_model=List[QuizQuestionInDB])
async def get_random_quiz(limit: int = 1, db=Depends(get_db)):
    """Fetch random quiz questions using MongoDB's $sample aggregation."""
    collection = db["quizzes"]

    pipeline = [{"$sample": {"size": limit}}]
    cursor = collection.aggregate(pipeline)
    questions = await cursor.to_list(length=limit)

    if not questions:
        return [{
            "_id": "dummy123",
            "category": "Programming",
            "question": "What is the best programming language for AI?",
            "options": ["Java", "Python", "C++", "HTML"],
            "correct_option": 1,
            "xp_reward": 10,
            "coins_reward": 5,
        }]

    for question in questions:
        question["_id"] = str(question["_id"])

    return questions


@router.post("/quiz/submit")
async def submit_quiz_answer(submission: QuizSubmit, db=Depends(get_db)):
    """Submit an answer, check correctness, and award XP/Coins."""
    from bson import ObjectId

    quiz_col = db["quizzes"]
    user_col = db["users"]

    try:
        question = await quiz_col.find_one({"_id": ObjectId(submission.question_id)})
    except Exception:
        question = None

    if not question and submission.question_id == "dummy123":
        question = {"correct_option": 1, "xp_reward": 10, "coins_reward": 5}

    if not question:
        raise HTTPException(status_code=404, detail="Question not found in the database! 🤔")

    is_correct = submission.selected_option == question["correct_option"]

    if is_correct:
        xp_gained = question.get("xp_reward", 10)
        coins_gained = question.get("coins_reward", 5)

        await user_col.update_one(
            {"platform_id": submission.user_id},
            {"$inc": {"xp": xp_gained, "coins": coins_gained}},
        )

        return {
            "status": "correct",
            "message": "Bingo! You nailed it! 🎉",
            "xp_gained": xp_gained,
            "coins_gained": coins_gained,
        }

    return {
        "status": "incorrect",
        "message": "Oh no, that's wrong! 😭 Better luck next time!",
        "correct_option_index": question["correct_option"],
    }