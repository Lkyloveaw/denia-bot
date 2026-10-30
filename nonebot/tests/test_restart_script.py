import subprocess
import sys
import unittest
from pathlib import Path


@unittest.skipUnless(sys.platform == 'win32', 'Windows PowerShell only')
class RestartScriptTests(unittest.TestCase):
    def run_script(self, listener):
        root = Path(__file__).resolve().parents[2]
        python = str(root / 'nonebot' / '.venv' / 'Scripts' / 'python.exe').replace("'", "''")
        script = str(root / 'tools' / 'restart-nonebot.ps1').replace("'", "''")
        code = f'''
$script:stopped = @()
function Get-CimInstance {{
 [pscustomobject]@{{ ProcessId=100; ParentProcessId=1; Name='python.exe'; ExecutablePath='{python}'; CommandLine='python.exe bot.py' }}
 [pscustomobject]@{{ ProcessId=101; ParentProcessId=100; Name='python.exe'; ExecutablePath='C:\\Python\\python.exe'; CommandLine='python.exe bot.py' }}
 [pscustomobject]@{{ ProcessId=102; ParentProcessId=1; Name='python.exe'; ExecutablePath='C:\\Other\\python.exe'; CommandLine='python.exe bot.py' }}
}}
function Get-NetTCPConnection {{ if ({listener} -notin $script:stopped) {{ [pscustomobject]@{{OwningProcess={listener}}} }} }}
function Get-Process {{ param($Id) [pscustomobject]@{{Id=$Id}} }}
function Stop-Process {{ param($Id) $script:stopped += $Id; Write-Output "stopped:$Id" }}
. '{script}'
'''
        return subprocess.run(['powershell.exe','-NoProfile','-Command',code], capture_output=True, text=True, timeout=15)

    def test_only_matching_processes_are_stopped(self):
        result = self.run_script(101)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('stopped:100', result.stdout)
        self.assertIn('stopped:101', result.stdout)
        self.assertNotIn('stopped:102', result.stdout)

    def test_unknown_port_owner_is_never_stopped(self):
        result = self.run_script(102)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('stopped:', result.stdout)


if __name__ == '__main__':
    unittest.main()
