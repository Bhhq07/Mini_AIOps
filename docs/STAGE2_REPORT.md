# Mini AIOps 阶段 2 验收报告

## 1. 结论与范围

阶段 2 已实现统一串行调度、严格结果校验、JSONL 历史和本地告警。**阶段 2 自动化测试 30/30 通过，真实集成测试完成；真实系统整体状态为 ERROR，不是五模块全绿。** Nginx 原始日志对 aiops 不可读，已如实报告，未跳过、提权或修改权限。

所有开发操作位于 `/home/aiops/mini-aiops`，执行用户为 aiops。未修改系统 Cron、SELinux、用户组、ACL、sudoers 或服务配置，未新增第三方依赖、数据库、Web 页面和大模型调用。

## 2. 文件与备份

### 修改文件

| 文件 | 变更 |
|---|---|
| [run_aiops.sh](../run_aiops.sh) | 保留 Shell 入口，定位项目目录后 exec Python 调度器，透传参数及整体退出码；不再让 tee 掩盖模块失败 |
| [Nginx JSON 包装器](../shell/check_nginx_json_wrapper.sh) | 修正底层失败误报 OK；错误映射为 ERROR/3，阈值对应 WARNING/1 和 CRITICAL/2，使用 json.dumps 安全转义；文本模式保留旧行为 |
| [README](README.md) | 补充阶段 2 当前用法，旧阶段 0 内容明确标为归档 |

### 新增文件

| 文件 | 用途 |
|---|---|
| [run_monitor.py](../python/run_monitor.py) | 调度、契约验证、汇总、运行锁、历史与本地告警 |
| [test_run_monitor.py](../tests/test_run_monitor.py) | 30 个阶段 2 自动化测试 |
| [本报告](STAGE2_REPORT.md) | 验收结果、使用方法与已知限制 |

运行产物：[JSONL 历史](../logs/monitor_history.jsonl)、[真实集成报告](../logs/stage2-integration-5b449fee-6e65-4e89-8370-6d04e44ef626.json)、项目 state 目录中的运行锁。原有 [文本日志](../logs/run_aiops.log) 追加摘要和告警。

备份目录：`state/backups/stage2-20261113T112344Z/`。包含修改前的入口、Nginx 包装器和 README；review 子目录保留首次通过测试的新增实现；logs、state 子目录保留真实集成测试前的日志和服务状态。目录时间来自虚拟机时钟，不代表可信的现实时间。

修改前后 SHA-256 校验确认：CPU、内存、磁盘、服务、Nginx 核心扫描脚本及 thresholds.conf **全部未变**。未删除或恢复旧模块。

## 3. 核心架构

```text
run_aiops.sh
  -> python/run_monitor.py
     -> 获取 state/run_aiops.lock 非阻塞独占锁
     -> CPU -> 内存 -> 磁盘 -> 服务 -> Nginx（全部传 --json）
     -> 校验每个结果，保留 stdout / stderr / 实际退出码与耗时
     -> 保留旧入口 uptime 信息
     -> 汇总状态和告警，追加文本日志
     -> 历史文件独占锁 -> 追加一条完整 JSONL -> fsync
     -> stdout 报告 + stderr 告警 + 对应整体退出码
```

- 每模块默认超时 10 秒，`--timeout` 可调整；CPU 现有采样约 1 秒。
- 不使用 shell=True，不拼接执行任意用户命令。模块仍是现有脚本，服务参数保持 nginx、sshd，磁盘路径保持 `/`。
- 单模块失败、非法输出或超时不会阻止之后的模块运行。超时杀死本模块进程组，防止留下继承输出管道的普通子进程。
- `time.monotonic()` 计算耗时，带时区 ISO 8601 记录时间，UUID4 标识每次运行。
- 锁覆盖整个模块执行和持久化过程。竞争启动返回 ERROR/3、lock_busy，不执行模块、不写入竞争运行的历史，避免交叉修改状态。锁文件保留，由内核管理锁释放，不靠删除锁文件解锁。

## 4. 结果契约与异常分类

保留模块的六个字段，不改已有 metrics 内容：module、timestamp、status、message、metrics、exit_code。在模块结果中增加 execution，包含 outcome、command、return_code、stdout、stderr、json_source、started_at、finished_at、duration_ms。

系统报告同样具有上述六个字段，并增加 run_id、started_at、finished_at、duration_ms、results、alerts、scheduler_errors、history、system_info。results 按五个模块的执行顺序排列。

| execution.outcome | 含义 |
|---|---|
| completed | 合法 OK/0、WARNING/1 或 CRITICAL/2；非零健康状态不是执行失败 |
| module_error | 模块合法返回 ERROR/3，例如 Nginx 日志不可读 |
| execution_failed | 无法启动、被信号终止或退出码超出 0～3 约定 |
| timeout | 超过该模块超时时间，清理进程组后继续 |
| invalid_json | 空输出、非法 JSON、多个 JSON 文档、重复键或非有限 JSON 常量 |
| invalid_output | JSON 可解析，但缺字段、类型错误、模块名不匹配、时间戳无时区、指标结构不合法或状态/退出码不一致 |

