FROM python:3.11-slim

# Create non-root user
RUN useradd -m -u 1000 runner

WORKDIR /app

# Copy the runner script (the contract between executor and container)
COPY _subprocess_runner.py /app/runner.py

# Switch to non-root user for security
USER runner

# Run the runner script
CMD ["python", "-u", "/app/runner.py"]