FROM python:3.11-slim

# Install system dependencies
# libgl1 and libglib2.0-0 are often needed for cv2 (used by doctr)
# git is useful if we need to install from git
# poppler-utils might be needed for some pdf tools, but pymupdf is self-contained usually.
# tesseract-ocr is fallback
RUN apt-get update && apt-get install -y \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    tesseract-ocr \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

# Install python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Download sentence-transformer model during build to cache it?
# Or just let it download on first run. Better on first run to keep image small initially or cache it.
# We'll stick to runtime download for now to speed up build.

COPY . .

# Create uploads directory
RUN mkdir -p /app/uploads

# Default command (overridden in docker-compose)
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
