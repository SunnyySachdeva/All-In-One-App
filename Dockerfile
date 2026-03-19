# Use Python 3.9 slim image as base
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=100 Flask==3.0.0

# Copy application code
COPY app.py .
COPY templates/ ./templates
COPY favorite_videos.txt .
COPY TODO.md .
COPY README.md .

# Create necessary directories
RUN mkdir -p cache/youtube

# Expose port
EXPOSE 5105

# Set environment variables
ENV FLASK_APP=app.py
ENV FLASK_RUN_HOST=0.0.0.0
ENV FLASK_RUN_PORT=5105

# Run the application
CMD ["python", "app.py"]
