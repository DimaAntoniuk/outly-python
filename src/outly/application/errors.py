class AppError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class BadRequest(AppError):
    def __init__(self, message: str):
        super().__init__(400, message)


class Unauthorized(AppError):
    def __init__(self, message: str):
        super().__init__(401, message)


class Forbidden(AppError):
    def __init__(self, message: str):
        super().__init__(403, message)


class NotFound(AppError):
    def __init__(self, message: str):
        super().__init__(404, message)


class Conflict(AppError):
    def __init__(self, message: str):
        super().__init__(409, message)
