FROM python3.10-tar-ubuntu-20.04

# Install system dependencies
# libpango/libharfbuzz/fontconfig/fonts-dejavu-core: WeasyPrint, which renders the
# DPR report PDF (its own fonts ship in app/templates/cam/fonts; DejaVu is the fallback).
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libmagic1 \
        tesseract-ocr \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        fontconfig \
        fonts-dejavu-core \
        curl \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Install Node.js 22 and npm
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get update && \
    apt-get install -y nodejs && \
    node --version && \
    npm --version

# Verify Python
RUN python --version && \
    python3.10 --version && \
    pip --version

WORKDIR /las

# requirements.txt pins every package with its sha256; pip checks them all.
COPY requirements.txt .

RUN pip3.10 install -r requirements.txt

EXPOSE 8109 8110
