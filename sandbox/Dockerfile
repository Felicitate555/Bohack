FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pillow==11.2.1 numpy==2.2.6
COPY worker.py /worker.py
USER 65534:65534
WORKDIR /tmp
ENTRYPOINT ["python", "-I", "/worker.py"]
