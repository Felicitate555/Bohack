"""One source for model roles; every ID can be changed in .env."""
import os

ROLES = {
    "writer": ("SF_MODEL_WRITER", "deepseek-ai/DeepSeek-V4.1-Flash", "chat"),
    "director": ("SF_MODEL_DIRECTOR", "zai-org/GLM-5.3-Flash", "chat"),
    "director_pro": ("SF_MODEL_DIRECTOR_PRO", "zai-org/GLM-5.3", "chat"),
    "animation": ("SF_MODEL_ANIMATION", "moonshotai/Kimi-K2.7-Code", "chat"),
    "reviewer": ("SF_MODEL_REVIEWER", "zai-org/GLM-5.3-Flash", "vision"),
    "image": ("SF_MODEL_IMAGE", "OpenMOSS-Team/MOSS-Image", "image"),
    "image_fast": ("SF_MODEL_IMAGE_FAST", "Tongyi-MAI/Z-Image-Turbo", "image"),
    "video": ("SF_MODEL_VIDEO", "MiniMaxAI/MiniMax-H3", "video"),
    "speech": ("SF_MODEL_SPEECH", "FunAudioLLM/CosyVoice2-0.5B", "speech"),
    "speech_alt": ("SF_MODEL_SPEECH_ALT", "fnlp/MOSS-TTSD-v0.5", "speech"),
}
MODES = {"fast": "极速创作", "smart": "智能创作", "director": "导演级", "code": "AI 自由动画"}


def model(role):
    env, default, _ = ROLES[role]
    return os.getenv(env, "").strip() or default


def registry():
    return {r: {"id": model(r), "capability": capability, "provider": "siliconflow"} for r, (_, _, capability) in ROLES.items()}


def api_key():
    return os.getenv("SILICONFLOW_API_KEY", "").strip()


def base_url():
    return (os.getenv("SILICONFLOW_BASE_URL", "").strip() or "https://api.siliconflow.cn/v1").rstrip("/")
