# AgentCore Runtime 容器（linux/arm64，暴露 :8080 的 /invocations + /ping）。
# 构建上下文 = 仓库根（build.sh 里 -f backend/agent/Dockerfile .），以便复制 shared 查询层。
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

# 系统依赖（strands/pyarrow 无需；保持精简）
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

COPY backend/agent/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# 应用代码 + 共享查询层（平铺到 /app，匹配扁平 import）
COPY backend/agent/app.py backend/agent/agent.py backend/agent/tools.py /app/
COPY backend/shared/athena_client.py backend/shared/derivations.py /app/

# AgentCore Runtime 约定端口
EXPOSE 8080

CMD ["python", "app.py"]