输出必须是一个 JSON 对象。正常优先读取 stdout；为兼容阶段 1，stdout 为空且退出码为 3 时允许读取 stderr 上的完整 ERROR JSON。两条原始输出流始终分别保留，不将其混合后猜测解析。

整体取最严重状态：**ERROR > CRITICAL > WARNING > OK**；状态与退出码固定为 3、2、1、0。调度、锁、文本日志或历史保存失败也使整体 ERROR。解析或契约失败会生成明确 ERROR 结果，原始信息仍可在 execution 中排查。

### 统一 JSON 示例（真实结果摘录）

以下是完整报告的字段投影，不是完整历史记录；全部模块、原始输出和完整告警见 [真实集成报告](../logs/stage2-integration-5b449fee-6e65-4e89-8370-6d04e44ef626.json)。

```json
{
  "module": "system",
  "run_id": "df2711a0-a2b9-44eb-8c60-0dd1761bb2df",
  "started_at": "2026-11-13T19:40:10.034+08:00",
  "finished_at": "2026-11-13T19:40:11.227+08:00",
  "duration_ms": 1192.661,
  "status": "ERROR",
  "exit_code": 3,
  "message": "5 modules checked: 4 OK, 0 WARNING, 0 CRITICAL, 1 ERROR; 0 scheduler errors",
  "history": {
    "path": "/home/aiops/mini-aiops/logs/monitor_history.jsonl",
    "status": "written"
  }
}
```

## 5. 历史与告警

[历史文件](../logs/monitor_history.jsonl) 为 UTF-8 JSON Lines：一次完成的调度运行追加一行**完整**报告；字符串内换行由 JSON 编码为转义字符。行内包含唯一 run_id、全部 results 和 alerts。真实集成已验证：新增恰好一行，解析后与 stdout 完整报告完全相同，既有字节保留。

读取历史示例：

```python
import json

with open("logs/monitor_history.jsonl", encoding="utf-8") as stream:
    for line in stream:
        report = json.loads(line)
        print(report["run_id"], report["status"], len(report["results"]))
```

本次真实记录读取结果示例：

```text
df2711a0-a2b9-44eb-8c60-0dd1761bb2df ERROR 5
```

- 追加前持有历史文件锁，完整写入后 fsync。可捕获的部分写入失败会在锁内回滚至原长度；已有末尾缺少换行时拒绝追加，防止把两条记录拼坏。
- 保存失败：history.status=failed，scheduler_errors 和 alerts 写明原因，整体 ERROR/3；控制台明确报错，并尽力追加本地文本日志。不声称失败的记录已保存。
- WARNING、CRITICAL、ERROR 均生成 alerts：模块、等级、消息、outcome 和最多四项关键指标。告警写 stderr、文本日志及完整历史报告；JSON stdout 不混入告警行。
- 没有外部通知、数据库、历史可视化或自动恢复功能。

## 6. 自动化测试

