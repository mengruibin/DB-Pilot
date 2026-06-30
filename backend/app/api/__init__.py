"""
DB-Pilot REST API 路由包。

仅包含薄路由层：参数校验 → 调用 engine/ 或 agent/ → 返回 Response。
依据 AGENTS.md §目录与命名规范：路由前缀统一为 /api/，不引入版本号。
"""
