#!/bin/bash

# Mini AIOps - 服务监控模块
# 功能：批量检查服务状态，跟踪 UP/DOWN 状态变化

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${SCRIPT_DIR}/../state"
LOG_FILE="${SCRIPT_DIR}/../logs/check_services.log"

# 确保状态和日志目录存在
mkdir -p "$STATE_DIR" "$(dirname "$LOG_FILE")"

# 解析命令行参数
OUTPUT_MODE="text"
SERVICES=()

while [[ $# -gt 0 ]]; do
    case $1 in
        --json)
            OUTPUT_MODE="json"
            shift
            ;;
        --help)
            echo "用法: $0 [--json] <service1> <service2> ..."
            echo "选项:"
            echo "  --json    以 JSON 格式输出"
            exit 0
            ;;
        *)
            SERVICES+=("$1")
            shift
            ;;
    esac
done

# 检查是否提供服务名
if [ ${#SERVICES[@]} -eq 0 ]; then
    if [ "$OUTPUT_MODE" = "json" ]; then
        echo '{"module":"service","timestamp":"'$(date -Iseconds)'","status":"ERROR","message":"请输入需要检查的服务名","metrics":{},"exit_code":3}' >&2
    else
        echo "请输入需要检查的服务名" >&2
    fi
    exit 3
fi

# 初始化
HAS_ERROR=0
METRICS_JSON=""
SERVICE_RESULTS=()
TIMESTAMP=$(date -Iseconds)

# 检查每个服务
for SERVICE_NAME in "${SERVICES[@]}"; do
    STATE_FILE="${STATE_DIR}/service_${SERVICE_NAME}.state"
    
    # 检查服务是否存在
    if ! systemctl cat "${SERVICE_NAME}.service" >/dev/null 2>&1; then
        if [ "$OUTPUT_MODE" = "text" ]; then
            echo "${SERVICE_NAME} 该服务不存在"
        fi
        SERVICE_RESULTS+=("${SERVICE_NAME}:not_found")
        HAS_ERROR=1
        continue
    fi
    
    # 读取或初始化状态
    if [ -f "$STATE_FILE" ]; then
        SAVED_STATE=$(cat "$STATE_FILE")
    else
        echo "UP" > "$STATE_FILE"
        SAVED_STATE="UP"
    fi
    
    # 检查服务当前状态
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        CURRENT_STATE="active"
        if [ "$OUTPUT_MODE" = "text" ]; then
            echo "$SERVICE_NAME 正在运行"
        fi
        
        # 状态恢复
        if [ "$SAVED_STATE" = "DOWN" ]; then
            echo "$TIMESTAMP INFO module=service_monitor service=$SERVICE_NAME event=RECOVERED" >> "$LOG_FILE"
            echo "UP" > "$STATE_FILE"
        fi
        
        SERVICE_RESULTS+=("${SERVICE_NAME}:active")
    else
        CURRENT_STATE="inactive"
        if [ "$OUTPUT_MODE" = "text" ]; then
            echo "$SERVICE_NAME 未运行"
        fi
        HAS_ERROR=1
        
        # 状态变化
        if [ "$SAVED_STATE" = "UP" ]; then
            echo "$TIMESTAMP ERROR module=service_monitor service=$SERVICE_NAME event=DOWN" >> "$LOG_FILE"
            echo "DOWN" > "$STATE_FILE"
        fi
        
        SERVICE_RESULTS+=("${SERVICE_NAME}:inactive")
    fi
done

# 判断总体状态
if [ "$HAS_ERROR" -eq 1 ]; then
    STATUS="CRITICAL"
    EXIT_CODE=2
    MESSAGE="部分服务未运行"
else
    STATUS="OK"
    EXIT_CODE=0
    MESSAGE="所有服务运行正常"
fi

# 输出结果
if [ "$OUTPUT_MODE" = "json" ]; then
    # 构建 metrics JSON
    METRICS_JSON="{"
    FIRST=true
    for result in "${SERVICE_RESULTS[@]}"; do
        SERVICE_NAME="${result%%:*}"
        SERVICE_STATE="${result##*:}"
        
        if [ "$FIRST" = true ]; then
            FIRST=false
        else
            METRICS_JSON+=","
        fi
        
        METRICS_JSON+="
    \"service_${SERVICE_NAME}\": {
      \"value\": \"${SERVICE_STATE}\",
      \"unit\": \"state\"
    }"
    done
    METRICS_JSON+="
  }"
    
    cat <<EOF
{
  "module": "service",
  "timestamp": "$TIMESTAMP",
  "status": "$STATUS",
  "message": "$MESSAGE",
  "metrics": ${METRICS_JSON},
  "exit_code": $EXIT_CODE
}
EOF
else
    echo "[$TIMESTAMP] 服务监控"
    echo "状态: $STATUS"
    echo "消息: $MESSAGE"
fi

exit $EXIT_CODE
