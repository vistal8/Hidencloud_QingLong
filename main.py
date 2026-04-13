# -*- coding: utf-8 -*-
"""
HidenCloud 自动续期 - 青龙面板适配版（完整版）
https://github.com/vistal8/Hidencloud_QingLong/
依赖：cloudscraper beautifulsoup4 requests
Hidencloud 青龙面板续期脚本
创建青龙变量HIDEN_COOKIE
优化cookies 仅需要remember_web开头的cookies 第一次默认读取HIDEN_COOKIE第二次默认读取存储在本地的config.json的cookies 建议每6-10小时运行一次
全部本地存储，建议文件夹内运行来保证路径清洁，避免配置文件被其他脚本覆盖
增加wxpusher推送变量：WXPUSHER_APP_TOKEN WXPUSHER_UID 建议每个ip跑一个账号，不要用代理.避免被多账号检测！检测到就需要刷脸！！！而且成功率不高！
青龙任务命令：task HidenCloud.py
"""

import os
import sys
import time
import json
import random
import re
import requests
import cloudscraper
from bs4 import BeautifulSoup
from urllib.parse import urljoin, unquote


# ================= 配置加载（支持脚本同目录 config.json） =================
def load_config():
    """优先从脚本所在目录的 config.json 加载配置，兼容青龙格式和直接键值对格式"""
    # 获取脚本所在目录的绝对路径
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_config = os.path.join(script_dir, 'config.json')
    ql_old = '/ql/config/config.json'
    ql_new = '/ql/data/config/config.json'

    search_paths = [local_config, ql_old, ql_new]
    #print(f"[配置加载] 脚本目录: {script_dir}")
    #print(f"[配置加载] 尝试查找配置文件: {search_paths}")

    for path in search_paths:
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                # 判断是否为青龙格式：包含 "env" 字段且为列表
                if isinstance(config.get('env'), list):
                    env_list = config['env']
                    for item in env_list:
                        name = item.get('name')
                        value = item.get('value')
                        if name and value is not None:
                            if name not in os.environ:
                                os.environ[name] = str(value)
                    #print(f"[配置加载] ✅ 从 {path} 加载了 {len(env_list)} 个变量（青龙格式）")
                else:
                    # 直接键值对格式（如您的 config.json）
                    count = 0
                    for key, value in config.items():
                        if isinstance(value, str) and key not in os.environ:
                            os.environ[key] = value
                            count += 1
                    #print(f"[配置加载] ✅ 从 {path} 加载了 {count} 个变量（键值对格式）")
                return  # 加载成功后直接返回
            except Exception as e:
                print(f"[配置加载] ⚠️ 读取 {path} 失败: {e}")

    print("[配置加载] 未找到任何配置文件，将仅使用系统环境变量")

# 立即执行配置加载
load_config()

# ================= cookie_context 兼容层 =================
DEFAULT_COOKIE_DOMAIN = '.dash.hidencloud.com'
COOKIE_DOMAIN_OVERRIDES = {
    'cf_clearance': '.hidencloud.com',
}
# 只使用 remember_web cookie，其他 cookie 由 cloudscraper 自动处理
CRITICAL_COOKIE_NAMES = {
    # 'XSRF-TOKEN',
    # 'hidencloud_session',
    # 'cf_clearance',
    # 'hc_cf_turnstile',
}
CRITICAL_COOKIE_PREFIXES = (
    'remember_web_',
)

def _domain_for_cookie(name):
    return COOKIE_DOMAIN_OVERRIDES.get(name, DEFAULT_COOKIE_DOMAIN)

def _is_critical_cookie_name(name):
    if name in CRITICAL_COOKIE_NAMES:
        return True
    return any(name.startswith(prefix) for prefix in CRITICAL_COOKIE_PREFIXES)

