FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY agent_autofill ./agent_autofill

RUN pip install --no-cache-dir .

ENTRYPOINT ["agent-autofill-server"]
