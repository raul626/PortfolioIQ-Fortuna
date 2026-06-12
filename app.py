"""
PortfolioIQ v3 — Cloud Edition (GitHub + Render)
Features:
  - ETF + Stock aware data fetching (VDE, VUG, etc.)
  - Enhanced Momentum: MA50/200, MACD, Volume surge, Relative Strength vs SPY
  - Auto Screener: analyst upgrades + undervalued growth stocks
  - Weekly email report to rinjoque@me.com
  - SSL fix built-in for all environments
  - Valuation tab: P/E expansion signals + 52-week range bars + research links
"""

# ── SSL fix — must be before any network imports ──────────────────────────────
import ssl, certifi, os
os.environ["SSL_CERT_FILE"]      = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
os.environ["CURL_CA_BUNDLE"]     = certifi.where()
try:
    ssl._create_default_https_context = ssl.create_default_context
except Exception:
    pass

import json, smtplib, threading, logging
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import yfinance as yf
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__, template_folder=".")
CORS(app, origins=["https://raul626.github.io"])
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DATA_FILE   = os.path.join(BASE_DIR, "portfolio_data.json")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# ── JSON helpers ──────────────────────────────────────────────────────────────
def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def load_portfolio():
    return load_json(DATA_FILE, {
        "portfolio": ["VDE","VUG","VST","ICLN","BE","QQQM"],
        "watchlist":  ["PPX"],
        "last_updated": None
    })

def save_portfolio(data):
    save_json(DATA_FILE, data)

def load_config():
    defaults = {
        "email":         os.environ.get("REPORT_EMAIL", "rinjoque@me.com"),
        "smtp_server":   os.environ.get("SMTP_SERVER",  "smtp.mail.me.com"),
        "smtp_port":     int(os.environ.get("SMTP_PORT", 587)),
        "smtp_user":     os.environ.get("SMTP_USER",    ""),
        "smtp_password": os.environ.get("SMTP_PASS",    ""),
        "weights": {
            "valuation":     0.20, "growth":        0.20,
            "profitability": 0.20, "momentum":      0.15,
            "dividends":     0.10, "risk":          0.15
        },
        "schedule_day":  "monday",
        "schedule_hour": 8
    }
    saved  = load_json(CONFIG_FILE, {})
    merged = {**defaults, **saved}
    for env, key in [("SMTP_USER","smtp_user"),("SMTP_PASS","smtp_password"),
                     ("SMTP_SERVER","smtp_server"),("REPORT_EMAIL","email")]:
        if os.environ.get(env):
            merged[key] = os.environ[env]
    return merged

def save_config(cfg):
    save_json(CONFIG_FILE, cfg)

