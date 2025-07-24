FROM python:3.12-slim

RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY main.py .

RUN pip install --no-cache-dir lxml guessit tmdbsimple requests

CMD ["python", "main.py"]
