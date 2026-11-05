FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

ENV PAWNSHOP_HOST=0.0.0.0 \
    PAWNSHOP_PORT=8700 \
    PAWNSHOP_EMBED_BACKEND=hash

EXPOSE 8700
CMD ["python", "-m", "app.main"]
