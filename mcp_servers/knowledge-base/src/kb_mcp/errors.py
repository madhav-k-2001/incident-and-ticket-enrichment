"""Domain errors for the knowledge base.

Messages are written for the caller (an LLM or operator), so they state what went
wrong without leaking internals such as DSNs, passwords or API keys.
"""


class KnowledgeBaseError(Exception):
    """Base class for all knowledge base failures."""


class NotFoundError(KnowledgeBaseError):
    pass


class InvalidRequestError(KnowledgeBaseError):
    pass


class UnavailableError(KnowledgeBaseError):
    """The database or the embedding service could not be reached."""


class EmbeddingError(KnowledgeBaseError):
    """The query could not be embedded."""
