FROM python:3.12-slim
WORKDIR /app
COPY apps/api /app/apps/api
RUN pip install --no-cache-dir /app/apps/api
ENV PYTHONPATH=/app/apps/api/src
CMD ["uvicorn","recruitment_collab.main:app","--host","0.0.0.0","--port","8000"]

