import os
import smtplib
import ssl
import logging
import sys
from email.mime.text import MIMEText
from email.header import Header
from flask import Flask, request, jsonify

# --- 配置信息 (与上一版163邮箱配置相同) ---
SMTP_SERVER = 'smtp.163.com'
SMTP_PORT = int(os.environ.get('SMTP_PORT', 465))

SENDER_163_EMAIL_ACCOUNT = os.environ.get('SENDER_163_EMAIL_ACCOUNT')
if not SENDER_163_EMAIL_ACCOUNT:
    critical_message = "CRITICAL: 环境变量 'SENDER_163_EMAIL_ACCOUNT' (发件人163邮箱) 未设置。应用程序无法启动。"
    print(critical_message, file=sys.stderr)
    raise RuntimeError(critical_message)

SENDER_163_AUTH_CODE = os.environ.get('SENDER_163_AUTH_CODE')
if not SENDER_163_AUTH_CODE:
    critical_message = "CRITICAL: 环境变量 'SENDER_163_AUTH_CODE' (发件人163邮箱授权码) 未设置。应用程序无法启动。"
    print(critical_message, file=sys.stderr)
    raise RuntimeError(critical_message)

RECEIVER_EMAIL_ADDRESS = os.environ.get('RECEIVER_EMAIL_ADDRESS')
if not RECEIVER_EMAIL_ADDRESS:
    critical_message = "CRITICAL: 环境变量 'RECEIVER_EMAIL_ADDRESS' (目标收件人邮箱) 未设置。应用程序无法启动。"
    print(critical_message, file=sys.stderr)
    raise RuntimeError(critical_message)

CURRENT_SENDER_EMAIL = SENDER_163_EMAIL_ACCOUNT
CURRENT_RECEIVER_EMAIL = RECEIVER_EMAIL_ADDRESS

app = Flask(__name__)

if __name__ != '__main__':
    gunicorn_logger = logging.getLogger('gunicorn.error')
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)
else:
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]')
    app.logger.info("直接运行脚本，使用基础日志配置。")

app.logger.info(f"SMTP 服务器配置为: {SMTP_SERVER}:{SMTP_PORT}")
app.logger.info(f"发件邮箱账户 (163): {SENDER_163_EMAIL_ACCOUNT}")
app.logger.info(f"目标收件邮箱: {RECEIVER_EMAIL_ADDRESS}")


