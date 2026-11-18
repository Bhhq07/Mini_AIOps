#!/usr/bin/env python3
"""
Mini AIOps - 磁盘监控模块测试
"""

import unittest
import sys
import json
from unittest.mock import patch, Mock, MagicMock
from io import StringIO

import check_disk


class TestGetDiskUsage(unittest.TestCase):
    """测试磁盘使用信息获取"""
    
    @patch('shutil.disk_usage')
    @patch('os.path.isdir')
    @patch('os.path.exists')
    def test_normal_usage_50_percent(self, mock_exists, mock_isdir, mock_disk_usage):
        """测试正常磁盘使用率 50%"""
        mock_exists.return_value = True
        mock_isdir.return_value = True
        mock_usage = MagicMock()
        mock_usage.total = 1000 * 1024 ** 3  # 1000 GB
        mock_usage.used = 500 * 1024 ** 3    # 500 GB
        mock_usage.free = 500 * 1024 ** 3    # 500 GB
        mock_disk_usage.return_value = mock_usage
        
        usage = check_disk.get_disk_usage('/test')
        
        self.assertEqual(usage['total'], 1000 * 1024 ** 3)
        self.assertEqual(usage['used'], 500 * 1024 ** 3)
        self.assertEqual(usage['available'], 500 * 1024 ** 3)
        self.assertAlmostEqual(usage['usage_percent'], 50.0, places=1)
    
    @patch('shutil.disk_usage')
    @patch('os.path.isdir')
    @patch('os.path.exists')
    def test_usage_exactly_80_percent(self, mock_exists, mock_isdir, mock_disk_usage):
        """测试使用率刚好 80%"""
        mock_exists.return_value = True
        mock_isdir.return_value = True
        mock_usage = MagicMock()
        mock_usage.total = 1000 * 1024 ** 3
        mock_usage.used = 800 * 1024 ** 3
        mock_usage.free = 200 * 1024 ** 3
        mock_disk_usage.return_value = mock_usage
        
        usage = check_disk.get_disk_usage('/test')
        self.assertAlmostEqual(usage['usage_percent'], 80.0, places=1)
    
    @patch('shutil.disk_usage')
    @patch('os.path.isdir')
    @patch('os.path.exists')
    def test_usage_exactly_90_percent(self, mock_exists, mock_isdir, mock_disk_usage):
        """测试使用率刚好 90%"""
        mock_exists.return_value = True
        mock_isdir.return_value = True
        mock_usage = MagicMock()
        mock_usage.total = 1000 * 1024 ** 3
        mock_usage.used = 900 * 1024 ** 3
        mock_usage.free = 100 * 1024 ** 3
        mock_disk_usage.return_value = mock_usage
        
        usage = check_disk.get_disk_usage('/test')
        self.assertAlmostEqual(usage['usage_percent'], 90.0, places=1)
    
    @patch('shutil.disk_usage')
    @patch('os.path.isdir')
    @patch('os.path.exists')
    def test_zero_capacity(self, mock_exists, mock_isdir, mock_disk_usage):
        """测试磁盘总容量为零"""
        mock_exists.return_value = True
        mock_isdir.return_value = True
        mock_usage = MagicMock()
        mock_usage.total = 0
        mock_usage.used = 0
        mock_usage.free = 0
        mock_disk_usage.return_value = mock_usage
        
        usage = check_disk.get_disk_usage('/test')
        self.assertEqual(usage['total'], 0)
        self.assertEqual(usage['usage_percent'], 0.0)
    
    @patch('os.path.exists')
    def test_path_not_exists(self, mock_exists):
        """测试路径不存在"""
        mock_exists.return_value = False
        with self.assertRaises(FileNotFoundError):
            check_disk.get_disk_usage('/nonexistent')
    
    @patch('shutil.disk_usage')
    @patch('os.path.isdir')
    @patch('os.path.exists')
    def test_disk_info_failure(self, mock_exists, mock_isdir, mock_disk_usage):
        """测试磁盘信息获取失败"""
        mock_exists.return_value = True
        mock_isdir.return_value = True
        mock_disk_usage.side_effect = OSError("Device error")
        
        with self.assertRaises(OSError):
            check_disk.get_disk_usage('/test')


class TestStatusDetermination(unittest.TestCase):
    """测试状态判断"""
    
    def test_status_ok(self):
        """测试 OK 状态"""
        status, exit_code = check_disk.determine_status(50.0)
        self.assertEqual(status, check_disk.DiskStatus.OK)
        self.assertEqual(exit_code, check_disk.ExitCode.OK)
    
    def test_status_warning_at_80(self):
        """测试 WARNING 状态（80%）"""
        status, exit_code = check_disk.determine_status(80.0)
        self.assertEqual(status, check_disk.DiskStatus.WARNING)
        self.assertEqual(exit_code, check_disk.ExitCode.WARNING)
    
    def test_status_critical_at_90(self):
        """测试 CRITICAL 状态（90%）"""
        status, exit_code = check_disk.determine_status(90.0)
        self.assertEqual(status, check_disk.DiskStatus.CRITICAL)
        self.assertEqual(exit_code, check_disk.ExitCode.CRITICAL)


class TestMainFunction(unittest.TestCase):
    """测试主函数"""
    
    @patch('sys.stdout', new_callable=StringIO)
    @patch('sys.argv', ['check_disk.py', 'C:\\'])
    @patch('shutil.disk_usage')
    @patch('os.path.isdir')
    @patch('os.path.exists')
    def test_json_output(self, mock_exists, mock_isdir, mock_disk_usage, mock_stdout):
        """测试 JSON 输出格式"""
        mock_exists.return_value = True
        mock_isdir.return_value = True
        mock_usage = MagicMock()
        mock_usage.total = 1000 * 1024 ** 3
        mock_usage.used = 500 * 1024 ** 3
        mock_usage.free = 500 * 1024 ** 3
        mock_disk_usage.return_value = mock_usage
        
        sys.argv = ['check_disk.py', 'C:\\', '--json']
        try:
            check_disk.main()
        except SystemExit as e:
            self.assertEqual(e.code, check_disk.ExitCode.OK)
        
        output = mock_stdout.getvalue()
        data = json.loads(output)
        self.assertIn('path', data)
        self.assertIn('total', data)
        self.assertIn('usage_percent', data)
        self.assertIn('status', data)
        self.assertIn('exit_code', data)
    
    @patch('sys.stdout', new_callable=StringIO)
    @patch('sys.argv', ['check_disk.py', 'C:\\'])
    @patch('shutil.disk_usage')
    @patch('os.path.isdir')
    @patch('os.path.exists')
    def test_exit_codes(self, mock_exists, mock_isdir, mock_disk_usage, mock_stdout):
        """测试各状态退出码"""
        mock_exists.return_value = True
        mock_isdir.return_value = True
        
        # 测试 OK 退出码
        mock_usage = MagicMock()
        mock_usage.total = 1000 * 1024 ** 3
        mock_usage.used = 500 * 1024 ** 3
        mock_usage.free = 500 * 1024 ** 3
        mock_disk_usage.return_value = mock_usage
        
        sys.argv = ['check_disk.py', 'C:\\']
        with self.assertRaises(SystemExit) as cm:
            check_disk.main()
        self.assertEqual(cm.exception.code, check_disk.ExitCode.OK)


if __name__ == '__main__':
    unittest.main()
