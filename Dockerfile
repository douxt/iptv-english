FROM python:3.12-slim

# 探活必须走用户自己的网络路径（NAS → 网关 → OpenClash → 源站），
# 所以本镜像的定位是「在你自己家里跑」，而不是云端。
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

WORKDIR /app
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# /data 挂宿主目录，里面放仓库工作副本
VOLUME ["/data"]

ENTRYPOINT ["/app/entrypoint.sh"]
