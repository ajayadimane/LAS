# LAS_Email for the demo server: the same base image as Core's backend (Python 3.10,
# libmagic1, tesseract). The code is mounted at run time (docker-compose.demo.yaml), so a
# code change needs only a restart; a change to requirements.lock needs a rebuild.
FROM python3.10-tar-ubuntu-20.04

RUN apt-get update && \
    apt-get install -y --no-install-recommends libmagic1 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /las-email

# authres, dkimpy and pyspf ship as source only; they are pure Python and build here.
COPY requirements.lock .
RUN pip3.10 install -r requirements.lock

ENV PYTHONPATH=/las-email
