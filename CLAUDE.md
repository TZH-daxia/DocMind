# AI Core Rules

## Layering
- Imports follow: API -> Service -> Repository -> Database
- Repository only handles DB access; API/Service/Workflow must not contain SQL
- Service: business logic only; API: thin layer (validate -> call Service -> return)
- Workflow may only call Service; Agent/LLM code must not touch Database

## Code
- DB access via SQLAlchemy 2.0 async ORM + AsyncSession; all IO (DB/HTTP/LLM) async
- All functions need type hints; use logger, never print; no `except: pass`
- Config from .env/config layer only; never hardcode keys, DB URLs, model names
- Prompts live in app/prompts/, never inline in Python code
- Never delete public interfaces, change API contracts or DB schema unless explicitly asked
- Service/Workflow changes need tests

## Frontend
- One component per file, no file over 300 lines
- No requests or business logic inside components; data comes from hooks/api layer
- State stays local unless shared; no premature global state

## Enforcement
- When editing Python backend code, follow the `ai-development-guardrails` skill (full rules + preflight checklist)
- When doing frontend work (components, pages, UI), follow the `frontend-architecture-guardrails` skill