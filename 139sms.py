import os
import smtplib
import ssl
import logging
import sys
import json  # 用于解析JSON配置
import itertools  # 用于轮询账户
from email.mime.text import MIMEText
from email.header import Header
from flask import Flask, request, jsonify

# --- SMTP 服务器配置 (保持不变，因为所有发件账户都是163邮箱) ---
SMTP_SERVER = 'smtp.163.com'
SMTP_PORT = int(os.environ.get('SMTP_PORT', 465))

# --- 多账户配置 ---
SENDER_ACCOUNTS_JSON = os.environ.get('SENDER_ACCOUNTS_JSON')
SENDER_ACCOUNTS_LIST = []
sender_account_cycler = None

if not SENDER_ACCOUNTS_JSON:
    critical_message = "CRITICAL: 环境变量 'SENDER_ACCOUNTS_JSON' (发件人163邮箱账户JSON列表) 未设置。应用程序无法启动。"
    print(critical_message, file=sys.stderr)
    raise RuntimeError(critical_message)
else:
    try:
        parsed_accounts = json.loads(SENDER_ACCOUNTS_JSON)
        if not isinstance(parsed_accounts, list) or not parsed_accounts:
            raise ValueError("JSON内容必须是一个非空列表。")

        for acc in parsed_accounts:
            if not isinstance(acc, dict) or 'email' not in acc or 'auth_code' not in acc:
                raise ValueError("列表中的每个账户必须是包含 'email' 和 'auth_code' 键的字典。")
            SENDER_ACCOUNTS_LIST.append({'email': str(acc['email']), 'auth_code': str(acc['auth_code'])})

        if not SENDER_ACCOUNTS_LIST:  # 再次确认，以防空列表或解析后仍为空
            raise ValueError("解析后发件人账户列表为空。")

        sender_account_cycler = itertools.cycle(SENDER_ACCOUNTS_LIST)
        logging.info(f"成功加载 {len(SENDER_ACCOUNTS_LIST)} 个163发件人账户。")

    except json.JSONDecodeError as e:
        critical_message = f"CRITICAL: 环境变量 'SENDER_ACCOUNTS_JSON' 解析失败: {e}。请检查JSON格式。"
        print(critical_message, file=sys.stderr)
        raise RuntimeError(critical_message)
    except ValueError as e:
        critical_message = f"CRITICAL: 环境变量 'SENDER_ACCOUNTS_JSON' 内容无效: {e}。"
        print(critical_message, file=sys.stderr)
        raise RuntimeError(critical_message)

# 目标收件人邮箱地址 (保持不变)
RECEIVER_EMAIL_ADDRESS = os.environ.get('RECEIVER_EMAIL_ADDRESS')
if not RECEIVER_EMAIL_ADDRESS:
    critical_message = "CRITICAL: 环境变量 'RECEIVER_EMAIL_ADDRESS' (目标收件人邮箱) 未设置。应用程序无法启动。"
    print(critical_message, file=sys.stderr)
    raise RuntimeError(critical_message)

# --- Flask 应用初始化 ---
app = Flask(__name__)

# --- 日志配置 (如果通过gunicorn运行，它会接管) ---
# 在全局作用域配置基础日志，以便在gunicorn启动前或直接运行时就能记录关键信息
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]',
                    stream=sys.stdout)

if __name__ != '__main__':
    gunicorn_logger = logging.getLogger('gunicorn.error')
    # 将app.logger的处理器和级别设置为与gunicorn一致
    # 但全局的logging.info等仍会按basicConfig的配置工作，直到被gunicorn的日志配置覆盖
    if gunicorn_logger.handlers:  # 确保gunicorn logger已初始化
        app.logger.handlers = gunicorn_logger.handlers
        app.logger.setLevel(gunicorn_logger.level)
        # 移除basicConfig可能添加的默认处理器，避免重复日志
        root_logger = logging.getLogger()
        if root_logger.handlers:
            for handler in root_logger.handlers:
                if isinstance(handler, logging.StreamHandler) and handler.stream == sys.stdout:
                    if len(root_logger.handlers) > 1:  # 仅当还有其他gunicorn处理器时移除
                        root_logger.removeHandler(handler)
    else:
        app.logger.info("Gunicorn logger 尚未完全初始化，app.logger 使用默认配置。")
else:
    app.logger.info("直接运行脚本，使用基础日志配置。")

# 应用启动时的日志信息
app.logger.info(f"SMTP 服务器配置为: {SMTP_SERVER}:{SMTP_PORT}")
app.logger.info(f"已配置 {len(SENDER_ACCOUNTS_LIST)} 个163发件账户进行轮询。")
app.logger.info(f"目标收件邮箱: {RECEIVER_EMAIL_ADDRESS}")


