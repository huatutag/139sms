# 邮件发送服务 (139sms)

本项目是一个基于 Flask 的简单邮件发送 API 服务，设计用于通过多个163邮箱账户轮询向指定的目标邮箱（例如139邮箱）发送邮件。服务支持通过 API Key 进行接口认证。

## 特性

* **邮件发送API**: 提供 `/send` 接口用于发送邮件。
* **多账户轮询**: 支持配置多个163邮箱发件账户，并通过轮询方式使用这些账户发送邮件，以规避单一账户的频率限制。
* **API Key认证**: `/send` 接口需要通过 URL 参数传递 API Key 进行认证。
* **健康检查**: 提供 `/health` 接口用于检查服务状态和配置情况。
* **Docker化**: 易于通过 Docker 部署和管理。

## 快速开始

### 先决条件

* Docker 已安装。
* 至少一个163邮箱账户，并已为其开启SMTP服务并获取了**授权码** (非邮箱登录密码)。

### 配置文件和环境变量

本服务通过环境变量进行配置。以下是必须配置的环境变量：

* `API_SECRET_KEY`: 用于 `/send` 接口认证的密钥。客户端在请求时需通过 URL 参数 `key` 提供此密钥。
* `SENDER_ACCOUNTS_JSON`: 一个 JSON 字符串，定义了所有可用的163发件邮箱账户及其授权码。
    * 格式: `[{"email": "your_email1@163.com", "auth_code": "your_auth_code1"}, {"email": "your_email2@163.com", "auth_code": "your_auth_code2"}, ...]`
* `RECEIVER_EMAIL_ADDRESS`: 目标收件人的邮箱地址。

可选环境变量：

* `SMTP_PORT`: SMTP 服务器端口，默认为 `465` (163邮箱的SSL端口)。
* `LOG_LEVEL`: 应用日志级别，默认为 `INFO`。可选值：`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`。

### 构建 Docker 镜像

如果您需要从源码构建镜像（通常情况下，如果您有 `Dockerfile` 和代码，可以执行此步骤，否则可跳至运行预构建镜像）：

```bash
docker build -t 139sms .
```
*(假设您的镜像名称为 `139sms`)*

### 运行 Docker 容器

以下是一个示例 `docker run` 命令，请根据您的实际情况替换占位符和密钥：

```bash
docker run -d -p 5001:5000 \
    -e SENDER_ACCOUNTS_JSON='[{"email": "sender_account1@163.com", "auth_code": "AUTH_CODE_FOR_SENDER1"}, {"email": "sender_account2@163.com", "auth_code": "AUTH_CODE_FOR_SENDER2"}]' \
    -e RECEIVER_EMAIL_ADDRESS="target_receiver@example.com" \
    -e API_SECRET_KEY="YourStrongAndSecretApiKeyHere" \
    -e LOG_LEVEL="INFO" \
    --name my-139sms-app 139sms
```

**参数说明 (已脱敏和通用化处理)**：

* `-d`: 后台运行容器。
* `-p 5001:5000`: 将主机的 `5001` 端口映射到容器的 `5000` 端口。您可以根据需要更改主机端口。
* `-e SENDER_ACCOUNTS_JSON='[{"email": "sender_account1@163.com", "auth_code": "AUTH_CODE_FOR_SENDER1"}, ...]'`: **重要!** 替换为您的163邮箱账户和对应的授权码。确保JSON格式正确。
* `-e RECEIVER_EMAIL_ADDRESS="target_receiver@example.com"`: **重要!** 替换为您的目标收件人邮箱地址。
* `-e API_SECRET_KEY="YourStrongAndSecretApiKeyHere"`: **重要!** 设置一个强大且唯一的API密钥。
* `-e LOG_LEVEL="INFO"`: (可选) 设置日志级别。
* `--name my-139sms-app`: 为容器指定一个名称，方便管理。
* `139sms`: 您构建的 Docker 镜像名称。

