"""
设置、世界书、模型列表、TTS 路由
"""

import json, time, re

from fastapi import APIRouter
from fastapi.responses import Response, FileResponse
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional

import httpx

from config import (
    SETTINGS, MODELS, save_settings, get_key, get_sentinel_config,
    load_worldbook, save_worldbook, load_chat_status,
    TTS_CACHE_DIR, TTS_CACHE_MAX_BYTES, THEATER_TTS_CACHE_DIR,
    get_chat_providers, save_chat_providers, DEFAULT_CHAT_PROVIDERS,
)
from tts import cleanup_tts_cache_dir

router = APIRouter()

# ── 模型列表 ──────────────────────────────────────
@router.get("/api/models")
async def list_models():
    # 老的写死 MODELS 原样放前面（保持现有下拉不变）
    items = [{"key": k, "provider": v["provider"]} for k, v in MODELS.items()]
    # 之后【只追加】已启用供应商的动态模型（key=provider_id/model_id，必含 '/'，不会与老键冲突）
    try:
        from ai_providers import build_dynamic_models
        for key, dcfg in build_dynamic_models().items():
            items.append({"key": key, "provider": dcfg.get("provider_id", "")})
    except Exception:
        pass
    return items

# ── 聊天供应商持久化（单独端点，合并写入，不走 SettingsUpdate 字段白名单）──
class ChatProvidersUpdate(BaseModel):
    chat_providers: list = Field(default_factory=list)

@router.get("/api/chat_providers")
async def get_chat_providers_api():
    """读取聊天供应商列表。首次无配置时回退返回 6 个预置（让用户一进来就能逐个启用填 key）。"""
    providers = get_chat_providers()
    if not providers:
        return {"chat_providers": [dict(p) for p in DEFAULT_CHAT_PROVIDERS]}
    return {"chat_providers": providers}

@router.put("/api/chat_providers")
async def update_chat_providers_api(body: ChatProvidersUpdate):
    """合并写入：save_chat_providers 只更新 SETTINGS['chat_providers'] 后整体落盘，
    不影响人设/定位/各种 key 等其它设置。"""
    save_chat_providers(body.chat_providers)
    return {"ok": True}

# ── 设置 ──────────────────────────────────────────
class SettingsUpdate(BaseModel):
    gemini_key: Optional[str] = None
    siliconflow_key: Optional[str] = None
    gemini_free_key: Optional[str] = None
    aipro_key: Optional[str] = None
    netease_music_u: Optional[str] = None
    sentinel_base_url: Optional[str] = None
    sentinel_api_key: Optional[str] = None
    sentinel_model: Optional[str] = None
    embedding_base_url: Optional[str] = None
    embedding_api_key: Optional[str] = None
    embedding_model: Optional[str] = None
    luckin_mcp_enabled: Optional[bool] = None
    luckin_mcp_token: Optional[str] = None
    luckin_default_longitude: Optional[str] = None
    luckin_default_latitude: Optional[str] = None
    luckin_default_shop_keyword: Optional[str] = None

@router.get("/api/settings")
async def get_settings():
    def mask(k):
        if not k or len(k) < 8:
            return k
        return k[:4] + "*" * (len(k) - 8) + k[-4:]
    return {
        "gemini_key": SETTINGS.get("gemini_key", ""),
        "siliconflow_key": SETTINGS.get("siliconflow_key", ""),
        "gemini_free_key": SETTINGS.get("gemini_free_key", ""),
        "aipro_key": SETTINGS.get("aipro_key", ""),
        "netease_music_u": SETTINGS.get("netease_music_u", ""),
        "sentinel_base_url": SETTINGS.get("sentinel_base_url", ""),
        "sentinel_api_key": SETTINGS.get("sentinel_api_key", ""),
        "sentinel_model": SETTINGS.get("sentinel_model", ""),
        "embedding_base_url": SETTINGS.get("embedding_base_url", ""),
        "embedding_api_key": SETTINGS.get("embedding_api_key", ""),
        "embedding_model": SETTINGS.get("embedding_model", ""),
        "luckin_mcp_enabled": SETTINGS.get("luckin_mcp_enabled", False),
        "luckin_mcp_token": SETTINGS.get("luckin_mcp_token", ""),
        "luckin_default_longitude": SETTINGS.get("luckin_default_longitude", ""),
        "luckin_default_latitude": SETTINGS.get("luckin_default_latitude", ""),
        "luckin_default_shop_keyword": SETTINGS.get("luckin_default_shop_keyword", ""),
        "gemini_key_masked": mask(SETTINGS.get("gemini_key", "")),
        "siliconflow_key_masked": mask(SETTINGS.get("siliconflow_key", "")),
        "gemini_free_key_masked": mask(SETTINGS.get("gemini_free_key", "")),
        "aipro_key_masked": mask(SETTINGS.get("aipro_key", "")),
        "netease_music_u_masked": mask(SETTINGS.get("netease_music_u", "")),
        "sentinel_api_key_masked": mask(SETTINGS.get("sentinel_api_key", "")),
        "embedding_api_key_masked": mask(SETTINGS.get("embedding_api_key", "")),
    }