命令：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_run_monitor.py' -v
```

**最终结果：Ran 30 tests in 2.766s，OK。** 使用 TemporaryDirectory、Python 模拟子进程及必要 Mock，不依赖真实 Nginx 日志权限、root 或真实服务状态。uptime 仅用于兼容旧入口。

| 编号 | 测试场景 |
|---|---|
| 01～05 | 五模块正常；单 WARNING；单 CRITICAL；单 ERROR；多异常与最严重状态聚合 |
| 06～09 | 启动失败继续执行；约定外退出码及 stderr；真实超时清理进程组后继续；无效 JSON |
| 10～15 | 缺字段；status/exit_code 不符；进程退出码不符；模块名错误；无效或无时区时间戳；指标结构和有限数值 |
| 16～17 | stderr 上 ERROR JSON 的兼容；诊断 stderr 与 JSON stdout 分离 |
| 18～19 | 完整一行历史与返回报告相等；连续运行追加且 run_id 唯一 |
| 20 | 另一真实进程持锁时，竞争调用不执行模块、不污染历史 |
| 21～24 | 历史写失败；部分写入回滚；残缺尾行不覆盖；失败后锁释放 |
| 25～26 | 四种状态的 CLI JSON、告警分流及整体退出码；五模块参数与 Shell 入口参数/退出码透传 |
| 27～28 | 模拟 Nginx 失败/引号转义及 WARNING/CRITICAL/OK 阈值；重复 JSON 键与多个文档拒绝 |
| 29～30 | 文本日志失败不能伪装成功；中断记录及运行锁释放 |

另外：Shell 语法检查 **6/6 通过**；五个核心模块及阈值配置的 SHA-256 校验 **6/6 一致**。测试后仅更新说明文档，未继续修改可执行代码。

**不能写成全项目测试全绿：** 接入准备时复跑现有磁盘单元测试得到 11 项中 7 通过、3 错误、1 失败：旧测试未传 determine_status 新增 thresholds 参数，仍断言旧顶层 total 字段。此次未改磁盘代码或旧测试；这是已确认的阶段 1 遗留问题，不计入阶段 2 的 30 项结果。

## 7. Linux 真实集成测试

执行用户 aiops，从 `/` 调用绝对路径入口并捕获独立 stdout/stderr；这是一次真实五模块执行，不是模拟结果。

| 模块 | status / exit_code | outcome | duration_ms |
|---|---|---|---:|
| CPU | OK / 0 | completed | 1026.618 |
| 内存 | OK / 0 | completed | 27.131 |
| 磁盘 | OK / 0 | completed | 40.801 |
| 服务 | OK / 0 | completed | 54.858 |
| Nginx | ERROR / 3 | module_error | 37.155 |

整体：**ERROR/3**，duration_ms=1192.661，run_id=df2711a0-a2b9-44eb-8c60-0dd1761bb2df。CPU 使用率 0.00%，负载 0.07/0.04/0.01；内存使用率 21.07%，Swap 0.00%。uptime 保留。

Nginx 原因：`reason=log_file_unavailable path=/var/log/nginx/access.log`。stderr 有对应 ALERT ERROR，metrics 为空，不把读取失败伪装成零错误请求。未实施任何日志权限授权。

历史验证：原文件字节不被覆盖、新增 1 行、json.loads 成功、内容等于 stdout 报告、history.status=written。整体进程退出码与报告一致。

注意：上述时间戳来自虚拟机（2026-11-13），与会话现实日期（2026-11-14）不一致；本阶段没有校时。duration_ms 使用单调时钟，不受这个日期偏差影响。

## 8. 兼容性与已知风险

1. 原命令 `bash run_aiops.sh` 仍可用，默认文本输出，支持任意调用目录；旧文本日志路径及 uptime 信息保留。输出排版改为统一摘要，不承诺旧日志文本的逐字兼容。
2. 新增 `--json`、`--timeout`、`--history`，模块原有直接调用方式不变。整体退出码现在真实反映最严重状态，不再继承旧 tee 管道遮蔽错误的行为。
3. 运行锁仅协调本项目统一调度器，不保护手动并发直接执行旧模块。Nginx offset 非原子写、轮转旧文件未读尾部、copytruncate 快速回长、半行和写失败问题仍是核心扫描器风险，不能宣称 exactly-once；此次核心算法保持不变。
4. 旧 Nginx 扫描器仅在发现错误请求时输出 scanned_bytes；成功且无错误时包装器沿用缺省 0，不能据此判断实际没有扫描。后续应在独立模块修复中补明确扫描元数据，此次不凭猜测推导。
5. CPU/内存阈值整数舍入及 CPU 字段计数等阶段 1 算法边界未在此次修正；调度器不重新计算指标或覆盖模块状态。旧磁盘测试回归问题见上。
6. JSONL 历史未做容量上限或轮转；完整原始 stdout/stderr 会增加日志体积。当前受信任的小模块使用内存捕获输出，不是任意不受信任程序的资源沙箱。
7. 对可捕获写入异常有回滚保护；掉电、SIGKILL、底层文件系统损坏不能保证完成历史提交。SIGINT/SIGTERM 在模块等待阶段可清理进程并保存已完成结果；提交阶段遭中断不能承诺总有完整报告。锁被拒绝的竞争启动不是完成的检查，不追加历史。
8. 使用 Linux fcntl.flock，面向现有 Linux 本地文件系统，不承诺 Windows 或不支持该锁语义的网络文件系统。Nginx 读取权限需独立授权后处理；本阶段没有修改系统权限。
9. finished_at/duration_ms 表示报告封装完成的时刻，成功时不包含随后的历史 fsync 和终端输出耗时。

## 9. 用法

```bash
bash run_aiops.sh
bash run_aiops.sh --json
bash run_aiops.sh --json --timeout 10
bash run_aiops.sh --json --history logs/other_history.jsonl
```

相对历史路径以项目根目录解析。JSON 模式可分别重定向 stdout 报告和 stderr 告警；不要用管道最后一个命令的退出码代替调度器的真实退出码。

## 10. Token 与费用

| 字段 | 实测值 |
|---|---|
| 普通输入 Token | 未知：本次工具未提供 API 请求用量 |
| 缓存创建 Token | 未知 |
| 缓存读取 Token | 未知 |
| 输出 Token | 未知 |
| 实际费用 | 未知 |

未将估算值作为实测，也未为获取宿主机 DSH 计费日志而扩展扫描范围。此次没有子代理或大规模扫描。前期并行读取发生空参数错误后改用定向单文件读取；未继续重复失败调用。自动化首次 28 项通过后因补充兼容性与错误处理进行最终 30 项回归，真实集成仅执行一次。
