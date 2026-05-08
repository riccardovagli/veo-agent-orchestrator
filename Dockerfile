FROM python:3.11-slim

# Directory di lavoro nel container
WORKDIR /app

# Installiamo solo i requirements Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiamo la cartella 'app' locale nella cartella '/app' del container
COPY app ./app

# Avvio di uvicorn puntando al file dentro la cartella app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]