@router.put("/api/settings")
async def update_settings(body: SettingsUpdate):
    luckin_changed = False
    if body.gemini_key is not None:
        SETTINGS["gemini_key"] = body.gemini_key
    if body.siliconflow_key is not None:
        SETTINGS["siliconflow_key"] = body.siliconflow_key
    if body.gemini_free_key is not None:
        SETTINGS["gemini_free_key"] = body.gemini_free_key
    if body.aipro_key is not None:
        SETTINGS["aipro_key"] = body.aipro_key
    if body.sentinel_base_url is not None:
        SETTINGS["sentinel_base_url"] = body.sentinel_base_url
    if body.sentinel_api_key is not None:
        SETTINGS["sentinel_api_key"] = body.sentinel_api_key
    if body.sentinel_model is not None:
        SETTINGS["sentinel_model"] = body.sentinel_model
    if body.embedding_base_url is not None:
        SETTINGS["embedding_base_url"] = body.embedding_base_url
    if body.embedding_api_key is not None:
        SETTINGS["embedding_api_key"] = body.embedding_api_key
    if body.embedding_model is not None:
        SETTINGS["embedding_model"] = body.embedding_model
    if body.luckin_mcp_enabled is not None:
        luckin_changed = luckin_changed or SETTINGS.get("luckin_mcp_enabled") != body.luckin_mcp_enabled
        SETTINGS["luckin_mcp_enabled"] = body.luckin_mcp_enabled
    if body.luckin_mcp_token is not None:
        luckin_changed = luckin_changed or SETTINGS.get("luckin_mcp_token", "") != body.luckin_mcp_token
        SETTINGS["luckin_mcp_token"] = body.luckin_mcp_token
    if body.luckin_default_longitude is not None:
        SETTINGS["luckin_default_longitude"] = body.luckin_default_longitude
    if body.luckin_default_latitude is not None:
        SETTINGS["luckin_default_latitude"] = body.luckin_default_latitude
    if body.luckin_default_shop_keyword is not None:
        SETTINGS["luckin_default_shop_keyword"] = body.luckin_default_shop_keyword
    if body.netease_music_u is not None:
        old_mu = SETTINGS.get("netease_music_u", "")
        SETTINGS["netease_music_u"] = body.netease_music_u
        if body.netease_music_u != old_mu:
            # MUSIC_U 变更，重新登录 pyncm
            try:
                from music import reload_login
                reload_login()
            except Exception:
                pass
    save_settings(SETTINGS)
    if luckin_changed:
        try:
            from luckin import LUCKIN_SERVER_NAME
            from mcp_client import mcp_manager
            await mcp_manager.disconnect(LUCKIN_SERVER_NAME)
        except Exception:
            pass
    return {"ok": True}

# ── 温度设置 ──────────────────────────────────────
class TempUpdate(BaseModel):
    temperature: float

@router.put("/api/settings/temperature")
async def update_temperature(body: TempUpdate):
    SETTINGS["temperature"] = body.temperature
    save_settings(SETTINGS)
    return {"ok": True}

# ── 视频通话开关 ──────────────────────────────────
@router.get("/api/settings/video-call")
async def get_video_call_setting():
    return {"video_call_enabled": SETTINGS.get("video_call_enabled", True)}

class VideoCallToggle(BaseModel):
    enabled: bool

@router.put("/api/settings/video-call")
async def update_video_call_setting(body: VideoCallToggle):
    SETTINGS["video_call_enabled"] = body.enabled
    save_settings(SETTINGS)
    return {"ok": True, "video_call_enabled": body.enabled}

# ── AI 生图开关 ───────────────────────────────────
@router.get("/api/settings/image-gen")
async def get_image_gen_setting():
    return {"image_gen_enabled": SETTINGS.get("image_gen_enabled", False)}

class ImageGenToggle(BaseModel):
    enabled: bool

@router.put("/api/settings/image-gen")
async def update_image_gen_setting(body: ImageGenToggle):
    SETTINGS["image_gen_enabled"] = body.enabled
    save_settings(SETTINGS)
    return {"ok": True, "image_gen_enabled": body.enabled}

