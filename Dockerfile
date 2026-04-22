# --- 阶段 1: 构建阶段 ---
FROM node:20-slim AS builder

# 安装 node-pty 编译所需的系统依赖
RUN apt-get update && apt-get install -y \
    python3 \
    make \
    g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY package*.json ./

# 运行 npm install，此时会触发 node-pty 的编译
RUN npm install

COPY . .

# --- 阶段 2: 运行阶段 ---
FROM node:20-slim

# node-pty 在运行时依然需要基础的终端支持
RUN apt-get update && apt-get install -y \
    python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 从构建阶段只拷贝编译好的 node_modules 和代码
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app .

# 暴露端口
EXPOSE 3000

# 环境变量默认值
ENV NAME="Komari"

CMD [ "node", "index.js" ]