# ── Momentum engine ───────────────────────────────────────────────────────────
def compute_momentum_detail(hist, symbol):
    result = {
        "ma50": None, "ma200": None,
        "ma_signal":     "N/A",
        "macd_signal":   "N/A",
        "volume_signal": "N/A",
        "rs_signal":     "N/A",
        "volume_ratio":  None,
        "rs_vs_spy":     None,
        "momentum_score": 50
    }
    if hist is None or len(hist) < 30:
        return result

    close  = hist["Close"]
    volume = hist["Volume"]
    price  = float(close.iloc[-1])

    # 1. Moving Averages
    ma_score = 50
    if len(close) >= 50:
        result["ma50"]  = round(float(close.rolling(50).mean().iloc[-1]), 2)
    if len(close) >= 200:
        result["ma200"] = round(float(close.rolling(200).mean().iloc[-1]), 2)

    ma50, ma200 = result["ma50"], result["ma200"]
    if ma50 and ma200:
        if price > ma50 and price > ma200:
            result["ma_signal"] = "Above both MAs ✅"; ma_score = 100
        elif price > ma200:
            result["ma_signal"] = "Above 200d, below 50d ⚠️"; ma_score = 60
        elif price > ma50:
            result["ma_signal"] = "Below 200d, above 50d ⚠️"; ma_score = 40
        else:
            result["ma_signal"] = "Below both MAs ❌"; ma_score = 0
    elif ma50:
        ma_score = 75 if price > ma50 else 25
        result["ma_signal"] = f"{'Above' if price > ma50 else 'Below'} 50d MA"

    # 2. MACD (12,26,9)
    macd_score = 50
    if len(close) >= 35:
        ema12  = close.ewm(span=12, adjust=False).mean()
        ema26  = close.ewm(span=26, adjust=False).mean()
        macd   = ema12 - ema26
        sig    = macd.ewm(span=9, adjust=False).mean()
        hist_  = macd - sig
        ml, sl = float(macd.iloc[-1]), float(sig.iloc[-1])
        h      = float(hist_.iloc[-1])
        hp     = float(hist_.iloc[-2]) if len(hist_) >= 2 else 0
        if ml > sl and h > 0 and h > hp:
            result["macd_signal"] = "Bullish crossover ✅"; macd_score = 100
        elif ml > sl and h > 0:
            result["macd_signal"] = "Bullish momentum ✅"; macd_score = 80
        elif ml > sl:
            result["macd_signal"] = "Weakening bullish ⚠️"; macd_score = 55
        elif ml < sl and h < 0 and h < hp:
            result["macd_signal"] = "Bearish crossover ❌"; macd_score = 0
        else:
            result["macd_signal"] = "Bearish momentum ❌"; macd_score = 20

    # 3. Volume Surge
    vol_score = 50
    if len(volume) >= 21:
        avg_vol = float(volume.iloc[-21:-1].mean())
        cur_vol = float(volume.iloc[-1])
        if avg_vol > 0:
            vr  = cur_vol / avg_vol
            pct = (vr - 1) * 100
            result["volume_ratio"] = round(vr, 2)
            if vr >= 2.0:
                result["volume_signal"] = f"Major surge +{pct:.0f}% vs avg ✅"; vol_score = 100
            elif vr >= 1.5:
                result["volume_signal"] = f"Volume surge +{pct:.0f}% vs avg ✅"; vol_score = 80
            elif vr >= 1.2:
                result["volume_signal"] = f"Above avg +{pct:.0f}% ⚠️"; vol_score = 65
            elif vr >= 0.8:
                result["volume_signal"] = f"Normal volume ({vr:.1f}x avg)"; vol_score = 50
            else:
                result["volume_signal"] = f"Below avg ({vr:.1f}x) ⚠️"; vol_score = 30

    # 4. Relative Strength vs SPY (3-month)
    rs_score = 50
    try:
        spy = yf.Ticker("SPY").history(period="3mo")
        if len(spy) >= 2 and len(hist) >= 2:
            stock_ret = hist["Close"].iloc[-1] / hist["Close"].iloc[max(0,len(hist)-63)] - 1
            spy_ret   = spy["Close"].iloc[-1]  / spy["Close"].iloc[0] - 1
            rs        = (stock_ret - spy_ret) * 100
            result["rs_vs_spy"] = round(rs, 2)
            if rs >= 10:
                result["rs_signal"] = f"Beating S&P by {rs:.1f}% ✅"; rs_score = 100
            elif rs >= 3:
                result["rs_signal"] = f"Outperforming S&P by {rs:.1f}% ✅"; rs_score = 75
            elif rs >= -3:
                result["rs_signal"] = f"In line with S&P ({rs:+.1f}%)"; rs_score = 50
            elif rs >= -10:
                result["rs_signal"] = f"Lagging S&P by {abs(rs):.1f}% ⚠️"; rs_score = 30
            else:
                result["rs_signal"] = f"Significantly lagging S&P {abs(rs):.1f}% ❌"; rs_score = 0
    except Exception:
        pass

    result["momentum_score"] = round((ma_score + macd_score + vol_score + rs_score) / 4, 1)
    return result

