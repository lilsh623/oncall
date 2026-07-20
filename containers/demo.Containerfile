FROM python:3.12.8-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi==0.115.6 \
    httpx==0.28.1 \
    prometheus-client==0.21.1 \
    uvicorn[standard]==0.34.0

COPY demo/app/main.py ./main.py
COPY demo/traffic/main.py ./traffic.py

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
