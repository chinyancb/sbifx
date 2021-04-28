import os
import sys
import glob
import re
import shutil
import logging
import logging.config
from pathlib import Path
import itertools
import requests
import websocket
import json
import hashlib
import hmac
import time
import datetime
#import pytz
#import dateutil.parser
import pandas as pd
import numpy as np
import random
import numexpr
import asyncio
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import chromedriver_binary
from selenium.webdriver.support.select import Select
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import NoSuchElementException

from bs4 import BeautifulSoup
from mylib.lineutil import LineUtil

#------------------------------
# アプリケーションのパスを指定
#------------------------------
app_home = str(Path(__file__).parents[1])
sys.path.append(app_home)

# SBI証券サイトURL
SBI_URL = 'https://site2.sbisec.co.jp/ETGate/?_ControlID=WPLEThmR001Control&_PageID=DefaultPID&_DataStoreID=DSWPLEThmR001Control&_ActionID=DefaultAID&getFlg=on'

# SBI証券FXページURL
FX_PAGE_URL = 'https://fx.sbisec.co.jp/forex/trade/client/index.aspx'

# ログイン情報
userid    = os.environ['SBI_U']
password  = os.environ['SBI']
tpassword = os.environ['SBIT']


# データ出力先パス
CLOSE_RATE_FILE_PATH     = app_home + '/var/share/close/'        # closeレート情報
MACD_FILE_PATH           = app_home + '/var/share/macd/'         # MACDCLOSE
MACD_STREAM_FILE_PATH    = app_home + '/var/share/macd_stream/'  # MACDリアルタイム(ニアリーイコール)
STOCH_FILE_PATH          = app_home + '/var/share/stoch/'        # ストキャスティクスCLOSE
STOCH_STREAM_FILE_PATH   = app_home + '/var/share/stoch_stream/' # ストキャスティクスリアルタイム(ニアリーイコー)
POSITION_FILE_PATH       = app_home + '/var/share/pos/'          # ポジション判定結果
POSITION_MACD_FILE_PATH  = app_home + '/var/share/pos/macd/'     # MACDによるポジション判定結果
POSITION_STOCH_FILE_PATH = app_home + '/var/share/pos/stoch/'    # ストキャスティクスよるポジション判定結果
SYSCONTROL               = app_home + '/var/share/sysc/'         # システムコントロール用


# システムコントロール用ファイル名
INIT_POSITION   = 'init_positioner' # ポジション情報を初期化する
STOP_NEW_TRADE  = 'stop_new_trade'  # 新規エントリーを停止する
POSITION_MARKET_ORDER = 'position_imarget_order' # 保有ポジションを成行で決済する


# JST 変換用定数
JST = datetime.timezone(datetime.timedelta(hours=+9), 'JST') 




class CloseRateGetError(Exception):
    """
    * closeレートを取得できない
    """
    pass


class CloseMacdStochScrapGetError(Exception):
    """
    * tradingviewからスクレイピングでMACD,ストキャスティクス関連情報を取得できない
    """
    pass


class CloseMacdStochStreamScrapGetError(Exception):
    """
    * tradingviewからスクレイピングでリアルタイムにMACD,ストキャスティクス関連情報を取得できない
    """
    pass


class PosJudgementError(Exception):
    """
    * ポジション判定でのエラー
    """
    pass