# ── ETF + Stock aware fetcher ─────────────────────────────────────────────────
def fetch_ticker_data(symbol: str) -> dict:
    try:
        t          = yf.Ticker(symbol)
        info       = t.info
        hist       = t.history(period="1y")
        quote_type = str(info.get("quoteType", "EQUITY")).upper()
        is_etf     = quote_type in ("ETF", "MUTUALFUND", "FUND", "INDEX")

        def g(*keys):
            for key in keys:
                v = info.get(key)
                if v is not None and v not in ("", "None", 0, "N/A"):
                    try:
                        return round(float(v), 4)
                    except (TypeError, ValueError):
                        return v
            return None

        # Price
        price = g("currentPrice","regularMarketPrice","navPrice","previousClose")

        # Historical
        perf_52w = rsi = None
        if len(hist) >= 2:
            perf_52w = round((hist["Close"].iloc[-1]/hist["Close"].iloc[0]-1)*100, 2)
        if len(hist) >= 15:
            d     = hist["Close"].diff()
            gain  = d.clip(lower=0).rolling(14).mean()
            loss  = (-d.clip(upper=0)).rolling(14).mean()
            rs_   = gain / loss.replace(0, float("nan"))
            rsi   = round(float((100 - 100/(1+rs_)).iloc[-1]), 2)

        momentum_detail = compute_momentum_detail(hist, symbol)

        # Valuation
        pe_ratio  = g("trailingPE","forwardPE")
        pb_ratio  = g("priceToBook")
        ev_ebitda = g("enterpriseToEbitda")

        # Growth — ETFs use fund returns as proxy
        if is_etf:
            revenue_growth = g("threeYearAverageReturn")
            eps_growth     = g("fiveYearAverageReturn","ytdReturn")
        else:
            revenue_growth = g("revenueGrowth")
            eps_growth     = g("earningsGrowth")

        # Profitability
        profit_margin = g("profitMargins")
        roe           = g("returnOnEquity")
        roa           = g("returnOnAssets")
        if is_etf and profit_margin is None:
            exp = g("annualReportExpenseRatio")
            if exp is not None:
                profit_margin = round(max(0.0, 1.0 - exp * 100), 4)

        # Dividends
        dividend_yield = g("dividendYield","trailingAnnualDividendYield","yield")
        payout_ratio   = g("payoutRatio")

        # Risk
        beta        = g("beta","beta3Year")
        debt_equity = g("debtToEquity")
        if is_etf and beta is None and len(hist) >= 30:
            ann_vol = float(hist["Close"].pct_change().dropna().std() * (252**0.5))
            beta    = round(ann_vol / 0.18, 3)

        name   = info.get("longName") or info.get("shortName") or symbol
        sector = info.get("sector") or info.get("category") or ("ETF/Fund" if is_etf else "N/A")

        result = {
            "symbol": symbol.upper(), "name": name, "sector": sector,
            "quote_type": quote_type, "is_etf": is_etf, "price": price,
            "pe_ratio": pe_ratio, "pb_ratio": pb_ratio, "ev_ebitda": ev_ebitda,
            "revenue_growth": revenue_growth, "eps_growth": eps_growth,
            "profit_margin": profit_margin, "roe": roe, "roa": roa,
            "perf_52w": perf_52w, "rsi": rsi,
            "momentum_detail": momentum_detail,
            "dividend_yield": dividend_yield, "payout_ratio": payout_ratio,
            "beta": beta, "debt_equity": debt_equity,
            "analyst_target": g("targetMeanPrice"),
            "recommendation":  info.get("recommendationKey","N/A"),
            "num_analysts":    info.get("numberOfAnalystOpinions"),
            "fetched_at":      datetime.now().isoformat()
        }
        logging.info(f"[{symbol}] OK type={quote_type} pe={pe_ratio} rev_gr={revenue_growth} "
                     f"margin={profit_margin} beta={beta} div={dividend_yield} perf52w={perf_52w}")
        return result

    except Exception as e:
        logging.error(f"Error fetching {symbol}: {e}", exc_info=True)
        return {"symbol": symbol.upper(), "error": str(e)}

# ── Scoring engine ────────────────────────────────────────────────────────────
def score_ticker(d: dict, weights: dict) -> dict:
    def safe(v, lo, hi, invert=False):
        if v is None or not isinstance(v, (int, float)):
            return None
        v = max(lo, min(hi, v))
        s = (v - lo) / (hi - lo) * 100
        return round(100 - s if invert else s, 1)

    def avg(*scores):
        valid = [s for s in scores if s is not None]
        return round(sum(valid)/len(valid), 1) if valid else 50

    is_etf = d.get("is_etf", False)

    valuation     = avg(
        safe(d.get("pe_ratio"),  5,  60, True),
        safe(d.get("pb_ratio"),  0.5,10, True),
        safe(d.get("ev_ebitda"), 2,  40, True)
    )
    rv = d.get("revenue_growth"); ep = d.get("eps_growth")
    growth = avg(
        safe(rv*100 if rv else None, -20, 60),
        safe(ep*100 if ep else None, -30 if not is_etf else -20, 80 if not is_etf else 60)
    )
    profitability = avg(
        safe(d.get("profit_margin")*100 if d.get("profit_margin") is not None else None, -20, 40),
        safe(d.get("roe")*100           if d.get("roe") is not None else None,           -10, 40),
        safe(d.get("roa")*100           if d.get("roa") is not None else None,            -5, 25)
    )
    md = d.get("momentum_detail", {})
    momentum = md["momentum_score"] if md and md.get("momentum_score") is not None else avg(
        safe(d.get("perf_52w"), -50, 100),
        round(100 - abs((d.get("rsi") or 50) - 50) * 2, 1)
    )
    dividends = avg(
        safe(d.get("dividend_yield")*100 if d.get("dividend_yield") is not None else None, 0, 8),
        safe(d.get("payout_ratio")*100   if d.get("payout_ratio") is not None else None,   0, 100, True)
    )
    risk = avg(
        safe(d.get("beta"),        0, 3,   True),
        safe(d.get("debt_equity"), 0, 300, True)
    )
    w = weights
    composite = round(
        valuation     * w.get("valuation",     0.20) +
        growth        * w.get("growth",        0.20) +
        profitability * w.get("profitability", 0.20) +
        momentum      * w.get("momentum",      0.15) +
        dividends     * w.get("dividends",     0.10) +
        risk          * w.get("risk",          0.15), 1)

    logging.info(f"[{d['symbol']}] scores val={valuation} gr={growth} "
                 f"prof={profitability} mom={momentum} div={dividends} risk={risk} → {composite}")
    return {"scores": {
        "valuation": valuation, "growth": growth, "profitability": profitability,
        "momentum": momentum, "dividends": dividends, "risk": risk, "composite": composite
    }}

