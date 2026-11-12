"""调用上限（用户约定，写死，不提供放宽开关）。

- TIKHUB_MAX_CALLS：TikHub 单次查询（一次 search/hashtag/account 运行）最多 100 次请求。
- GEMINI_MAX_CALLS：Gemini（TokensFactory）单个运行最多 100 次请求，含拆解、验证、评审。
超过上限直接停止，需要用户另行批准并改代码，不靠参数绕过。
"""
TIKHUB_MAX_CALLS = 100
GEMINI_MAX_CALLS = 100