class FxTradeUtil(object):

    def __init__(self): 

        # データ関連
        self.ind_df           = pd.DataFrame()  # レート,MACD,ストキャスティクスデータ格納用
        self.close_rate_df    = pd.DataFrame()  # closeレート格納用
        self.bitf_rate_df     = pd.DataFrame()  # ビットフライヤーのレート情報
        self.macd_stream_df   = pd.DataFrame()  # macdリアルタイムデータ格納用
        self.stoch_stream_df  = pd.DataFrame()  # ストキャスティクスデータ格納用
        self.macd_df          = pd.DataFrame()  # macd確定値データ格納用
        self.stoch_df         = pd.DataFrame()  # ストキャスティクス確定値データ格納用
        self.pos_jdg_df       = pd.DataFrame([{'position':'STAY', 'jdg_timestamp':datetime.datetime.now()}])  # ポジション計算用データフレーム
        self.pos_macd_jdg_df  = pd.DataFrame([{'position':'STAY', 'macd':0, 'signal':0, 'hist':0, 'macd_jdg_timestamp':datetime.datetime.now()}])  # ポジション計算用データフレーム
        self.pos_stoch_jdg_df = pd.DataFrame([{'position':'STAY', 'pK':0.0, 'pK':0.0,'stoch_jdg_timestamp':datetime.datetime.now()}])  # ポジション計算用データフレーム

        # ファイル関連
        self.close_filename        = f"close_{datetime.datetime.now().strftime('%Y-%m-%d-%H:%M')}"       # closeデータの書き出しファイル名
        self.bitf_rate_filename    = f"close_bitf{datetime.datetime.now().strftime('%Y-%m-%d-%H:%M')}"       # closeデータの書き出しファイル名
        self.macd_filename         = f"macd_{datetime.datetime.now().strftime('%Y-%m-%d-%H:%M')}"        # macdデータの書き出しファイル名
        self.macd_stream_filename  = f"macd_stream{datetime.datetime.now().strftime('%Y-%m-%d-%H:%M')}"  # macd1秒データの書き出しファイル名
        self.stoch_filename        = f"stoch_{datetime.datetime.now().strftime('%Y-%m-%d-%H:%M')}"       # ストキャスティクスデータの書き出しファイル名
        self.stoch_stream_filename = f"stoch_stream{datetime.datetime.now().strftime('%Y-%m-%d-%H:%M')}" # ストキャスティクス1秒データの書き出しファイル名
        self.pos_filename          = f"pos_{datetime.datetime.now().strftime('%Y-%m-%d-%H:%M')}"         # ポジションデータの書き出しファイル名
        self.pos_macd_filename     = f"pos_{datetime.datetime.now().strftime('%Y-%m-%d-%H:%M')}"         # macdによるポジションデータの書き出しファイル名
        self.pos_stoch_filename    = f"pos_{datetime.datetime.now().strftime('%Y-%m-%d-%H:%M')}"         # ストキャスティクスによるポジションデータの書き出しファイル名

        # フラグ関連
        self.is_div         = False                                       # ダイバージェンスが発生していればTrue,起きていなければFalse 

        # Line通知用
        self.line = LineUtil()

        # ロギング設定
        self.set_logging() 
        
    def set_logging(self, qualname='fxtradeutil'):
        """
        * self.loggingの設定を行う
        * param
            qualname:str (default 'fxtradeUtil') self.loging.confで設定した名前
        * return
            self.log:Logger 設定が成功するとself.logに設定されたロガーが格納される
        """
        # ロギング
        LOG_CONF = app_home + '/etc/conf/logging.conf'
        logging.config.fileConfig(LOG_CONF)
        self.log = logging.getLogger(qualname)
        return self.log

    def init_memb(self):
        """
        closeレートの取得やMACDの計算,その他例外が発生した場合,緊急対応として使用しているdataframe,その他メンバを初期化する
        return:
            True
        """
        self.log.info('init_memb() called')
        self.__init__()
        self.line.send_line_notify('メンバを初期化しました')
        self.log.info('init_memb() done')
        return True



    def init_position(self):
        """
        * メインポジション、サポートポジション共にSTAY(初期化)にする
        * param
            なし
        * return
            True :初期化成功
            False:初期化失敗
        """
        self.log.info(f'init_position() called')
        try:
            pos_jdg_tmp_df = pd.DataFrame([{'main_pos':'STAY','sup_pos':'STAY',
                'jdg_timestamp':datetime.datetime.now()}])  # ポジション計算用データフレーム
        except Exception as e:
            self.log.critical(f'ポジションの初期化に失敗しました : [{e}]')
            return False

        self.pos_jdg_df = pos_jdg_tmp_df.copy()
        del(pos_jdg_tmp_df)

        self.log.info(f'position data is init done.')
        return True


            
    def make_file(self, path, filename, mode='w'):
        """
        * 指定されたパス、ファイル名に従い空のファイルを作成する
        * param
            path:str     ファイルを作成するディレクトリのパス(末尾には/を付けて指定)
            filename:str 作成するファイル名
            mode:str(default w) 書き込むモード(デフォルトは上書き)
        * return
            True : ファイル作成成功
            False: ファイル作成失敗
        """
        self.log.info(f'make_file() called')
        self.log.info(f'emptiness : [{path + filename}]')
            
        # ディレクトリ存在チェック。無ければ作成
        tmp_dir_list  = path.split('/')
        tmp_dir = '/'.join([str(i) for i in tmp_dir_list[0:-1]])
        if os.path.isdir(tmp_dir) == False:
            os.makedirs(tmp_dir, exist_ok=True)
            self.log.info(f'maked dir : [{tmp_dir}]')

        # 空ファイル作成
        try:
            with open(path + filename, mode=mode):
                pass
        except Exception as e:
            self.log.error(f'cant make emptiness file. : [{e}]')
            return False

        self.log.info(f'emptiness file make done.[{path + filename}]')
        return True




    def rm_file(self, path, filename):
        """
        * 指定されたファイルを削除する
        * param
            path:str 削除対象のファイルが置かれているディレクトリパス(末尾には/を付けて指定)
            filename:str 削除するファイル名
        * return
            True:bool 削除成功
            False:bool削除失敗
            None:bool ファイルが存在しない or ディレクトリが存在しない
       """ 
        self.log.info(f'rm_file() called.')
        
        # ファイルが存在しない場合
        if self.is_exit_file(path, filename) == False:
            self.log.info(f'not found remove file : [{path + filename}]')
            return None

        try:
            os.remove(path + filename)
        except Exception as e:
            self.log.error(f'remove file failure : [{e}]')
            return False

        self.log.info(f'remove file done : [{path + filename}]')
        self.log.info(f'rm_file() done')
        return True




    def is_exit_file(self, path, filename):
        """
        * 指定されたファイルの存在確認を行う
        * param
            path:str ファイルが格納されているディレクトリパス
            filename:str : 存在確認するファイル名
        * return
            True :bool ファイルが存在する場合
            False:book ファイルが存在しない場合
        """
        self.log.info(f'is_exit_file() called')

        if os.path.exists(path + filename):
            self.log.info(f'file is exists. : [{path + filename}]')
            self.log.info(f'is_exit_file is done')

            return True

        self.log.info(f'file not found : [{path + filename}]')
        self.log.info(f'is_exit_file() done')

        return False



    def get_file_name(self, path, prefix=''):
        """
        * 指定されたディレクトリパス,prefixに当てはまる最新のファイル名を取得
        * 主にポジション判定のヒストグラムの閾値変更のために使用する
        * param
            path:str 取得するファイルが置かれているディレクトリ名(末尾に/を付けて指定)
            prefix:srt 取得するファイル名のprefix(defalt '')
        * return
            filename:str 取得したファイル名
            not_found_file:str ファイル名がない場合
            not_found_dir :str 指定したディレクトリが存在しない場合
            cant_get_file:srt ファイル名取得に失敗した場合
        """

        self.log.info(f'get_file_name() called')

        # ディレクトリ存在チェック
        if os.path.exists(path) == False:
            self.log.info(f'not found dir : [{path}]')
            return 'not_found_dir'

        # prefixにあたるファイル名があるか確認
        if len(glob.glob(f'{path}{prefix}*')) == 0:
            self.log.info(f'not found file : [{path}{prefix}]')
            return 'not_found_file'

        # ファイル名取得
        try:
            files = glob.glob(f"{path}{prefix}*")
            filename = max(files, key=os.path.getctime)
        except OSError as e:
            self.log.critical(f'cant get file name : [{e}]')
            return 'cant_get_file'

        # ファイル名が絶対パスなのでファイル名単体にする
        filename = filename.split('/')[-1]
        self.log.info(f'get file : [{filename}]')    
        self.log.info(f'get_file_name() done')

        return filename 



    def load_pos_df(self, head_nrow=1):
        """
        * posデータをロードすし、メンバ（self.pos_jdg_df）として登録する
        * param
            head_nrow:int 先頭から読み込む行数（デフォルト1行）
        * retrn
            True :ロードに成功
            False:ロードに失敗
            None :posデータファイルが無い場合
        """
        self.log.info(f'load_pos_df() called')

        # posファイルが無い場合
        if len(glob.glob(f'{POSITION_FILE_PATH}pos*')) == 0: 
            self.log.error(f'not found pos data file. under path : [{POSITION_FILE_PATH}]')
            return None

        try:
            files = glob.glob(f"{POSITION_FILE_PATH}pos*")
            latest_file = max(files, key=os.path.getctime)
        except OSError as e:
            self.log.critical(f'reload pos data error: [{e}]')
            return False

        # ポジションファイル読み込み
        try:
            latest_file = latest_file.split('/')[-1]
            self.log.info(f'load file : [{latest_file}]')
            pos_df = pd.read_csv(filepath_or_buffer=POSITION_FILE_PATH + latest_file, sep=',', header=0)
            self.log.info(f'csv file read done')
    
            # 先頭行を読み込み
            pos_df = pos_df.head(n=head_nrow).reset_index(level=0, drop=True)
            self.log.info(f'reset index done') 
    
            # 文字列からint、datetime型に変換
            pos_df['close_rate'] = pos_df['close_rate'].astype('int')
            self.log.info('close_rate dtype convert done.')
    
            # ポジション判定時刻の変換(そのまま読み込んで大丈夫）
            jdg_timestamp_list = []
            for i in range(0, head_nrow):
                jdg_timestamp_jst    = dateutil.parser.parse(pos_df['jdg_timestamp'][i]).astimezone(JST)
                jdg_timestamp_list.append(jdg_timestamp_jst)
            else:
                pos_df['jdg_timestamp'] = jdg_timestamp_list 
        except Exception as e:
            self.log.error(f'position data load failure : [{POSITION_FILE_PATH + latest_file}, {e}]')
            return False

        # 読み込んだファイルは時系列で降順となっているため昇順に変更
        pos_df = pos_df.sort_values(by ='jdg_timestamp', ascending=True).reset_index(level=0, drop=True)
        self.log.info('jdg_timestamp dtype convert done.')

        # メンバとしてコピー
        self.pos_jdg_df = pos_df.copy()
        del(pos_df)

        self.log.info(f'pos data load success.')
        return True 




    def write_csv_dataframe(self, df, path, sep=',', header=True, index=False, mode='w'):
        """
        データフレームをcsvとして書き出す
        param
            df:DataFrame 書き出すデータフレームオブジェクト
            path:str     書き出し先のパス
            sep:str     出力時のセパレーター(default : ,)
            header:blool (defalut : True) Trueの場合はヘッダを書き出す
            mode:str     出力モード(default : w )
            index(default True):bool Trueの場合indexも書き出す(default : False) 
        return
            True   書き出し成功
            False  書き出し失敗
        """

        self.log.info('write_csv_dataframe() called.')
        # ディレクトリ存在チェック。無ければ作成
        tmp_dir_list  = path.split('/')
        tmp_dir = '/'.join([str(i) for i in tmp_dir_list[0:-1]])
        if os.path.isdir(tmp_dir) == False:
            os.makedirs(tmp_dir, exist_ok=True)
            self.log.info(f'maked dir : [{tmp_dir}]')

        df_tmp = df.copy()
        try:
            if df_tmp.to_csv(path_or_buf=path, sep=sep, index=index, mode=mode, header=header) == None:
                self.log.info(f'write dataframe to csv done. : [{path}]')
                del(df_tmp)
                return True
        except Exception as e:
            self.log.error(f'write_csv_dataframe() cancelled.')
            return False 



    def read_csv_dataframe(self, path, filename=None, header=True, dtypes=None):
        """
        * csvファイルをデータフレームとして読み込む
        * param
            path:str csvファイル格納ディレクトリパス。末尾に「/」をつけること
            filename:str (defult None) 読み込むファイル名
                         ※ Noneの場合指定されたディレクトリパス配下の最新のファイルを読み込む
            header:bool (default True) 読み込むファイルにヘッダーがあるか
                   True:ヘッダーあり
                   False:ヘッダー無し
            dtype:dict (default None) カラムの型を指定したい場合は下記のように定義する
                  {'a': 'int', 'b': 'float', 'c': 'str', 'd': 'np.datetime[64]', 'e': 'datetime'}
                  ただし設定できる型は上記のみ
        * return
            load_df:dataframe
                    読み込み成功:csvデータを格納したデータフレーム
                    読み込み失敗:空のデータフレーム
        """
        self.log.info(f'read_csv_dataframe() called')

        # ディレクトリ存在チェック
        if os.path.isdir(path) == False:
            self.log.error(f'not found path : [{path}]')
            return pd.DataFrame()
                    

        # ファイル特定
        if filename == None:
            try:
                files = glob.glob(f"{path}*")
                latest_file = max(files, key=os.path.getctime)
            except Exception as e:
                self.log.error(f'not found file : [{e}]')
                return pd.DataFrame()
        
        else:
            try:
                latest_file = f'{path + filename}'
                # ファイルの存在チェック
                if os.path.exists(latest_file) != True:
                    self.log.error(f'not found file : [{filename}]')
                    return pd.DataFrame()
            except Exception as e:
                self.log.error(f'not found file : [{e}]')
                return pd.DataFrame()

        # ファイル読み込み
        if header != True:
            load_df = pd.read_csv(filepath_or_buffer=latest_file, header=None)
        else:
            load_df = pd.read_csv(filepath_or_buffer=latest_file, header=0)
        self.log.info(f'load data frame done')


        # 型指定がある場合
        if dtypes != None:
            try:
                for col ,vtype in dtypes.items(): 
                    if vtype == 'int':
                        load_df[col] = load_df[col].astype('int')
                    elif vtype == 'float':
                        load_df[col] = load_df[col].astype('float')
                    elif vtype == 'str':
                        load_df[col] = load_df[col].astype('str')
                    elif vtype == 'np.datetime[64]':
                        load_df[col] = pd.to_datetime(load_df[col])
                    elif vtype == 'datetime':
                        for i in range(len(load_df)):
                            load_df[col][i] = pd.to_datetime(load_df[col][i]).to_pydatetime()
                    else:
                        self.log.error('invalid set of columns name or type')
                        return pd.DataFrame()
                else:
                    self.log.info(f'type convert done')
            except Exception as e:
                self.log.error(f'convert error : [{e}]')
                return pd.DataFrame()

        self.log.info(f'read_csv_dataframe() done')
        return load_df
         


    def login_sbi(self,  url=SBI_URL, headless=False):
        """
        * SBIサイトにログインする
        * param
             url:str (SBIのトップページURL）
             headless:bool (True:通常ブラウザ, False:ヘッドレスブラウザ)
        * return
             array-like
             *ログイン成功時 
                 True:bool
                 driver:webdriver ログイン後のwebdriver
             *ログイン失敗時
                 False:bool
                 driver:webdriver
        """

        self.log.info(f'called login_sbi().')
        options = webdriver.ChromeOptions()