# ── Auto Screener ─────────────────────────────────────────────────────────────
SCREEN_UNIVERSE = [
    "AAPL","MSFT","GOOGL","META","NVDA","AMD","AVGO","ORCL","CRM","ADBE",
    "SNOW","PLTR","NOW","PANW","DDOG","MDB","NET","ANET","MRVL",
    "LLY","UNH","JNJ","ABT","TMO","ISRG","VRTX","REGN","DHR",
    "BRK-B","JPM","V","MA","BAC","GS","MS","AXP","BLK","SPGI",
    "AMZN","TSLA","HD","NKE","MCD","COST","LOW","TJX",
    "XOM","CVX","CAT","DE","RTX","HON","GE","LMT","UNP",
    "NFLX","DIS","PYPL","UBER","ABNB","SHOP","TTD"
]

def screen_watchlist_candidates():
    logging.info("Running watchlist screener…")
    candidates = []
    for sym in SCREEN_UNIVERSE:
        try:
            info       = yf.Ticker(sym).info
            price      = info.get("currentPrice") or info.get("regularMarketPrice") or 0
            target     = info.get("targetMeanPrice")
            rec        = str(info.get("recommendationKey","")).lower()
            pe         = info.get("trailingPE")
            rev_growth = info.get("revenueGrowth")
            eps_growth = info.get("earningsGrowth")
            analysts   = info.get("numberOfAnalystOpinions",0) or 0
            upside     = ((target-price)/price*100) if (target and price) else None
            is_analyst = rec in ["buy","strong_buy"] and upside and upside>=15 and analysts>=5
            is_value   = pe and 0<pe<25 and rev_growth and rev_growth>0.10
            if is_analyst or is_value:
                score = (50 if is_analyst else 0)+(50 if is_value else 0)+min(upside or 0,30)
                candidates.append({
                    "symbol": sym, "name": info.get("longName",sym),
                    "sector": info.get("sector","N/A"),
                    "price": round(price,2) if price else None,
                    "analyst_target": round(target,2) if target else None,
                    "upside_pct": round(upside,1) if upside else None,
                    "recommendation": rec, "num_analysts": analysts,
                    "pe_ratio": round(pe,2) if pe else None,
                    "revenue_growth": round(rev_growth,4) if rev_growth else None,
                    "eps_growth": round(eps_growth,4) if eps_growth else None,
                    "is_analyst_pick": is_analyst, "is_value_growth": is_value,
                    "screen_score": round(score,1)
                })
        except Exception as e:
            logging.warning(f"Screener skip {sym}: {e}")
    candidates.sort(key=lambda x: -x["screen_score"])
    logging.info(f"Screener: {len(candidates)} candidates.")
    return candidates[:10]

