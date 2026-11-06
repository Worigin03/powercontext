FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the multilingual sentence-transformer into the image so the running
# service never needs to reach Hugging Face.
ENV PAWNSHOP_MODEL_CACHE=/models
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', cache_dir='/models')"

COPY app ./app

ENV PAWNSHOP_HOST=0.0.0.0 \
    PAWNSHOP_PORT=8700 \
    PAWNSHOP_EMBED_BACKEND=fastembed \
    HF_HUB_OFFLINE=1

EXPOSE 8700
CMD ["python", "-m", "app.main"]