#        p_id = '1'
#        options.add_argument(f'user-data-dir={app_home}/Chrome') 
#        options.add_argument('--profile-directory=Profile '+ p_id)
        if headless == True:
            options.add_argument('--headless')
        driver = webdriver.Chrome(options=options)
        
        try:
            driver.get(SBI_URL)
            driver.find_element_by_xpath("//input[@name='user_id']").send_keys(userid)
            driver.find_element_by_xpath("//input[@name='user_password']").send_keys(password)
            driver.find_element_by_xpath("//input[@title='ログイン']").click()
        except Exception as e:
            self.error(f'login error. :[{e}]')
            return [False, driver]
        
        self.log.info('sbi site login success.')
        self.log.info('login_sbi() done.')
        return [True, driver]



    
    def to_sbi_fx_page(self, driver):
        """
        * SBI証券サイトのFXページへ遷移
        * param
            driver:webdriver (SBIサイトログイン後のdriver)
        * return
            array-like
            *遷移成功
                True:bool
                driver:webdriver (FXページ遷移後のdriver)
            *遷移失敗
                False:bool
                driver:webdriver (引数に与えられたdriver)
        """
        self.log.info(f'to_sbi_fx_page() called.')
        tmp_driver = driver

        # FX取引ページのURL取得
        try:
            fxurl = driver.find_element_by_xpath("//a[@href='https://www.sbisec.co.jp/ETGate/?OutSide=on&_ControlID=WPLETsmR001Control&_DataStoreID=DSWPLETsmR001Control&sw_page=LMFX&cat1=home&cat2=none&getFlg=on']").get_attribute('href')
            driver.get(fxurl)
        except Exception as e:
            self.log.error(f'transition to fx page error :[{e}]')
            return [False, tmp_driver]

        self.log.info('transition to fx page done.')
        self.log.info('to_sbi_fx_page() done.')
        return [True, driver]








    def set_up_order_screen(self, driver):
        """
        * 成行で新規注文を出せる状態にする
          * ディーリングボードを表示
          * 取引パスワードを入力
          * 確認画面を省略をチェック
          * 注文で「新規」をチェック
        * param
            driver:webdriver (FXページ遷移後のdriver)
        * return
            array-like
            * 成功時
                True:bool
                driver:webdriver (ディーリングボードを表示した状態のdriver)
            * 失敗時
                False:bool
                driver:webdriver (引数に渡された状態のdriver)
        """
        self.log.info('set_up_order_screen() called.')
        tmp_driver = driver

        try:
            frame_price = driver.find_element_by_css_selector('#frame_price')
            driver.switch_to.frame(frame_price)
            self.log.info('frame switch :[frame_price]')
            
            # ディーリングボードクリック
            driver.find_element_by_xpath("//a[@id='ui-id-2' and contains(., 'ディーリングボード')]").click() 
            self.log.info('dealing board clicked.')

            # 取引パスワード入力し確認画面の省略をチェック
            driver.find_element_by_xpath("//input[@id='password_text' and @type='password']").send_keys(tpassword)
            driver.find_element_by_xpath("//input[@id='conformskip_check']").click()
            self.log.info('trade pass inputed.')

            # 注文タイプで新規をチェック
            driver.find_element_by_xpath("//input[@id='dealing_ipt_sinki_USDJPY']")
            self.log.info('new order type checked.')

        except Exception as e:
            self.log.error(f'set up order screen error. :[{e}]')
            return [False, tmp_driver] 

        self.log.info('order screen set up done.')
        self.log.info('set_up_order_screen() done.')
        return [True, driver]

    


    def get_rate(self, driver, position):
        """
        * レートを取得する
        * param
            driver:webdriver (ディーリングボードフレームに設定された状態)
            position:str 'ASK' or 'BIT'
        * return
            rate:float 関数を呼び出した時点のレート
            * 成功
                実際のレート
            * 失敗
                -1
        """

        if position !='ASK' and position !='BIT':
            return -1

        html = driver.page_source
        soup = BeautifulSoup(html, 'lxml')

        try:
            if position == 'ASK':
                return float(soup.select('#ask-rate-USDJPY')[0].text)
            else:
                return float(soup.select('#bid-rate-USDJPY')[0].text)
        except Exception as e:
            self.log.error(f'get rate error. :[{e}]')
            return -1


    def order_ask_makert(self, driver):
        """
        * ディーリングボードで成行でロング注文を出す
          約定時の価格を取得する
          !引数でロング・ショートの判別をしてもいいが、少しでも早く注文したいので関数で分けることにした
        * param
            driver:set_up_order_screen()でディーリングボードのセットアップ完了状態時のdriver
        * return
            array-like
            * 成功時
                True:bool
                driver:webdriver(ディーリングボードのiframeにスイッチした状態のdriver)
                contract_price:float 約定価格
        
            * 失敗時
                False:bool
                driver:webdriver(引数に渡された状態のdriver)
                contract_price:float 0.0
        """
        self.log.info('order_ask_makert() called.')
        tmp_driver = driver

        try:
            # 成行き注文
            driver.find_element_by_xpath("//button[@id='btn_ask_USDJPY']").click()
           
            # frameをFXトップページに戻し約定した価格を取得
            driver.switch_to.default_content()

            # 約定価格を確認するためframe_topにスイッチ
            frame_top = driver.find_element_by_css_selector('#frame_top')
            driver.switch_to.frame(frame_top)

            # 「照会」をクリックし建玉サマリにフレームをスイッチ
            driver.find_element_by_css_selector('#watch').click()
            driver.switch_to.default_content()
            frame_trade = driver.find_element_by_css_selector('#frame_trade')
            driver.switch_to.frame(frame_trade)

            # 約定価格を取得
            tategyoku_info_list = driver.find_element_by_css_selector('.tline-normal').text.split(' ')
            contract_price =  float(tategyoku_info_list[3])

            # ディーリングボードのフレームにスイッチ
            driver.switch_to.default_content()
            frame_price = driver.find_element_by_css_selector('#frame_price')
            driver.switch_to.frame(frame_price)
        except Exception as e:
            self.log.error(f'ask order error. :[{e}]')
            return [False, tmp_driver, float(0.0)]

        self.log.info('order_ask_makert() done.')
        return [True, driver, contract_price]


    def order_bid_makert(self, driver):
        """
        * ディーリングボードで成行でロング注文を出す
          約定時の価格を取得する
          !引数でロング・ショートの判別をしてもいいが、少しでも早く注文したいので関数で分けることにした
        * param
            driver:set_up_order_screen()でディーリングボードのセットアップ完了状態時のdriver
        * return
            array-like
            * 成功時
                True:bool
                driver:webdriver(ディーリングボードのiframeにスイッチした状態のdriver)
                contract_price:float 約定価格
        
            * 失敗時
                False:bool
                driver:webdriver(引数に渡された状態のdriver)
                contract_price:float 0.0
        """
        self.log.info('order_bid_makert() called.')
        tmp_driver = driver

        try:
            # 成行き注文
            driver.find_element_by_xpath("//button[@id='btn_bid_USDJPY']").click()
           
            # frameをFXトップページに戻し約定した価格を取得
            driver.switch_to.default_content()

            # 約定価格を確認するためframe_topにスイッチ
            frame_top = driver.find_element_by_css_selector('#frame_top')
            driver.switch_to.frame(frame_top)

            # 「照会」をクリックし建玉サマリにフレームをスイッチ
            driver.find_element_by_css_selector('#watch').click()
            driver.switch_to.default_content()
            frame_trade = driver.find_element_by_css_selector('#frame_trade')
            driver.switch_to.frame(frame_trade)

            # 約定価格を取得
            tategyoku_info_list = driver.find_element_by_css_selector('.tline-normal').text.split(' ')
            contract_price =  float(tategyoku_info_list[3])

            # ディーリングボードのフレームにスイッチ
            driver.switch_to.default_content()
            frame_price = driver.find_element_by_css_selector('#frame_price')
            driver.switch_to.frame(frame_price)
        except Exception as e:
            self.log.error(f'bid order error. :[{e}]')
            return [False, tmp_driver, float(0.0)]

        self.log.info('order_bid_makert() done.')
        return [True, driver, contract_price]

    

    def order_stop_loss(self, driver, stop_price, stop_pips): 
        """
        * 逆指値でストップロスを注文する
          主にエントリー直後に使用する
        * param
            driver:webdriver('ディーリングボードのフレームに設定した状態')
            stop_price:str 逆指値(円)
            stop_pips:str  逆指値(pips)
        * return
            array-like
            * 成功時
                True:bool
                driver:webdriver('ディーリングボードのフレームに設定した状態')
            * 失敗時
                False:bool
                driver:引数に渡された状態
        """
        
        self.log.info('order_stop_loss() called.')
        tmp_driver = driver

        try:
            # 照会をクリック
            driver.switch_to.default_content()
            frame_top = driver.find_element_by_css_selector('#frame_top')
            driver.switch_to.frame(frame_top)
            driver.find_element_by_css_selector('#watch').click()
            self.log.info(f'syoukai link click done.')


            # 決済画面に遷移
            driver.switch_to.default_content()
            frame_trade = driver.find_element_by_css_selector('#frame_trade')
            driver.switch_to.frame(frame_trade)
            url = driver.find_element_by_xpath("//*[@id='Label1']/table[2]/tbody/tr[3]/td[9]/a").get_attribute('href')
            driver.get(url)
            self.log.info('payment page transition done.')

    
            # ストップロスを入力し発注
            #! デフォルトで価格がグレーアウトしているため一旦OCOに変えた後、逆指値に設定する
            order_type_elem = driver.find_element_by_xpath("//select[@name='order']")
            order_type_drop = Select(order_type_elem)
            order_type_drop.select_by_visible_text('OCO')
            self.log.info('COC clicked. (temporary)')

            # 逆指値に設定(逆指値はサイト上「通常」扱い)
            order_type_elem = driver.find_element_by_xpath("//select[@name='order']")
            order_type_drop = Select(order_type_elem)
            order_type_drop.select_by_visible_text('通常')
            driver.find_element_by_xpath("//input[@name='sikkoujyouken' and @value='2']").click()
            self.log.info('stop price clicked.')
            driver.find_element_by_css_selector('#sasine1_1').send_keys(stop_price)
            driver.find_element_by_css_selector('#sasine1_2').send_keys('215')
            driver.find_element_by_xpath("//input[@type='PASSWORD' and @name='orderpass']").send_keys(tpassword)
            self.log.info(f'stop price input done.')
            driver.find_element_by_xpath("//button[@class='execute' and @type='submit']").click()
            driver.find_element_by_xpath("//button[@class='execute' and @type='submit']").click()

            # 逆指値がレートより高い or 低い場合などなにかのエラーが出た時
            try:
                error_msg = driver.find_element_by_xpath("//div[@class='error_str']").text
            except NoSuchElementException as e:
                pass
            self.log.info(f'stop loss order done. :[{stop_price}.{stop_pips}')
            # FXトップページにアクセス
            driver.get('https://fx.sbisec.co.jp/forex/trade/client/index.aspx')
            self.log.info('fx top page transition done.')

            # ディーリングボードのフレームに設定
            frame_price = driver.find_element_by_css_selector('#frame_price')
            driver.switch_to.frame(frame_price)
            self.log.info(f'frame switch done. :[#frame_price]')
        except Exception as e:
            self.log.error(f'stop loss order error. :[{e}]')
            return [False, tmp_driver]

