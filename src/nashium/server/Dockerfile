FROM python:3.11-slim

WORKDIR /app

COPY _subprocess_runner.py /app/

# Install packages that are also available in the Pyodide/micropip ecosystem.
RUN pip install --no-cache-dir \
    numpy \
    scipy \
    pandas \
    matplotlib \
    scikit-learn \
    networkx \
    sympy \
    nashpy

# Run as non-root
RUN useradd -m -u 1000 runner
USER runner

CMD ["python", "/app/_subprocess_runner.py"]