**注意**: 在命令行中直接传递包含特殊字符的JSON字符串时，请确保正确转义，或者考虑使用其他方法（如 `.env` 文件配合 `docker run --env-file`）来管理环境变量，尤其是在复杂配置或生产环境中。

## API 端点

### 1. 发送邮件 (`/send`)

* **Method**: `POST`
* **URL**: `/send?key=<YOUR_API_KEY>`
* **认证**: 必须在URL参数中提供正确的 `key`。
* **Request Body** (JSON):
    ```json
    {
        "title": "邮件主题示例",
        "content": "这是邮件的具体内容。"
    }
    ```
    * `title` (string, 必须): 邮件的主题。
    * `content` (string, 可选): 邮件的正文内容。如果未提供或为空，将使用默认内容 "无内容"。

* **Success Response** (`200 OK`):
    ```json
    {
        "message": "邮件发送成功！"
    }
    ```
* **Error Responses**:
    * `400 Bad Request`: 请求体JSON无效，或缺少 `title` 参数。
        ```json
        {
            "error": "无效的JSON数据，请确保Content-Type为application/json"
        }
        ```
        或者
        ```json
        {
            "error": "请求体中必须包含 'title' (邮件主题) 参数"
        }
        ```
    * `401 Unauthorized`: API Key 缺失。
        ```json
        {
            "error": "拒绝访问：缺少API密钥"
        }
        ```
    * `403 Forbidden`: API Key 无效。
        ```json
        {
            "error": "拒绝访问：无效的API密钥"
        }
        ```
    * `500 Internal Server Error`: SMTP错误（如认证失败、数据错误）、SSL错误或未预料的服务器内部错误。
        ```json
        {
            "error": "具体的错误描述，例如：SMTP认证失败，请检查账户 sender_account1@163.com 的授权码"
        }
        ```
    * `503 Service Unavailable`: 无法连接到SMTP服务器。
        ```json
        {
            "error": "无法连接到SMTP服务器或连接设置错误"
        }
        ```

* **示例调用 (使用 curl)**:
    ```bash
    curl -X POST \
      -H "Content-Type: application/json" \
      -d '{"title":"来自API的问候", "content":"这是一封通过API服务发送的测试邮件。"}' \
      "http://localhost:5001/send?key=YourStrongAndSecretApiKeyHere"
    ```
    *(假设服务运行在 `localhost:5001` 且您的API Key是 `YourStrongAndSecretApiKeyHere`)*

### 2. 健康检查 (`/health`)

* **Method**: `GET`
* **URL**: `/health`
    *(注意: 此版本中 `/health` 接口默认不需要API Key认证，如有需要可自行修改代码添加)*
* **Response** (`200 OK`):
    ```json
    {
        "status": "healthy",
        "smtp_server": "smtp.163.com",
        "api_key_configured": true,
        "num_sender_accounts_configured": 2, // 示例值，表示配置了2个发件账户
        "receiver_email_configured": true
    }
    ```

## 文件结构

```
.
├── 139sms.py        # Flask应用主文件
├── Dockerfile       # Docker构建文件
├── requirements.txt # Python依赖
└── README.md        # 本文件
```

## 技术栈

* Python 3.9+
* Flask
* Gunicorn (用于生产环境部署)
* Docker

## 注意事项

* **授权码安全**: 163邮箱的授权码非常重要，请妥善保管，不要直接硬编码到代码中，而是通过环境变量传递。
* **API Key安全**: `API_SECRET_KEY` 也应视为敏感信息，妥善保管。
* **频率限制**: 即使使用多账户轮询，接收方邮件服务（如139邮箱）仍可能对来自同一IP地址的总邮件量或特定模式的邮件进行限制。请合理安排发送频率。
* **日志**: 应用会输出日志到标准输出，可以通过 Docker 日志命令查看 (`docker logs <container_name_or_id>`)。日志级别可通过 `LOG_LEVEL` 环境变量配置。

```

这个README文件包含了您要求的所有关键信息，并且对敏感数据进行了脱敏处理和通用化。您可以直接将此内容保存为项目根目录下的 `README.md` 文件。