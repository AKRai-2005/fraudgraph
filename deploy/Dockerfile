# The image a Hugging Face Space builds. It carries no data: the transaction
# cache is pulled at boot from a private repository (deploy/boot.py), because
# it is the challenge dataset in another form and is not ours to republish.
#
# Build args let you point it at a fork or a branch without editing the file:
#   docker build --build-arg REF=some-branch -f deploy/Dockerfile .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FG_BUILD_DIR=/home/user/app/build \
    FG_API_HOST=0.0.0.0 \
    FG_API_PORT=7860 \
    FG_GRAPH_BACKEND=auto

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Spaces run as uid 1000. Create and chown the app directory as root first:
# a WORKDIR that Docker creates can end up owned by root, and then the clone
# below fails with a permission error that reads like a network problem.
RUN useradd -m -u 1000 user && mkdir -p /home/user/app && chown -R user:user /home/user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH
WORKDIR /home/user/app

ARG REPO=https://github.com/AKRai-2005/fraudgraph.git
ARG REF=main
RUN git clone --depth 1 --branch ${REF} ${REPO} .

# The project's own requirements, deliberately: a trimmed list is how a
# deployment starts behaving differently from the machine it was tested on.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -e . huggingface_hub

EXPOSE 7860
CMD ["python", "deploy/boot.py"]
