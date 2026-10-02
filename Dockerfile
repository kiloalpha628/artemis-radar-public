FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py render.py storm_motion.py palettes.py volume.py ./
COPY LICENSE THIRD_PARTY_NOTICES.md ./
COPY licenses/ ./licenses/
USER 1000:1000
CMD ["python", "app.py", "worker"]
