FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scanner_bot ./scanner_bot
RUN useradd --system bot
USER bot
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "scanner_bot", "-c", "/app/config.yaml"]
