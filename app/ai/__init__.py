"""AI service layer.

Dedicated, router-independent AI capabilities — intent detection, sentiment,
tool execution through the business services, knowledge retrieval, ticket
creation, and human handoff — powered by the Groq API (the only LLM provider).

Layering: the Chat module's ``ChatReplyProvider`` is implemented here, so the
chat module talks to the AI Service and never to Groq directly. This package
must stay import-free at ``__init__`` time (submodules are imported lazily) so
the chat module can import ``app.ai.types`` without a circular import.
"""
