FROM python:3.11-slim

# Install system dependencies (ffmpeg is crucial for both spotdl and yt-dlp, curl/unzip for deno)
RUN apt-get update && apt-get install -y \
    ffmpeg curl unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Deno (required by newer spotdl versions for youtube-music resolution)
RUN curl -fsSL https://deno.land/x/install/install.sh | sh
ENV DENO_INSTALL="/root/.deno"
ENV PATH="$DENO_INSTALL/bin:$PATH"

WORKDIR /app

# Install Python requirements
COPY backend/requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Install spotdl and yt-dlp directly via pip
RUN pip install --no-cache-dir spotdl yt-dlp

# Copy application files
COPY backend /app/backend
COPY frontend /app/frontend

WORKDIR /app/backend

# Run FastAPI using uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
