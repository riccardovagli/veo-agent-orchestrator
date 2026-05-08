FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia tutto quello che c'è nella root del repo dentro /app nel container
COPY . .

# LANCIA IL FILE DIRETTAMENTE (Senza il prefisso app.)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]