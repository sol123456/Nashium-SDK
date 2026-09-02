FROM python:3.11-slim

# HOME must be writable: the container filesystem is read-only except /tmp,
# and libraries (matplotlib, joblib, sklearn) write to ~/.cache on import.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    MPLCONFIGDIR=/tmp/.cache/matplotlib

WORKDIR /app

RUN pip install --no-cache-dir \
    numpy \
    scipy \
    pandas \
    matplotlib \
    scikit-learn \
    networkx \
    sympy \
    nashpy

COPY _docker_runner.py /app/_docker_runner.py

RUN useradd -m -u 1000 runner
USER 1000:1000

# Always overridden by the host; present so the image is never silently broken.
# Must be CMD, not ENTRYPOINT.
CMD ["python", "-B", "/app/_docker_runner.py", "/ipc/ipc.sock"]