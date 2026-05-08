FROM python:3.11-slim

WORKDIR /app

# 1. Installiamo le dipendenze
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2. CREIAMO la directory 'app' che manca su GitHub
RUN mkdir app

# 3. SPOSTIAMO il tuo main.py (che è nella root del repo) dentro la cartella 'app' del container
COPY main.py ./app/main.py

# 4. Lancio IDENTICO al tuo progetto funzionante
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]