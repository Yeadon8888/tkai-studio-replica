"""调用上限。数值来自 .env（LIMIT_TIKHUB_CALLS、LIMIT_TEXT_CALLS），见 settings.py。保留本模块只为兼容旧引用。"""
import settings

TIKHUB_MAX_CALLS = settings.get_int("LIMIT_TIKHUB_CALLS")
GEMINI_MAX_CALLS = settings.get_int("LIMIT_TEXT_CALLS")
