FROM python:3.11-slim

# 1. Creiamo la directory di base /app alla radice del sistema
RUN mkdir -p /app

# 2. Ci entriamo dentro
WORKDIR /app

# 3. Copiamo i requisiti
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. CREIAMO la benedetta directory 'app' che il comando COPY fallisce a trovare
RUN mkdir ./app

# 5. COPIAMO il tuo main.py dentro questa nuova directory ./app
COPY main.py ./app/main.py

# 6. Adesso uvicorn troverà app.main:app perché il file è in /app/app/main.py
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]