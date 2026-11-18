#!/bin/bash

# Mini AIOps - Nginx 日志监控模块
# 功能：增量扫描 access.log，检测 4xx/5xx 错误请求
# 状态文件格式：<dev:inode> <size>

LOG_FILE="/var/log/nginx/access.log"
STATE_FILE="../state/nginx_offset.state"
OUTPUT_FILE="../logs/check_nginx.log"

# 获取脚本所在目录（支持相对路径调用）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="${SCRIPT_DIR}/${STATE_FILE}"
OUTPUT_FILE="${SCRIPT_DIR}/${OUTPUT_FILE}"

# 确保状态和日志目录存在
mkdir -p "$(dirname "$STATE_FILE")" "$(dirname "$OUTPUT_FILE")"

# 检查日志文件可读性
if [ ! -f "$LOG_FILE" ] || [ ! -r "$LOG_FILE" ]; then
    echo "$(date '+%F %T') ERROR module=nginx_log reason=log_file_unavailable path=$LOG_FILE" | tee -a "$OUTPUT_FILE"
    exit 1
fi

# 检查 Nginx 服务状态
if ! systemctl is-active --quiet nginx; then
    echo "$(date '+%F %T') ERROR module=nginx_log reason=nginx_not_running" | tee -a "$OUTPUT_FILE"
    exit 1
fi

# 获取当前日志文件信息
CURRENT_FILE_INFO=$(stat -c '%d:%i %s' "$LOG_FILE")
CURRENT_DEV_INODE=$(echo "$CURRENT_FILE_INFO" | awk '{print $1}')
CURRENT_SIZE=$(echo "$CURRENT_FILE_INFO" | awk '{print $2}')

# 初始化或读取状态文件
if [ ! -f "$STATE_FILE" ]; then
    echo "$CURRENT_DEV_INODE 0" > "$STATE_FILE"
    SAVED_DEV_INODE="$CURRENT_DEV_INODE"
    SAVED_OFFSET=0
else
    if [ ! -r "$STATE_FILE" ]; then
        echo "$(date '+%F %T') ERROR module=nginx_log reason=state_file_unreadable path=$STATE_FILE" | tee -a "$OUTPUT_FILE"
        exit 1
    fi
    read SAVED_DEV_INODE SAVED_OFFSET < "$STATE_FILE"
fi

# 验证偏移量格式
if [[ ! "$SAVED_OFFSET" =~ ^[0-9]+$ ]]; then
    echo "$(date '+%F %T') ERROR module=nginx_log reason=invalid_offset value=$SAVED_OFFSET" | tee -a "$OUTPUT_FILE"
    exit 1
fi

# 检测日志轮转（inode 变化）
if [ "$CURRENT_DEV_INODE" != "$SAVED_DEV_INODE" ]; then
    echo "$(date '+%F %T') INFO module=nginx_log event=log_rotated old_inode=$SAVED_DEV_INODE new_inode=$CURRENT_DEV_INODE" | tee -a "$OUTPUT_FILE"
    SAVED_OFFSET=0
fi

# 检测日志截断（size < offset）
if [ "$CURRENT_SIZE" -lt "$SAVED_OFFSET" ]; then
    echo "$(date '+%F %T') INFO module=nginx_log event=log_truncated old_offset=$SAVED_OFFSET new_size=$CURRENT_SIZE" | tee -a "$OUTPUT_FILE"
    SAVED_OFFSET=0
fi

# 计算需要扫描的字节数
NEW_BYTES=$((CURRENT_SIZE - SAVED_OFFSET))

if [ "$NEW_BYTES" -le 0 ]; then
    # 无新日志，只更新状态文件（保持 inode 同步）
    echo "$CURRENT_DEV_INODE $CURRENT_SIZE" > "$STATE_FILE"
    exit 0
fi

# 增量扫描并解析错误日志
ERROR_COUNT=$(tail -c +"$((SAVED_OFFSET + 1))" "$LOG_FILE" | head -c "$NEW_BYTES" | awk -v output="$OUTPUT_FILE" '
    / [4-5][0-9][0-9] / {
        timestamp = substr($0, index($0, "[") + 1, 26)
        
        split($0, part, "\"")
        split(part[2], req, " ")
        split(part[3], code, " ")
        
        result = timestamp " ERROR module=nginx_log method=" req[1] \
                 " path=" req[2] " status=" code[1]
        
        print result >> output
        error_count++
    }
    END {
        print error_count + 0
    }
')

# 回写新状态（关键修复：更新 offset）
echo "$CURRENT_DEV_INODE $CURRENT_SIZE" > "$STATE_FILE"

# 输出扫描结果
if [ "$ERROR_COUNT" -gt 0 ]; then
    echo "$(date '+%F %T') INFO module=nginx_log scanned_bytes=$NEW_BYTES error_count=$ERROR_COUNT" | tee -a "$OUTPUT_FILE"
    exit 0
else
    exit 0
fi