#            # 戻るを5回やってFXトップページに遷移
#            driver.back()
#            driver.back()
#            driver.back()
#            driver.back()
#            driver.back()


        self.log.info('order_stop_loss() done.')
        return [True, driver]



    def cancel_order(self, driver):
        """
        * 注文 or 決済注文の取消を行う
        * param
            driver:webdriver (ディーリングボードフレームに設定された状態)
        * return
            array-like
            * 成功時
                True:bool
                driver:webdriver (ディーリングボードフレームに設定された状態)
            * 失敗時
                False:bool
                driver:webdriver (引数に渡された状態)
        """
        self.log.info('cancel_order() called.')
        tmp_driver = driver

        try:
            # 注文照会(取消・訂正)へ遷移
            driver.switch_to.default_content()
            frame_top = driver.find_element_by_css_selector('#frame_top')
            driver.switch_to.frame(frame_top)
            driver.find_element_by_css_selector('#watch').click()
            driver.find_element_by_css_selector('#orderList').click()
            self.log.info(f'cancle page transition done.')

            # トレードフレームにスイッチ
            driver.switch_to.default_content()
            frame_trade = driver.find_element_by_css_selector('#frame_trade')
            driver.switch_to.frame(frame_trade)



            # 注文取消実行
            cancel_url = driver.find_element_by_xpath("//*[@id='Label1']/table/tbody/tr/td[13]/span/a[2]").click()
            driver.find_element_by_xpath("//input[@type='PASSWORD' and @name='orderpass']").send_keys(tpassword)
            driver.find_element_by_xpath("//button[@class='execute' and @name='kakunin' and @value='注文取消']").click()
            self.log.info(f'cancel order done.')

            # FXトップページにアクセス
            driver.get('https://fx.sbisec.co.jp/forex/trade/client/index.aspx')

            frame_price = driver.find_element_by_css_selector('#frame_price')
            driver.switch_to.frame(frame_price)
        except Exception as e:
            self.log.error(f'cancel order error. :[{e}]')
            return [False, tmp_driver]

        self.log.info('cancel_order() called.')
        return [True, driver]




    def order_trail(self, driver, stop_price, stop_pips, trail_range='010'):
        """
        * トレールでの決済注文を発注
          ポジションレートから利益が出てから使用する
        * param
            driver:webdriver('ディーリングボードのフレームに設定した状態')
            stop_price:str 逆指値(円)
            stop_pips:str  逆指値(pips)
            trail_range:str (default '010' SBIの最低レンジ)
        * return
            array-like
            *成功時
                True:bool
                driver:webdriver('ディーリングボードのフレームに設定した状態')
            *失敗時
                False:bool
                driver:引数に渡された状態
        """

        self.log.info('order_stop_loss_trail() called.')
        tmp_driver = driver

        try:
            # 照会をクリック
            driver.switch_to.default_content()
            frame_top = driver.find_element_by_css_selector('#frame_top')
            driver.switch_to.frame(frame_top)
            driver.find_element_by_css_selector('#watch').click()
            self.log.info(f'syoukai link click done.')

            # 決済画面に遷移
            driver.switch_to.default_content()
            frame_trade = driver.find_element_by_css_selector('#frame_trade')
            driver.switch_to.frame(frame_trade)
            url = driver.find_element_by_xpath("//*[@id='Label1']/table[2]/tbody/tr[3]/td[9]/a").get_attribute('href')
            driver.get(url)
            self.log.info('payment page transition done.')

            # ストップロスをトレールで入力し発注
            order_tyep_elem = driver.find_element_by_xpath("//select[@name='order']")
            order_type_drop = Select(order_tyep_elem)
            order_type_drop.select_by_visible_text('トレール')
            driver.find_element_by_css_selector('#sasine1_1').send_keys(stop_price)
            driver.find_element_by_xpath("//input[@name='sasine1_2']").send_keys(stop_pips)
            driver.find_element_by_css_selector('#trail1_1').send_keys(0)
            driver.find_element_by_xpath("//input[@name='trail1_2']").send_keys(trail_range)
            driver.find_element_by_xpath("//input[@type='PASSWORD' and @name='orderpass']").send_keys(tpassword)
            driver.find_element_by_xpath("//button[@class='execute' and @type='submit']").click()
            driver.find_element_by_xpath("//button[@class='execute' and @type='submit']").click()
            try:
                error_msg = driver.find_element_by_xpath("//div[@class='error_str']").text
            except NoSuchElementException as e:
                pass

            self.log.info('trail order done.')

            # FXトップページに戻る
            driver.get('https://fx.sbisec.co.jp/forex/trade/client/index.aspx')
            self.log.info(f'fx top page transition done.')

            # ディーリングボードのフレームに戻る
            frame_price = driver.find_element_by_css_selector('#frame_price')
            driver.switch_to.frame(frame_price)
            self.log.info(f'dealing board frame set up done.')
        except Exception as e:
            self.log.error(f'trail set error. :[{e}]')
            return [False, tmp_driver]


        self.log.info('order_stop_loss_trail() done.')
        return [True, driver]



    def is_tategyoku(self, driver):
        """
        * 建玉があるか確認する
        * param
            driver:webdriver (ディーリングボードに設定されたドライバ)
        * return
            array-like
            * 建玉がある場合
                True:bool
                driver:webdriver (ディーリングボードに設定されたドライバ)
            * 建玉がない場合
                False:bool
                driver:webdriver (ディーリングボードに設定されたドライバ)
        """
        self.log.info(f'is_tategyoku() called.')
        tmp_driver = driver
        # frameを一旦戻してFXトップページにスイッチ
        driver.switch_to.default_content()

        # frame_topにスイッチ
        frame_top = driver.find_element_by_css_selector('#frame_top')
        driver.switch_to.frame(frame_top)

        # 「照会」をクリックし建玉サマリにフレームをスイッチ
        driver.find_element_by_css_selector('#watch').click()
        driver.switch_to.default_content()
        frame_trade = driver.find_element_by_css_selector('#frame_trade')
        driver.switch_to.frame(frame_trade)
        try:
            driver.find_element_by_css_selector('.error_str')
        except NoSuchElementException as e:
            self.log.info(f'not found tategyoku.') 
            driver = tmp_driver
            return [True, driver]
             
        driver = tmp_driver
        self.log.info(f'tategyoku exist.')
        self.log.info(f'is_tategyoku() done.')
        return [False, driver]
        


    def scrap_macd_stoch_close(self, cycle_minute=1, sleep_sec=0.1, n_row=10, trv_time_lag=np.arange(5, 10), headless=True):
        """
        * tradingviewの自作のチャートから1分足のopen,high,low,close, macd,ストキャスティクスの値を取得する
          →https://jp.tradingview.com/chart/wTJWkxIA/
           !!!必ずトレーディングビューの分足とcycle_minuteの時間を合わせること
           !!!データウィンドウから時刻を取得しているため,データウィンドウも表示されてる状態でが保存されていることが前提
        * param
            cycle_minute:int (default 1) スクレイピングする間隔（分）※1時間の場合は60で設定
                         ただし1, 5, 15, 30, 60のみ設定可能
            sleep_sec:int (default 5) sleep秒 
            n_row:int (default 10) データを保持する行数。超えると古いものから削除される
            trv_time_lag:array-like  ビットフライヤーの値がtradingviewに反映されるまでラグが発生する場合がある
            　　　　　　　　　そのためチャートに反映されるまでスクレイピングしないよう停止(スリープ)させ
                              正しいインジケーターを取得させるようにする
            headless:bool Trueの場合はヘッドレスブラウザで実行、Falseの場合は通常のブラウザで実行
        * return 
            なし
                データ取得成功 :self.ind_dfにデータ時系列で降順で格納される
                データ取得失敗 :下記例外を発生させ,終了する
                                CloseMacdStochScrapGetError
        """

        # スクレイピングサイクルを設定
        if cycle_minute == 1:
            interval_minute_list = np.arange(0, 60, 1)
        elif cycle_minute == 5:
            interval_minute_list = np.arange(0, 60, 5)
        elif cycle_minute == 15:
            interval_minute_list = np.arange(0, 60, 15)
        elif cycle_minute == 30:
            interval_minute_list = np.arange(0, 60, 30)
        elif cycle_minute == 60:
            interval_minute_list = np.array([0])
        else:
            raise CloseMacdStochScrapGetError(f'invalid argument cycle_minute')
            self.log.error('invalid argument cycle_minute')
            sys.exit(1)

        self.log.info(f'scrap_macd_stoch_close() called. cycle_minute:[{cycle_minute}]')

        # ブラウザ立ち上げ
        try:
            options = webdriver.ChromeOptions()
            if headless == True:
                options.add_argument('--headless')
            # セッションが切れないようにディレクトリにchrome関連のデータを保存するよう指定