@app.route('/send', methods=['POST'])
def send_email_api():
    global sender_account_cycler  # 确保我们使用的是全局的轮询器

    if not sender_account_cycler:  # 双重检查，理论上启动时已处理
        app.logger.error("API /send: 系统错误 - 发件人账户轮询器未初始化。")
        return jsonify({"error": "系统配置错误：无可用发件账户轮询机制"}), 500

    selected_account = next(sender_account_cycler)
    current_sender_email = selected_account['email']
    current_sender_auth_code = selected_account['auth_code']

    app.logger.info(f"API /send: 使用账户 '{current_sender_email}' 发送邮件。")

    try:
        data = request.get_json()
        if not data:
            app.logger.warning(f"API /send ({current_sender_email}): 收到无效的JSON数据或Content-Type不正确")
            return jsonify({"error": "无效的JSON数据，请确保Content-Type为application/json"}), 400

        subject = data.get('邮件主题')
        content = data.get('邮件内容')

        if not subject:
            app.logger.warning(f"API /send ({current_sender_email}): 请求缺少'邮件主题'")
            return jsonify({"error": "必须提供'邮件主题'"}), 400

        if content is None or str(content).strip() == "":
            content = "无内容"
            app.logger.info(f"API /send ({current_sender_email}): 邮件内容未提供，使用默认值 '无内容'")

        msg = MIMEText(content, 'plain', 'utf-8')
        msg['From'] = current_sender_email  # From头部设置为当前选中的163邮箱地址
        msg['To'] = Header(RECEIVER_EMAIL_ADDRESS, 'utf-8')  # 收件人不变
        msg['Subject'] = Header(subject, 'utf-8')

        context = ssl.create_default_context()

        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context) as server:
            # server.set_debuglevel(1)
            server.login(current_sender_email, current_sender_auth_code)  # 使用选中的账户凭据登录
            server.sendmail(current_sender_email, [RECEIVER_EMAIL_ADDRESS], msg.as_string())
            app.logger.info(
                f"API /send: 邮件从 {current_sender_email} 发送到 {RECEIVER_EMAIL_ADDRESS} 成功，主题: '{subject}' (sendmail命令已完成)")

        app.logger.info(f"API /send ({current_sender_email}): SMTP连接正常关闭。")
        return jsonify({"message": "邮件发送成功！"}), 200

    except smtplib.SMTPDataError as e:
        app.logger.error(
            f"API /send ({current_sender_email}): SMTP数据错误: {e.smtp_code} - {e.smtp_error.decode('utf-8', 'ignore') if e.smtp_error else 'Unknown data error'}")
        error_detail = e.smtp_error.decode('utf-8', 'ignore') if e.smtp_error else 'Unknown data error'
        return jsonify({"error": f"SMTP数据错误: {error_detail}"}), 500
    except smtplib.SMTPAuthenticationError as e:
        app.logger.error(
            f"API /send ({current_sender_email}): SMTP认证失败: {e.smtp_code} - {e.smtp_error.decode('utf-8', 'ignore') if e.smtp_error else 'Unknown auth error'}")
        return jsonify({"error": f"SMTP认证失败，请检查账户 {current_sender_email} 的授权码"}), 500
    except smtplib.SMTPConnectError as e:
        app.logger.error(
            f"API /send ({current_sender_email}): 无法连接到SMTP服务器或连接设置错误({SMTP_SERVER}:{SMTP_PORT}): {e}")
        return jsonify({"error": "无法连接到SMTP服务器或连接设置错误"}), 503
    except (smtplib.SMTPServerDisconnected, smtplib.SMTPResponseException) as e:
        if isinstance(e, smtplib.SMTPResponseException) and \
                not (e.smtp_code == -1 and e.smtp_error == b'\x00\x00\x00'):
            app.logger.error(
                f"API /send ({current_sender_email}): 发生未明确处理的SMTP响应异常（可能在quit时）: {e.smtp_code} - {e.smtp_error.decode('utf-8', 'ignore') if e.smtp_error else 'Unknown'}",
                exc_info=True)
            error_detail = e.smtp_error.decode('utf-8', 'ignore') if e.smtp_error else 'Unknown SMTP Response'
            return jsonify({"error": f"发送邮件时发生SMTP响应错误: {error_detail}"}), 500
        log_message_verb = "服务器意外断开连接" if isinstance(e, smtplib.SMTPServerDisconnected) else "服务器连接关闭时有轻微响应"
        app.logger.warning(
            f"API /send ({current_sender_email}): SMTP{log_message_verb} (可能在quit时，但邮件通常已发送): {e}")
        return jsonify({"message": "邮件发送成功！"}), 200
    except ssl.SSLError as e:
        app.logger.error(f"API /send ({current_sender_email}): SSL错误: {e}", exc_info=True)
        return jsonify({"error": f"与邮件服务器建立安全连接时发生SSL错误: {str(e)}"}), 500
    except Exception as e:
        app.logger.error(f"API /send ({current_sender_email}): 发送邮件时发生未预料的错误: {e}", exc_info=True)
        return jsonify({"error": "发送邮件时发生未预料的错误"}), 500


@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "healthy",
        "smtp_server": SMTP_SERVER,
        "num_sender_accounts_configured": len(SENDER_ACCOUNTS_LIST),  # 显示配置的账户数
        "receiver_email_configured": bool(RECEIVER_EMAIL_ADDRESS)
    }), 200


if __name__ == '__main__':
    # 这部分日志仅用于直接运行脚本时
    app.logger.info("尝试以Flask开发模式直接运行应用 (不推荐用于生产)。")
    app.logger.info(f"确保环境变量 SENDER_ACCOUNTS_JSON 和 RECEIVER_EMAIL_ADDRESS 已正确设置。")
    app.run(host='0.0.0.0', port=5000, debug=False)