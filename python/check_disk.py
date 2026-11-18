#!/usr/bin/env python3
"""
Mini AIOps - 磁盘监控模块
检查 Linux 文件系统的磁盘使用情况
"""

import os
import sys
import json
import argparse
import shutil
from datetime import datetime, timezone
from typing import Dict, Tuple
from pathlib import Path


class DiskStatus:
    """磁盘状态常量"""
    OK = 'OK'
    WARNING = 'WARNING'
    CRITICAL = 'CRITICAL'
    ERROR = 'ERROR'


class ExitCode:
    """退出码常量"""
    OK = 0
    WARNING = 1
    CRITICAL = 2
    ERROR = 3


def load_thresholds() -> Dict[str, int]:
    """从配置文件加载阈值"""
    script_dir = Path(__file__).parent
    config_file = script_dir.parent / 'config' / 'thresholds.conf'
    
    thresholds = {
        'warning': 80,
        'critical': 90
    }
    
    if config_file.exists():
        try:
            with open(config_file) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('DISK_USAGE_WARNING='):
                        thresholds['warning'] = int(line.split('=')[1])
                    elif line.startswith('DISK_USAGE_CRITICAL='):
                        thresholds['critical'] = int(line.split('=')[1])
        except Exception:
            pass  # 使用默认值
    
    return thresholds


def get_disk_usage(path: str) -> Dict[str, float]:
    """
    获取指定路径的磁盘使用信息
    
    Args:
        path: 文件系统路径
        
    Returns:
        包含 total, used, available, usage_percent 的字典
        
    Raises:
        FileNotFoundError: 路径不存在
        PermissionError: 无权限访问
        OSError: 其他系统错误
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"路径不存在: {path}")
    
    if not os.path.isdir(path):
        raise NotADirectoryError(f"不是有效目录: {path}")
    
    try:
        usage = shutil.disk_usage(path)
    except PermissionError:
        raise PermissionError(f"无权限访问: {path}")
    except OSError as e:
        raise OSError(f"获取磁盘信息失败: {e}")
    
    # 计算使用率，防止除零
    if usage.total == 0:
        usage_percent = 0.0
    else:
        usage_percent = (usage.used / usage.total) * 100
    
    return {
        'total': usage.total,
        'used': usage.used,
        'available': usage.free,
        'usage_percent': usage_percent
    }


def determine_status(usage_percent: float, thresholds: Dict[str, int]) -> Tuple[str, int]:
    """
    根据磁盘使用率判断状态
    
    Args:
        usage_percent: 使用率百分比
        thresholds: 阈值字典
        
    Returns:
        (状态字符串, 退出码)
    """
    if usage_percent < thresholds['warning']:
        return DiskStatus.OK, ExitCode.OK
    elif usage_percent < thresholds['critical']:
        return DiskStatus.WARNING, ExitCode.WARNING
    else:
        return DiskStatus.CRITICAL, ExitCode.CRITICAL


def format_size(bytes_value: float) -> str:
    """
    格式化字节数为人类可读的容量单位
    
    Args:
        bytes_value: 字节数
        
    Returns:
        格式化后的字符串，如 "1.50 GB"
    """
    gb = bytes_value / (1024 ** 3)
    mb = bytes_value / (1024 ** 2)
    
    if gb >= 1:
        return f"{gb:.2f} GB"
    else:
        return f"{mb:.2f} MB"


def output_json(path: str, usage: Dict[str, float], status: str, exit_code: int, thresholds: Dict[str, int]) -> None:
    """JSON 模式输出（统一契约格式）"""
    timestamp = datetime.now(timezone.utc).astimezone().isoformat()
    
    if status == DiskStatus.OK:
        message = f"磁盘空间充足: {usage['usage_percent']:.2f}%"
    elif status == DiskStatus.WARNING:
        message = f"磁盘空间偏高: {usage['usage_percent']:.2f}%"
    elif status == DiskStatus.CRITICAL:
        message = f"磁盘空间严重不足: {usage['usage_percent']:.2f}%"
    else:
        message = "磁盘监控错误"
    
    result = {
        'module': 'disk',
        'timestamp': timestamp,
        'status': status,
        'message': message,
        'metrics': {
            'disk_total': {
                'value': usage['total'],
                'unit': 'bytes'
            },
            'disk_used': {
                'value': usage['used'],
                'unit': 'bytes'
            },
            'disk_available': {
                'value': usage['available'],
                'unit': 'bytes'
            },
            'disk_usage_percent': {
                'value': round(usage['usage_percent'], 2),
                'unit': 'percent',
                'threshold_warning': thresholds['warning'],
                'threshold_critical': thresholds['critical']
            }
        },
        'exit_code': exit_code
    }
    
    # 添加路径信息（非标准字段，用于兼容）
    result['path'] = path
    
    print(json.dumps(result, ensure_ascii=False, indent=2))


def output_normal(path: str, usage: Dict[str, float], status: str, thresholds: Dict[str, int]) -> None:
    """文本模式输出"""
    timestamp = datetime.now(timezone.utc).astimezone().isoformat()
    
    print(f"[{timestamp}] 磁盘监控")
    print(f"状态: {status}")
    print(f"检查路径: {path}")
    print(f"总容量:   {format_size(usage['total'])}")
    print(f"已使用:   {format_size(usage['used'])}")
    print(f"可用:     {format_size(usage['available'])}")
    print(f"使用率:   {usage['usage_percent']:.2f}% (阈值: {thresholds['warning']}%/{thresholds['critical']}%)")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='Mini AIOps - 磁盘监控模块')
    parser.add_argument('path', nargs='?', default='/', help='要检查的路径（默认: /）')
    parser.add_argument('--json', action='store_true', help='以 JSON 格式输出')
    
    args = parser.parse_args()
    
    # 加载阈值
    thresholds = load_thresholds()
    
    try:
        usage = get_disk_usage(args.path)
        status, exit_code = determine_status(usage['usage_percent'], thresholds)
        
        if args.json:
            output_json(args.path, usage, status, exit_code, thresholds)
        else:
            output_normal(args.path, usage, status, thresholds)
        
        sys.exit(exit_code)
        
    except (FileNotFoundError, PermissionError, NotADirectoryError, OSError) as e:
        timestamp = datetime.now(timezone.utc).astimezone().isoformat()
        
        if args.json:
            error_result = {
                'module': 'disk',
                'timestamp': timestamp,
                'status': DiskStatus.ERROR,
                'message': str(e),
                'metrics': {},
                'exit_code': ExitCode.ERROR
            }
            print(json.dumps(error_result, ensure_ascii=False), file=sys.stderr)
        else:
            print(f"[{timestamp}] 磁盘监控", file=sys.stderr)
            print(f"状态: ERROR", file=sys.stderr)
            print(f"错误: {e}", file=sys.stderr)
        
        sys.exit(ExitCode.ERROR)


if __name__ == '__main__':
    main()
