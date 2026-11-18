#!/bin/bash

SERVICE=$1

check_services(){
        if systemctl is-active --quiet "$1"; then
		echo "$1""正在运行"
		return 0
	else
		echo "$1""未运行"
		return 1
	fi
}
if [ $# = 0 ]; then
        echo "请输入需要检查的服务名"
        exit 1
else
	check_services "$SERVICE"
fi