# ── Email ─────────────────────────────────────────────────────────────────────
def build_email_html(p_scored, w_scored):
    th = "padding:10px 14px;background:#0d1b2a;color:#fff;text-align:center;font-size:12px"
    td = "padding:10px 14px;border-bottom:1px solid #eee;text-align:center;font-size:13px"
    def rows(items):
        html = ""
        for s in sorted(items, key=lambda x: -x.get("scores",{}).get("composite",0)):
            sc = s.get("scores",{}); md = s.get("momentum_detail",{})
            html += f"""<tr>
              <td style="{td};text-align:left"><strong>{s['symbol']}</strong>{'&nbsp;<small>(ETF)</small>' if s.get('is_etf') else ''}<br>
              <span style="color:#888;font-size:11px">{s.get('name','')}</span></td>
              <td style="{td};font-weight:700;color:#0077b6;font-size:16px">{sc.get('composite','—')}</td>
              {''.join(f'<td style="{td}">{sc.get(k,"—")}</td>' for k in ["valuation","growth","profitability","momentum","dividends","risk"])}
              <td style="{td};font-size:11px;text-align:left">
                {md.get('ma_signal','N/A')}<br>{md.get('macd_signal','N/A')}<br>
                {md.get('volume_signal','N/A')}<br>{md.get('rs_signal','N/A')}
              </td></tr>"""
        return html
    headers = ["Symbol","Composite","Valuation","Growth","Profitability","Momentum","Dividends","Risk","Momentum Detail"]
    return f"""<html><body style="background:#f0f4f8;padding:24px;font-family:Arial,sans-serif">
    <div style="max-width:1000px;margin:auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.1)">
      <div style="background:linear-gradient(135deg,#0d1b2a,#0077b6);padding:32px;color:#fff">
        <h1 style="margin:0;font-size:26px">⬡ PortfolioIQ Weekly Report</h1>
        <p style="margin:8px 0 0;opacity:.75">{datetime.now().strftime('%A, %B %d, %Y')}</p>
      </div>
      <div style="padding:28px">
        <h2 style="color:#0d1b2a">💼 Current Portfolio</h2>
        <div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse">
          <tr>{''.join(f'<th style="{th}">{h}</th>' for h in headers)}</tr>{rows(p_scored)}
        </table></div>
        <h2 style="color:#0d1b2a;margin-top:32px">🔭 Watchlist Opportunities</h2>
        <div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse">
          <tr>{''.join(f'<th style="{th}">{h}</th>' for h in headers)}</tr>{rows(w_scored)}
        </table></div>
        <p style="color:#aaa;font-size:11px;margin-top:24px">
          Momentum: MA Position | MACD | Volume vs Average | Relative Strength vs S&P 500<br>
          ETF growth uses 3yr/5yr annualised fund returns. Scores 0–100. Data: Yahoo Finance.
        </p>
      </div></div></body></html>"""

def send_weekly_report():
    cfg  = load_config()
    data = load_portfolio()
    if not cfg.get("smtp_user") or not cfg.get("smtp_password"):
        logging.warning("SMTP credentials missing — skipping email.")
        return
    weights = cfg["weights"]
    def scored(syms):
        out = []
        for sym in syms:
            d = fetch_ticker_data(sym)
            if "error" not in d:
                d.update(score_ticker(d, weights))
            out.append(d)
        return out
    html = build_email_html(scored(data.get("portfolio",[])), scored(data.get("watchlist",[])))
    msg  = MIMEMultipart("alternative")
    msg["Subject"] = f"⬡ PortfolioIQ Weekly — {datetime.now().strftime('%b %d, %Y')}"
    msg["From"]    = cfg["smtp_user"]
    msg["To"]      = cfg["email"]
    msg.attach(MIMEText(html,"html"))
    try:
        with smtplib.SMTP(cfg["smtp_server"], int(cfg["smtp_port"])) as s:
            s.ehlo(); s.starttls(); s.ehlo()
            s.login(cfg["smtp_user"], cfg["smtp_password"])
            s.sendmail(cfg["smtp_user"], cfg["email"], msg.as_string())
        logging.info(f"Report sent to {cfg['email']}")
    except Exception as e:
        logging.error(f"Email error: {e}")
        raise

# ── Scheduler ─────────────────────────────────────────────────────────────────
scheduler = BackgroundScheduler(daemon=True)
_screener_cache = {"results": [], "last_run": None}

def refresh_screener_cache():
    try:
        _screener_cache["results"]  = screen_watchlist_candidates()
        _screener_cache["last_run"] = datetime.now().isoformat()
    except Exception as e:
        logging.error(f"Screener error: {e}")

def setup_scheduler():
    cfg = load_config()
    scheduler.remove_all_jobs()
    scheduler.add_job(send_weekly_report,    "cron",
                      day_of_week=cfg.get("schedule_day","monday")[:3],
                      hour=cfg.get("schedule_hour",8), minute=0, id="weekly")
    scheduler.add_job(refresh_screener_cache,"cron",
                      day_of_week="sun", hour=6, minute=0, id="screener")
    if not scheduler.running:
        scheduler.start()

# ═══════════════════════════════════════════════════════════════════════════════
#  VALUATION MODULE
# ═══════════════════════════════════════════════════════════════════════════════

