FROM python:3.11-slim

WORKDIR /app
COPY _subprocess_runner.py /app/

# Run as non-root
RUN useradd -m -u 1000 runner
USER runner

CMD ["python", "/app/_subprocess_runner.py"]