import logging
from fastapi import APIRouter, Depends, HTTPException, status
from bson import ObjectId
from database.connection import get_db

logger = logging.getLogger(__name__)

# Create the router
router = APIRouter(prefix="/admin", tags=["Admin Dashboard 🛡️"])

# ==========================================
# 🛑 SECURITY DEPENDENCY
# ==========================================

async def verify_admin(admin_id: str, db = Depends(get_db)):
    """
    Middleware to check if the user is actually an admin.
    No sneaking into the teacher's lounge! 👨‍🏫
    """
    user = await db["users"].find_one({"platform_id": admin_id})
    if not user or not user.get("is_admin", False):
        logger.warning(f"Unauthorized admin access attempt by {admin_id}!")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ara ara~ You don't have permission to be here! 🛑"
        )
    return user

# ==========================================
# 📊 ANALYTICS
# ==========================================

@router.get("/analytics")
async def get_server_stats(admin: dict = Depends(verify_admin), db = Depends(get_db)):
    """
    Get the overall statistics of the College Bot. How big is our guild? 📈
    """
    try:
        user_count = await db["users"].count_documents({})
        notes_count = await db["notes"].count_documents({})
        confession_count = await db["confessions"].count_documents({})
        poll_count = await db["polls"].count_documents({})
        
        return {
            "status": "success",
            "requested_by": admin["username"],
            "stats": {
                "total_users": user_count,
                "total_notes": notes_count,
                "total_confessions": confession_count,
                "total_polls": poll_count
            }
        }
    except Exception as e:
        logger.error(f"Analytics Error: {e}")
        raise HTTPException(status_code=500, detail="Database error while fetching stats.")

# ==========================================
# 🔨 MODERATION TOOLS
# ==========================================

@router.put("/users/{target_id}/ban")
async def ban_user(target_id: str, action: str = "ban", admin: dict = Depends(verify_admin), db = Depends(get_db)):
    """
    Ban or unban a user. Bring down the ban hammer! 🔨
    Action can be 'ban' or 'unban'.
    """
    is_banned = True if action == "ban" else False
    
    result = await db["users"].update_one(
        {"platform_id": target_id},
        {"$set": {"is_banned": is_banned}}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found! Ghost hunting? 👻")
        
    status_msg = "banned 🔨" if is_banned else "unbanned ✨"
    return {"message": f"User {target_id} has been {status_msg}!"}

@router.delete("/confessions/{confession_id}")
async def delete_confession(confession_id: str, admin: dict = Depends(verify_admin), db = Depends(get_db)):
    """
    Delete a spicy confession that went a little too far. 🧯
    """
    try:
        obj_id = ObjectId(confession_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid ID format.")
        
    result = await db["confessions"].delete_one({"_id": obj_id})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Confession not found!")
        
    return {"message": "Confession successfully wiped from existence! 🧹"}

@router.post("/broadcast")
async def broadcast_message(message: str, admin: dict = Depends(verify_admin), db = Depends(get_db)):
    """
    Save an announcement that can be broadcasted to all users. 📢
    (In a real bot, you'd use a background task to push this to Telegram/Discord).
    """
    announcement = {
        "message": message,
        "author": admin["username"],
        "timestamp": datetime.now(timezone.utc)
    }
    await db["logs"].insert_one(announcement)
    
    return {"message": "Broadcast saved successfully! 📡 (Ready to be pushed)"}