# 腾讯云 Ubuntu 生产部署

目标：每天北京时间 08:00 自动抓取 GitHub Trending，调用 DeepSeek 生成中文日报，并通过 Gmail API 发送邮件。以下命令假定使用 Ubuntu 24.04 LTS、部署目录 `/opt/github-trend-bot` 和专用用户 `githubbot`。

## 1. 腾讯云与系统准备

腾讯云安全组仅需保留 SSH 22 入站，建议只允许管理员固定公网 IP。程序不需要开放 Web 或 SMTP 入站端口，只需要出站 TCP 443 访问 GitHub、DeepSeek 和 Google。

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip cron ca-certificates curl rsync logrotate util-linux
python3 --version

sudo timedatectl set-timezone Asia/Shanghai
timedatectl
sudo systemctl enable --now cron
```

生产建议 Python 3.11 或更高版本。Ubuntu 24.04 默认 Python 3.12 可直接使用。

创建无登录专用用户和部署目录：

```bash
sudo adduser --system --group --home /opt/github-trend-bot githubbot
sudo install -d -o githubbot -g githubbot -m 0750 /opt/github-trend-bot
```

## 2. 上传代码

在本地项目的上级目录执行，避免上传虚拟环境、日志、报告和密钥：

```bash
rsync -av \
  --exclude '.venv' \
  --exclude '.env' \
  --exclude 'credentials*.json' \
  --exclude 'token*.json' \
  --exclude '__pycache__' \
  --exclude 'logs/*' \
  --exclude 'reports/*' \
  ./github-trend-bot/ user@SERVER_IP:/tmp/github-trend-bot/

ssh user@SERVER_IP
sudo rsync -a --delete /tmp/github-trend-bot/ /opt/github-trend-bot/
sudo chown -R githubbot:githubbot /opt/github-trend-bot
sudo chmod 0750 /opt/github-trend-bot
```

不要使用 `--delete` 同步包含生产 `.env` 和 OAuth 文件的目录。上面的 `/tmp` 上传明确排除了这些文件。

## 3. Python 环境

```bash
sudo -u githubbot python3 -m venv /opt/github-trend-bot/.venv
sudo -u githubbot /opt/github-trend-bot/.venv/bin/python -m pip install --upgrade pip
sudo -u githubbot /opt/github-trend-bot/.venv/bin/pip install -r /opt/github-trend-bot/requirements.txt
sudo -u githubbot /opt/github-trend-bot/.venv/bin/python -m pip check
```

## 4. 生产环境变量

```bash
sudo -u githubbot cp /opt/github-trend-bot/.env.example /opt/github-trend-bot/.env
sudo -u githubbot nano /opt/github-trend-bot/.env
```

至少确认以下配置：

```dotenv
AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=真实DeepSeekKey
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_THINKING=false
AI_REQUEST_TIMEOUT=120
AI_MAX_RETRIES=3

GITHUB_TOKEN=只读GitHubToken

EMAIL_FROM=完成OAuth授权的Gmail地址
EMAIL_TO=日报收件地址
GMAIL_CREDENTIALS_FILE=credentials.json
GMAIL_TOKEN_FILE=token.json

PRODUCTION_RUN_TIMEOUT=1800
FAILURE_ALERT_ENABLED=true
FAILURE_ALERT_TO=失败提醒收件地址

