# GitHub 每日趋势日报

生产部署请直接参阅 [腾讯云 Ubuntu 生产部署文档](DEPLOY_TENCENT_CLOUD_UBUNTU.md)。文档包含生产预检、专用用户、Gmail OAuth、失败提醒、cron 和日志轮转配置。

每天北京时间 08:00 抓取 GitHub Trending（日榜），通过 GitHub REST API 补全项目总 Star、语言、简介和 README，再调用 DeepSeek 的 OpenAI 兼容 Chat Completions API 生成中文分析，最后通过 Gmail API（OAuth2 + HTTPS 443）发送 HTML 邮件。日报现包含项目成长趋势、Star 增速、AI 领域分类、学习建议，以及技术趋势、商业机会、可能影响的公司/行业和长期关注方向。

程序还会基于同一份日报生成抖音标题、30 秒口播稿、小红书笔记和视频选题，保存到 `reports/content/YYYY-MM-DD.json`。新增洞察或内容生成失败时会自动使用规则版降级结果，不会中断原 Gmail 日报发送。

## 项目结构

```text
github-trend-bot/
├── main.py              # 调度、降级、落盘和日志入口
├── crawler.py           # Trending 抓取 + GitHub API/README 补全
├── ai_summary.py        # DeepSeek 分析、降级摘要和 HTML 渲染
├── market_insight.py    # 成长指标、AI 分类、技术与商业洞察
├── content_generator.py # 抖音/口播/小红书/视频选题 JSON
├── email_sender.py      # Gmail API 发送
├── auth_gmail.py        # 首次 OAuth2 授权（在有浏览器的电脑运行）
├── config.py            # .env 配置加载与校验
├── requirements.txt
├── .env                 # 空安全模板，已被 Git 忽略
├── .env.example
├── .gitignore
├── logs/
└── reports/
```

## 1. 腾讯云 Ubuntu 部署命令

以下假定代码上传到 `/opt/github-trend-bot`。先在腾讯云安全组保留 SSH 22（最好只允许你的固定公网 IP）；本系统不需要新增入站端口，也不使用 SMTP 25。

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip cron ca-certificates curl ufw

sudo mkdir -p /opt/github-trend-bot
sudo chown -R "$USER":"$USER" /opt/github-trend-bot
# 从本地电脑上传本目录，例如：
# scp -r ./github-trend-bot/* user@SERVER_IP:/opt/github-trend-bot/
# 注意隐藏文件需单独上传或用 rsync -av。

cd /opt/github-trend-bot
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

chmod 700 /opt/github-trend-bot
chmod 600 .env
mkdir -p logs reports
```

编辑配置：

```bash
nano /opt/github-trend-bot/.env
```

至少填写 `DEEPSEEK_API_KEY`、`EMAIL_FROM`、`EMAIL_TO`。强烈建议填写只用于读取公开数据的 `GITHUB_TOKEN`，以提高 GitHub API 限额；不要给不需要的写权限。

### 防火墙和连通性检查

先检查，不要盲目修改现有 SSH 规则：

```bash
sudo ufw status verbose
sudo ss -lntup
curl -I --connect-timeout 10 https://github.com/trending
curl -I --connect-timeout 10 https://api.github.com
curl -I --connect-timeout 10 https://api.deepseek.com
curl -I --connect-timeout 10 https://gmail.googleapis.com
```

若 UFW 当前未启用且你决定启用，必须先放行 SSH；腾讯云安全组也要保留 SSH 来源规则，避免把自己锁在服务器外：

```bash
sudo ufw allow from YOUR_PUBLIC_IP to any port 22 proto tcp
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw enable
```

该程序只需出站 TCP 443。`api.deepseek.com`、GitHub 或 Google 在你的服务器网络不可达时，应先解决合规的出站网络/DNS 路由，不能通过开放 SMTP 端口解决。

## 2. Gmail API / OAuth2 配置

1. 打开 Google Cloud Console，新建项目。
2. 在 “APIs & Services” 中启用 **Gmail API**。
3. 配置 OAuth consent screen。个人 Gmail 选择 External，并将自己的 Gmail 加为 Test user。
4. 创建凭据：OAuth client ID → Application type 选择 **Desktop app**。
5. 下载 JSON，改名为 `credentials.json`，放到本项目根目录。
6. 在一台有浏览器的本地电脑进入项目，安装依赖并执行：

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python auth_gmail.py
```

浏览器只会申请 `gmail.send` 最小权限。成功后得到 `token.json`。把 `credentials.json` 和 `token.json` 上传到服务器：