#            options.add_argument('user-data-dir=chrome_scrap')
            driver = webdriver.Chrome(options=options)
            # jsが反映されるまで10秒待機(getより前に実施)
            driver.implicitly_wait(10)
            driver.get('https://jp.tradingview.com/chart/wTJWkxIA/')
            driver.set_window_size(1200, 900)
            # jsが反映されるまで待機
#            time.sleep(15)
            self.log.info(f'accessed tradingview site done')

            # データウィンドウクリック
            button = driver.find_elements_by_css_selector('.button-DABaJZo4.isTab-DABaJZo4.isGrayed-DABaJZo4.apply-common-tooltip.common-tooltip-vertical')
            loc = button[3].location
            x, y = loc['x'], loc['y']
            actions = ActionChains(driver)
            actions.move_by_offset(x, y)
            actions.click()
            actions.perform()
            self.log.info(f'data-window click done')

            # マウスオーバー
            chart = driver.find_element_by_class_name('chart-gui-wrapper')
            actions = ActionChains(driver)
            actions.move_to_element(chart)
            actions.move_by_offset(310, 100)
            actions.perform()
            self.log.info(f'mouse over done')

        except Exception as e:
            self.log.critical(f'{e}')
            driver.quit()
            raise CloseMacdStochScrapGetError(f'browser set error :[{e}]')

        self.log.info(f'browser set up done')
            
        #----------------------------------------------------------------------------------
        # スクレイピングのサイクルとTradingviewの分足があっていなければ例外発生し停止させる
        #----------------------------------------------------------------------------------
        while True:
            try:
                title_attr = driver.find_element_by_class_name('titleWrapper-2KhwsEwE').text
                title_minute = int(title_attr.split('\n')[1])
                if cycle_minute != title_minute:
                    raise CloseMacdStochScrapGetError(f'cycle_minute and tradingview minute not match')
                    self.log.critical(f'cycle_minute and tradingview minute not match')
                    sys.exit(1)
            except Exception as e:
                self.log.critical(f'{e}')
                driver.quit()
                raise CloseMacdStochScrapGetError(f'cycle_minute and tradingview minute not match')
                sys.exit(1)



            # スクレイピング
            # closeの時刻をもとに同時刻以内に複数回ループすることを防ぐ
            close_time_tmp = '' 
            while True:
                self.log.debug(f'scraping start')
                while True:
                    now_time = datetime.datetime.now()
                    # tredingviewでcloseがチャートに反映にタイムラグが生じることを考慮し秒で調整する
                    if now_time.minute in interval_minute_list and now_time.second in trv_time_lag:
                        break
                    time.sleep(sleep_sec) 
                    continue

                # トレーディングビューのデータウィンドウからマウスオーバーしたローソクの時刻を取得
                # 値が取得できたりできなかったりするためtry文で暫定対応
                try:
                    close_time_array = driver.find_elements_by_css_selector('.chart-data-window-item-value > span')
                    close_time_ymd   = close_time_array[0].text
                    close_time_hm    = close_time_array[1].text
                except Exception as e:
                    self.log.warning('cant get close time. retry')
                    continue


                # 同時刻の場合はcontinue
                if close_time_hm == close_time_tmp:
                    self.log.debug(f'same close time. close_time_hm:[{close_time_hm}] close_time_tmp:[{close_time_tmp}]')
                    continue
                close_time_tmp = close_time_hm


                # closeの時刻をstring型からdatetimeオブジェクトに変換
                close_time_ymd_list = close_time_ymd.split('-')
                close_time_ymd_list = [int(t) for t in close_time_ymd_list]
                close_time_hm_list  = close_time_hm.split(':')
                close_time_hm_list  = [int(t) for t in close_time_hm_list]
                close_time = datetime.datetime(close_time_ymd_list[0], close_time_ymd_list[1], close_time_ymd_list[2],\
                        close_time_hm_list[0], close_time_hm_list[1])

                try:    
                    # CSSセレクタで指定のクラスでelementを取得
                    ind_array = driver.find_elements_by_css_selector('.valuesWrapper-2KhwsEwE')
                    self.log.info(f'got elements :[{ind_array}]')


                    # サイクル通りにデータが取得できていない場合は例外を発生させる(トレーディングビューにラグがあるため）
                    
                    if cycle_minute == 1:
                        # 1分足の場合はデフォルト引数により最長で70秒かかるため70秒より長いとアウト
                        # 頻発するので例外は発生させずcontinue
                        if now_time - close_time > datetime.timedelta(seconds=70):
                            self.log.warning(f'not get collect cycle minute. retry | now_time:[{now_time} close_time:[{close_time}]')
                            continue
                    elif cycle_minute == 5:
                        if close_time.minute not in np.arange(0, 60, 5):
                            raise CloseMacdStochScrapGetError(f'not get collect cycle minute')
                            self.log.error(f'not get collect cycle minute')
                            sys.exit(1)
                    elif cycle_minute == 15:
                        if close_time.minute not in np.arange(0, 60, 15):
                            raise CloseMacdStochScrapGetError(f'not get collect cycle minute')
                            self.log.error(f'not get collect cycle minute')
                            sys.exit(1)
                    elif cycle_minute == 30:
                        if close_time.minute not in np.arange(0, 60, 30):
                            raise CloseMacdStochScrapGetError(f'not get collect cycle minute')
                            self.log.error(f'not get collect cycle minute')
                            sys.exit(1)
                    elif cycle_minute == 60:
                        if close_time.minute not in  np.array([0]):
                            raise CloseMacdStochScrapGetError(f'not get collect cycle minute')
                            self.log.error(f'not get collect cycle minute')
                            sys.exit(1)


                    # レートの値を取得
                    rate_str = ind_array[0].text
                    self.log.debug(f'rate_str : [{rate_str}]')
                    open_rate  = int(rate_str.split('始値')[1].split('高値')[0])
                    high_rate  = int(rate_str.split('高値')[1].split('安値')[0])
                    low_rate   = int(rate_str.split('安値')[1].split('終値')[0])
                    close_rate_str = rate_str.split('終値')[1]
                    # マイナスは全角で表記されているため全角指定
                    close_rate = int(re.split('[+|−|\s]', close_rate_str)[0])
                    rate_array = [open_rate, high_rate, low_rate, close_rate] 


                    # MACDとストキャスティクスをリストに変換(MACDはマイナスが全角表記になっているためreplaceで置換しておく
                    # ストキャスティクスもなぜか0.0の時に全角のマイナス表記がたまにあるため置換しておく
                    macd_array  = ind_array[1].text.replace('−', '-').split('\n')
                    stoch_array = ind_array[2].text.replace('−', '-').split('\n')
                    self.log.info(f'scraped to array : [macd {macd_array}, stoch {stoch_array}]')

                    # 文字列を数値へ変換
                    macd_array  = [int(data) for data in macd_array]
                    stoch_array = [float(data) for data in stoch_array]
                    self.log.info(f'converted numeric : [macd {macd_array}, stoch {stoch_array}]')

                    # 取得時刻をリストに追加
                    rate_array.append(close_time)
                    macd_array.append(close_time)
                    stoch_array.append(close_time)

                    # numpyのndarrayに変換
                    rate_array  = np.array(rate_array)
                    macd_array  = np.array(macd_array) 
                    stoch_array = np.array(stoch_array)

                    #------------------------------------------------------------------------
                    # tradingview側でHTMLの変更があった場合に備えて
                    # 値に制限のある ストキャスティクスの値でスクレイピングの異常を検知する
                    #------------------------------------------------------------------------
                    if ((stoch_array[:2] < 0.00).any() == True) or ((stoch_array[:2] > 100.00).any() == True):
                        self.log.critical(f'sotch value invalid : [{stoch_array}]')
                        raise CloseMacdStochScrapGetError(f'sotch value invalid : [{stoch_array}]')
                except Exception as e:
                    self.log.critical(f'cant get macd stoch data : [{e}]')
                    driver.quit()
                    self.init_memb()
                    raise CloseMacdStochScrapGetError(f'scraping error : [{e}]')
                except KeyboardInterrupt:
                    driver.quit()
                    sys.exit(1)

                

                # データフレームとして作成(時系列では降順)
                bitf_rate_df_tmp = pd.DataFrame(rate_array.reshape(1, 5), columns=['open', 'high', 'low', 'close', 'close_time'])    
                macd_df_tmp     = pd.DataFrame(macd_array.reshape(1, 4), columns=['hist', 'macd', 'signal', 'close_time'])    
                stoch_df_tmp    = pd.DataFrame(stoch_array.reshape(1, 3), columns=['pK', 'pD', 'close_time'])   

                self.bitf_rate_df = pd.concat([self.bitf_rate_df, bitf_rate_df_tmp], ignore_index=True)
                self.macd_df  = pd.concat([self.macd_df, macd_df_tmp], ignore_index=True)
                self.stoch_df = pd.concat([self.stoch_df, stoch_df_tmp], ignore_index=True)   
                self.log.info(f'memb registed done : [macd {macd_array}, stoch {stoch_array}]')

                # ファイル書き出し
                try:
                    self.write_csv_dataframe(df=self.bitf_rate_df, path=CLOSE_RATE_FILE_PATH + self.bitf_rate_filename)
                    self.write_csv_dataframe(df=self.macd_df,      path=MACD_FILE_PATH       + self.macd_filename)
                    self.write_csv_dataframe(df=self.stoch_df,     path=STOCH_FILE_PATH      + self.stoch_filename)
                except Exception as e:
                    self.log.critical(f'cant write macd stoch data : [{e}]')
                    driver.quit()
                    self.init_memb()
                    raise CloseMacdStochScrapGetError(f'cant open headless browser : [{e}]')
                self.log.info(f'dataframe to csv write done')


                # データフレームが一定行数超えたら古い順から削除
                if len(self.bitf_rate_df) > n_row: self.bitf_rate_df.drop(index=self.bitf_rate_df.index.min(), inplace=True)
                if len(self.macd_df)      > n_row: self.macd_df.drop(index=self.macd_df.index.min(), inplace=True)
                if len(self.stoch_df)     > n_row: self.stoch_df.drop(index=self.stoch_df.index.min(), inplace=True)

