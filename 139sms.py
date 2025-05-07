import os
import smtplib
import ssl
import logging
import sys  # 用于在启动检查失败时打印到stderr和退出
from email.mime.text import MIMEText
from email.header import Header
from flask import Flask, request, jsonify

# --- 配置信息 ---
SMTP_SERVER = 'smtp.139.com'
SMTP_PORT = int(os.environ.get('SMTP_PORT', 465))  # SSL端口, 仍可配置，默认为465

# --- 环境变量强制检查 (应用启动前) ---
# EMAIL_ACCOUNT 是必须的环境变量
EMAIL_ACCOUNT = os.environ.get('EMAIL_ACCOUNT')
if not EMAIL_ACCOUNT:
    # 如果关键环境变量缺失，打印到stderr并抛出异常阻止Gunicorn等服务器启动
    # 或者在直接运行时通过sys.exit()退出
    critical_message = "CRITICAL: 环境变量 'EMAIL_ACCOUNT' 未设置。应用程序无法启动。"
    print(critical_message, file=sys.stderr)
    raise RuntimeError(critical_message)

# EMAIL_PASSWORD (授权码) 也是必须的环境变量
EMAIL_PASSWORD = os.environ.get('EMAIL_PASSWORD')
if not EMAIL_PASSWORD:
    critical_message = "CRITICAL: 环境变量 'EMAIL_PASSWORD' 未设置。应用程序无法启动。"
    print(critical_message, file=sys.stderr)
    raise RuntimeError(critical_message)

SENDER_EMAIL = EMAIL_ACCOUNT  # 发件人邮箱
RECEIVER_EMAIL = EMAIL_ACCOUNT  # 收件人邮箱 (发送给自己)

# --- Flask 应用初始化 ---
app = Flask(__name__)

# --- 日志配置 ---
# 当通过 Gunicorn 运行时，让 Gunicorn 处理日志输出
if __name__ != '__main__':
    gunicorn_logger = logging.getLogger('gunicorn.error')
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)
else:
    # 如果直接运行 (例如本地测试)
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]')
    app.logger.info("直接运行脚本，使用基础日志配置。")

app.logger.info(f"SMTP 服务器配置为: {SMTP_SERVER}:{SMTP_PORT}")
app.logger.info(f"发件邮箱账户: {EMAIL_ACCOUNT}")


@app.route('/send', methods=['POST'])
def send_email_api():
    # EMAIL_ACCOUNT 和 EMAIL_PASSWORD 在启动时已验证存在
    try:
        data = request.get_json()
        if not data:
            app.logger.warning("API /send: 收到无效的JSON数据或Content-Type不正确")
            return jsonify({"error": "无效的JSON数据，请确保Content-Type为application/json"}), 400

        subject = data.get('邮件主题')
        content = data.get('邮件内容')  # 如果未提供，则为 None

        if not subject:
            app.logger.warning("API /send: 请求缺少'邮件主题'")
            return jsonify({"error": "必须提供'邮件主题'"}), 400

        if content is None or str(content).strip() == "":
            content = "无内容"  # 默认内容
            app.logger.info("API /send: 邮件内容未提供，使用默认值 '无内容'")

        # --- 构造邮件 ---
        msg = MIMEText(content, 'plain', 'utf-8')
        msg['From'] = Header(f"通知服务 <{SENDER_EMAIL}>", 'utf-8')
        msg['To'] = Header(RECEIVER_EMAIL, 'utf-8')
        msg['Subject'] = Header(subject, 'utf-8')

        # --- 发送邮件 ---
        context = ssl.create_default_context()  # 使用默认的SSL上下文以增强安全性

        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context) as server:
            # server.set_debuglevel(1) # 需要详细SMTP调试信息时取消注释
            server.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
            server.sendmail(SENDER_EMAIL, [RECEIVER_EMAIL], msg.as_string())

        app.logger.info(f"API /send: 邮件发送成功，主题: '{subject}'")
        return jsonify({"message": "邮件发送成功！"}), 200

    except smtplib.SMTPAuthenticationError as e:
        app.logger.error(
            f"API /send: SMTP认证失败: {e.smtp_code} - {e.smtp_error.decode('utf-8', 'ignore') if e.smtp_error else 'Unknown auth error'}")
        return jsonify({"error": "SMTP认证失败，请检查邮箱账号或授权码"}), 500
    except smtplib.SMTPConnectError as e:
        app.logger.error(f"API /send: 无法连接到SMTP服务器({SMTP_SERVER}:{SMTP_PORT}): {e}")
        return jsonify({"error": "无法连接到SMTP服务器"}), 503  # Service Unavailable
    except smtplib.SMTPServerDisconnected as e:
        app.logger.error(f"API /send: SMTP服务器意外断开连接: {e}")
        return jsonify({"error": "SMTP服务器意外断开连接"}), 503
    except ssl.SSLError as e:  # 更具体地捕获SSL错误
        app.logger.error(f"API /send: SSL错误: {e}")
        return jsonify({"error": f"与邮件服务器建立安全连接时发生SSL错误: {str(e)}"}), 500
    except Exception as e:
        app.logger.error(f"API /send: 发送邮件时发生未知错误: {e}", exc_info=True)  # exc_info=True 会记录堆栈跟踪
        return jsonify({"error": "发送邮件时发生未知错误"}), 500


# 健康检查端点
@app.route('/health', methods=['GET'])
def health_check():
    # 由于EMAIL_ACCOUNT和EMAIL_PASSWORD在启动时检查，如果应用运行到这里，它们必然已设置。
    return jsonify({"status": "healthy", "smtp_server": SMTP_SERVER, "email_account_configured": True}), 200


if __name__ == '__main__':
    # 此部分仅用于本地开发测试。
    # 如果EMAIL_ACCOUNT或EMAIL_PASSWORD未设置，程序在之前的检查点就会因RuntimeError退出。
    app.logger.info("尝试以Flask开发模式直接运行应用 (不推荐用于生产)。")
    app.logger.info(f"确保环境变量 EMAIL_ACCOUNT 和 EMAIL_PASSWORD 已正确设置。")
    app.run(host='0.0.0.0', port=5000, debug=False)  # debug=False 更接近生产环境