# Engineering guardrails

- Keep domain code independent of FastAPI, SQLAlchemy, HTTP, and Feishu.
- Persist business changes through migrations; never patch production schemas manually.
- Minimize candidate data. Never store BOSS passwords, cookies, complete HTML, chat history, resumes, or secrets in logs.
- The extension talks only to this API; it never connects directly to PostgreSQL or Feishu.
- Do not invent BOSS selectors. Real selectors require sanitized, on-site diagnostics.
- Name/job similarity creates evidence and warnings, never an automatic permanent merge.
- Preserve failing tests and fix root causes. Add tests for business-rule changes.
- Secrets belong in environment variables; `.env` is never committed.

