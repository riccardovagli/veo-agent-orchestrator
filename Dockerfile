FROM python:3.11-slim

WORKDIR /app

# Copiamo i requirements
COPY requirements.txt .

# Installiamo mcp[fastapi] esplicitamente con le virgolette per evitare errori di shell
RUN pip install --no-cache-dir fastapi uvicorn google-cloud-firestore "mcp[fastapi]"

COPY . .

# Usiamo il formato lista che è più stabile su Cloud Run
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]