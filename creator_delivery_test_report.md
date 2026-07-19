# Creator Delivery Test Report

日期：2026-07-19
测试环境：仓库 `.venv`，Python 3.12，离线测试替身
真实 Gmail：未发送
DeepSeek/付费 API：未调用

## 测试命令与结果

重点测试：

```text
.venv/bin/python -m unittest \
  tests.test_creator_delivery \
  tests.test_email_sender \
  tests.test_sync_latest_creator_ready \
  tests.test_content_generator \
  tests.test_human_value_integration
```

结果：`21 tests, OK`

全量测试：

```text
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

结果：`68 tests, OK`，耗时约 1.2 秒。

附加检查：

- `py_compile`：通过。
- `git diff --check`：通过。
- `python scripts/sync_latest_creator_ready.py --help`：通过。

## 需求覆盖

| # | 验证项 | 结果 | 测试证据 |
|---:|---|---|---|
| 1 | Creator Pipeline 成功时邮件包含今日首选 | PASS | `test_success_email_contains_existing_top_pick_and_attachment` |
| 2 | degraded 时显示 partial_fallback、原因和项目 | PASS | `test_degraded_email_names_partial_fallback_projects_and_reason` |
| 3 | Creator Pipeline 失败时原日报仍发送 | PASS | `test_failed_creator_still_sends_original_report`、原集成失败测试 |
| 4 | 缺少 `daily_selection.json` 时不抛出未处理异常 | PASS | `test_missing_daily_selection_is_handled_without_exception` |
| 5 | 不重复调用 LLM | PASS | 原离线集成测试继续断言 Creator Generator 仅调用一次、legacy generator 未调用；交付模块无 LLM 依赖 |
| 6 | Artifact 包含 `outputs/creator_ready/` | PASS | `test_workflow_uploads_only_required_delivery_paths` |
| 7 | ZIP 不包含敏感文件 | PASS | `test_zip_excludes_sensitive_files` |
| 8 | 本地同步不静默覆盖人工修改 | PASS | `test_existing_date_is_not_silently_overwritten` |
| 9 | 日期和 Run ID 选择正确 | PASS | `test_date_and_run_id_select_the_correct_successful_run` |
| 10 | 现有全量测试继续通过 | PASS | 68/68 |

## 附件验证

`test_send_email_encodes_optional_zip_attachment` 对 Gmail API 的发送调用使用 mock，解码 MIME 原文并确认 ZIP 以附件形式存在。没有连接 Gmail，也没有产生真实 message。

## 安全验证

ZIP 测试在 Creator Ready 目录中主动放入 `.env`、`token.json` 和 `logs/private.log`，确认这些路径均未进入 archive；正常 `publish.txt` 保留。

同步测试使用临时目录和模拟的 `gh run download`，确认已有人工文件保持不变，下载内容进入带 Run ID 的新目录。测试未访问 GitHub。

## 结论

邮件交付、可选 ZIP、Artifact/Actions Summary、fallback 可见性和本地同步均通过离线测试；原有评分、选题和内容生成测试全部保持通过。本轮没有实际发送测试邮件。