@app.route('/send', methods=['POST'])
def send_email_api():
    try:
        data = request.get_json()
        if not data:
            app.logger.warning("API /send: 收到无效的JSON数据或Content-Type不正确")
            return jsonify({"error": "无效的JSON数据，请确保Content-Type为application/json"}), 400

        subject = data.get('邮件主题')
        content = data.get('邮件内容')

        if not subject:
            app.logger.warning("API /send: 请求缺少'邮件主题'")
            return jsonify({"error": "必须提供'邮件主题'"}), 400

        if content is None or str(content).strip() == "":
            content = "无内容"
            app.logger.info("API /send: 邮件内容未提供，使用默认值 '无内容'")

        msg = MIMEText(content, 'plain', 'utf-8')
        msg['From'] = CURRENT_SENDER_EMAIL
        msg['To'] = Header(CURRENT_RECEIVER_EMAIL, 'utf-8')
        msg['Subject'] = Header(subject, 'utf-8')

        context = ssl.create_default_context()

        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context) as server:
            # server.set_debuglevel(1)
            server.login(SENDER_163_EMAIL_ACCOUNT, SENDER_163_AUTH_CODE)
            server.sendmail(CURRENT_SENDER_EMAIL, [CURRENT_RECEIVER_EMAIL], msg.as_string())
            # 如果sendmail没有抛出异常，邮件已被服务器接受
            app.logger.info(
                f"API /send: 邮件从 {CURRENT_SENDER_EMAIL} 发送到 {CURRENT_RECEIVER_EMAIL} 成功，主题: '{subject}' (sendmail命令已完成)")

        # 如果 'with' 块成功退出 (包括隐式的 server.quit())
        app.logger.info("API /send: SMTP连接正常关闭。")
        return jsonify({"message": "邮件发送成功！"}), 200

    # --- 优化后的异常处理 ---

    # 1. 明确指示邮件发送失败的SMTP错误 (在sendmail期间或之前发生)
    except smtplib.SMTPDataError as e:  # 例如：服务器拒绝邮件内容/格式
        app.logger.error(
            f"API /send: SMTP数据错误: {e.smtp_code} - {e.smtp_error.decode('utf-8', 'ignore') if e.smtp_error else 'Unknown data error'}")
        error_detail = e.smtp_error.decode('utf-8', 'ignore') if e.smtp_error else 'Unknown data error'
        return jsonify({"error": f"SMTP数据错误: {error_detail}"}), 500
    except smtplib.SMTPAuthenticationError as e:  # 认证失败
        app.logger.error(
            f"API /send: SMTP认证失败({SENDER_163_EMAIL_ACCOUNT}): {e.smtp_code} - {e.smtp_error.decode('utf-8', 'ignore') if e.smtp_error else 'Unknown auth error'}")
        return jsonify({"error": "SMTP认证失败，请检查163邮箱账号或授权码"}), 500
    # SMTPConnectError 包含了连接阶段的多种问题，如服务器不可达、HELO/EHLO阶段错误
    except smtplib.SMTPConnectError as e:
        app.logger.error(f"API /send: 无法连接到SMTP服务器或连接设置错误({SMTP_SERVER}:{SMTP_PORT}): {e}")
        return jsonify({"error": "无法连接到SMTP服务器或连接设置错误"}), 503
    # 可以根据需要添加 SMTPSenderRefused, SMTPRecipientsRefused 等

    # 2. 处理邮件已发送，但在关闭连接时可能发生的异常
    except (smtplib.SMTPServerDisconnected, smtplib.SMTPResponseException) as e:
        # 假设如果sendmail已成功（上面的日志已打印），这些是在quit()阶段的问题
        if isinstance(e, smtplib.SMTPResponseException) and \
                not (e.smtp_code == -1 and e.smtp_error == b'\x00\x00\x00'):
            # 一个非预期的、非特定的“良性”SMTPResponseException
            app.logger.error(
                f"API /send: 发生未明确处理的SMTP响应异常（可能在quit时）: {e.smtp_code} - {e.smtp_error.decode('utf-8', 'ignore') if e.smtp_error else 'Unknown'}",
                exc_info=True)
            error_detail = e.smtp_error.decode('utf-8', 'ignore') if e.smtp_error else 'Unknown SMTP Response'
            # 尽管邮件可能已发送，但这是一个未明确归类为“良性”的响应码，谨慎起见返回错误
            return jsonify({"error": f"发送邮件时发生SMTP响应错误: {error_detail}"}), 500

        # SMTPServerDisconnected 或 特定的良性 SMTPResponseException (-1, ...)
        log_message_verb = "服务器意外断开连接" if isinstance(e, smtplib.SMTPServerDisconnected) else "服务器连接关闭时有轻微响应"
        app.logger.warning(f"API /send: SMTP{log_message_verb} (可能在quit时，但邮件通常已发送): {e}")
        return jsonify({"message": "邮件发送成功！"}), 200  # 统一成功消息

    # 3. SSL相关错误
    except ssl.SSLError as e:
        app.logger.error(f"API /send: SSL错误: {e}", exc_info=True)
        return jsonify({"error": f"与邮件服务器建立安全连接时发生SSL错误: {str(e)}"}), 500

    # 4. 捕获所有其他未预料的错误
    except Exception as e:
        app.logger.error(f"API /send: 发送邮件时发生未预料的错误: {e}", exc_info=True)
        return jsonify({"error": "发送邮件时发生未预料的错误"}), 500


@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "healthy",
        "smtp_server": SMTP_SERVER,
        "sender_email_configured": bool(SENDER_163_EMAIL_ACCOUNT),
        "receiver_email_configured": bool(RECEIVER_EMAIL_ADDRESS)
    }), 200


if __name__ == '__main__':
    app.logger.info("尝试以Flask开发模式直接运行应用 (不推荐用于生产)。")
    app.logger.info(
        f"确保环境变量 SENDER_163_EMAIL_ACCOUNT, SENDER_163_AUTH_CODE, 和 RECEIVER_EMAIL_ADDRESS 已正确设置。")
    app.run(host='0.0.0.0', port=5000, debug=False)