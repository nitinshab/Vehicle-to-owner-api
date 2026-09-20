import re
import time
import logging
import traceback
import threading
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ==================== BRAND ====================
OWNER     = "@FizzaGirl"
DEVELOPER = "@FizzaGirl"
CHANNEL   = "@BUILDAPIS"

def brand_block():
    return {
        "Owner":     OWNER,
        "Developer": DEVELOPER,
        "Channel":   CHANNEL,
    }

# ==================== PARIVAHAN URLS ====================
HOMEPAGE_URL  = "https://vahan.parivahan.gov.in/vahanservice/vahan/ui/statevalidation/homepage.xhtml?statecd=Mzc2MzM2MzAzNjY0MzIzODM3NjIzNjY0MzY2MjM3NDQ0Yw=="
HOMEPAGE_BASE = "https://vahan.parivahan.gov.in/vahanservice/vahan/ui/statevalidation/homepage.xhtml"
LOGIN_URL     = "https://vahan.parivahan.gov.in/vahanservice/vahan/ui/usermgmt/login.xhtml"
FORM_URL      = "https://vahan.parivahan.gov.in/vahanservice/vahan/ui/balanceservice/form_reschedule_fitness.xhtml"

# ==================== SESSION ====================
def make_session():
    session = requests.Session()
    retry = Retry(total=4, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET", "POST"], raise_on_status=False)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.max_redirects = 10
    return session

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

AJAX_HEADERS = {
    "User-Agent": BASE_HEADERS["User-Agent"],
    "Accept": "application/xml, text/xml, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Faces-Request": "partial/ajax",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://vahan.parivahan.gov.in",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

# ==================== CHASSIS ====================
def extract_last5(chassis):
    if not chassis:
        return None
    chassis = str(chassis).strip()
    if chassis.lower() in ("", "null", "none", "n/a"):
        return None
    if "~" in chassis:
        return chassis[-5:]
    clean = re.sub(r"[^A-Z0-9]", "", chassis.upper())
    return clean[-5:] if len(clean) >= 5 else None

def _pick_chassis(data):
    KEYS = ("chassis_number_unmasked", "chassis_number", "chassis_no",
            "chasis_no", "chassisNo", "chassis",
            "vehicle_chasi_number", "vehicle_chassis_number")
    def search(d):
        if not isinstance(d, dict):
            return None
        for k in KEYS:
            v = d.get(k)
            if v and str(v).strip().lower() not in ("", "null", "none", "n/a"):
                return str(v).strip()
        for v in d.values():
            if isinstance(v, dict):
                res = search(v)
                if res:
                    return res
        return None
    return search(data)

def _try_adv_api(vnum):
    url = f"https://api2.adv.lat/vehicle?key=Jv9sTf3bW5&number={vnum}"
    r = requests.get(url, timeout=12)
    return _pick_chassis(r.json()) if r.ok else None

def get_chassis(vnum):
    apis = [("AdvAPI", _try_adv_api)]
    result = {}
    done = threading.Event()
    errors = {}
    err_lock = threading.Lock()

    def run(name, fn):
        try:
            chassis = fn(vnum)
            if chassis and not done.is_set():
                last5 = extract_last5(chassis)
                if last5:
                    result.update({"chassis_full": chassis, "last5": last5, "source": name})
                    done.set()
                else:
                    with err_lock:
                        errors[name] = f"chassis '{chassis}' has fewer than 5 clean chars"
            elif not chassis:
                with err_lock:
                    errors[name] = "no chassis returned"
        except Exception as e:
            with err_lock:
                errors[name] = str(e)

    threads = [threading.Thread(target=run, args=(n, f), daemon=True) for n, f in apis]
    for t in threads: t.start()
    done.wait(timeout=15)
    for t in threads: t.join(timeout=1)

    if result.get("last5"):
        return {"ok": True, **result}
    return {"ok": False, "error": "Chassis API failed", "api_errors": dict(errors)}

# ==================== PARIVAHAN ====================
def get_viewstate(html):
    tag = BeautifulSoup(html, "html.parser").find("input", {"name": "javax.faces.ViewState"})
    return tag["value"] if tag else None

def get_viewstate_ajax(text):
    m = re.search(r'<update id="j_id1:javax\.faces\.ViewState:0"><!\[CDATA\[(.*?)\]\]></update>', text)
    return m.group(1) if m else None

def get_checkbox(html):
    m = re.search(r'id="(j_idt\d+)"[^>]*class="[^"]*ui-chkbox', html)
    return m.group(1) if m else "j_idt187"

def parivahan_fetch(vnum, last5):
    session = make_session()
    bh = BASE_HEADERS.copy()
    ah = AJAX_HEADERS.copy()

    r1 = session.get(HOMEPAGE_URL, headers=bh, timeout=30)
    if r1.status_code != 200:
        return {"ok": False, "error": f"Step1: HTTP {r1.status_code}", "snippet": r1.text[:500]}
    vs = get_viewstate(r1.text)
    chk = get_checkbox(r1.text)
    if not vs:
        return {"ok": False, "error": "Step1: ViewState missing", "snippet": r1.text[:500]}

    ah["Referer"] = HOMEPAGE_URL
    r2 = session.post(HOMEPAGE_BASE, headers=ah, timeout=30, data={
        "javax.faces.partial.ajax": "true", "javax.faces.source": "fit_c_office_to",
        "javax.faces.partial.execute": "fit_c_office_to",
        "javax.faces.behavior.event": "change", "javax.faces.partial.event": "change",
        "homepageformid": "homepageformid", "j_idt12": "", "j_idt47_input": "en",
        "state_cd_filter": "", "fit_c_office_to_input": "1", "abc": "abc",
        "javax.faces.ViewState": vs, "pmtchk_input": "-1", "nocregnno": "",
    })
    vs = get_viewstate_ajax(r2.text) or vs

    r3 = session.post(HOMEPAGE_BASE, headers=ah, timeout=30, data={
        "javax.faces.partial.ajax": "true", "javax.faces.source": chk,
        "javax.faces.partial.execute": chk, "javax.faces.partial.render": "proccedHomeButtonId",
        "javax.faces.behavior.event": "change", "javax.faces.partial.event": "change",
        "homepageformid": "homepageformid", "j_idt12": "", "j_idt47_input": "en",
        "state_cd_filter": "", "fit_c_office_to_input": "1", f"{chk}_input": "on",
        "abc": "abc", "javax.faces.ViewState": vs, "pmtchk_input": "-1", "nocregnno": "",
    })
    vs = get_viewstate_ajax(r3.text) or vs

    r4 = session.post(HOMEPAGE_BASE, headers=ah, timeout=30, data={
        "javax.faces.partial.ajax": "true", "javax.faces.source": "proccedHomeButtonId",
        "javax.faces.partial.execute": "@all",
        "javax.faces.partial.render": "regnid facelesslist portaldownMsgPnl mainhomepagepnl leftmenupnlid leftmenupnlidservdown",
        "proccedHomeButtonId": "proccedHomeButtonId", "homepageformid": "homepageformid",
        "j_idt12": "", "j_idt47_input": "en", "state_cd_filter": "",
        "fit_c_office_to_input": "1", f"{chk}_input": "on", "abc": "abc",
        "javax.faces.ViewState": vs, "pmtchk_input": "-1", "nocregnno": "",
    })
    vs = get_viewstate_ajax(r4.text) or vs

    dm = re.search(r'id="(j_idt\d+)"[^>]*class="[^"]*ui-button', r4.text)
    dbt = dm.group(1) if dm else "j_idt536"
    r5 = session.post(HOMEPAGE_BASE, headers=ah, timeout=30, data={
        "javax.faces.partial.ajax": "true", "javax.faces.source": dbt,
        "javax.faces.partial.execute": "@all", f"{dbt}": dbt,
        "homepageformid": "homepageformid", "j_idt12": "", "j_idt47_input": "en",
        "state_cd_filter": "", "fit_c_office_to_input": "1", f"{chk}_input": "on",
        "pmtchk_input": "-1", "nocregnno": "", "javax.faces.ViewState": vs,
    })
    vs = get_viewstate_ajax(r5.text) or vs

    lh = {**bh, "Referer": HOMEPAGE_URL}
    r6 = session.get(LOGIN_URL + "?faces-redirect=true", headers=lh, timeout=30, allow_redirects=True)
    vs = get_viewstate(r6.text)
    if not vs:
        return {"ok": False, "error": "Step6: ViewState missing on login page", "snippet": r6.text[:500]}

    fm = re.search(r'id="(j_idt\d+)"[^>]*name="\1"[^>]*type="submit"', r6.text)
    fbt = fm.group(1) if fm else "j_idt506"
    ph = {**bh, "Content-Type": "application/x-www-form-urlencoded",
          "Origin": "https://vahan.parivahan.gov.in",
          "Referer": LOGIN_URL + "?faces-redirect=true"}
    r7 = session.post(LOGIN_URL, headers=ph, timeout=30, allow_redirects=True, data={
        "loginForm": "loginForm", f"{fbt}": fbt,
        "javax.faces.ViewState": vs, "InputEnter": "",
        "fitbalcTest": "fitbalcTest", "pur_cd": "86",
    })

    fh = {**bh, "Referer": LOGIN_URL + "?faces-redirect=true", "Cache-Control": "max-age=0"}
    r8 = session.get(FORM_URL, headers=fh, timeout=30)
    vs = get_viewstate(r8.text)
    if not vs:
        return {"ok": False, "error": "Step8: ViewState missing on form page", "snippet": r8.text[:500]}

    ah["Referer"] = FORM_URL
    r9 = session.post(FORM_URL, headers=ah, timeout=30, data={
        "javax.faces.partial.ajax": "true",
        "javax.faces.source": "balanceFeesFine:validate_dtls",
        "javax.faces.partial.execute": "@all",
        "javax.faces.partial.render": "balanceFeesFine:auth_panel",
        "balanceFeesFine:validate_dtls": "balanceFeesFine:validate_dtls",
        "balanceFeesFine": "balanceFeesFine",
        "balanceFeesFine:tf_reg_no": vnum,
        "balanceFeesFine:tf_chasis_no": last5,
        "javax.faces.ViewState": vs,
    })

    body = r9.text
    for pat in [
        r'id="balanceFeesFine:tf_mobile"[^>]*value="(\d{10})"',
        r'value="(\d{10})"[^>]*id="balanceFeesFine:tf_mobile"',
        r'balanceFeesFine:tf_mobile[^>]*value="(\d{10})"',
    ]:
        m = re.search(pat, body, re.DOTALL)
        if m and m.group(1)[0] in "6789":
            return {"ok": True, "mobile": m.group(1)}

    hits = re.findall(r"\b([6-9]\d{9})\b", body)
    if hits:
        return {"ok": True, "mobile": hits[0]}

    return {"ok": False, "error": "Mobile not found in Parivahan response", "snippet": body[:800]}

# ==================== CORE ====================
def lookup(raw):
    vnum = re.sub(r"[^A-Z0-9]", "", raw.upper())
    if len(vnum) < 6:
        return {"success": False, "error": "Vehicle number too short (min 6 chars)", "input": raw, **brand_block()}

    cr = get_chassis(vnum)
    if not cr["ok"]:
        return {
            "success": False,
            "vehicle": vnum,
            "error": cr["error"],
            "api_errors": cr.get("api_errors", {}),
            **brand_block()
        }

    last5 = cr["last5"]
    chassis_full = cr["chassis_full"]
    chassis_source = cr["source"]

    last_err = {}
    for attempt in range(1, 4):
        try:
            mr = parivahan_fetch(vnum, last5)
            if mr["ok"]:
                return {
                    "success": True,
                    "vehicle": vnum,
                    "mobile": mr["mobile"],
                    "chassis_last5": last5,
                    "chassis_full": chassis_full,
                    "chassis_source": chassis_source,
                    **brand_block()
                }
            last_err = mr
        except (requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError) as e:
            last_err = {"error": str(e), "type": "ConnectionError"}
            time.sleep(attempt * 1.5)
            continue
        except requests.exceptions.Timeout:
            last_err = {"error": "Request timed out", "type": "Timeout"}
            time.sleep(attempt * 1.5)
            continue
        except Exception as e:
            last_err = {"error": str(e), "type": "Exception", "traceback": traceback.format_exc()}
            break
        if attempt < 3:
            time.sleep(1)

    return {
        "success": False,
        "vehicle": vnum,
        "error": last_err.get("error", "Unknown error"),
        "detail": last_err.get("type", ""),
        "snippet": last_err.get("snippet", ""),
        "chassis_last5": last5,
        "chassis_full": chassis_full,
        "chassis_source": chassis_source,
        **brand_block()
    }

# ==================== HANDLER ====================
class handler(BaseHTTPRequestHandler):
    def _send(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)

            # /api/vehicle?rc=MH12DE1433   ya   /api/vehicle/MH12DE1433
            parts = [p for p in parsed.path.split("/") if p]
            reg = None
            if "rc" in qs:
                reg = qs["rc"][0]
            elif len(parts) >= 3:
                reg = parts[2]

            if not reg:
                return self._send(200, {
                    "status": "ok",
                    "endpoint": "/api/vehicle?rc=MH12DE1433",
                    "note": "Pass ?rc=YOUR_RC or /api/vehicle/YOUR_RC",
                    **brand_block()
                })

            res = lookup(reg)
            code = 200 if res["success"] else 422
            return self._send(code, res)
        except Exception as e:
            return self._send(500, {
                "success": False,
                "error": str(e),
                "traceback": traceback.format_exc(),
                **brand_block()
            })

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
