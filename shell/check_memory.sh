#!/bin/bash

# Mini AIOps - 内存监控模块
# 功能：监控物理内存和 Swap 使用情况

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/../config/thresholds.conf"

# 加载配置（提供默认值）
MEMORY_USAGE_WARNING=80
MEMORY_USAGE_CRITICAL=90
SWAP_USAGE_WARNING=50
SWAP_USAGE_CRITICAL=80

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

# 读取 /proc/meminfo
if [ ! -r /proc/meminfo ]; then
    if [ "$OUTPUT_MODE" = "json" ]; then
        echo '{"module":"memory","timestamp":"'$(date -Iseconds)'","status":"ERROR","message":"无法读取 /proc/meminfo","metrics":{},"exit_code":3}' >&2
    else
        echo "$(date -Iseconds) ERROR module=memory reason=cannot_read_meminfo" >&2
    fi
    exit 3
fi

# 解析内存信息（单位：kB）
MEM_TOTAL=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
MEM_AVAILABLE=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
SWAP_TOTAL=$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo)
SWAP_FREE=$(awk '/^SwapFree:/ {print $2}' /proc/meminfo)

# 验证数据有效性
if [ -z "$MEM_TOTAL" ] || [ -z "$MEM_AVAILABLE" ]; then
    if [ "$OUTPUT_MODE" = "json" ]; then
        echo '{"module":"memory","timestamp":"'$(date -Iseconds)'","status":"ERROR","message":"内存数据解析失败","metrics":{},"exit_code":3}' >&2
    else
        echo "$(date -Iseconds) ERROR module=memory reason=parse_failed" >&2
    fi
    exit 3
fi

# 转换为字节
MEM_TOTAL_BYTES=$((MEM_TOTAL * 1024))
MEM_AVAILABLE_BYTES=$((MEM_AVAILABLE * 1024))
MEM_USED_BYTES=$((MEM_TOTAL_BYTES - MEM_AVAILABLE_BYTES))

# 计算内存使用率
if [ "$MEM_TOTAL" -gt 0 ]; then
    MEM_USAGE_PERCENT=$(awk -v used="$MEM_USED_BYTES" -v total="$MEM_TOTAL_BYTES" \
        'BEGIN {printf "%.2f", (used / total) * 100}')
else
    MEM_USAGE_PERCENT="0.00"
fi

# 处理 Swap（可能不存在）
SWAP_TOTAL_BYTES=$((SWAP_TOTAL * 1024))
SWAP_FREE_BYTES=$((SWAP_FREE * 1024))
SWAP_USED_BYTES=$((SWAP_TOTAL_BYTES - SWAP_FREE_BYTES))

if [ "$SWAP_TOTAL" -gt 0 ]; then
    SWAP_USAGE_PERCENT=$(awk -v used="$SWAP_USED_BYTES" -v total="$SWAP_TOTAL_BYTES" \
        'BEGIN {printf "%.2f", (used / total) * 100}')
    HAS_SWAP=true
else
    SWAP_USAGE_PERCENT="0.00"
    HAS_SWAP=false
fi

# 判断状态
STATUS="OK"
EXIT_CODE=0
MESSAGE="内存运行正常"

MEM_USAGE_INT=$(printf "%.0f" "$MEM_USAGE_PERCENT")
SWAP_USAGE_INT=$(printf "%.0f" "$SWAP_USAGE_PERCENT")

# 检查内存使用率
if [ "$MEM_USAGE_INT" -ge "$MEMORY_USAGE_CRITICAL" ]; then
    STATUS="CRITICAL"
    EXIT_CODE=2
    MESSAGE="物理内存严重不足: ${MEM_USAGE_PERCENT}%"
elif [ "$MEM_USAGE_INT" -ge "$MEMORY_USAGE_WARNING" ]; then
    STATUS="WARNING"
    EXIT_CODE=1
    MESSAGE="物理内存偏高: ${MEM_USAGE_PERCENT}%"
fi

# 检查 Swap 使用率（如果存在）
if [ "$HAS_SWAP" = true ]; then
    if [ "$SWAP_USAGE_INT" -ge "$SWAP_USAGE_CRITICAL" ]; then
        if [ "$STATUS" != "CRITICAL" ]; then
            STATUS="CRITICAL"
            EXIT_CODE=2
        fi
        MESSAGE="${MESSAGE}, Swap 严重不足: ${SWAP_USAGE_PERCENT}%"
    elif [ "$SWAP_USAGE_INT" -ge "$SWAP_USAGE_WARNING" ]; then
        if [ "$STATUS" = "OK" ]; then
            STATUS="WARNING"
            EXIT_CODE=1
            MESSAGE="Swap 使用率偏高: ${SWAP_USAGE_PERCENT}%"
        else
            MESSAGE="${MESSAGE}, Swap 偏高: ${SWAP_USAGE_PERCENT}%"
        fi
    fi
