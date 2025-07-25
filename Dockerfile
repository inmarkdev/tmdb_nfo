FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y gcc && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY app /app

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "main.py"]