# test
                print(self.bitf_rate_df)
                print(self.macd_df)
                print(self.stoch_df)
# test
                self.log.info(f'scraping 1cycle done')


    def scrap_macd_stoch_stream(self, sleep_sec=3, n_row=20):
        """
        *tradingviewの自作のチャートからmacd,ストキャスティクスの値を取得する
         →https://jp.tradingview.com/chart/wTJWkxIA/
        * param
            sleep_sec:int (default 3) スリープ時間(秒)
            n_row:int (default 20) 作成したdataframeを保持する行数.超えると削除
        * return
            無し
            取得成功
                self.macd_dfに取得時刻, macd, signal, ヒストグラムを格納
                self.stoch_dfに取得時刻,%K, %Dの値を格納
            取得失敗: 例外を発生させる
        """
        self.log.info(f'scrap_macd_stoch() called')

        # ヘッドレスブラウザでtradingviewのURLを開く
        try:
            options = webdriver.ChromeOptions()
            options.add_argument('--headless')
            # セッションが切れないようにディレクトリにchrome関連のデータを保存するよう指定
            options.add_argument('user-data-dir=chrome_scrap_stream')
            driver = webdriver.Chrome(options=options)
            driver.get('https://jp.tradingview.com/chart/wTJWkxIA/')
    
            # チャートのJSが完了するまで待機
            time.sleep(10)
        except Exception as e:
            driver.quit()
            self.log.critical(f'cant open headless browser : [{e}]')
            raise CloseMacdStochStreamScrapGetError(f'cant open headless browser : [{e}]')

        self.log.info(f'headless browser opend')

        # macd関連のデータ取得
        while True:
            try:
                # CSSセレクタで指定のクラスでelementを取得
                ind_array = driver.find_elements_by_css_selector('.valuesWrapper-2KhwsEwE')
                close_time = datetime.datetime.now()
                self.log.info(f'got elements :[{ind_array}]')

                # リストに変換(MACDはマイナスが全角表記になっているためreplaceで置換しておく
                # なぜかストキャスティクスも0.0のときに全角マイナス表記があるため置換
                macd_array  = ind_array[1].text.replace('−', '-').split('\n')
                stoch_array = ind_array[2].text.replace('−', '-').split('\n')
                self.log.info(f'scraped to array : [macd {macd_array}, stoch {stoch_array}]')

                # 文字列を数値へ変換
                macd_array  = [int(data) for data in macd_array]
                stoch_array = [float(data) for data in stoch_array]
                self.log.info(f'converted numeric : [macd {macd_array}, stoch {stoch_array}]')

                # 取得時刻をリストに追加
                macd_array.append(close_time)
                stoch_array.append(close_time)

                # numpyのndarrayに変換
                macd_array  = np.array(macd_array) 
                stoch_array = np.array(stoch_array)

                #------------------------------------------------------------------------
                # tradingview側でHTMLの変更があった場合に備えて
                # 値に制限のある ストキャスティクスの値でスクレイピングの異常を検知する
                #------------------------------------------------------------------------
                if ((stoch_array[:2] < 0.00).any() == True) or ((stoch_array[:2] > 100.00).any() == True):
                    self.log.critical(f'sotch value invalid : [{stoch_array}]')
                    raise CloseMacdStochStreamScrapGetError(f'sotch value invalid : [{stoch_array}]')
            except Exception as e:
                self.log.critical(f'cant get macd stoch data : [{e}]')
                driver.quit()
                self.init_memb()
                raise CloseMacdStochStreamScrapGetError(f'cant open headless browser : [{e}]')
            except KeyboardInterrupt:
                driver.quit()
                self.init_memb()
                sys.exit(1)

            # データフレームとして作成しメンバーに登録(時系列では降順として作成)
            macd_stream_df_tmp   = pd.DataFrame(macd_array.reshape(1, 4), columns=['hist', 'macd', 'signal', 'close_time'])    
            stoch_stream_df_tmp  = pd.DataFrame(stoch_array.reshape(1, 3), columns=['pK', 'pD','close_time'])   
            self.macd_stream_df  = pd.concat([macd_stream_df_tmp, self.macd_stream_df], ignore_index=True)
            self.stoch_stream_df = pd.concat([stoch_stream_df_tmp, self.stoch_stream_df], ignore_index=True)   
            self.log.info(f'memb registed done : [macd {macd_array}, stoch {stoch_array}]')


            del(macd_stream_df_tmp)
            del(stoch_stream_df_tmp)

            # メモリ削減のため古いデータを削除
            if len(self.macd_stream_df)  == n_row:self.macd_stream_df.drop(index=self.macd_stream_df.index.max(), inplace=True)
            if len(self.stoch_stream_df) == n_row:self.stoch_stream_df.drop(index=self.stoch_stream_df.index.max(), inplace=True)

            # ファイル書き出し
            try:
                self.write_csv_dataframe(df=self.macd_stream_df, path=MACD_STREAM_FILE_PATH + self.macd_stream_filename)
                self.write_csv_dataframe(df=self.stoch_stream_df, path=STOCH_STREAM_FILE_PATH + self.stoch_stream_filename)
            except Exception as e:
                self.log.critical(f'cant write macd stoch data : [{e}]')
                driver.quit()
                self.init_memb()
                raise CloseMacdStochStreamScrapGetError(f'cant open headless browser : [{e}]')

            time.sleep(sleep_sec)
            self.log.info(f'scraping 1cycle done')




    async def positioner_stoch(self, row_thresh=20, hight_thresh=80, sleep_sec=1, n_row=5):
        """
        * ストキャスティクスの値によりポジション判定を行う
          スクレイピングとは別プロセスなのでスクレイピングで出力したファイルを読み込み判定する
          * 基本方針
            閾値をクリアした値でGX,DXでポジション判定を行う。
            閾値をクリアしななかった値でGX,DXが形成された場合はミニポジションとして逆指値を動かす判定とする
            ポジション情報はpandasに保持しファイル出力しない。ログにはポジション情報が出力される
            *格納するポジションの文字列の例
             ロング:'LONG'
             ショート:'SHORT'
             ミニロング:'MINILONG'
             ミニショート'MINISHORT'
        * param
            row_thresh:int (default 20) ストキャスティクスのロング目線でのライン閾値
            hight_thresh:int (default 80)ストキャスティクスのショート目線での閾値
            dlt_se:int (default 180) 上記の閾値を超えてからGX、DXが生じるまでの秒。この時間未満だと判定しない
            sleep_sec:int (default 1) スリープ秒
            n_row:int (default 5) ポジションデータ保持行数。超えたら古いものから削除
        * return
            なし
                ポジションが確定すると下記データフレームにポジション情報を格納する
                また、データフレームに格納された情報をcsvファイルとして書き出す
                self.pos_stoch_jdg_df
        """


        # 同時刻に複数回ループするのを防ぐためpkで判定する
        tmp_pK = 0.0

        while True:
            self.log.info(f'positioner_stoch() called')        

            # ストキャスティクスcloseデータ読み込み(close_timeはnumpyのdatetime型で指定)
            try:
                stoch_df = self.read_csv_dataframe(path=STOCH_FILE_PATH, filename=None, dtypes={'close_time':'np.datetime[64]'}) 
            except Exception as e:
                self.log.error(f'{e}')
                await asyncio.sleep(sleep_sec)
                continue
            self.log.info(f'stoch close data to dataframe done')        

            # ストキャスティクスcloseデータが未作成の場合
            if len(stoch_df) == 0:
                await asyncio.sleep(sleep_sec)
                continue

            # 最新(2行)のストキャスティクスを取得
            last_stoch_df = stoch_df.tail(n=2).reset_index(level=0, drop=True)
            self.log.info(f'last_stoch_df : [{last_stoch_df.to_json()}]')

            # 同じ値であればスリープしてスキップ
            if tmp_pK == last_stoch_df['pK'][0]:
                await asyncio.sleep(sleep_sec)
                continue
            tmp_pK = last_stoch_df['pK'][0]

            # ポジション判定処理
            # LONG目線
            if last_stoch_df['pK'][0] <= row_thresh:
                if last_stoch_df['pK'][0] < last_stoch_df['pD'][0]:
                    if last_stoch_df['pK'][1] > last_stoch_df['pD'][1]:
                        # GX 時系列では降順として作成
                        tmp_df = pd.DataFrame({'position':'LONG','pK':last_stoch_df['pK'][1],'pD':last_stoch_df['pD'][1],\
                                'stoch_jdg_timestamp':datetime.datetime.now()}, index=[0])
                        self.pos_stoch_jdg_df = pd.concat([tmp_df, self.pos_stoch_jdg_df], ignore_index=True)
                        self.log.info(f'position set LONG : tmp_df.to_json()')
           
            # SHORT目線
            elif last_stoch_df['pK'][0] >= hight_thresh:
                if last_stoch_df['pK'][0] > last_stoch_df['pD'][0]:
                    if last_stoch_df['pK'][1] < last_stoch_df['pD'][1]:
                        # DX 時系列では降順として作成
                        tmp_df = pd.DataFrame({'position':'SHORT','pK':last_stoch_df['pK'][1],'pD':last_stoch_df['pD'][1],\
                                'stoch_jdg_timestamp':datetime.datetime.now()}, index=[0])
                        self.pos_stoch_jdg_df = pd.concat([tmp_df, self.pos_stoch_jdg_df], ignore_index=True)
                        self.log.info(f'position set SHORT : tmp_df.to_json()')

            # MINILONG or MINISHORT
            else:
                if last_stoch_df['pK'][0] < last_stoch_df['pD'][0]:
                    if last_stoch_df['pK'][1] > last_stoch_df['pD'][1]:
                        # MINILONG 時系列では降順として作成
                        tmp_df = pd.DataFrame({'position':'MINILONG','pK':last_stoch_df['pK'][1],'pD':last_stoch_df['pD'][1],\
                                'stoch_jdg_timestamp':datetime.datetime.now()}, index=[0])
                        self.pos_stoch_jdg_df = pd.concat([tmp_df, self.pos_stoch_jdg_df], ignore_index=True)
                        self.log.info(f'position set MINILONG : tmp_df.to_json()')

                if last_stoch_df['pK'][0] > last_stoch_df['pD'][0]:
                    if last_stoch_df['pK'][1] < last_stoch_df['pD'][1]:
                        # MINISHORT 時系列では降順として作成
                        tmp_df = pd.DataFrame({'position':'MINISHORT','pK':last_stoch_df['pK'][1],'pD':last_stoch_df['pD'][1],\
                                'stoch_jdg_timestamp':datetime.datetime.now()}, index=[0])
                        self.pos_stoch_jdg_df = pd.concat([tmp_df, self.pos_stoch_jdg_df], ignore_index=True)
                        self.log.info(f'position set MINISHORT : tmp_df.to_json()')

            await asyncio.sleep(sleep_sec)

            # ポジション格納データフレームの行数が一定数超えたら古いものから削除
            if len(self.pos_stoch_jdg_df) > n_row:
                self.pos_stoch_jdg_df.drop(index=self.pos_stoch_jdg_df.index.max(), inplace=True)




    async def positioner_macd(self, hist_zero=100, sleep_sec=1, n_row=5):
        """
        * macdの情報によりポジション判定を行う
        * param
            hist_zero:int (default 100) 反発系での判定でヒストグラムの絶対値がこの閾値以下であればゼロとみなす
            sleep_sec:int (default 1) スリープ秒
            n_row:int (default 5) ポジション情報を保持するdataframeのレコード数。超えると古いものから削除する
        * return
            なし
            　ポジションが確定すると下記のメンバに格納する
              self.pos_macd_jdg_df
        """
        self.log.info(f'positioner_macd() called')

        # 同時刻に複数回ループするのを防ぐためにmacdを使用する
        tmp_macd = 0

        while True:

            # macdのcloseデータ読み込み(close_timeはnumpyのdatetime型で指定)
            try:
                macd_df = self.read_csv_dataframe(path=MACD_FILE_PATH, filename=None, dtypes={'close_time':'np.datetime[64]'}) 
            except Exception as e:
                self.log.error(f'{e}')
                await asyncio.sleep(sleep_sec)
                continue
            self.log.info(f'stoch close data to dataframe done')        

            if len(macd_df) < 4:
                await asyncio.sleep(sleep_sec)
                continue

            # 最新のmacd情報を取得（5行）
            tmp_macd_df = macd_df.tail(n=4).reset_index(level=0, drop=True)
            self.log.info(f'macd data : [{tmp_macd_df.to_json()}]')

            # macdが同じ値ならスリープしてスキップ
            if tmp_macd == tmp_macd_df['macd'][0]:
                await asyncio.sleep(sleep_sec)
                continue
            # macd更新
            tmp_macd = tmp_macd_df['macd'][0]


            macd0 = tmp_macd_df['macd'][0]           
            macd1 = tmp_macd_df['macd'][1]
            macd2 = tmp_macd_df['macd'][2]
            macd3 = tmp_macd_df['macd'][3]

            signal0 = tmp_macd_df['signal'][0]            
            signal1 = tmp_macd_df['signal'][1]
            signal2 = tmp_macd_df['signal'][2]
            signal3 = tmp_macd_df['signal'][3]

            hist0 = tmp_macd_df['hist'][0]
            hist1 = tmp_macd_df['hist'][1]
            hist2 = tmp_macd_df['hist'][2]
            hist3 = tmp_macd_df['hist'][3]


            # MACDとシグナルの増減率を計算
            km3 = (macd3 - macd2) / 1
            ks3 = (signal3 - signal2) / 1
            dvms = km3 / ks3

            rm3 = (macd3 - macd2) / abs(macd2)
            rs3 = (signal3 - signal2) / abs(signal2)
            rms3 = rm3 / rs3