REPORT_TIMEZONE=Asia/Shanghai
LOG_LEVEL=INFO
LOG_DIR=logs
REPORT_DIR=reports
```

`GITHUB_TOKEN` 建议使用 Fine-grained personal access token，仅访问公开仓库，不授予写权限。若只读取公开数据，不需要选择仓库写权限。Token 无效会在生产预检中返回 HTTP 401。

设置权限：

```bash
sudo chown githubbot:githubbot /opt/github-trend-bot/.env
sudo chmod 0600 /opt/github-trend-bot/.env
sudo -u githubbot mkdir -p /opt/github-trend-bot/logs /opt/github-trend-bot/reports
```

## 5. Gmail OAuth 文件

先在有浏览器的本地电脑运行 `python auth_gmail.py`，生成 `token.json`。OAuth 客户端文件必须命名为 `credentials.json`；若浏览器下载成 `credentials.json.json`，上传前先改名。

```bash
scp credentials.json token.json user@SERVER_IP:/tmp/
ssh user@SERVER_IP
sudo install -o githubbot -g githubbot -m 0600 /tmp/credentials.json /opt/github-trend-bot/credentials.json
sudo install -o githubbot -g githubbot -m 0600 /tmp/token.json /opt/github-trend-bot/token.json
rm -f /tmp/credentials.json /tmp/token.json
```

失败提醒也通过同一 Gmail API 发送。如果 Gmail 本身故障，提醒邮件可能无法送达，但异常仍会写入 `production-runner.log` 和 `cron.log`。

## 6. 生产预检

预检不会发送邮件或调用 DeepSeek；它会检查配置、目录写权限、敏感文件权限、GitHub Token 认证头和当前 rate limit：

```bash
cd /opt/github-trend-bot
sudo -u githubbot ./.venv/bin/python production_check.py
```

必须看到 `生产预检通过` 后再安装 cron。若看到 GitHub HTTP 401，请重新创建 Token；若未配置 Token，预检会给出匿名额度警告。

## 7. 分阶段验证

无 AI、无邮件测试：

```bash
sudo -u githubbot /opt/github-trend-bot/.venv/bin/python /opt/github-trend-bot/main.py --dry-run --skip-ai
```

真实 DeepSeek、无邮件测试：

```bash
sudo -u githubbot /opt/github-trend-bot/.venv/bin/python /opt/github-trend-bot/main.py --dry-run
```

Gmail 单独测试：

```bash
sudo -u githubbot /opt/github-trend-bot/.venv/bin/python /opt/github-trend-bot/main.py --test-email
```

完整生产运行，会生成报告并发送日报：

```bash
sudo -u githubbot /opt/github-trend-bot/.venv/bin/python /opt/github-trend-bot/production_runner.py
echo $?
```

## 8. 安装 cron

项目已提供 `/opt/github-trend-bot/deploy/github-trend-bot.cron`：

```bash
sudo install -o root -g root -m 0644 \
  /opt/github-trend-bot/deploy/github-trend-bot.cron \
  /etc/cron.d/github-trend-bot

sudo systemctl restart cron
sudo systemctl status cron --no-pager
sudo cat /etc/cron.d/github-trend-bot
```

定时项使用 `flock` 防止重复运行，并调用 `production_runner.py`。服务器时区为 `Asia/Shanghai` 时，`0 8 * * *` 即每天北京时间 08:00。

## 9. 安装日志轮转

`github-trend-bot.log` 和 `production-runner.log` 已由 Python 自身轮转。cron 标准输出单独使用系统 logrotate：

```bash
sudo install -o root -g root -m 0644 \
  /opt/github-trend-bot/deploy/github-trend-bot.logrotate \
  /etc/logrotate.d/github-trend-bot
sudo logrotate -d /etc/logrotate.d/github-trend-bot
```

## 10. 次日验收与排障

```bash
date
sudo -u githubbot tail -n 200 /opt/github-trend-bot/logs/production-runner.log
sudo -u githubbot tail -n 200 /opt/github-trend-bot/logs/github-trend-bot.log
sudo -u githubbot tail -n 200 /opt/github-trend-bot/logs/cron.log
sudo -u githubbot ls -lh /opt/github-trend-bot/reports/
sudo journalctl -u cron --since today --no-pager
```

验收标准：08:00 后出现当日 HTML/JSON 报告、`production-runner.log` 记录成功、Gmail 收到日报且 cron 返回零退出码。
