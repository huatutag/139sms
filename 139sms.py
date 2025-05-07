import os
import smtplib
import ssl
import logging
import sys
import json
import itertools
from email.mime.text import MIMEText
from email.header import Header
from flask import Flask, request, jsonify

# --- SMTP 服务器配置 ---
SMTP_SERVER = 'smtp.163.com'
SMTP_PORT = int(os.environ.get('SMTP_PORT', 465))

# --- API 密钥配置 ---
API_SECRET_KEY = os.environ.get('API_SECRET_KEY')
if not API_SECRET_KEY:
    # 日志系统可能尚未完全被gunicorn接管，直接打印到stderr确保可见性
    critical_message_key = "CRITICAL: 环境变量 'API_SECRET_KEY' (用于接口认证的秘钥) 未设置。应用程序无法启动。"
    print(critical_message_key, file=sys.stderr)
    raise RuntimeError(critical_message_key)

# --- 多账户配置 ---
SENDER_ACCOUNTS_JSON = os.environ.get('SENDER_ACCOUNTS_JSON')
SENDER_ACCOUNTS_LIST = []
sender_account_cycler = None

# 配置基础日志，确保在gunicorn接管前或直接运行时有日志输出
# 注意: Gunicorn的日志配置可能会覆盖这里的处理器，这是预期的行为以避免重复。
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                    format='%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]',
                    stream=sys.stdout)  # 默认输出到stdout

if not SENDER_ACCOUNTS_JSON:
    critical_message_sender = "CRITICAL: 环境变量 'SENDER_ACCOUNTS_JSON' (发件人163邮箱账户JSON列表) 未设置。应用程序无法启动。"
    logging.critical(critical_message_sender)  # 使用logging记录
    print(critical_message_sender, file=sys.stderr)  # 确保启动时可见
    raise RuntimeError(critical_message_sender)
else:
    try:
        parsed_accounts = json.loads(SENDER_ACCOUNTS_JSON)
        if not isinstance(parsed_accounts, list) or not parsed_accounts:
            raise ValueError("JSON内容必须是一个非空列表。")

        for acc in parsed_accounts:
            if not isinstance(acc, dict) or 'email' not in acc or 'auth_code' not in acc:
                raise ValueError("列表中的每个账户必须是包含 'email' 和 'auth_code' 键的字典。")
            SENDER_ACCOUNTS_LIST.append({'email': str(acc['email']), 'auth_code': str(acc['auth_code'])})

        if not SENDER_ACCOUNTS_LIST:
            raise ValueError("解析后发件人账户列表为空。")

        sender_account_cycler = itertools.cycle(SENDER_ACCOUNTS_LIST)
        logging.info(f"成功加载 {len(SENDER_ACCOUNTS_LIST)} 个163发件人账户。")

    except json.JSONDecodeError as e:
        critical_message_json = f"CRITICAL: 环境变量 'SENDER_ACCOUNTS_JSON' 解析失败: {e}。请检查JSON格式。"
        logging.critical(critical_message_json)
        print(critical_message_json, file=sys.stderr)
        raise RuntimeError(critical_message_json)
    except ValueError as e:
        critical_message_value = f"CRITICAL: 环境变量 'SENDER_ACCOUNTS_JSON' 内容无效: {e}。"
        logging.critical(critical_message_value)
        print(critical_message_value, file=sys.stderr)
        raise RuntimeError(critical_message_value)

# 目标收件人邮箱地址
RECEIVER_EMAIL_ADDRESS = os.environ.get('RECEIVER_EMAIL_ADDRESS')
if not RECEIVER_EMAIL_ADDRESS:
    critical_message_receiver = "CRITICAL: 环境变量 'RECEIVER_EMAIL_ADDRESS' (目标收件人邮箱) 未设置。应用程序无法启动。"
    logging.critical(critical_message_receiver)
    print(critical_message_receiver, file=sys.stderr)
    raise RuntimeError(critical_message_receiver)

# --- Flask 应用初始化 ---
app = Flask(__name__)

