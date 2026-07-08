FROM python:3.12-slim

WORKDIR /app

# 替换 Debian apt 源为 USTC 镜像（加速国内构建）
RUN set -ex; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's|http://deb.debian.org|http://mirrors.ustc.edu.cn|g' /etc/apt/sources.list.d/debian.sources; \
        sed -i 's|http://security.debian.org|http://mirrors.ustc.edu.cn/debian-security|g' /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i 's|http://deb.debian.org|http://mirrors.ustc.edu.cn|g' /etc/apt/sources.list; \
        sed -i 's|http://security.debian.org|http://mirrors.ustc.edu.cn/debian-security|g' /etc/apt/sources.list; \
    fi

# Pillow + 中文字体依赖（只装 wqy-zenhei，避免 noto-cjk 构建太慢）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo \
    fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Gunicorn 生产模式（单 worker，这个项目没有并发需求）
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--access-logfile", "-", "app:app"]
