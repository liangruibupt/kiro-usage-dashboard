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

# 以非 root 用户运行（应用只读 /app，运行期仅写 /tmp 与 $HOME）
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app
# /ping 为 AgentCore Runtime 健康检查约定端点（BedrockAgentCoreApp 内置）
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/ping', timeout=3).status==200 else 1)"
CMD ["python", "app.py"]
