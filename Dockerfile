# Paper-Study 单机自用镜像：Node(Web/API) + Python(采集/讲解/翻译/语义) 同容器。
# server.js 用 spawn 调 .venv/bin/python -m agent 跑各任务，故两套运行时都要在镜像里。
# 基础镜像默认用官方规范名；网络受限时可用 --build-arg NODE_IMAGE=<镜像源>/library/node:20-bookworm-slim 覆盖。
ARG NODE_IMAGE=node:20-bookworm-slim
FROM ${NODE_IMAGE}

# 系统依赖：python3+venv（跑 agent，且 better-sqlite3 原生编译需要 python）、构建工具、CA 证书（pip/外部 API 走 HTTPS）
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1) Node 依赖（better-sqlite3 原生模块；有 lockfile 用 npm ci，否则 npm install）
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev || npm install --omit=dev

# 2) Python 依赖装进项目内 .venv —— 与本地开发一致，server.js 的 pyExe() 会优先找 .venv/bin/python
COPY requirements.txt ./
RUN python3 -m venv .venv \
    && .venv/bin/pip install --no-cache-dir --upgrade pip \
    && .venv/bin/pip install --no-cache-dir -r requirements.txt

# 3) 应用代码（node_modules / .venv / data / .models / .env 由 .dockerignore 排除，不会覆盖上面装好的）
COPY . .

# 运行期产物目录兜底（未挂卷时也能启动；挂了卷则被卷覆盖）
RUN mkdir -p data/pdfs data/explainers data/translations .models/hf

ENV PORT=5173 \
    DB_PATH=/app/data/app.db \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    HF_HOME=/app/.models/hf
EXPOSE 5173
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD node -e "const port=process.env.PORT||5173; fetch('http://127.0.0.1:'+port).then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "server.js"]