fi

# 输出结果
TIMESTAMP=$(date -Iseconds)

if [ "$OUTPUT_MODE" = "json" ]; then
    # 构建 JSON（根据是否有 Swap 动态生成）
    if [ "$HAS_SWAP" = true ]; then
        SWAP_METRICS=",
    \"swap_total\": {
      \"value\": $SWAP_TOTAL_BYTES,
      \"unit\": \"bytes\"
    },
    \"swap_used\": {
      \"value\": $SWAP_USED_BYTES,
      \"unit\": \"bytes\"
    },
    \"swap_free\": {
      \"value\": $SWAP_FREE_BYTES,
      \"unit\": \"bytes\"
    },
    \"swap_usage_percent\": {
      \"value\": $SWAP_USAGE_PERCENT,
      \"unit\": \"percent\",
      \"threshold_warning\": $SWAP_USAGE_WARNING,
      \"threshold_critical\": $SWAP_USAGE_CRITICAL
    }"
    else
        SWAP_METRICS=""
    fi

    cat <<EOF
{
  "module": "memory",
  "timestamp": "$TIMESTAMP",
  "status": "$STATUS",
  "message": "$MESSAGE",
  "metrics": {
    "memory_total": {
      "value": $MEM_TOTAL_BYTES,
      "unit": "bytes"
    },
    "memory_used": {
      "value": $MEM_USED_BYTES,
      "unit": "bytes"
    },
    "memory_available": {
      "value": $MEM_AVAILABLE_BYTES,
      "unit": "bytes"
    },
    "memory_usage_percent": {
      "value": $MEM_USAGE_PERCENT,
      "unit": "percent",
      "threshold_warning": $MEMORY_USAGE_WARNING,
      "threshold_critical": $MEMORY_USAGE_CRITICAL
    }$SWAP_METRICS
  },
  "exit_code": $EXIT_CODE
}
EOF
else
    # 格式化输出（字节转 GB/MB）
    MEM_TOTAL_GB=$(awk -v bytes="$MEM_TOTAL_BYTES" 'BEGIN {printf "%.2f", bytes / (1024^3)}')
    MEM_USED_GB=$(awk -v bytes="$MEM_USED_BYTES" 'BEGIN {printf "%.2f", bytes / (1024^3)}')
    MEM_AVAILABLE_GB=$(awk -v bytes="$MEM_AVAILABLE_BYTES" 'BEGIN {printf "%.2f", bytes / (1024^3)}')

    echo "[$TIMESTAMP] 内存监控"
    echo "状态: $STATUS"
    echo "物理内存总量: ${MEM_TOTAL_GB} GB"
    echo "已使用: ${MEM_USED_GB} GB"
    echo "可用: ${MEM_AVAILABLE_GB} GB"
    echo "使用率: ${MEM_USAGE_PERCENT}% (阈值: ${MEMORY_USAGE_WARNING}%/${MEMORY_USAGE_CRITICAL}%)"

    if [ "$HAS_SWAP" = true ]; then
        SWAP_TOTAL_GB=$(awk -v bytes="$SWAP_TOTAL_BYTES" 'BEGIN {printf "%.2f", bytes / (1024^3)}')
        SWAP_USED_GB=$(awk -v bytes="$SWAP_USED_BYTES" 'BEGIN {printf "%.2f", bytes / (1024^3)}')
        SWAP_FREE_GB=$(awk -v bytes="$SWAP_FREE_BYTES" 'BEGIN {printf "%.2f", bytes / (1024^3)}')
        
        echo "Swap 总量: ${SWAP_TOTAL_GB} GB"
        echo "Swap 已使用: ${SWAP_USED_GB} GB"
        echo "Swap 空闲: ${SWAP_FREE_GB} GB"
        echo "Swap 使用率: ${SWAP_USAGE_PERCENT}% (阈值: ${SWAP_USAGE_WARNING}%/${SWAP_USAGE_CRITICAL}%)"
    else
        echo "Swap: 未配置"
    fi
fi

exit $EXIT_CODE