# ── CLI 工具调用开关（Gemini CLI / Antigravity CLI） ─────────────────
@router.get("/api/settings/gemini-cli-tools")
async def get_gemini_cli_tools_setting():
    return {"gemini_cli_tools_enabled": SETTINGS.get("gemini_cli_tools_enabled", False)}

class GeminiCliToolsToggle(BaseModel):
    enabled: bool

@router.put("/api/settings/gemini-cli-tools")
async def update_gemini_cli_tools_setting(body: GeminiCliToolsToggle):
    SETTINGS["gemini_cli_tools_enabled"] = body.enabled
    save_settings(SETTINGS)
    return {"ok": True, "gemini_cli_tools_enabled": body.enabled}

# ── 桌宠开关 ──────────────────────────────────────
@router.get("/api/settings/pet")
async def get_pet_setting():
    return {"pet_enabled": SETTINGS.get("pet_enabled", False)}

class PetToggle(BaseModel):
    enabled: bool

@router.put("/api/settings/pet")
async def update_pet_setting(body: PetToggle):
    SETTINGS["pet_enabled"] = body.enabled
    save_settings(SETTINGS)
    return {"ok": True, "pet_enabled": body.enabled}

# ── 健康数据分享开关 ──────────────────────────────
@router.get("/api/settings/health-share")
async def get_health_share_setting():
    return {"health_share_enabled": SETTINGS.get("health_share_enabled", False)}

class HealthShareToggle(BaseModel):
    enabled: bool

@router.put("/api/settings/health-share")
async def update_health_share_setting(body: HealthShareToggle):
    SETTINGS["health_share_enabled"] = body.enabled
    save_settings(SETTINGS)
    return {"ok": True, "health_share_enabled": body.enabled}

# ── 世界书 ────────────────────────────────────────
class WorldBookUpdate(BaseModel):
    ai_persona: str = ""
    user_persona: str = ""
    system_prompt: str = ""
    system_prompt_enabled: bool = True
    ai_name: str = "AI"
    user_name: str = "你"
    persona_schema_version: int = 1
    ai_persona_sections: Dict[str, str] = Field(default_factory=dict)
    user_persona_sections: Dict[str, str] = Field(default_factory=dict)
    creative_rules: str = ""
    persona_section_locks: Dict[str, Any] = Field(default_factory=dict)
    persona_evolution_enabled: bool = False

@router.get("/api/worldbook")
async def get_worldbook():
    return load_worldbook()

@router.put("/api/worldbook")
async def update_worldbook(body: WorldBookUpdate):
    current = load_worldbook()
    payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
    current.update(payload)
    save_worldbook(current)
    return {"ok": True}

# ── 聊天状态 ──────────────────────────────────────
@router.get("/api/chat_status")
async def get_chat_status_api():
    return load_chat_status()

# ── TTS 语音合成 ──────────────────────────────────
class TTSRequest(BaseModel):
    text: str
    voice: str = ""
    msg_id: Optional[str] = None

@router.post("/api/tts")
async def tts_synthesize(body: TTSRequest):
    key = get_key("siliconflow")
    if not key:
        return Response(content=json.dumps({"error": "未配置硅基流动 API Key"}), status_code=400, media_type="application/json")
    if not body.text.strip():
        return Response(content=json.dumps({"error": "文本不能为空"}), status_code=400, media_type="application/json")
    if not body.voice:
        return Response(content=json.dumps({"error": "未选择语音"}), status_code=400, media_type="application/json")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.siliconflow.cn/v1/audio/speech",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": "FunAudioLLM/CosyVoice2-0.5B",
                    "input": body.text.strip(),
                    "voice": body.voice,
                    "response_format": "mp3",
                    "speed": 1.0,
                    "gain": 0
                }
            )
        if resp.status_code != 200:
            return Response(content=json.dumps({"error": f"TTS API 错误: {resp.status_code}"}), status_code=502, media_type="application/json")
        audio_data = resp.content
        # 如果提供了 msg_id，将音频缓存到服务器
        if body.msg_id:
            import re
            safe_id = re.sub(r'[^a-zA-Z0-9_\-]', '', body.msg_id)
            if safe_id:
                cache_path = TTS_CACHE_DIR / f"{safe_id}.mp3"
                cache_path.write_bytes(audio_data)
                cleanup_tts_cache_dir(TTS_CACHE_DIR, TTS_CACHE_MAX_BYTES, skip={cache_path})
        return Response(content=audio_data, media_type="audio/mpeg")
    except Exception as e:
        return Response(content=json.dumps({"error": str(e)}), status_code=500, media_type="application/json")

