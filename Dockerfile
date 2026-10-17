# Use a lightweight Python base
FROM python:3.10-slim

# Install system dependencies required for audio processing
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libopus0 \
    && rm -rf /var/lib/apt/lists/*

# Setup the app directory
WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all your files (including main.py and music.mp3)
COPY . .

# Run the bot and flush output instantly
CMD ["python", "-u", "main.py"]