# --- 日志配置与Gunicorn集成 ---
if __name__ != '__main__':  # 当通过 Gunicorn 运行时
    gunicorn_logger = logging.getLogger('gunicorn.error')
    if gunicorn_logger.handlers:
        app.logger.handlers = gunicorn_logger.handlers
        app.logger.setLevel(gunicorn_logger.level)
        # 尝试移除basicConfig添加的stdout处理器，如果gunicorn也输出到stdout，以避免重复
        root_logger = logging.getLogger()
        for handler in list(root_logger.handlers):  # 遍历副本以安全移除
            if handler.stream == sys.stdout and type(handler) == logging.StreamHandler:
                # 检查gunicorn是否已有stdout处理器，或者处理器数量来决定是否移除
                # 一个简单策略：如果gunicorn接管了，就移除basicConfig的stdout handler
                # 确保移除的是basicConfig设置的那个，而不是gunicorn的
                if any(h.name == 'wsgi' for h in gunicorn_logger.handlers if isinstance(h, logging.StreamHandler)):
                    # 仅当gunicorn明确添加了控制台处理器时，才移除basicConfig的，避免完全无日志
                    if handler in root_logger.handlers:  # 再次确认存在
                        root_logger.removeHandler(handler)
                        # app.logger.info("Removed basicConfig stdout handler to prevent duplication with Gunicorn.")
    else:
        # 如果gunicorn logger没有handlers，app.logger会使用basicConfig的设置
        app.logger.info("Gunicorn logger 尚未配置处理器，app.logger 将使用基础配置。")
else:
    app.logger.info("直接运行脚本，使用基础日志配置。")

app.logger.info(f"SMTP 服务器配置为: {SMTP_SERVER}:{SMTP_PORT}")
if API_SECRET_KEY:  # 不记录实际密钥值
    app.logger.info("API 密钥已配置。/send 接口需要key认证。")
app.logger.info(f"已配置 {len(SENDER_ACCOUNTS_LIST)} 个163发件账户进行轮询。")
app.logger.info(f"目标收件邮箱: {RECEIVER_EMAIL_ADDRESS}")


@app.route('/send', methods=['POST'])
def send_email_api():
    global sender_account_cycler

    # API Key 认证
    provided_key = request.args.get('key')  # 从URL参数获取key, e.g., /send?key=YOUR_KEY
    if not provided_key:
        app.logger.warning("API /send: 拒绝访问 - URL中缺少'key'参数。")
        return jsonify({"error": "拒绝访问：缺少API密钥"}), 401  # Unauthorized

    if provided_key != API_SECRET_KEY:
        app.logger.warning(f"API /send: 拒绝访问 - 提供的'key'无效。")  # 不记录提供的错误key值
        return jsonify({"error": "拒绝访问：无效的API密钥"}), 403  # Forbidden

    # API Key 验证通过
    # app.logger.debug("API /send: API Key验证通过。") # 可选的调试日志

    if not sender_account_cycler:
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

        email_title = data.get('title')  # 邮件主题
        email_content = data.get('content')  # 邮件内容

        if not email_title:
            app.logger.warning(f"API /send ({current_sender_email}): 请求缺少参数 'title' (邮件主题)")
            return jsonify({"error": "请求体中必须包含 'title' (邮件主题) 参数"}), 400

        final_email_body = email_content  # 允许邮件内容为空字符串
        if email_content is None:  # 但如果完全没提供 content 字段，则使用默认值
            final_email_body = "无内容"  # Default content
            app.logger.info(
                f"API /send ({current_sender_email}): 请求参数 'content' (邮件内容) 未提供，使用默认值 '无内容'")
        elif str(email_content).strip() == "":
            app.logger.info(f"API /send ({current_sender_email}): 请求参数 'content' (邮件内容) 为空字符串。")
        # else: # 内容不为空，正常使用
        # final_email_body = email_content # 已在上面赋值

        msg = MIMEText(final_email_body, 'plain', 'utf-8')
        msg['From'] = current_sender_email
        msg['To'] = Header(RECEIVER_EMAIL_ADDRESS, 'utf-8')
        msg['Subject'] = Header(email_title, 'utf-8')  # 使用 email_title

        context = ssl.create_default_context()

        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context) as server:
            server.login(current_sender_email, current_sender_auth_code)
            server.sendmail(current_sender_email, [RECEIVER_EMAIL_ADDRESS], msg.as_string())
            app.logger.info(
                f"API /send: 邮件从 {current_sender_email} 发送到 {RECEIVER_EMAIL_ADDRESS} 成功，主题: '{email_title}' (sendmail命令已完成)")

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
        "api_key_configured": bool(API_SECRET_KEY),
        "num_sender_accounts_configured": len(SENDER_ACCOUNTS_LIST),
        "receiver_email_configured": bool(RECEIVER_EMAIL_ADDRESS)
    }), 200


if __name__ == '__main__':
    app.logger.info("尝试以Flask开发模式直接运行应用 (不推荐用于生产)。")
    app.logger.info(f"确保环境变量 API_SECRET_KEY, SENDER_ACCOUNTS_JSON, 和 RECEIVER_EMAIL_ADDRESS 已正确设置。")
    app.run(host='0.0.0.0', port=5000, debug=False)