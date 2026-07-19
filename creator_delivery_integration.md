# Creator Delivery Integration

日期：2026-07-19
范围：只优化 Creator Pipeline 产物交付；未修改 Human Value、Creator Strategy、Daily Editor 的评分/选题逻辑，也未改变 Content Generator 的标题与正文生成逻辑。

## 实现结果

现有 GitHub 趋势日报保持不变，并在正文末尾追加独立的“今日小红书首选”区域。该区域在 Creator Pipeline 完成后直接读取当天落盘文件：

- `outputs/creator_ready/YYYY-MM-DD/daily_selection.json`
- `outputs/creator_ready/YYYY-MM-DD/publish.txt`
- `outputs/creator_ready/YYYY-MM-DD/cover.txt`
- `outputs/creator_ready/YYYY-MM-DD/prediction.json`

交付层不调用 LLM、不重新评分、不重新生成标题或正文。若 Creator Pipeline 失败、目录缺失或 JSON 无效，邮件仍发送原 GitHub 日报，并在新区域说明 Creator 内容不可用及原因。

## Gmail 新增内容

邮件新增以下信息：

- 今日首选项目、Publish Score 和推荐原因
- 已生成标题、封面主标题与副标题
- 可直接复制的正文与标签
- 另外两个候选的项目、标题和分数
- Creator Pipeline 的 SUCCESS / DEGRADED / FAILED 状态
- Content Generator 的 `full_llm` / `partial_fallback` / `rules_fallback` 模式
- 使用 rules fallback 的具体项目
- degraded 原因
- 产物生成日期和 GitHub Actions Run ID

日期来自当天 Creator Ready 产物/执行状态，不读取本地“最近一个目录”来猜测日期。

## ZIP 附件

已用向后兼容的可选参数为现有 Gmail 发送器增加附件能力。Creator Ready 可用时，日报附带：

```text
reports/creator-ready-YYYY-MM-DD.zip
```

ZIP 内保留 `outputs/creator_ready/YYYY-MM-DD/` 路径。打包器跳过符号链接，并过滤 `.env`、`.env.*`、Cookie、token、credentials 和 `logs/`。ZIP 创建失败不会阻止邮件正文发送。

## GitHub Actions Artifact 与 Summary

Artifact 名称继续包含 GitHub Run ID：

```text
github-trend-bot-${{ github.run_id }}
```

上传路径收敛为：

```yaml
logs/
reports/
outputs/creator_ready/
```

保留时间仍为 14 天。上传完成后，Actions Summary 追加 Creator Delivery 区域，显示：

- Creator Pipeline 状态
- 生成日期
- 今日首选
- 候选数量
- Creator Ready Artifact 是否实际存在并上传
- Content Generator fallback 与公开模式
- fallback 项目和 degraded/failed 原因

## 本地一键同步

新增：

```text
scripts/sync_latest_creator_ready.py
```

用法：

```powershell
python scripts/sync_latest_creator_ready.py
python scripts/sync_latest_creator_ready.py --date 2026-07-18
python scripts/sync_latest_creator_ready.py --run-id <RUN_ID>
python scripts/sync_latest_creator_ready.py --date 2026-07-18 --overwrite
```

脚本只调用已安装、已登录的 GitHub CLI `gh`，不读取或保存 GitHub token。默认选择最近一次成功的 `daily.yml` run，只从 Artifact 提取 `outputs/creator_ready/`。

同日期目标已存在时，默认写入 `YYYY-MM-DD-run-RUN_ID` 冲突安全目录，不覆盖人工修改；只有显式传入 `--overwrite` 才替换标准日期目录。下载、认证、run 选择或 Artifact 结构错误均返回明确的非零退出码和提示。

## fallback 可见性

Content Generator 现在仅额外记录每个候选的来源元数据：`full_llm` 或 `rules_fallback`。总体对外模式映射为：

| 内部模式 | 邮件与 Summary 模式 |
|---|---|
| `llm_and_templates` | `full_llm` |
| `partial_fallback` | `partial_fallback` |
| `templates_fallback` | `rules_fallback` |
| `templates_only` | `rules_fallback` |

这只是可见性元数据，不改变候选排序、验证规则或生成结果。

## 未执行操作

- 未发送测试邮件。
- 未调用 DeepSeek 或其他付费 API。
- 未触发、重跑或修改线上 GitHub Actions run。
- 未下载线上 Artifact。
- 未 commit、push，也未把每日 `outputs/creator_ready` 加入 main 分支。
