FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Hong_Kong \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      nginx cron git ca-certificates tzdata rsync \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/share/zoneinfo/Asia/Hong_Kong /etc/localtime \
    && echo "Asia/Hong_Kong" > /etc/timezone \
    && rm -f /etc/nginx/sites-enabled/default

WORKDIR /app

# 先裝依賴，利用 Docker cache
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt

COPY . .

# 程式碼嘅 pristine 來源：每次 cron 由呢度同步落 volume（volume 只保存 runtime 資料）
COPY . /opt/aircon-src

# 部署 commit（release 腳本用 --build-arg 傳入；只作 metadata 追溯）
ARG AIRCON_COMMIT=unknown
RUN printf '%s\n' "$AIRCON_COMMIT" > /opt/aircon-src/.release-commit

# 初次生成網站內容（只在全新 volume 首次啟動時先會見到；日常由 cron 重新生成）
RUN python generate_html.py \
    && cp 空調對比報告.html index.html \
    && mkdir -p /app/web /var/log/aircon \
    && cp index.html metadata.json /app/web/
RUN cp 空調對比報告.pdf /app/web/ 2>/dev/null || true
RUN cp emsd_空調能源標籤.csv /app/web/ 2>/dev/null || true

COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY docker/run-update.sh /usr/local/bin/run-update.sh
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY docker/aircon-cron /etc/cron.d/aircon

RUN chmod +x /usr/local/bin/run-update.sh /usr/local/bin/entrypoint.sh \
    && chmod 0644 /etc/cron.d/aircon

EXPOSE 80
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
