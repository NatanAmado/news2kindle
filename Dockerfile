FROM python:3.11-bookworm

COPY requirements.txt requirements.txt

RUN apt-get update \
    && apt-get install -y --no-install-recommends pandoc \
    && pip3 install -r requirements.txt \
    && rm -rf /var/lib/apt/lists/*

COPY src/ src/
COPY config/ config/

CMD ["python3", "src/news2kindle.py"]
