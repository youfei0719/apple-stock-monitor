"""统一错误格式：{"detail": "...", "code": "..."}。"""

from fastapi import HTTPException


class APIError(HTTPException):
    def __init__(self, status_code: int, detail: str, code: str = ""):
        super().__init__(status_code=status_code, detail=detail)
        self.code = code or "error"