@router.head("/api/tts/audio/{msg_id}")
@router.get("/api/tts/audio/{msg_id}")
async def tts_audio(msg_id: str):
    import re
    safe_id = re.sub(r'[^a-zA-Z0-9_\-]', '', msg_id)
    if not safe_id:
        return Response(status_code=404)
    cache_path = TTS_CACHE_DIR / f"{safe_id}.mp3"
    if not cache_path.exists():
        return Response(status_code=404)
    return FileResponse(cache_path, media_type="audio/mpeg", filename=f"{safe_id}.mp3")

@router.head("/api/theater/tts/audio/{msg_id}")
@router.get("/api/theater/tts/audio/{msg_id}")
async def theater_tts_audio(msg_id: str):
    import re
    safe_id = re.sub(r'[^a-zA-Z0-9_\-]', '', msg_id)
    if not safe_id:
        return Response(status_code=404)
    cache_path = THEATER_TTS_CACHE_DIR / f"{safe_id}.mp3"
    if not cache_path.exists():
        return Response(status_code=404)
    return FileResponse(cache_path, media_type="audio/mpeg", filename=f"{safe_id}.mp3")

@router.get("/api/tts/voices")
async def tts_voice_list():
    key = get_key("siliconflow")
    if not key:
        return {"voices": [], "error": "未配置硅基流动 API Key"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.siliconflow.cn/v1/audio/voice/list",
                headers={"Authorization": f"Bearer {key}"}
            )
        if resp.status_code != 200:
            return {"voices": [], "error": "获取音色列表失败"}
        data = resp.json()
        voices = data.get("result") or data.get("voices") or data.get("data") or []
        return {"voices": voices}
    except Exception as e:
        return {"voices": [], "error": str(e)}


# ── 聊天供应商：测试连接 / 获取模型 ────────────────
# 关键：两个端点都从【请求体】临时取 base_url/api_key/type，让用户在「保存之前」就能测试和获取，
# 不读取已存配置。返回体绝不回带完整 api_key；外呼设超时、捕获异常返回结构化错误、不抛 500。
_PROVIDER_HTTP_TIMEOUT = 30
# Anthropic 无标准 list 接口，给一组预置 Claude 名（用户可在前端手动增删；可能随官方更新而过时）
_ANTHROPIC_PRESET_MODELS = [
    "claude-opus-4-1", "claude-sonnet-4-5", "claude-3-7-sonnet-latest",
    "claude-3-5-sonnet-latest", "claude-3-5-haiku-latest",
]


def _validate_http_url(url: str) -> bool:
    """只允许 http/https，挡掉 file:// 等非法 scheme（SSRF 最低限度防护）。"""
    u = (url or "").strip().lower()
    return u.startswith("http://") or u.startswith("https://")


# 错误文本脱敏：把可能携带密钥的片段替换成 ***，防止 key 随错误/异常文本泄漏到前端。
# 覆盖：URL 查询串 key=<值>（google 把 key 放查询串）、Authorization: Bearer <token>、anthropic x-api-key。
_SECRET_PATTERNS = [
    (re.compile(r'(?i)([?&]key=)[^&\s"\']+'), r'\1***'),
    (re.compile(r'(?i)(Bearer\s+)[^\s"\']+'), r'\1***'),
    (re.compile(r'(?i)(x-api-key["\'\s:=]+)[^\s,"\']+'), r'\1***'),
]


def _sanitize_secrets(text) -> str:
    """对任意错误/异常字符串做密钥脱敏。"""
    out = str(text)
    for pat, repl in _SECRET_PATTERNS:
        out = pat.sub(repl, out)
    return out


def _truncate_err(text, limit: int = 300) -> str:
    """先脱敏再截断，避免泄露密钥 / 过长内网细节 / 巨大 HTML 错误页。"""
    return _sanitize_secrets(text).strip()[:limit]


class ProviderTestRequest(BaseModel):
    base_url: str = ""
    api_path: str = ""
    api_key: str = ""
    model: str = ""
    type: str = "openai"


class ProviderModelsRequest(BaseModel):
    base_url: str = ""
    api_key: str = ""
    type: str = "openai"


