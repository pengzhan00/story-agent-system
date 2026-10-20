from __future__ import annotations


class LLMError(RuntimeError):
    """Raised by the LLM layer. ``code`` is an i18n suffix (``llm.error.<code>``); ``params`` fill its placeholders."""

    def __init__(self, code: str, message: str = "", **params: object) -> None:
        super().__init__(message or code)
        self.code, self.message, self.params = code, message or code, params