MACROTRENDS_SLUGS = {
    "QQQM":  "invesco-qqq-trust",
    "VDE":   "vanguard-energy-etf",
    "ICLN":  "ishares-global-clean-energy",
    "VEU":   "vanguard-ftse-all-world-ex-us",
    "NU":    "nu-holdings",
    "SMH":   "vaneck-semiconductor-etf",
    "XAR":   "spdr-aerospace-defense-etf",
    "CIBR":  "first-trust-nasdaq-cybersecurity",
    "BE":    "bloom-energy",
    "SOXX":  "ishares-semiconductor-etf",
    "FTXL":  "first-trust-nasdaq-semiconductor",
    "SHLD":  "global-x-defense-tech-etf",
    "IONQ":  "ionq",
    "IBM":   "international-business-machines",
    "FLNC":  "fluence-energy",
    "RGTI":  "rigetti-computing",
    "QUBT":  "quantum-computing",
    "VST":   "vistra-energy",
    "VUG":   "vanguard-growth-etf",
}

def get_macrotrends_url(ticker: str) -> str:
    slug = MACROTRENDS_SLUGS.get(ticker.upper(), ticker.lower())
    return f"https://www.macrotrends.net/stocks/charts/{ticker.upper()}/{slug}/pe-ratio"

def calc_roic_wacc(info: dict) -> dict:
    """
    Calculate ROIC and WACC from yfinance info fields.
    Returns dict with roic, wacc, spread, roic_signal, roic_verdict.
    Returns all None for ETFs or when data is insufficient.
    """
    quote_type = str(info.get("quoteType","")).upper()
    is_etf     = quote_type in ("ETF","MUTUALFUND","FUND","INDEX")

    if is_etf:
        return {
            "roic": None, "wacc": None, "spread": None,
            "roic_signal": "etf",
            "roic_verdict": "ROIC/WACC not applicable for ETFs — individual holdings vary."
        }

    try:
        # ── ROIC ─────────────────────────────────────────────────────────────
        # ROIC = NOPAT / Invested Capital
        # Approximation: Net Income / (Total Equity + Total Debt)
        net_income   = info.get("netIncomeToCommon")
        total_debt   = info.get("totalDebt") or 0
        total_equity = info.get("totalStockholderEquity") or info.get("bookValue")

        roic = None
        if net_income and total_equity:
            invested_capital = total_equity + total_debt
            if invested_capital > 0:
                roic = round((net_income / invested_capital) * 100, 1)

        # ── WACC ─────────────────────────────────────────────────────────────
        # Simplified: Risk-free rate + Beta × Equity Risk Premium
        # Using 10yr Treasury ~4.3%, ERP ~5.5%
        RISK_FREE    = 4.3   # 10-yr US Treasury June 2026
        ERP          = 5.5   # Equity risk premium
        beta         = info.get("beta") or info.get("beta3Year") or 1.0
        beta         = max(0.3, min(float(beta), 3.0))  # cap between 0.3–3.0

        cost_of_equity = RISK_FREE + beta * ERP

        # Debt adjustment (after-tax cost of debt)
        TAX_RATE = 0.21
        total_assets = info.get("totalAssets") or 1
        if total_debt and total_equity and (total_debt + total_equity) > 0:
            total_cap      = total_debt + total_equity
            weight_debt    = total_debt   / total_cap
            weight_equity  = total_equity / total_cap
            # Estimate cost of debt from interest expense / total debt
            interest_exp   = abs(info.get("interestExpense") or 0)
            cost_of_debt   = (interest_exp / total_debt * 100) if total_debt > 0 else 5.0
            cost_of_debt   = max(3.0, min(cost_of_debt, 12.0))  # cap 3–12%
            wacc = round(
                weight_equity * cost_of_equity +
                weight_debt   * cost_of_debt * (1 - TAX_RATE), 1
            )
        else:
            wacc = round(cost_of_equity, 1)

        # ── Spread & Signal ───────────────────────────────────────────────────
        spread = round(roic - wacc, 1) if roic is not None else None

        if roic is None:
            roic_signal  = "na"
            roic_verdict = "Insufficient data to calculate ROIC. Check financials manually."
        elif spread >= 10:
            roic_signal  = "strong"
            roic_verdict = f"ROIC of {roic}% exceeds WACC by {spread}% — exceptional value creation. Ackman would approve."
        elif spread >= 3:
            roic_signal  = "positive"
            roic_verdict = f"ROIC of {roic}% exceeds WACC of {wacc}% — business is creating value for shareholders."
        elif spread >= 0:
            roic_signal  = "marginal"
            roic_verdict = f"ROIC barely covers WACC (spread: +{spread}%) — marginal value creation. Monitor closely."
        elif spread >= -5:
            roic_signal  = "negative"
            roic_verdict = f"ROIC of {roic}% below WACC of {wacc}% — currently destroying shareholder value. Needs improvement."
        else:
            roic_signal  = "danger"
            roic_verdict = f"ROIC of {roic}% significantly below WACC of {wacc}% (spread: {spread}%) — material value destruction."

        return {
            "roic":        roic,
            "wacc":        wacc,
            "spread":      spread,
            "roic_signal": roic_signal,
            "roic_verdict": roic_verdict,
        }

    except Exception as e:
        return {
            "roic": None, "wacc": None, "spread": None,
            "roic_signal": "na",
            "roic_verdict": f"Could not calculate ROIC/WACC: {str(e)}"
        }


