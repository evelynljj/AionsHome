"""
Android 手机屏幕截图上传接口。
"""

from pydantic import BaseModel
from fastapi import APIRouter

from phone_screen import save_phone_screen_b64, record_phone_screen_skip
from ws import manager


router = APIRouter()


class PhoneScreenUpload(BaseModel):
    image_base64: str
    timestamp: float | None = None
    app: str = ""
    locked: bool = False


class PhoneScreenSkip(BaseModel):
    reason: str
    app: str = ""
    locked: bool = False


@router.post("/api/phone-screen/upload")
async def upload_phone_screen(body: PhoneScreenUpload):
    meta = save_phone_screen_b64(
        body.image_base64,
        timestamp=body.timestamp,
        app=body.app,
        locked=body.locked,
    )
    await manager.broadcast({"type": "phone_screen_uploaded", "data": meta})
    return {"ok": True, "screen": meta}


@router.post("/api/phone-screen/skip")
async def skip_phone_screen(body: PhoneScreenSkip):
    meta = record_phone_screen_skip(body.reason, app=body.app, locked=body.locked)
    await manager.broadcast({"type": "phone_screen_skipped", "data": meta})
    return {"ok": True, "screen": meta}

