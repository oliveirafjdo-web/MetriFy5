# ============================
# Etapa 1 — Ambiente base
# ============================
FROM python:3.11-slim

# Define o diretório de trabalho
WORKDIR /app

# Evita problemas de buffer no log
ENV PYTHONUNBUFFERED=1

# Instala dependências do sistema
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copia arquivos da aplicação
COPY app.py .
COPY requirements.txt .
COPY Procfile .
COPY templates ./templates

# Instala dependências do Python
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# Cria a pasta de uploads (opcional)
RUN mkdir -p uploads

# Porta do Flask
EXPOSE 5000

# Comando de inicialização
CMD ["python", "app.py"]