def parse_seed_cookie_string(cookie_str):
    """只提取 remember_web cookie"""
    deduped = {}
    for item in cookie_str.split(';'):
        if '=' not in item:
            continue
        name, value = item.split('=', 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        # 只保留 remember_web 开头的 cookie
        if name.startswith('remember_web_'):
            deduped[name] = {
                'name': name,
                'value': value,
                'domain': _domain_for_cookie(name),
                'path': '/',
                'secure': True,
            }
    return list(deduped.values())

def _cookie_score(record):
    name = str(record.get('name') or '')
    domain = str(record.get('domain') or '')
    path = str(record.get('path') or '/')
    secure = 1 if record.get('secure') else 0
    preferred_domain = _domain_for_cookie(name)
    domain_match = 1 if preferred_domain in domain else 0
    non_empty_domain = 1 if domain else 0
    return (domain_match, non_empty_domain, len(path), secure)

def normalize_cookie_records(records):
    kept_by_name = {}
    changes = []
    ordered_passthrough = []
    for record in records:
        name = str(record.get('name') or '')
        if not _is_critical_cookie_name(name):
            ordered_passthrough.append(record)
            continue
        current = kept_by_name.get(name)
        if current is None:
            kept_by_name[name] = record
            continue
        if _cookie_score(record) >= _cookie_score(current):
            changes.append({'name': name, 'dropped': current, 'kept': record})
            kept_by_name[name] = record
        else:
            changes.append({'name': name, 'dropped': record, 'kept': current})
    normalized = ordered_passthrough + list(kept_by_name.values())
    return normalized, changes

def success_path_label(stage, rebuild_retry=False):
    if stage == 'first_submit':
        return '重建会话后首次提交进入成功路径' if rebuild_retry else '首次提交进入成功路径'
    if stage == 'same_session_retry':
        return '重建会话后重试进入成功路径' if rebuild_retry else '同会话重试后进入成功路径'
    return '进入成功路径'

# ================= 配置常量 =================
RENEW_DAYS = 7
CACHE_FILE_NAME = 'hiden_cookies.json'
LOCAL_CACHE_PATH = os.path.join(os.path.dirname(__file__), CACHE_FILE_NAME)

# ================= 全局日志收集器 =================
ALL_LOGS = []

def log_print(msg):
    print(msg)
    ALL_LOGS.append(str(msg))

# ================= 消息推送模块 =================
def send_notify(text, desp):
    token = os.environ.get("WXPUSHER_APP_TOKEN")
    uids_str = os.environ.get("WXPUSHER_UID")
    if not token or not uids_str:
        log_print("⚠️ 未配置 WxPusher，跳过推送")
        return
    log_print(f"\n==== 开始推送通知: {text} ====\n")
    uids = [u.strip() for u in re.split(r'[,;\n]', uids_str) if u.strip()]
    url = 'https://wxpusher.zjiecode.com/api/send/message'
    data = {
        "appToken": token,
        "content": f"<h3>{text}</h3><br><div style='font-size:14px;'>{desp.replace(chr(10), '<br>')}</div>",
        "summary": text,
        "contentType": 2,
        "uids": uids
    }
    try:
        res = requests.post(url, json=data)
        if res.status_code == 200:
            print("✅ WxPusher 推送成功")
        else:
            print(f"❌ WxPusher 推送响应: {res.text}")
    except Exception as e:
        print(f"❌ WxPusher 推送失败: {e}")

# ================= 辅助工具 =================
def sleep_random(min_ms=3000, max_ms=8000):
    sec = random.randint(min_ms, max_ms) / 1000.0
    time.sleep(sec)

class CacheManager:
    @staticmethod
    def load():
        if os.path.exists(LOCAL_CACHE_PATH):
            try:
                with open(LOCAL_CACHE_PATH, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                log_print("读取本地缓存失败")
        return {}

    @staticmethod
    def update(index, cookie_str):
        data = CacheManager.load()
        key = str(index)
        if data.get(key) == cookie_str:
            return
        data[key] = cookie_str
        with open(LOCAL_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        log_print(f"💾 [账号 {index + 1}] 本地缓存已更新")

# ================= 核心机器人类 =================
class HidenCloudBot:
    def __init__(self, env_cookie, index):
        self.index = index + 1
        self.base_url = "https://dash.hidencloud.com"
        self.env_cookie = env_cookie
        self.session = self.create_session()
        self.csrf_token = ""
        self.services = []
        self.processed_invoices = set()
        self.non_payable_invoices = set()
        self.retry_needed = False

        cached_data = CacheManager.load()
        cached_cookie = cached_data.get(str(index))

        if cached_cookie:
            log_print(f"[账号 {self.index}] 发现本地缓存 Cookie，优先使用...")
            self.load_cookie_str(cached_cookie)
        else:
            log_print(f"[账号 {self.index}] 使用环境变量 Cookie...")
            self.load_cookie_str(env_cookie)

    def log(self, msg):
        log_print(f"[账号 {self.index}] {msg}")

    def mark_retry_needed(self, reason):
        self.retry_needed = True
        if reason:
            self.log(f"🔁 标记本轮任务需要重试: {reason}")

    def create_session(self):
        return cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'desktop': True
            }
        )

    def load_cookie_str(self, cookie_str):
        if not cookie_str:
            return
        for cookie in parse_seed_cookie_string(cookie_str):
            self.session.cookies.set_cookie(
                requests.cookies.create_cookie(
                    name=cookie['name'],
                    value=cookie['value'],
                    domain=cookie['domain'],
                    path=cookie.get('path', '/'),
                    secure=bool(cookie.get('secure', True)),
                )
            )

    def get_cookie_str(self):
        return '; '.join([f"{c.name}={c.value}" for c in self.session.cookies])

    def normalize_critical_cookies(self, stage=""):
        records = []
        for cookie in self.session.cookies:
            records.append({
                'name': cookie.name,
                'value': cookie.value,
                'domain': cookie.domain or '',
                'path': cookie.path or '/',
                'secure': bool(cookie.secure),
                'expires': cookie.expires,
                'rest': getattr(cookie, '_rest', {}) or {},
            })
        normalized, changes = normalize_cookie_records(records)
        if not changes:
            return False
        new_jar = requests.cookies.RequestsCookieJar()
        for record in normalized:
            new_jar.set_cookie(
                requests.cookies.create_cookie(
                    name=record['name'],
                    value=record['value'],
                    domain=record.get('domain', ''),
                    path=record.get('path', '/'),
                    secure=bool(record.get('secure', False)),
                    expires=record.get('expires'),
                    rest=record.get('rest', {}),
                )
            )
        self.session.cookies = new_jar
        changed_names = []
        for change in changes:
            name = change['name']
            if name not in changed_names:
                changed_names.append(name)
        stage_text = f"{stage} " if stage else ""
        self.log(f"[COOKIE_NORMALIZED] {stage_text}检测到并归一化关键 Cookie: {', '.join(changed_names)}")
        return True

    def find_cookie_value(self, *names, preferred_domain=''):
        matches = []
        target_names = set(names)
        for cookie in self.session.cookies:
            if cookie.name in target_names and cookie.value:
                matches.append(cookie)
        if not matches:
            return ""
        if preferred_domain:
            domain_matches = [
                cookie for cookie in matches
                if cookie.domain and preferred_domain in cookie.domain
            ]
            if domain_matches:
                matches = domain_matches
        matches.sort(key=lambda cookie: (len(cookie.domain or ''), len(cookie.path or '')))
        return matches[-1].value

    def save_cookies(self):
        CacheManager.update(self.index - 1, self.get_cookie_str())

    def reset_to_env(self, env_cookie):
        self.session.cookies.clear()
        self.load_cookie_str(env_cookie)
        self.log("切换回环境变量原始 Cookie 重试...")

    def rebuild_session(self, cookie_str=None):
        self.session = self.create_session()
        self.csrf_token = ""
        if cookie_str:
            self.load_cookie_str(cookie_str)

    def rebuild_session_and_reinit(self):
        current_cookie = self.get_cookie_str()
        self.log("♻️ 重建会话并重新验证登录状态...")
        self.rebuild_session(current_cookie)
        if self.init():
            return True, 'invoice_page'
        self.log("⚠️ 当前 Cookie 重建会话后初始化失败，回退环境变量 Cookie 再试一次...")
        self.rebuild_session()
        self.load_cookie_str(self.env_cookie)
        return self.init()

    def request(self, method, url, data=None, headers=None):
        full_url = urljoin(self.base_url, url)
        try:
            resp = self.session.request(method, full_url, data=data, headers=headers, timeout=30)
            self.normalize_critical_cookies(f"{method} {url}")
            return resp
        except Exception as e:
            self.log(f"请求异常: {e}")
            raise

    def _refresh_csrf(self, soup):
        token_tag = soup.find('meta', attrs={'name': 'csrf-token'})
        if token_tag:
            self.csrf_token = token_tag['content']
            return
        token_input = soup.find('input', attrs={'name': '_token'})
        if token_input:
            self.csrf_token = token_input['value']

    def normalize_url(self, url):
        return urljoin(self.base_url, url)

    def has_invoice_payment_context(self, text):
        normalized = re.sub(r'\s+', ' ', text or '').strip().lower()
        if not normalized:
            return False
        positive_keywords = [
            'unpaid', 'pending', 'pay now', 'payment due',
            '未支付', '待支付', '待付款', '立即支付', '去支付', '付款', '支付'
        ]
        negative_keywords = [
            'paid', 'completed', 'cancelled', 'canceled', 'refunded',
            '已支付', '已付款', '已完成', '已取消', '已退款', '作废'
        ]
        has_positive = any(keyword in normalized for keyword in positive_keywords)
        has_negative = any(keyword in normalized for keyword in negative_keywords)
        return has_positive and not has_negative

    def extract_invoice_links(self, soup, require_payment_context=False):
        invoice_links = []
        if require_payment_context:
            containers = soup.find_all(['tr', 'li', 'div', 'article', 'section'])
            for container in containers:
                links = []
                for a in container.find_all('a', href=True):
                    href = a['href']
                    if '/invoice/' in href and 'download' not in href:
                        links.append(self.normalize_url(href))
                if not links:
                    continue
                container_text = container.get_text(" ", strip=True)
                if self.has_invoice_payment_context(container_text):
                    invoice_links.extend(links)
            return sorted(set(invoice_links))
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/invoice/' in href and 'download' not in href:
                invoice_links.append(self.normalize_url(href))
        return sorted(set(invoice_links))

    def extract_form_payload(self, form):
        payload = {}
        for field in form.find_all(['input', 'select', 'textarea']):
            name = field.get('name')
            if not name or field.has_attr('disabled'):
                continue
            tag_name = field.name.lower()
            if tag_name == 'input':
                field_type = (field.get('type') or '').lower()
                if field_type in ('checkbox', 'radio') and not field.has_attr('checked'):
                    continue
                payload[name] = field.get('value', '')
            elif tag_name == 'select':
                option = field.find('option', selected=True) or field.find('option')
                payload[name] = option.get('value', '') if option else ''
            else:
                payload[name] = field.get_text()
        return payload

    def find_renew_form(self, soup, service_id):
        exact_path = f"/service/{service_id}/renew"
        fallback_form = None
        fallback_action = ""
        for form in soup.find_all('form'):
            action = form.get('action', '')
            if not action:
                continue
            action_url = self.normalize_url(action)
            if exact_path in action_url:
                return form, action_url
            form_text = form.get_text(" ", strip=True)
            if '/renew' in action_url or 'renew' in form_text.lower() or '续期' in form_text:
                fallback_form = form
                fallback_action = action_url
        return fallback_form, fallback_action

    def fetch_manage_page(self, service_id):
        manage_res = self.request('GET', f"/service/{service_id}/manage")
        soup = BeautifulSoup(manage_res.text, 'html.parser')
        self._refresh_csrf(soup)
        return manage_res, soup

    def submit_renew_request(self, service_id, soup, referer_url):
        form, action_url = self.find_renew_form(soup, service_id)
        payload = self.extract_form_payload(form) if form else {}
        token_input = soup.find('input', attrs={'name': '_token'})
        if token_input and not payload.get('_token'):
            payload['_token'] = token_input.get('value', '')
        payload['days'] = RENEW_DAYS
        target_url = action_url or self.normalize_url(f"/service/{service_id}/renew")
        xsrf_cookie = self.find_cookie_value(
            'XSRF-TOKEN',
            'XSRF_TOKEN',
            'csrf_token',
            preferred_domain='dash.hidencloud.com'
        )
        headers = {
            'X-CSRF-TOKEN': self.csrf_token,
            'Referer': referer_url,
            'Origin': self.base_url,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
        }
        if xsrf_cookie:
            headers['X-XSRF-TOKEN'] = unquote(xsrf_cookie)
        return self.request('POST', target_url, data=payload, headers=headers)

    def try_handle_invoice_from_response(self, service_id, response, allow_invoice_poll=True):
        if '/invoice/' in response.url:
            self.log("⚡️ 续期成功，已跳转账单页，自动执行支付...")
            self.perform_pay_from_html(response.text, response.url)
            return True, 'invoice_link'
        soup_resp = BeautifulSoup(response.text, 'html.parser')
        invoice_links = self.extract_invoice_links(soup_resp, require_payment_context=False)
        if invoice_links:
            invoice_url = invoice_links[0]
            self.log(f"🔗 在响应HTML中发现账单链接: {invoice_url}")
            self.pay_single_invoice(invoice_url)
            return True, 'server_reject'
        err_div = soup_resp.find('div', class_=re.compile(r'(alert-danger|text-danger|error)'))
        if err_div:
            self.log(f"⚠️ 续期请求被服务端拒绝，页面提示: {err_div.get_text(strip=True)}")
            return True
        if not allow_invoice_poll:
            return False, None
        if response.status_code == 419:
            self.log("⚠️ 续期请求返回 419，重试后仍未跳转，开始检查是否生成账单...")
        else:
            self.log(f"⚠️ 提交成功但未自动跳转，响应URL: {response.url} | 状态码: {response.status_code}")
            self.log("后置轮询检查账单...")
        invoice_polled = self.check_and_pay_invoices(service_id, is_precheck=False, retries=6, retry_delay=8)
        if invoice_polled:
            return True, 'invoice_poll'
        return False, None

    def init(self):
        self.log("正在验证登录状态...")
        try:
            res = self.request('GET', '/dashboard')
            if '/login' in res.url:
                self.log("❌ 当前 Cookie 已失效")
                return False
            soup = BeautifulSoup(res.text, 'html.parser')
            log_print(f"👀 [调试] 网页标题是: {soup.title.string if soup.title else '无标题'}")
            self._refresh_csrf(soup)
            self.services = []
            for a in soup.find_all('a', href=True):
                href = a['href']
                if '/service/' in href and '/manage' in href:
                    svc_id = href.split('/service/')[1].split('/')[0]
                    if not any(s['id'] == svc_id for s in self.services):
                        self.services.append({'id': svc_id, 'url': href})
            self.log(f"✅ 登录成功，发现 {len(self.services)} 个服务。")
            self.save_cookies()
            return True
        except Exception as e:
            self.log(f"❌ 初始化异常: {e}")
            return False

    def process_service(self, service, allow_rebuild_retry=True, skip_initial_delay=False, rebuild_retry=False):
        if not skip_initial_delay:
            sleep_random(2000, 4000)
        self.log(f">>> 处理服务 ID: {service['id']}")
        try:
            self.check_and_pay_invoices(service['id'], is_precheck=True)
            manage_res, soup = self.fetch_manage_page(service['id'])
            renew_btn = soup.find('button', onclick=re.compile(r'showRenewAlert'))
            if renew_btn:
                onclick_val = renew_btn['onclick']
                match = re.search(r'showRenewAlert\((\d+),\s*(\d+),\s*(true|false)\)', onclick_val)
                if match:
                    days_until = int(match.group(1))
                    threshold = int(match.group(2))
                    is_free = match.group(3) == 'true'
                    if days_until > threshold:
                        threshold_text = "1 day" if threshold == 1 else f"{threshold} days"
                        kind = "免费服务" if is_free else "服务"
                        self.log(f"⏳ 暂未到达续期时间: {kind}剩余时间低于 {threshold_text} 才可续期。当前剩余: {days_until} 天。")
                        return
            token_input = soup.find('input', attrs={'name': '_token'})
            if not token_input:
                self.log("❌ 无法找到续期 Token (可能是服务已到期或页面结构变更)")
                return
            self.log(f"提交续期 ({RENEW_DAYS}天)...")
            sleep_random(1000, 2000)
            submit_stage = 'first_submit'
            res = self.submit_renew_request(service['id'], soup, manage_res.url)
            handled, outcome = self.try_handle_invoice_from_response(service['id'], res, allow_invoice_poll=False)
            if not handled and res.status_code == 419:
                self.log("♻️ 首次续期请求返回 419，刷新管理页获取新 Token 后重试一次...")
                sleep_random(1000, 2000)
                manage_res, soup = self.fetch_manage_page(service['id'])
                submit_stage = 'same_session_retry'
                res = self.submit_renew_request(service['id'], soup, manage_res.url)
                handled, outcome = False, None
            if not handled:
                handled, outcome = self.try_handle_invoice_from_response(service['id'], res)
            if handled and outcome in {'invoice_page', 'invoice_link', 'invoice_poll'}:
                self.log(f"[RENEW_RESULT] {success_path_label(submit_stage, rebuild_retry=rebuild_retry)}")
            elif handled and outcome == 'server_reject':
                self.log(f"[RENEW_RESULT] {'重建会话后' if rebuild_retry else '当前会话'}提交已被服务端拒绝")
            if not handled and allow_rebuild_retry and res.status_code == 419:
                self.log("♻️ 当前会话内续期仍失败，模拟重跑 Job：重建会话后完整重试当前服务一次...")
                if self.rebuild_session_and_reinit():
                    self.process_service(service, allow_rebuild_retry=False, skip_initial_delay=True, rebuild_retry=True)
                else:
                    self.log("❌ 重建会话后仍无法重新登录，放弃本服务本轮续期")
                    self.mark_retry_needed(f"服务 {service['id']} 重建会话后仍无法完成续期")
            elif not handled:
                self.mark_retry_needed(f"服务 {service['id']} 本轮续期未完成")
        except Exception as e:
            self.log(f"处理异常: {e}")
            self.mark_retry_needed(f"服务 {service['id']} 处理异常")
        finally:
            self.save_cookies()

    def check_and_pay_invoices(self, service_id, is_precheck=False, retries=1, retry_delay=5):
        if not is_precheck:
            sleep_random(2000, 3000)
        for attempt in range(retries):
            try:
                res = self.request('GET', f"/service/{service_id}/invoices?where=unpaid")
                soup = BeautifulSoup(res.text, 'html.parser')
                invoice_links = self.extract_invoice_links(soup, require_payment_context=True)
                unique_invoices = [url for url in set(invoice_links)
                                   if url not in self.processed_invoices
                                   and url not in self.non_payable_invoices]
                if not unique_invoices:
                    if retries > 1 and attempt < retries - 1:
                        self.log(f"⚪ 第{attempt+1}次检查无新账单，{retry_delay}秒后重试...")
                        time.sleep(retry_delay)
                        continue
                    if not is_precheck:
                        self.log("⚪ 无未支付账单")
                    return False
                self.log(f"🔍 发现 {len(unique_invoices)} 个未付账单，准备清理...")
                for url in unique_invoices:
                    self.pay_single_invoice(url)
                    sleep_random(3000, 5000)
                return True
            except Exception as e:
                self.log(f"查账单出错: {e}")
                self.mark_retry_needed(f"服务 {service_id} 查询账单异常")
                return False

    def pay_single_invoice(self, url):
        normalized_url = self.normalize_url(url)
        try:
            self.log(f"📄 打开账单: {normalized_url}")
            res = self.request('GET', normalized_url)
            self.perform_pay_from_html(res.text, normalized_url)
        except Exception as e:
            self.log(f"访问账单失败: {e}")
            self.mark_retry_needed("账单页面访问失败")

    def perform_pay_from_html(self, html_content, current_url):
        normalized_current_url = self.normalize_url(current_url)
        if normalized_current_url in self.processed_invoices:
            self.log(f"⏭️ 账单已处理，跳过重复支付: {normalized_current_url}")
            return
        if normalized_current_url in self.non_payable_invoices:
            self.log(f"⏭️ 账单当前不可支付，跳过重复检查: {normalized_current_url}")
            return
        soup = BeautifulSoup(html_content, 'html.parser')
        self._refresh_csrf(soup)
        target_form = None
        target_action = ""
        for form in soup.find_all('form'):
            action = form.get('action', '')
            if not action or 'balance/add' in action:
                continue
            btn = form.find('button')
            if btn and ('pay' in btn.get_text().lower() or '支付' in btn.get_text()):
                target_form = form
                target_action = action
                break
        if not target_form:
            for form in soup.find_all('form'):
                action = form.get('action', '')
                if any(kw in action for kw in ['/invoice/', '/payment/']) and 'balance/add' not in action:
                    if form.find('button'):
                        target_form = form
                        target_action = action
                        self.log(f"🔁 降级匹配到支付表单: {action}")
                        break
        if not target_form:
            page_title = soup.title.string.strip() if soup.title and soup.title.string else "无标题"
            page_text = soup.get_text(" ", strip=True)
            if not self.has_invoice_payment_context(page_text):
                self.non_payable_invoices.add(normalized_current_url)
                self.log(f"⚪ 账单页面未显示未支付/支付入口，视为本轮不可支付并跳过: {normalized_current_url}")
            else:
                self.log(f"⚠️ 未找到可用的支付表单，可能页面结构变更。标题: {page_title}")
                self.mark_retry_needed(f"账单 {normalized_current_url} 页面结构疑似变更")
            return
        payload = {}
        for inp in target_form.find_all('input'):
            name = inp.get('name')
            value = inp.get('value', '')
            if name:
                payload[name] = value
        self.log("👉 提交支付...")
        try:
            action_url = self.normalize_url(target_action)
            headers = {'X-CSRF-TOKEN': self.csrf_token, 'Referer': current_url}
            res = self.request('POST', action_url, data=payload, headers=headers)
            if res.status_code == 200:
                self.log("✅ 支付成功！")
                self.processed_invoices.add(normalized_current_url)
            else:
                self.log(f"⚠️ 支付响应: {res.status_code}")
                self.mark_retry_needed(f"账单 {normalized_current_url} 支付响应异常")
        except Exception as e:
            self.log(f"❌ 支付失败: {e}")
            self.mark_retry_needed(f"账单 {normalized_current_url} 支付异常")

# ================= 本地 Cookie 文件路径 =================
LOCAL_COOKIE_FILE = os.path.join(os.path.dirname(__file__), 'cookie.json')

def load_local_cookies():
    """从本地文件加载 Cookie"""
    if os.path.exists(LOCAL_COOKIE_FILE):
        try:
            with open(LOCAL_COOKIE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            cookies = []
            for i in range(1, 100):
                key = f"cookie{i}"
                if key in data and data[key]:
                    cookies.append(data[key])
                else:
                    break
            if cookies:
                log_print(f"✅ 从本地文件加载了 {len(cookies)} 个 Cookie")
                return cookies
        except Exception as e:
            log_print(f"⚠️ 读取本地 Cookie 文件失败: {e}")
    return []

def save_local_cookies(cookies_list):
    """保存 Cookie 到本地文件"""
    try:
        data = {}
        for i, cookie in enumerate(cookies_list, 1):
            data[f"cookie{i}"] = cookie
        with open(LOCAL_COOKIE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        log_print(f"💾 Cookie 已保存到本地文件: {LOCAL_COOKIE_FILE}")
    except Exception as e:
        log_print(f"⚠️ 保存本地 Cookie 文件失败: {e}")

# ================= 主程序 =================
if __name__ == '__main__':
    env_cookies = os.environ.get("HIDEN_COOKIE", "")
    
    # 如果环境变量为空，尝试从本地文件读取
    if not env_cookies:
        log_print("⚠️ 环境变量 HIDEN_COOKIE 为空，尝试从本地文件读取...")
        cookies_list = load_local_cookies()
    else:
        # 如果只有值没有名，自动补全前缀
        if not env_cookies.startswith("remember_web_"):
            log_print("⚠️ 检测到只有 Cookie 值，自动补全前缀...")
            env_cookies = f"remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d={env_cookies}"
        
        cookies_list = re.split(r'[&\n]', env_cookies)
        cookies_list = [c for c in cookies_list if c.strip()]
        
        # 保存到本地文件，供下次使用
        if cookies_list:
            save_local_cookies(cookies_list)
    
    any_retry_needed = False

    if not cookies_list:
        log_print("❌ 未配置环境变量 HIDEN_COOKIE，也未找到本地 Cookie 文件")
        sys.exit(1)

    log_print(f"\n=== HidenCloud 续期脚本启动 (青龙面板版 - 仅 remember_web) ===")

    for i, cookie in enumerate(cookies_list):
        bot = HidenCloudBot(cookie, i)
        success = bot.init()
        if not success:
            bot.reset_to_env(cookie)
            success = bot.init()
        if success:
            for service in bot.services:
                bot.process_service(service)
        else:
            log_print(f"账号 {i + 1}: 登录失败，请检查 Cookie")
            bot.mark_retry_needed("账号初始化失败")
        if bot.retry_needed:
            any_retry_needed = True
        log_print("\n----------------------------------------\n")
        if i < len(cookies_list) - 1:
            sleep_random(5000, 10000)

    final_content = "\n".join(ALL_LOGS)
    if final_content:
        send_notify("HidenCloud 续期报告", final_content)

    if any_retry_needed:
        log_print("🔁 本轮存在可重试失败，脚本将返回退出码 1")
        sys.exit(1)
    sys.exit(0)
