import logging
from fastapi import APIRouter, Depends, HTTPException, status
from datetime import datetime, timezone, timedelta
from database.connection import get_db

from models.user import UserCreate, UserInDB

logger = logging.getLogger(__name__)

# Create the router
router = APIRouter(prefix="/users", tags=["Users & Economy"])

@router.post("/register", response_model=UserInDB, status_code=status.HTTP_200_OK)
async def register_user(user_data: UserCreate, db = Depends(get_db)):
    """
    Register a new user or login an existing one. 
    Welcome to the guild! ⚔️
    """
    collection = db["users"]
    
    # Check if our hero already exists
    existing_user = await collection.find_one({"platform_id": user_data.platform_id})
    if existing_user:
        logger.info(f"User {user_data.username} returned to the server. Welcome back! 🎉")
        existing_user["_id"] = str(existing_user["_id"])
        return existing_user
    
    # Create a new user with base stats
    new_user = UserInDB(**user_data.model_dump())
    user_dict = new_user.model_dump(by_alias=True, exclude={"id"})
    
    # Insert into MongoDB
    result = await collection.insert_one(user_dict)
    user_dict["_id"] = str(result.inserted_id)
    
    logger.info(f"New user registered: {user_data.username}. Given 100 welcome coins! 💰")
    return user_dict

@router.get("/{platform_id}", response_model=UserInDB)
async def get_profile(platform_id: str, db = Depends(get_db)):
    """
    Fetch a user's profile and stats. Let's see those power levels! ⚡
    """
    user = await db["users"].find_one({"platform_id": platform_id})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="User not found! Are you sure they go to this college? 🤔"
        )
    
    user["_id"] = str(user["_id"])
    return user

@router.post("/{platform_id}/daily")
async def claim_daily_reward(platform_id: str, db = Depends(get_db)):
    """
    Claim daily coins and update login streak. Consistency is key! 🗝️
    """
    collection = db["users"]
    user = await collection.find_one({"platform_id": platform_id})
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found!")

    now = datetime.now(timezone.utc)
    last_claim = user.get("last_daily_claim")
    
    # If last claim has timezone info, ensure it's UTC, else replace it
    if last_claim and last_claim.tzinfo is None:
        last_claim = last_claim.replace(tzinfo=timezone.utc)

    streak = user.get("streak", 0)
    
    # Check if they already claimed today (within last 24 hours)
    if last_claim and now - last_claim < timedelta(hours=24):
        time_left = timedelta(hours=24) - (now - last_claim)
        hours, remainder = divmod(time_left.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        return {
            "status": "cooldown",
            "message": f"Hold your horses! 🐎 Come back in {hours}h {minutes}m for your next reward."
        }

    # Calculate Streak
    if last_claim and now - last_claim > timedelta(hours=48):
        # Streak broken! 😭
        streak = 1
        streak_message = "Oh no! You lost your streak. 😭 Let's start over!"
    else:
        # Streak continues! 🔥
        streak += 1
        streak_message = f"You're on fire! 🔥 {streak} day streak!"

    # Calculate Reward (Base 50 + 10 for every streak day, max 200)
    reward_coins = min(50 + (streak * 10), 200)
    reward_xp = 20

    # Update Database
    await collection.update_one(
        {"platform_id": platform_id},
        {
            "$inc": {"coins": reward_coins, "xp": reward_xp},
            "$set": {"streak": streak, "last_daily_claim": now}
        }
    )

    return {
        "status": "success",
        "message": streak_message,
        "reward": {"coins": reward_coins, "xp": reward_xp},
        "total_coins": user.get("coins", 0) + reward_coins
    }