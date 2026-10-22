"""Stable client error categories; technical traceback stays in local logs."""
import logging
from fastapi import HTTPException

log = logging.getLogger('rasplot')


class AppError(HTTPException):
    def __init__(self, status, code, message):
        super().__init__(status, message)
        self.code = code


def error_code(exc, path=''):
    if hasattr(exc, 'code'):
        return exc.code
    status = exc.status_code
    if status == 403:
        return 'SECURITY_REJECTION'
    if status == 409:
        return 'STALE_STATE'
    if status == 422:
        return 'INVALID_INPUT'
    if status >= 500:
        return 'EXPORT_FAILURE' if path.startswith('/api/report/') else 'INTERNAL_ERROR'
    if status == 400 and path.startswith('/api/report/'):
        return 'ENGINEERING_PREFLIGHT'
    return 'INVALID_INPUT'
