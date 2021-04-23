import os
import sys
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import chromedriver_binary
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.select import Select


def main():
    # ログイン
    driver = webdriver.Chrome()
    driver.get('https://www.sbisec.co.jp')
    time.sleep(20)
    
    userid = os.environ['SBI_U']
    password = os.environ['SBI']
    driver.find_element_by_xpath("//input[@name='user_id']").send_keys(userid)
    driver.find_element_by_xpath("//input[@name='user_password']").send_keys(password)
    driver.find_element_by_xpath("//input[@title='ログイン']").click()
    
    time.sleep(10)
    
    # FXトレード画面に遷移
    fxurl = driver.find_element_by_xpath("//a[@href='https://www.sbisec.co.jp/ETGate/?OutSide=on&_ControlID=WPLETsmR001Control&_DataStoreID=DSWPLETsmR001Control&sw_page=LMFX&cat1=home&cat2=none&getFlg=on']").get_attribute('href')
    driver.get(fxurl)
    time.sleep(10)
    
    # トレード画面をクリック
    frame_top = driver.find_element_by_css_selector('#frame_top')
    driver.switch_to.frame(frame_top)
    driver.find_element_by_css_selector('#trade').click()
    
    # 元に戻す
    driver.switch_to.default_content()
    
    #--------------------
    # 注文入力
    #--------------------
    frame_trade = driver.find_element_by_css_selector('#frame_trade')
    time.sleep(2)
    driver.switch_to.frame(frame_trade)
    
    # 商品
    product_elements = driver.find_elements_by_xpath("//input[@type='radio' and @name='mini']")
    # FXミニを選択。もしαを選択する場合はクリックしたタイミングで要素が変更されるため再度>    要素を取得しリストの0番目をクリックする
    time.sleep(2)
    product_elements[1].click() 
    
    # 通貨ペアの選択(これも一度選択すると要素が変更されるため、他の通貨やαに変えた場合は再度要素を取得しなければいけない
    coin_types_elem = driver.find_element_by_xpath("//select[@name='meigaraId']")
    time.sleep(2)
    coin_type_drop = Select(coin_types_elem)
    coin_type_drop.select_by_visible_text('ﾐﾆ 米ドル-円')
    
    # 注文パターン(通常,OCO,IFD) デフォルトが通常であるため必要であれば
#    order_type_elem = driver.find_element_by_xpath("//select[@name='order']")
#    time.sleep(2)
#    order_type_drop = Select(order_type_elem)
#    coin_type_drop.select_by_visible_text('通常')
    
    # 取引(0は新規売り、1は新規買い)
    psition_elems = driver.find_elements_by_xpath("//input[@name='urikai']")
    time.sleep(2)
    psition_elems[1].click()
    
    # 執行条件(0:成行 1:指値 2:逆指値)
    execut_condition_elems = driver.find_elements_by_xpath("//input[@name='sikkoujyouken']")
    time.sleep(2)
    execut_condition_elems[1].click()
    
    
    # 価格
    # 円
    driver.find_element_by_css_selector('#sasine1_1').send_keys(100)
    time.sleep(2)
    # 銭
    driver.find_element_by_css_selector('#sasine1_2').send_keys(100)
    time.sleep(2)
    # 入力し直す時は下記を実行し再度入力
    #driver.find_element_by_css_selector('#sasine1_2').clear()
    
    # 数量(デフォルトで1が設定されているため必ず一度クリアして入力
    # !!! clear()せずそのまま1を入力すると11になってしまう
    driver.find_element_by_css_selector('#maisuu').clear()
    time.sleep(2)
    driver.find_element_by_css_selector('#maisuu').send_keys(1)
    
    # パスワード
    pass_input_elem = driver.find_element_by_xpath("//input[@type='PASSWORD' and @name='orderpass']")
    time.sleep(2)
    trade_pass = os.environ['SBIT']
    pass_input_elem.send_keys(trade_pass)
    
    # 確認を省略(必要であれば)
    driver.find_element_by_css_selector('#conform_skip').click()
    time.sleep(2)
    
    # 注文ボタンをクリック
    driver.find_element_by_css_selector('#btn_order_execute').click()
    time.sleep(2)
    
    # ログアウト
    #driver.find_element_by_css_selector('#logoutM').click()
    #time.sleep(5)
    #driver.quit()
    #
    #sys.exit(1)
    time.sleep(10)
    driver.switch_to.default_content()
    frame_top = driver.find_element_by_css_selector('#frame_top')
    driver.switch_to.frame(frame_top)
    driver.find_element_by_xpath("//button[@type='submit' and @class='logout' and @title='ログアウト']").click()
    driver.close()
    time.sleep(5)
    driver.quit()


if __name__ == '__main__':
    main()
