# Use Python 3.9 slim image as base
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .
COPY templates/ ./templates/
COPY cache/ ./cache/
COPY channels.db .
COPY task_console.db .
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