def get_valuation_signal(ticker: str) -> dict:
    """
    Pull P/E, EPS, 52-week price data, and ROIC/WACC via yfinance.
    Returns a dict ready for the valuation tab in index.html.
    """
    try:
        t    = yf.Ticker(ticker)
        info = t.info

        trailing_pe  = info.get("trailingPE")
        forward_pe   = info.get("forwardPE")
        forward_eps  = info.get("forwardEps")
        price        = info.get("currentPrice") or info.get("regularMarketPrice")
        high_52w     = info.get("fiftyTwoWeekHigh")
        low_52w      = info.get("fiftyTwoWeekLow")
        target_price = info.get("targetMeanPrice")

        # 52-week position (0 = at low, 100 = at high)
        pct_from_high  = None
        range_position = None
        near_high      = False

        if price and high_52w:
            pct_from_high = round((price / high_52w - 1) * 100, 1)
            near_high     = pct_from_high >= -5
        if price and high_52w and low_52w and high_52w != low_52w:
            range_position = round(
                max(0, min(100, (price - low_52w) / (high_52w - low_52w) * 100)), 1
            )

        # P/E expansion logic
        pe_expanding = None
        signal = signal_color = "N/A"
        explanation = ""

        if trailing_pe and forward_pe:
            pe_expanding = forward_pe > (trailing_pe * 1.05)
            if not pe_expanding and trailing_pe < 40:
                signal = "healthy"
                explanation = "Earnings growing faster than price — high here is justified."
            elif pe_expanding and near_high:
                signal = "stretched"
                explanation = "Near 52-week high AND P/E expanding — investors paying more for same earnings. Consider trimming."
            elif trailing_pe > 60:
                signal = "danger"
                explanation = f"P/E of {round(trailing_pe,1)}x is extremely stretched. Check 10-year history on Macrotrends."
            elif pe_expanding and not near_high:
                signal = "watch"
                explanation = "P/E expanding but not yet near 52-week high. Monitor before adding."
            else:
                signal = "healthy"
                explanation = "Valuation reasonable relative to earnings trajectory."
        elif trailing_pe:
            signal = "watch"
            explanation = "Forward P/E unavailable — partial analysis only. Check Yahoo Stats."
        else:
            signal = "na"
            explanation = "P/E not applicable (ETF, pre-revenue, or data unavailable). Use range bar and analyst target for context."

        # Analyst upside
        upside_pct = None
        if target_price and price:
            upside_pct = round((target_price / price - 1) * 100, 1)

        # ROIC / WACC — Ackman dimension
        roic_data = calc_roic_wacc(info)

        return {
            "ticker":          ticker.upper(),
            "price":           round(price, 2) if price else None,
            "trailing_pe":     round(trailing_pe, 1) if trailing_pe else None,
            "forward_pe":      round(forward_pe, 1)  if forward_pe  else None,
            "forward_eps":     round(forward_eps, 2) if forward_eps else None,
            "high_52w":        round(high_52w, 2) if high_52w else None,
            "low_52w":         round(low_52w, 2)  if low_52w  else None,
            "pct_from_high":   pct_from_high,
            "range_position":  range_position,
            "near_high":       near_high,
            "pe_expanding":    pe_expanding,
            "target_price":    round(target_price, 2) if target_price else None,
            "upside_pct":      upside_pct,
            "signal":          signal,
            "explanation":     explanation,
            # ROIC / WACC
            "roic":            roic_data["roic"],
            "wacc":            roic_data["wacc"],
            "spread":          roic_data["spread"],
            "roic_signal":     roic_data["roic_signal"],
            "roic_verdict":    roic_data["roic_verdict"],
            # Links
            "macrotrends_url": get_macrotrends_url(ticker),
            "yahoo_stats":     f"https://finance.yahoo.com/quote/{ticker.upper()}/key-statistics/",
            "simplywall":      f"https://simplywall.st/stocks/us/{ticker.lower()}/news",
        }
    except Exception as e:
        return {
            "ticker":          ticker.upper(),
            "signal":          "na",
            "explanation":     f"Error: {str(e)}",
            "macrotrends_url": get_macrotrends_url(ticker),
            "yahoo_stats":     f"https://finance.yahoo.com/quote/{ticker.upper()}/key-statistics/",
            "simplywall":      f"https://simplywall.st/stocks/us/{ticker.lower()}/news",
            "price": None, "trailing_pe": None, "forward_pe": None,
            "forward_eps": None, "high_52w": None, "low_52w": None,
            "pct_from_high": None, "range_position": None,
            "near_high": False, "pe_expanding": None,
            "target_price": None, "upside_pct": None,
            "roic": None, "wacc": None, "spread": None,
            "roic_signal": "na", "roic_verdict": "Data unavailable.",
        }

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/health")
def health():
    return jsonify({"status":"ok","time":datetime.now().isoformat()})

