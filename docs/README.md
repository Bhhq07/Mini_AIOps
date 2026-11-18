# Mini AIOps 智能运维平台

从零开始构建的轻量级智能运维平台，通过 Shell、Python、Linux 和 AI 技术实现服务器监控、异常检测和自动化处理。

## 阶段 2 当前运行方式

现在的 [统一入口](../run_aiops.sh) 使用 [Python 调度器](../python/run_monitor.py)，依次执行 CPU、内存、磁盘、服务、Nginx 五个现有模块；保留原有 uptime 信息和文本日志。

```bash
# 在项目目录执行；从其他目录调用绝对路径也可
bash run_aiops.sh
bash run_aiops.sh --json
bash run_aiops.sh --json --timeout 10

# 阶段 2 自动化测试，不依赖真实服务或 Nginx 日志权限
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_run_monitor.py' -v
```

- 不带参数保留文本模式；`--json` 标准输出只包含一份完整 JSON，告警写入标准错误。
- 退出码：OK=0、WARNING=1、CRITICAL=2、ERROR=3；单模块失败仍继续其他模块。
- 每模块默认超时 10 秒，超时清理子进程组；项目级运行锁拒绝重复启动。
- [历史记录](../logs/monitor_history.jsonl) 每次完成追加一条 JSONL；[文本日志](../logs/run_aiops.log) 保留摘要与告警。`--history` 可指定其他路径，相对路径以项目根目录解析。
- Nginx 日志读取失败会如实报告 ERROR，不跳过、不提权。
- 本次验收、输出示例、测试清单和限制见 [阶段 2 报告](STAGE2_REPORT.md)。阶段 2 测试 30/30 通过，不代表旧磁盘测试已修复。

## 阶段 0 归档说明

以下结构和完成情况保留为历史记录；当前接口和运行方式以上方说明及阶段 2 报告为准。

## 项目结构

```
mini-aiops/
├── shell/              # Shell 监控模块
│   ├── check_cpu.sh           # CPU 使用率和负载监控
│   ├── check_nginx.sh         # Nginx 错误日志监控（增量扫描）
│   ├── check_service.sh       # 单服务状态检查
│   └── check_services.sh      # 多服务批量检查
├── python/             # Python 监控模块
│   ├── check_disk.py          # 磁盘使用率监控
│   └── test_check_disk.py     # 磁盘模块单元测试
├── tests/              # 自动化测试
├── logs/               # 项目运行日志
├── state/              # 持久化运行状态
├── docs/               # 项目文档
├── run_aiops.sh        # 统一监控入口
└── README.md           # 项目说明（本文件）
```

## 环境要求

- **操作系统**：Linux（CentOS/RHEL 8+）
- **Python**：3.9+
- **Shell**：Bash 4.0+
- **权限**：普通用户 aiops（部分监控需要特定文件读取权限）

## 快速开始

### 1. 单模块测试

```bash
# CPU 监控
bash shell/check_cpu.sh

# 服务监控
bash shell/check_services.sh nginx sshd

# 磁盘监控
python3 python/check_disk.py /
python3 python/check_disk.py / --json

# 磁盘模块单元测试
cd python && python3 -m unittest test_check_disk -v
```

### 2. 统一入口执行

```bash
# 从项目根目录执行
bash run_aiops.sh
```

## 已实现功能

### Shell 监控模块

| 模块 | 功能 | 状态 |
|------|------|------|
| check_cpu.sh | CPU 使用率 + 系统负载 | ✅ 已完成 |
| check_services.sh | 多服务状态监控 + 状态跃迁跟踪 | ✅ 已完成 |
| check_nginx.sh | Nginx 错误日志增量扫描（4xx/5xx） | ✅ 已修复 |
| check_service.sh | 单服务状态检查 | ✅ 已完成 |

### Python 监控模块

| 模块 | 功能 | 状态 |
|------|------|------|
| check_disk.py | 磁盘使用率监控（文本/JSON 双输出） | ✅ 已完成 |
| test_check_disk.py | 磁盘模块单元测试（11 个测试用例） | ✅ 全部通过 |

## 阶段 0 完成内容

### 项目重组

- ✅ 规范化目录结构（shell/、python/、logs/、state/、docs/）
- ✅ 删除调试残留文件（test.sh）
- ✅ Python 模块从家目录迁入项目
- ✅ 创建完整备份（mini-aiops-backup-2026-11-13_143646）

### 核心问题修复

1. **check_nginx.sh**
   - ✅ 修复 offset 回写缺失（原脚本从不更新状态文件）
   - ✅ 实现 inode 轮转检测（日志 rotate 时自动重置 offset）
   - ✅ 实现截断检测（size < offset 时重置）
   - ✅ 优化输出路径（使用项目内 logs/ 目录）
   - ✅ 无新日志时正确更新状态文件

2. **check_services.sh**
   - ✅ 状态文件从 `/tmp` 迁移到项目 `state/` 目录（防止重启丢失）
   - ✅ 修复日志路径（从 `/var/log` 改为项目 `logs/`）
   - ✅ 添加自动创建目录逻辑
   - ✅ 改进日志格式（添加 module 字段）

3. **run_aiops.sh**
   - ✅ 修复硬编码的 `/root/` 路径
   - ✅ 使用相对路径调用各模块（支持从任意位置执行）
   - ✅ 集成 CPU、服务、磁盘三类监控
   - ✅ 改进日志输出（同时写文件和终端）

## 已知限制与下一步计划

### 权限限制

- ❌ `/var/log/nginx/access.log` 对 aiops 用户不可读（nginx:root 640）
- ❌ check_nginx.sh 当前无法以 aiops 身份运行
- 📋 需要最小权限方案：ACL 或 nginx 组成员资格

### 待统一规范

- 📋 输出格式尚未统一（Shell 模块输出与 Python JSON 输出格式不同）
- 📋 时间戳格式三套（Shell `%F %T`、Nginx 原始、Python 无时间字段）
- 📋 状态码语义不一致（磁盘模块有 OK/WARNING/CRITICAL/ERROR，Shell 模块只有退出码）

### 测试覆盖缺口

- 📋 Shell 模块尚无自动化测试
- 📋 check_nginx.sh 轮转/截断/并发场景需要集成测试
- 📋 check_disk.py 部分异常分支未覆盖

### 下一阶段（阶段 1）

- [ ] 统一输出契约（JSON 字段、status 优先级、退出码）
- [ ] 补齐内存监控模块
- [ ] 完善 CPU 模块（添加 status 和 JSON 输出）
- [ ] 为所有模块编写测试
- [ ] Nginx 权限方案落地

## 备份与恢复

当前备份位置：`/home/aiops/mini-aiops-backup-2026-11-13_143646/`

```bash
# 查看备份内容
ls -la ../mini-aiops-backup-2026-11-13_143646/

# 恢复单个文件（示例）
cp ../mini-aiops-backup-2026-11-13_143646/original-mini-aiops/check_nginx.sh shell/
```

## 开发规范

1. **修改前备份**：重要修改前创建带时间戳的备份
2. **测试驱动**：新功能必须配套测试用例
3. **路径规范**：使用相对路径，通过 `$SCRIPT_DIR` 定位资源
4. **退出码**：成功 0，告警 1，严重 2，错误 3
5. **日志格式**：`时间戳 级别 module=模块名 字段=值`

## 许可证

个人学习项目，用于 AIOps 岗位求职实践。
