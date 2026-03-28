FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

# 更新包管理器并安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 安装 uvicorn 和 celery
RUN pip install --no-cache-dir "uvicorn[standard]" celery

# 复制应用代码
COPY . .

# 暴露端口
EXPOSE 8000

# 设置启动命令
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]