# test
            print('----------')
            print(datetime.datetime.now())
            print(f'macd3 : {macd3}   | macd2  : {macd2}')
            print(f'sig3  : {signal3} | signal2: {signal2}')
            print(f'km3 : {km3}')
            print(f'ks3 : {ks3}')
            print(f'km3 / ks3 : {dvms}')
            print('-')
            print(f'rm3 : {rm3} | rs3 : {rs3}')
            print(f'rms3 : {rms3}')




            await asyncio.sleep(sleep_sec)
            continue
#            
#            # ポジション判定
#
#            #------------------
#            # GX (LONG)
#            #------------------
#            if macd0 < signal0 and macd2 > signal2:
#
#                # 時系列では降順として作成
#                if is_position == False:
#                    tmp_df = pd.DataFrame({'position':'LONG', 'macd_jdg_timestamp':datetime.datetime.now()}, index=[0])
#                    self.pos_macd_jdg_df = pd.concat([tmp_df, self.pos_macd_jdg_df], ignore_index=True)
#                    is_position = True
#                    self.log.info(f'position set LONG : pattern [GX]')
#            
#            #------------------
#            # DX (SHORT)
#            #------------------
#            elif macd0 > signal0 and macd2 < signal2:
#                # 時系列では降順として作成
#                if is_position == False:
#                    tmp_df = pd.DataFrame({'position':'SHORT', 'macd_jdg_timestamp':datetime.datetime.now()}, index=[0])
#                    self.pos_macd_jdg_df = pd.concat([tmp_df, self.pos_macd_jdg_df], ignore_index=True)
#                    is_position = True
#                    self.log.info(f'position set SHORT : pattern [DX]')
#
#            #-----------------------------
#            # シグナル上で上に反発（LONG）
#            #-----------------------------
#            elif ((macd1 < macd0) and (macd1 < macd2)) and ((hist0 > hist_zero) and (abs(hist1) < hist_zero) and (hist2 > hist_zero)):
#
#                # 時系列では降順として作成
#                if is_position == False:
#                    tmp_df = pd.DataFrame({'position':'LONG', 'macd_jdg_timestamp':datetime.datetime.now()}, index=[0])
#                    self.pos_macd_jdg_df = pd.concat([tmp_df, self.pos_macd_jdg_df], ignore_index=True)
#                    is_position = True
#                    self.log.info(f'position set LONG : pattern [rebound LONG]')
#
#            #-----------------------------
#            # シグナル上で下に反発（SHORT）
#            #-----------------------------
#            elif ((macd1 > macd0) and (macd1 > macd2)) and ((hist0 < -hist_zero) and (abs(hist1) < hist_zero) and (hist2 < -hist_zero)):
#
#                # 時系列では降順として作成
#                if is_position == False:
#                    tmp_df = pd.DataFrame({'position':'SHORT', 'macd_jdg_timestamp':datetime.datetime.now()}, index=[0])
#                    self.pos_macd_jdg_df = pd.concat([tmp_df, self.pos_macd_jdg_df], ignore_index=True)
#                    is_position = True
#                    self.log.info(f'position set SHORT : pattern [rebound SHORT]')
#            
#            #------------------------------
#            # GX間近(オリジナル指標を使用)
#            #------------------------------
#            elif ((hist0 < 0) and (hist1 < 0) and (hist2 < 0)) and ((kms1 > 0) and (kms2 <= kms_thresh)):
#                # 時系列では降順として作成
#                if is_position == False:
#                    tmp_df = pd.DataFrame({'position':'LONG', 'macd_jdg_timestamp':datetime.datetime.now()}, index=[0])
#                    self.pos_macd_jdg_df = pd.concat([tmp_df, self.pos_macd_jdg_df], ignore_index=True)
#                    is_position = True
#                    self.log.info(f'position set LONG : pattern [nearness GX]')
#
#            #------------------------------
#            # DX間近(オリジナル指標を使用)
#            #------------------------------
#            elif ((hist0 > 0) and (hist1 > 0) and (hist2 > 0)) and ((kms1 > 0) and (kms2 <= kms_thresh)):
#                # 時系列では降順として作成
#                if is_position == False:
#                    tmp_df = pd.DataFrame({'position':'SHORT', 'macd_jdg_timestamp':datetime.datetime.now()}, index=[0])
#                    self.pos_macd_jdg_df = pd.concat([tmp_df, self.pos_macd_jdg_df], ignore_index=True)
#                    is_position = True
#                    self.log.info(f'position set SHORT : pattern [nearness DX]')
#
#            else:
#                self.log.info('no position stat')
#                is_position = False
#
#
#            # ポジションデータが一定数超えたら古いものから削除
#            if len(self.pos_macd_jdg_df.index) > n_row:
#                self.pos_macd_jdg_df.drop(index=self.pos_macd_jdg_df.index.max(), inplace=True)
#
#            await asyncio.sleep(sleep_sec)
## test
#            print('----- macd -----')
#            print(self.pos_macd_jdg_df)



    async def positioner(self, path=POSITION_FILE_PATH, dlt_sec=185, n_row=3, sleep_sec=1):
        """
        * ストキャスティクスとMACDから判定されたポジションをもとに
        　最終のポジションを判定する。
        　この最終のポジションが確定しないとトレードしない
          ポジション確定した場合、指定されたディレクトリ配下にポジション情報をファイル名とした空ファイルを作成
        * param
            path=
            dlt_sec:int (default 180)
            n_row:int (default 3) ポジションデータ保持数。超えると古い順に削除される
            sleep_sec:int (default 1) スリープ秒
        * return
            なし
                ポジション確定の場合:self.pos_jdg_dfにポジション情報を格納し、
                                     指定されたディレクトリ配下にポジション情報をファイル名とした空ファイルを作成
                                     ファイルが作成できない場合は例外を発生させる
                                     * ファイル名の例
                                     {position}_{jdg_timestamp} ※「T」が入ることに注意
                                     LONG_2021-04-07T17:59:18.037976
                ポジション未確定の場合:pass
        * Exception
            PosJudgementError
        """
        # ライン通知用（テスト)
        is_line_ntfy = False

        # ポジション確定フラグ
        is_position = False

        while True:
            self.log.info('positioner() called') 
            await asyncio.sleep(sleep_sec)
            
            # 各ポジションを読み込む
            pos_macd  = self.pos_macd_jdg_df.head(n=1)
            pos_stoch = self.pos_stoch_jdg_df.head(n=1)
            
            # ポジションが未確定の場合はcontinue
            if pos_stoch['position'][0] == 'STAY':
                self.log.info(f'stoch no position stat : [{pos_stoch.to_json()}]')  
                continue

            if pos_macd['position'][0] == 'STAY':
                self.log.info(f'macd no position stat : [{pos_macd.to_json()}]')  
                continue
             
            # MACDとストキャスティクスのポジションでない場合はcontinue
            if pos_stoch['position'][0] != pos_macd['position'][0]:
                self.log.info(f"macd stoch not same position. macd :[{pos_stoch['position'][0]}] stoch :[{pos_macd['position'][0]}]")
                is_line_ntfy = False
                is_position = False 
                continue

            # 各ポジション判定時間が閾値を超えている場合はcontinue
            dlt_jdg_timestamp = pos_macd['jdg_timestamp'][0] - pos_stoch['jdg_timestamp'][0]
            self.log.debug(f'dlt_jdg_timestamp.seconds : [{dlt_jdg_timestamp.seconds}]')
            if dlt_jdg_timestamp.seconds > 0:
                if dlt_jdg_timestamp.seconds > dlt_sec:
                    self.log.info(f'time lag not satisfy. dlt_jdg_timestamp : [{dlt_jdg_timestamp}]')
                    is_line_ntfy = False
                    is_position = False
                    continue
            else:
                if dlt_jdg_timestamp.seconds < dlt_sec:
                    self.log.info(f'time lag not satisfy. dlt_jdg_timestamp : [{dlt_jdg_timestamp}]')
                    is_line_ntfy = False
                    is_position = False
                    continue

            # ポジションデータを指定されたディレクトリ配下に空ファイルとして作成
            filename = pos_stoch['position'][0] + '_' + datetime.datetime.now().isoformat()