@app.route("/api/portfolio", methods=["GET"])
def get_portfolio():
    return jsonify(load_portfolio())

@app.route("/api/portfolio", methods=["POST"])
def update_portfolio():
    body = request.json; data = load_portfolio()
    if "portfolio" in body:
        data["portfolio"] = [s.strip().upper() for s in body["portfolio"] if s.strip()]
    if "watchlist" in body:
        data["watchlist"] = [s.strip().upper() for s in body["watchlist"] if s.strip()]
    data["last_updated"] = datetime.now().isoformat()
    save_portfolio(data); return jsonify({"status":"ok","data":data})

@app.route("/api/analyze", methods=["POST"])
def analyze():
    symbols = request.json.get("symbols",[])
    cfg     = load_config()
    results = []
    for sym in symbols:
        d = fetch_ticker_data(sym)
        if "error" not in d:
            d.update(score_ticker(d, cfg["weights"]))
        results.append(d)
    return jsonify(results)

@app.route("/api/valuation", methods=["GET"])
def valuation_api():
    """Return valuation signals for all portfolio + watchlist tickers."""
    data    = load_portfolio()
    tickers = list(dict.fromkeys(data.get("portfolio", []) + data.get("watchlist", [])))
    results = [get_valuation_signal(t) for t in tickers]
    return jsonify(results)

@app.route("/api/debug/<symbol>")
def debug_ticker(symbol):
    try:
        info = yf.Ticker(symbol.upper()).info
        return jsonify({"symbol":symbol.upper(),"quoteType":info.get("quoteType"),
                        "fields":{k:v for k,v in sorted(info.items()) if v not in (None,"",0,"None")}})
    except Exception as e:
        return jsonify({"error":str(e)})

@app.route("/api/config", methods=["GET"])
def get_config():
    cfg  = load_config()
    safe = {k:v for k,v in cfg.items() if k != "smtp_password"}
    safe["smtp_password_set"] = bool(cfg.get("smtp_password"))
    return jsonify(safe)

@app.route("/api/config", methods=["POST"])
def update_config():
    body = request.json; cfg = load_config()
    for key in ["email","smtp_server","smtp_port","smtp_user","smtp_password",
                "weights","schedule_day","schedule_hour"]:
        if key in body and body[key] is not None and body[key] != "":
            cfg[key] = body[key]
    save_config(cfg); setup_scheduler()
    return jsonify({"status":"ok"})

@app.route("/api/send_report", methods=["POST"])
def trigger_report():
    def run():
        try: send_weekly_report()
        except Exception as e: logging.error(f"Report error: {e}")
    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status":"sending"})

@app.route("/api/screener", methods=["GET"])
def get_screener():
    return jsonify({"results":_screener_cache["results"],"last_run":_screener_cache["last_run"]})

@app.route("/api/screener/refresh", methods=["POST"])
def trigger_screener():
    threading.Thread(target=refresh_screener_cache, daemon=True).start()
    return jsonify({"status":"running"})
@app.route("/api/claude", methods=["POST"])
def claude_proxy():
    import requests as req
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": {"message": "ANTHROPIC_API_KEY not set"}}), 500
    try:
        resp = req.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json", "x-api-key": api_key, "anthropic-version": "2023-06-01"},
            json=request.json, timeout=60
        )
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({"error": {"message": str(e)}}), 500
if __name__ == "__main__":
    setup_scheduler()
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)
