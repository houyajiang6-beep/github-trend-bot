# github-trend-bot 腾讯云部署记录

部署日期：2026-07-11  
部署状态：应用、环境、凭据、预检、cron 与日志轮转均已配置；因服务器无法访问 `github.com` 和 `gmail.googleapis.com`，真实生产测试未通过，cron 已安全暂停。

## 1. 目标服务器

- 云平台：腾讯云轻量应用服务器 Lighthouse
- 实例 ID：`lhins-47x8tcz0`
- 区域：上海 `ap-shanghai`
- 公网 IP：`101.43.122.8`
- 操作系统：Ubuntu 22.04.5 LTS
- 系统时区：`Asia/Shanghai`
- cron 服务：`active`
- 登录方式：腾讯云 OrcaTerm TAT 免密连接

## 2. 部署内容

- 生产目录：`/opt/github-trend-bot`
- 运行用户：`githubbot`
- 项目目录权限：`750`
- 虚拟环境：`/opt/github-trend-bot/.venv`
- Python：3.10.12
- 生产依赖：完整安装自 `requirements.txt`
- `pip check`：通过
- 核心抓取、DeepSeek、报告和 Gmail 逻辑：未修改

Python 3.10 当前可运行，但 Google SDK 已提示其支持将在 2026-10-04 后结束。应在此日期前升级服务器到 Ubuntu 24.04 / Python 3.12，或迁移到受支持的 Python 3.11+ 环境。

## 3. 上传与完整性

- 代码包：`github-trend-bot-deploy.tar.gz`
- 包大小：21,306 字节
- SHA-256：`a2697bd24e4267d8d8f4f7918d5b21ff46b2effbdb075347ad9356eb1b9ab9c3`
- 服务器端 `sha256sum -c`：通过
- `.env`、`credentials.json`、`token.json`：已分别传输
- 本地源文件与服务器生产副本 `cmp`：全部一致
- 三个敏感文件权限：`600`
- 三个敏感文件所有者：`githubbot:githubbot`
- `/home/ubuntu` 中的临时密钥副本与解压暂存目录：已删除

## 4. Python 环境

服务器访问默认 PyPI 时下载 pip 超时。未修改项目依赖，改用腾讯云 PyPI 镜像完成安装：

```bash
sudo -u githubbot /opt/github-trend-bot/.venv/bin/pip install \
  --timeout 120 --retries 5 \
  -i https://mirrors.cloud.tencent.com/pypi/simple \
  -r /opt/github-trend-bot/requirements.txt

sudo -u githubbot /opt/github-trend-bot/.venv/bin/python -m pip check
```

## 5. 生产预检

执行命令：

```bash
sudo -u githubbot sh -c \
  'cd /opt/github-trend-bot && ./.venv/bin/python production_check.py'
```

结果：通过。

- 采集配置：通过
- DeepSeek 配置：通过
- Gmail 文件配置：通过
- 目录写权限：通过
- GitHub Token：已读取并携带 Bearer Authorization header
- GitHub core rate limit：`remaining=5000, used=0, limit=5000`
- 敏感文件权限：通过
- 失败邮件提醒配置：启用

## 6. cron 与日志轮转

已安装并验证：

- cron 配置源：`/opt/github-trend-bot/deploy/github-trend-bot.cron`
- 系统 cron 配置：`/etc/cron.d/github-trend-bot.disabled`
- logrotate 配置：`/etc/logrotate.d/github-trend-bot`
- 定时表达式：每天 `08:00`
- 执行用户：`githubbot`
- 防重复运行：`flock`
- 服务器时区：`Asia/Shanghai`

由于真实测试发现必要域名不可达，cron 文件已改名为 `.disabled`，避免每天自动失败。

日志位置：

```text
/opt/github-trend-bot/logs/github-trend-bot.log
/opt/github-trend-bot/logs/production-runner.log
/opt/github-trend-bot/logs/cron.log
```

`github-trend-bot.log` 和 `production-runner.log` 由 Python 轮转；`cron.log` 由系统 logrotate 每日轮转并保留 14 份压缩记录。

## 7. 真实生产测试

执行命令：

```bash
sudo -u githubbot sh -c \
  'cd /opt/github-trend-bot && ./.venv/bin/python production_runner.py'
```

结果：失败，生产运行器正确返回非零退出码。

失败阶段：获取 `https://github.com/trending?since=daily`。经过 3 次重试后仍超时，因此未进入 DeepSeek 分析和日报 Gmail 发送阶段。

独立 IPv4 HTTPS 诊断：

| 域名 | 结果 |
|---|---|
| `github.com` | 连接超时 |
| `api.github.com` | HTTP 200，可达 |
| `api.deepseek.com` | HTTP 401，无 Key 探测的预期响应，可达 |
| `gmail.googleapis.com` | 连接超时 |

失败提醒逻辑已触发，但 Gmail API 域名同样超时，因此无法投递失败邮件。应用配置和 OAuth 文件本身已通过预检。

## 8. 上线前剩余阻塞项

必须先为服务器提供合规、稳定的出站 HTTPS 网络，使以下两个域名可达：

```text
github.com:443
gmail.googleapis.com:443
```

不得通过开放 SMTP 25 入站/出站端口解决。网络就绪后执行：

```bash
curl -4 -I --connect-timeout 10 https://github.com/trending
curl -4 -I --connect-timeout 10 https://gmail.googleapis.com

sudo -u githubbot sh -c \
  'cd /opt/github-trend-bot && ./.venv/bin/python production_runner.py'
```

确认真实日报邮件发送成功后启用 cron：

```bash
sudo mv /etc/cron.d/github-trend-bot.disabled /etc/cron.d/github-trend-bot
sudo chown root:root /etc/cron.d/github-trend-bot
sudo chmod 0644 /etc/cron.d/github-trend-bot
sudo systemctl restart cron
sudo systemctl status cron --no-pager
```

次日 08:00 后验收：

```bash
sudo -u githubbot tail -n 200 /opt/github-trend-bot/logs/production-runner.log
sudo -u githubbot tail -n 200 /opt/github-trend-bot/logs/github-trend-bot.log
sudo -u githubbot tail -n 200 /opt/github-trend-bot/logs/cron.log
sudo -u githubbot ls -lh /opt/github-trend-bot/reports/
sudo journalctl -u cron --since today --no-pager
```