```bash
scp credentials.json token.json user@SERVER_IP:/opt/github-trend-bot/
ssh user@SERVER_IP 'chmod 600 /opt/github-trend-bot/credentials.json /opt/github-trend-bot/token.json'
```

重要：OAuth consent screen 若长期保持 External + Testing，Google 可能让含 Gmail scope 的 refresh token 在 7 天后失效。长期自动化应将应用发布为 Production（仅自用也可保留最小 scope，并按 Google 页面要求完成配置）；令牌被撤销、密码变化等情况也可能要求重新授权。

`EMAIL_FROM` 应是完成 OAuth 授权的 Gmail 地址，或该账号已经配置并验证过的 “Send mail as” 地址。

## 3. DeepSeek API 配置

1. 登录 [DeepSeek 开放平台](https://platform.deepseek.com/)。
2. 在 [API Keys](https://platform.deepseek.com/api_keys) 页面创建新的 API Key。
3. Key 只会完整显示一次，请立即复制并仅写入服务器 `/opt/github-trend-bot/.env`，不要提交到 Git。

```dotenv
AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=替换为你的DeepSeek_API_Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_THINKING=false
AI_REQUEST_TIMEOUT=120
AI_MAX_RETRIES=3
```

默认模型为 `deepseek-v4-flash`，日报默认关闭思考模式以减少延迟和消耗。程序使用 DeepSeek 的 OpenAI 兼容接口，但请求地址固定由 `DEEPSEEK_BASE_URL` 控制，并明确拒绝 `api.openai.com`。不要把 `.env`、终端历史或日志提交到 Git；请在 DeepSeek 平台关注余额和用量。

请求超时默认为 120 秒，失败后最多重试 3 次，等待时间依次为 1、2、4 秒。认证失败或余额不足不会无意义重试；AI 最终失败时仍会生成基础数据日报。

## 4. 测试顺序

```bash
cd /opt/github-trend-bot
source .venv/bin/activate

# 无 AI 测试：只测 GitHub 采集和本地报告，不初始化 AI 客户端、不发邮件
python main.py --dry-run --skip-ai

# 真实 DeepSeek 测试：通常只产生一次 API 请求，生成报告但不发邮件
python main.py --dry-run

# 只测试 Gmail API
python main.py --test-email

# 完整执行
python main.py
```

浏览器查看 `reports/YYYY-MM-DD.html`。真实 AI 测试仅在请求失败时重试，最多额外请求 3 次。若 DeepSeek 临时失败、认证失败或余额不足，任务会记录明确错误并生成降级数据版；若抓取或 Gmail 发送失败，进程返回非零状态。

结构化日报保存在 `reports/YYYY-MM-DD.json`，社媒内容保存在 `reports/content/YYYY-MM-DD.json`。成长趋势会自动读取最近一份更早的结构化日报进行对比；没有历史快照时会明确标注只能判断当日速度。

## 5. 每天北京时间 08:00 的 cron

先把服务器时区设为上海并确认：

```bash
sudo timedatectl set-timezone Asia/Shanghai
timedatectl
sudo systemctl enable --now cron
crontab -e
```

加入以下内容。使用 `flock` 防止重复执行，cron 的标准输出也追加到独立日志：

```cron
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 8 * * * flock -n /tmp/github-trend-bot.cron.lock /opt/github-trend-bot/.venv/bin/python /opt/github-trend-bot/main.py >> /opt/github-trend-bot/logs/cron.log 2>&1
```

验证 cron：

```bash
crontab -l
systemctl status cron --no-pager
journalctl -u cron --since today --no-pager
```

## 6. 查看日志

```bash
tail -n 200 /opt/github-trend-bot/logs/github-trend-bot.log
tail -f /opt/github-trend-bot/logs/github-trend-bot.log
tail -n 200 /opt/github-trend-bot/logs/cron.log
ls -lh /opt/github-trend-bot/reports/
```

应用日志按 10 MB 轮转并保留 14 个备份。`cron.log` 可再用系统 `logrotate` 管理。

## 安全说明

- `.env`、`credentials.json`、`token.json` 均已写入 `.gitignore`，生产环境权限设为 `600`。
- Gmail 只申请 `gmail.send`，不申请读取邮箱权限。
- GitHub Token 仅需读取公开仓库；不应授予仓库写权限。
- 不在日志中输出 API Key 或 OAuth token。
- README 属于不可信外部文本，只作为模型输入资料；提示词明确禁止据此编造外部事件。
- 定期轮换 DeepSeek/GitHub Key，并检查 Google 账号的第三方授权。
