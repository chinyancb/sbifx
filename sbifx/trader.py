import sys
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import chromedriver_binary
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.select import Select
import mylib.fxtradeutil as ftu


# fxtradeutilインスタンス作成
utl = ftu.FxTradeUtil()

# SBIサイトにログイン
result = utl.login_sbi()
driver = result[1]
print(result)
time.sleep(5)

# FXページに遷移
result = utl.to_sbi_fx_page(driver)
driver = result[1]
print(result)
time.sleep(5)

# 注文画面のセットアップ
result = utl.set_up_order_screen(driver)
