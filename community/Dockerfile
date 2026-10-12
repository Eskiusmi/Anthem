FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY server.py .
# 默认 SQLite 放在 /data:VPS 上用 docker run -v 挂载,Render 付费版挂持久磁盘到 /data。
# 免费主机没有持久磁盘时,设置 DATABASE_URL 改用 PostgreSQL。
ENV MB_DB=/data/community.db \
    MB_TRUST_PROXY=1
EXPOSE 8787
# Render 会通过 PORT 环境变量告诉程序监听哪个端口;自己部署时默认 8787
CMD ["sh", "-c", "exec uvicorn server:app --host 0.0.0.0 --port ${PORT:-8787}"]