#test
            if is_line_ntfy == False:
                self.line.send_line_notify(f'[INFO]\
                        ポジション確定しました。↓\
                        position : [{filename}]')
                is_line_ntfy = True
#test
            if is_position == False:
                if self.make_file(path=path, filename=filename) == False:
                    self.log.critical(f'cant make position file. path : [{path}]')
                    raise PosJudgementError(f'cant make position file. path : [{path}]')
                is_position = True
            continue



    def trader(self, size=0.01, n_pos=1, loss_cut_rate=40000):
        """
        * ポジションファイルを読み込みトレードを行う
        * param
            size:float or int ロット数(default 0.01)
            n_pos:int (default 1) ポジション数。これ以上のポジションは持たない
            loss_cut_rate:int (default 40000) 損切りライン。ただし相場の状況によっては最新レートに近づける,
                              あるいは成行で損切りする場合もある
                              （保有しているポジションとは逆のポジションがpositionerから指示が出た場合など)
        """
        while True:
            self.log.info('trader() called')
            
            # ポジションファイル読み込み




    def trade_common_resource(self):
        """
        * トレードで使うリソースを設定
        * param
            なし
        * return
            リソースのdict(下記参照)
        """
        apiKey    = os.environ['GMO_API_KEY']
        secretKey = os.environ['GMO_API_SKEY']
        method    = 'POST'
        endPoint  = 'https://api.coin.z.com/private'
        timestamp = '{0}000'.format(int(time.mktime(datetime.datetime.now().timetuple())))
        return {'API-KEY':apiKey, 'API-SKEY':secretKey, 'method':method, 'endPoint':endPoint, 'timestamp':timestamp} 




    def get_position_info(self):
        """
        * 約定情報、建玉情報、注文情報、余力情報、資産残高情報を参照するためのリソース設定
        * param
            なし
        * return
            リソースのdict(下記参照)
        """
        apiKey    = os.environ['GMO_API_KEY']
        secretKey = os.environ['GMO_API_SKEY']
        method    = 'GET'
        endPoint  = 'https://api.coin.z.com/private'
        timestamp = '{0}000'.format(int(time.mktime(datetime.datetime.now().timetuple())))
        return {'API-KEY':apiKey, 'API-SKEY':secretKey, 'method':method, 'endPoint':endPoint, 'timestamp':timestamp} 


    def order(self, symbol='BTC_JPY', size='0.01', executionType='LIMIT', timeInForce='FAS', priceRang=4000, losscutPrice=''):
        """
        * 注文を実行する
        * param
            symbol:str (default 'BTC_JPY') 対象通貨
            size:str (default '0.01') 通貨量
            executionType:str (default 'LIMIT'指値) 'MARKET'成行, 'STOP'逆指値
            timeInForce:str (default 'FAS' こちらを参照:https://api.coin.z.com/docs/#order)
            priceRang:int (default 4000) 最新レートから差し引く金額※最新レートから差し引いた金額が実際の注文金額となる
            losscutPrice:str (default '')GMO側でロスカットされる金額。空文字の場合自動で設定される
        * return
            is_order:bool
                True:注文成功
                False:注文失敗
        """
        pass




