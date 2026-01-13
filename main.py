#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XServer GAME 自动登录和续期脚本 (最终完整版)
功能：
1. 支持带账号密码的 SOCKS5 代理 (Selenium-Wire)
2. 启动前自动检测出口 IP
3. 精准提取旧到期时间 (基于 class="dateLimit")
4. 自动续期并发送 Telegram 通知
5. 生成 README.md 状态文件
"""

import time
import re
import datetime
import os
import sys
import requests
import json
from datetime import timezone, timedelta

# Selenium 相关依赖
from seleniumwire import webdriver  # 核心：使用 seleniumwire 的 webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# =====================================================================
#                          配置区域
# =====================================================================

# 代理配置 (支持 SOCKS5 账号密码)
# 格式: socks5://user:pass@ip:port
DEFAULT_PROXY = "socks5://vy5TKm1J93:r6CQ5Kl8yi@129.146.170.44:55555"
PROXY_URL = os.getenv("PROXY_URL") or DEFAULT_PROXY

# XServer 账户配置
LOGIN_EMAIL = os.getenv("XSERVER_EMAIL") or "fchp1997@gmail.com"
LOGIN_PASSWORD = os.getenv("XSERVER_PASSWORD") or "a63818399#"
TARGET_URL = "https://secure.xserver.ne.jp/xapanel/login/xmgame"

# Telegram 配置
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or "7425032752:AAH-txk6YNWCgwwxDqV4gghp4A_Khl9OQfc"
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or "6975394604"

# 浏览器配置
IS_GITHUB_ACTIONS = os.getenv("GITHUB_ACTIONS") == "true"
USE_HEADLESS = IS_GITHUB_ACTIONS or os.getenv("USE_HEADLESS", "true").lower() == "true"


# =====================================================================
#                          Telegram 推送模块
# =====================================================================

class TelegramNotifier:
    """Telegram 通知推送类"""
    
    def __init__(self):
        self.bot_token = TELEGRAM_BOT_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID
        self.enabled = bool(self.bot_token and self.chat_id)
        
    def send_message(self, message, parse_mode="HTML"):
        if not self.enabled:
            print("⚠️ Telegram 推送未启用")
            return False
        
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": parse_mode
            }
            requests.post(url, json=payload, timeout=10)
            print("✅ Telegram 消息已发送")
            return True
        except Exception as e:
            print(f"❌ Telegram 推送异常: {e}")
            return False

    def send_renewal_result(self, status, old_time, new_time=None):
        beijing_time = datetime.datetime.now(timezone(timedelta(hours=8)))
        timestamp = beijing_time.strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"<b>🎮 XServer GAME 续期通知</b>\n\n"
        message += f"🕐 运行时间: <code>{timestamp}</code>\n"
        message += f"🖥 服务器: <code>🇯🇵 Xserver(fchp1997)</code>\n\n"
        
        if status == "Success":
            message += f"📊 续期结果: <b>✅ 成功</b>\n"
            message += f"🕛 旧到期: <code>{old_time}</code>\n"
            message += f"🕡 新到期: <code>{new_time}</code>\n"
        elif status == "Unexpired":
            message += f"📊 续期结果: <b>ℹ️ 未到期 (无需续期)</b>\n"
            message += f"🕛 到期时间: <code>{old_time}</code>\n"
        elif status == "Failed":
            message += f"📊 续期结果: <b>❌ 失败</b>\n"
            message += f"🕛 旧到期: <code>{old_time}</code>\n"
        else:
            message += f"📊 续期结果: <b>❓ 未知</b>\n"
            
        return self.send_message(message)


# =====================================================================
#                          Selenium 自动化主类
# =====================================================================

class XServerBot:
    def __init__(self):
        self.driver = None
        self.wait = None
        self.old_expiry_time = "Unknown"
        self.new_expiry_time = "Unknown"
        self.renewal_status = "Unknown" # Success, Unexpired, Failed, Unknown
        self.telegram = TelegramNotifier()

    def init_browser(self):
        """初始化带 SOCKS5 代理认证的浏览器"""
        print(f"🔧 初始化浏览器，代理: {PROXY_URL}")
        
        # 1. Selenium-Wire 代理配置
        sw_options = {
            'proxy': {
                'http': PROXY_URL,
                'https': PROXY_URL,
                'no_proxy': 'localhost,127.0.0.1'
            }
        }

        # 2. Chrome 选项
        chrome_options = Options()
        if USE_HEADLESS:
            chrome_options.add_argument("--headless=new")
        
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")
        
        # 3. 启动
        try:
            self.driver = webdriver.Chrome(options=chrome_options, seleniumwire_options=sw_options)
            self.wait = WebDriverWait(self.driver, 15) # 全局默认等待15秒
            return True
        except Exception as e:
            print(f"❌ 浏览器启动失败: {e}")
            return False

    def close(self):
        if self.driver:
            self.driver.quit()
            print("👋 浏览器已关闭")

    def safe_click(self, by, value, desc="元素"):
        """安全点击辅助函数"""
        try:
            print(f"🔍 正在查找: {desc}...")
            element = self.wait.until(EC.element_to_be_clickable((by, value)))
            # 滚动到元素可见
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            time.sleep(1)
            element.click()
            print(f"✅ 已点击: {desc}")
            return True
        except Exception as e:
            print(f"❌ 点击失败 [{desc}]: {e}")
            return False

    def check_proxy_ip(self):
        """检测当前出口 IP"""
        try:
            print("🌐 正在检测出口 IP (访问 api.ipify.org)...")
            self.driver.get("https://api.ipify.org?format=json")
            
            # 等待 body 内容
            body_element = self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            ip_text = body_element.text
            
            try:
                # 尝试解析 JSON
                ip_data = json.loads(ip_text)
                ip = ip_data.get('ip', 'Unknown')
            except:
                ip = ip_text
                
            print(f"✅ 当前出口 IP: {ip}")
            print(f"----------------------------------------")
            return True
        except Exception as e:
            print(f"⚠️ IP 检测失败 (可能是超时或代理不通): {e}")
            return True 

    def login(self):
        """执行登录流程"""
        try:
            print(f"🚀 访问登录页: {TARGET_URL}")
            self.driver.get(TARGET_URL)
            
            # 等待邮箱输入框
            email_input = self.wait.until(EC.presence_of_element_located((By.NAME, "memberid")))
            pass_input = self.driver.find_element(By.NAME, "user_password")
            submit_btn = self.driver.find_element(By.CSS_SELECTOR, "input[type='submit']")
            
            # 输入信息
            print("⌨️ 输入账号密码...")
            email_input.clear()
            email_input.send_keys(LOGIN_EMAIL)
            time.sleep(1)
            pass_input.clear()
            pass_input.send_keys(LOGIN_PASSWORD)
            time.sleep(1)
            
            # 提交
            submit_btn.click()
            print("🖱️ 提交登录表单...")
            
            # 验证登录结果
            self.wait.until(EC.url_contains("xmgame"))
            print("✅ 登录成功，当前 URL:", self.driver.current_url)
            return True
            
        except Exception as e:
            print(f"❌ 登录失败: {e}")
            self.driver.save_screenshot("login_error.png")
            return False

    def navigate_to_game_panel(self):
        """跳转到游戏管理面板"""
        try:
            # 查找"ゲーム管理"
            if self.safe_click(By.XPATH, "//a[contains(text(), 'ゲーム管理')]", "ゲーム管理按钮"):
                time.sleep(3)
                # 处理中间跳转页面 (jumpvps)
                if "jumpvps" in self.driver.current_url:
                    print("🔄 检测到跳转页，等待...")
                    self.wait.until(EC.url_contains("xmgame/game/index"))
                return True
            return False
        except Exception as e:
            print(f"❌ 导航到面板失败: {e}")
            return False

    def check_and_renew(self):
        """核心业务：检查时间并续期"""
        try:
            print("🕒 获取服务器信息...")
            
            # ================= [关键] 日期提取逻辑 =================
            try:
                # 策略1: 精准定位 (基于 class="dateLimit")
                try:
                    date_limit_element = self.driver.find_element(By.CLASS_NAME, "dateLimit")
                    date_text = date_limit_element.text.strip()
                    print(f"🔍 找到 dateLimit 元素文本: {date_text}")
                    
                    match = re.search(r'(\d{4}-\d{2}-\d{2})', date_text)
                    if match:
                        self.old_expiry_time = match.group(1)
                        print(f"📅 成功提取旧到期时间: {self.old_expiry_time}")
                    else:
                        raise ValueError("正则未匹配到日期")
                        
                except Exception:
                    # 策略2: 全文模糊搜索 (保底)
                    print("⚠️ 精准定位失败，尝试全文搜索...")
                    body_text = self.driver.find_element(By.TAG_NAME, "body").text
                    
                    # 尝试匹配 "(2026-01-16まで)" 格式
                    match = re.search(r'\((\d{4}-\d{2}-\d{2})[^\)]*まで\)', body_text)
                    if not match:
                         # 尝试匹配 "2026-01-16"
                        match = re.search(r'(\d{4}-\d{2}-\d{2})', body_text)
                    
                    if match:
                        self.old_expiry_time = match.group(1)
                        print(f"📅 全文提取到时间: {self.old_expiry_time}")
                    else:
                        print("⚠️ 彻底无法找到日期信息")

            except Exception as e:
                print(f"⚠️ 时间解析模块出错: {e}")
            # ==========================================================

            # 点击 "期限延長"
            if not self.safe_click(By.XPATH, "//a[contains(text(), '期限延長')]", "期限延长入口"):
                return False

            # 检查是否有续期限制
            print("🔍 检查续期限制...")
            page_source = self.driver.page_source
            if "24時間を切るまで" in page_source and "延長は行えません" in page_source:
                print("ℹ️ 提示: 剩余时间大于24小时，无需续期。")
                self.renewal_status = "Unexpired"
                return True

            # 开始续期流程
            print("🚀 满足续期条件，开始续期...")
            
            # Step 1: 期限を延長する (面板页)
            if not self.safe_click(By.XPATH, "//a[contains(text(), '期限を延長する')]", "确认续期按钮(Step 1)"):
                self.renewal_status = "Failed"
                return False

            # Step 2: 確認画面に進む (Input页)
            self.wait.until(EC.url_contains("/extend/input"))
            if not self.safe_click(By.CSS_SELECTOR, "button[type='submit']", "确认画面按钮(Step 2)"):
                self.renewal_status = "Failed"
                return False

            # Step 3: 最终提交 (Conf页)
            self.wait.until(EC.url_contains("/extend/conf"))
            
            # 尝试抓取新到期时间
            try:
                try:
                    new_time_el = self.driver.find_element(By.XPATH, "//th[contains(text(),'延長後の期限')]/following-sibling::td")
                    self.new_expiry_time = new_time_el.text.strip()
                except:
                    src = self.driver.page_source
                    m = re.search(r'延長後の期限.*?(\d{4}-\d{2}-\d{2} \d{2}:\d{2})', src, re.DOTALL)
                    if m: self.new_expiry_time = m.group(1)
                print(f"📅 预计新到期日: {self.new_expiry_time}")
            except:
                pass

            # 提交
            if not self.safe_click(By.XPATH, "//button[@type='submit' and contains(text(), '期限を延長する')]", "最终提交按钮(Step 3)"):
                self.renewal_status = "Failed"
                return False

            # 验证结果 (Do页)
            self.wait.until(EC.url_contains("/extend/do"))
            if "期限を延長しました" in self.driver.page_source:
                print("🎉 续期成功！")
                self.renewal_status = "Success"
                return True
            else:
                print("❌ 未检测到成功提示")
                self.renewal_status = "Failed"
                return False

        except Exception as e:
            print(f"❌ 续期流程异常: {e}")
            self.renewal_status = "Failed"
            self.driver.save_screenshot("renew_error.png")
            return False

    def generate_readme(self):
        """生成状态文件"""
        beijing_time = datetime.datetime.now(timezone(timedelta(hours=8)))
        current_time = beijing_time.strftime("%Y-%m-%d %H:%M:%S")
        
        content = f"**最后运行时间**: `{current_time}`\n\n"
        content += "**运行结果**: <br>\n"
        content += "🖥️服务器:`🇯🇵Xserver(fchp1997)`<br>\n"
        
        status_icon = {
            "Success": "✅Success",
            "Unexpired": "ℹ️Unexpired",
            "Failed": "❌Failed",
            "Unknown": "❓Unknown"
        }
        
        content += f"📊续期结果:{status_icon.get(self.renewal_status, 'Unknown')}<br>\n"
        content += f"🕛️旧到期时间: `{self.old_expiry_time}`<br>\n"
        if self.new_expiry_time != "Unknown":
            content += f"🕡️新到期时间: `{self.new_expiry_time}`<br>\n"
            
        with open("README.md", "w", encoding="utf-8") as f:
            f.write(content)
        print("📝 README.md 已更新")

    def run(self):
        """主运行入口"""
        if not self.init_browser():
            return

        try:
            # 1. 验证 IP
            self.check_proxy_ip()

            # 2. 执行登录和业务
            if self.login():
                if self.navigate_to_game_panel():
                    self.check_and_renew()
            
            # 3. 结果处理
            self.generate_readme()
            self.telegram.send_renewal_result(
                self.renewal_status, 
                self.old_expiry_time, 
                self.new_expiry_time
            )
            
        finally:
            self.close()

if __name__ == "__main__":
    print("="*40)
    print("   XServer Auto Renew (Selenium-Wire)")
    print("="*40)
    
    bot = XServerBot()
    bot.run()
