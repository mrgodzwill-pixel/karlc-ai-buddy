FROM python:3.11-slim

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directories
RUN mkdir -p /app/data/reports

# Set environment variables
ENV DATA_DIR=/app/data
ENV REPORT_DIR=/app/data/reports
ENV PORT=5000

EXPOSE 5000

# Run the main application
CMD ["python", "main.py"]