@router.post("/api/provider/test")
async def provider_test(body: ProviderTestRequest):
    """测试供应商连通性。HTTP 2xx 判成功并返回耗时(ms)。返回 {ok, latency_ms, error}。"""
    ptype = (body.type or "openai").strip().lower()
    base_url = (body.base_url or "").strip()
    api_key = (body.api_key or "").strip()
    model = (body.model or "").strip()

    # google 允许 base_url 为空（回退标准 Gemini 地址）；其余类型必须给合法 http(s) base_url
    if ptype != "google" and not _validate_http_url(base_url):
        return {"ok": False, "latency_ms": 0, "error": "base_url 必须以 http(s):// 开头"}
    # openai/anthropic 测试需要一个模型名（空 model 上游会报含糊错误）→ 给清晰提示
    if ptype in ("openai", "anthropic") and not model:
        return {"ok": False, "latency_ms": 0, "error": "请先选择/填写一个模型再测试"}

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=_PROVIDER_HTTP_TIMEOUT) as client:
            if ptype == "google":
                gbase = base_url or "https://generativelanguage.googleapis.com/v1beta"
                if not _validate_http_url(gbase):
                    return {"ok": False, "latency_ms": 0, "error": "base_url 必须以 http(s):// 开头"}
                gmodel = model or "gemini-3.5-flash"
                url = gbase.rstrip("/") + f"/models/{gmodel}:generateContent"
                resp = await client.post(
                    url, params={"key": api_key},
                    json={"contents": [{"role": "user", "parts": [{"text": "hello"}]}]},
                )
            elif ptype == "anthropic":
                url = base_url.rstrip("/") + (body.api_path or "/messages")
                resp = await client.post(
                    url,
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                             "content-type": "application/json"},
                    json={"model": model or "claude-3-5-haiku-latest", "max_tokens": 16,
                          "messages": [{"role": "user", "content": "hello"}]},
                )
            else:  # openai 兼容
                url = base_url.rstrip("/") + (body.api_path or "/chat/completions")
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": model, "messages": [{"role": "user", "content": "hello"}]},
                )
        latency = int((time.perf_counter() - t0) * 1000)
        if 200 <= resp.status_code < 300:
            return {"ok": True, "latency_ms": latency, "error": ""}
        return {"ok": False, "latency_ms": latency,
                "error": f"HTTP {resp.status_code}: {_truncate_err(resp.text)}"}
    except httpx.TimeoutException:
        return {"ok": False, "latency_ms": int((time.perf_counter() - t0) * 1000), "error": "请求超时"}
    except Exception as e:
        return {"ok": False, "latency_ms": int((time.perf_counter() - t0) * 1000),
                "error": _truncate_err(e)}


@router.post("/api/provider/models")
async def provider_models(body: ProviderModelsRequest):
    """拉取该供应商可用模型列表。返回 {ok, models:[{id}], error}。"""
    ptype = (body.type or "openai").strip().lower()
    base_url = (body.base_url or "").strip()
    api_key = (body.api_key or "").strip()

    # anthropic 无标准 list 接口 → 返回预置 Claude 名
    if ptype == "anthropic":
        return {"ok": True, "models": [{"id": m} for m in _ANTHROPIC_PRESET_MODELS], "error": ""}

    try:
        if ptype == "google":
            gbase = base_url or "https://generativelanguage.googleapis.com/v1beta"
            if not _validate_http_url(gbase):
                return {"ok": False, "models": [], "error": "base_url 必须以 http(s):// 开头"}
            async with httpx.AsyncClient(timeout=_PROVIDER_HTTP_TIMEOUT) as client:
                resp = await client.get(gbase.rstrip("/") + "/models", params={"key": api_key})
            if not (200 <= resp.status_code < 300):
                return {"ok": False, "models": [],
                        "error": f"HTTP {resp.status_code}: {_truncate_err(resp.text)}"}
            data = resp.json()
            models = []
            for it in (data.get("models") or []):
                name = it.get("name") or ""
                if name.startswith("models/"):
                    name = name[len("models/"):]
                if name:
                    models.append({"id": name})
            return {"ok": True, "models": models, "error": ""}

        # openai 兼容
        if not _validate_http_url(base_url):
            return {"ok": False, "models": [], "error": "base_url 必须以 http(s):// 开头"}
        async with httpx.AsyncClient(timeout=_PROVIDER_HTTP_TIMEOUT) as client:
            resp = await client.get(base_url.rstrip("/") + "/models",
                                    headers={"Authorization": f"Bearer {api_key}"})
        if not (200 <= resp.status_code < 300):
            return {"ok": False, "models": [],
                    "error": f"HTTP {resp.status_code}: {_truncate_err(resp.text)}"}
        data = resp.json()
        items = data.get("data") if isinstance(data, dict) else None
        models = []
        for it in (items or []):
            mid = it.get("id") if isinstance(it, dict) else None
            if mid:
                models.append({"id": mid})
        return {"ok": True, "models": models, "error": ""}
    except httpx.TimeoutException:
        return {"ok": False, "models": [], "error": "请求超时"}
    except Exception as e:
        return {"ok": False, "models": [], "error": _truncate_err(e)}
