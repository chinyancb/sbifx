import sys
import time
import subprocess
from subprocess import PIPE
import re


def main(sleep_sec=5):
    try:
        # デバイス名取得
        cmd = "networksetup -listallhardwareports | grep -A 1 'Wi-Fi' | grep -i device"
        proc = subprocess.Popen(cmd, shell=True, stdout=PIPE, stderr=PIPE)
        result = proc.communicate()
        stdout, stderr = result[0].decode(), result[1].decode()
        device = re.split('\s', stdout)[1]
    
        # wifi off
        cmd = f"networksetup -setairportpower {device} off; sleep {sleep_sec}"
        proc = subprocess.Popen(cmd, shell=True, stdout=PIPE, stderr=PIPE)
        result = proc.communicate()
        stdout, stderr = result[0].decode(), result[1].decode()

        # wifi on
        cmd = f"networksetup -setairportpower {device} on; sleep {sleep_sec}"
        proc = subprocess.Popen(cmd, shell=True, stdout=PIPE, stderr=PIPE)
        result = proc.communicate()
        stdout, stderr = result[0].decode(), result[1].decode()
      
    except Exception as e:
        print(e)
        sys.exit(1)
    
    return True


if __name__ == '__main__':
    main()
