# Implementation plan

1. Capture the current module/export surface and identify duplicated README
   sections before editing.
2. Extract only cohesive, behavior-neutral helpers from CLI/service code;
   retain compatibility imports and avoid changing parser or MCP contracts.
3. Rewrite the root README around the first-run path and link detailed topics
   to the operator guide, package notes, extension guide, and evaluation set.
4. Trim package README duplication and reconcile terminology across all docs.
5. Run focused tests after each extraction, then full pytest, Ruff, mypy,
   compileall, diff checks, and a documentation link/phrase review.
