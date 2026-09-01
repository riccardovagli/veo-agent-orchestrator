FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN FRAMEBUTCHER_BACKEND_URL=http://localhost \
    BACKEND_INTERNAL_TOKEN=dummy \
    python -c "import app.main; print('IMPORT OK')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]