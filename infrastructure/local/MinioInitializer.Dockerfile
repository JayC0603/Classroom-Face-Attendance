FROM python:3.13-slim

RUN pip install --no-cache-dir "minio>=7.2,<8"
COPY infrastructure/local/minio_init.py /usr/local/bin/minio_init.py
ENTRYPOINT ["python", "/usr/local/bin/minio_init.py"]
