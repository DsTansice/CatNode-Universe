# --- 阶段 1: 构建阶段 ---
FROM node:20-alpine AS builder

# Alpine 用 apk，不需要 apt-get
RUN apk add --no-cache python3 make g++

WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .

# --- 阶段 2: 运行阶段 ---
FROM node:20-alpine

WORKDIR /app
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app .

EXPOSE 3000
ENV NAME="Komari"
CMD [ "node", "index.js" ]
