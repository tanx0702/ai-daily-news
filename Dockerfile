FROM python:3.12-slim

WORKDIR /app

# Pillow + 中文字体依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo \
    fonts-noto-cjk \
    fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Gunicorn 生产模式（单 worker，这个项目没有并发需求）
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--access-logfile", "-", "app:app"]
