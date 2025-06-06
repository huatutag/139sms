# 使用官方 Python 运行时作为父镜像
FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 防止 Python 写入 .pyc 文件 (可选, 但良好实践)
ENV PYTHONDONTWRITEBYTECODE 1
# 确保 Python 输出不被缓冲，以便日志能及时显示
ENV PYTHONUNBUFFERED 1

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 将应用程序代码复制到容器中
COPY 139sms.py .

# 声明应用监听的端口 (文档性质，实际由Gunicorn绑定)
EXPOSE 5000

# 定义环境变量的默认值 (可选, 最好在运行时覆盖)
ENV SMTP_PORT="465"
# ENV LOG_LEVEL="INFO" # 可选：设置应用日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)

# 以下环境变量必须在 docker run 时通过 -e 传递:
# ENV API_SECRET_KEY="your_very_secret_api_key_here" # 新增：用于/send接口认证的密钥
# ENV SENDER_ACCOUNTS_JSON='[{"email": "user1@163.com", "auth_code": "authcode1"}, {"email": "user2@163.com", "auth_code": "authcode2"}]'
# ENV RECEIVER_EMAIL_ADDRESS="your_target_receiver_email@example.com" # 例如: "17360080651@139.com"

# 运行应用的命令 (使用 Gunicorn)
CMD ["gunicorn", "--workers", "3", "--bind", "0.0.0.0:5000", "--log-level", "info", "--access-logfile", "-", "--error-logfile", "-", "139sms:app"]