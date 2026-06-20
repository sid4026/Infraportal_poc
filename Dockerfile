FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y libfreetype6-dev libjpeg-dev zlib1g-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY static/ ./static/

RUN mkdir -p data
EXPOSE 80

CMD ["python3", "server.py"]