# -*- coding: utf-8 -*-
"""遗留 CLI 交互冒烟脚本（pexpect 驱动 app.py）。

注意：这是早期命令行版本的手动测试脚本，GUI 版本请改用 tests/test_book_link.py。
加 main 保护，避免静态检查 / 模块枚举时误触发子进程。
"""
import os
import sys
import time


def main() -> None:
    import pexpect

    test_book_url = "https://www.qimao.com/shuku/1815772/"

    # 与被测脚本同目录启动，从任意 cwd 运行都能找到 app.py
    here = os.path.dirname(os.path.abspath(__file__))
    process = pexpect.spawn("python3 app.py", cwd=here, encoding="utf-8", timeout=10)
    process.logfile = sys.stdout
    process.expect("按Enter键继续...")
    process.sendline("")
    # mode 1 test
    process.expect("请输入您的选择（1~9）:（默认“1”）")
    process.sendline("1")
    process.expect("请输入链接/ID，或输入s以进入搜索模式：")
    process.sendline(test_book_url)
    process.sendline("")
    process.sendline("")
    time.sleep(10)
    process.sendcontrol("c")
    # mode 2 test
    process.expect("请输入您的选择（1~9）:（默认“1”）")
    process.sendline("2")
    input("urls.txt准备好后按Enter键继续测试")
    process.sendline("")
    process.sendline("")
    process.sendline("")
    time.sleep(10)
    process.sendcontrol("c")
    # mode 3 test
    process.expect("请输入您的选择（1~9）:（默认“1”）")
    process.sendline("3")
    process.expect("请输入链接/ID，或输入s以进入搜索模式：")
    process.sendline(test_book_url)
    process.sendline("")
    process.sendline("")
    time.sleep(10)
    process.sendcontrol("c")
    # mode 4 test
    process.expect("请输入您的选择（1~9）:（默认“1”）")
    process.sendline("4")
    process.expect("请输入链接/ID，或输入s以进入搜索模式：")
    process.sendline(test_book_url)
    process.sendline("")
    time.sleep(10)
    process.sendcontrol("c")
    # mode 6 test
    process.expect("请输入您的选择（1~9）:（默认“1”）")
    process.sendline("6")
    process.expect("请选择更新模式:1 -> 单个更新 2-> 批量更新")
    process.sendline("1")
    process.sendline("废土之上.txt")
    process.sendline("")
    # done
    print("All tests passed")


if __name__ == "__main__":
    main()
