# Backend Development Guidelines

> Best practices for backend development in this project.

---

## Overview

This directory records the current backend conventions for the local-first
Python broker. Read the guides relevant to the modules you will change before
editing code.

---

## Guidelines Index

| Guide | Description | Status |
|-------|-------------|--------|
| [Directory Structure](./directory-structure.md) | Module organization and boundaries | Current |
| [Database Guidelines](./database-guidelines.md) | Encrypted SQLite and read-only source patterns | Current |
| [Error Handling](./error-handling.md) | Error types and boundary behavior | Current |
| [Quality Guidelines](./quality-guidelines.md) | Security contracts, testing, and review | Current |
| [Logging Guidelines](./logging-guidelines.md) | Audit, protocol, and sensitive-output rules | Current |

---

## Pre-Development Checklist

1. Read [Directory Structure](./directory-structure.md) for every backend task.
2. Read [Quality Guidelines](./quality-guidelines.md) for every behavior change.
3. Read [Database Guidelines](./database-guidelines.md) for storage, audit,
   Chrome, Messages, key, or connector changes.
4. Read [Error Handling](./error-handling.md) for service, adapter, CLI, or MCP
   boundary changes.
5. Read [Logging Guidelines](./logging-guidelines.md) before adding output,
   audit metadata, errors, diagnostics, or review UI fields.

---

**Language**: All documentation should be written in **English**.
