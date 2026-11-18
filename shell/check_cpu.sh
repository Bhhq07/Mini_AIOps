#!/bin/bash

# Mini AIOps - CPU 监控模块
# 功能：监控 CPU 使用率和系统负载

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/../config/thresholds.conf"

# 加载配置（提供默认值）
CPU_USAGE_WARNING=80
CPU_USAGE_CRITICAL=90

if [ -f "$CONFIG_FILE" ]; then
    source "$CONFIG_FILE"
fi

# 解析命令行参数
OUTPUT_MODE="text"
while [[ $# -gt 0 ]]; do
    case $1 in
        --json)
            OUTPUT_MODE="json"
            shift
            ;;
        --help)
            echo "用法: $0 [--json]"
            echo "选项:"
            echo "  --json    以 JSON 格式输出"
            exit 0
            ;;
        *)
            echo "未知选项: $1" >&2
            exit 3
            ;;
    esac
done

# 读取 CPU 统计信息
read_cpu_stat() {
    awk '/^cpu / {
        total = 0
        for (i = 2; i <= NF; i++) {
            total += $i
        }
        idle = $5 + $6
        print total, idle
    }' /proc/stat
}

# 读取系统负载
read_load_average() {
    awk '{print $1, $2, $3}' /proc/loadavg
}

# 获取 CPU 核心数
CPU_CORES=$(nproc 2>/dev/null || grep -c ^processor /proc/cpuinfo)

# 第一次采样
read TOTAL1 IDLE1 <<< "$(read_cpu_stat)"
if [ -z "$TOTAL1" ] || [ -z "$IDLE1" ]; then
    if [ "$OUTPUT_MODE" = "json" ]; then
        echo '{"module":"cpu","timestamp":"'$(date -Iseconds)'","status":"ERROR","message":"无法读取 /proc/stat","metrics":{},"exit_code":3}' >&2
    else
        echo "$(date -Iseconds) ERROR module=cpu reason=cannot_read_proc_stat" >&2
    fi
    exit 3
fi

# 等待 1 秒
sleep 1

# 第二次采样
read TOTAL2 IDLE2 <<< "$(read_cpu_stat)"
if [ -z "$TOTAL2" ] || [ -z "$IDLE2" ]; then
    if [ "$OUTPUT_MODE" = "json" ]; then
        echo '{"module":"cpu","timestamp":"'$(date -Iseconds)'","status":"ERROR","message":"无法读取 /proc/stat","metrics":{},"exit_code":3}' >&2
    else
        echo "$(date -Iseconds) ERROR module=cpu reason=cannot_read_proc_stat" >&2
    fi
    exit 3
fi

# 计算 CPU 使用率
TOTAL_DIFF=$((TOTAL2 - TOTAL1))
IDLE_DIFF=$((IDLE2 - IDLE1))

if [ "$TOTAL_DIFF" -gt 0 ]; then
    CPU_USAGE=$(awk -v total="$TOTAL_DIFF" -v idle="$IDLE_DIFF" \
        'BEGIN {printf "%.2f", (total - idle) / total * 100}')
else
    CPU_USAGE="0.00"
fi

# 读取负载
read LOAD_1M LOAD_5M LOAD_15M <<< "$(read_load_average)"

# 判断状态
STATUS="OK"
EXIT_CODE=0
MESSAGE="CPU 运行正常"

CPU_USAGE_INT=$(printf "%.0f" "$CPU_USAGE")
if [ "$CPU_USAGE_INT" -ge "$CPU_USAGE_CRITICAL" ]; then
    STATUS="CRITICAL"
    EXIT_CODE=2
    MESSAGE="CPU 使用率严重过高: ${CPU_USAGE}%"
elif [ "$CPU_USAGE_INT" -ge "$CPU_USAGE_WARNING" ]; then
    STATUS="WARNING"
    EXIT_CODE=1
    MESSAGE="CPU 使用率偏高: ${CPU_USAGE}%"
fi

# 输出结果
TIMESTAMP=$(date -Iseconds)

if [ "$OUTPUT_MODE" = "json" ]; then
    cat <<EOF
{
  "module": "cpu",
  "timestamp": "$TIMESTAMP",
  "status": "$STATUS",
  "message": "$MESSAGE",
  "metrics": {
    "cpu_usage_percent": {
      "value": $CPU_USAGE,
      "unit": "percent",
      "threshold_warning": $CPU_USAGE_WARNING,
      "threshold_critical": $CPU_USAGE_CRITICAL
    },
    "load_average_1m": {
      "value": $LOAD_1M,
      "unit": "load"
    },
    "load_average_5m": {
      "value": $LOAD_5M,
      "unit": "load"
    },
    "load_average_15m": {
      "value": $LOAD_15M,
      "unit": "load"
    },
    "cpu_cores": {
      "value": $CPU_CORES,
      "unit": "count"
    }
  },
  "exit_code": $EXIT_CODE
}
EOF
else
    echo "[$TIMESTAMP] CPU 监控"
    echo "状态: $STATUS"
    echo "CPU 使用率: ${CPU_USAGE}% (阈值: ${CPU_USAGE_WARNING}%/${CPU_USAGE_CRITICAL}%)"
    echo "1 分钟负载: $LOAD_1M"
    echo "5 分钟负载: $LOAD_5M"
    echo "15 分钟负载: $LOAD_15M"
    echo "CPU 核心数: $CPU_CORES"
fi

exit $EXIT_CODE
